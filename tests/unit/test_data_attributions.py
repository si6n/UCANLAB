"""T2-7 — "Open Source & Data Attributions" rendered surface (legal compliance).

Context: T2-4 vendored four external datasets into ``data/``. SITRAK error
codes are **CC BY 4.0**, which obliges the distributor to keep the credit
``Источник: МегаДата / megadata.pro — CC BY 4.0`` *reasonably visible to the
recipient*; canboat is Apache-2.0 (NOTICE, §4(d)) and Wal33D/dtc-database is MIT
(copyright notice). Shipping the notice files satisfies the
distribution-content half of that duty — the panel + the read-only bridge
method tested here are the rendered half.

What these tests actually prove (no aspirational claims):

1. The bridge method ``get_data_attributions()`` exists, is risk-classified
   ``read``, and returns the SITRAK credit **byte-for-byte**.
2. The credit is genuinely reachable through the JS-facing surface
   (``DesktopBridge.getDataAttributions`` calls it) — not merely present in a
   Python constant.
3. The React panel renders the credit **inline** (not behind a click) and is
   reachable from the settings navigation.
4. The catalog cannot silently drift from ``data/licenses/*`` /
   ``data/diagnostics/PROVENANCE.md``: every pinned commit SHA, source URL and
   licence id recorded in the catalog is re-derived from those files on disk.
5. The method is read-only: no TX / E-Stop / minting surface is touched.

Note on scope: these tests assert the *text* is reachable and rendered, not
that a browser painted it. ``vite build`` cannot run under the current file
sandbox (``spawn EPERM`` on piped stdio), so the render-path assertions are
done over the React source plus the real bridge call — see the T2-7 report.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from src.ui.data_attribution_catalog import (
    DATA_SOURCE_LICENSES,
    OBLIGATION_REQUIRED,
    SITRAK_ATTRIBUTION_TEXT,
    build_attribution_payload,
    get_attribution_sources,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The single string CC BY 4.0 compliance hinges on. Copied by hand into the
#: test (NOT imported) so the assertion still fails if someone edits the
#: constant in the module under test — an imported constant would move with the
#: bug it is supposed to catch.
SITRAK_CREDIT_EXPECTED = "Источник: МегаДата / megadata.pro — CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)"

SITRAK_ATTRIBUTION_FILE = REPO_ROOT / "data" / "licenses" / "ATTRIBUTION.sitrak.md"
FRONTEND_SRC = REPO_ROOT / "src" / "ui" / "frontend" / "src"
PANEL_TSX = FRONTEND_SRC / "components" / "workbench" / "SettingsAttributionPanel.tsx"
# B8: the workbench Settings screen (the old settings/SettingsView.tsx was removed).
VIEW_TSX = FRONTEND_SRC / "components" / "workbench" / "SettingsPanel.tsx"
BRIDGE_TS = FRONTEND_SRC / "services" / "bridge.ts"
DESKTOP_APP_PY = REPO_ROOT / "src" / "ui" / "desktop_app.py"


def _strip_ts_comments(source: str) -> str:
    """Remove TS/TSX block + line comments so code-only assertions are exact."""
    without_block = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", without_block, flags=re.MULTILINE)


def _bridge():
    """Build a real DesktopApiBridge over a fresh (offline, virtual) app."""
    from src.ui.desktop_app import DesktopApiBridge, UniversalCanDesktopApp

    app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
    return DesktopApiBridge(app)


# ===========================================================================
# 1. The canonical source of truth still holds the SITRAK credit
# ===========================================================================


class TestCanonicalSourceOfTruth:
    def test_sitrak_credit_constant_matches_the_vendored_file_byte_for_byte(self) -> None:
        """Zero-fabrication guard: the constant must exist verbatim in the file
        CC BY 4.0 requires us to preserve."""
        text = SITRAK_ATTRIBUTION_FILE.read_text(encoding="utf-8")
        assert SITRAK_CREDIT_EXPECTED in text, (
            "SITRAK CC-BY-4.0 credit is no longer verbatim in data/licenses/ATTRIBUTION.sitrak.md"
        )
        assert SITRAK_ATTRIBUTION_TEXT == SITRAK_CREDIT_EXPECTED, (
            "the catalog constant drifted from the mandated CC BY 4.0 credit"
        )

    def test_attribution_text_matches_the_provenance_record(self) -> None:
        """The credit the UI shows must equal the one PROVENANCE.md records."""
        provenance = (REPO_ROOT / "data" / "diagnostics" / "PROVENANCE.md").read_text(encoding="utf-8")
        assert SITRAK_CREDIT_EXPECTED in provenance

    def test_every_required_attribution_is_verbatim_in_its_canonical_file(self) -> None:
        """canboat (Apache-2.0 §4(d)) and Wal33D (MIT) must also be verbatim."""
        for entry in get_attribution_sources():
            if entry.obligation != OBLIGATION_REQUIRED or entry.id == "obdex":
                continue
            canon = (REPO_ROOT / entry.canonical_file).read_text(encoding="utf-8")
            assert entry.attribution_text in canon, (
                f"{entry.id}: attribution_text is not verbatim in {entry.canonical_file}"
            )

    def test_obdex_text_is_declared_a_paraphrase_not_licence_text(self) -> None:
        """OBDex carries no obligation — its Turkish summary must NOT be
        presented as (or accidentally equal to) upstream licence text."""
        obdex = next(e for e in get_attribution_sources() if e.id == "obdex")
        canon = (REPO_ROOT / obdex.canonical_file).read_text(encoding="utf-8")
        assert obdex.attribution_text not in canon
        assert obdex.obligation != OBLIGATION_REQUIRED


# ===========================================================================
# 2. Drift control — re-derive metadata from the vendored files on disk
# ===========================================================================


class TestNoSilentDrift:
    def test_pinned_commits_and_urls_still_appear_in_the_vendored_files(self) -> None:
        """Falsifier for 'the panel quietly lies after the data is re-vendored':
        every commit SHA / source URL in the catalog is re-derived from the
        licence files + PROVENANCE.md, not trusted."""
        corpus = "\n".join(
            path.read_text(encoding="utf-8")
            for path in [
                *sorted((REPO_ROOT / "data" / "licenses").glob("*")),
                REPO_ROOT / "data" / "diagnostics" / "PROVENANCE.md",
            ]
            if path.is_file()
        )
        for entry in get_attribution_sources():
            assert entry.pinned_commit, f"{entry.id}: catalog lost its pinned commit"
            assert entry.pinned_commit in corpus, (
                f"{entry.id}: pinned commit {entry.pinned_commit} no longer appears "
                "in data/licenses/* or PROVENANCE.md — the catalog has drifted"
            )
            assert entry.source_url in corpus, (
                f"{entry.id}: source URL {entry.source_url} no longer appears in the "
                "vendored files — the catalog has drifted"
            )
            assert entry.license in corpus, (
                f"{entry.id}: licence id {entry.license} no longer appears in the "
                "vendored files — the catalog has drifted"
            )

    def test_commit_sha_hashes_are_well_formed(self) -> None:
        """A truncated/typo'd SHA is a silent lie; shape-check all of them."""
        for entry in get_attribution_sources():
            assert re.fullmatch(r"[0-9a-f]{40}", entry.pinned_commit), (
                f"{entry.id}: {entry.pinned_commit!r} is not a full 40-char git SHA"
            )

    def test_every_catalog_entry_names_an_existing_canonical_file(self) -> None:
        for entry in get_attribution_sources():
            assert (REPO_ROOT / entry.canonical_file).is_file(), (
                f"{entry.id}: canonical file missing: {entry.canonical_file}"
            )

    def test_catalog_covers_all_four_t2_4_sources(self) -> None:
        """If T2-4 grows a fifth source, this list must grow with it."""
        assert {e.id for e in DATA_SOURCE_LICENSES} == {
            "sitrak-error-codes",
            "canboat",
            "dtc-database-wal33d",
            "obdex",
        }
        required = {e.id for e in DATA_SOURCE_LICENSES if e.obligation == OBLIGATION_REQUIRED}
        assert required == {"sitrak-error-codes", "canboat", "dtc-database-wal33d"}


