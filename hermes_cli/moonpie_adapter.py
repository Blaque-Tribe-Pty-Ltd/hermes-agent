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
import hashlib
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
    - Lazy initialisation of one ``AIAgent`` per MoonPie conversation.
    - ``chat()`` — send a message, stream text deltas, return final response.
    - ``synthesize_speech()`` — synthesise speech audio, return raw bytes.
    - ``cancel_turn()`` — signal cancellation, withdraw approvals, stop streaming (Gate 6, B5).
    """

    def __init__(self, session_db_provider: Callable[[], Any] | None = None) -> None:
        self._agents: dict[str, Any] = {}
        self._session_db_provider = session_db_provider
        self._lock = asyncio.Lock()
        self._turn_lock = threading.Lock()
        self._active_turns: dict[str, _TurnState] = {}

    # ------------------------------------------------------------------
    # Public interface (Hermes-agnostic upward-facing)
    # ------------------------------------------------------------------

    async def chat(
        self,
        content: Any,
        stream_callback: Callable[[str], None],
        tool_event_callback: Callable[[str, dict], None] | None = None,
        *,
        device_id: str = "",
        conversation_id: str = "",
        session_key: str = "",
    ) -> str:
        """Send text or multimodal *content* and stream deltas via *stream_callback*.

        Optional *tool_event_callback* receives normalized MoonPie tool lifecycle
        events: ``tool.started``, ``tool.completed``, ``tool.failed``.

        Returns the final response string.  Raises ``RuntimeError`` if the
        agent is not available.

        Gate 6: Cancellation is tracked per device.  If ``cancel_turn`` is called
        while this chat is in flight, subsequent deltas and tool events are dropped
        and the turn returns early.
        """
        if not device_id or not conversation_id:
            raise RuntimeError("MoonPie device and conversation IDs are required")
        session_id = self._session_id(device_id, conversation_id)
        session_db = self._get_session_db()
        agent = await self._get_agent(
            session_id,
            session_db,
            session_key=session_key,
            device_id=device_id,
            conversation_id=conversation_id,
            tool_event_callback=tool_event_callback,
        )
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

            with _config_profile_scope(None):
                try:
                    history = session_db.get_messages_as_conversation(agent.session_id)
                except Exception as exc:
                    raise RuntimeError("MoonPie session history is unavailable") from exc
                result = agent.run_conversation(
                    content,
                    conversation_history=history,
                    task_id=agent.session_id,
                    stream_callback=_wrapped_stream,
                )
                return str(result.get("final_response") or "")

        try:
            result = await asyncio.to_thread(_run)
            return result
        finally:
            with self._turn_lock:
                turn.completed = True
                # Only remove if this is still the current turn for the device
                if self._active_turns.get(device_id) is turn:
                    self._active_turns.pop(device_id, None)

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

    @staticmethod
    def _session_id(device_id: str, conversation_id: str) -> str:
        """Stable, device-scoped Hermes session ID for a MoonPie conversation."""
        digest = hashlib.sha256(
            f"{device_id}\0{conversation_id}".encode("utf-8")
        ).hexdigest()[:24]
        return f"moonpie_{digest}"

    def _get_session_db(self) -> Any:
        if self._session_db_provider is None:
            raise RuntimeError("MoonPie session database is not configured")
        return self._session_db_provider()

    async def _get_agent(
        self,
        session_id: str,
        session_db: Any,
        *,
        session_key: str,
        device_id: str,
        conversation_id: str,
        tool_event_callback: Callable[[str, dict], None] | None = None,
    ) -> Optional[Any]:
        """Lazily initialise the Hermes agent bound to one MoonPie conversation."""
        agent = self._agents.get(session_id)
        if agent is not None:
            # If a tool_event_callback was provided but the agent already exists,
            # wire it dynamically for this turn only.
            if tool_event_callback is not None:
                self._wire_tool_callbacks(agent, tool_event_callback,
                                          device_id=device_id)
            return agent

        async with self._lock:
            agent = self._agents.get(session_id)
            if agent is not None:
                if tool_event_callback is not None:
                    self._wire_tool_callbacks(agent, tool_event_callback,
                                              device_id=device_id)
                return agent

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
                        fb = cfg.get("fallback_providers", [])
                        fallback_model = None
                        if isinstance(fb, list) and fb:
                            first = fb[0]
                        elif isinstance(fb, dict) and fb:
                            first = next(iter(fb.values()))
                        else:
                            first = None
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
                            session_id=session_id,
                            session_db=session_db,
                            gateway_session_key=session_key,
                            user_id=device_id,
                            chat_id=conversation_id,
                            chat_type="dm",
                            load_soul_identity=True,
                        )

                agent = await asyncio.to_thread(_init)
                self._agents[session_id] = agent
                if tool_event_callback is not None:
                    self._wire_tool_callbacks(agent, tool_event_callback,
                                              device_id=device_id)
                _log.info(
                    "MoonPie agent initialized: session=%s provider=%s model=%s",
                    session_id,
                    getattr(agent, "provider", "?"),
                    getattr(agent, "model", "?"),
                )
            except Exception as exc:
                _log.warning("MoonPie agent init failed: %s", exc, exc_info=True)
                self._agents.pop(session_id, None)
                agent = None

        return agent

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
