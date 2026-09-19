# UCanLab v13.0 — Modern Dark Design Language & Telemetry Console

This frontend is the primary operator console for **Universal CAN-Bus Diagnostic & Telemetry Tool v13.0**, built with **React 18 + TypeScript + Tailwind CSS** following a strict **Design-Language Transfer**:
- **Structure, Information Architecture, Telemetry Model & Instruments:** 100% Reference A (**UCanLab v13.0** automotive CAN console).
- **Visual Skin & Styling:** Reference B (**Calm Dark Developer Workspace**) — micro-tonal near-black surfaces (`#0b0b0f`, `#0c0c11`, `#0e0e13`), whisper hairlines (`rgba(255,255,255,0.07)`), single interactive indigo accent (`#818cf8`), disciplined low-alpha semantic badges, and zero chat/transcript bloat.

---

## 1. Quick Start / Running

### Prerequisites
- Node.js 18+ or 20+
- npm 9+

### Installation & Development
```bash
# Navigate to the frontend directory
cd src/ui/frontend

# Install dependencies
npm install

# Run the Vite development server (hot reload)
npm run dev
```

### Production Build & Bundling
```bash
# Typecheck with tsc and bundle with Vite
npm run build

# Preview production build locally
npm run preview
```

The production output compiles directly to `src/ui/frontend/dist/`, which is automatically discovered and embedded by the native Windows pywebview launcher (`src/ui/desktop_app.py`).

---

## 2. Layout & Architectural Hierarchy

```
┌────────────────────────────────────────────────────────────────────────────────────────────────┐
│ [0] Row A: WindowChrome (h-9) — Brand Glyph + "Universal CAN Bus Diagnostic Tool v13.0" + Win  │
├────────────────────────────────────────────────────────────────────────────────────────────────┤
│ [0] Row B: Toolbar (h-11) — vcan0 Connection Chip + Yük/Paket Telemetry + E-STOP + Başlat + Hata│
├──────────────┬─────────────────────────────────────────────────────────────────────────────────┤
│              │ [Panel #1] Sniffer Table (h-55%)                                                │
│              │   - Controls: Hataları Süz (5) · Search · Devam/Durdur · Temizle · Info         │
│  SideRail    │   - Columns: Zaman | Kanal | CAN ID | Tür | Yön | DLC | Veri (Hex) | ASCII       │
│   (w-48)     │   - High-density per-byte colored hex payload (calm syntax cycle, no chips)     │
│              │   - Anomaly row tint (rgba(251,191,36,0.09)) + warn triangle                    │
│   TEŞHİS     │   - Summary Strip: Toplam Gösterilen · Arıza Kareleri · Bus Errors              │
│   ARAÇLAR    ├─────────────────────────────────────────────────────────────────────────────────┤
│   SİSTEM     │ [Panel #2] Scope & Signal Analysis (h-45%)                                      │
│              │   - Header: Live Legend Chips (HV, SOC, Akım) + Osiloskop/Isı Haritası Switch   │
│              │   - Alert Banner: "RPM Ani Düşüşü: -575 RPM (Tekleme Çentiği!)" + AI Analiz     │
│              │   - 60fps Canvas: Waveforms (Solid Blue + Dashed Green) + Red Dip Event Marker  │
│              │   - Interactive hover crosshairs + mono floating readout chip                   │
├──────────────┴─────────────────────────────────────────────────────────────────────────────────┤
│ [3] StatusBar (h-7) — Operational State (Çalışıyor/Durduruldu) + Watchdog Leases + vcan0 Node  │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Visual Token Palette (Reference B)

| Role | Token | Value / Treatment |
|---|---|---|
| **App Base** | `--bg-app` | `#0b0b0f` (near-black window base) |
| **Rail** | `--bg-rail` | `#0e0e13` (grouped left navigation) |
| **Elevated Chrome** | `--bg-chrome` | `#0c0c11` (titlebar, toolbar, headers, statusbar) |
| **Panel Surface** | `--bg-panel` | `#0e0e13` (sniffer & oscilloscope bodies) |
| **Whisper Border** | `--border` | `rgba(255, 255, 255, 0.07)` (hairline separation) |
| **Active Accent** | `--accent` | `#818cf8` (single interactive indigo) |
| **Selected Row** | `--bg-row-selected` | `rgba(129, 140, 248, 0.10)` + 2px left accent bar |
| **Anomaly Row** | `--bg-row-anomaly` | `rgba(251, 191, 36, 0.09)` + warn triangle on ID |
| **Emergency Red** | `--danger` | `#f87171` (Solid E-STOP, low-alpha alert banner) |
| **Start Green** | `--ok` | `#34d399` (Solid Başlat button, active state dot) |
| **Warning Amber** | `--warn` | `#fbbf24` (Hata outline, anomaly counter) |
| **Direction Teal** | `--info-teal` | `#2dd4bf` (RX / TX tags) |

---

## 4. Key Keyboard Shortcuts & Interactions
- `Space`: Toggle live packet streaming on / off (when not focused on search input).
- `Escape`: Deselect active row and reset search/filter state.
- `Hataları Süz`: Dims non-anomaly rows (35% opacity + grayscale) preserving spatial context.
- `Right-Click` on table row: Opens context popover to copy raw hex/ASCII or isolate by CAN ID.
- `Vertical Divider`: Drag the subtle handle between Sniffer and Scope to customize viewport heights.
