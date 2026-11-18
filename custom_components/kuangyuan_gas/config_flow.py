"""Config and options flow for the Kuangyuan Gas integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import settings
from .api import (
    GasAccount,
    KuangyuanAPI,
    KuangyuanAuthError,
    KuangyuanConnectionError,
    KuangyuanDataError,
    KuangyuanError,
    KuangyuanWafError,
    create_session,
)
from .const import (
    ALL_ENTITY_KEYS,
    CONF_ACCOUNTS,
    CONF_BASE_URL,
    CONF_ENTITY_KEYS,
    CONF_LOW_BALANCE,
    CONF_RECHARGE_PAGES,
    CONF_SCAN_INTERVAL,
    CONF_SELECTED_UNOS,
    DEFAULT_BASE_URL,
    DEFAULT_LOW_BALANCE,
    DEFAULT_RECHARGE_PAGES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ENTITY_LABELS_ZH,
    MAX_RECHARGE_PAGES,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .entity import mask_phone

_LOGGER = logging.getLogger(__name__)


async def async_fetch_accounts(
    hass, base_url: str, username: str, password: str
) -> list[GasAccount]:
    """Log in with a throwaway session and read the bound meters."""
    session = create_session()
    try:
        api = KuangyuanAPI(session, username, password, base_url)
        return await api.async_get_accounts()
    finally:
        await session.close()


def account_options(accounts: dict[str, GasAccount]) -> list[SelectOptionDict]:
    """Select options that identify a meter without exposing the raw name."""
    options: list[SelectOptionDict] = []
    for account in accounts.values():
        parts = [account.uno, account.category or ""]
        if account.owner:
            parts.append(account.owner)
        if account.address:
            parts.append(account.address)
        options.append(
            SelectOptionDict(
                value=account.uno,
                label=" · ".join(part for part in parts if part),
            )
        )
    return options


def entity_options() -> list[SelectOptionDict]:
    """Select options for the entity kinds."""
    return [
        SelectOptionDict(value=key, label=ENTITY_LABELS_ZH.get(key, key))
        for key in ALL_ENTITY_KEYS
    ]


def credential_schema(defaults: dict[str, Any]) -> vol.Schema:
    """Form asking for the portal credentials."""
    return vol.Schema(
        {
            vol.Required(
                CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
            vol.Required(
                CONF_PASSWORD, default=defaults.get(CONF_PASSWORD, "")
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
            vol.Optional(
                CONF_BASE_URL, default=defaults.get(CONF_BASE_URL, DEFAULT_BASE_URL)
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
        }
    )


def selection_schema(
    defaults: dict[str, Any], accounts: dict[str, GasAccount]
) -> vol.Schema:
    """Form for meters, entity kinds and polling settings."""
    selected = [
        uno for uno in (defaults.get(CONF_SELECTED_UNOS) or []) if uno in accounts
    ]
    if not selected:
        selected = list(accounts)

    raw_keys = defaults.get(CONF_ENTITY_KEYS)
    if raw_keys is None:
        keys = list(ALL_ENTITY_KEYS)
    else:
        keys = [key for key in raw_keys if key in ALL_ENTITY_KEYS]

    return vol.Schema(
        {
            vol.Required(CONF_SELECTED_UNOS, default=selected): SelectSelector(
                SelectSelectorConfig(
                    options=account_options(accounts),
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            ),
            vol.Required(CONF_ENTITY_KEYS, default=keys): SelectSelector(
                SelectSelectorConfig(
                    options=entity_options(),
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            ),
            vol.Required(
                CONF_SCAN_INTERVAL,
                default=int(defaults.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL,
                    max=MAX_SCAN_INTERVAL,
                    step=30,
                    unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_LOW_BALANCE,
                default=float(defaults.get(CONF_LOW_BALANCE, DEFAULT_LOW_BALANCE)),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=100000,
                    step=0.01,
                    unit_of_measurement="CNY",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_RECHARGE_PAGES,
                default=int(defaults.get(CONF_RECHARGE_PAGES, DEFAULT_RECHARGE_PAGES)),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1,
                    max=MAX_RECHARGE_PAGES,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
    )


def normalize(user_input: dict[str, Any], known: set[str]) -> dict[str, Any]:
    """Clamp and normalise everything the form may return."""
    keys = [
        key for key in (user_input.get(CONF_ENTITY_KEYS) or []) if key in ALL_ENTITY_KEYS
    ]
    return {
        CONF_SELECTED_UNOS: [
            uno for uno in (user_input.get(CONF_SELECTED_UNOS) or []) if uno in known
        ],
        CONF_ENTITY_KEYS: keys,
        CONF_SCAN_INTERVAL: settings.clamp_interval(user_input.get(CONF_SCAN_INTERVAL)),
        CONF_LOW_BALANCE: settings.clamp_threshold(user_input.get(CONF_LOW_BALANCE)),
        CONF_RECHARGE_PAGES: settings.clamp_pages(user_input.get(CONF_RECHARGE_PAGES)),
    }


def serialize_accounts(accounts: dict[str, GasAccount]) -> list[dict[str, str]]:
    """Store only what is needed to rebuild devices without logging in."""
    return [
        {
            "uno": account.uno,
            "tel": account.tel,
            "owner": account.owner,
            "address": account.address,
            "category": account.category,
        }
        for account in accounts.values()
    ]


def entry_title(selected: list[str], accounts: dict[str, GasAccount]) -> str:
    """A short, non-identifying title for the config entry."""
    if len(selected) == 1:
        account = accounts.get(selected[0])
        label = account.uno if account else selected[0]
        return f"旷远燃气 {label}"
    for account in accounts.values():
        if account.tel:
            return f"旷远燃气 {mask_phone(account.tel)}"
    return f"旷远燃气 {len(selected)} 个户号"


class KuangyuanGasConfigFlow(ConfigFlow, domain=DOMAIN):
    """Two-step setup: credentials, then meters and entity selection."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise per-flow state (never class attributes)."""
        self._accounts: dict[str, GasAccount] = {}
        self._credentials: dict[str, Any] = {}
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the customer portal credentials."""
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = (user_input.get(CONF_BASE_URL) or DEFAULT_BASE_URL).strip()
            username = str(user_input.get(CONF_USERNAME) or "").strip()
            password = str(user_input.get(CONF_PASSWORD) or "")
            try:
                accounts = await async_fetch_accounts(
                    self.hass, base_url, username, password
                )
            except KuangyuanAuthError:
                errors["base"] = "invalid_auth"
            except (KuangyuanConnectionError, KuangyuanWafError):
                errors["base"] = "cannot_connect"
            except KuangyuanDataError:
                errors["base"] = "no_accounts"
            except KuangyuanError:
                errors["base"] = "unknown"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error while reading the meters")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(f"{base_url}|{username}")
                self._abort_if_unique_id_configured()
                self._accounts = {account.uno: account for account in accounts}
                self._credentials = {
                    CONF_USERNAME: username,
                    CONF_PASSWORD: password,
                    CONF_BASE_URL: base_url,
                }
                return await self.async_step_meters()

        return self.async_show_form(
            step_id="user",
            data_schema=credential_schema(self._credentials),
            errors=errors,
        )

    async def async_step_meters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the meters to show, the entity kinds and the interval."""
        errors: dict[str, str] = {}

        if user_input is not None:
            data = normalize(user_input, set(self._accounts))
            if not data[CONF_SELECTED_UNOS]:
                errors["base"] = "no_meter_selected"
            else:
                data[CONF_ACCOUNTS] = serialize_accounts(self._accounts)
                return self.async_create_entry(
                    title=entry_title(data[CONF_SELECTED_UNOS], self._accounts),
                    data={**self._credentials, **data},
                )

        return self.async_show_form(
            step_id="meters",
            data_schema=selection_schema({}, self._accounts),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle a rejected password (credential change)."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask only for the password: the username stays unchanged."""
        entry = self._reauth_entry
        errors: dict[str, str] = {}

        if entry is None:
            return self.async_abort(reason="reauth_failed")

        if user_input is not None:
            password = str(user_input.get(CONF_PASSWORD) or "")
            base_url = entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
            username = entry.data[CONF_USERNAME]
            try:
                accounts = await async_fetch_accounts(
                    self.hass, base_url, username, password
                )
            except KuangyuanAuthError:
                errors["base"] = "invalid_auth"
            except (KuangyuanConnectionError, KuangyuanWafError):
                errors["base"] = "cannot_connect"
            except KuangyuanError:
                errors["base"] = "unknown"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during re-authentication")
                errors["base"] = "unknown"
            else:
                merged = dict(entry.data)
                merged[CONF_PASSWORD] = password
                merged[CONF_ACCOUNTS] = serialize_accounts(
                    {account.uno: account for account in accounts}
                )
                self.hass.config_entries.async_update_entry(entry, data=merged)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            description_placeholders={
                "username": mask_phone(str(entry.data.get(CONF_USERNAME, ""))),
                "url": str(entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)),
            },
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return KuangyuanGasOptionsFlow()


