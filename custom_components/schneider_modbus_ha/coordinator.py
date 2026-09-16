"""Polling for the Schneider Conext gateway.

Since HA 2026.9 the Modbus socket is owned and shared by Home Assistant's
``modbus`` integration: we ask it for one :class:`ModbusUnit` per slave over a
single pooled connection (``async_get_unit``). The connection auto-connects on
the first read, serializes concurrent reads, reconnects on the next read after
a drop, and never retries a request — so this module holds no socket, no lock,
and no reconnection logic of its own.

:class:`SchneiderConnection` groups sensor registers into block reads (with a
gap-free contiguous-run fallback for the Conext gateway, which rejects reads
that span an unmapped hole or cut a 32-bit register pair). Two
:class:`SchneiderCoordinator` instances share it: a small "live" group polled
fast and the main group polled slowly.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from homeassistant.components.modbus import async_get_unit
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from modbus_connection import ModbusError, ModbusExceptionError, ModbusTcpParams

from .const import (
    CONF_HOST,
    CONF_PORT,
    CONF_WORD_ORDER,
    DEFAULT_PORT,
    DEFAULT_WORD_ORDER,
    MAX_BLOCK_GAP,
    MAX_BLOCK_REGISTERS,
)
from .decode import decode
from .devices import RegSensor

if TYPE_CHECKING:
    from modbus_connection import ModbusUnit

_LOGGER = logging.getLogger(__name__)


class _Block:
    """A planned read for one (slave, input_type).

    ``runs`` are the fallback reads: each is a *contiguous run of real sensor
    registers* — whole values back-to-back with no gap. Reading by runs never
    bridges an unmapped hole and never cuts a 32-bit register pair, both of
    which the Conext gateway rejects.
    """

    __slots__ = ("slave", "input_type", "start", "end", "runs")

    def __init__(
        self,
        slave: int,
        input_type: str,
        start: int,
        end: int,
        runs: list[tuple[int, int]],
    ) -> None:
        self.slave = slave
        self.input_type = input_type
        self.start = start
        self.end = end  # exclusive
        self.runs = runs  # list of (start, count), gap-free and pair-aligned

    @property
    def count(self) -> int:
        return self.end - self.start


def _plan_blocks(sensors: list[RegSensor]) -> list[_Block]:
    """Group sensor registers into block reads per (slave, input_type).

    Each block carries both the bridged span (for one optimistic read) and the
    gap-free contiguous runs (the safe fallback).
    """
    groups: dict[tuple[int, str], list[RegSensor]] = {}
    for reg in sensors:
        groups.setdefault((reg.slave, reg.input_type), []).append(reg)

    blocks: list[_Block] = []
    for (slave, input_type), regs in groups.items():
        regs.sort(key=lambda r: r.address)
        cur_start = regs[0].address
        cur_end = regs[0].address + regs[0].count
        run_start = regs[0].address
        runs: list[tuple[int, int]] = []
        for reg in regs[1:]:
            r_end = reg.address + reg.count
            gap = reg.address - cur_end
            if gap <= MAX_BLOCK_GAP and (r_end - cur_start) <= MAX_BLOCK_REGISTERS:
                if gap > 0:  # mapped-but-unread or unmapped hole: split the run
                    runs.append((run_start, cur_end - run_start))
                    run_start = reg.address
                cur_end = r_end
            else:  # gap too big (or block too long): close this block
                runs.append((run_start, cur_end - run_start))
                blocks.append(_Block(slave, input_type, cur_start, cur_end, runs))
                cur_start = run_start = reg.address
                cur_end = r_end
                runs = []
        runs.append((run_start, cur_end - run_start))
        blocks.append(_Block(slave, input_type, cur_start, cur_end, runs))
    return blocks


class SchneiderConnection:
    """Holds one shared Modbus unit per slave and does the block reads.

    The units come from HA's ``modbus`` integration, which owns the socket and
    shares it across every consumer of the same endpoint.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.word_order = entry.options.get(CONF_WORD_ORDER, DEFAULT_WORD_ORDER)
        self._params = ModbusTcpParams(
            host=entry.data[CONF_HOST],
            port=entry.data.get(CONF_PORT, DEFAULT_PORT),
        )
        self._units: dict[int, ModbusUnit] = {}
        # Block identities (slave, itype, start, end) known to need the run-by-run
        # fallback, so we skip the doomed optimistic whole-block read next time.
        self._needs_runs: set[tuple[int, str, int, int]] = set()
        # Slaves we've already logged a device rejection for (log once, re-arm
        # on the next successful read of that slave).
        self._logged_exc: set[int] = set()

    def _unit(self, slave: int) -> ModbusUnit:
        unit = self._units.get(slave)
        if unit is None:
            # Asking for a unit performs no I/O; the shared connection connects
            # on the first read and is released when the entry unloads.
            unit = self._units[slave] = async_get_unit(
                self.hass, self.entry, self._params, slave
            )
        return unit

    async def close(self) -> None:
        # HA releases the shared units/connection when the entry unloads; we
        # only drop our references.
        self._units.clear()

    async def read_blocks(
        self, blocks: list[_Block]
    ) -> dict[tuple[int, str], dict[int, int]]:
        """Read the given blocks. A transport failure aborts the whole poll."""
        cache: dict[tuple[int, str], dict[int, int]] = {}
        try:
            for block in blocks:
                reg_map = cache.setdefault((block.slave, block.input_type), {})
                await self._read_block(block, reg_map)
        except ModbusError as err:
            # Device (illegal-address) exceptions are handled per-block below;
            # anything that reaches here is a transport/connection failure. Let
            # the coordinator mark the group unavailable — HA reconnects the
            # shared link on the next poll's first read (no retry from us).
            raise UpdateFailed(str(err)) from err
        return cache

    async def _read_block(self, block: _Block, reg_map: dict[int, int]) -> None:
        unit = self._unit(block.slave)
        reader = (
            unit.read_input_registers
            if block.input_type == "input"
            else unit.read_holding_registers
        )
        key = (block.slave, block.input_type, block.start, block.end)

        # Optimistic single read of the whole bridged span, but only when it is
        # a single gap-free run and not already known to need the fallback.
        if key not in self._needs_runs and len(block.runs) == 1:
            try:
                words = await reader(block.start, block.count)
                self._logged_exc.discard(block.slave)
                for i, word in enumerate(words):
                    reg_map[block.start + i] = word
                return
            except ModbusExceptionError as exc:
                self._needs_runs.add(key)
                self._note_device_exception(block, exc)
            # A transport error propagates for the poll to fail cleanly.
        elif key not in self._needs_runs and len(block.runs) > 1:
            self._needs_runs.add(key)  # has gaps; skip straight to runs

        # Fallback: read each gap-free, pair-aligned run. Skip a run only if the
        # device genuinely rejects it (register absent on this hardware).
        for start, count in block.runs:
            try:
                words = await reader(start, count)
                self._logged_exc.discard(block.slave)
                for i, word in enumerate(words):
                    reg_map[start + i] = word
            except ModbusExceptionError as exc:
                self._note_device_exception(block, exc)
                continue

    def _note_device_exception(
        self, block: _Block, exc: ModbusExceptionError
    ) -> None:
        """Log, once per slave, that the gateway rejected reads for a device.

        Turns a silently-skipped read into a plain reason a user can see in
        Settings → System → Logs, with the exact Modbus exception code.
        """
        if block.slave in self._logged_exc:
            return
        self._logged_exc.add(block.slave)
        code = getattr(exc, "exception_code", None)
        _LOGGER.warning(
            "Slave %d: the gateway answered but REJECTED the read with Modbus "
            "exception %s. Codes 10/11 (gateway path unavailable / target "
            "failed to respond) mean the gateway cannot reach this device on "
            "the Conext Xanbus bus — check it is powered and on the bus, and "
            "power-cycle the gateway to re-enumerate. Code 2 (illegal data "
            "address) means the register is not served by this device.",
            block.slave, code,
        )


