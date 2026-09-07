"""Universal CAN-Bus Multi-Domain Live Traffic & Hardware-Free ECU Simulator.

Simulates real-world automotive, marine, electric vehicle (EV/BMS), and CAN-FD traffic:
1. Heavy Duty Commercial Fleet (SAE J1939): EEC1, EEC2, ET1, IC1, LFE, TCO1, DM1, EBC1, ERC1, ETC1
2. Electric Vehicle & High Voltage BMS: 400V Pack Voltage, Current, SOC%, Cell Deltas, Inverter
3. Marine Vessel (NMEA 2000): Twin Engines, Water Depth Sonar, GPS SOG/COG, Rudder, Wind Sensor
4. Next-Gen CAN-FD & ADAS: 64-Byte Radar Object Clustering, Camera Lane Tracking, Steering Angle CRC8
5. ISO 14229 UDS Mock Diagnostic Responder: Responds to 0x10, 0x22 (VIN/PIDs), 0x14, 0x19, 0x27 requests
"""

from __future__ import annotations

import argparse
import math
import sys
import time

from src.core.models.can_frame import CanFrame
from src.hal.drivers.pcan_kvaser import PythonCanBus
from src.safety.gateway import TxSafetyGateway

# Demo traffic may only target sandboxed buses — physical interfaces are refused (F-07).
ALLOWED_INTERFACES = frozenset({"virtual", "socketcan"})
ALLOWED_CHANNEL_PREFIXES = ("vcan", "slcan", "vxcan")


