"""Tests for agent/agent_init.py _fallback_entries dict-of-dicts handling."""

from agent.agent_init import _fallback_entries


class TestFallbackEntries:
    def test_list_format(self):
        raw = [{"provider": "openai-api", "model": "gpt-4o"}]
        assert _fallback_entries(raw) == [{"provider": "openai-api", "model": "gpt-4o"}]

    def test_single_dict_format(self):
        raw = {"provider": "openai-api", "model": "gpt-4o"}
        assert _fallback_entries(raw) == [{"provider": "openai-api", "model": "gpt-4o"}]

    def test_dict_of_dicts_format(self):
        raw = {"0": {"provider": "openai-api", "model": "gpt-4o"}}
        assert _fallback_entries(raw) == [{"provider": "openai-api", "model": "gpt-4o"}]

    def test_dict_of_dicts_multiple_entries(self):
        raw = {
            "0": {"provider": "openai-api", "model": "gpt-4o"},
            "1": {"provider": "anthropic", "model": "claude-3"},
        }
        entries = _fallback_entries(raw)
        assert len(entries) == 2
        assert entries[0] == {"provider": "openai-api", "model": "gpt-4o"}
        assert entries[1] == {"provider": "anthropic", "model": "claude-3"}

    def test_empty_dict(self):
        assert _fallback_entries({}) == []

    def test_none(self):
        assert _fallback_entries(None) == []

    def test_invalid_entries_filtered(self):
        raw = [
            {"provider": "openai-api", "model": "gpt-4o"},
            {"provider": ""},
            {"model": "gpt-4"},
            "not-a-dict",
        ]
        entries = _fallback_entries(raw)
        assert len(entries) == 1
        assert entries[0] == {"provider": "openai-api", "model": "gpt-4o"}

    def test_mixed_dict_of_dicts_with_invalid(self):
        raw = {
            "0": {"provider": "openai-api", "model": "gpt-4o"},
            "1": {"provider": ""},
        }
        entries = _fallback_entries(raw)
        assert len(entries) == 1
        assert entries[0] == {"provider": "openai-api", "model": "gpt-4o"}
