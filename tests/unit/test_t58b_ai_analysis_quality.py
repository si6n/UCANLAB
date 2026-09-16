"""T58-B (ORTA) — AI motoru analiz kalitesi: A3-7 / A3-8 / A3-10 / A3-13.

Her test, önce BAŞARISIZ olacak şekilde yazıldı (TDD), sonra fix uygulandı.

- A3-7: `_band_for` eşleşmezse (ör. RPM 800-1800 boşluğunda) sinyal artık
  sessizce atlanmaz; en yakın band seçilir (deterministik, sınırlar aritmetik
  orta noktadan) — "veri yok" ile "değerlendirilmedi" ayrımı kaybolmaz.
- A3-8: RPM bağlamı hiç yokken ilk band (1.0 bar) yerine band-bağımsız
  ispatlanabilir EN GENİŞ aralık (en düşük min / en yüksek max) kullanılır
  → yanlış bant / yanlış pozitif üretilmez; 2.5 bar artık "anomali" değil.
- A3-10: Levenshtein `<= 1` eşiği transpozisyon (Damerau) eşleşmesini de
  kabul ediyordu. Transpozisyon artık yalnız TAM (mesafe 0) eşleşmede geçerli;
  `<= 1` yolu klasik Levenshtein'e kısıtlandı. "misfire" ↔ "misfrie" eşleşmez.
- A3-13: `get_mode06_database` (ölü API) gerçek bir tüketim yoluna bağlandı:
  CAN ID'den OBD-II Mode $06 MID çıkarıp izleme testi/subsystem zenginliği
  üretir; KB'de olmayan CAN ID'lerde dürüst fallback korunur.
"""

from __future__ import annotations

from src.core.models.diagnostics import (
    DiagnosticDomain,
    SignalSample,
    SignalSource,
    VehicleSession,
)
from src.engine.ai.anomaly_detector import detect_anomalies, load_thresholds
from src.engine.ai.diagnostic_copilot import (
    AUTOMOTIVE_SEMANTIC_DICTIONARY,
    AutomotiveTokenizer,
    CausalBayesianInferenceEngine,
    get_mode06_database,
)


def _sample(name: str, value: float) -> SignalSample:
    return SignalSample(
        timestamp_ns=1_000_000_000,
        name=name,
        raw_value=int(value) if abs(value) < 2**31 else 0,
        physical_value=value,
        unit="",
        source=SignalSource.J1939,
        confidence=1.0,
    )


def _session(samples: list[SignalSample]) -> VehicleSession:
    s = VehicleSession(session_id="t58b", started_at_ns=1, domain=DiagnosticDomain.HEAVY_DUTY)
    s.samples.extend(samples)
    return s


# ────────────────────────────── A3-7 ──────────────────────────────


class TestA37BandGapNotSilentlySkipped:
    """RPM 800-1800 boşluğunda bant eşleşmez → en yakın band seçilmeli."""

    def test_rpm_in_gap_band_still_evaluated(self) -> None:
        thresholds = load_thresholds()
        # 900 RPM: hiçbir band min_rpm<=900<=max_rpm sağlamaz (0-800, 1800-2200).
        # ÖNCE (fix'siz): 0.4 bar düşük yağ basıncı TAMAMEN sessizce atlanırdı.
        samples = [_sample("EngineSpeed", 900.0)] + [_sample("EngineOilPressure", 0.4) for _ in range(5)]
        findings = detect_anomalies(_session(samples), thresholds)
        assert any(f.signal == "EngineOilPressure" for f in findings), (
            "900 RPM bant boşluğunda sinyal sessizce atlandı (A3-7)"
        )

    def test_gap_uses_nearest_band_deterministically(self) -> None:
        thresholds = load_thresholds()
        # 1400 RPM: (1400-800)=600 vs (1800-1400)=400 → en yakın band 1800-2200
        # (min 3.0 bar). 0.5 bar bu bandda anomalidir; rölanti bandı (min 1.0)
        # seçilseydi de anomalidir, ama 2.0 bar ile ayrım yapılabilir.
        samples = [_sample("EngineSpeed", 1400.0)] + [_sample("EngineOilPressure", 2.0) for _ in range(5)]
        findings = detect_anomalies(_session(samples), thresholds)
        # 2.0 bar rölanti bandında (min 1.0) NORMAL, 1800+ bandında (min 3.0) ANOMALİ.
        assert any(f.signal == "EngineOilPressure" for f in findings), (
            "1400 RPM'de en yakın band (1800-2200, min 3.0) seçilmedi (A3-7)"
        )

    def test_gap_selection_is_deterministic(self) -> None:
        thresholds = load_thresholds()
        samples = [_sample("EngineSpeed", 900.0)] + [_sample("EngineOilPressure", 0.4) for _ in range(5)]
        a = detect_anomalies(_session(list(samples)), thresholds)
        b = detect_anomalies(_session(list(samples)), thresholds)
        assert a == b


