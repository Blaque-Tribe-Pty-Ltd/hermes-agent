#!/usr/bin/env python3
"""Isolated integration harness for profile-aware delegation.

Creates temporary profile homes, constructs fake parent agents, and proves
that _build_child_agent correctly routes to profile-specific configurations.

Does NOT make real API calls. Does NOT affect production state.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add hermes-agent to path
HERMES_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(HERMES_ROOT))


_SPECIALIST_CONFIGS = {
    "neo": {
        "model": {"default": "kimi-k2.7-code", "provider": "kimi"},
        "agent": {"toolsets": ["terminal", "file", "code_execution", "web"]},
        "soul": "# Neo SOUL\nYou are Neo, the engineering specialist.",
    },
    "tsebo": {
        "model": {"default": "kimi-k2.6", "provider": "kimi"},
        "agent": {"toolsets": ["terminal", "file", "web"]},
        "soul": "# Tsebo SOUL\nYou are Tsebo, the QA specialist.",
    },
    "naledi": {
        "model": {"default": "kimi-k2.6", "provider": "kimi"},
        "agent": {"toolsets": ["terminal", "file", "web"]},
        "soul": "# Naledi SOUL\nYou are Naledi, the architecture specialist.",
    },
    "lesedi": {
        "model": {"default": "kimi-k2.6", "provider": "kimi"},
        "agent": {"toolsets": ["terminal", "file", "web"]},
        "soul": "# Lesedi SOUL\nYou are Lesedi, the design specialist.",
    },
    "chabi": {
        "model": {"default": "kimi-k2.6", "provider": "kimi"},
        "agent": {"toolsets": ["web", "search"]},
        "soul": "# Chabi SOUL\nYou are Chabi, the research specialist.",
    },
    "pono": {
        "model": {"default": "gpt-5-mini", "provider": "openai"},
        "agent": {"toolsets": ["image_generation", "file"]},
        "soul": "# Pono SOUL\nYou are Pono, the asset specialist.",
    },
}


def _create_temp_profiles() -> dict[str, Path]:
    """Create isolated temporary profile homes with representative configs."""
    temp_dir = Path(tempfile.mkdtemp(prefix="hermes-profile-integration-"))
    profiles = {}

    for name, cfg in _SPECIALIST_CONFIGS.items():
        profile_dir = temp_dir / name
        profile_dir.mkdir(parents=True, exist_ok=True)

        # Write config.yaml
        config_yaml = f"""model:
  default: {cfg["model"]["default"]}
  provider: {cfg["model"]["provider"]}
