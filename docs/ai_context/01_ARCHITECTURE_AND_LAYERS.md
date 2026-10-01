---
title: "AI Context: Architecture & Hexagonal Layers"
tags:
  - ai-context
  - architecture
  - hexagonal
  - core
  - hal
updated: 2026-09-01
---

# Architecture & Hexagonal Layers Context Card

Bu kart, AI ajanlarının projeye yeni bir bileşen eklerken veya var olan kodu refactor ederken **Hexagonal Mimari (Ports & Adapters)** ve bağımlılık sınırlarına %100 uymasını sağlamak için tasarlanmıştır.

## 1. Mimari Katmanlar ve İzolasyon Kuralları

```
[ Protocols (OBD/UDS/J1939) ] ---> [ Engine (Router/Decoders/Buffers) ]
              │                                      │
              ▼                                      ▼
    [ Safety Gateway (Choke Point) ]       [ Core Domain & Models ]
              │                                      ▲
              ▼                                      │
    [ HAL (Hardware Adapters / TxPort) ] ────────────┘
```

### Katman Sorumlulukları:
1. **`src/core/` (Core Domain - Kesinlikle Dış Bağımlılık Yok):**
   - `CanFrame`: Immutable (donmuş) CAN çerçevesi (`can_id`, `data`, `timestamp`, `is_extended`, `is_fd`, `dlc`).
   - `PlatformError` hiyerarşisi: Tüm özel exception'lar buradan türemelidir (`BusError`, `SafetyViolationError`, `ProtocolError`, vb.).
   - `TxPort`, `RxSubscription`, `ClockProvider`: Arayüz kontratları (Protocols / Abstract Base Classes).

2. **`src/hal/` (Hardware Abstraction Layer - Donanım Adaptörleri):**
   - `AbstractBus`: Taban sınıf. `send(frame)`, `recv(timeout)`, `shutdown()`.
   - `PythonCanBus`: `python-can` kütüphanesini sarmalar (SocketCAN, Vector, PEAK PCAN, Kvaser vb.).
   - `VirtualBus`: Testler ve simülasyonlar için bellek içi ring-buffer tabanlı bus.
   - `RP1210Client` / `RP1210Bus`: Ağır vasıta donanım adaptörleri (Nexiq, DLA vb. DLL sarmalayıcı).

3. **`src/safety/` (Güvenlik Katmanı - Asla Atlanamaz):**
   - `TxSafetyGateway`: Bus'a gidecek HER çerçevenin geçtiği 6 aşamalı dar boğaz (choke-point).
   - `EmergencyStopSystem`: E-Stop tetiklendiğinde bus iletimini kilitler. HMAC-SHA256 reset token gerektirir.
   - `E2ESafetyValidator` / `E2ESafetyPackager`: AUTOSAR E2E Profiles 1/2, CRC-8 (0x1D/0x2F) ve rolling counter denetimleri.

4. **`src/engine/` (İşleme ve Yönlendirme Motoru):**
   - `FrameRouter`: Çoklu abonelikli (pub/sub) non-blocking CAN çerçeve yönlendirici.
   - `DbcSignalDecoder`: LRU önbellekli DBC sinyal çözümleyici.
   - `ReassemblyPipeline`: J1939 TP ve ISO-TP parçalı mesajları toplayıp dekoderlere aktarır.
   - `BinaryRingBuffer` & `RollingDiskBuffer`: Yüksek hızlı telemetri kaydı (Zstandard sıkıştırma + HMAC bütünlük mührü).

5. **`src/protocols/` (Protokol Yığınları):**
   - `src/protocols/obd/`: SAE J1979 OBD-II Mode 01 PID veritabanı ve poller.
   - `src/protocols/uds/`: ISO 14229 UDS istemcisi, servisler ve ISO 15765-2 DoCAN (ISO-TP).
   - `src/protocols/j1939/`: J1939-21 Transport (BAM / RTS-CTS), J1939-73 Diagnostic (DM1..DM11), J1939-81 Address Claim ve OEM dekoderleri (`cummins`, `cat`, `scania`, `volvo`, `detroit`, `actros`).

## 2. Kodlama & Tasarım Kuralları (AI Prompt Guidelines)
- **Tip Güvenliği:** Python 3.12+ `typing` zorunludur (mypy strict CI'da koşar) (`Optional`, `Union`, `Callable`, `Protocol`, `dataclass(frozen=True)`).
- **Asla Doğrudan HAL Çağrısı Yapmayın:** Protokol veya UI katmanları doğrudan `hal.send()` yapamaz. Her zaman `TxPort` (yani `TxSafetyGateway`) üzerinden geçmelidir.
- **Monotonic Clock:** Zaman hesaplamalarında daima `time.monotonic()` veya enjekte edilen `ClockProvider` kullanılmalıdır (`time.time()` kullanılmaz).