# ===========================================================================
# 3. The bridge method: read-only, risk-classified, and actually reachable
# ===========================================================================


class TestBridgeMethod:
    def test_bridge_exposes_the_read_only_attribution_method(self) -> None:
        bridge = _bridge()
        assert hasattr(bridge, "get_data_attributions")
        payload = bridge.get_data_attributions()

        assert payload["success"] is True
        assert payload["networkAccess"] is False
        assert payload["offline"] is True
        assert payload["errors"] == []

    def test_bridge_payload_carries_the_sitrak_credit_verbatim(self) -> None:
        """THE compliance assertion: the mandated credit is reachable through the
        JS-facing bridge surface, byte-for-byte."""
        payload = _bridge().get_data_attributions()
        sitrak = next(s for s in payload["sources"] if s["id"] == "sitrak-error-codes")

        assert sitrak["attributionText"] == SITRAK_CREDIT_EXPECTED
        assert "МегаДата" in sitrak["attributionText"]
        assert "megadata.pro" in sitrak["attributionText"]
        assert sitrak["license"] == "CC-BY-4.0"
        assert "creativecommons.org/licenses/by/4.0" in sitrak["licenseUrl"]

    def test_bridge_delivers_the_verbatim_licence_file_body(self) -> None:
        """The panel shows the vendored file text, not a re-typed summary; a
        source whose file cannot be read must be reported, not silently blank."""
        payload = _bridge().get_data_attributions()
        for source in payload["sources"]:
            assert source["sourceText"], f"{source['id']}: no licence body delivered"
            assert source["sourcePath"] == source["canonicalFile"]
        sitrak = next(s for s in payload["sources"] if s["id"] == "sitrak-error-codes")
        # The full canonical file text travels with the entry.
        assert "ATTRIBUTION — SITRAK error codes (CC BY 4.0)" in sitrak["sourceText"]
        # ...and the mandated credit is inside that delivered body too.
        assert SITRAK_CREDIT_EXPECTED in sitrak["sourceText"]

    def test_bridge_reports_required_obligation_count(self) -> None:
        payload = _bridge().get_data_attributions()
        assert payload["obligationRequiredCount"] == 3

    def test_bridge_method_is_risk_classified_read(self) -> None:
        """R2-U1 manifest: the method must be classified, and as 'read'."""
        from src.ui.desktop_app import DesktopApiBridge

        assert DesktopApiBridge.BRIDGE_RISK_MANIFEST["get_data_attributions"] == "read"

    def test_bridge_method_is_read_only_no_tx_safety_or_minting_surface(self) -> None:
        """AGENTS.md §2.5/§2.8: the renderer must never reach TX, E-Stop,
        watchdog-arming or licence-minting through this method."""
        source = DESKTOP_APP_PY.read_text(encoding="utf-8")
        tree = ast.parse(source)
        method = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "DesktopApiBridge":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "get_data_attributions":
                        method = item
        assert method is not None, "get_data_attributions is missing from DesktopApiBridge"

        body = ast.get_source_segment(source, method) or ""
        forbidden = (
            "validate_and_transmit",
            "arm_tx",
            "disarm_tx",
            "estop",
            "mint",
            "issue_confirmation_token",
            "reset_estop",
            "watchdog",
            "cloud_client",
            "urllib",
            "requests",
            "socket",
            "httpx",
        )
        lowered = body.lower()
        for needle in forbidden:
            assert needle.lower() not in lowered, (
                f"read-only guarantee broken: get_data_attributions references {needle!r}"
            )
        # It must take no renderer-supplied argument that could become a path.
        assert not method.args.args[1:], "method must accept no arguments"

    def test_payload_builder_reads_only_allowlisted_files(self, tmp_path: Path) -> None:
        """A renderer-reachable reader must never become an arbitrary file read;
        the allowlist is positive and lives in desktop_app."""
        from src.ui.desktop_app import ATTRIBUTION_ALLOWED_FILES

        assert set(ATTRIBUTION_ALLOWED_FILES) == {
            "data/licenses/ATTRIBUTION.sitrak.md",
            "data/licenses/NOTICE.canboat",
            "data/licenses/ATTRIBUTION.obdex-and-dtcdb.md",
            "data/diagnostics/PROVENANCE.md",
        }
        for entry in get_attribution_sources():
            assert entry.canonical_file in ATTRIBUTION_ALLOWED_FILES

        # Against an empty root the builder still reports the legal strings and
        # flags the gap instead of crashing or silently pretending compliance.
        payload = build_attribution_payload(tmp_path)
        assert payload["success"] is True
        assert payload["errors"], "missing canonical files were not reported"
        assert any(
            s["id"] == "sitrak-error-codes" and s["attributionText"] == SITRAK_CREDIT_EXPECTED
            for s in payload["sources"]
        )