class SchneiderCoordinator(DataUpdateCoordinator[dict[str, object]]):
    """Polls one group of sensors at one interval over a shared connection."""

    def __init__(
        self,
        hass: HomeAssistant,
        connection: SchneiderConnection,
        sensors: list[RegSensor],
        interval: int,
        name: str,
    ) -> None:
        self._conn = connection
        self.sensors = sensors
        super().__init__(
            hass,
            _LOGGER,
            name=name,
            update_interval=timedelta(seconds=interval),
            # Data is a plain dict (value-comparable), so skip entity callbacks
            # and state writes when a poll returns the same values as before.
            always_update=False,
        )

    async def _async_update_data(self) -> dict[str, object]:
        # Only poll registers whose entity is actually enabled/added. HA does
        # not add disabled entities, so their register never reaches a context
        # and costs no Modbus traffic.
        active: list[RegSensor] = list(self.async_contexts())
        if not active:
            return {}
        cache = await self._conn.read_blocks(_plan_blocks(active))
        return self._decode(active, cache)

    def _decode(
        self,
        sensors: list[RegSensor],
        cache: dict[tuple[int, str], dict[int, int]],
    ) -> dict[str, object]:
        word_order = self._conn.word_order
        data: dict[str, object] = {}
        for reg in sensors:
            reg_map = cache.get((reg.slave, reg.input_type), {})
            words = [reg_map.get(reg.address + i) for i in range(reg.count)]
            if any(w is None for w in words):
                data[reg.key] = None
                continue
            value: object = decode(words, reg.data_type, word_order)

            if reg.value_map is not None:
                data[reg.key] = reg.value_map.get(int(value), "N/A")
                continue

            num = value * reg.scale + reg.offset
            if reg.to_fahrenheit:
                num = num * 9.0 / 5.0 + 32.0
            if reg.precision is not None:
                num = round(num, reg.precision)
                if reg.precision == 0:
                    num = int(num)
            data[reg.key] = num
        return data
