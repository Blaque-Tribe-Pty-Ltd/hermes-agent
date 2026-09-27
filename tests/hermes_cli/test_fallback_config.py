"""Tests for hermes_cli/fallback_config.py — fallback entry API-key resolution."""

from agent.secret_scope import reset_secret_scope, set_secret_scope
from hermes_cli.fallback_config import effective_runtime_provider, resolve_entry_api_key, _iter_fallback_entries, get_fallback_chain


class TestResolveEntryApiKey:
    def test_inline_api_key_wins(self, monkeypatch):
        monkeypatch.setenv("FB_KEY", "env-key")
        entry = {"provider": "custom", "api_key": "inline-key", "key_env": "FB_KEY"}
        assert resolve_entry_api_key(entry) == "inline-key"


    def test_no_key_fields_returns_none(self):
        assert resolve_entry_api_key({"provider": "openrouter", "model": "glm"}) is None


    def test_whitespace_inline_key_falls_through_to_env(self, monkeypatch):
        monkeypatch.setenv("FB_KEY", "env-key")
        entry = {"api_key": "   ", "key_env": "FB_KEY"}
        assert resolve_entry_api_key(entry) == "env-key"

    def test_key_env_resolves_from_active_secret_scope_not_raw_env(self, monkeypatch):
        # Multiplexed gateway: os.environ holds another profile's key, but the
        # active per-turn secret scope holds this profile's key. The scoped
        # value must win — a raw os.getenv() would leak the other profile's
        # credential (issue #74311).
        monkeypatch.setenv("FB_KEY", "fake-other-profile-key")
        token = set_secret_scope({"FB_KEY": "fake-active-profile-key"})
        try:
            assert resolve_entry_api_key({"key_env": "FB_KEY"}) == "fake-active-profile-key"
        finally:
            reset_secret_scope(token)

    def test_key_env_falls_back_to_env_when_no_active_scope(self, monkeypatch):
        # Non-multiplexed / single-profile behavior must be unchanged: with no
        # secret scope installed, resolution still reads os.environ.
        monkeypatch.setenv("FB_KEY", "env-key")
        assert resolve_entry_api_key({"key_env": "FB_KEY"}) == "env-key"


class TestEffectiveRuntimeProvider:
    """Named custom fallback entries must keep their configured identity (#98739)."""

    def test_named_custom_entry_keeps_configured_id(self):
        entry = {"provider": "my-custom-provider", "model": "some-model"}
        runtime = {"provider": "custom", "requested_provider": "my-custom-provider"}
        assert effective_runtime_provider(entry, runtime) == "my-custom-provider"

    def test_requested_provider_missing_falls_back_to_entry(self):
        entry = {"provider": "my-custom-provider", "model": "some-model"}
        runtime = {"provider": "custom"}
        assert effective_runtime_provider(entry, runtime) == "my-custom-provider"

    def test_builtin_provider_untouched(self):
        entry = {"provider": "openrouter", "model": "glm"}
        runtime = {"provider": "openrouter", "requested_provider": "openrouter"}
        assert effective_runtime_provider(entry, runtime) == "openrouter"

    def test_genuinely_bare_custom_stays_custom(self):
        # Ad-hoc endpoint: user literally configured provider: custom.
        entry = {"provider": "custom", "model": "some-model"}
        runtime = {"provider": "custom", "requested_provider": "custom"}
        assert effective_runtime_provider(entry, runtime) == "custom"

    def test_none_inputs_are_safe(self):
        assert effective_runtime_provider(None, None) == ""


class TestIterFallbackEntries:
    """Dict-of-dicts config format (e.g. YAML parsed with string indices) must flatten correctly."""

    def test_list_format(self):
        raw = [{"provider": "openai-api", "model": "gpt-4o"}]
        assert _iter_fallback_entries(raw) == [{"provider": "openai-api", "model": "gpt-4o"}]

    def test_single_dict_format(self):
        raw = {"provider": "openai-api", "model": "gpt-4o"}
        assert _iter_fallback_entries(raw) == [{"provider": "openai-api", "model": "gpt-4o"}]

    def test_dict_of_dicts_format(self):
        raw = {"0": {"provider": "openai-api", "model": "gpt-4o"}}
        assert _iter_fallback_entries(raw) == [{"provider": "openai-api", "model": "gpt-4o"}]

    def test_dict_of_dicts_multiple_entries(self):
        raw = {
            "0": {"provider": "openai-api", "model": "gpt-4o"},
            "1": {"provider": "anthropic", "model": "claude-3"},
        }
        entries = _iter_fallback_entries(raw)
        assert len(entries) == 2
        assert entries[0] == {"provider": "openai-api", "model": "gpt-4o"}
        assert entries[1] == {"provider": "anthropic", "model": "claude-3"}

    def test_empty_dict(self):
        assert _iter_fallback_entries({}) == []

    def test_none(self):
        assert _iter_fallback_entries(None) == []

    def test_invalid_entries_filtered(self):
        raw = [
            {"provider": "openai-api", "model": "gpt-4o"},
            {"provider": ""},  # missing model
            {"model": "gpt-4"},  # missing provider
            "not-a-dict",
        ]
        entries = _iter_fallback_entries(raw)
        assert len(entries) == 1
        assert entries[0] == {"provider": "openai-api", "model": "gpt-4o"}

    def test_mixed_dict_of_dicts_with_invalid(self):
        raw = {
            "0": {"provider": "openai-api", "model": "gpt-4o"},
            "1": {"provider": ""},  # invalid, filtered
        }
        entries = _iter_fallback_entries(raw)
        assert len(entries) == 1
        assert entries[0] == {"provider": "openai-api", "model": "gpt-4o"}


class TestGetFallbackChain:
    def test_dict_of_dicts_fallback_providers(self):
        config = {
            "fallback_providers": {
                "0": {"provider": "openai-api", "model": "gpt-4o"}
            }
        }
        chain = get_fallback_chain(config)
        assert len(chain) == 1
        assert chain[0] == {"provider": "openai-api", "model": "gpt-4o"}

    def test_merges_fallback_providers_and_fallback_model(self):
        config = {
            "fallback_providers": {
                "0": {"provider": "openai-api", "model": "gpt-4o"}
            },
            "fallback_model": {"provider": "anthropic", "model": "claude-3"},
        }
        chain = get_fallback_chain(config)
        assert len(chain) == 2
        assert chain[0] == {"provider": "openai-api", "model": "gpt-4o"}
        assert chain[1] == {"provider": "anthropic", "model": "claude-3"}

    def test_deduplicates_entries(self):
        config = {
            "fallback_providers": [
                {"provider": "openai-api", "model": "gpt-4o"}
            ],
            "fallback_model": {"provider": "openai-api", "model": "gpt-4o"},
        }
        chain = get_fallback_chain(config)
        assert len(chain) == 1

    def test_empty_config(self):
        assert get_fallback_chain({}) == []
        assert get_fallback_chain(None) == []
