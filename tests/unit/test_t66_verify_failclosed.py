# -*- coding: utf-8 -*-
"""T66 / I-19 regression lock — the verifier must never exit 0 without proof.

THE DEFECT (review finding I-19)
--------------------------------
`t66_verify.py` printed "no records" and returned 0 on an empty input list,
although its own docstring said "no valid sample" must be a failure. A CI gate
wired to this script went green on a scan that proved nothing. The source
fetching also needed an explicit network policy: host allowlist, private-IP /
loopback rejection, redirect limit, and a bounded streaming read.

LOCKED CONTRACT
---------------
  * exit 0 ONLY on a PASS verdict (ratio >= 0.5 over a non-empty valid sample);
  * exit 2 when the input file is missing/unreadable/unparseable, or the
    record list is empty;
  * exit 1 when no record yields a valid evaluation (`no-valid-sample`), or
    every fetch failed (`all-fetch-failed`), or the ratio is below 0.5;
  * the network policy refuses non-http(s) schemes, unknown hosts, and
    loopback / private / link-local / reserved IP literals (IPv4 and IPv6),
    and re-applies the same policy to every redirect target.

All tests use tmp_path fixtures and never touch the network (fetching is
monkeypatched or refused by policy before any socket opens).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
VERIFY_PATH = REPO_ROOT / "scripts" / "t66_verify.py"

PROBE = (
    "Catalyst efficiency below threshold bank one measured at warm idle with "
    "steady throttle and no misfire counters present on the scan tool"
)


def _load_verify_module():
    spec = importlib.util.spec_from_file_location("t66_verify_i19", VERIFY_PATH)
    assert spec and spec.loader, "verify module must be loadable"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def verify_mod():
    return _load_verify_module()


def _write(tmp_path: Path, name: str, payload) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def _record(url: str = "https://obdfyi.com/codes/p0420") -> dict:
    return {"url": url, "steps": [PROBE]}


# ---------------------------------------------------------------------------
# 1. Unusable input exits nonzero
# ---------------------------------------------------------------------------

class TestUnusableInput:
    def test_missing_file_exits_nonzero(self, verify_mod, tmp_path, capsys) -> None:
        rc = verify_mod.main(["--input", str(tmp_path / "absent.json")])
        assert rc != 0, "a missing input file must never exit green"
        assert "ERROR" in capsys.readouterr().out

    def test_unparseable_file_exits_nonzero(self, verify_mod, tmp_path, capsys) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        rc = verify_mod.main(["--input", str(bad)])
        assert rc != 0
        assert "ERROR" in capsys.readouterr().out

    def test_empty_record_list_exits_nonzero(self, verify_mod, tmp_path, capsys) -> None:
        empty = _write(tmp_path, "empty.json", {"records": []})
        rc = verify_mod.main(["--input", str(empty), "--out", str(tmp_path / "o.json")])
        assert rc != 0, "an empty input proves nothing and must not exit 0"
        assert "no records" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 2. Unprovable samples exit nonzero with a specific reason
# ---------------------------------------------------------------------------

class TestUnprovableSample:
    def test_no_valid_sample_exits_nonzero(self, verify_mod, tmp_path) -> None:
        # A record with no probe-eligible text yields only NO_PROBE rows.
        inp = _write(tmp_path, "noprobe.json",
                     {"records": [{"url": "https://obdfyi.com/codes/p0420",
                                   "steps": ["short"]}]})
        out = tmp_path / "out.json"
        rc = verify_mod.main(["--input", str(inp), "--out", str(out)])
        assert rc != 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["verdict"] == "FAIL"
        assert report["fail_reason"] == "no-valid-sample"

    def test_all_fetch_failed_exits_nonzero(self, verify_mod, tmp_path) -> None:
        # The host is not allowlisted, so every fetch is refused by policy.
        inp = _write(tmp_path, "refused.json",
                     {"records": [_record("https://evil.example/p0420")]})
        out = tmp_path / "out.json"
        rc = verify_mod.main(["--input", str(inp), "--out", str(out)])
        assert rc != 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["verdict"] == "FAIL"
        assert report["fail_reason"] == "all-fetch-failed"
        assert report["fetch_fail"] >= 1

    def test_below_ratio_exits_nonzero(self, verify_mod, tmp_path, monkeypatch) -> None:
        inp = _write(tmp_path, "mismatch.json", {"records": [_record()]})
        out = tmp_path / "out.json"
        monkeypatch.setattr(verify_mod, "fetch", lambda url, timeout=25: (200, "<html>unrelated page</html>"))
        rc = verify_mod.main(["--input", str(inp), "--out", str(out)])
        assert rc != 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["verdict"] == "FAIL" and report["fail_reason"] is None

    def test_pass_exits_zero(self, verify_mod, tmp_path, monkeypatch) -> None:
        inp = _write(tmp_path, "match.json", {"records": [_record()]})
        out = tmp_path / "out.json"
        page = f"<html><body><p>{PROBE}</p></body></html>"
        monkeypatch.setattr(verify_mod, "fetch", lambda url, timeout=25: (200, page))
        rc = verify_mod.main(["--input", str(inp), "--out", str(out)])
        assert rc == 0
        report = json.loads(out.read_text(encoding="utf-8"))
        assert report["verdict"] == "PASS"


# ---------------------------------------------------------------------------
# 3. Network policy: allowlist, IP rejection, scheme, redirects
# ---------------------------------------------------------------------------

class TestNetworkPolicy:
    @pytest.mark.parametrize(
        "url, allowed",
        [
            ("https://obdfyi.com/codes/p0420", True),
            ("https://www.obd2.com/dtc/p0420", True),
            ("https://troubleshootmyvehicle.com/honda/x", True),
            ("http://obdfyi.com/codes/p0420", True),
            ("https://evil.example/p0420", False),
            ("https://obdfyi.com.evil.example/p0420", False),
            ("file:///etc/passwd", False),
            ("ftp://obdfyi.com/x", False),
            ("https://localhost/x", False),
            ("https://127.0.0.1/x", False),
            ("https://169.254.169.254/latest/meta-data", False),
            ("https://10.0.0.5/x", False),
            ("https://192.168.1.10/x", False),
            ("https://[::1]/x", False),
            ("https://[fe80::1%25eth0]/x", False),
            ("", False),
        ],
    )
    def test_url_policy(self, verify_mod, url, allowed) -> None:
        assert verify_mod._url_allowed(url) is allowed

    def test_fetch_refuses_disallowed_host_without_network(self, verify_mod) -> None:
        # If this opened a socket to evil.example the test environment would
        # either hang or resolve it; the policy must return before any I/O.
        assert verify_mod.fetch("https://evil.example/p0420") == (0, "")
        assert verify_mod.fetch("https://127.0.0.1:8080/x") == (0, "")

    def test_redirect_to_private_address_is_refused(self, verify_mod) -> None:
        import urllib.error
        import urllib.request

        limiter = verify_mod._RedirectLimiter()
        req = urllib.request.Request("https://obdfyi.com/codes/p0420")
        with pytest.raises(urllib.error.HTTPError):
            limiter.redirect_request(req, None, 302, "Found", {}, "http://127.0.0.1/x")

    def test_redirect_limit_is_enforced(self, verify_mod) -> None:
        import urllib.error
        import urllib.request

        limiter = verify_mod._RedirectLimiter()
        req = urllib.request.Request("https://obdfyi.com/codes/p0420")
        req._t66_redirects = verify_mod.MAX_REDIRECTS  # noqa: SLF001
        with pytest.raises(urllib.error.HTTPError):
            limiter.redirect_request(
                req, None, 302, "Found", {}, "https://obdfyi.com/codes/p0421"
            )

    def test_bounded_streaming_read_is_capped(self, verify_mod, monkeypatch) -> None:
        """A response larger than MAX_PAGE_BYTES is refused, not buffered."""
        import io
        import urllib.request

        class _BigResponse(io.BytesIO):
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_open(self, req, timeout=None):
            return _BigResponse(b"x" * (verify_mod.MAX_PAGE_BYTES + 10))

        monkeypatch.setattr(urllib.request.OpenerDirector, "open", fake_open)
        status, body = verify_mod.fetch("https://obdfyi.com/codes/p0420")
        assert status == 200 and body == "", "over-cap pages must not be buffered"
