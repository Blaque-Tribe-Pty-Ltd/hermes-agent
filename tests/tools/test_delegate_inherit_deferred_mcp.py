"""A delegated child built from a parent on "all tools" inherits the MCP toolsets the parent reaches via tool_search."""

from types import SimpleNamespace
from unittest.mock import patch

from tools.delegate_tool_toolsets import _resolve_child_toolsets

_TOOLSET_OF = {"terminal": "terminal", "read_file": "file", "tool_search": "tool_search"}


def _parent(valid_tool_names, enabled_toolsets=None):
    return SimpleNamespace(enabled_toolsets=enabled_toolsets, valid_tool_names=set(valid_tool_names), disabled_toolsets=None)


def _resolve(parent, *, inherit=True, registered=("mcp-mac_workspace", "terminal", "file")):
    with patch("tools.delegate_tool_toolsets._get_inherit_mcp_toolsets", return_value=inherit), \
         patch("model_tools.get_toolset_for_tool", side_effect=lambda name: _TOOLSET_OF.get(name)), \
         patch("tools.registry.registry.get_registered_toolset_names", return_value=list(registered)):
        return _resolve_child_toolsets(parent, None, "leaf")[0]


def test_child_gets_the_deferred_mcp_toolset_the_parent_can_reach():
    """Deferred MCP tools are not in valid_tool_names, so the derived toolsets used to lack them entirely."""
    enabled = _resolve(_parent(["terminal", "read_file", "tool_search"]))
    assert "mcp-mac_workspace" in enabled
    assert "terminal" in enabled and "file" in enabled


def test_opting_out_of_mcp_inheritance_keeps_the_child_without_mcp_tools():
    enabled = _resolve(_parent(["terminal", "read_file"]), inherit=False)
    assert not any(str(t).startswith("mcp-") for t in enabled)


def test_an_explicit_parent_toolset_list_is_inherited_exactly():
    """A parent with its own enabled list is untouched: no extra MCP toolsets are added behind its back."""
    enabled = _resolve(_parent(["terminal"], enabled_toolsets=["terminal", "file"]))
    assert sorted(enabled) == ["file", "terminal"]


def test_only_mcp_toolsets_are_added_not_other_registered_ones():
    enabled = _resolve(_parent(["terminal"]), registered=("mcp-a", "browser", "mcp-b", "terminal"))
    assert "mcp-a" in enabled and "mcp-b" in enabled
    assert "browser" not in enabled


def test_an_unreadable_registry_degrades_to_the_old_behaviour():
    parent = _parent(["terminal"])
    with patch("tools.delegate_tool_toolsets._get_inherit_mcp_toolsets", return_value=True), \
         patch("model_tools.get_toolset_for_tool", side_effect=lambda name: _TOOLSET_OF.get(name)), \
         patch("tools.registry.registry.get_registered_toolset_names", side_effect=RuntimeError("boom")):
        enabled = _resolve_child_toolsets(parent, None, "leaf")[0]
    assert "terminal" in enabled
    assert not any(str(t).startswith("mcp-") for t in enabled)
