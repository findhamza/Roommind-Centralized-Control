"""RoomMind CC – Holistic room climate management for Home Assistant.

A fork of RoomMind with its own domain ("roommind_cc") so it can be
installed side by side with upstream RoomMind: separate storage, panel,
websocket API, and entities.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from homeassistant.components.frontend import (
    async_register_built_in_panel,
    async_remove_panel,
)
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, PLATFORMS, VERSION
from .coordinator import RoomMindCoordinator
from .store import RoomMindStore
from .websocket_api import async_register_websocket_commands

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the RoomMind CC integration (YAML, runs once)."""
    hass.data.setdefault(DOMAIN, {})
    async_register_websocket_commands(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up RoomMind CC from a config entry."""
    # Ensure the store is created and loaded (once across all entries)
    store = hass.data[DOMAIN].get("store")
    if not store:
        store = RoomMindStore(hass)
        await store.async_load()
        hass.data[DOMAIN]["store"] = store

    coordinator = RoomMindCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator
    hass.data[DOMAIN]["coordinator"] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Clean up orphaned entities (e.g. cover entities for rooms without covers)
    coordinator.cleanup_orphaned_entities()

    await _async_register_panel(hass)
    await _async_check_version_mismatch(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a RoomMind CC config entry."""
    unload_ok: bool = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        hass.data[DOMAIN].pop("coordinator", None)

    # Remove panel if no entries remain
    if not hass.data[DOMAIN]:
        async_remove_panel(hass, "roommind-cc")

    return unload_ok


async def _async_check_version_mismatch(hass: HomeAssistant) -> None:
    """Compare in-memory VERSION (from boot) with manifest.json on disk."""
    manifest_path = Path(__file__).parent / "manifest.json"
    try:
        disk_version: str = await hass.async_add_executor_job(lambda: json.loads(manifest_path.read_text())["version"])
    except Exception:  # noqa: BLE001
        return

    if disk_version != VERSION:
        ir.async_create_issue(
            hass,
            DOMAIN,
            "restart_required",
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="restart_required",
            translation_placeholders={"version": disk_version},
        )
        _LOGGER.warning(
            "RoomMind CC on disk is %s but running %s – restart required",
            disk_version,
            VERSION,
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, "restart_required")


async def _async_register_panel(hass: HomeAssistant) -> None:
    """Register the RoomMind CC custom panel in the sidebar.

    All identifiers (URL path, static path, custom-element name) are
    distinct from upstream RoomMind so both integrations can be installed
    side by side without colliding in the frontend.
    """
    if hass.data[DOMAIN].get("panel_registered"):
        return

    panel_js = Path(__file__).parent / "frontend" / "roommind-cc-panel.js"
    if not panel_js.exists():
        _LOGGER.warning(
            "RoomMind CC panel JS not found at %s – sidebar panel not registered",
            panel_js,
        )
        return

    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig("/roommind_cc/roommind-cc-panel.js", str(panel_js), False)]
        )
    except RuntimeError:
        _LOGGER.debug("RoomMind CC static path already registered")

    try:
        async_register_built_in_panel(
            hass,
            component_name="custom",
            sidebar_title="RoomMind CC",
            sidebar_icon="mdi:home-thermometer",
            frontend_url_path="roommind-cc",
            config={
                "_panel_custom": {
                    "name": "roommind-cc-panel",
                    "embed_iframe": False,
                    "trust_external": False,
                    "js_url": "/roommind_cc/roommind-cc-panel.js",
                }
            },
        )
    except ValueError:
        _LOGGER.debug("RoomMind CC panel already registered")

    hass.data[DOMAIN]["panel_registered"] = True
    _LOGGER.info("RoomMind CC panel registered in sidebar")
