"""Profile resolution for delegate_task — load a Hermes profile's config, SOUL, and runtime.

Used when delegate_task is called with an explicit `profile` parameter.
The child agent runs under the target profile's configuration instead of
inheriting the parent's.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home, named_profile_home, set_hermes_home_override

logger = logging.getLogger(__name__)

# Profiles that may be dispatched to via delegate_task.
# This is the security boundary: unknown profiles fail closed.
ALLOWED_PROFILES = frozenset({
    "neo", "tsebo", "naledi", "lesedi", "pono", "chabi", "vision", "tlhogi"
})


@dataclass
class ProfileRuntime:
    """Resolved runtime configuration for a target profile."""
    profile: str
    model: str = ""
    provider: str = ""
    base_url: str = ""
    api_key: str = ""
    api_mode: str = ""
    soul: str = ""
    toolsets: List[str] = field(default_factory=list)
    request_overrides: Dict[str, Any] = field(default_factory=dict)
    workspace: str = ""
    capabilities: Dict[str, bool] = field(default_factory=dict)


def resolve_profile_runtime(profile: str) -> ProfileRuntime:
    """Load a profile's config and SOUL, returning a ProfileRuntime.

    Raises ValueError for unknown profiles or missing config.
    """
    profile_norm = (profile or "").strip().lower()
    if not profile_norm:
        raise ValueError("Profile name is empty")
    if profile_norm not in ALLOWED_PROFILES:
        raise ValueError(
            f"Profile '{profile}' is not in the allowed specialist set. "
            f"Allowed: {sorted(ALLOWED_PROFILES)}"
        )

    profile_home = named_profile_home(profile_norm)
    if profile_home is None or not profile_home.exists():
        raise ValueError(
            f"Profile '{profile}' home does not exist: {profile_home}"
        )

    # Temporarily override HERMES_HOME to load the profile's config
    token = set_hermes_home_override(str(profile_home))
    try:
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly()
    except Exception as exc:
        raise ValueError(f"Could not load config for profile '{profile}': {exc}") from exc
    finally:
        from hermes_constants import reset_hermes_home_override
        reset_hermes_home_override(token)

    model_cfg = cfg.get("model", {})
    model = model_cfg.get("default", "") if isinstance(model_cfg, dict) else ""
    provider = model_cfg.get("provider", "") if isinstance(model_cfg, dict) else ""

    # Resolve runtime provider for base_url/api_key if provider is set
    base_url = ""
    api_key = ""
    api_mode = ""
    request_overrides = {}
    if provider:
        try:
            from hermes_cli.runtime_provider import resolve_runtime_provider
            runtime = resolve_runtime_provider(requested=provider, target_model=model)
            base_url = runtime.get("base_url", "")
            api_key = runtime.get("api_key", "")
            api_mode = runtime.get("api_mode", "")
            request_overrides = dict(runtime.get("request_overrides") or {})
        except Exception as exc:
            logger.warning("Could not resolve runtime provider for profile '%s': %s", profile, exc)

    # Load SOUL
    soul_path = profile_home / "SOUL.md"
    soul = ""
    if soul_path.exists():
        try:
            soul = soul_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Could not read SOUL for profile '%s': %s", profile, exc)

    # Toolsets from config
    toolsets = []
    agent_cfg = cfg.get("agent", {})
    if isinstance(agent_cfg, dict):
        ts = agent_cfg.get("toolsets")
        if isinstance(ts, list):
            toolsets = [str(t) for t in ts]

    workspace = str(profile_home)

    return ProfileRuntime(
        profile=profile_norm,
        model=model,
        provider=provider,
        base_url=base_url,
        api_key=api_key,
        api_mode=api_mode,
        soul=soul,
        toolsets=toolsets,
        request_overrides=request_overrides,
        workspace=workspace,
    )


def load_profile_soul(profile: str) -> str:
    """Load the SOUL.md for a profile, returning empty string if missing."""
    profile_norm = (profile or "").strip().lower()
    if not profile_norm:
        return ""
    profile_home = named_profile_home(profile_norm)
    if profile_home is None:
        return ""
    soul_path = profile_home / "SOUL.md"
    if not soul_path.exists():
        return ""
    try:
        return soul_path.read_text(encoding="utf-8")
    except Exception:
        return ""


def is_allowed_profile(profile: str) -> bool:
    """Return True if the profile is in the allowed set."""
    return (profile or "").strip().lower() in ALLOWED_PROFILES
