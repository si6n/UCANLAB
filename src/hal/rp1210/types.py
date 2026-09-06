"""TMC RP1210 (A/B/C) Standard Return Codes and Data Types."""

from __future__ import annotations

from enum import IntEnum


class RP1210ErrorCode(IntEnum):
    """TMC RP1210 standard error codes."""

    NO_ERRORS = 0
    ERR_DLL_NOT_FOUND = 128
    ERR_INVALID_CLIENT_ID = 129
    ERR_CLIENT_ALREADY_CONNECTED = 130
    ERR_CLIENT_AREA_FULL = 131
    ERR_FREE_MEMORY = 132
    ERR_NOT_ENOUGH_MEMORY = 133
    ERR_TX_QUEUE_FULL = 134
    ERR_TX_QUEUE_CORRUPT = 135
    ERR_RX_QUEUE_FULL = 136
    ERR_RX_QUEUE_CORRUPT = 137
    ERR_DEVICE_IN_USE = 138
    ERR_INVALID_DEVICE = 139
    ERR_DEVICE_NOT_SUPPORTED = 140
    ERR_INVALID_PROTOCOL = 141
    ERR_HARDWARE_NOT_RESPONDING = 142
    ERR_COMMAND_TIMED_OUT = 143
    ERR_HARDWARE_STATUS_CHANGE = 144
    ERR_BUS_OFF = 145
    ERR_COM_DEVICE_NOT_FOUND = 146
    ERR_INVALID_COMMAND = 147
    ERR_TX_MSG_SPECIAL = 148
    ERR_BLOCKING_NOT_ALLOWED = 149
    ERR_MAX_NOTIFY_EXCEEDED = 150
    ERR_MAX_INSTANCES_EXCEEDED = 151
    ERR_INVALID_CONFIG = 152
    ERR_CHANGE_MODE_FAILED = 153
    ERR_INIFILE_NOT_FOUND = 154
    ERR_ADDRESS_LOST = 155
    ERR_CODE_NOT_FOUND = 156
    ERR_BLOCK_NOT_ALLOWED = 157
    ERR_MULTIPLE_CLIENTS_CONNECTED = 158
    ERR_BUS_OFF_PASSIVE = 159
    ERR_SHUTDOWN = 160
    ERR_PROCESS_NOT_FOUND = 161
    ERR_CANNOT_ACCESS_PORT = 162

    # Standard RP1210C aliases
    ERR_DLL_NOT_INITIALIZED = 128
    ERR_COMM_DEVICE_NOT_FOUND = 146
    ERR_COMM_DEVICE_NOT_RESPONDING = 142
    ERR_ADDRESS_NEVER_CLAIMED = 155

    @classmethod
    def get_description(cls, code: int) -> str:
        """Return human-readable English description for RP1210 error code."""
        try:
            return cls(code).name.replace("ERR_", "").replace("_", " ").title()
        except ValueError:
            return f"Unknown RP1210 Error ({code})"
