"""Conext device *types*, slave-ID ranges, and the sensor builder.

The gateway exposes each Xanbus device as a Modbus slave ID, auto-assigned
contiguously from a fixed base per device type (see the SE Conext Modbus map).
We scan each type's range to detect what's connected, read the device's name and
serial from the common block, and apply the matching register template.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .registers import (
    BATTMON_REGS,
    GATEWAY_REGS,
    MPPT_REGS,
    SCP_REGS,
    SW_REGS,
    XW_REGS,
    RegTemplate,
)


@dataclass(frozen=True)
class StrSpec:
    """Where a common-block string lives (Device Name / Hardware Serial)."""

    address: int
    input_type: str   # "input" | "holding"
    length: int       # number of 16-bit registers to read


@dataclass(frozen=True)
class DeviceType:
    """A kind of Conext device: its slave-ID range, identity regs, and map."""

    key: str
    name: str
    model: str
    range_start: int          # inclusive slave id
    range_end: int            # inclusive slave id
    name_spec: StrSpec
    serial_spec: StrSpec
    registers: list[RegTemplate] = field(default_factory=list)


# Slave-ID ranges per Schneider's Conext Gateway Modbus address map.
# Device Name string is at address 0 (holding on XW+, input elsewhere); the
# Hardware Serial Number is at address 43 (input). Lengths differ per device.
DEVICE_TYPES: list[DeviceType] = [
    DeviceType("gateway", "Conext Gateway", "Conext Gateway", 1, 9,
               StrSpec(0, "input", 8), StrSpec(43, "input", 8), GATEWAY_REGS),
    DeviceType("xw", "XW Inverter", "Conext XW+", 10, 29,
               StrSpec(0, "holding", 8), StrSpec(43, "input", 10), XW_REGS),
    DeviceType("mppt", "MPPT 60 Charge Controller", "Conext MPPT 60 150",
               30, 49, StrSpec(0, "input", 8), StrSpec(43, "input", 10),
               MPPT_REGS),
    DeviceType("scp", "System Control Panel", "Conext SCP", 70, 89,
               StrSpec(0, "input", 10), StrSpec(43, "input", 10), SCP_REGS),
    DeviceType("sw", "Conext SW Inverter", "Conext SW", 90, 109,
               StrSpec(0, "input", 8), StrSpec(43, "input", 10), SW_REGS),
    DeviceType("battmon", "Battery Monitor", "Conext Battery Monitor",
               190, 209, StrSpec(0, "input", 8), StrSpec(43, "input", 10),
               BATTMON_REGS),
]

DEVICE_TYPES_BY_KEY: dict[str, DeviceType] = {d.key: d for d in DEVICE_TYPES}

GATEWAY_INSTANCE = "gateway"  # the gateway is always the bus master at slave 1


def infer_type(slave: int) -> str | None:
    """Map a slave id to a device-type key by Schneider's range scheme."""
    for dtype in DEVICE_TYPES:
        if dtype.range_start <= slave <= dtype.range_end:
            return dtype.key
    return None


@dataclass(frozen=True)
class RegSensor:
    """A register template resolved to a concrete device instance + slave."""

    key: str
    name: str
    device_instance: str
    device_label: str
    device_model: str
    device_serial: str | None
    slave: int
    address: int
    data_type: str
    input_type: str
    scale: float
    offset: float
    precision: int | None
    unit: str | None
    device_class: str | None
    state_class: str | None
    value_map: dict[int, str] | None
    to_fahrenheit: bool
    enabled_default: bool
    fast: bool          # True → belongs to the live (fast) polling group

    @property
    def count(self) -> int:
        return 2 if self.data_type in ("uint32", "int32") else 1


def build_sensors(detected: list[dict]) -> list[RegSensor]:
    """Resolve detected devices into concrete sensors.

    ``detected`` items: ``{"type", "slave", "name"?, "serial"?}``.

    The lowest-numbered slave of each type is the "primary" instance: it keeps
    the bare template key / device id so its entities match earlier versions.
    Additional instances get a ``_<slave>`` suffix and a labelled device. A
    user-supplied ``name`` overrides the generated device label.
    """
    by_type: dict[str, list[dict]] = {}
    for dev in detected:
        by_type.setdefault(dev["type"], []).append(dev)
    for items in by_type.values():
        items.sort(key=lambda d: int(d["slave"]))

    sensors: list[RegSensor] = []
    for type_key, items in by_type.items():
        dtype = DEVICE_TYPES_BY_KEY.get(type_key)
        if dtype is None or not dtype.registers:
            continue  # detected but no register map -> no entities (yet)
        multiple = len(items) > 1
        for index, dev in enumerate(items):
            slave = int(dev["slave"])
            primary = index == 0
            instance = dtype.key if primary else f"{dtype.key}_{slave}"
            suffix = "" if primary else f"_{slave}"

            # A user-supplied custom name wins as-is; otherwise use the device's
            # self-reported name (or the type name), disambiguated by slave when
            # several of the same type exist.
            custom = (dev.get("custom_name") or "").strip()
            detected = (dev.get("name") or "").strip()
            if custom:
                label = custom
            else:
                base = detected or dtype.name
                label = f"{base} ({slave})" if multiple else base

            serial = dev.get("serial") or None

            for tmpl in dtype.registers:
                sensors.append(
                    RegSensor(
                        key=f"{tmpl.key}{suffix}",
                        name=tmpl.name,
                        device_instance=instance,
                        device_label=label,
                        device_model=dtype.model,
                        device_serial=serial,
                        slave=slave,
                        address=tmpl.address,
                        data_type=tmpl.data_type,
                        input_type=tmpl.input_type,
                        scale=tmpl.scale,
                        offset=tmpl.offset,
                        precision=tmpl.precision,
                        unit=tmpl.unit,
                        device_class=tmpl.device_class,
                        state_class=tmpl.state_class,
                        value_map=tmpl.value_map,
                        to_fahrenheit=tmpl.to_fahrenheit,
                        enabled_default=tmpl.enabled_default,
                        fast=tmpl.fast,
                    )
                )
    return sensors


# Device set assumed for entries created before auto-detection existed.
LEGACY_DEVICES: list[dict] = [
    {"type": "gateway", "slave": 1},
    {"type": "xw", "slave": 10},
    {"type": "mppt", "slave": 30},
]
