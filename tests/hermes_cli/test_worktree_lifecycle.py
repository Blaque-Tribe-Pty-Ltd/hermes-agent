"""Tests for hermes_cli.worktree_lifecycle."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from hermes_cli.worktree_lifecycle import (
    WorktreeState,
    _create_worktree,
    _git,
    _remove_worktree_safe,
    inspect_worktree,
    repair_worktree,
)


def _init_repo(repo: Path) -> None:
    """Create a git repo with an initial commit on branch 'master'."""
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "file.txt").write_text("hello")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init", "--quiet"], cwd=repo, check=True)


class TestGitHelper:
    def test_git_runs_git_command(self, tmp_path):
        result = _git("--version", cwd=tmp_path)
        assert result.returncode == 0
        assert "git version" in result.stdout


class TestInspectWorktree:
    def test_missing(self, tmp_path):
        state, error = inspect_worktree(
            tmp_path / "nonexistent",
            repo_root=tmp_path,
            expected_branch="main",
        )
        assert state == WorktreeState.MISSING
        assert error is None

    def test_exists_not_a_worktree(self, tmp_path):
        d = tmp_path / "not_a_worktree"
        d.mkdir()
        state, error = inspect_worktree(d, repo_root=tmp_path, expected_branch="main")
        assert state == WorktreeState.EXISTS_NOT_A_WORKTREE
        assert "not a git worktree" in error

    def test_exists_valid(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        subprocess.run(["git", "branch", "wt"], cwd=repo, check=True)

        wt = tmp_path / "wt"
        _create_worktree(repo, wt, "wt")

        state, error = inspect_worktree(wt, repo_root=repo, expected_branch="wt")
        assert state == WorktreeState.EXISTS_VALID
        assert error is None

    def test_exists_wrong_branch(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        subprocess.run(["git", "branch", "other"], cwd=repo, check=True)

        wt = tmp_path / "wt"
        _create_worktree(repo, wt, "other")

        state, error = inspect_worktree(wt, repo_root=repo, expected_branch="master")
        assert state == WorktreeState.EXISTS_WRONG_BRANCH
        assert "other" in error

    def test_exists_unclean(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        subprocess.run(["git", "branch", "wt"], cwd=repo, check=True)

        wt = tmp_path / "wt"
        _create_worktree(repo, wt, "wt")
        (wt / "new_file.txt").write_text("dirty")

        state, error = inspect_worktree(wt, repo_root=repo, expected_branch="wt")
        assert state == WorktreeState.EXISTS_UNCLEAN
        assert "uncommitted changes" in error


class TestRepairWorktree:
    def test_creates_missing(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        subprocess.run(["git", "branch", "wt"], cwd=repo, check=True)

        wt = tmp_path / "wt"
        repair_worktree(wt, repo_root=repo, branch_name="wt")
        assert wt.exists()
        assert (wt / ".git").exists()

    def test_repairs_wrong_branch(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        subprocess.run(["git", "branch", "other"], cwd=repo, check=True)
        subprocess.run(["git", "branch", "target"], cwd=repo, check=True)

        wt = tmp_path / "wt"
        _create_worktree(repo, wt, "other")

        repair_worktree(wt, repo_root=repo, branch_name="target")
        assert wt.exists()
        result = _git("branch", "--show-current", cwd=wt)
        assert result.stdout.strip() == "target"

    def test_fails_on_unclean(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        subprocess.run(["git", "branch", "wt"], cwd=repo, check=True)

        wt = tmp_path / "wt"
        _create_worktree(repo, wt, "wt")
        (wt / "dirty.txt").write_text("dirty")

        with pytest.raises(RuntimeError) as exc:
            repair_worktree(wt, repo_root=repo, branch_name="wt")
        assert "uncommitted changes" in str(exc.value)


class TestRemoveWorktreeSafe:
    def test_removes_worktree(self, tmp_path):
        repo = tmp_path / "repo"
        _init_repo(repo)
        subprocess.run(["git", "branch", "wt"], cwd=repo, check=True)

        wt = tmp_path / "wt"
        _create_worktree(repo, wt, "wt")
        assert wt.exists()

        _remove_worktree_safe(wt, repo_root=repo)
        assert not wt.exists()