# ────────────────────────────── A3-8 ──────────────────────────────


class TestA38NoRpmUsesWidestRange:
    """RPM bağlamı yokken ilk band yerine band-bağımsız EN GENİŞ aralık."""

    def test_no_rpm_band_is_widest_not_first(self) -> None:
        # ÖNCE (fix'siz): `_band_for(ranges, None)` -> ranges[0] (ilk band)
        # dönerdi. RPM yokken hiçbir band RPM'e göre seçilemez → band-bağımsız,
        # İSPATLANABİLİR en geniş aralık kullanılmalı (en düşük min + en yüksek
        # max). Synthetic ranges: ilk band DAR, en geniş aralık sonda.
        from src.engine.ai.anomaly_detector import _band_for

        ranges = [
            {"min_rpm": 0, "max_rpm": 800, "min": 3.0, "max": 4.0},
            {"min_rpm": 1800, "max_rpm": 2200, "min": 2.0, "max": 5.0},
        ]
        band = _band_for(ranges, None)
        assert band is not None, "RPM yokken band None olmamalı (en geniş aralık)"
        assert band.get("min") == 2.0 and band.get("max") == 5.0, (
            f"RPM yokken ilk band yerine en geniş aralık seçilmeli (A3-8), bulunan: {band}"
        )

    def test_no_rpm_uses_widest_range_for_oil_pressure(self) -> None:
        # Gönderilen DB'de en geniş aralık = {min:1.0, max:None}.
        from src.engine.ai.anomaly_detector import _band_for

        thresholds = load_thresholds()
        ranges = thresholds["EngineOilPressure"]["ranges"]
        band = _band_for(ranges, None)
        assert band is not None
        assert band.get("min") == 1.0 and band.get("max") is None

    def test_no_rpm_widest_range_low_pressure_still_anomaly(self) -> None:
        thresholds = load_thresholds()
        # RPM yok. En geniş aralık = [min 1.0, max ∞).
        # 0.4 bar alt sınırın altında → gerçek anomali, yakalanmalı.
        samples = [_sample("EngineOilPressure", 0.4) for _ in range(5)]
        findings = detect_anomalies(_session(samples), thresholds)
        assert any(f.signal == "EngineOilPressure" for f in findings)

    def test_no_rpm_does_not_use_first_band_wrongly(self) -> None:
        # Band-bağımsız geniş aralık, kanıtsız "anomali" uydurmaz:
        # 2.5 bar min 1.0'ın üstünde → nominal kalır.
        thresholds = load_thresholds()
        samples = [_sample("EngineOilPressure", 2.5) for _ in range(5)]
        findings = detect_anomalies(_session(samples), thresholds)
        assert not any(f.signal == "EngineOilPressure" for f in findings), (
            "RPM yokken band-bağımsız en geniş aralık yerine ilk band kullanıldı (A3-8)"
        )

    def test_no_rpm_still_reports_documented_wide_band(self) -> None:
        # Negatif kontrol: geniş aralığın alt sınırı rapora yansımalı.
        thresholds = load_thresholds()
        samples = [_sample("EngineOilPressure", 0.5) for _ in range(5)]
        findings = detect_anomalies(_session(samples), thresholds)
        assert len(findings) == 1
        assert "min 1" in findings[0].finding


# ────────────────────────────── A3-10 ──────────────────────────────


