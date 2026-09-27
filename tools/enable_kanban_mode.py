"""Enable Kanban mode dynamically for orchestration workflows.

When MoonPie needs to orchestrate a multi-specialist pipeline with Kanban task
tracking, calling this tool loads the Kanban toolset and updates the agent's
system prompt for the next turn.
"""
from __future__ import annotations

import json
from typing import Any, Dict

from tools.registry import registry


def enable_kanban_mode() -> str:
    """Load Kanban tools into the current agent session.

    Use this before orchestrating a multi-specialist workflow that requires
    Kanban task creation, linking, and lifecycle management. After calling,
    Kanban tools become available on the next turn.
    """
    import model_tools
    from agent.prompt_builder import KANBAN_GUIDANCE

    # Try to reach the caller's agent via the registry dispatch context.
    # The dispatcher injects 'agent' into tool kwargs when available.
    agent = None
    try:
        # Access the current thread's dispatch context if available
        from agent.conversation_loop import _current_agent
        agent = _current_agent.get()
    except Exception:
        pass

    if agent is None:
        return json.dumps({
            "enabled": False,
            "error": "Agent context not available. Kanban mode cannot be enabled outside an active session."
        })

    # Add kanban to enabled toolsets if not already present
    current = list(getattr(agent, "enabled_toolsets", None) or [])
    if "kanban" not in current:
        current.append("kanban")
        agent.enabled_toolsets = current

    # Reload tools with kanban included
    try:
        agent.tools = model_tools.get_tool_definitions(
            enabled_toolsets=agent.enabled_toolsets,
            disabled_toolsets=getattr(agent, "disabled_toolsets", None),
            quiet_mode=True,
        )
        agent.valid_tool_names = {t["function"]["name"] for t in agent.tools} if agent.tools else set()

        # Inject Kanban worker guidance if the agent owns a Kanban task
        from agent.delegation_context import owned_kanban_task
        if owned_kanban_task() and "kanban_show" in agent.valid_tool_names:
            agent._kanban_worker_guidance = KANBAN_GUIDANCE
        else:
            agent._kanban_worker_guidance = ""

        return json.dumps({
            "enabled": True,
            "kanban_tools_loaded": [t for t in agent.valid_tool_names if t.startswith("kanban_")],
            "note": "Kanban tools are now available. Use them on this turn or the next."
        })
    except Exception as e:
        return json.dumps({
            "enabled": False,
            "error": f"Failed to reload tools with Kanban: {e}"
        })


# Register as a core tool in the 'orchestration' toolset
registry.register(
    name="enable_kanban_mode",
    toolset="clarify",
    schema={
        "name": "enable_kanban_mode",
        "description": (
            "Enable Kanban task-tracking tools for the current session. "
            "Call this before orchestrating a multi-specialist workflow that requires "
            "Kanban task creation, linking, and lifecycle management. "
            "After calling, Kanban tools (kanban_create, kanban_link, kanban_show, etc.) "
            "become available for use on the next turn."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    handler=lambda args, **kw: enable_kanban_mode(),
)
