"""Config and options flow for the Schneider Conext / Insight Home integration."""

from __future__ import annotations

import uuid
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_DEVICE_UID,
    CONF_DEVICES,
    CONF_FAST_KEYS,
    CONF_HOST,
    CONF_LIVE_INTERVAL,
    CONF_PORT,
    CONF_SCAN_CIDR,
    CONF_SCAN_INTERVAL,
    CONF_WORD_ORDER,
    DEFAULT_LIVE_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WORD_ORDER,
    DOMAIN,
    SIGNATURE_ADDRESS,
    SIGNATURE_SLAVE,
    WORD_ORDER_OPTIONS,
)
from homeassistant.components.modbus import async_get_temporary_unit
from modbus_connection import ModbusError, ModbusTcpParams

from .devices import DEVICE_TYPES, DEVICE_TYPES_BY_KEY, LEGACY_DEVICES, build_sensors
from .discovery import detect_devices, scan_for_gateways

# Friendly labels for the manual "add a device" type dropdown.
_TYPE_LABELS = {d.key: d.name for d in DEVICE_TYPES}


async def _resolve_uid(hass, host: str) -> str:
    """Return a stable device id (MAC if resolvable, else a random uuid)."""
    try:
        from getmac import get_mac_address

        mac = await hass.async_add_executor_job(lambda: get_mac_address(ip=host))
        if mac:
            return mac.lower().replace(":", "")
    except Exception:  # noqa: BLE001 - getmac is best-effort
        pass
    return uuid.uuid4().hex


async def _validate_host(hass, host: str, port: int) -> bool:
    """Confirm the Conext gateway answers at host:port via HA's shared modbus."""
    params = ModbusTcpParams(host=host, port=port)
    try:
        async with async_get_temporary_unit(hass, params, SIGNATURE_SLAVE) as unit:
            await unit.read_input_registers(SIGNATURE_ADDRESS, 1)
        return True
    except (ModbusError, OSError):
        return False


def _candidate_label(cand: dict) -> str:
    """Human label for a detected device in the selection list."""
    dtype = DEVICE_TYPES_BY_KEY.get(cand["type"])
    base = (cand.get("name") or "").strip() or (dtype.name if dtype else cand["type"])
    return f"{base} — slave {cand['slave']}"


class SchneiderConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str = ""
        self._port: int = DEFAULT_PORT
        self._uid: str = ""
        self._candidates: list[dict] = []
        self._chosen: list[dict] = []
        self._added: list[dict] = []
        self._reconfigure_entry: ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["scan", "manual"])

    async def async_step_scan(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Scan the local subnet for a gateway."""
        if user_input is not None:
            self._port = user_input[CONF_PORT]
            cidr = user_input.get(CONF_SCAN_CIDR) or None
            found = await scan_for_gateways(self.hass, self._port, cidr)
            if not found:
                return self.async_show_form(
                    step_id="scan",
                    data_schema=self._scan_schema(),
                    errors={"base": "no_devices_found"},
                )
            return await self._set_host(found[0], self._port)

        return self.async_show_form(step_id="scan", data_schema=self._scan_schema())

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Enter the gateway host manually."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input[CONF_PORT]
            if await _validate_host(self.hass, host, port):
                return await self._set_host(host, port)
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                }
            ),
            errors=errors,
        )

    async def _set_host(self, host: str, port: int) -> ConfigFlowResult:
        """Record the gateway, set the unique id, then detect devices."""
        self._host = host
        self._port = port
        self._uid = await _resolve_uid(self.hass, host)
        await self.async_set_unique_id(self._uid)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        self._candidates = await detect_devices(self.hass, host, port)
        return await self.async_step_select()

    async def async_step_select(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick which detected devices to create."""
        if user_input is not None:
            self._chosen = [
                self._candidates[int(i)] for i in user_input["devices"]
            ]
            if user_input.get("add_device"):
                return await self.async_step_add()
            return await self._finalize()

        options = {
            str(i): _candidate_label(cand)
            for i, cand in enumerate(self._candidates)
        }
        schema = vol.Schema(
            {
                vol.Required("devices", default=list(options)): cv.multi_select(
                    options
                ),
                vol.Required("add_device", default=False): bool,
            }
        )
        return self.async_show_form(
            step_id="select",
            data_schema=schema,
            description_placeholders={"count": str(len(self._candidates))},
        )

    async def async_step_add(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manually add a device that wasn't detected (loops if requested)."""
        if user_input is not None:
            self._added.append(
                {
                    "type": user_input["type"],
                    "slave": int(user_input["slave"]),
                    "custom_name": user_input.get("name", ""),
                }
            )
            if user_input.get("add_another"):
                return await self.async_step_add()
            return await self._finalize()

        schema = vol.Schema(
            {
                vol.Required("type"): vol.In(_TYPE_LABELS),
                vol.Required("slave"): vol.All(int, vol.Range(min=1, max=247)),
                vol.Optional("name"): str,
                vol.Required("add_another", default=False): bool,
            }
        )
        return self.async_show_form(step_id="add", data_schema=schema)

    async def _finalize(self) -> ConfigFlowResult:
        """Persist the chosen + manually added devices."""
        devices = self._chosen + self._added
        if self._reconfigure_entry is not None:
            return self.async_update_reload_and_abort(
                self._reconfigure_entry,
                data={**self._reconfigure_entry.data, CONF_DEVICES: devices},
            )
        return self.async_create_entry(
            title=f"Conext Gateway ({self._host})",
            data={
                CONF_HOST: self._host,
                CONF_PORT: self._port,
                CONF_DEVICE_UID: self._uid,
                CONF_DEVICES: devices,
            },
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-scan an existing entry and re-select devices."""
        entry = self._get_reconfigure_entry()
        self._reconfigure_entry = entry
        self._host = entry.data[CONF_HOST]
        self._port = entry.data[CONF_PORT]
        self._candidates = await detect_devices(self.hass, self._host, self._port)
        return await self.async_step_select()

    @staticmethod
    def _scan_schema() -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Optional(CONF_SCAN_CIDR): str,
            }
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return SchneiderOptionsFlow()


class SchneiderOptionsFlow(OptionsFlow):
    """Handle integration options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        opts = self.config_entry.options

        # Build the candidate list for the live-sensor picker.
        # All sensors are eligible except energy counters (total_increasing),
        # which only accumulate slowly and gain nothing from fast polling.
        all_sensors = build_sensors(
            self.config_entry.data.get(CONF_DEVICES, LEGACY_DEVICES)
        )

        # Sort by device-type order then sensor name so the dropdown is
        # grouped naturally (Gateway → XW → MPPT → SCP → …).
        _type_order = {d.key: i for i, d in enumerate(DEVICE_TYPES)}
        candidates = sorted(
            (s for s in all_sensors if s.state_class != "total_increasing"),
            key=lambda s: (
                _type_order.get(s.device_instance.split("_")[0], 99),
                s.device_label,
                s.name,
            ),
        )
        live_options = [
            {"value": s.key, "label": f"{s.device_label} — {s.name}"}
            for s in candidates
        ]
        live_keys = {o["value"] for o in live_options}

        # Default selection: stored user choice, or the template fast=True set.
        stored_fast: list[str] | None = opts.get(CONF_FAST_KEYS)
        if stored_fast is not None:
            default_fast = [k for k in stored_fast if k in live_keys]
        else:
            default_fast = [s.key for s in candidates if s.fast]

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SCAN_INTERVAL,
                        default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                    ): NumberSelector(NumberSelectorConfig(
                        min=5, max=60, step=1, mode=NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )),
                    vol.Required(
                        CONF_LIVE_INTERVAL,
                        default=opts.get(CONF_LIVE_INTERVAL, DEFAULT_LIVE_INTERVAL),
                    ): NumberSelector(NumberSelectorConfig(
                        min=1, max=60, step=1, mode=NumberSelectorMode.BOX,
                        unit_of_measurement="s",
                    )),
                    vol.Required(
                        CONF_FAST_KEYS,
                        default=default_fast,
                    ): SelectSelector(SelectSelectorConfig(
                        options=live_options,
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )),
                    vol.Required(
                        CONF_WORD_ORDER,
                        default=opts.get(CONF_WORD_ORDER, DEFAULT_WORD_ORDER),
                    ): vol.In(WORD_ORDER_OPTIONS),
                }
            ),
        )
