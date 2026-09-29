"""MoonPie device-token provider for the dashboard token-auth seam.

Validates bearer tokens issued by the MoonPie device-registration flow
(``/api/moonpie/devices/verify``) so that MoonPie REST and WebSocket routes
can use the same token-auth middleware as other service callers.
"""
from __future__ import annotations

from typing import Callable, Optional

from hermes_cli.dashboard_auth.base import (
    DashboardAuthProvider,
    LoginStart,
    ProviderError,
    Session,
    TokenPrincipal,
)


class MoonPieDeviceProvider(DashboardAuthProvider):
    """Non-interactive token provider for MoonPie native-client device tokens.

    Tokens are opaque UUIDs minted during device pairing verification and
    stored in the shared ``_device_tokens`` map inside
    ``hermes_cli.web_routers.moonpie``.  Because that map lives in a web-router
    module, this provider does not import it directly; instead the router
    injects a verify callback at import time (see ``moonpie.py``).
    """

    name = "moonpie_device"
    display_name = "MoonPie Device Token"
    supports_token = True
    supports_session = False
    supports_password = False

    _verify_callback: Optional[Callable[[str], Optional[str]]] = None

    @classmethod
    def set_verify_callback(cls, callback: Callable[[str], Optional[str]]) -> None:
        """Inject the callable that maps a token string to a device_id."""
        cls._verify_callback = callback

    # ------------------------------------------------------------------
    # Token-auth seam (the only path we actually support)
    # ------------------------------------------------------------------

    def verify_token(self, *, token: str) -> Optional[TokenPrincipal]:
        """Return a :class:`TokenPrincipal` when the token is known."""
        # Access through the class to avoid descriptor binding
        cb = MoonPieDeviceProvider._verify_callback
        if cb is None:
            return None
        device_id = cb(token)
        if device_id:
            return TokenPrincipal(principal=device_id, provider=self.name)
        return None

    # ------------------------------------------------------------------
    # Session stubs — unsupported but required by the protocol
    # ------------------------------------------------------------------

    def start_login(self, *, redirect_uri: str) -> LoginStart:
        raise NotImplementedError(
            "MoonPie device provider does not support interactive login"
        )

    def complete_login(
        self, *, code: str, state: str, code_verifier: str, redirect_uri: str
    ) -> Session:
        raise NotImplementedError(
            "MoonPie device provider does not support interactive login"
        )

    def verify_session(self, *, access_token: str) -> Optional[Session]:
        return None

    def refresh_session(self, *, refresh_token: str) -> Session:
        raise NotImplementedError(
            "MoonPie device provider does not support session refresh"
        )

    def revoke_session(self, *, refresh_token: str) -> None:
        pass
