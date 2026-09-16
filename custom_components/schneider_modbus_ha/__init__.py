"""The Schneider Conext / Insight Home integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_DEVICES,
    CONF_FAST_KEYS,
    CONF_LIVE_INTERVAL,
    CONF_SCAN_INTERVAL,
    DEFAULT_LIVE_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import SchneiderConnection, SchneiderCoordinator
from .devices import LEGACY_DEVICES, RegSensor, build_sensors

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Schneider Insight from a config entry."""

    # Build the full sensor list from whatever devices are in the entry.
    detected: list[dict] = entry.data.get(CONF_DEVICES, LEGACY_DEVICES)
    all_sensors: list[RegSensor] = build_sensors(detected)

    # Split into fast (live) and slow (background) groups.
    # If the user has chosen a custom set via the options flow, honour it;
    # otherwise fall back to the per-template fast=True defaults.
    stored_fast: list[str] | None = entry.options.get(CONF_FAST_KEYS)
    if stored_fast is not None:
        fast_set = set(stored_fast)
        fast_sensors = [s for s in all_sensors if s.key in fast_set]
        slow_sensors = [s for s in all_sensors if s.key not in fast_set]
    else:
        fast_sensors = [s for s in all_sensors if s.fast]
        slow_sensors = [s for s in all_sensors if not s.fast]

    # Read polling intervals from options (user can tune in the options flow).
    live_interval: int = entry.options.get(CONF_LIVE_INTERVAL, DEFAULT_LIVE_INTERVAL)
    main_interval: int = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    # Holds one shared Modbus unit per slave from HA's modbus integration.
    connection = SchneiderConnection(hass, entry)

    coordinators: list[SchneiderCoordinator] = []

    if fast_sensors:
        live_coord = SchneiderCoordinator(
            hass,
            connection,
            fast_sensors,
            live_interval,
            name=f"{DOMAIN}_live_{entry.entry_id}",
        )
        await live_coord.async_config_entry_first_refresh()
        coordinators.append(live_coord)

    if slow_sensors:
        main_coord = SchneiderCoordinator(
            hass,
            connection,
            slow_sensors,
            main_interval,
            name=f"{DOMAIN}_main_{entry.entry_id}",
        )
        await main_coord.async_config_entry_first_refresh()
        coordinators.append(main_coord)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "connection": connection,
        "coordinators": coordinators,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry_data: dict = hass.data[DOMAIN].pop(entry.entry_id)
        for coord in entry_data["coordinators"]:
            await coord.async_shutdown()
        await entry_data["connection"].close()
    return unloaded


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
