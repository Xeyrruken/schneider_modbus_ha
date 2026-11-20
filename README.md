# Schneider Conext / Insight Home — Home Assistant integration

A custom integration that reads a Schneider Electric Conext gateway (Insight Home)
over Modbus TCP and turns your inverter, charge controllers and other Conext
devices into native, UI-configured Home Assistant entities.

Replaces a hand-maintained `modbus:` + `template:` YAML setup.

> **Alpha software.** It works on the author's own system (gateway, XW+, two
> MPPT 60s, SCP), but other setups are less tested. Please open an issue if
> something reads wrong.

## Features

- **Finds the gateway for you**: scans the local network and confirms each
  candidate with a Modbus signature read. You can also enter the IP manually.
- **Auto-detects connected devices** at setup by scanning each Conext device
  type's slave-ID range. Reads each device's **name** and **serial**, so a
  second MPPT, an SCP, etc. are found on their own.
- **You choose what to create**: setup lists every detected device (name, slave
  ID and type) to select from, lets you give a custom name, and lets you **add a
  device** that wasn't found. Entities are created only for your selection.
  Re-scan or re-select any time via the integration's **Reconfigure** option.
- **Two polling speeds**: a small group of "live" sensors (e.g. power) refreshes
  every couple of seconds, everything else on a slower interval. Only enabled
  entities are polled.
- **Efficient reads**: neighbouring registers are batched into block reads, with
  an automatic fallback when the gateway rejects a block.
- Native enum sensors for inverter status, charger mode, device state, fault /
  warning flags.
- One HA device per unit (Conext Gateway, XW Inverter, MPPT 60 #1/#2, …), linked
  under the gateway.
- Supported device types: **Gateway, XW, MPPT, SCP, Conext SW, Battery
  Monitor**, each with a curated set of the useful sensors from its Modbus map.

## Requirements

- **Home Assistant 2026.9.0 or newer.** The integration uses Home Assistant's
  built-in shared Modbus connection, added in 2026.9. Older versions can use
  release v0.0.3-alpha.
- A Conext gateway (Insight Home / Conext Gateway) with **Modbus TCP enabled**,
  reachable from Home Assistant (default port **503**).
- A fixed IP for the gateway (a DHCP reservation in your router) is
  recommended. If its address changes, use **Reconfigure** to point the
  integration at the new one.

## Install

### HACS (recommended)

1. HACS → ⋮ → **Custom repositories** → add
   `https://github.com/Xeyrruken/schneider_modbus_ha`, type *Integration*.
2. Install **Schneider Conext / Insight Home**, then restart Home Assistant.

### Manual

Download `schneider_modbus_ha.zip` from the
[latest release](https://github.com/Xeyrruken/schneider_modbus_ha/releases),
unzip it into `config/custom_components/`, and restart Home Assistant.

## Set up

**Settings → Devices & Services → Add Integration → Schneider Conext / Insight Home.**

- **Scan my network**: probes local subnets for a gateway. Optionally narrow
  it with a CIDR like `192.168.1.0/24`.
- **Enter the IP manually**: if you already know the address.

Then pick the devices you want from the list that's found.

## Options (gear icon)

| Option | Notes |
| --- | --- |
| Main polling interval | Seconds between reads of most sensors (default 30). |
| Live polling interval | Seconds between reads of the live sensor group (default 2). Don't go lower than needed; polling too fast can overload the gateway. |
| Live sensors | Which sensors belong to the live group. |
| 32-bit word order | `big` (default). Switch to `little` only if power or voltage values look wrong. |

## Notes

- Register definitions live as per-device-type templates in `registers.py`;
  `devices.py` maps Conext slave-ID ranges to those templates.
- Found a sensor with a wrong value or scale? Please open an
  [issue](https://github.com/Xeyrruken/schneider_modbus_ha/issues) with the
  device type, the sensor and what you expected.

## License and disclaimer

[MIT](LICENSE). Provided as is, with no warranty.

This is an independent community project. It is not affiliated with, endorsed
by or supported by Schneider Electric. "Schneider Electric", "Conext" and
"Insight" are trademarks of their respective owners.