# ===========================================================================
# 4. The JS-facing wrapper and the React render path
# ===========================================================================


class TestRendererSurface:
    def test_js_bridge_wrapper_calls_the_native_method(self) -> None:
        ts = BRIDGE_TS.read_text(encoding="utf-8")
        assert "get_data_attributions?" in ts, "the pywebview API type is not declared"
        assert "getDataAttributions" in ts

        helper = ts.index("public static async getDataAttributions")
        body = ts[helper : helper + 900]
        assert "apiMethod('get_data_attributions')" in body, "the TS wrapper does not call the native bridge method"
        # Fail loudly instead of fabricating an (empty) compliant-looking list.
        assert "requireCapability('get_data_attributions'" in body
        assert "NATIVE_BRIDGE_MISSING" in body

    def test_panel_renders_the_attribution_text_inline_not_behind_a_click(self) -> None:
        """The SITRAK credit must be visible when the panel is open — not
        inside an accordion, tooltip or modal. Guards against a future refactor
        that hides the only legally-required string behind a disclosure."""
        tsx = PANEL_TSX.read_text(encoding="utf-8")

        # The verbatim render helper exists...
        assert 'data-testid="attribution-verbatim-text"' in tsx
        # ...and is called unconditionally in the card body, OUTSIDE the
        # `expanded` conditional block that gates the long-form file body.
        assert "renderVerbatimBlock(src.attributionText" in tsx
        expand_index = tsx.index("{isExpanded && (")
        verbatim_index = tsx.index("renderVerbatimBlock(src.attributionText")
        assert verbatim_index < expand_index, (
            "attributionText is rendered after/inside the expandable block — the "
            "required credit would be hidden behind a click"
        )
        # No truncation of the required credit.
        assert "whitespace-pre-wrap" in tsx
        assert (
            "text-overflow" not in tsx
            and "truncate" not in tsx.split("attribution-verbatim-text")[1].split("</div>")[0]
        )

    def test_panel_draws_all_fields_the_task_card_requires(self) -> None:
        """name, licence, source URL, pinned commit SHA, attribution text."""
        tsx = PANEL_TSX.read_text(encoding="utf-8")
        for field in (
            "src.name",
            "src.license",
            "src.sourceUrl",
            "src.pinnedCommit",
            "src.attributionText",
            "src.canonicalFile",
        ):
            assert field in tsx, f"panel does not render {field}"

    def test_panel_has_no_hardcoded_attribution_copy(self) -> None:
        """Single source of truth: the TSX must not carry a second copy of the
        credit (or any commit SHA) that could drift from the Python catalog.

        Comments are stripped first — the file explains *why* the credit is
        mandatory and must be allowed to quote the modal legal language. What is
        forbidden is a hardcoded value in rendered code.
        """
        tsx = PANEL_TSX.read_text(encoding="utf-8")
        code = _strip_ts_comments(tsx)

        for entry in get_attribution_sources():
            assert entry.pinned_commit not in code, (
                f"{entry.id}: the panel hardcodes a commit SHA instead of using the bridge"
            )
            assert entry.source_url not in code, (
                f"{entry.id}: the panel hardcodes a source URL instead of using the bridge"
            )
        assert "МегаДата" not in code, "the panel hardcodes the SITRAK credit"
        assert "megadata.pro" not in code, "the panel hardcodes the SITRAK credit"
        assert "creativecommons.org" not in code, "the panel hardcodes a licence URL instead of using the bridge"
        # Sanity: the comment really did explain the obligation (so stripping
        # did not just pass because the file is silent about it).
        assert "CC-BY-4.0" in tsx or "CC BY 4.0" in tsx

    def test_panel_fetches_through_the_bridge_not_local_files(self) -> None:
        tsx = PANEL_TSX.read_text(encoding="utf-8")
        assert "DesktopBridge.getDataAttributions()" in tsx

    def test_settings_navigation_exposes_the_attribution_section(self) -> None:
        """The panel must be reachable without editing source."""
        view = VIEW_TSX.read_text(encoding="utf-8")
        assert "import { SettingsAttributionPanel } from './SettingsAttributionPanel';" in view
        assert "{ value: 'sources'" in view
        assert "{section === 'sources' && <SettingsAttributionPanel />}" in view

    def test_settings_nav_type_union_allows_the_section(self) -> None:
        """The compile-time section union must include 'sources'."""
        view = VIEW_TSX.read_text(encoding="utf-8")
        match = re.search(r"type Section = ([^;]*);", view)
        assert match is not None and "'sources'" in match.group(1)
        assert "'connection'" in match.group(1)

    def test_panel_declares_the_offline_guarantee_to_the_operator(self) -> None:
        tsx = PANEL_TSX.read_text(encoding="utf-8")
        assert "Çevrimdışı" in tsx
        assert "Salt okunur" in tsx
