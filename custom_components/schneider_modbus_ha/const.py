"""Constants for the Schneider Conext / Insight Home integration."""

from __future__ import annotations

DOMAIN = "schneider_modbus_ha"

# Config / options keys
CONF_HOST = "host"
CONF_PORT = "port"
CONF_SCAN_CIDR = "scan_cidr"
CONF_WORD_ORDER = "word_order"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_DEVICE_UID = "device_uid"
CONF_DEVICES = "devices"

# Defaults
DEFAULT_PORT = 503
DEFAULT_SCAN_INTERVAL = 30  # seconds, main/slow polling group
DEFAULT_LIVE_INTERVAL = 2   # seconds, live (fast) polling group
DEFAULT_WORD_ORDER = "big"  # matches a no-swap HA modbus config
DEFAULT_TIMEOUT = 10  # seconds, per Modbus request

CONF_LIVE_INTERVAL = "live_scan_interval"
CONF_FAST_KEYS = "fast_keys"   # list[str] stored in options; sensors in the live group
PROBE_TIMEOUT = 3  # seconds, per slave-id probe during device detection

# Modbus exception codes that mean "device is not present behind the gateway".
ABSENT_EXCEPTION_CODES = (0x0A, 0x0B)  # gateway path unavailable / target failed

# Devices are assigned contiguously from each type's base slave id, so stop
# scanning a range after this many consecutive absent ids (small safety margin).
SCAN_LOOKAHEAD = 2

# Common-block register where every device exposes its name (probe target).
COMMON_NAME_ADDRESS = 0

WORD_ORDER_OPTIONS = ["big", "little"]

# Signature read used to confirm a host is really the Conext gateway during
# a subnet scan: gateway slave 1, input register 65 (active fault count).
SIGNATURE_SLAVE = 1
SIGNATURE_INPUT_TYPE = "input"
SIGNATURE_ADDRESS = 65

# Register block-read tuning (Modbus caps a single read at 125 registers).
MAX_BLOCK_REGISTERS = 120
MAX_BLOCK_GAP = 16

# Subnet scan tuning
SCAN_CONCURRENCY = 64
SCAN_CONNECT_TIMEOUT = 0.6  # seconds per host TCP probe

# Reconnection backoff. While the gateway is unreachable we keep cheaply retrying
# the SAME host every poll (a static/reserved IP returns at the same address), but
# the expensive subnet rescan (for the rare case the IP changed) is rate-limited:
# first after MIN seconds, then doubling up to MAX.
SCAN_BACKOFF_MIN = 15   # seconds before the first rescan after a loss
SCAN_BACKOFF_MAX = 120  # cap between rescans during a sustained outage
