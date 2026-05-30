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
    """A contiguous run of registers read together.

    ``subblocks`` is discovered lazily: ``None`` means "try the whole block";
    once the gateway rejects it (unmapped register hole) we binary-split the
    range into the readable sub-ranges and cache them here.
    """

    __slots__ = ("slave", "input_type", "start", "end", "subblocks")

    def __init__(self, slave: int, input_type: str, start: int, end: int) -> None:
        self.slave = slave
        self.input_type = input_type
        self.start = start
        self.end = end  # exclusive
        self.subblocks: list[tuple[int, int]] | None = None

    @property
    def count(self) -> int:
        return self.end - self.start


def _plan_blocks(sensors: list[RegSensor]) -> list[_Block]:
    """Group sensor registers into block reads per (slave, input_type)."""
    groups: dict[tuple[int, str], list[RegSensor]] = {}
    for reg in sensors:
        groups.setdefault((reg.slave, reg.input_type), []).append(reg)

    blocks: list[_Block] = []
    for (slave, input_type), regs in groups.items():
        regs.sort(key=lambda r: r.address)
        cur_start = regs[0].address
        cur_end = regs[0].address + regs[0].count
        for reg in regs[1:]:
            r_end = reg.address + reg.count
            if (reg.address - cur_end) <= MAX_BLOCK_GAP and (
                r_end - cur_start
            ) <= MAX_BLOCK_REGISTERS:
                cur_end = max(cur_end, r_end)
            else:
                blocks.append(_Block(slave, input_type, cur_start, cur_end))
                cur_start, cur_end = reg.address, r_end
        blocks.append(_Block(slave, input_type, cur_start, cur_end))
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
        if block.subblocks is None:
            try:
                words = await self._client.read_registers(
                    block.slave, block.input_type, block.start, block.count
                )
                block.subblocks = [(block.start, block.count)]
                for i, word in enumerate(words):
                    reg_map[block.start + i] = word
                return
            except ModbusDeviceException:
                _LOGGER.info(
                    "Gateway rejected block (slave %d, %s %d+%d); binary-splitting "
                    "into readable sub-blocks",
                    block.slave, block.input_type, block.start, block.count,
                )
                block.subblocks = await self._probe_range(
                    block.slave, block.input_type, block.start, block.count
                )
            # ModbusError (transport) propagates for rediscovery handling.

        for start, count in block.subblocks:
            try:
                words = await self._client.read_registers(
                    block.slave, block.input_type, start, count
                )
                for i, word in enumerate(words):
                    reg_map[start + i] = word
            except ModbusDeviceException:
                continue  # genuinely unmapped on this hardware

    async def _probe_range(
        self, slave: int, input_type: str, start: int, count: int
    ) -> list[tuple[int, int]]:
        """Binary-split a rejected range into the sub-ranges the device accepts."""
        if count <= 0:
            return []
        try:
            await self._client.read_registers(slave, input_type, start, count)
            return [(start, count)]
        except ModbusDeviceException:
            if count == 1:
                return []  # single register unreadable -> drop it
            mid = count // 2
            left = await self._probe_range(slave, input_type, start, mid)
            right = await self._probe_range(
                slave, input_type, start + mid, count - mid
            )
            return left + right

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
        self._blocks = _plan_blocks(sensors)
        super().__init__(
            hass, _LOGGER, name=name, update_interval=timedelta(seconds=interval)
        )

    async def _async_update_data(self) -> dict[str, object]:
        cache = await self._conn.read_blocks(self._blocks)
        return self._decode_all(cache)

    def _decode_all(
        self, cache: dict[tuple[int, str], dict[int, int]]
    ) -> dict[str, object]:
        word_order = self._conn.word_order
        data: dict[str, object] = {}
        for reg in self.sensors:
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
