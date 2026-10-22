"""MoonPie agents are built after MCP discovery has been fired and waited for."""

from unittest.mock import patch

import pytest

from hermes_cli.moonpie_adapter import MoonPieHermesAdapter


class _DB:
    def get_messages_as_conversation(self, session_id):
        return []


def _agent_class(events):
    class FakeAgent:
        def __init__(self, **kwargs):
            events.append("agent built")
            self.__dict__.update(kwargs)

        def run_conversation(self, message, *, conversation_history, **_kwargs):
            return {"final_response": "ok"}

    return FakeAgent


async def _chat(adapter, conversation="c1"):
    return await adapter.chat(
        "hi", lambda _p: None, device_id="d", conversation_id=conversation, session_key="moonpie_d"
    )


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr("hermes_cli.config.load_config_readonly", lambda: {"model": {"provider": "custom", "default": "m"}})


@pytest.mark.asyncio
async def test_discovery_is_waited_for_before_the_agent_is_built():
    events = []
    adapter = MoonPieHermesAdapter(session_db_provider=lambda: _DB())
    with patch("run_agent.AIAgent", _agent_class(events)), \
         patch("hermes_cli.mcp_startup.wait_for_mcp_discovery", side_effect=lambda timeout=None, **kw: events.append(f"discovery({timeout})")):
        assert await _chat(adapter) == "ok"
    assert events[0].startswith("discovery(")
    assert events[1] == "agent built"
    assert float(events[0][len("discovery("):-1]) >= 10  # long enough for a server that is still connecting


@pytest.mark.asyncio
async def test_a_conversation_is_waited_for_once_not_on_every_message():
    events = []
    adapter = MoonPieHermesAdapter(session_db_provider=lambda: _DB())
    with patch("run_agent.AIAgent", _agent_class(events)), \
         patch("hermes_cli.mcp_startup.wait_for_mcp_discovery", side_effect=lambda timeout=None, **kw: events.append("discovery")):
        await _chat(adapter)
        await _chat(adapter)
    assert events.count("discovery") == 1
    assert events.count("agent built") == 1


@pytest.mark.asyncio
async def test_a_failing_discovery_wait_does_not_block_the_turn():
    events = []
    adapter = MoonPieHermesAdapter(session_db_provider=lambda: _DB())
    with patch("run_agent.AIAgent", _agent_class(events)), \
         patch("hermes_cli.mcp_startup.wait_for_mcp_discovery", side_effect=RuntimeError("boom")):
        assert await _chat(adapter) == "ok"
    assert events == ["agent built"]
