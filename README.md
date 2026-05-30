# Schneider Conext / Insight Home — Home Assistant integration

A custom integration that reads a Schneider Electric Conext gateway (Insight Home)
over Modbus TCP, with **automatic discovery of the gateway's IP** and
**transparent recovery when that IP changes**.

Replaces a hand-maintained `modbus:` + `template:` YAML setup with native,
UI-configured entities.

## Features

- Auto-discovers the gateway by scanning the local subnet and confirming each
  candidate with a Modbus signature read (no MAC list or mDNS needed).
- **Auto-detects connected devices** at setup by scanning each Conext device
  type's slave-ID range (from its base, stopping after a short gap — matches
  Schneider's contiguous assignment). Reads each device's **name** and **serial**
  from the common block, so a second MPPT, an SCP, etc. are found on their own.
- **You choose what to create**: setup shows every detected device (name + slave
  + type) to select from, lets you give a custom name, and lets you **add a
  device** that wasn't found. Entities are built only for your selection.
  Re-scan/re-select any time via the integration's **Reconfigure** option.
- If the connection drops (e.g. DHCP hands the gateway a new address), the
  integration rescans, relocates the gateway, and updates the host
  automatically — entities keep stable IDs and history.
- Polls efficiently: contiguous registers are batched into block reads, and a
  block the gateway rejects (unmapped register hole) self-heals by falling back
  to per-register reads.
- Native enum sensors for inverter status, charger mode, device state, fault /
  warning flags — replacing the old template-sensor YAML.
- One HA device per detected unit (Conext Gateway, XW Inverter, MPPT 60 #1/#2,
  …), with the sub-devices linked under the gateway.

## Requirements

- Home Assistant 2024.12 or newer (HA OS / Supervised / Container / Core).
- A Conext gateway reachable over Modbus TCP (default port **503**).

## Install (HACS)

1. HACS → Integrations → ⋮ → **Custom repositories** → add this repo, category
   *Integration*.
2. Install **Schneider Conext / Insight Home**, then restart Home Assistant.

Or copy `custom_components/schneider_modbus_ha/` into your HA `config/custom_components/`
and restart.

## Set up

**Settings → Devices & Services → Add Integration → Schneider Conext / Insight Home.**

- **Scan my network** — probes local subnets for a gateway. Optionally narrow
  it with a CIDR like `192.168.50.0/24`.
- **Enter the IP manually** — if you already know the address.

## Options (gear icon)

| Option | Notes |
| --- | --- |
| Polling interval | Seconds between reads (default 30). |
| 32-bit word order | `big` (default, matches a no-swap modbus YAML). Switch to `little` if 32-bit values (power/voltage) read wrong. |
| Rediscovery subnet | Optional CIDR to limit the rescan when relocating the gateway. |

## How rediscovery works

The integration stores a device id (the gateway MAC when resolvable) so entity
IDs never depend on the IP. On a connection failure it:

1. probes the configured subnet for hosts with the Modbus port open, then
2. reads gateway slave 1 / input register 65 to confirm identity, and
3. reconnects to the matching host, updating the stored address.

## Notes

- Modbus addresses, scales and offsets are ported directly from the original
  working YAML (port 503; slaves 1 = gateway, 10 = XW, 30/31 = MPPT).
- Register definitions live as per-device-type *templates* in `registers.py`;
  `devices.py` maps Conext slave-ID ranges to those templates and resolves them
  against the devices detected on your system.
- Supported device types: **Gateway, XW, MPPT, SCP, Conext SW, Battery
  Monitor** — each with a curated set of the useful sensors from its Modbus map.
