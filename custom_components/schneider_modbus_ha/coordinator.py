"""Polling for the Schneider Conext gateway.

A single :class:`SchneiderConnection` owns the one Modbus socket (reads are
serialized with a lock), the IP auto-rediscovery, and the adaptive block reads.
Two :class:`SchneiderCoordinator` instances share that connection: a small
"live" group polled fast and the main group polled slowly.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_CIDR,
    CONF_WORD_ORDER,
    DEFAULT_PORT,
    DEFAULT_WORD_ORDER,
    DOMAIN,
    MAX_BLOCK_GAP,
    MAX_BLOCK_REGISTERS,
)
from .devices import RegSensor
from .discovery import scan_for_gateways
from .modbus_api import ModbusDeviceException, ModbusError, SchneiderModbus, decode

_LOGGER = logging.getLogger(__name__)


class _Block:
    """A planned read for one (slave, input_type).

    ``whole`` is the optimistic single read spanning ``start``..``end`` (small
    gaps bridged). ``runs`` are the fallback reads: each is a *contiguous run of
    real sensor registers* — whole values back-to-back with no gap. Reading by
    runs never bridges an unmapped hole and never cuts a 32-bit register pair,
    both of which the Conext gateway rejects.
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
        cur_start = cur_end = regs[0].address + 0
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
    """Owns the single Modbus client, a read lock, and IP rediscovery."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._host = entry.data[CONF_HOST]
        self._port = entry.data.get(CONF_PORT, DEFAULT_PORT)
        self.word_order = entry.options.get(CONF_WORD_ORDER, DEFAULT_WORD_ORDER)
        self._cidr = entry.options.get(CONF_SCAN_CIDR) or None
        self._client = SchneiderModbus(self._host, self._port)
        self._lock = asyncio.Lock()
        self._rediscovering = False
        # Block identities (slave, itype, start, end) known to need the run-by-run
        # fallback, so we skip the doomed optimistic whole-block read next time.
        self._needs_runs: set[tuple[int, str, int, int]] = set()

    async def close(self) -> None:
        await self._client.close()

    async def read_blocks(
        self, blocks: list[_Block]
    ) -> dict[tuple[int, str], dict[int, int]]:
        """Read the given blocks, serialized against other callers."""
        async with self._lock:
            if not self._client.connected:
                try:
                    await self._client.connect()
                except (ModbusError, OSError) as err:
                    if not await self._try_rediscover():
                        raise UpdateFailed(str(err)) from err
            try:
                return await self._read_all(blocks)
            except (ModbusError, OSError) as err:
                await self._client.close()
                # Cheap path first: a transient blip usually just needs a fresh
                # socket to the same host. Only sweep the subnet if that fails
                # (i.e. the gateway's IP actually changed).
                try:
                    await self._client.connect()
                    return await self._read_all(blocks)
                except (ModbusError, OSError):
                    await self._client.close()
                if await self._try_rediscover():
                    try:
                        return await self._read_all(blocks)
                    except (ModbusError, OSError) as err2:
                        raise UpdateFailed(str(err2)) from err2
                raise UpdateFailed(str(err)) from err

    async def _read_all(
        self, blocks: list[_Block]
    ) -> dict[tuple[int, str], dict[int, int]]:
        cache: dict[tuple[int, str], dict[int, int]] = {}
        for block in blocks:
            reg_map = cache.setdefault((block.slave, block.input_type), {})
            await self._read_block(block, reg_map)
        return cache

    async def _read_block(self, block: _Block, reg_map: dict[int, int]) -> None:
        key = (block.slave, block.input_type, block.start, block.end)

        # Optimistic single read of the whole bridged span — but only if it
        # isn't already known to need the fallback, and only worth trying when
        # the span is a single gap-free run.
        if key not in self._needs_runs and len(block.runs) == 1:
            try:
                words = await self._client.read_registers(
                    block.slave, block.input_type, block.start, block.count
                )
                for i, word in enumerate(words):
                    reg_map[block.start + i] = word
                return
            except ModbusDeviceException:
                self._needs_runs.add(key)
                _LOGGER.info(
                    "Gateway rejected block (slave %d, %s %d+%d); reading by "
                    "contiguous runs instead",
                    block.slave, block.input_type, block.start, block.count,
                )
            # ModbusError (transport) propagates for rediscovery handling.
        elif key not in self._needs_runs and len(block.runs) > 1:
            self._needs_runs.add(key)  # has gaps; skip straight to runs

        # Fallback: read each gap-free, pair-aligned run. Skip a run only if the
        # device genuinely rejects it (register absent on this hardware).
        for start, count in block.runs:
            try:
                words = await self._client.read_registers(
                    block.slave, block.input_type, start, count
                )
                for i, word in enumerate(words):
                    reg_map[start + i] = word
            except ModbusDeviceException:
                continue

    async def _try_rediscover(self) -> bool:
        """Scan the subnet for the gateway and switch to its current IP."""
        if self._rediscovering:
            return False
        self._rediscovering = True
        try:
            _LOGGER.warning(
                "Lost connection to gateway at %s; scanning subnet to relocate it",
                self._host,
            )
            found = await scan_for_gateways(self.hass, self._port, self._cidr)
            if not found:
                _LOGGER.warning("Rediscovery scan found no Conext gateway")
                return False

            new_host = found[0]
            self._host = new_host
            self._client.host = new_host
            await self._client.connect()
            _LOGGER.warning("Gateway relocated to %s; reconnected", new_host)

            if new_host != self.entry.data.get(CONF_HOST):
                self.hass.config_entries.async_update_entry(
                    self.entry, data={**self.entry.data, CONF_HOST: new_host}
                )
            return True
        except (ModbusError, OSError) as err:
            _LOGGER.error("Rediscovery failed: %s", err)
            return False
        finally:
            self._rediscovering = False


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
