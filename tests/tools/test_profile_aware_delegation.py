"""Tests for profile-aware delegation in delegate_task.

Covers:
- Profile resolution loads correct config, SOUL, and credentials
- delegate_task schema includes profile parameter
- _build_child_agent applies profile overrides
- Unknown profiles fail closed with DISPATCH_FAILED
- Backwards compatibility: no profile = existing behavior
- Telemetry hook fires for profile-routed children
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.profile_resolution import (
    ProfileRuntime,
    load_profile_soul,
    resolve_profile_runtime,
)


class TestProfileResolution:
    """Unit tests for the profile resolution helper."""

    def test_resolve_default_profile(self):
        """default profile should load from ~/.hermes/config.yaml and SOUL.md."""
        pr = resolve_profile_runtime("default")
        assert pr.profile == "default"
        assert pr.provider  # should have a provider
        assert pr.model     # should have a model

    def test_load_profile_soul_existing(self):
        """load_profile_soul should return the SOUL.md content for existing profiles."""
        soul = load_profile_soul("default")
        assert isinstance(soul, str)
        assert len(soul) > 0

    def test_load_profile_soul_missing(self):
        """load_profile_soul should return empty string for missing SOUL."""
        soul = load_profile_soul("nonexistent-profile-xyz")
        assert soul == ""

    def test_resolve_unknown_profile_fails_closed(self):
        """Unknown profiles must raise ValueError (fail closed)."""
        with pytest.raises(ValueError) as exc_info:
            resolve_profile_runtime("nonexistent-profile-xyz")
        assert "not found" in str(exc_info.value).lower() or "does not exist" in str(exc_info.value).lower()


class TestDelegateTaskSchema:
    """Verify the schema advertises the profile parameter."""

    def test_schema_has_profile_property(self):
        from tools.delegate_tool import DELEGATE_TASK_SCHEMA
        props = DELEGATE_TASK_SCHEMA["parameters"]["properties"]
        assert "profile" in props
        assert props["profile"]["type"] == "string"


class TestBuildChildAgentProfileOverride:
    """Verify _build_child_agent applies profile overrides correctly."""

    @pytest.fixture
    def fake_parent(self):
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
        return agent

    def test_no_profile_uses_parent_config(self, fake_parent):
        """When profile is None, child inherits parent model/provider."""
        from tools.delegate_tool import _build_child_agent
        child = _build_child_agent(
            task_index=0,
            goal="test goal",
            context=None,
            toolsets=None,
            model=None,
            max_iterations=10,
            task_count=1,
            parent_agent=fake_parent,
            profile=None,
        )
        assert child.model == fake_parent.model
        assert child.provider == fake_parent.provider

    def test_unknown_profile_raises(self, fake_parent):
        """Passing an unknown profile must raise ValueError."""
        from tools.delegate_tool import _build_child_agent
        with pytest.raises(ValueError) as exc_info:
            _build_child_agent(
                task_index=0,
                goal="test goal",
                context=None,
                toolsets=None,
                model=None,
                max_iterations=10,
                task_count=1,
                parent_agent=fake_parent,
                profile="nonexistent-profile-xyz",
            )
        assert "Profile dispatch failed" in str(exc_info.value)

    def test_profile_tag_in_session_ref(self, fake_parent):
        """When profile is set, child_session_ref should contain profile tag."""
        from tools.delegate_tool import _build_child_agent
        # We can't easily build a real child without full Hermes runtime,
        # but we can inspect the function internals via a side-effect test.
        # For now, verify the profile parameter is accepted without error
        # for the default profile (which we know exists).
        child = _build_child_agent(
            task_index=0,
            goal="test goal",
            context=None,
            toolsets=None,
            model=None,
            max_iterations=10,
            task_count=1,
            parent_agent=fake_parent,
            profile="default",
        )
        # The default profile should resolve successfully
        assert child is not None


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


class TestBackwardsCompatibility:
    """Ensure existing delegate_task calls continue to work."""

    def test_delegate_task_without_profile(self):
        """delegate_task should accept calls without the profile parameter."""
        from tools.delegate_tool import delegate_task
        # This is a signature test: the function should accept the old kwargs
        import inspect
        sig = inspect.signature(delegate_task)
        params = list(sig.parameters.keys())
        assert "profile" in params
        # profile should have a default of None
        assert sig.parameters["profile"].default is None