class TestA310TranspositionNotTypoTolerant:
    """Transpozisyon tek başına domain eşleşmesi üretmemeli (A3-10)."""

    def test_transposition_does_not_match_domain(self) -> None:
        # "misfire" domain anahtar kelimesi; "misfrie" tam transpozisyon.
        assert "misfire" in AUTOMOTIVE_SEMANTIC_DICTIONARY["MISFIRE"], (
            "test varsayımı: 'misfire' MISFIRE sözlüğünde olmalı"
        )
        scores = AutomotiveTokenizer.extract_semantic_intents("misfrie")
        # ÖNCE (fix'siz): Damerau transpozisyonu mesafe 1 → 0.8 katkı → skor > 0.
        # ("misfrie" lemmatize sonrası "misfrie" olarak kalır — "e" soneki
        #  len(word)-len(suffix) >= 4 koşulunu sağlamaz.)
        assert scores.get("MISFIRE", 0.0) == 0.0, (
            "transpozisyon ('misfrie') domain eşleşmesi üretti (A3-10)"
        )

    def test_single_substitution_still_typo_tolerant(self) -> None:
        # Gerçek tek-harf yazım hatası (substitution, transpozisyon DEĞİL)
        # <= 1 klasik Levenshtein ile hâlâ tolere edilmeli (fix aşırı dar olmamalı).
        scores = AutomotiveTokenizer.extract_semantic_intents("misfirre")
        assert scores.get("MISFIRE", 0.0) > 0.0, (
            "klasik tek-harf yazım hatası artık tolere edilmiyor (aşırı sıkılaştırma)"
        )

    def test_exact_match_unaffected(self) -> None:
        scores = AutomotiveTokenizer.extract_semantic_intents("misfire")
        assert scores.get("MISFIRE", 0.0) > 0.0


# ────────────────────────────── A3-13 ──────────────────────────────


class TestA313Mode06Consumed:
    """get_mode06_database ölü API olmaktan çıkarıldı (A3-13)."""

    def test_mode06_database_loads_real_monitors(self) -> None:
        db = get_mode06_database()
        assert isinstance(db, dict)
        assert db.get("monitors"), "obd_mode06_database.json tüketilmedi"

    def test_mode06_mid_enriches_dtc_analysis(self) -> None:
        # SAE J1979 Mode $06: 0x46 = On-Board Monitoring Test Results, MID low
        # byte (0x01 = O2 Bank1 Sensor1). explain_can_frame_mode06 is the
        # tüketim yolu: CAN ID + payload'dan MID/TID çözer ve KB izleme testini
        # raporlar.
        frame_id = 0x18DA_46_01
        payload = b"\x01\x01\x00\x7F\x00\x00\x00\x00"
        text, matched = CausalBayesianInferenceEngine.explain_can_frame_mode06(frame_id, payload)
        # ÖNCE (fix'siz): böyle bir tüketim yolu YOK; explain_can_frame generic
        # döner ve MID'i hiç tanımaz → obd_mode06_database.json okunmaz.
        assert matched is True, "Mode $06 MID tüketim yolu yok — veritabanı ölü (A3-13)"
        assert "İzleme" in text or "mode 06" in text.lower() or "6" in text

    def test_mode06_consumed_via_explain_can_packet(self) -> None:
        """Production path: explain_can_packet SID 0x06 branch reads the DB."""
        from src.engine.ai.diagnostic_copilot import explain_can_packet

        # SF frame: [0x04, 0x06, MID=0x01, TID=0x01] on the OBD-II request ID.
        text, _actions = explain_can_packet("0x7E0", [0x04, 0x06, 0x01, 0x01])
        assert "Mode $06" in text
        assert "İzleme" in text, f"Mode $06 MID katalogdan çözülmedi: {text!r}"

    def test_mode06_unknown_mid_honest_fallback(self) -> None:
        from src.engine.ai.diagnostic_copilot import explain_can_packet

        text, _actions = explain_can_packet("0x7E0", [0x04, 0x06, 0xFA, 0x01])
        assert "Mode $06" in text
        assert "katalogda yok" in text, f"bilinmeyen MID için dürüst fallback yok: {text!r}"

    def test_mode06_format_unknown_mid_no_fabrication(self) -> None:
        from src.engine.ai.diagnostic_copilot import format_mode06_monitor

        out = format_mode06_monitor(0xFA, 0x01)
        assert "kayıtlı değil" in out

