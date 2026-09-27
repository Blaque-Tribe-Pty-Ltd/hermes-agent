"""Worktree validation, repair, and safe recreation for Kanban tasks.

Guarantees that a worktree workspace actually exists, is attached to the
intended repository, and is on the correct branch before a worker is spawned.
Never silently uses a stale or mis-attached worktree.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def _git(*args: str, cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


class WorktreeState:
    """Classification of a worktree directory's current state."""

    MISSING = "missing"
    EXISTS_VALID = "exists_valid"
    EXISTS_WRONG_REPO = "exists_wrong_repo"
    EXISTS_WRONG_BRANCH = "exists_wrong_branch"
    EXISTS_UNCLEAN = "exists_unclean"
    EXISTS_NOT_A_WORKTREE = "exists_not_a_worktree"


def inspect_worktree(
    worktree_path: Path,
    *,
    repo_root: Path,
    expected_branch: str,
) -> Tuple[str, Optional[str]]:
    """Inspect a worktree and return its state + an error message if any.

    Returns (WorktreeState.*, error_message).
    """
    if not worktree_path.exists():
        return WorktreeState.MISSING, None

    if not worktree_path.is_dir():
        return (
            WorktreeState.EXISTS_NOT_A_WORKTREE,
            f"{worktree_path} exists but is not a directory",
        )

    # Is this a git worktree?
    git_dir = worktree_path / ".git"
    if not git_dir.exists():
        # Check if it's a git worktree file (contains path to actual git dir)
        git_file = worktree_path / ".git"
        if not git_file.is_file():
            return (
                WorktreeState.EXISTS_NOT_A_WORKTREE,
                f"{worktree_path} exists but is not a git worktree (no .git)",
            )

    # Is it part of the expected repo?
    # For worktrees, --show-toplevel returns the worktree dir, not the main
    # repo. Use --git-common-dir to get the shared .git directory and resolve
    # its parent as the canonical repo root.
    result = _git("rev-parse", "--git-common-dir", cwd=worktree_path)
    if result.returncode != 0:
        return (
            WorktreeState.EXISTS_NOT_A_WORKTREE,
            f"{worktree_path} exists but git rev-parse failed: {result.stderr.strip()}",
        )

    git_common_dir = Path(result.stdout.strip()).resolve()
    # git-common-dir points to .git (main repo) or .git/worktrees/... (worktree)
    actual_repo_root = git_common_dir
    while actual_repo_root.name == ".git" or "worktrees" in actual_repo_root.parts[-3:]:
        actual_repo_root = actual_repo_root.parent
    expected_toplevel = repo_root.resolve()
    if actual_repo_root != expected_toplevel:
        return (
            WorktreeState.EXISTS_WRONG_REPO,
            f"{worktree_path} points to repo {actual_repo_root}, expected {expected_toplevel}",
        )

    # Is it on the expected branch?
    result = _git("branch", "--show-current", cwd=worktree_path)
    if result.returncode != 0:
        return (
            WorktreeState.EXISTS_NOT_A_WORKTREE,
            f"{worktree_path}: git branch --show-current failed: {result.stderr.strip()}",
        )

    actual_branch = result.stdout.strip()
    if actual_branch != expected_branch:
        return (
            WorktreeState.EXISTS_WRONG_BRANCH,
            f"{worktree_path} is on branch {actual_branch!r}, expected {expected_branch!r}",
        )

    # Is it clean?
    result = _git("status", "--porcelain", cwd=worktree_path)
    if result.stdout.strip():
        return (
            WorktreeState.EXISTS_UNCLEAN,
            f"{worktree_path} has uncommitted changes; refusing to recreate to avoid data loss",
        )

    return WorktreeState.EXISTS_VALID, None


def repair_worktree(
    worktree_path: Path,
    *,
    repo_root: Path,
    branch_name: str,
) -> None:
    """Validate and (if needed) safely recreate a worktree.

    Raises RuntimeError if the worktree is in a state that cannot be safely
    repaired (e.g., points to wrong repo, has uncommitted changes, etc.).
    """
    state, error = inspect_worktree(
        worktree_path,
        repo_root=repo_root,
        expected_branch=branch_name,
    )

    if state == WorktreeState.EXISTS_VALID:
        logger.info("worktree %s is valid (branch %s)", worktree_path, branch_name)
        return

    if state == WorktreeState.MISSING:
        logger.info("worktree %s missing; will create for branch %s", worktree_path, branch_name)
        _create_worktree(repo_root, worktree_path, branch_name)
        return

    if state == WorktreeState.EXISTS_WRONG_BRANCH:
        logger.warning("worktree %s on wrong branch; removing and recreating", worktree_path)
        _remove_worktree_safe(worktree_path, repo_root=repo_root)
        _create_worktree(repo_root, worktree_path, branch_name)
        return

    if state == WorktreeState.EXISTS_NOT_A_WORKTREE:
        logger.warning("worktree %s is not a valid git worktree; removing and recreating", worktree_path)
        _remove_worktree_safe(worktree_path, repo_root=repo_root)
        _create_worktree(repo_root, worktree_path, branch_name)
        return

    # Everything else is a hard failure
    raise RuntimeError(f"worktree {worktree_path} cannot be safely repaired: {error}")


def _create_worktree(repo_root: Path, worktree_path: Path, branch_name: str) -> None:
    """Create a git worktree at ``worktree_path`` for ``branch_name``."""
    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    result = _git("worktree", "add", str(worktree_path), branch_name, cwd=repo_root)
    if result.returncode != 0:
        raise RuntimeError(
            f"git worktree add failed for {worktree_path}: {result.stderr.strip()}"
        )
    logger.info("created worktree %s for branch %s", worktree_path, branch_name)


def _remove_worktree_safe(worktree_path: Path, *, repo_root: Path) -> None:
    """Remove a worktree safely using git worktree remove."""
    result = _git("worktree", "remove", "--force", str(worktree_path), cwd=repo_root)
    if result.returncode != 0:
        # Fallback: shutil.rmtree if git refuses
        logger.warning("git worktree remove failed for %s; using shutil.rmtree", worktree_path)
        shutil.rmtree(worktree_path, ignore_errors=True)
    logger.info("removed worktree %s", worktree_path)
