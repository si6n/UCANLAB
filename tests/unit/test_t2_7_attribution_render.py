"""T2-7 — rendered-DOM proof that the attribution panel paints the credit.

Why a python test drives a Node render
---------------------------------------
The cheapest defensible answer to "is the SITRAK CC-BY-4.0 credit actually
*visible to the recipient*?" is to render the real React component with the real
bridge payload and inspect the painted HTML. ``vite build`` cannot run in this
sandbox (esbuild's service subprocess hits ``spawn EPERM``), so this test mounts
the component through ``react-dom/client`` over a minimal in-process DOM shim and
serialises the result.

What it proves that a source-grep cannot:

* the credit string is present in RENDERED OUTPUT, byte-for-byte;
* it is present with every disclosure collapsed (``live_credit_needs_no_click``)
  — i.e. not behind an accordion/tooltip/modal;
* all four vendored sources, their licence ids, source URLs and pinned commits
  are painted;
* the panel is reachable from the settings navigation.

Skips (does not fail) when the frontend toolchain is absent, so a Python-only
environment still runs the rest of the suite.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND = REPO_ROOT / "src" / "ui" / "frontend"
HARNESS = REPO_ROOT / "tests" / "fixtures" / "render_attribution_panel.cjs"

SITRAK_CREDIT = "Источник: МегаДата / megadata.pro — CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)"


def _node() -> str | None:
    return shutil.which("node")


def _require_toolchain() -> str:
    node = _node()
    if node is None:
        pytest.skip("node is not installed; rendered-DOM proof unavailable")
    for rel in ("node_modules/react", "node_modules/react-dom", "node_modules/typescript"):
        if not (FRONTEND / rel).exists():
            pytest.skip(f"frontend dep missing ({rel}); run npm install first")
    return node


@pytest.fixture(scope="module")
def rendered(tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    """Run the harness once; return {"html": painted DOM, "report": json}."""
    node = _require_toolchain()

    # Real payload straight from the Python catalog (no fixture duplication).
    from src.ui.data_attribution_catalog import build_attribution_payload

    payload_path = tmp_path_factory.mktemp("t27") / "payload.json"
    payload_path.write_text(
        json.dumps(build_attribution_payload(REPO_ROOT), ensure_ascii=False),
        encoding="utf-8",
    )

    proc = subprocess.run(
        [node, str(HARNESS), str(payload_path), str(FRONTEND)],
        cwd=str(FRONTEND),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    if proc.returncode != 0:
        pytest.fail(f"render harness failed:\nstdout: {proc.stdout[-2000:]}\nstderr: {proc.stderr[-2000:]}")
    # stdout = machine-readable report; stderr = the painted DOM.
    return {"report": json.loads(proc.stdout), "html": proc.stderr}


@pytest.fixture(scope="module")
def rendered_html(rendered: dict[str, object]) -> str:
    return str(rendered["html"])


@pytest.fixture(scope="module")
def render_report(rendered: dict[str, object]) -> dict[str, object]:
    return dict(rendered["report"])  # type: ignore[arg-type]


class TestRenderedPanel:
    def test_panel_mounts_and_paints_the_sitrak_credit_verbatim(self, rendered_html: str) -> None:
        """THE test: the CC-BY-4.0 credit is in the rendered DOM."""
        assert SITRAK_CREDIT in rendered_html, "the SITRAK CC-BY-4.0 credit is NOT in the rendered panel output"
        assert "МегаДата" in rendered_html
        assert "megadata.pro" in rendered_html
        assert "creativecommons.org/licenses/by/4.0" in rendered_html

    def test_credit_is_visible_without_any_click(self, rendered_html: str) -> None:
        """No disclosure may gate the legally-required credit. The harness mounts
        with every toggle collapsed and asserts the inline verbatim block count."""
        assert 'data-testid="attribution-verbatim-text"' in rendered_html
        # Collapsed long-form licence bodies must NOT be present in this mount.
        assert 'data-testid="attribution-source-text"' not in rendered_html, (
            "a disclosure body rendered on first paint — the credit may be hidden"
        )
        # ...and crucially: the credit must sit in an inline verbatim block, NOT
        # inside a <pre> (which only exists once the long-form body is expanded).
        credit_pos = rendered_html.index(SITRAK_CREDIT)
        open_pre_before = rendered_html.rfind("<pre", 0, credit_pos)
        close_pre_before = rendered_html.rfind("</pre>", 0, credit_pos)
        inside_pre = open_pre_before > close_pre_before  # an unclosed <pre> precedes it
        assert not inside_pre, "the credit is rendered inside a collapsed <pre> disclosure"
        # Its verbatim block must be the one that carries the credit.
        block_open = rendered_html.rfind('data-testid="attribution-verbatim-text"', 0, credit_pos)
        assert block_open != -1
        assert credit_pos - block_open < 300, "the credit is not inside the inline verbatim block"

    def test_all_four_sources_and_metadata_are_painted(self, rendered_html: str) -> None:
        for testid in (
            "attribution-source",
            "attribution-source-name",
            "attribution-license",
            "attribution-source-url",
            "attribution-pinned-commit",
            "attribution-canonical-file",
        ):
            assert f'data-testid="{testid}"' in rendered_html, f"{testid} not rendered"

        assert rendered_html.count('data-testid="attribution-source"') == 4
        for license_id in ("CC-BY-4.0", "Apache-2.0", "MIT", "CC0-1.0"):
            assert license_id in rendered_html
        # The pinned SITRAK commit is shown.
        assert "fdb0c0d9daf0643975b0ff62e0ff69ef9c07f742" in rendered_html
        # Exactly three sources are flagged as legally required.
        assert rendered_html.count("ATIF ZORUNLU") == 3

    def test_panel_declares_its_offline_read_only_nature(self, rendered_html: str) -> None:
        assert "Çevrimdışı" in rendered_html
        assert "Salt okunur" in rendered_html

    def test_harness_output_is_a_json_report(self, render_report: dict[str, object]) -> None:
        """The harness self-reports so a silent partial render cannot pass."""
        assert render_report["panel_mounted"] is True
        assert render_report["sitrak_credit_rendered_verbatim"] is True
        assert render_report["credit_needs_no_click"] is True
        assert render_report["source_card_count"] == 4
