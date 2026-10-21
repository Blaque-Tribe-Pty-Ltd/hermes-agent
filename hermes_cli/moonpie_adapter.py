"""Hermes adapter for the MoonPie native client.

This module isolates all Hermes-specific agent interaction from the MoonPie
router (``web_routers/moonpie.py``).  It is the **only** module in the
MoonPie gateway path permitted to import ``AIAgent``, ``_config_profile_scope``,
Hermes config, or TTS tools.

IMPORTANT: This is a **temporary isolation seam** for Gate 2 (B3), not the
future Moonbeam Client API boundary.  It is a direct MoonPie→Hermes bridge
that removes router-level coupling.  A future Client API gate will introduce
a Moonbeam service abstraction between the client and this adapter.

Design principles:
- The adapter owns the Hermes interaction surface.
- The router depends on the adapter, never on Hermes internals.
- All ``_config_profile_scope`` usage lives here.
- Return values are plain Python types (str, bytes, None) — no Hermes objects
  escape upward.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from typing import Any, Callable, Optional

_log = logging.getLogger("hermes_cli.moonpie_adapter")


class _TurnState:
    """Per-device turn state for cancellation tracking (Gate 6, B5)."""

    __slots__ = ("device_id", "conversation_id", "session_key",
                 "cancelled_event", "pending_approvals", "completed")

    def __init__(self, device_id: str, conversation_id: str, session_key: str) -> None:
        self.device_id = device_id
        self.conversation_id = conversation_id
        self.session_key = session_key
        self.cancelled_event = threading.Event()
        self.pending_approvals: set[str] = set()
        self.completed = False


class MoonPieHermesAdapter:
    """Minimal adapter isolating Hermes agent interaction from MoonPie router.

    Current responsibilities:
    - Lazy initialisation of a shared ``AIAgent`` for MoonPie clients.
    - ``chat()`` — send a message, stream text deltas, return final response.
    - ``synthesize_speech()`` — synthesise speech audio, return raw bytes.
    - ``cancel_turn()`` — signal cancellation, withdraw approvals, stop streaming (Gate 6, B5).
    """

    def __init__(self) -> None:
        self._agent: Optional[Any] = None
        self._lock = asyncio.Lock()
        self._turn_lock = threading.Lock()
        self._active_turns: dict[str, _TurnState] = {}

    # ------------------------------------------------------------------
    # Public interface (Hermes-agnostic upward-facing)
    # ------------------------------------------------------------------

    async def chat(
        self,
        content: str,
        stream_callback: Callable[[str], None],
        tool_event_callback: Callable[[str, dict], None] | None = None,
        *,
        device_id: str = "",
        conversation_id: str = "",
        session_key: str = "",
        reasoning_level: str | None = None,
        permission_mode: str | None = None,
    ) -> str:
        """Send *content* to the agent and stream deltas via *stream_callback*.

        Optional *reasoning_level* (low/medium/high) and *permission_mode*
        (ask/auto) apply to this turn only and are restored afterwards.

        Optional *tool_event_callback* receives normalized MoonPie tool lifecycle
        events: ``tool.started``, ``tool.completed``, ``tool.failed``.

        Returns the final response string.  Raises ``RuntimeError`` if the
        agent is not available.

        Gate 6: Cancellation is tracked per device.  If ``cancel_turn`` is called
        while this chat is in flight, subsequent deltas and tool events are dropped
        and the turn returns early.
        """
        agent = await self._get_agent(tool_event_callback=tool_event_callback)
        if agent is None:
            raise RuntimeError("Agent not available")

        # Register turn for cancellation tracking
        turn = _TurnState(device_id, conversation_id, session_key)
        with self._turn_lock:
            self._active_turns[device_id] = turn

        # Wrap stream_callback so cancellation drops deltas atomically
        def _wrapped_stream(part: str) -> None:
            if turn.cancelled_event.is_set():
                return
            stream_callback(part)

        def _run() -> str:
            # _config_profile_scope is a Hermes internal — it stays inside the
            # adapter boundary.
            from hermes_cli.web_routers._common import _config_profile_scope

            restore = self._apply_turn_options(agent, session_key, reasoning_level, permission_mode)
            try:
                with _config_profile_scope(None):
                    return agent.chat(content, stream_callback=_wrapped_stream)
            finally:
                restore()

        try:
            result = await asyncio.to_thread(_run)
            return result
        finally:
            with self._turn_lock:
                turn.completed = True
                # Only remove if this is still the current turn for the device
                if self._active_turns.get(device_id) is turn:
                    self._active_turns.pop(device_id, None)

    # ------------------------------------------------------------------
    # Composer capabilities, per-turn options and usage
    # ------------------------------------------------------------------

    REASONING_LEVELS = ("low", "medium", "high")
    PERMISSION_MODES = ("ask", "auto")

    def capabilities(self) -> dict:
        """What MoonPie composers may offer. Keys are optional for clients; old clients ignore them."""
        model = getattr(self._agent, "model", None) if self._agent is not None else None
        if not model:
            try:
                from hermes_cli.config import load_config_readonly

                cfg = load_config_readonly()
                model_cfg = cfg.get("model", {})
                model = model_cfg.get("default") if isinstance(model_cfg, dict) else None
            except Exception:
                model = None
        models = [{"id": str(model), "label": str(model), "active": True}] if model else []
        return {
            "models": models,
            "reasoning_levels": list(self.REASONING_LEVELS),
            "default_reasoning_level": "medium",
            "permission_modes": list(self.PERMISSION_MODES),
            "default_permission_mode": "ask",
            "usage": True,
        }

    def _apply_turn_options(self, agent: Any, session_key: str, reasoning_level, permission_mode):
        """Apply per-turn options; return a callable that restores the previous state. Invalid values are ignored."""
        restorers: list[Callable[[], None]] = []

        if reasoning_level is not None:
            if reasoning_level in self.REASONING_LEVELS:
                from hermes_constants import parse_reasoning_effort

                previous = getattr(agent, "reasoning_config", None)
                agent.reasoning_config = parse_reasoning_effort(reasoning_level)
                restorers.append(lambda: setattr(agent, "reasoning_config", previous))
            else:
                _log.warning("MoonPie: ignoring unknown reasoning_level %r", reasoning_level)

        if permission_mode is not None:
            if permission_mode in self.PERMISSION_MODES and session_key:
                from tools.approval import (
                    disable_session_yolo,
                    enable_session_yolo,
                    is_session_yolo_enabled,
                )

                was_auto = is_session_yolo_enabled(session_key)
                if permission_mode == "auto":
                    enable_session_yolo(session_key)
                else:
                    disable_session_yolo(session_key)

                def _restore_permission() -> None:
                    if was_auto:
                        enable_session_yolo(session_key)
                    else:
                        disable_session_yolo(session_key)

                restorers.append(_restore_permission)
            else:
                _log.warning("MoonPie: ignoring unknown permission_mode %r", permission_mode)

        def restore() -> None:
            for fn in reversed(restorers):
                try:
                    fn()
                except Exception:  # restoring must never mask the turn's own result
                    _log.warning("MoonPie: failed to restore a per-turn option", exc_info=True)

        return restore

    def usage_snapshot(self) -> dict | None:
        """Token counters after a turn, or None when the agent exposes nothing usable."""
        agent = self._agent
        if agent is None:
            return None
        snapshot: dict[str, int] = {}

        def _int(value: Any) -> int | None:
            return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

        for key, attrs in (
            ("input_tokens", ("session_input_tokens", "session_prompt_tokens")),
            ("output_tokens", ("session_output_tokens", "session_completion_tokens")),
        ):
            for attr in attrs:
                value = _int(getattr(agent, attr, None))
                if value:
                    snapshot[key] = value
                    break
        engine = getattr(agent, "context_compressor", None)
        window = _int(getattr(engine, "context_length", None)) if engine is not None else None
        used = _int(getattr(engine, "last_prompt_tokens", None)) if engine is not None else None
        if window:
            snapshot["context_window"] = window
        if used:
            snapshot["context_used"] = used
        return snapshot or None

    async def synthesize_speech(self, text: str) -> Optional[bytes]:
        """Synthesise speech for *text* and return the audio bytes.

        Returns ``None`` on any failure (missing tool, file not found, etc.).
        """
        if not text:
            return None

        def _synthesize():
            from hermes_cli.web_routers._common import _config_profile_scope

            with _config_profile_scope(None):
                from tools.tts_tool import text_to_speech_tool

                return text_to_speech_tool(text)

        try:
            result_json = await asyncio.to_thread(_synthesize)
            result = json.loads(result_json) if isinstance(result_json, str) else result_json
            if not result.get("success"):
                return None

            file_path = result.get("file_path")
            if not file_path or not os.path.isfile(file_path):
                return None

            def _read_and_unlink() -> bytes:
                try:
                    with open(file_path, "rb") as fh:
                        return fh.read()
                finally:
                    try:
                        os.unlink(file_path)
                    except OSError:
                        pass

            return await asyncio.to_thread(_read_and_unlink)
        except Exception:
            _log.warning("TTS synthesis failed", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Cancellation (Gate 6, B5)
    # ------------------------------------------------------------------

    def cancel_turn(self, device_id: str, conversation_id: str) -> dict:
        """Cancel the active turn for *device_id* if it matches *conversation_id*.

        Returns a dict with:
        - ``cancelled`` (bool): whether a turn was found and signalled
        - ``already_complete`` (bool): the turn had already finished
        - ``not_found`` (bool): no active turn for this device
        - ``withdrawn_approvals`` (list[str]): request_ids of approvals withdrawn
        """
        with self._turn_lock:
            turn = self._active_turns.get(device_id)

        if turn is None:
            return {"cancelled": False, "not_found": True,
                    "already_complete": False, "withdrawn_approvals": []}

        if turn.conversation_id != conversation_id:
            return {"cancelled": False, "not_found": True,
                    "already_complete": False, "withdrawn_approvals": []}

        if turn.completed:
            return {"cancelled": False, "not_found": False,
                    "already_complete": True, "withdrawn_approvals": []}

        # Signal cancellation
        turn.cancelled_event.set()

        # Withdraw all pending approvals for this session
        withdrawn: list[str] = []
        if turn.session_key and turn.pending_approvals:
            try:
                from tools.approval import withdraw_gateway_approval
                for req_id in list(turn.pending_approvals):
                    if withdraw_gateway_approval(turn.session_key, req_id,
                                                 cause="conversation.cancel"):
                        withdrawn.append(req_id)
            except Exception:
                _log.warning("Failed to withdraw approvals", exc_info=True)

        with self._turn_lock:
            # Remove from active turns so a new message can start immediately
            if self._active_turns.get(device_id) is turn:
                self._active_turns.pop(device_id, None)

        return {"cancelled": True, "not_found": False,
                "already_complete": False, "withdrawn_approvals": withdrawn}

    def _current_turn(self, device_id: str) -> _TurnState | None:
        """Return the active turn for *device_id* (None if none)."""
        with self._turn_lock:
            return self._active_turns.get(device_id)

    # ------------------------------------------------------------------
    # Private — Hermes-specific internals
    # ------------------------------------------------------------------

    async def _get_agent(
        self, tool_event_callback: Callable[[str, dict], None] | None = None,
        *, device_id: str = "",
    ) -> Optional[Any]:
        """Lazily initialise a shared ``AIAgent`` for MoonPie clients."""
        if self._agent is not None:
            # If a tool_event_callback was provided but the agent already exists,
            # wire it dynamically for this turn only.
            if tool_event_callback is not None:
                self._wire_tool_callbacks(self._agent, tool_event_callback,
                                          device_id=device_id)
            return self._agent

        async with self._lock:
            if self._agent is not None:
                if tool_event_callback is not None:
                    self._wire_tool_callbacks(self._agent, tool_event_callback,
                                              device_id=device_id)
                return self._agent

            try:
                from run_agent import AIAgent
                from hermes_cli.config import load_config_readonly
                from hermes_cli.web_routers._common import _config_profile_scope

                def _init():
                    with _config_profile_scope(None):
                        cfg = load_config_readonly()
                        model_cfg = cfg.get("model", {})
                        provider = (
                            model_cfg.get("provider", "kimi")
                            if isinstance(model_cfg, dict)
                            else "kimi"
                        )
                        model = (
                            model_cfg.get("default", "kimi-k2.6")
                            if isinstance(model_cfg, dict)
                            else "kimi-k2.6"
                        )
                        fb = cfg.get("fallback_providers", {})
                        fallback_model = None
                        if isinstance(fb, dict) and fb:
                            first = next(iter(fb.values()))
                            if (
                                isinstance(first, dict)
                                and first.get("provider")
                                and first.get("model")
                            ):
                                fallback_model = dict(first)
                        _log.info(
                            "MoonPie agent creating with provider=%s model=%s fallback=%s",
                            provider,
                            model,
                            fallback_model,
                        )
                        return AIAgent(
                            platform="moonpie",
                            quiet_mode=True,
                            provider=provider,
                            model=model,
                            fallback_model=fallback_model,
                        )

                self._agent = await asyncio.to_thread(_init)
                if tool_event_callback is not None:
                    self._wire_tool_callbacks(self._agent, tool_event_callback,
                                              device_id=device_id)
                _log.info(
                    "MoonPie agent initialized: provider=%s model=%s",
                    getattr(self._agent, "provider", "?"),
                    getattr(self._agent, "model", "?"),
                )
            except Exception as exc:
                _log.warning("MoonPie agent init failed: %s", exc, exc_info=True)
                self._agent = None

        return self._agent

    def _wire_tool_callbacks(
        self,
        agent: Any,
        tool_event_callback: Callable[[str, dict], None],
        *, device_id: str = "",
    ) -> None:
        """Wire Hermes tool callbacks into normalized MoonPie tool events.

        The adapter normalizes at the boundary so the router never sees
        Hermes-specific shapes.

        Gate 6: Tool events are dropped if the turn has been cancelled.
        Pending approvals are tracked so they can be withdrawn on cancel.
        """
        import time

        _start_times: dict[str, float] = {}
        _adapter = self

        def _is_cancelled() -> bool:
            turn = _adapter._current_turn(device_id)
            return turn is not None and turn.cancelled_event.is_set()

        def _on_tool_start(call_id: str, tool_name: str, args: dict) -> None:
            if _is_cancelled():
                return
            _start_times[call_id] = time.time()
            # Track approval request_ids if present in args
            turn = _adapter._current_turn(device_id)
            if turn is not None and args:
                req_id = args.get("request_id") or args.get("approval_request_id")
                if req_id:
                    turn.pending_approvals.add(str(req_id))
            # Sanitize: drop any args that might contain secrets
            preview = tool_name
            tool_event_callback(
                "tool.started",
                {
                    "tool_call_id": call_id,
                    "tool_name": tool_name,
                    "preview": preview,
                },
            )

        def _on_tool_complete(
            call_id: str, tool_name: str, args: dict, result: Any
        ) -> None:
            if _is_cancelled():
                return
            started_at = _start_times.pop(call_id, None)
            duration_ms = (
                int((time.time() - started_at) * 1000)
                if started_at is not None
                else None
            )
            # Detect failure from result shape
            is_error = False
            error_message = None
            if isinstance(result, dict):
                is_error = bool(result.get("error")) or result.get("status") == "error"
                error_message = result.get("error", result.get("message"))
            elif isinstance(result, str) and result.startswith("Error:"):
                is_error = True
                error_message = result

            if is_error:
                tool_event_callback(
                    "tool.failed",
                    {
                        "tool_call_id": call_id,
                        "tool_name": tool_name,
                        "duration_ms": duration_ms,
                        "error_message": error_message or "Tool execution failed",
                    },
                )
            else:
                tool_event_callback(
                    "tool.completed",
                    {
                        "tool_call_id": call_id,
                        "tool_name": tool_name,
                        "duration_ms": duration_ms,
                    },
                )

        agent.tool_start_callback = _on_tool_start
        agent.tool_complete_callback = _on_tool_complete
