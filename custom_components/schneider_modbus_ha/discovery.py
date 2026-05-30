"""Subnet scanning + Modbus identity confirmation for the Conext gateway."""

from __future__ import annotations

import asyncio
import ipaddress
import logging

from homeassistant.components import network
from homeassistant.core import HomeAssistant

from .const import (
    DEFAULT_TIMEOUT,
    PROBE_TIMEOUT,
    SCAN_CONCURRENCY,
    SCAN_CONNECT_TIMEOUT,
    SCAN_LOOKAHEAD,
    SIGNATURE_ADDRESS,
    SIGNATURE_INPUT_TYPE,
    SIGNATURE_SLAVE,
)
from .devices import DEVICE_TYPES, DeviceType, StrSpec
from .modbus_api import ModbusError, SchneiderModbus, decode_string

_LOGGER = logging.getLogger(__name__)


async def _local_candidate_ips(hass: HomeAssistant) -> list[str]:
    """Enumerate host IPs from HA's enabled IPv4 adapters (excluding /large nets)."""
    hosts: list[str] = []
    adapters = await network.async_get_adapters(hass)
    for adapter in adapters:
        if not adapter["enabled"]:
            continue
        for ip_info in adapter["ipv4"]:
            try:
                net = ipaddress.ip_network(
                    f"{ip_info['address']}/{ip_info['network_prefix']}",
                    strict=False,
                )
            except ValueError:
                continue
            if net.is_loopback or net.num_addresses > 1024:
                # Skip loopback and anything bigger than a /22 to keep scans sane.
                continue
            hosts.extend(str(h) for h in net.hosts())
    # De-dupe while preserving order.
    seen: set[str] = set()
    return [h for h in hosts if not (h in seen or seen.add(h))]


def candidate_ips_from_cidr(cidr: str) -> list[str]:
    """Expand an explicit CIDR (e.g. ``192.168.50.0/24``) into host IPs."""
    net = ipaddress.ip_network(cidr, strict=False)
    return [str(h) for h in net.hosts()]


async def _port_open(host: str, port: int) -> bool:
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout=SCAN_CONNECT_TIMEOUT)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return True
    except (asyncio.TimeoutError, OSError):
        return False


async def _is_gateway(host: str, port: int) -> bool:
    """Confirm a host is the Conext gateway via the signature register read."""
    client = SchneiderModbus(host, port, timeout=DEFAULT_TIMEOUT)
    try:
        await client.connect()
        await client.read_registers(
            SIGNATURE_SLAVE, SIGNATURE_INPUT_TYPE, SIGNATURE_ADDRESS, 1
        )
        return True
    except (ModbusError, OSError, asyncio.TimeoutError):
        return False
    finally:
        await client.close()


async def scan_for_gateways(
    hass: HomeAssistant, port: int, cidr: str | None = None
) -> list[str]:
    """Scan the subnet and return IPs that respond as a Conext gateway.

    Two passes: a fast concurrent TCP port probe, then a Modbus signature read
    on the hosts that had the port open.
    """
    if cidr:
        candidates = candidate_ips_from_cidr(cidr)
    else:
        candidates = await _local_candidate_ips(hass)

    _LOGGER.debug("Probing %d candidate hosts on port %d", len(candidates), port)

    sem = asyncio.Semaphore(SCAN_CONCURRENCY)

    async def probe(host: str) -> str | None:
        async with sem:
            return host if await _port_open(host, port) else None

    open_hosts = [
        h for h in await asyncio.gather(*(probe(c) for c in candidates)) if h
    ]
    _LOGGER.debug("Hosts with port %d open: %s", port, open_hosts)

    found: list[str] = []
    for host in open_hosts:
        if await _is_gateway(host, port):
            found.append(host)
    return found


async def _read_string(
    client: SchneiderModbus, slave: int, spec: StrSpec
) -> str:
    """Best-effort read of a common-block string (name/serial)."""
    try:
        words = await client.read_registers(
            slave, spec.input_type, spec.address, spec.length
        )
        return decode_string(words)
    except (ModbusError, OSError, asyncio.TimeoutError):
        return ""


async def _scan_range(client: SchneiderModbus, dtype: DeviceType) -> list[dict]:
    """Scan one device-type's slave range; stop after a run of empty ids.

    The gateway pre-allocates each type's id range and answers reads for unused
    ids with zeros, so an exception-based probe sees them all as "present".
    A real device, instead, reports a non-empty Device Name — that's the
    presence test. Empty name = unused slot.
    """
    found: list[dict] = []
    absent = 0
    for slave in range(dtype.range_start, dtype.range_end + 1):
        name = await _read_string(client, slave, dtype.name_spec)
        if name:
            serial = await _read_string(client, slave, dtype.serial_spec)
            _LOGGER.debug("Found %s at slave %d: name=%r serial=%r",
                          dtype.key, slave, name, serial)
            found.append(
                {"type": dtype.key, "slave": slave, "name": name, "serial": serial}
            )
            absent = 0
        else:
            absent += 1
            if absent >= SCAN_LOOKAHEAD:
                break
    return found


async def detect_devices(host: str, port: int) -> list[dict]:
    """Probe the Conext slave-id ranges and return the devices found.

    Returns a list of ``{"type", "slave", "name", "serial"}``.
    """
    client = SchneiderModbus(host, port, timeout=PROBE_TIMEOUT)
    detected: list[dict] = []
    try:
        await client.connect()
        for dtype in DEVICE_TYPES:
            detected.extend(await _scan_range(client, dtype))
    finally:
        await client.close()
    return detected