class KuangyuanGasOptionsFlow(OptionsFlow):
    """Change the shown meters, entity kinds and polling settings."""

    def __init__(self) -> None:
        """Initialise the options flow."""
        self._accounts: dict[str, GasAccount] = {}

    def _known_accounts(self) -> dict[str, GasAccount]:
        """Meter metadata stored at setup time."""
        if self._accounts:
            return self._accounts
        stored = settings.stored_accounts(self.config_entry)
        self._accounts = {
            str(item["uno"]): GasAccount(
                uno=str(item["uno"]),
                tel=str(item.get("tel") or ""),
                owner=str(item.get("owner") or ""),
                address=str(item.get("address") or ""),
                category=str(item.get("category") or ""),
            )
            for item in stored
        }
        return self._accounts

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit this account's selection without re-entering credentials."""
        entry = self.config_entry
        errors: dict[str, str] = {}
        accounts = self._known_accounts()

        if not accounts:
            return self.async_abort(reason="no_accounts")

        if user_input is not None:
            data = normalize(user_input, set(accounts))
            if not data[CONF_SELECTED_UNOS]:
                errors["base"] = "no_meter_selected"
            else:
                return self.async_create_entry(title="", data=data)

        current = {**entry.data, **entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=selection_schema(current, accounts),
            errors=errors,
        )

