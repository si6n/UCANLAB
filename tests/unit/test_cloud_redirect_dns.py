"""I-07 follow-up — DNS-rebinding cover for cloud redirect targets.

The redirect policy already refused cross-origin/scheme-downgrade/plain-HTTP
hops and literal private IPs, but a HOSTNAME target was only checked against
the allowlist: an attacker controlling an allowlisted name could answer the
redirect lookup with a private address (or flip the A record between checks).

`_SafeRedirectHandler.redirect_request` now resolves every hostname target
and refuses the hop when ANY resolved address is private / loopback /
link-local / reserved / multicast / unspecified — or when the name does not
resolve at all (fail-closed).

These tests drive the handler with a stubbed resolver so no real DNS traffic
leaves the machine.
"""

from __future__ import annotations

import urllib.request

import pytest

from src.core.errors import SecurityError
from src.security.cloud.client import _SafeRedirectHandler


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={"Authorization": "Bearer test"})


def _handler(**overrides: object) -> _SafeRedirectHandler:
    kwargs: dict[str, object] = {
        "allowed_hosts": ("cloud.example",),
        "enforce_allowlist": True,
        "require_https": True,
    }
    kwargs.update(overrides)
    return _SafeRedirectHandler(**kwargs)  # type: ignore[arg-type]


class TestHostnameResolutionGate:
    """A redirect to a hostname whose DNS answer is not public must fail."""

    def test_private_resolution_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _handler()
        monkeypatch.setattr(h, "_resolve_ips", lambda host: ["10.0.0.7"])
        with pytest.raises(SecurityError) as exc:
            h.redirect_request(
                _request("https://cloud.example/a"),
                None,
                302,
                "Found",
                {},
                "https://cloud.example/b",
            )
        assert exc.value.code == "CLOUD_REDIRECT_REFUSED"
        assert "10.0.0.7" in str(exc.value)

    def test_loopback_resolution_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _handler()
        monkeypatch.setattr(h, "_resolve_ips", lambda host: ["127.0.0.1"])
        with pytest.raises(SecurityError) as exc:
            h.redirect_request(
                _request("https://cloud.example/a"), None, 302, "Found", {},
                "https://cloud.example/b",
            )
        assert exc.value.code == "CLOUD_REDIRECT_REFUSED"

    def test_link_local_resolution_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _handler()
        monkeypatch.setattr(h, "_resolve_ips", lambda host: ["169.254.169.254"])
        with pytest.raises(SecurityError):
            h.redirect_request(
                _request("https://cloud.example/a"), None, 302, "Found", {},
                "https://cloud.example/b",
            )

    def test_mixed_answer_refused_when_any_address_is_private(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A public answer that also carries a private one must fail closed."""
        h = _handler()
        monkeypatch.setattr(h, "_resolve_ips", lambda host: ["93.184.216.34", "192.168.1.10"])
        with pytest.raises(SecurityError):
            h.redirect_request(
                _request("https://cloud.example/a"), None, 302, "Found", {},
                "https://cloud.example/b",
            )

    def test_unresolvable_host_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _handler()
        monkeypatch.setattr(h, "_resolve_ips", lambda host: [])
        with pytest.raises(SecurityError) as exc:
            h.redirect_request(
                _request("https://cloud.example/a"), None, 302, "Found", {},
                "https://cloud.example/b",
            )
        assert "unresolvable" in str(exc.value).lower()

    def test_public_resolution_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _handler()
        monkeypatch.setattr(h, "_resolve_ips", lambda host: ["93.184.216.34"])
        # Same-origin, allowlisted, public answer → the hop is permitted and
        # urllib's base implementation builds the follow-up request.
        follow = h.redirect_request(
            _request("https://cloud.example/a"), None, 302, "Found", {},
            "https://cloud.example/b",
        )
        assert follow is not None
        assert follow.full_url == "https://cloud.example/b"

    def test_dev_hosts_keep_their_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit loopback dev hosts stay reachable (dev-only exception)."""
        h = _handler(allowed_hosts=("localhost",), enforce_allowlist=True)
        monkeypatch.setattr(h, "_resolve_ips", lambda host: ["127.0.0.1"])
        # No SecurityError from the private-resolution gate for dev hosts.
        follow = h.redirect_request(
            _request("https://localhost:8443/a"), None, 302, "Found", {},
            "https://localhost:8443/b",
        )
        assert follow is not None


class TestLiteralIpGateUnchanged:
    """Literal-IP targets are still rejected before any DNS work."""

    def test_literal_private_ip_refused_without_dns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        h = _handler(allowed_hosts=("10.0.0.7",))
        called: list[str] = []

        def _boom(host: str) -> list[str]:
            called.append(host)
            return []

        monkeypatch.setattr(h, "_resolve_ips", _boom)
        with pytest.raises(SecurityError):
            h.redirect_request(
                _request("https://10.0.0.7/a"), None, 302, "Found", {},
                "https://10.0.0.7/b",
            )
        assert called == [], "literal-IP destinations must not trigger a DNS lookup"


class TestCrossOriginStillRefused:
    """Regression guard: the DNS gate must not weaken the origin check."""

    def test_cross_origin_redirect_refused_before_resolution(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        h = _handler(allowed_hosts=("cloud.example", "evil.example"))
        called: list[str] = []
        monkeypatch.setattr(h, "_resolve_ips", lambda host: called.append(host) or [])
        with pytest.raises(SecurityError) as exc:
            h.redirect_request(
                _request("https://cloud.example/a"), None, 302, "Found", {},
                "https://evil.example/b",
            )
        assert exc.value.code == "CLOUD_REDIRECT_REFUSED"
        assert called == [], "origin refusal must short-circuit before DNS"

    def test_scheme_downgrade_refused(self) -> None:
        h = _handler(allowed_hosts=("cloud.example",))
        with pytest.raises(SecurityError):
            h.redirect_request(
                _request("https://cloud.example/a"), None, 302, "Found", {},
                "http://cloud.example/b",
            )
