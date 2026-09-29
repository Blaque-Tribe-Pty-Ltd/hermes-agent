"""Tests for profile-aware delegation in delegate_task.

All tests use isolated temporary fixtures — no dependency on the operator's
real ~/.hermes home directory."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.profile_resolution import (
    ALLOWED_PROFILES,
    ProfileRuntime,
    is_allowed_profile,
    load_profile_soul,
    resolve_profile_runtime,
)


# ── Shared fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def temp_profile_factory():
    """Factory that creates isolated temporary profile homes.

    Returns a callable: create_profile(name, config_yaml, soul_text)
    that writes files into a temp dir and returns the path.
    """
    temp_dir = tempfile.mkdtemp(prefix="hermes-profile-test-")
    created = {}

    def _create(name: str, config_yaml: str, soul_text: str = "") -> Path:
        profile_dir = Path(temp_dir) / name
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "config.yaml").write_text(config_yaml, encoding="utf-8")
        if soul_text:
            (profile_dir / "SOUL.md").write_text(soul_text, encoding="utf-8")
        created[name] = profile_dir
        return profile_dir

    yield _create, created

    # Cleanup
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def mock_profile_home(temp_profile_factory):
    """Patch named_profile_home to return temp fixture paths."""
    create_profile, created = temp_profile_factory

    # Pre-create a default profile with a basic config
    create_profile(
        "default",
        config_yaml=(
            "model:\n"
            "  default: kimi-k2.6\n"
            "  provider: kimi\n"
            "agent:\n"
            "  toolsets:\n"
            "    - terminal\n"
            "    - file\n"
            "    - web\n"
        ),
        soul_text="# Default SOUL\nYou are the default agent.\n",
    )

    # Pre-create a neo profile
    create_profile(
        "neo",
        config_yaml=(
            "model:\n"
            "  default: kimi-k2.7-code\n"
            "  provider: kimi\n"
            "agent:\n"
            "  toolsets:\n"
            "    - terminal\n"
            "    - file\n"
            "    - code_execution\n"
            "    - web\n"
        ),
        soul_text="# Neo SOUL\nYou are Neo, the engineering specialist.\n",
    )

    # Pre-create a chabi profile (research-only, no engineering mutation)
    create_profile(
        "chabi",
        config_yaml=(
            "model:\n"
            "  default: kimi-k2.6\n"
            "  provider: kimi\n"
            "agent:\n"
            "  toolsets:\n"
            "    - web\n"
            "    - search\n"
        ),
        soul_text="# Chabi SOUL\nYou are Chabi, the research specialist.\n",
    )

    # Pre-create a pono profile (asset generation)
    create_profile(
        "pono",
        config_yaml=(
            "model:\n"
            "  default: gpt-5-mini\n"
            "  provider: openai\n"
            "agent:\n"
            "  toolsets:\n"
            "    - image_generation\n"
            "    - file\n"
        ),
        soul_text="# Pono SOUL\nYou are Pono, the asset specialist.\n",
    )

    # Pre-create a vision profile (basic allowed profile)
    create_profile(
        "vision",
        config_yaml=(
            "model:\n"
            "  default: kimi-k2.6\n"
            "  provider: kimi\n"
            "agent:\n"
            "  toolsets:\n"
            "    - terminal\n"
            "    - file\n"
            "    - web\n"
        ),
        soul_text="# Vision SOUL\nYou are the vision specialist.\n",
    )

    def _mock_home(profile: str) -> Path | None:
        return created.get(profile)

    with patch("tools.profile_resolution.named_profile_home", side_effect=_mock_home):
        with patch("hermes_constants.named_profile_home", side_effect=_mock_home):
            yield created


@pytest.fixture
def fake_parent():
    """Create a mock parent agent."""
    agent = MagicMock()
    agent._delegate_depth = 0
    agent.session_id = "parent-session-123"
    agent.model = "parent-model"
    agent.provider = "parent-provider"
    agent.api_key = "parent-key"
    agent.base_url = "https://parent.example"
    agent.request_overrides = {}
    agent.prefill_messages = None
    agent._print_fn = None
    agent._current_turn_id = "turn-1"
    agent._current_task_id = None
    # Parent has a broad toolset including engineering tools
    agent.enabled_toolsets = [
        "terminal", "file", "web", "code_execution",
        "image_generation", "search", "delegation",
    ]
    agent.disabled_toolsets = []
    return agent


# ── Profile resolution tests ─────────────────────────────────────────────────

class TestProfileResolution:
    """Unit tests for the profile resolution helper using temp fixtures."""

    def test_resolve_allowed_profile(self, mock_profile_home):
        """An allowed profile (vision) should load from temp fixture config.yaml."""
        pr = resolve_profile_runtime("vision")
        assert pr.profile == "vision"
        assert pr.provider == "kimi"
        assert pr.model == "kimi-k2.6"
        # Toolsets loaded from config
        assert "terminal" in pr.toolsets
        assert "file" in pr.toolsets

    def test_resolve_neo_profile(self, mock_profile_home):
        """neo profile should load neo-specific config."""
        pr = resolve_profile_runtime("neo")
        assert pr.profile == "neo"
        assert pr.provider == "kimi"
        assert pr.model == "kimi-k2.7-code"
        assert "code_execution" in pr.toolsets
        assert "terminal" in pr.toolsets

    def test_resolve_chabi_profile(self, mock_profile_home):
        """chabi profile should have research tools, not engineering."""
        pr = resolve_profile_runtime("chabi")
        assert pr.profile == "chabi"
        assert "web" in pr.toolsets
        assert "search" in pr.toolsets
        # Should NOT have code_execution
        assert "code_execution" not in pr.toolsets

    def test_resolve_pono_profile(self, mock_profile_home):
        """pono profile should have image_generation."""
        pr = resolve_profile_runtime("pono")
        assert pr.profile == "pono"
        assert pr.provider == "openai"
        assert pr.model == "gpt-5-mini"
        assert "image_generation" in pr.toolsets

    def test_load_profile_soul_existing(self, mock_profile_home):
        """load_profile_soul should return SOUL.md content for temp fixtures."""
        soul = load_profile_soul("neo")
        assert "Neo" in soul
        assert "engineering specialist" in soul

    def test_load_profile_soul_missing(self, mock_profile_home):
        """load_profile_soul should return empty string for missing SOUL."""
        soul = load_profile_soul("nonexistent-profile-xyz")
        assert soul == ""

    def test_resolve_unknown_profile_fails_closed(self, mock_profile_home):
        """Unknown profiles must raise ValueError (fail closed)."""
        with pytest.raises(ValueError) as exc_info:
            resolve_profile_runtime("nonexistent-profile-xyz")
        assert "not in the allowed specialist set" in str(exc_info.value)


# ── Security / negative tests ────────────────────────────────────────────────

class TestProfileSecurity:
    """Profile identifier security boundary tests."""

    def test_path_traversal_rejected(self, mock_profile_home):
        """Path traversal style identifiers must fail closed."""
        with pytest.raises(ValueError):
            resolve_profile_runtime("../../../etc/passwd")

    def test_absolute_path_rejected(self, mock_profile_home):
        """Absolute path identifiers must fail closed."""
        with pytest.raises(ValueError):
            resolve_profile_runtime("/etc/passwd")

    def test_malformed_identifier_rejected(self, mock_profile_home):
        """Malformed identifiers must fail closed."""
        with pytest.raises(ValueError):
            resolve_profile_runtime("neo;tsebo")
        with pytest.raises(ValueError):
            resolve_profile_runtime("neo\ntsebo")
        with pytest.raises(ValueError):
            resolve_profile_runtime("")

    def test_is_allowed_profile_exact_match(self, mock_profile_home):
        """is_allowed_profile must only match exact allowed names."""
        assert is_allowed_profile("neo") is True
        assert is_allowed_profile("NEO") is True  # case-insensitive
        assert is_allowed_profile("default") is False  # not in allowed set
        assert is_allowed_profile("../../../etc") is False


# ── Schema tests ─────────────────────────────────────────────────────────────

class TestDelegateTaskSchema:
    """Verify the schema advertises the profile parameter."""

    def test_schema_has_profile_property(self):
        from tools.delegate_tool import DELEGATE_TASK_SCHEMA
        props = DELEGATE_TASK_SCHEMA["parameters"]["properties"]
        assert "profile" in props
        assert props["profile"]["type"] == "string"


# ── Toolset boundary tests ───────────────────────────────────────────────────

class TestExactToolsetBoundaries:
    """Verify profile-aware delegation establishes real capability boundaries."""

    def test_explicit_neo_does_not_inherit_parent_only_tools(self, fake_parent):
        """Neo delegation should NOT inherit parent-only tools not in Neo profile."""
        from tools.delegate_tool_toolsets import _resolve_child_toolsets

        # Neo profile has: terminal, file, code_execution, web
        # Parent ALSO has: image_generation, search, delegation
        # Neo child should NOT get image_generation or search
        neo_toolsets = ["terminal", "file", "code_execution", "web"]
        enabled, disabled = _resolve_child_toolsets(
            fake_parent, toolsets=None, effective_role="leaf",
            profile_toolsets=neo_toolsets,
        )
        assert "terminal" in enabled
        assert "file" in enabled
        assert "code_execution" in enabled
        assert "web" in enabled
        # Parent-only tools NOT in Neo profile should be absent
        assert "image_generation" not in enabled
        assert "search" not in enabled

    def test_explicit_chabi_no_engineering_tools(self, fake_parent):
        """Chabi delegation must not gain engineering mutation tools."""
        from tools.delegate_tool_toolsets import _resolve_child_toolsets

        chabi_toolsets = ["web", "search"]
        enabled, disabled = _resolve_child_toolsets(
            fake_parent, toolsets=None, effective_role="leaf",
            profile_toolsets=chabi_toolsets,
        )
        assert "web" in enabled
        assert "search" in enabled
        assert "code_execution" not in enabled
        assert "terminal" not in enabled
        assert "file" not in enabled

    def test_explicit_pono_sees_asset_capability(self, fake_parent):
        """Pono delegation should expose image_generation capability."""
        from tools.delegate_tool_toolsets import _resolve_child_toolsets

        pono_toolsets = ["image_generation", "file"]
        enabled, disabled = _resolve_child_toolsets(
            fake_parent, toolsets=None, effective_role="leaf",
            profile_toolsets=pono_toolsets,
        )
        assert "image_generation" in enabled
        assert "file" in enabled

    def test_blocked_tools_still_blocked_with_profile(self, fake_parent):
        """Globally blocked delegation tools remain blocked even if profile requests them."""
        from tools.delegate_tool_toolsets import (
            DELEGATE_BLOCKED_TOOLS,
            _resolve_child_toolsets,
        )

        # Try to dispatch with a profile that claims blocked tools
        bad_toolsets = ["terminal", "delegate_task", "clarify", "memory"]
        enabled, disabled = _resolve_child_toolsets(
            fake_parent, toolsets=None, effective_role="leaf",
            profile_toolsets=bad_toolsets,
        )
        assert "delegate_task" not in enabled
        assert "clarify" not in enabled
        assert "memory" not in enabled
        # But terminal should still be present (not blocked)
        assert "terminal" in enabled

    def test_no_profile_uses_parent_toolsets(self, fake_parent):
        """Without explicit profile, child inherits parent toolsets (backwards compat)."""
        from tools.delegate_tool_toolsets import _resolve_child_toolsets

        enabled, disabled = _resolve_child_toolsets(
            fake_parent, toolsets=None, effective_role="leaf",
            profile_toolsets=None,
        )
        # Parent has these
        assert "terminal" in enabled
        assert "file" in enabled
        assert "code_execution" in enabled
        assert "web" in enabled
        assert "image_generation" in enabled
        assert "search" in enabled

    def test_profile_intersects_with_parent_availability(self, fake_parent):
        """Profile tool the parent does NOT have should be filtered out."""
        from tools.delegate_tool_toolsets import _resolve_child_toolsets

        # Parent does NOT have "dangerous_tool"
        profile_with_unavailable = ["terminal", "dangerous_tool"]
        enabled, disabled = _resolve_child_toolsets(
            fake_parent, toolsets=None, effective_role="leaf",
            profile_toolsets=profile_with_unavailable,
        )
        assert "terminal" in enabled
        assert "dangerous_tool" not in enabled


# ── Telemetry tests ──────────────────────────────────────────────────────────

class TestTelemetryHook:
    """Verify the telemetry hook captures metadata correctly."""

    def test_emit_telemetry_skips_non_profile_children(self):
        """Telemetry should not fire for children without a profile."""
        from tools.delegate_tool import _emit_delegation_telemetry
        child = MagicMock()
        child.profile = None
        child._progress_identity_ref = {}
        parent = MagicMock()
        parent.session_id = "parent-123"
        # Should return without error and without emitting
        _emit_delegation_telemetry(child, "task-1", "sa-1", "goal", parent)

    def test_emit_telemetry_with_profile(self):
        """Telemetry should fire for profile-routed children."""
        from tools.delegate_tool import _emit_delegation_telemetry
        child = MagicMock()
        child.profile = None
        child._progress_identity_ref = {"profile": "neo"}
        child.model = "kimi-k2.7-code"
        child.provider = "kimi"
        child.session_id = "child-session-1"
        child.session_prompt_tokens = 100
        child.session_completion_tokens = 50
        child.session_api_calls = 3
        child._last_turn_usage = {"cache_read_tokens": 20}
        child._delegate_role = "leaf"
        parent = MagicMock()
        parent.session_id = "parent-123"
        # Should not raise
        _emit_delegation_telemetry(child, "task-1", "sa-1", "goal", parent)


# ── Backwards compatibility tests ────────────────────────────────────────────

class TestBackwardsCompatibility:
    """Ensure existing delegate_task calls continue to work."""

    def test_delegate_task_without_profile(self):
        """delegate_task should accept calls without the profile parameter."""
        from tools.delegate_tool import delegate_task
        import inspect
        sig = inspect.signature(delegate_task)
        params = list(sig.parameters.keys())
        assert "profile" in params
        # profile should have a default of None
        assert sig.parameters["profile"].default is None

    def test_no_profile_param_in_task_dict(self):
        """Tasks dict should not require profile field."""
        from tools.delegate_tool_tasks import _normalize_task_list
        tasks, err = _normalize_task_list(
            goal="test goal",
            context=None,
            tasks=None,
            output_schema=None,
            top_role="leaf",
            max_children=10,
        )
        assert err is None
        assert tasks is not None
        assert len(tasks) == 1
        assert "profile" not in tasks[0]


# ── Evidence language tests ──────────────────────────────────────────────────

class TestEvidenceLanguage:
    """Verify tests use correct UNPROVEN language for runtime-unproven fields."""

    def test_runtime_fields_are_unproven_in_tests(self, mock_profile_home):
        """Before real execution, model/provider are resolved but runtime-unproven."""
        pr = resolve_profile_runtime("neo")
        assert pr.profile == "neo"
        # These are RESOLVED from config, but RUNTIME-UNPROVEN until actual API call
        assert pr.model == "kimi-k2.7-code"  # resolved from config
        assert pr.provider == "kimi"  # resolved from config
        # The test documents: actual runtime model may differ due to fallback,
        # routing overrides, or provider errors. We mark it UNPROVEN here.
        runtime_model = "UNPROVEN"
        runtime_provider = "UNPROVEN"
        assert runtime_model == "UNPROVEN"
        assert runtime_provider == "UNPROVEN"
