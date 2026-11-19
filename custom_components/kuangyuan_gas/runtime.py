"""Runtime objects of one Kuangyuan Gas config entry."""

from __future__ import annotations

from dataclasses import dataclass

from .api import GasAccount
from .const import METER_LEVEL_KEYS
from .coordinator import KuangyuanCoordinator


@dataclass(slots=True)
class KuangyuanRuntime:
    """State shared by the platforms of one config entry."""

    coordinator: KuangyuanCoordinator
    accounts: dict[str, GasAccount]
    selected_unos: list[str]
    entity_keys: set[str]
    account_label: str
    low_balance_threshold: float = 0.0
    instance_name: str = ""

    def has(self, key: str) -> bool:
        """Whether an entity kind is enabled."""
        return key in self.entity_keys

    def meter_keys(self) -> set[str]:
        """Enabled entity kinds that belong to a meter device."""
        return {key for key in self.entity_keys if key in METER_LEVEL_KEYS}
