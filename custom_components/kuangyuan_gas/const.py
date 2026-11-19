"""Constants for the Kuangyuan Gas integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "kuangyuan_gas"
NAME: Final = "Kuangyuan Gas"
# Shown in the Home Assistant UI for devices; the domain and manifest stay
# ascii because they are also used in file paths and unique ids.
DEVICE_PREFIX: Final = "旷远燃气"
MANUFACTURER: Final = "旷远能源股份有限公司"

CONF_BASE_URL: Final = "base_url"
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_ACCOUNTS: Final = "accounts"
CONF_SELECTED_UNOS: Final = "selected_unos"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_LOW_BALANCE: Final = "low_balance_threshold"
CONF_ENTITY_KEYS: Final = "entity_keys"
CONF_RECHARGE_PAGES: Final = "recharge_pages"

DEFAULT_BASE_URL: Final = "https://www.kynyyyt.com"
DEFAULT_SCAN_INTERVAL: Final = 1800
MIN_SCAN_INTERVAL: Final = 300
MAX_SCAN_INTERVAL: Final = 86400
DEFAULT_LOW_BALANCE: Final = 20.0
DEFAULT_RECHARGE_PAGES: Final = 1
MAX_RECHARGE_PAGES: Final = 5

# Upper bound on the number of gas meters one account may expose.
MAX_ACCOUNTS: Final = 20

# The portal sits behind a cloud WAF in front of an ASP application, so
# requests are serialised with a small delay between calls.
REQUEST_GAP_SECONDS: Final = 0.4

# Entity key suffixes. They are used inside unique ids, so they must stay stable.
KEY_BALANCE: Final = "balance"
KEY_USAGE: Final = "usage"
KEY_DAILY_USAGE: Final = "daily_usage"
KEY_MONTHLY_USAGE: Final = "monthly_usage"
KEY_SUPPLY: Final = "supply"
KEY_LOW_BALANCE: Final = "low_balance"
KEY_UPDATED_AT: Final = "data_updated_at"
KEY_LAST_RECHARGE_AMOUNT: Final = "last_recharge_amount"
KEY_LAST_RECHARGE_TIME: Final = "last_recharge_time"
KEY_RECHARGE_COUNT: Final = "recharge_count"
KEY_REFRESH_BUTTON: Final = "refresh"

# Entity kinds offered by the integration, in UI order.
# Every entity belongs to one gas meter (user 编号); there is no separate
# account level device, so the entry only lists the meters themselves.
ENTITY_OPTIONS: Final = (
    (KEY_BALANCE, True),
    (KEY_USAGE, True),
    (KEY_DAILY_USAGE, True),
    (KEY_MONTHLY_USAGE, True),
    (KEY_SUPPLY, True),
    (KEY_LOW_BALANCE, True),
    (KEY_UPDATED_AT, True),
    (KEY_LAST_RECHARGE_AMOUNT, True),
    (KEY_LAST_RECHARGE_TIME, True),
    (KEY_RECHARGE_COUNT, True),
    (KEY_REFRESH_BUTTON, True),
)

ALL_ENTITY_KEYS: Final = [key for key, _default in ENTITY_OPTIONS]
DEFAULT_ENTITY_KEYS: Final = [key for key, default in ENTITY_OPTIONS if default]
METER_LEVEL_KEYS: Final = set(ALL_ENTITY_KEYS)
RECHARGE_KEYS: Final = {
    KEY_LAST_RECHARGE_AMOUNT,
    KEY_LAST_RECHARGE_TIME,
    KEY_RECHARGE_COUNT,
}
# Entities whose value comes from the Home Assistant recorder instead of the
# portal.  Enabling them requires the recorder component.
CONSUMPTION_KEYS: Final = {KEY_DAILY_USAGE, KEY_MONTHLY_USAGE}

# Labels used by the config flow and the README (zh-Hans first).
ENTITY_LABELS_ZH: Final = {
    KEY_BALANCE: "账户余额",
    KEY_USAGE: "累积用气量",
    KEY_DAILY_USAGE: "日用气量（本机统计）",
    KEY_MONTHLY_USAGE: "月用气量（本机统计）",
    KEY_SUPPLY: "通气状态",
    KEY_LOW_BALANCE: "余额不足提醒",
    KEY_UPDATED_AT: "数据时间",
    KEY_LAST_RECHARGE_AMOUNT: "最近充值金额",
    KEY_LAST_RECHARGE_TIME: "最近充值时间",
    KEY_RECHARGE_COUNT: "充值笔数",
    KEY_REFRESH_BUTTON: "立即刷新",
}

ENTITY_LABELS_EN: Final = {
    KEY_BALANCE: "Balance",
    KEY_USAGE: "Total gas usage",
    KEY_DAILY_USAGE: "Daily usage (local)",
    KEY_MONTHLY_USAGE: "Monthly usage (local)",
    KEY_SUPPLY: "Gas supply",
    KEY_LOW_BALANCE: "Low balance",
    KEY_UPDATED_AT: "Data timestamp",
    KEY_LAST_RECHARGE_AMOUNT: "Last recharge amount",
    KEY_LAST_RECHARGE_TIME: "Last recharge time",
    KEY_RECHARGE_COUNT: "Recharge count",
    KEY_REFRESH_BUTTON: "Refresh now",
}

UPDATE_INTERVAL: Final = timedelta(seconds=DEFAULT_SCAN_INTERVAL)
