"""MoonPie conversations remain in one Hermes session across inference backends."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hermes_cli.moonpie_adapter import MoonPieHermesAdapter


@pytest.mark.asyncio
async def test_second_turn_reuses_session_history_and_runtime(monkeypatch):
    class FakeSessionDB:
        def __init__(self):
            self.messages = {}

        def get_messages_as_conversation(self, session_id):
            return list(self.messages.get(session_id, []))

    class FakeAgent:
        instances = []

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            self.tools = [{"function": {"name": "delegate_task"}}]
            self.__class__.instances.append(self)

        def run_conversation(self, message, *, conversation_history, **_kwargs):
            remembered = any("amber" in str(item.get("content", "")) for item in conversation_history)
            response = "amber" if remembered else "noted"
            self.session_db.messages[self.session_id] = [
                *conversation_history,
                {"role": "user", "content": message},
                {"role": "assistant", "content": response},
            ]
            return {"final_response": response}

    db = FakeSessionDB()
    adapter = MoonPieHermesAdapter(session_db_provider=lambda: db)
    monkeypatch.setattr(
        "hermes_cli.config.load_config_readonly",
        lambda: {"model": {"provider": "custom", "default": "Qwen3.5-0.8B-Q4_0"}},
    )

    with patch("run_agent.AIAgent", FakeAgent):
        first = await adapter.chat(
            "Remember amber",
            lambda _part: None,
            device_id="device-1",
            conversation_id="conversation-1",
            session_key="moonpie_device-1",
        )
        second = await adapter.chat(
            "What did I ask you to remember?",
            lambda _part: None,
            device_id="device-1",
            conversation_id="conversation-1",
            session_key="moonpie_device-1",
        )

    assert first == "noted"
    assert second == "amber"
    assert len(FakeAgent.instances) == 1
    agent = FakeAgent.instances[0]
    assert agent.model == "Qwen3.5-0.8B-Q4_0"
    assert agent.gateway_session_key == "moonpie_device-1"
    assert agent.load_soul_identity is True
    assert agent.tools[0]["function"]["name"] == "delegate_task"