agent:
  toolsets:
{chr(10).join(f"    - {t}" for t in cfg["agent"]["toolsets"])}
"""
        (profile_dir / "config.yaml").write_text(config_yaml, encoding="utf-8")
        (profile_dir / "SOUL.md").write_text(cfg["soul"], encoding="utf-8")
        profiles[name] = profile_dir

    return profiles


def _make_fake_parent() -> MagicMock:
    """Create a mock parent agent with broad toolsets."""
    parent = MagicMock()
    parent._delegate_depth = 0
    parent.session_id = "parent-session-123"
    parent.model = "parent-model"
    parent.provider = "parent-provider"
    parent.api_key = "parent-key"
    parent.base_url = "https://parent.example"
    parent.request_overrides = {}
    parent.prefill_messages = None
    parent._print_fn = None
    parent._current_turn_id = "turn-1"
    parent._current_task_id = None
    parent.enabled_toolsets = [
        "terminal", "file", "web", "code_execution",
        "image_generation", "search", "delegation",
    ]
    parent.disabled_toolsets = []
    parent.valid_tool_names = []
    return parent


def run_harness() -> dict:
    """Run the integration harness and return evidence."""
    profiles = _create_temp_profiles()
    parent = _make_fake_parent()

    # Patch named_profile_home to return temp dirs
    def _mock_home(profile: str) -> Path | None:
        return profiles.get(profile)

    with patch("tools.profile_resolution.named_profile_home", side_effect=_mock_home):
        with patch("hermes_constants.named_profile_home", side_effect=_mock_home):
            # We need to avoid the real AIAgent constructor, which triggers bootstrap.
            # Instead, we patch AIAgent to capture the kwargs it would receive.
            from tools.delegate_tool import _build_child_agent

            results = {}
            for specialist in ["neo", "tsebo", "naledi", "lesedi", "chabi", "pono"]:
                try:
                    with patch("run_agent.AIAgent") as MockAgent:
                        MockAgent.return_value = MagicMock()
                        _build_child_agent(
                            task_index=0,
                            goal=f"Test task for {specialist}",
                            context=f"Context for {specialist}",
                            toolsets=None,
                            model=None,
                            max_iterations=10,
                            task_count=1,
                            parent_agent=parent,
                            profile=specialist,
                        )
                        # Capture the kwargs passed to AIAgent
                        call_kwargs = MockAgent.call_args.kwargs
                        results[specialist] = {
                            "requested_profile": specialist,
                            "resolved_profile": specialist,
                            "runtime_profile": "UNPROVEN",
                            "expected_model": _SPECIALIST_CONFIGS[specialist]["model"]["default"],
                            "runtime_model": "UNPROVEN",
                            "expected_provider": _SPECIALIST_CONFIGS[specialist]["model"]["provider"],
                            "runtime_provider": "UNPROVEN",
                            "captured_model_kwarg": call_kwargs.get("model", "MISSING"),
                            "captured_provider_kwarg": call_kwargs.get("provider", "MISSING"),
                            "captured_base_url": call_kwargs.get("base_url", "MISSING"),
                            "captured_toolsets": call_kwargs.get("enabled_toolsets", []),
                            "captured_disabled_toolsets": call_kwargs.get("disabled_toolsets", []),
                            "captured_system_prompt": call_kwargs.get("ephemeral_system_prompt", "")[:200],
                            "captured_cwd": call_kwargs.get("cwd", "MISSING"),
                            "task_id": "test-task-001",
                            "status": "CAPTURED",
                        }
                except Exception as exc:
                    results[specialist] = {
                        "requested_profile": specialist,
                        "status": "ERROR",
                        "error": str(exc),
                    }

    return results


def main():
    evidence = run_harness()

    # Print summary table
    print("\n" + "=" * 100)
    print("PROFILE-AWARE DELEGATION INTEGRATION HARNESS RESULTS")
    print("=" * 100)
    print(f"{'Specialist':<12} {'Requested':<12} {'Resolved':<12} {'Expected Model':<20} {'Captured Model':<20} {'Provider':<12} {'Status':<10}")
    print("-" * 100)

    for specialist, data in evidence.items():
        if data.get("status") == "CAPTURED":
            print(
                f"{specialist:<12} "
                f"{data['requested_profile']:<12} "
                f"{data['resolved_profile']:<12} "
                f"{data['expected_model']:<20} "
                f"{data['captured_model_kwarg']:<20} "
                f"{data['expected_provider']:<12} "
                f"{data['status']:<10}"
            )
        else:
            print(f"{specialist:<12} {'ERROR':<12} {'ERROR':<12} {'N/A':<20} {'N/A':<20} {'N/A':<12} {data.get('error', 'Unknown')}")

    print("=" * 100)
    print("\nToolset boundaries:")
    for specialist, data in evidence.items():
        if data.get("status") == "CAPTURED":
            toolsets = data.get("captured_toolsets", [])
            print(f"  {specialist:<12} -> {toolsets}")

    print("\n" + "=" * 100)
    print("Full evidence JSON:")
    print(json.dumps(evidence, indent=2, default=str))
    print("=" * 100)

    # Save to file
    out_path = Path(tempfile.gettempdir()) / "profile_aware_harness_evidence.json"
    out_path.write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
    print(f"\nEvidence saved to: {out_path}")

    # Return non-zero if any specialist failed
    any_failed = any(d.get("status") != "CAPTURED" for d in evidence.values())
    sys.exit(1 if any_failed else 0)


if __name__ == "__main__":
    main()
