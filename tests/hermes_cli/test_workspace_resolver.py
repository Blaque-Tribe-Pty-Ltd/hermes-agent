"""Tests for hermes_cli.workspace_resolver."""

import os
import platform
from pathlib import Path
from unittest.mock import patch

import pytest

from hermes_cli.workspace_resolver import (
    _detect_host_os,
    _guess_path_os,
    _load_mapping,
    _repo_name_from_path,
    _resolve_via_mapping,
    resolve_workspace_path,
    resolve_worktree_path,
)


class TestDetectHostOS:
    def test_linux(self):
        with patch("platform.system", return_value="Linux"):
            assert _detect_host_os() == "linux"

    def test_darwin(self):
        with patch("platform.system", return_value="Darwin"):
            assert _detect_host_os() == "macos"

    def test_windows(self):
        with patch("platform.system", return_value="Windows"):
            assert _detect_host_os() == "windows"


class TestGuessPathOS:
    def test_macos_users(self):
        assert _guess_path_os("/Users/nkhatho/Projects/foo") == "macos"

    def test_linux_home(self):
        assert _guess_path_os("/home/moonbeam/workspaces/foo") == "linux"

    def test_windows_c(self):
        assert _guess_path_os("C:\\\\Users\\\\foo") == "windows"

    def test_unknown(self):
        assert _guess_path_os("/opt/foo") is None


class TestResolveViaMapping:
    def test_valid_mac_to_linux(self):
        mapping = {
            "mappings": {
                "moonpie-macos": {
                    "hosts": {
                        "macos-ziggy": {"path": "/Users/nkhatho/Projects/moonpie-macos"},
                        "linux-moonbeam": {"path": "/home/moonbeam/workspaces/moonpie-macos"},
                    },
                    "default_host": "linux-moonbeam",
                }
            }
        }
        resolved, project = _resolve_via_mapping(
            "/Users/nkhatho/Projects/moonpie-macos/.worktrees/t_123",
            "linux",
            mapping,
        )
        assert resolved == "/home/moonbeam/workspaces/moonpie-macos"
        assert project == "moonpie-macos"

    def test_missing_mapping(self):
        mapping = {"mappings": {}}
        resolved, project = _resolve_via_mapping(
            "/Users/nkhatho/Projects/unknown-project",
            "linux",
            mapping,
        )
        assert resolved is None
        assert project is None

    def test_no_current_host_entry(self):
        mapping = {
            "mappings": {
                "foo": {
                    "hosts": {
                        "macos-ziggy": {"path": "/Users/nkhatho/Projects/foo"},
                    },
                }
            }
        }
        resolved, project = _resolve_via_mapping(
            "/Users/nkhatho/Projects/foo",
            "linux",
            mapping,
        )
        assert resolved is None
        assert project is None


class TestRepoNameFromPath:
    def test_worktree_path(self):
        assert _repo_name_from_path("/home/moonbeam/workspaces/moonpie-macos/.worktrees/t_123") == "moonpie-macos"

    def test_plain_path(self):
        assert _repo_name_from_path("/home/moonbeam/workspaces/second_brain") == "second_brain"


class TestResolveWorkspacePath:
    def test_local_path_unchanged(self, tmp_path):
        """A path matching the current host is used as-is."""
        current_os = _detect_host_os()
        if current_os == "linux":
            path = str(tmp_path)
            result = resolve_workspace_path(path, task_id="t_test", validate_exists=False)
            assert result == path

    def test_foreign_path_fallback_without_mapping(self):
        """A foreign host path with no mapping uses fallback pattern."""
        current_os = _detect_host_os()
        if current_os == "linux":
            result = resolve_workspace_path(
                "/Users/nkhatho/Projects/moonpie-macos/.worktrees/t_123",
                task_id="t_test",
                validate_exists=False,
            )
            assert result == "/home/moonbeam/workspaces/moonpie-macos"

    def test_foreign_path_resolved_with_mapping(self, tmp_path):
        """A foreign host path with a valid mapping is rewritten."""
        current_os = _detect_host_os()
        if current_os != "linux":
            pytest.skip("Test assumes Linux host")

        mapping_content = """
version: 1.0
mappings:
  moonpie-macos:
    hosts:
      macos-ziggy:
        path: /Users/nkhatho/Projects/moonpie-macos
      linux-moonbeam:
        path: /home/moonbeam/workspaces/moonpie-macos
    default_host: linux-moonbeam
"""
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        mapping_file = hermes_home / "workspace-mapping.yaml"
        mapping_file.write_text(mapping_content)

        with patch.dict(os.environ, {"HERMES_HOME": str(hermes_home)}):
            result = resolve_workspace_path(
                "/Users/nkhatho/Projects/moonpie-macos/.worktrees/t_123",
                task_id="t_test",
                validate_exists=False,
            )
            assert result == "/home/moonbeam/workspaces/moonpie-macos"

    def test_invalid_mapped_directory_raises(self):
        """If validate_exists=True and resolved path doesn't exist, raise."""
        with pytest.raises(ValueError, match="does not exist"):
            resolve_workspace_path(
                "/nonexistent/path/that/should/not/exist",
                task_id="t_test",
                validate_exists=True,
            )

    def test_stale_macos_path_with_mapping(self, tmp_path):
        """A task containing a stale macOS path gets rewritten via mapping."""
        current_os = _detect_host_os()
        if current_os != "linux":
            pytest.skip("Test assumes Linux host")

        mapping_content = """
mappings:
  second_brain:
    hosts:
      macos-ziggy:
        path: /Users/nkhatho/Projects/second_brain
      linux-moonbeam:
        path: /home/moonbeam/workspaces/obsidian-vault
    default_host: linux-moonbeam
"""
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        mapping_file = hermes_home / "workspace-mapping.yaml"
        mapping_file.write_text(mapping_content)

        with patch.dict(os.environ, {"HERMES_HOME": str(hermes_home)}):
            result = resolve_workspace_path(
                "/Users/nkhatho/Projects/second_brain/.worktrees/t_456",
                task_id="t_test",
                validate_exists=False,
            )
            assert result == "/home/moonbeam/workspaces/obsidian-vault"


class TestResolveWorktreePath:
    def test_worktree_fallback_without_mapping(self):
        """Worktree resolution falls back to pattern when no mapping exists."""
        current_os = _detect_host_os()
        if current_os == "linux":
            result = resolve_worktree_path(
                "/Users/nkhatho/Projects/foo/.worktrees/t_123",
                task_id="t_test",
            )
            assert result == "/home/moonbeam/workspaces/foo"
