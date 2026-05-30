"""Register templates per Conext device *type*.

A template is slave-agnostic: the same MPPT template is applied to every MPPT
the gateway reports (slave 30, 31, …). Slaves are assigned at runtime from
auto-detection (see devices.py), not hard-coded here.

Template keys keep their original ``gw_`` / ``xw_`` / ``mppt_`` prefixes so the
primary instance of each type produces the same entity unique_ids as earlier
versions — existing entities and history are preserved.

Fields:
    key            unique slug within the device type
    name           friendly name
    input_type     "input" | "holding"
    address        Modbus register address (protocol address, as in the YAML)
    data_type      "uint16" | "int16" | "uint32" | "int32"
    scale          multiplier applied to the raw value (default 1)
    offset         added after scaling (default 0)
    precision      decimal places to round to (None = no rounding)
    unit           unit_of_measurement
    device_class   HA SensorDeviceClass value
    state_class    HA SensorStateClass value
    value_map      dict[int, str] -> render as an enum/text sensor
    to_fahrenheit  bool -> convert a Celsius result to Fahrenheit
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RegTemplate:
    key: str
    name: str
    address: int
    data_type: str = "uint16"
    input_type: str = "input"
    scale: float = 1.0
    offset: float = 0.0
    precision: int | None = None
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    value_map: dict[int, str] | None = None
    to_fahrenheit: bool = False
    enabled_default: bool = True
    fast: bool = False   # True → polled in the fast (live) coordinator group

    @property
    def count(self) -> int:
        return 2 if self.data_type in ("uint32", "int32") else 1


# --- Enum maps (replace the old template-sensor YAML) -----------------------

_ENABLED_MAP = {2: "Standby", 3: "Operating"}
_FLAG_FAULT_MAP = {0: "No Active Faults", 1: "Has Active Faults"}
_FLAG_WARN_MAP = {0: "No Active Warnings", 1: "Has Active Warnings"}
_DEVICE_STATE_MAP = {
    0: "Hibernate",
    1: "Power Save",
    2: "Safe Mode",
    3: "Operating",
    4: "Diagnostic Mode",
    5: "Remote Power Off",
    255: "Data Not Available",
}
_INVERTER_STATUS_MAP = {
    1024: "Invert",
    1025: "AC Pass Through",
    1026: "APS Only",
    1027: "Load Sense",
    1028: "Inverter Disabled",
    1029: "Load Sense Ready",
    1030: "Engaging Inverter",
    1031: "Engaging Inverter",
    1032: "Inverter Standby",
    1033: "Grid-Tied",
    1034: "Grid Support",
    1035: "Gen Support",
    1036: "Sell-to-Grid",
    1037: "Load Shaving",
    1038: "Grid Frequency Stabilization",
    1039: "AC Coupling",
    1040: "Reverse Ibatt",
}
_CHARGER_ENABLED_MAP = {0: "Disabled", 1: "Enabled"}
_CHARGE_MODE_MAP = {0: "Stand alone", 1: "Primary", 2: "Secondary"}
_PRESENT_MAP = {0: "Inactive", 1: "Active"}
_CHARGER_STATUS_MAP = {
    768: "Not Charging", 769: "Bulk", 770: "Absorption", 771: "Overcharge",
    772: "Equalize", 773: "Float", 774: "No Float", 775: "Constant VI",
    776: "Charger Disabled", 777: "Qualifying AC", 778: "Qualifying APS",
    779: "Engaging Charger", 780: "AC Good", 781: "APS Good", 782: "AC Fault",
    783: "Charge", 784: "AC Fault", 785: "Charge", 786: "Absorption Exit Pending",
    787: "Ground Fault", 788: "AC Good Pending", 789: "Load Shaving",
    790: "AC Support", 791: "AC Coupled", 792: "AC Coupled Bulk",
    793: "AC Coupled Absorption", 794: "AC Coupled Float",
    795: "AC Coupled No Float", 796: "External BMS", 797: "Load Sense",
}


# --------------------------- Conext Gateway --------------------------------

GATEWAY_REGS: list[RegTemplate] = [
    RegTemplate("gw_active_faults", "System Active Faults", 65, "uint16",
                state_class="measurement"),
    RegTemplate("gw_active_warnings", "System Active Warnings", 67, "uint16",
                state_class="measurement"),
    RegTemplate("gw_batt_bank1_charging_power", "Battery Bank 1 Charging Power",
                520, "uint32", unit="W", device_class="power",
                state_class="measurement"),
    RegTemplate("gw_batt_bank1_inverting_power",
                "Battery Bank 1 Inverting Power", 524, "uint32", unit="W",
                device_class="power", state_class="measurement"),
    RegTemplate("gw_batt_bank1_voltage", "Battery Bank 1 Voltage", 512,
                "uint32", scale=0.001, precision=2, unit="V",
                device_class="voltage", state_class="measurement"),
    RegTemplate("gw_pv_harvest_power", "PV Harvest Power", 68, "uint32",
                precision=0, unit="W", device_class="power",
                state_class="measurement", fast=True),
    RegTemplate("gw_dc_charging_power", "DC Charging Power", 70, "uint32",
                precision=0, unit="W", device_class="power",
                state_class="measurement"),
    RegTemplate("gw_dc_inverting_power", "DC Inverting Power", 74, "uint32",
                precision=0, unit="W", device_class="power",
                state_class="measurement"),
    RegTemplate("gw_grid_input_power", "Grid Input Power", 82, "uint32",
                precision=0, unit="W", device_class="power",
                state_class="measurement", fast=True),
    RegTemplate("gw_grid_output_power", "Grid Output Power", 88, "uint32",
                precision=0, unit="W", device_class="power",
                state_class="measurement", fast=True),
    RegTemplate("gw_load_power_inout", "Load Power (In/Out)", 94, "int32",
                precision=0, unit="W", device_class="power",
                state_class="measurement"),
    RegTemplate("gw_load_output_power", "Load Output Power", 96, "uint32",
                precision=0, unit="W", device_class="power",
                state_class="measurement"),
    RegTemplate("gw_load_in_power", "Load In Power", 98, "int32", precision=0,
                unit="W", device_class="power", state_class="measurement"),
    RegTemplate("gw_load_power", "Load Power", 102, "int32", precision=0,
                unit="W", device_class="power", state_class="measurement"),
    RegTemplate("gw_cxw_ac1_power", "CXW AC1 Power", 130, "uint32",
                precision=0, unit="W", device_class="power",
                state_class="measurement"),
    RegTemplate("gw_cxw_ac_out_power", "CXW AC Out Power", 136, "int32",
                precision=0, unit="W", device_class="power",
                state_class="measurement"),
    RegTemplate("gw_mppt_pv_power", "MPPT PV Power", 144, "uint32",
                precision=0, unit="W", device_class="power",
                state_class="measurement"),
    RegTemplate("gw_mppt_battery_power", "MPPT Battery Power", 148, "uint32",
                precision=0, unit="W", device_class="power",
                state_class="measurement"),
    RegTemplate("gw_battery_soc", "Battery SOC", 968, "uint32", unit="%",
                device_class="battery", state_class="measurement", fast=True),
]

# --------------------------- XW / XW Pro inverter --------------------------

XW_REGS: list[RegTemplate] = [
    RegTemplate("xw_inverter_enabled", "Inverter Enabled", 71, "uint16",
                value_map=_ENABLED_MAP),
    RegTemplate("xw_active_faults", "Active Faults", 75, "uint16",
                value_map=_FLAG_FAULT_MAP),
    RegTemplate("xw_active_warnings", "Active Warnings", 76, "uint16",
                value_map=_FLAG_WARN_MAP),
    RegTemplate("xw_inverter_status", "Inverter Status", 122, "uint16",
                value_map=_INVERTER_STATUS_MAP),
    RegTemplate("xw_battery_voltage", "Battery Voltage", 80, "uint32",
                scale=0.001, precision=3, unit="V", device_class="voltage",
                state_class="measurement"),
    RegTemplate("xw_battery_current", "Battery Current", 82, "int32",
                scale=0.001, precision=3, unit="A", device_class="current",
                state_class="measurement"),
    RegTemplate("xw_battery_power", "Battery Power", 84, "int32", precision=0,
                unit="W", device_class="power", state_class="measurement", fast=True),
    RegTemplate("xw_battery_temperature", "Battery Temperature", 86, "uint16",
                scale=0.01, offset=-273, precision=2, unit="°C",
                device_class="temperature", state_class="measurement"),
    RegTemplate("xw_load_ac_voltage", "Load AC Voltage", 140, "uint32",
                scale=0.001, precision=3, unit="V", device_class="voltage",
                state_class="measurement"),
    RegTemplate("xw_load_ac_power", "Load AC Power", 154, "uint32", unit="W",
                device_class="power", state_class="measurement", fast=True),
    RegTemplate("xw_load_ac_frequency", "Load AC Frequency", 152, "uint16",
                scale=0.01, precision=2, unit="Hz", device_class="frequency",
                state_class="measurement"),
    RegTemplate("xw_grid_ac_l1_voltage", "Grid AC L1 Voltage", 110, "uint32",
                scale=0.001, precision=2, unit="V", device_class="voltage",
                state_class="measurement"),
    RegTemplate("xw_grid_ac_l2_voltage", "Grid AC L2 Voltage", 114, "uint32",
                scale=0.001, precision=2, unit="V", device_class="voltage",
                state_class="measurement"),
    RegTemplate("xw_load_ac_current", "Load AC Current", 150, "uint32",
                scale=0.001, precision=3, unit="A", device_class="current",
                state_class="measurement"),
    RegTemplate("xw_grid_ac_input_power", "Grid AC Input Power", 108, "uint32",
                unit="W", device_class="power", state_class="measurement"),
]

# --------------------------- MPPT 60 charge controller ---------------------

MPPT_REGS: list[RegTemplate] = [
    RegTemplate("mppt_device_state", "Device State", 64, "uint16",
                value_map=_DEVICE_STATE_MAP),
    RegTemplate("mppt_active_faults", "Active Faults", 68, "uint16",
                value_map=_FLAG_FAULT_MAP),
    RegTemplate("mppt_active_warnings", "Active Warnings", 69, "uint16",
                value_map=_FLAG_WARN_MAP),
    RegTemplate("mppt_charger_enabled_status", "Charger Enabled Status", 65,
                "uint16", value_map=_CHARGER_ENABLED_MAP),
    RegTemplate("mppt_charge_mode_status", "Charge Mode Status", 67, "uint16",
                value_map=_CHARGE_MODE_MAP),
    RegTemplate("mppt_pv_power", "PV Power", 80, "uint32", precision=0,
                unit="W", device_class="power", state_class="measurement", fast=True),
    RegTemplate("mppt_pv_voltage", "PV Voltage", 76, "uint32", scale=0.001,
                precision=3, unit="V", device_class="voltage",
                state_class="measurement"),
    RegTemplate("mppt_pv_current", "PV Current", 78, "uint32", scale=0.001,
                precision=3, unit="A", device_class="current",
                state_class="measurement"),
    RegTemplate("mppt_dc_output_voltage", "DC Output Voltage", 88, "int32",
                scale=0.001, precision=3, unit="V", device_class="voltage",
                state_class="measurement"),
    RegTemplate("mppt_dc_output_current", "DC Output Current", 90, "int32",
                scale=0.001, precision=3, unit="A", device_class="current",
                state_class="measurement"),
    RegTemplate("mppt_energy_pv_hour", "Energy From PV This Hour", 102,
                "uint32", scale=0.001, precision=3, unit="kWh",
                device_class="energy", state_class="total_increasing"),
    RegTemplate("mppt_pv_active_hour", "PV Input Active This Hour", 104,
                "uint32", scale=0.001, precision=3, unit="kWh",
                device_class="energy", state_class="total_increasing"),
    RegTemplate("mppt_energy_pv_today", "Energy From PV Today", 106, "uint32",
                scale=0.001, precision=3, unit="kWh", device_class="energy",
                state_class="total_increasing"),
    RegTemplate("mppt_energy_pv_week", "Energy From PV This Week", 110,
                "uint32", scale=0.001, precision=3, unit="kWh",
                device_class="energy", state_class="total_increasing"),
    RegTemplate("mppt_pv_active_week", "PV Input Active This Week", 112,
                "uint32", scale=0.001, precision=3, unit="kWh",
                device_class="energy", state_class="total_increasing"),
    RegTemplate("mppt_energy_pv_month", "Energy From PV This Month", 114,
                "uint32", scale=0.001, precision=3, unit="kWh",
                device_class="energy", state_class="total_increasing"),
    RegTemplate("mppt_pv_active_month", "PV Input Active This Month", 116,
                "uint32", scale=0.001, precision=3, unit="kWh",
                device_class="energy", state_class="total_increasing"),
    RegTemplate("mppt_energy_pv_year", "Energy From PV This Year", 118,
                "uint32", scale=0.001, precision=3, unit="kWh",
                device_class="energy", state_class="total_increasing"),
    RegTemplate("mppt_pv_active_year", "PV Input Active This Year", 120,
                "uint32", scale=0.001, precision=3, unit="kWh",
                device_class="energy", state_class="total_increasing"),
    RegTemplate("mppt_operating_mode", "Operating Mode", 172, "uint32",
                "holding", value_map=_ENABLED_MAP),
]

# --------------------------- System Control Panel (SCP) --------------------

SCP_REGS: list[RegTemplate] = [
    RegTemplate("scp_device_state", "Device State", 64, "uint16",
                value_map=_DEVICE_STATE_MAP),
    RegTemplate("scp_device_present", "Device Present", 65, "uint16",
                value_map=_PRESENT_MAP),
    RegTemplate("scp_active_faults", "Active Faults", 66, "uint16",
                value_map=_FLAG_FAULT_MAP),
    RegTemplate("scp_active_warnings", "Active Warnings", 67, "uint16",
                value_map=_FLAG_WARN_MAP),
    RegTemplate("scp_operating_mode", "Operating Mode", 73, "uint16",
                "holding", value_map=_ENABLED_MAP),
]

# --------------------------- Conext SW inverter ----------------------------

SW_REGS: list[RegTemplate] = [
    RegTemplate("sw_device_state", "Device State", 65, "uint16",
                value_map=_DEVICE_STATE_MAP),
    RegTemplate("sw_inverter_enabled", "Inverter Enabled", 67, "uint16",
                value_map=_ENABLED_MAP),
    RegTemplate("sw_charger_enabled", "Charger Enabled", 68, "uint16",
                value_map=_ENABLED_MAP),
    RegTemplate("sw_active_faults", "Active Faults", 69, "uint16",
                value_map=_FLAG_FAULT_MAP),
    RegTemplate("sw_active_warnings", "Active Warnings", 70, "uint16",
                value_map=_FLAG_WARN_MAP),
    RegTemplate("sw_charge_mode_status", "Charge Mode Status", 71, "uint16",
                value_map=_CHARGE_MODE_MAP),
    RegTemplate("sw_inverter_status", "Inverter Status", 75, "uint16",
                value_map=_INVERTER_STATUS_MAP),
    RegTemplate("sw_charger_status", "Charger Status", 85, "uint16",
                value_map=_CHARGER_STATUS_MAP),
    RegTemplate("sw_battery_voltage", "Battery Voltage", 78, "uint32",
                scale=0.001, precision=3, unit="V", device_class="voltage",
                state_class="measurement"),
    RegTemplate("sw_battery_current", "Battery Current", 80, "int32",
                scale=0.001, precision=3, unit="A", device_class="current",
                state_class="measurement"),
    RegTemplate("sw_battery_power", "Battery Power", 82, "int32", precision=0,
                unit="W", device_class="power", state_class="measurement", fast=True),
    RegTemplate("sw_battery_temperature", "Battery Temperature", 84, "uint16",
                scale=0.01, offset=-273, precision=2, unit="°C",
                device_class="temperature", state_class="measurement"),
    RegTemplate("sw_invert_dc_power", "Invert DC Power", 88, "uint32",
                precision=0, unit="W", device_class="power",
                state_class="measurement"),
    RegTemplate("sw_charge_dc_power", "Charge DC Power", 92, "uint32",
                precision=0, unit="W", device_class="power",
                state_class="measurement"),
    RegTemplate("sw_ac1_frequency", "AC1 Frequency", 95, "uint16", scale=0.01,
                precision=2, unit="Hz", device_class="frequency",
                state_class="measurement"),
    RegTemplate("sw_ac1_voltage", "AC1 Voltage", 96, "uint32", scale=0.001,
                precision=2, unit="V", device_class="voltage",
                state_class="measurement"),
    RegTemplate("sw_ac1_input_power", "AC1 Input Power", 100, "int32",
                precision=0, unit="W", device_class="power",
                state_class="measurement"),
    RegTemplate("sw_load_ac_voltage", "Load AC Voltage", 120, "uint32",
                scale=0.001, precision=2, unit="V", device_class="voltage",
                state_class="measurement"),
    RegTemplate("sw_load_ac_current", "Load AC Current", 130, "int32",
                scale=0.001, precision=3, unit="A", device_class="current",
                state_class="measurement"),
    RegTemplate("sw_load_ac_power", "Load AC Power", 132, "int32", precision=0,
                unit="W", device_class="power", state_class="measurement", fast=True),
    RegTemplate("sw_load_ac_frequency", "Load AC Frequency", 138, "uint16",
                scale=0.01, precision=2, unit="Hz", device_class="frequency",
                state_class="measurement"),
    RegTemplate("sw_energy_from_battery_today", "Energy From Battery Today",
                148, "uint32", scale=0.001, precision=3, unit="kWh",
                device_class="energy", state_class="total_increasing"),
    RegTemplate("sw_energy_to_battery_today", "Energy To Battery Today", 172,
                "uint32", scale=0.001, precision=3, unit="kWh",
                device_class="energy", state_class="total_increasing"),
    RegTemplate("sw_ac1_input_energy_today", "AC1 Input Energy Today", 196,
                "uint32", scale=0.001, precision=3, unit="kWh",
                device_class="energy", state_class="total_increasing"),
    RegTemplate("sw_load_output_energy_today", "Load Output Energy Today", 220,
                "uint32", scale=0.001, precision=3, unit="kWh",
                device_class="energy", state_class="total_increasing"),
    RegTemplate("sw_load_output_energy_lifetime", "Load Output Energy Lifetime",
                236, "uint32", scale=0.001, precision=3, unit="kWh",
                device_class="energy", state_class="total_increasing"),
    RegTemplate("sw_ac1_input_energy_lifetime", "AC1 Input Energy Lifetime",
                212, "uint32", scale=0.001, precision=3, unit="kWh",
                device_class="energy", state_class="total_increasing"),
]

# --------------------------- Battery Monitor -------------------------------

BATTMON_REGS: list[RegTemplate] = [
    RegTemplate("bm_device_state", "Device State", 64, "uint16",
                value_map=_DEVICE_STATE_MAP),
    RegTemplate("bm_battery_voltage", "Battery Voltage", 70, "uint32",
                scale=0.001, precision=3, unit="V", device_class="voltage",
                state_class="measurement"),
    RegTemplate("bm_battery_current", "Battery Current", 72, "int32",
                scale=0.001, precision=3, unit="A", device_class="current",
                state_class="measurement", fast=True),
    RegTemplate("bm_battery_temperature", "Battery Temperature", 74, "uint32",
                scale=0.01, offset=-273, precision=2, unit="°C",
                device_class="temperature", state_class="measurement"),
    RegTemplate("bm_battery_soc", "State of Charge", 76, "uint32", unit="%",
                device_class="battery", state_class="measurement", fast=True),
    RegTemplate("bm_battery_soh", "State of Health", 78, "uint32", unit="%",
                state_class="measurement"),
    RegTemplate("bm_capacity_remaining", "Capacity Remaining", 88, "uint32",
                unit="Ah", state_class="measurement"),
    RegTemplate("bm_capacity_removed", "Capacity Removed", 90, "uint32",
                unit="Ah", state_class="measurement"),
    RegTemplate("bm_time_to_full", "Time To Full", 94, "uint32", unit="min",
                device_class="duration", state_class="measurement"),
    RegTemplate("bm_time_to_discharge", "Time To Discharge", 96, "uint32",
                unit="min", device_class="duration", state_class="measurement"),
    RegTemplate("bm_hours_in_float", "Hours In Float", 98, "uint32", scale=0.1,
                precision=1, unit="h", device_class="duration",
                state_class="measurement"),
    RegTemplate("bm_charge_cycles", "Number of Charge Cycles", 110, "uint16",
                state_class="total_increasing"),
]
