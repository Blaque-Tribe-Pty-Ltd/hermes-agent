"""Cross-platform workspace resolution for Kanban tasks.

Reads ~/.hermes/workspace-mapping.yaml and resolves logical workspace
identities to host-specific paths. Prevents tasks created on one host
from spawning workers with invalid paths on another host.
"""

from __future__ import annotations

import logging
import os
import platform
from pathlib import Path
from typing import Optional

try:
    import yaml
except Exception:
    yaml = None  # type: ignore

_log = logging.getLogger(__name__)

# Known host path prefixes that indicate a specific OS
_HOST_PATH_PREFIXES = {
    "/Users/": "macos",
    "/home/": "linux",
    "C:\\\\": "windows",
}

# Default fallback patterns when no mapping exists
_DEFAULT_FALLBACKS = {
    "linux": "/home/{user}/workspaces/{repo_name}",
    "macos": "/Users/{user}/Projects/{repo_name}",
}


def _detect_host_os() -> str:
    """Return a canonical host OS identifier."""
    system = platform.system().lower()
    if system == "darwin":
        return "macos"
    if system == "linux":
        return "linux"
    if system == "windows":
        return "windows"
    return system


def _guess_path_os(path: str) -> Optional[str]:
    """Infer which OS a path belongs to based on its prefix."""
    path_lower = path.lower()
    for prefix, os_name in _HOST_PATH_PREFIXES.items():
        if path_lower.startswith(prefix.lower()):
            return os_name
    return None


def _load_mapping() -> dict:
    """Load workspace-mapping.yaml from HERMES_HOME."""
    hermes_home = os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes"))
    mapping_path = Path(hermes_home) / "workspace-mapping.yaml"
    if not mapping_path.exists():
        return {}
    if yaml is None:
        _log.warning("workspace_resolver: PyYAML not available, cannot load %s", mapping_path)
        return {}
    try:
        with open(mapping_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        _log.warning("workspace_resolver: failed to load %s: %s", mapping_path, exc)
        return {}


def _resolve_via_mapping(
    path: str, current_os: str, mapping: dict
) -> tuple[Optional[str], Optional[str]]:
    """Try to resolve a path using the workspace-mapping.yaml config.

    Returns (resolved_path, logical_project) or (None, None) if no match.
    """
    mappings = mapping.get("mappings", {})
    for project_slug, project_cfg in mappings.items():
        if not isinstance(project_cfg, dict):
            continue
        hosts = project_cfg.get("hosts", {})
        # Check if the given path matches any host's path for this project
        matched_host = None
        for host_name, host_cfg in hosts.items():
            if not isinstance(host_cfg, dict):
                continue
            host_path = host_cfg.get("path", "")
            if host_path and path.startswith(host_path):
                matched_host = host_name
                break
        if matched_host is None:
            continue
        # Found the project — now resolve to the current host's path
        current_host_cfg = hosts.get(current_os)
        if current_host_cfg and current_host_cfg.get("path"):
            return current_host_cfg["path"], project_slug
        # Fall back to default_host
        default_host = project_cfg.get("default_host")
        if default_host and default_host != matched_host:
            default_cfg = hosts.get(default_host)
            if default_cfg and default_cfg.get("path"):
                return default_cfg["path"], project_slug
    return None, None


def _repo_name_from_path(path: str) -> str:
    """Extract a likely repo name from a path."""
    p = Path(path)
    # If the path ends with .worktrees/<task-id>, go up two levels
    if p.name.startswith("t_") and p.parent.name == ".worktrees":
        p = p.parent.parent
    return p.name


def _fallback_resolution(path: str, current_os: str, mapping: dict) -> Optional[str]:
    """Try fallback pattern resolution when no explicit mapping matches."""
    fallback_rules = mapping.get("resolution_rules", {})
    if not fallback_rules.get("rewrite_host_paths", True):
        return None

    fallback_pattern = fallback_rules.get("fallback_pattern", {}).get(current_os)
    if not fallback_pattern:
        default_fallback = _DEFAULT_FALLBACKS.get(current_os)
        if not default_fallback:
            return None
        fallback_pattern = default_fallback

    repo_name = _repo_name_from_path(path)
    user = os.environ.get("USER", os.environ.get("USERNAME", "moonbeam"))
    try:
        resolved = fallback_pattern.format(repo_name=repo_name, user=user)
        return resolved
    except Exception:
        return None


def resolve_workspace_path(
    path: str,
    *,
    task_id: Optional[str] = None,
    validate_exists: bool = True,
    fail_on_missing_mapping: bool = True,
) -> str:
    """Resolve a workspace path to one valid on the current execution host.

    Args:
        path: The raw workspace_path from a Kanban task.
        task_id: Optional task ID for logging.
        validate_exists: If True, raise if the resolved path does not exist.
        fail_on_missing_mapping: If True, raise when a foreign host path
            cannot be resolved to a valid local path.

    Returns:
        The resolved path valid for the current host.

    Raises:
        ValueError: If the path cannot be resolved or does not exist.
    """
    current_os = _detect_host_os()
    original_path = path
    path_os = _guess_path_os(path)

    # If the path already matches the current host, use it directly
    if path_os == current_os:
        _log.debug(
            "workspace_resolver: path %s matches current host %s, using directly",
            path, current_os,
        )
        resolved = path
    elif path_os is not None and path_os != current_os:
        # Foreign host path — attempt resolution
        mapping = _load_mapping()
        mapped_path, logical_project = _resolve_via_mapping(path, current_os, mapping)
        if mapped_path:
            _log.info(
                "workspace_resolver: task %s mapped project %s from %s (%s) to %s (%s)",
                task_id, logical_project, original_path, path_os, mapped_path, current_os,
            )
            resolved = mapped_path
        else:
            # Try fallback pattern
            fallback = _fallback_resolution(path, current_os, mapping)
            if fallback:
                _log.info(
                    "workspace_resolver: task %s fallback %s (%s) to %s (%s)",
                    task_id, original_path, path_os, fallback, current_os,
                )
                resolved = fallback
            elif fail_on_missing_mapping:
                raise ValueError(
                    f"task {task_id}: workspace path {original_path!r} belongs to "
                    f"host OS {path_os!r} but current host is {current_os!r} and "
                    f"no workspace mapping exists. Add it to workspace-mapping.yaml "
                    f"or create the worktree manually."
                )
            else:
                _log.warning(
                    "workspace_resolver: task %s keeping foreign path %s unresolved",
                    task_id, original_path,
                )
                resolved = path
    else:
        # Cannot determine path OS — use as-is
        resolved = path

    # Validate the resolved path exists (for worktrees)
    if validate_exists and not Path(resolved).expanduser().exists():
        raise ValueError(
            f"task {task_id}: resolved workspace path {resolved!r} does not exist. "
            f"Create it or adjust the workspace mapping."
        )

    return resolved


def resolve_worktree_path(
    workspace_path: str,
    *,
    task_id: Optional[str] = None,
) -> str:
    """Resolve a worktree workspace path, with cross-platform rewriting.

    This is a convenience wrapper around resolve_workspace_path with
    worktree-specific defaults.
    """
    return resolve_workspace_path(
        workspace_path,
        task_id=task_id,
        validate_exists=False,  # Worktrees may need creation
        fail_on_missing_mapping=True,
    )
