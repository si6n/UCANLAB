"""Aşama 4: vehicle catalog honesty, VIN / J1939 identity check, mode prefs, bridge."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.engine.vehicle.identity import (
    compare_identity,
    j1939_manufacturer_names,
    mask_vin,
    normalize_vin,
    profiles_for_vin,
)
from src.engine.vehicle.profiles import CatalogError, default_catalog, load_catalog
from src.ui.mechanic_prefs import MechanicPrefs, MechanicPrefsStore

_TYPE = {
    "id": "truck", "label_tr": "Kamyon", "label_en": "Truck", "sub_tr": "a", "sub_en": "a",
    "protocol": "j1939", "socket": "deutsch_9pin", "bitrate_candidates": [250000],
    "expected_traffic": [{"kind": "j1939_pgn", "pgn": 61444, "name_tr": "Motor", "name_en": "Engine"}],
    "plug_tr": "p", "plug_en": "p", "passive_note_tr": "n", "passive_note_en": "n",
}


def _write_catalog(tmp_path: Path, profiles: list[dict[str, Any]], types: list[dict[str, Any]] | None = None) -> Path:
    path = tmp_path / "vehicle_profiles.json"
    path.write_text(json.dumps({"schema_version": 1, "vehicle_types": types or [_TYPE], "profiles": profiles}),
                    encoding="utf-8")
    return path


def _dbc_root(tmp_path: Path) -> Path:
    root = tmp_path / "dbc"
    (root / "heavy_duty").mkdir(parents=True, exist_ok=True)
    (root / "heavy_duty" / "scania_test.dbc").write_text("VERSION \"\"", encoding="utf-8")
    return root


_GENERIC = {"id": "truck_generic", "type": "truck", "make": "J1939", "label_tr": "Genel", "label_en": "Generic",
            "coverage": "standard"}


# ---------------------------------------------------------------------------
# Catalog: the shipped file loads and never claims coverage it cannot back
# ---------------------------------------------------------------------------


def test_shipped_catalog_loads_and_is_honest() -> None:
    catalog = load_catalog()
    assert {t.id for t in catalog.types} == {"car", "truck", "boat", "construction"}
    for profile in catalog.profiles:
        if profile.coverage == "enriched":
            assert profile.dbc_files or profile.oem_decoder, profile.id
        else:
            assert not profile.dbc_files and profile.oem_decoder is None, profile.id
        if profile.coverage == "unsupported":
            assert profile.note_tr and not profile.selectable
    for vtype in catalog.types:
        assert any(p.selectable for p in catalog.profiles_for(vtype.id)), vtype.id
        assert vtype.bitrate_candidates


def test_shipped_catalog_is_listed_in_gitignore_allowlist_and_bundle() -> None:
    root = Path(__file__).resolve().parents[2]
    assert "!data/vehicle_profiles.json" in (root / ".gitignore").read_text(encoding="utf-8")
    assert "vehicle_profiles.json" in (root / "scripts" / "build_exe.py").read_text(encoding="utf-8")


def test_catalog_as_dict_is_json_safe_and_hides_file_paths() -> None:
    payload = default_catalog().as_dict()
    json.dumps(payload)
    assert all("dbc_files" not in p for p in payload["profiles"])
    assert any(not p["selectable"] for p in payload["profiles"])


def test_enriched_without_matching_files_fails(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, [_GENERIC, {**_GENERIC, "id": "x", "coverage": "enriched",
                                                "dbc_globs": ["heavy_duty/man_*.dbc"]}])
    with pytest.raises(CatalogError, match="match no file"):
        load_catalog(path, _dbc_root(tmp_path))


def test_enriched_without_any_data_fails(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, [_GENERIC, {**_GENERIC, "id": "x", "coverage": "enriched"}])
    with pytest.raises(CatalogError, match="needs maker data"):
        load_catalog(path, _dbc_root(tmp_path))


def test_standard_may_not_reference_maker_data(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, [_GENERIC, {**_GENERIC, "id": "x", "dbc_globs": ["heavy_duty/scania_*.dbc"]}])
    with pytest.raises(CatalogError, match="only 'enriched'"):
        load_catalog(path, _dbc_root(tmp_path))


def test_unknown_oem_decoder_fails(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, [_GENERIC, {**_GENERIC, "id": "x", "coverage": "enriched",
                                                "oem_decoder": "Imaginary"}])
    with pytest.raises(CatalogError, match="not a registered"):
        load_catalog(path, _dbc_root(tmp_path))


def test_unsupported_needs_reason_and_type_needs_selectable(tmp_path: Path) -> None:
    bare = {**_GENERIC, "coverage": "unsupported"}
    with pytest.raises(CatalogError, match="note"):
        load_catalog(_write_catalog(tmp_path, [bare]), _dbc_root(tmp_path))
    with pytest.raises(CatalogError, match="no selectable"):
        load_catalog(_write_catalog(tmp_path, [{**bare, "note_tr": "yok"}]), _dbc_root(tmp_path))


@pytest.mark.parametrize("wmi", ["W", "WVWX", "W-1"])
def test_bad_wmi_fails(tmp_path: Path, wmi: str) -> None:
    path = _write_catalog(tmp_path, [{**_GENERIC, "wmi": [wmi]}])
    with pytest.raises(CatalogError, match="wmi"):
        load_catalog(path, _dbc_root(tmp_path))


def test_duplicate_ids_and_unknown_type_fail(tmp_path: Path) -> None:
    with pytest.raises(CatalogError, match="unique"):
        load_catalog(_write_catalog(tmp_path, [_GENERIC, _GENERIC]), _dbc_root(tmp_path))
    with pytest.raises(CatalogError, match="unknown vehicle type"):
        load_catalog(_write_catalog(tmp_path, [{**_GENERIC, "type": "plane"}]), _dbc_root(tmp_path))


def test_bad_schema_and_bitrates_fail(tmp_path: Path) -> None:
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
    with pytest.raises(CatalogError, match="schema_version"):
        load_catalog(path)
    with pytest.raises(CatalogError, match="bitrate"):
        load_catalog(_write_catalog(tmp_path, [_GENERIC], [{**_TYPE, "bitrate_candidates": [0]}]))


def test_valid_enriched_profile_records_matched_files(tmp_path: Path) -> None:
    path = _write_catalog(tmp_path, [_GENERIC, {**_GENERIC, "id": "truck_scania", "coverage": "enriched",
                                                "oem_decoder": "Scania", "dbc_globs": ["heavy_duty/scania_*.dbc"]}])
    profile = load_catalog(path, _dbc_root(tmp_path)).profile("truck_scania")
    assert profile is not None and profile.dbc_files == (str(Path("heavy_duty/scania_test.dbc")),)


# ---------------------------------------------------------------------------
# Identity: VIN and J1939 NAME comparison
# ---------------------------------------------------------------------------


def test_vin_normalization_and_masking() -> None:
    assert normalize_vin(" ys2r4x20005399401 ") == "YS2R4X20005399401"
    assert normalize_vin("YS2R4X2000539940O") is None  # letter O is never used
    assert normalize_vin("SHORT") is None
    assert normalize_vin(None) is None
    assert mask_vin("YS2R4X20005399401") == "YS2**********9401"
    assert mask_vin("x") == "***"


def test_vin_match_and_mismatch_suggests_same_type() -> None:
    catalog = default_catalog()
    scania = catalog.profile("truck_scania")
    assert scania is not None
    assert compare_identity(catalog, scania, vin="YS2R4X20005399401").status == "match"
    result = compare_identity(catalog, scania, vin="YV2RT40A5KB123456")
    assert (result.status, result.source, result.suggested_profile_id) == ("mismatch", "vin", "truck_volvo")


def test_shared_wmi_prefers_selected_type() -> None:
    catalog = default_catalog()
    assert {p.id for p in profiles_for_vin(catalog, "WDB9634031L123456")} == {"car_mercedes", "truck_actros"}
    actros = catalog.profile("truck_actros")
    assert actros is not None
    assert compare_identity(catalog, actros, vin="WDB9634031L123456").status == "match"
    scania = catalog.profile("truck_scania")
    assert scania is not None
    assert compare_identity(catalog, scania, vin="WDB9634031L123456").suggested_profile_id == "truck_actros"


def test_generic_profile_and_unknown_wmi_never_guess() -> None:
    catalog = default_catalog()
    generic = catalog.profile("truck_generic")
    scania = catalog.profile("truck_scania")
    assert generic is not None and scania is not None
    assert compare_identity(catalog, generic, vin="YV2RT40A5KB123456").status == "unknown"
    assert compare_identity(catalog, scania, vin="ZZZRT40A5KB123456").status == "unknown"
    assert compare_identity(catalog, scania).status == "unknown"


def test_j1939_name_manufacturer_comparison() -> None:
    catalog = default_catalog()
    scania = catalog.profile("truck_scania")
    assert scania is not None
    assert compare_identity(catalog, scania, j1939_manufacturer_codes=[68]).status == "match"
    result = compare_identity(catalog, scania, j1939_manufacturer_codes=[60])
    assert result.status == "mismatch" and result.source == "j1939_name"
    assert result.suggested_profile_id == "truck_volvo"
    assert "Volvo" in result.detected_tr
    # Codes that belong to no known OEM decoder say nothing.
    assert compare_identity(catalog, scania, j1939_manufacturer_codes=[9999]).status == "unknown"


def test_manufacturer_table_comes_from_vendored_dbc(tmp_path: Path) -> None:
    names = j1939_manufacturer_names()
    assert names.get(68, "").lower().startswith("scania")
    missing = tmp_path / "none.dbc"
    assert j1939_manufacturer_names.__wrapped__(missing) == {}


# ---------------------------------------------------------------------------
# Mode / vehicle preferences
# ---------------------------------------------------------------------------


def test_prefs_roundtrip_and_partial_update(tmp_path: Path) -> None:
    store = MechanicPrefsStore(tmp_path / "sub" / "prefs.json")
    assert store.load() == MechanicPrefs()
    store.update(mode="mechanic")
    assert store.update(vehicle_profile_id="truck_scania") == MechanicPrefs("mechanic", "truck_scania")
    assert MechanicPrefsStore(store.path).load() == MechanicPrefs("mechanic", "truck_scania")
    assert not list(store.path.parent.glob(".mechanic_prefs.*"))


@pytest.mark.parametrize("content", ["{broken", "[]", '{"mode": "admin", "vehicle_profile_id": 5}'])
def test_prefs_corrupt_file_asks_again(tmp_path: Path, content: str) -> None:
    path = tmp_path / "prefs.json"
    path.write_text(content, encoding="utf-8")
    assert MechanicPrefsStore(path).load() == MechanicPrefs()


def test_prefs_reject_unknown_mode(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        MechanicPrefsStore(tmp_path / "p.json").save(MechanicPrefs(mode="root"))


# ---------------------------------------------------------------------------
# Bridge methods
# ---------------------------------------------------------------------------


def _bridge(tmp_path: Path, *, engineer: bool = False, vin: str | None = None, codes: list[int] | None = None):
    from src.ui.desktop_app import DesktopApiBridge

    nodes = {f"0x{i:02X}": {"manufacturer_code": c} for i, c in enumerate(codes or [])}
    app = SimpleNamespace(
        mechanic_prefs=MechanicPrefsStore(tmp_path / "prefs.json"),
        _detected_vin=vin,
        j1939_get_address_claim_status=lambda: {"nodes": nodes},
    )
    bridge = DesktopApiBridge(app)  # type: ignore[arg-type]
    bridge._entitlements = lambda: {"mechanic": True, "engineer": engineer}  # type: ignore[method-assign]
    return bridge, app


def test_bridge_mode_selection_respects_license(tmp_path: Path) -> None:
    bridge, app = _bridge(tmp_path)
    assert bridge.mechanic_get_state()["mode"] is None
    denied = bridge.mechanic_set_mode("engineer")
    assert denied["error_code"] == "ENGINEER_NOT_ALLOWED" and denied["message_tr"]
    assert bridge.mechanic_set_mode("root")["error_code"] == "INVALID_MODE"
    assert bridge.mechanic_set_mode("mechanic") == {"success": True, "mode": "mechanic"}
    assert app.mechanic_prefs.load().mode == "mechanic"

    pro, _ = _bridge(tmp_path, engineer=True)
    assert pro.mechanic_set_mode("engineer")["mode"] == "engineer"


def test_bridge_vehicle_select(tmp_path: Path) -> None:
    bridge, app = _bridge(tmp_path)
    assert bridge.vehicle_catalog()["success"] is True
    assert bridge.vehicle_select("nope")["error_code"] == "INVALID_PROFILE"
    assert bridge.vehicle_select("x" * 65)["error_code"] == "INVALID_PROFILE"
    blocked = bridge.vehicle_select("boat_nmea0183")
    assert blocked["error_code"] == "PROFILE_UNSUPPORTED" and "0183" in blocked["message_tr"]
    chosen = bridge.vehicle_select("truck_scania")
    assert chosen["success"] and chosen["type"]["bitrate_candidates"] == [250000, 500000]
    assert app.mechanic_prefs.load().vehicle_profile_id == "truck_scania"


def test_bridge_identity_check(tmp_path: Path) -> None:
    bridge, _ = _bridge(tmp_path, vin="YV2RT40A5KB123456")
    assert bridge.vehicle_check_identity()["error_code"] == "NO_VEHICLE_SELECTED"
    bridge.vehicle_select("truck_scania")
    assert bridge.vehicle_check_identity()["suggested_profile_id"] == "truck_volvo"

    by_name, _ = _bridge(tmp_path, codes=[68])
    assert by_name.vehicle_check_identity()["status"] == "match"


def test_bridge_identity_survives_claim_status_errors(tmp_path: Path) -> None:
    bridge, app = _bridge(tmp_path)
    bridge.vehicle_select("truck_scania")

    def boom() -> dict[str, Any]:
        raise RuntimeError("bus gone")

    app.j1939_get_address_claim_status = boom
    assert bridge.vehicle_check_identity()["status"] == "unknown"
