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
from typing import Any, Callable, Optional

_log = logging.getLogger("hermes_cli.moonpie_adapter")


class MoonPieHermesAdapter:
    """Minimal adapter isolating Hermes agent interaction from MoonPie router.

    Current responsibilities (Gate 2, B3 only):
    - Lazy initialisation of a shared ``AIAgent`` for MoonPie clients.
    - ``chat()`` — send a message, stream text deltas, return final response.
    - ``synthesize_speech()`` — synthesise speech audio, return raw bytes.

    Non-goals for this gate:
    - Approval bridging, kanban wiring, session management, persistence.
    - These belong to later gates (B4, B5+) and the Application Services layer.
    """

    def __init__(self) -> None:
        self._agent: Optional[Any] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public interface (Hermes-agnostic upward-facing)
    # ------------------------------------------------------------------

    async def chat(
        self,
        content: str,
        stream_callback: Callable[[str], None],
    ) -> str:
        """Send *content* to the agent and stream deltas via *stream_callback*.

        Returns the final response string.  Raises ``RuntimeError`` if the
        agent is not available.
        """
        agent = await self._get_agent()
        if agent is None:
            raise RuntimeError("Agent not available")

        def _run() -> str:
            # _config_profile_scope is a Hermes internal — it stays inside the
            # adapter boundary.
            from hermes_cli.web_routers._common import _config_profile_scope

            with _config_profile_scope(None):
                return agent.chat(content, stream_callback=stream_callback)

        return await asyncio.to_thread(_run)

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
    # Private — Hermes-specific internals
    # ------------------------------------------------------------------

    async def _get_agent(self) -> Optional[Any]:
        """Lazily initialise a shared ``AIAgent`` for MoonPie clients."""
        if self._agent is not None:
            return self._agent

        async with self._lock:
            if self._agent is not None:
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
                _log.info(
                    "MoonPie agent initialized: provider=%s model=%s",
                    getattr(self._agent, "provider", "?"),
                    getattr(self._agent, "model", "?"),
                )
            except Exception as exc:
                _log.warning("MoonPie agent init failed: %s", exc, exc_info=True)
                self._agent = None

        return self._agent
