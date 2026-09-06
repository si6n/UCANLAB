"""Example 01: Listen-Only Sniffer (Passive Mode).

Connects to CAN bus in safe listen-only mode and prints incoming frames.
"""

from src.hal.drivers.pcan_kvaser import PythonCanBus
from src.safety.state_machine import SafetyState, SafetySupervisor


def main():
    print("=== Universal CAN Sniffer: Listen-Only Mode ===")
    supervisor = SafetySupervisor()
    supervisor.transition_to(SafetyState.SAFE, reason="Hardware stack ready")
    supervisor.enter_passive_mode("Example Listen-Only Sniffer")

    bus = PythonCanBus(interface="virtual", channel="vcan0", bitrate=250000, listen_only=True)
    bus.connect()
    print(f"Connected to {bus.channel_id} (Safety State: {supervisor.current_state.value})")

    try:
        print("Listening for incoming CAN frames...")
        for _ in range(5):
            frame = bus.recv(timeout_s=0.1)
            if frame:
                print(
                    f"[{frame.timestamp_ns / 1e9:.6f}] ID: 0x{frame.arbitration_id:08X} DLC: {frame.dlc} Data: {frame.data.hex(' ')}"
                )
    finally:
        bus.disconnect()
        print("Bus disconnected successfully.")


if __name__ == "__main__":
    main()
