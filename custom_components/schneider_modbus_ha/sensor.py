"""Sensor platform for the Schneider Conext / Insight Home integration."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_UID, DOMAIN
from .coordinator import SchneiderCoordinator
from .devices import GATEWAY_INSTANCE, RegSensor

# Counts that are really diagnostics, mirroring the original
# entity_category: diagnostic YAML.
_DIAGNOSTIC_KEYS = {"gw_active_faults", "gw_active_warnings"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors for every detected device across all coordinator groups."""
    entry_data: dict = hass.data[DOMAIN][entry.entry_id]
    device_uid: str = entry.data[CONF_DEVICE_UID]
    coordinators: list[SchneiderCoordinator] = entry_data["coordinators"]

    entities: list[SchneiderSensor] = []
    for coordinator in coordinators:
        for reg in coordinator.sensors:
            entities.append(SchneiderSensor(coordinator, reg, device_uid))

    async_add_entities(entities)


class SchneiderSensor(CoordinatorEntity[SchneiderCoordinator], SensorEntity):
    """A single decoded Modbus register exposed as a sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SchneiderCoordinator,
        reg: RegSensor,
        device_uid: str,
    ) -> None:
        super().__init__(coordinator)
        self._reg = reg
        self._attr_unique_id = f"{device_uid}_{reg.key}"
        self._attr_name = reg.name
        self._attr_entity_registry_enabled_default = reg.enabled_default

        if reg.value_map is not None:
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = sorted(set(reg.value_map.values()) | {"N/A"})
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        else:
            self._attr_native_unit_of_measurement = reg.unit
            if reg.device_class:
                self._attr_device_class = SensorDeviceClass(reg.device_class)
            if reg.state_class:
                self._attr_state_class = SensorStateClass(reg.state_class)
            if reg.key in _DIAGNOSTIC_KEYS:
                self._attr_entity_category = EntityCategory.DIAGNOSTIC

        device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{device_uid}_{reg.device_instance}")},
            name=reg.device_label,
            manufacturer="Schneider Electric",
            model=reg.device_model,
        )
        if reg.device_serial:
            device_info["serial_number"] = reg.device_serial
        if reg.device_instance != GATEWAY_INSTANCE:
            device_info["via_device"] = (DOMAIN, f"{device_uid}_{GATEWAY_INSTANCE}")
        self._attr_device_info = device_info

    @property
    def native_value(self) -> object | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._reg.key)

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self.coordinator.data.get(self._reg.key) is not None
        )