class UniversalTrafficSimulator:
    """Multi-domain CAN traffic generator and virtual ECU node."""

    def __init__(
        self,
        interface: str = "virtual",
        channel: str = "vcan0",
        bitrate: int = 250000,
        scenario: str = "nominal",
        hz: float = 50.0,
        enable_can_fd: bool = False,
    ) -> None:
        self.interface = interface
        self.channel = channel
        self.bitrate = bitrate
        self.scenario = scenario
        self.hz = hz
        self.enable_can_fd = enable_can_fd

        if interface not in ALLOWED_INTERFACES or not channel.startswith(ALLOWED_CHANNEL_PREFIXES):
            raise RuntimeError(
                f"Demo traffic generator refuses non-virtual bus '{interface}:{channel}'. "
                f"Use an interface in {sorted(ALLOWED_INTERFACES)} with a channel prefixed by "
                f"{ALLOWED_CHANNEL_PREFIXES} (e.g. vcan0)."
            )

        # listen_only=False is explicit: the simulator's whole purpose is to
        # broadcast synthetic traffic — but only onto sandbox virtual buses
        # (guarded by ALLOWED_INTERFACES above).
        self.bus = PythonCanBus(
            interface=self.interface, channel=self.channel, bitrate=self.bitrate, listen_only=False
        )
        # Whitelist bypass is reserved for the simulator's synthetic traffic
        # (dozens of dynamic IDs that no static whitelist can enumerate). The
        # bus is already hard-locked to virtual interfaces by the guard above,
        # and estop/supervisor/watchdog stages remain fully enforced.
        self.gateway = TxSafetyGateway.for_testing(bus=self.bus)
        # P0 (perf): all synthetic broadcasts go through the dedicated
        # 'simulation' token-bucket lane (500 burst / 250 msg/s refill).
        # The aggregate multi-ECU cadence is ~220 msg/s - the 100 msg/s
        # default sliding window rejected ~80% of frames, and the per-frame
        # backpressure logging plus RATE_ESTOP_AFTER escalation throttled
        # the simulator into an E-Stop instead of broadcasting.
        self.tx_budget_category = "simulation"
        self.rolling_counter = 0
        self.sim_time = 0.0

    def connect(self) -> None:
        self.bus.connect()
        print(f"[+] Universal CAN Simulator connected on {self.interface}:{self.channel} @ {self.bitrate} bps.")
        print(f"[*] Active Scenario: '{self.scenario.upper()}' | Broadcast Rate: {self.hz} Hz | CAN-FD: {self.enable_can_fd}")
        print("[*] Broadcasting multi-ECU streams. Press Ctrl+C to stop.\n")

    def disconnect(self) -> None:
        self.bus.disconnect()
        print("\n[!] Simulator disconnected cleanly.")

    def run(self) -> None:
        interval_s = 1.0 / max(1.0, self.hz)
        last_1000ms = 0.0
        last_100ms = 0.0
        last_20ms = 0.0

        try:
            while True:
                # docs/ai_context/01 §2: TX pacing math uses the monotonic
                # clock — wall-clock jumps (NTP sync, suspend/resume) must
                # never stall or burst the broadcast schedule.
                now = time.monotonic()
                t = self.sim_time
                self.sim_time += interval_s

                # ── 1. Fast Cyclic Telemetry (20ms) ──────────────────────────
                if now - last_20ms >= 0.02:
                    last_20ms = now
                    self._broadcast_20ms_frames(t)

                # ── 2. Medium Cyclic Telemetry (100ms) ────────────────────────
                if now - last_100ms >= 0.10:
                    last_100ms = now
                    self._broadcast_100ms_frames(t)

                # ── 3. Slow Cyclic & DTC Frames (1000ms) ──────────────────────
                if now - last_1000ms >= 1.00:
                    last_1000ms = now
                    self._broadcast_1000ms_frames(t)

                # Check for incoming UDS diagnostic requests (Mock Responder)
                self._handle_mock_uds_responder()

                time.sleep(interval_s)

        except KeyboardInterrupt:
            pass

    def _broadcast_20ms_frames(self, t: float) -> None:
        """Broadcast high-frequency engine, BMS, and transmission frames."""
        # 1. J1939 EEC1 Engine Speed & Torque (PGN 61444 - 0x0CF00400)
        rpm = 1800.0 + 800.0 * math.sin(t * 0.8)
        if self.scenario == "misfire" and math.sin(t * 3.0) > 0.4:
            rpm -= 320.0

        raw_rpm = int(rpm / 0.125) & 0xFFFF
        actual_torque_pct = 50 + int(30 * math.sin(t * 0.5))
        eec1_data = bytearray(8)
        eec1_data[0] = 0xF0
        eec1_data[1] = (125 + actual_torque_pct) & 0xFF
        eec1_data[2] = (125 + actual_torque_pct) & 0xFF
        eec1_data[3] = raw_rpm & 0xFF
        eec1_data[4] = (raw_rpm >> 8) & 0xFF
        eec1_data[5:8] = b"\xff\xff\xff"

        f_eec1 = CanFrame.create(
            channel_id=self.channel,
            arbitration_id=0x0CF00400,
            data=bytes(eec1_data),
            is_extended=True,
            direction="tx",
            source="virtual",
        )
        self.gateway.validate_and_transmit(f_eec1, budget_category=self.tx_budget_category)

        # 2. EV / BMS Pack Voltage & Current (0x1806E5F4)
        if self.scenario in ("bms", "ev", "nominal"):
            current_amp = 45.0 + 35.0 * math.sin(t * 1.5)
            pack_volt = 398.0 - (current_amp * 0.08)
            raw_volt = int(pack_volt * 10) & 0xFFFF
            raw_amp = int((current_amp + 500.0) * 10) & 0xFFFF
            bms_data = bytearray(8)
            bms_data[0] = (raw_volt >> 8) & 0xFF
            bms_data[1] = raw_volt & 0xFF
            bms_data[2] = (raw_amp >> 8) & 0xFF
            bms_data[3] = raw_amp & 0xFF

            f_bms = CanFrame.create(
                channel_id=self.channel,
                arbitration_id=0x1806E5F4,
                data=bytes(bms_data),
                is_extended=True,
                direction="tx",
            )
            self.gateway.validate_and_transmit(f_bms, budget_category=self.tx_budget_category)

        # 3. CAN-FD 64-Byte Radar Object Tracking (0x220)
        if self.enable_can_fd or self.scenario == "canfd":
            fd_payload = bytearray(64)
            # 8 Radar target objects (Distance, RelSpeed, Azimuth)
            for obj in range(8):
                dist = int(25.0 + obj * 8.0 + math.sin(t) * 2.0) & 0xFF
                fd_payload[obj * 8] = dist
                fd_payload[obj * 8 + 1] = 0x10  # Tracked
                fd_payload[obj * 8 + 2] = (self.rolling_counter + obj) & 0x0F
            f_radar = CanFrame.create(
                channel_id=self.channel,
                arbitration_id=0x00000220,
                data=bytes(fd_payload),
                is_extended=False,
                is_fd=True,
                direction="tx",
            )
            self.gateway.validate_and_transmit(f_radar, budget_category=self.tx_budget_category)

        # 4. Proprietary Discovery Frame with Rolling Counter and XOR Checksum
        prop_data = bytearray(8)
        prop_data[0] = 0xAA
        prop_data[1] = int(50 + 40 * math.sin(t)) & 0xFF
        prop_data[2] = (self.rolling_counter) & 0x0F
        self.rolling_counter += 1
        prop_data[7] = (sum(prop_data[:7]) ^ 0xAA) & 0xFF

        f_prop = CanFrame.create(
            channel_id=self.channel,
            arbitration_id=0x18FF0501,
            data=bytes(prop_data),
            is_extended=True,
            direction="tx",
            source="virtual",
        )
        self.gateway.validate_and_transmit(f_prop, budget_category=self.tx_budget_category)

    def _broadcast_100ms_frames(self, t: float) -> None:
        """Broadcast medium-frequency frames (N2K Marine, Speed, SOC%)."""
        # 1. NMEA 2000 Engine Rapid Update (PGN 127488 / 0x19F20000)
        boost_bar = 2.45 if self.scenario == "overboost" else 1.66 + 0.12 * math.sin(t * 0.5)
        raw_boost = int(boost_bar * 1000) & 0xFFFF
        rpm = 1800.0 + 800.0 * math.sin(t * 0.8)
        raw_rpm = int(rpm * 4) & 0xFFFF

        n2k_rapid = bytearray(8)
        n2k_rapid[0] = 0x00  # Port Engine
        n2k_rapid[1] = raw_rpm & 0xFF
        n2k_rapid[2] = (raw_rpm >> 8) & 0xFF
        n2k_rapid[3] = raw_boost & 0xFF
        n2k_rapid[4] = (raw_boost >> 8) & 0xFF

        f_n2k = CanFrame.create(
            channel_id=self.channel,
            arbitration_id=0x19F20000,
            data=bytes(n2k_rapid),
            is_extended=True,
            direction="tx",
            source="virtual",
        )
        self.gateway.validate_and_transmit(f_n2k, budget_category=self.tx_budget_category)

        # 2. NMEA 2000 Water Depth Sonar (PGN 128267 / 0x19F50300)
        depth_m = 24.5 + 6.0 * math.sin(t * 0.1)
        raw_depth = int(depth_m * 100) & 0xFFFFFFFF
        n2k_depth = bytearray(8)
        n2k_depth[0] = 0x01
        n2k_depth[1] = raw_depth & 0xFF
        n2k_depth[2] = (raw_depth >> 8) & 0xFF
        n2k_depth[3] = (raw_depth >> 16) & 0xFF
        n2k_depth[4] = (raw_depth >> 24) & 0xFF

        f_depth = CanFrame.create(
            channel_id=self.channel,
            arbitration_id=0x19F50300,
            data=bytes(n2k_depth),
            is_extended=True,
            direction="tx",
            source="virtual",
        )
        self.gateway.validate_and_transmit(f_depth, budget_category=self.tx_budget_category)

    def _broadcast_1000ms_frames(self, t: float) -> None:
        """Broadcast slow frames: Temperature (ET1), Hours, and Active DTCs (DM1)."""
        # 1. Engine Coolant Temperature (PGN 65249 / 0x18FEE100)
        coolant_temp = 109.5 if self.scenario == "overheat" else 85.0 + 3.0 * math.sin(t * 0.2)
        raw_temp = int(coolant_temp + 40.0) & 0xFF
        et1_data = bytearray([raw_temp, 0x96, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])

        f_et1 = CanFrame.create(
            channel_id=self.channel,
            arbitration_id=0x18FEE100,
            data=bytes(et1_data),
            is_extended=True,
            direction="tx",
            source="virtual",
        )
        self.gateway.validate_and_transmit(f_et1, budget_category=self.tx_budget_category)

        # 2. J1939 DM1 Diagnostic Fault Frame (PGN 65226 / 0x18FECA00)
        if self.scenario == "misfire":
            # Amber Lamp, SPN 651 (Cylinder 1 Misfire) / FMI 18
            dm1_data = b"\x04\xff\x8b\x02\x32\x0e\xff\xff"
        elif self.scenario == "overboost":
            # Amber Lamp, SPN 102 (Turbo Boost High) / FMI 0
            dm1_data = b"\x04\xff\x66\x00\x20\x07\xff\xff"
        elif self.scenario == "overheat":
            # Red+Amber Lamp, SPN 110 (Coolant Temp Critical) / FMI 0
            dm1_data = b"\x14\xff\x6e\x00\x20\x05\xff\xff"
        elif self.scenario == "fleet":
            # SPN 1087 (EBS Brake Air Pressure Low)
            dm1_data = b"\x14\xff\x3f\x04\x21\x03\xff\xff"
        else:
            # Nominal: No Active DTCs (00 FF FF FF FF FF FF FF)
            dm1_data = b"\x00\xff\xff\xff\xff\xff\xff\xff"

        f_dm1 = CanFrame.create(
            channel_id=self.channel,
            arbitration_id=0x18FECA00,
            data=dm1_data,
            is_extended=True,
            direction="tx",
            source="virtual",
        )
        self.gateway.validate_and_transmit(f_dm1, budget_category=self.tx_budget_category)

    def _handle_mock_uds_responder(self) -> None:
        """Inspect bus for diagnostic UDS requests (0x7E0) and emit positive responses (0x7E8)."""
        rx_frame = self.bus.recv(timeout_s=0.001)
        if rx_frame and rx_frame.arbitration_id in (0x7E0, 0x18DA00F1):
            data = rx_frame.data
            if len(data) >= 2:
                sid = data[1]
                # Positive UDS Response: SID + 0x40
                resp = bytearray(8)
                resp[0] = 0x03  # Single Frame 3 bytes
                resp[1] = (sid + 0x40) & 0xFF

                if sid == 0x10:  # DiagnosticSessionControl
                    resp[0] = 0x06
                    resp[2] = data[2] if len(data) > 2 else 0x01
                    resp[3:7] = b"\x00\x32\x01\xf4"  # P2 / P2* timings
                elif sid == 0x22:  # ReadDataByIdentifier
                    resp[0] = 0x07
                    did = ((data[2] << 8) | data[3]) if len(data) >= 4 else 0xF190
                    resp[2] = (did >> 8) & 0xFF
                    resp[3] = did & 0xFF
                    resp[4:8] = b"V130"  # Calibration ID / VIN
                elif sid == 0x14:  # ClearDiagnosticInformation
                    resp[0] = 0x01
                elif sid == 0x27:  # SecurityAccess (Return Seed)
                    resp[0] = 0x06
                    resp[2] = data[2] if len(data) > 2 else 0x01
                    resp[3:7] = b"\x12\x34\x56\x78"  # Seed

                f_uds_resp = CanFrame.create(
                    channel_id=self.channel,
                    arbitration_id=0x7E8,
                    data=bytes(resp),
                    is_extended=False,
                    direction="tx",
                    source="virtual",
                )
                self.gateway.validate_and_transmit(f_uds_resp, budget_category=self.tx_budget_category)


def main() -> int:
    parser = argparse.ArgumentParser(description="Universal CAN-Bus Multi-Domain ECU & Traffic Simulator")
    parser.add_argument(
        "--scenario",
        type=str,
        default="nominal",
        choices=["nominal", "misfire", "overboost", "overheat", "bms", "marine", "fleet", "canfd", "surge", "wiring"],
        help="Active simulation scenario profile",
    )
    parser.add_argument("--channel", type=str, default="vcan0", help="CAN Channel Name (default: vcan0)")
    parser.add_argument("--interface", type=str, default="virtual", help="python-can driver interface")
    parser.add_argument("--bitrate", type=int, default=250000, help="CAN Bitrate (default: 250000)")
    parser.add_argument("--hz", type=float, default=50.0, help="Broadcast rate frequency in Hz (default: 50.0)")
    parser.add_argument("--can-fd", action="store_true", help="Enable CAN-FD 64-byte payload transmission")

    args = parser.parse_args()

    sim = UniversalTrafficSimulator(
        interface=args.interface,
        channel=args.channel,
        bitrate=args.bitrate,
        scenario=args.scenario,
        hz=args.hz,
        enable_can_fd=args.can_fd,
    )
    sim.connect()
    sim.run()
    sim.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())

