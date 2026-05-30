"""Async Modbus TCP helper for the Schneider Conext gateway.

Wraps pymodbus with a version-tolerant read call and does its own integer
decoding via ``struct`` so we don't depend on pymodbus' decoder API, which has
changed repeatedly across 3.x releases.
"""

from __future__ import annotations

import asyncio
import logging

from pymodbus.client import AsyncModbusTcpClient

try:  # location/name has been stable, but guard against pymodbus churn
    from pymodbus.pdu import ExceptionResponse
except Exception:  # noqa: BLE001
    ExceptionResponse = None  # type: ignore[assignment]

try:
    from pymodbus.exceptions import ConnectionException, ModbusIOException
except Exception:  # noqa: BLE001
    ConnectionException = ModbusIOException = ()  # type: ignore[assignment]

from .const import DEFAULT_TIMEOUT

_LOGGER = logging.getLogger(__name__)

_TRANSPORT_ERRORS = tuple(
    e for e in (ConnectionException, ModbusIOException, asyncio.TimeoutError, OSError)
    if isinstance(e, type)
)


class ModbusError(Exception):
    """Raised when a Modbus operation fails (transport/connection level)."""


class ModbusDeviceException(ModbusError):
    """The gateway responded but rejected the request (e.g. illegal address).

    Distinct from a transport failure: the device is reachable, so this should
    trigger a per-register fallback rather than an IP rediscovery.

    ``exception_code`` carries the Modbus exception code so callers can tell an
    illegal data address (0x02 — device exists, wrong register) from a gateway
    "target failed to respond" (0x0B — device not present).
    """

    def __init__(self, message: str, exception_code: int | None = None) -> None:
        super().__init__(message)
        self.exception_code = exception_code


class SchneiderModbus:
    """Thin async wrapper around a single pymodbus TCP client."""

    def __init__(self, host: str, port: int, timeout: int = DEFAULT_TIMEOUT) -> None:
        self._host = host
        self._port = port
        self._timeout = timeout
        self._client: AsyncModbusTcpClient | None = None

    @property
    def host(self) -> str:
        return self._host

    @host.setter
    def host(self, value: str) -> None:
        self._host = value

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.connected

    async def connect(self) -> None:
        """(Re)connect to the gateway."""
        await self.close()
        self._client = AsyncModbusTcpClient(
            self._host, port=self._port, timeout=self._timeout
        )
        await self._client.connect()
        if not self._client.connected:
            raise ModbusError(f"Could not connect to {self._host}:{self._port}")

    async def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001 - close must never raise
                pass
            self._client = None

    async def read_registers(
        self, slave: int, input_type: str, address: int, count: int
    ) -> list[int]:
        """Read ``count`` registers and return the raw 16-bit words."""
        if self._client is None or not self._client.connected:
            raise ModbusError("Not connected")

        reader = (
            self._client.read_input_registers
            if input_type == "input"
            else self._client.read_holding_registers
        )

        # pymodbus renamed the unit kwarg over time (unit -> slave -> device_id).
        # Try the current names in order; fall back on TypeError.
        result = None
        try:
            for unit_kw in ("slave", "device_id", "unit"):
                try:
                    result = await reader(address, count=count, **{unit_kw: slave})
                    break
                except TypeError:
                    continue
        except _TRANSPORT_ERRORS as err:
            raise ModbusError(
                f"Transport error reading {input_type} {address}+{count} "
                f"on slave {slave}: {err}"
            ) from err

        if result is None:
            raise ModbusError("Incompatible pymodbus signature for read call")

        if result.isError():
            if ExceptionResponse is not None and isinstance(result, ExceptionResponse):
                raise ModbusDeviceException(
                    f"Device rejected read of {input_type} {address}+{count} "
                    f"on slave {slave}: {result}",
                    exception_code=getattr(result, "exception_code", None),
                )
            raise ModbusError(
                f"Transport error reading {input_type} {address}+{count} "
                f"on slave {slave}: {result}"
            )
        return list(result.registers)


def decode(words: list[int], data_type: str, word_order: str) -> int:
    """Decode raw 16-bit words into a signed/unsigned integer."""
    if data_type == "uint16":
        return words[0]
    if data_type == "int16":
        v = words[0]
        return v - 0x10000 if v >= 0x8000 else v

    hi, lo = words[0], words[1]
    if word_order == "little":
        hi, lo = lo, hi
    raw = (hi << 16) | lo
    if data_type == "int32" and raw >= 0x80000000:
        raw -= 0x100000000
    return raw


def decode_string(words: list[int]) -> str:
    """Decode a Conext packed string: two 8-bit chars per register, hi byte first."""
    chars = bytearray()
    for word in words:
        chars.append((word >> 8) & 0xFF)
        chars.append(word & 0xFF)
    # Strings are NUL-padded; cut at the first NUL and drop non-printables.
    text = bytes(chars).split(b"\x00", 1)[0]
    return text.decode("ascii", "ignore").strip()
