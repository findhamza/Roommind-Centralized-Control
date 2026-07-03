"""Tests for single-zone priority room wiring in the coordinator.

The scenario: a sensor-only bedroom (no climate devices of its own) is served
by one central thermostat. When the bedroom runs hot, the coordinator must
bias the thermostat setpoint downward; the bedroom's EKF must train from the
central thermostat's hvac_action.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from .conftest import _create_coordinator, _make_store_mock, make_mock_states_get

BEDROOM = {
    "area_id": "bedroom_abc",
    "thermostats": [],
    "acs": [],
    "devices": [],
    "temperature_sensor": "sensor.bedroom_temp",
    "humidity_sensor": "",
    "climate_mode": "auto",
    "schedules": [],
    "schedule_selector_entity": "",
    "comfort_temp": 18.0,
    "eco_temp": 16.0,
    "comfort_heat": 18.0,
    "comfort_cool": 23.0,
    "eco_heat": 16.0,
    "eco_cool": 27.0,
    "occupancy_sensors": [],
}

SINGLE_ZONE_SETTINGS = {
    "climate_control_active": True,
    "priority_zones": [
        {
            "id": "down",
            "name": "Downstairs",
            "enabled": True,
            "thermostat_entity": "climate.downstairs",
            "zone_rooms": ["bedroom_abc"],
            "priority_rooms": [{"area_id": "bedroom_abc"}],
        }
    ],
}


def _thermostat_attrs(**overrides):
    attrs = {
        "temperature": 23.0,
        "current_temperature": 23.0,
        "hvac_modes": ["off", "cool", "heat"],
        "hvac_action": "idle",
    }
    attrs.update(overrides)
    return attrs


def _states_get(bedroom_temp="25.0", thermostat_state="cool", thermostat_attrs=None):
    """Mock states.get for the bedroom + central thermostat setup."""
    return make_mock_states_get(
        outdoor_temp="30.0",
        extra={
            "sensor.bedroom_temp": (bedroom_temp, {}),
            "climate.downstairs": (
                thermostat_state,
                thermostat_attrs if thermostat_attrs is not None else _thermostat_attrs(),
            ),
        },
    )


def _setup(hass, settings=None, states_get=None):
    store = _make_store_mock({"bedroom_abc": dict(BEDROOM)}, settings or dict(SINGLE_ZONE_SETTINGS))
    hass.states.get = MagicMock(side_effect=states_get or _states_get())
    hass.services.async_call = AsyncMock()
    hass.data = {"roommind_cc": {"store": store}}
    return store


def _thermostat_setpoint_calls(hass):
    return [
        c
        for c in hass.services.async_call.call_args_list
        if len(c.args) >= 3
        and c.args[0] == "climate"
        and c.args[1] == "set_temperature"
        and c.args[2].get("entity_id") == "climate.downstairs"
    ]


class TestSingleZoneCoordinatorWiring:
    @pytest.mark.asyncio
    async def test_bedroom_hot_main_satisfied_lowers_setpoint(self, hass, mock_config_entry):
        """Headline case: bedroom 2°C hot, thermostat area satisfied → bias down."""
        _setup(hass)
        coordinator = _create_coordinator(hass, mock_config_entry)
        await coordinator._async_update_data()

        calls = _thermostat_setpoint_calls(hass)
        assert len(calls) == 1
        # error 2.0 → dynamic bias (2.0-0.2)+0.5 = 2.3 → 23.0 - 2.3 = 20.7
        assert calls[0].args[2]["temperature"] == pytest.approx(20.7)
        assert coordinator.priority_zone_data["down"]["status"] == "forcing_cooling"
        assert coordinator.priority_zone_data["down"]["active_room"] == "bedroom_abc"
        assert coordinator.priority_zone_data["down"]["room_error"] == pytest.approx(2.0)

    @pytest.mark.asyncio
    async def test_no_zones_never_touches_thermostat(self, hass, mock_config_entry):
        settings = {"climate_control_active": True}  # no priority_zones
        _setup(hass, settings=settings)
        coordinator = _create_coordinator(hass, mock_config_entry)
        await coordinator._async_update_data()

        assert not _thermostat_setpoint_calls(hass)
        assert coordinator.priority_zone_data == {}

    @pytest.mark.asyncio
    async def test_disabled_zone_never_touches_thermostat(self, hass, mock_config_entry):
        settings = {
            "climate_control_active": True,
            "priority_zones": [{**SINGLE_ZONE_SETTINGS["priority_zones"][0], "enabled": False}],
        }
        _setup(hass, settings=settings)
        coordinator = _create_coordinator(hass, mock_config_entry)
        await coordinator._async_update_data()

        assert not _thermostat_setpoint_calls(hass)
        assert coordinator.priority_zone_data["down"]["status"] == "disabled"

    @pytest.mark.asyncio
    async def test_bedroom_satisfied_no_forcing(self, hass, mock_config_entry):
        _setup(hass, states_get=_states_get(bedroom_temp="23.0"))
        coordinator = _create_coordinator(hass, mock_config_entry)
        await coordinator._async_update_data()

        assert not _thermostat_setpoint_calls(hass)
        assert coordinator.priority_zone_data["down"]["status"] == "idle"

    @pytest.mark.asyncio
    async def test_main_protection_blocks_forcing(self, hass, mock_config_entry):
        """Thermostat area already at the main_min bound → no authority."""
        attrs = _thermostat_attrs(current_temperature=20.0)
        _setup(hass, states_get=_states_get(thermostat_attrs=attrs))
        coordinator = _create_coordinator(hass, mock_config_entry)
        await coordinator._async_update_data()

        assert not _thermostat_setpoint_calls(hass)
        assert coordinator.priority_zone_data["down"]["main_protection_active"] is True
        assert coordinator.priority_zone_data["down"]["forcing"] is False

    @pytest.mark.asyncio
    async def test_restore_after_bedroom_reaches_target(self, hass, mock_config_entry):
        """Second cycle: bedroom satisfied past min-run → nominal restored."""
        _setup(hass)
        coordinator = _create_coordinator(hass, mock_config_entry)
        await coordinator._async_update_data()
        assert coordinator.priority_zone_data["down"]["forcing"] is True

        # Bedroom reaches target; the entity now reflects our biased setpoint
        attrs = _thermostat_attrs(temperature=20.7)
        hass.states.get = MagicMock(side_effect=_states_get(bedroom_temp="23.0", thermostat_attrs=attrs))
        hass.services.async_call.reset_mock()
        # Simulate min-run having elapsed
        coordinator._zone_managers["down"]._session_started -= 3600

        await coordinator._async_update_data()

        calls = _thermostat_setpoint_calls(hass)
        assert len(calls) == 1
        assert calls[0].args[2]["temperature"] == pytest.approx(23.0)
        assert coordinator.priority_zone_data["down"]["forcing"] is False

    @pytest.mark.asyncio
    async def test_zone_room_trains_ekf_from_thermostat_action(self, hass, mock_config_entry):
        """Sensor-only zone room gets its EKF label from the central hvac_action."""
        attrs = _thermostat_attrs(hvac_action="cooling")
        _setup(hass, states_get=_states_get(thermostat_attrs=attrs))
        coordinator = _create_coordinator(hass, mock_config_entry)
        coordinator._ekf_training = MagicMock()
        await coordinator._async_update_data()

        assert coordinator._ekf_training.process.called
        kwargs = coordinator._ekf_training.process.call_args.kwargs
        assert kwargs["area_id"] == "bedroom_abc"
        assert kwargs["ekf_mode"] == "cooling"
        assert kwargs["ekf_pf"] == 1.0
        assert kwargs["can_cool"] is True
        assert kwargs["can_heat"] is True

    @pytest.mark.asyncio
    async def test_zone_room_trains_idle_when_thermostat_idle(self, hass, mock_config_entry):
        _setup(hass, states_get=_states_get(bedroom_temp="23.0"))
        coordinator = _create_coordinator(hass, mock_config_entry)
        coordinator._ekf_training = MagicMock()
        await coordinator._async_update_data()

        kwargs = coordinator._ekf_training.process.call_args.kwargs
        assert kwargs["ekf_mode"] == "idle"

    @pytest.mark.asyncio
    async def test_zone_room_mode_reflects_thermostat_cooling(self, hass, mock_config_entry):
        """A sensor-only zone room shows 'cooling' when the thermostat is cooling."""
        attrs = _thermostat_attrs(hvac_action="cooling")
        _setup(hass, states_get=_states_get(thermostat_attrs=attrs))
        coordinator = _create_coordinator(hass, mock_config_entry)
        await coordinator._async_update_data()

        rs = coordinator.rooms["bedroom_abc"]
        assert rs["mode"] == "cooling"
        assert rs["zone_id"] == "down"
        assert rs["zone_priority_active"] is True
        assert rs["zone_priority_direction"] == "cool"

    @pytest.mark.asyncio
    async def test_zone_room_mode_idle_when_thermostat_idle(self, hass, mock_config_entry):
        """Zone room stays idle when the thermostat isn't actively conditioning."""
        # Bedroom satisfied so no forcing; thermostat hvac_action idle
        _setup(hass, states_get=_states_get(bedroom_temp="23.0"))
        coordinator = _create_coordinator(hass, mock_config_entry)
        await coordinator._async_update_data()

        rs = coordinator.rooms["bedroom_abc"]
        assert rs["mode"] == "idle"
        assert rs["zone_id"] == "down"
        assert rs["zone_priority_active"] is False

    @pytest.mark.asyncio
    async def test_zone_room_with_own_device_not_overridden(self, hass, mock_config_entry):
        """The zone mode override is skipped for a room that owns a device.

        Bedroom sits in its idle band (22°C, between heat 18 and cool 23) so its
        own controller is idle. The thermostat is cooling its area on its own.
        A sensor-only room would be shown as cooling; a device-owning room keeps
        its own (idle) mode.
        """
        room = {
            **BEDROOM,
            "devices": [{"entity_id": "climate.bedroom_ac", "type": "ac", "role": "auto"}],
            "acs": ["climate.bedroom_ac"],
        }
        store = _make_store_mock({"bedroom_abc": room}, dict(SINGLE_ZONE_SETTINGS))
        attrs = _thermostat_attrs(hvac_action="cooling")

        def states_get(eid):
            if eid == "climate.bedroom_ac":
                s = MagicMock()
                s.state = "off"
                s.attributes = {"hvac_modes": ["off", "cool"], "hvac_action": "off"}
                return s
            return _states_get(bedroom_temp="22.0", thermostat_attrs=attrs)(eid)

        hass.states.get = MagicMock(side_effect=states_get)
        hass.services.async_call = AsyncMock()
        hass.data = {"roommind_cc": {"store": store}}
        coordinator = _create_coordinator(hass, mock_config_entry)
        await coordinator._async_update_data()

        # Device-owning room → zone override skipped → keeps its own idle mode
        assert coordinator.rooms["bedroom_abc"]["mode"] == "idle"
        assert coordinator.rooms["bedroom_abc"]["zone_id"] == "down"

    @pytest.mark.asyncio
    async def test_thermostat_in_controlled_room_is_conflict(self, hass, mock_config_entry):
        """The central thermostat must not also be a controlled room device."""
        conflicted_room = {
            **BEDROOM,
            "area_id": "living_abc",
            "temperature_sensor": "",
            "devices": [{"entity_id": "climate.downstairs", "type": "ac", "role": "auto"}],
            "acs": ["climate.downstairs"],
        }
        store = _make_store_mock(
            {"bedroom_abc": dict(BEDROOM), "living_abc": conflicted_room},
            dict(SINGLE_ZONE_SETTINGS),
        )
        hass.states.get = MagicMock(side_effect=_states_get())
        hass.services.async_call = AsyncMock()
        hass.data = {"roommind_cc": {"store": store}}

        coordinator = _create_coordinator(hass, mock_config_entry)
        await coordinator._async_update_data()

        assert coordinator.priority_zone_data["down"]["status"] == "disabled"
        assert "controlled by a RoomMind room" in coordinator.priority_zone_data["down"]["reason"]


UPSTAIRS_BEDROOM = {
    **BEDROOM,
    "area_id": "loft_xyz",
    "temperature_sensor": "sensor.loft_temp",
}

TWO_ZONE_SETTINGS = {
    "climate_control_active": True,
    "priority_zones": [
        SINGLE_ZONE_SETTINGS["priority_zones"][0],
        {
            "id": "up",
            "name": "Upstairs",
            "enabled": True,
            "thermostat_entity": "climate.upstairs",
            "zone_rooms": ["loft_xyz"],
            "priority_rooms": [{"area_id": "loft_xyz"}],
        },
    ],
}


class TestMultiZone:
    def _setup_two_zones(self, hass, *, loft_temp="25.5", upstairs_attrs=None):
        store = _make_store_mock(
            {"bedroom_abc": dict(BEDROOM), "loft_xyz": dict(UPSTAIRS_BEDROOM)},
            dict(TWO_ZONE_SETTINGS),
        )
        base = _states_get()

        def states_get(eid):
            if eid == "sensor.loft_temp":
                s = MagicMock()
                s.state = loft_temp
                s.attributes = {}
                return s
            if eid == "climate.upstairs":
                s = MagicMock()
                s.state = "cool"
                s.attributes = upstairs_attrs if upstairs_attrs is not None else _thermostat_attrs()
                return s
            return base(eid)

        hass.states.get = MagicMock(side_effect=states_get)
        hass.services.async_call = AsyncMock()
        hass.data = {"roommind_cc": {"store": store}}
        return store

    def _calls_for(self, hass, entity_id):
        return [
            c
            for c in hass.services.async_call.call_args_list
            if len(c.args) >= 3
            and c.args[0] == "climate"
            and c.args[1] == "set_temperature"
            and c.args[2].get("entity_id") == entity_id
        ]

    @pytest.mark.asyncio
    async def test_both_zones_force_independently(self, hass, mock_config_entry):
        """Downstairs and upstairs bias their own thermostats simultaneously."""
        self._setup_two_zones(hass)
        coordinator = _create_coordinator(hass, mock_config_entry)
        await coordinator._async_update_data()

        # Downstairs: bedroom error 2.0 → 23.0 - 2.3 = 20.7
        down_calls = self._calls_for(hass, "climate.downstairs")
        assert len(down_calls) == 1
        assert down_calls[0].args[2]["temperature"] == pytest.approx(20.7)
        # Upstairs: loft error 2.5 → bias (2.5-0.2)+0.5 = 2.8 → clamp 2.5 → 20.5
        up_calls = self._calls_for(hass, "climate.upstairs")
        assert len(up_calls) == 1
        assert up_calls[0].args[2]["temperature"] == pytest.approx(20.5)

        assert coordinator.priority_zone_data["down"]["status"] == "forcing_cooling"
        assert coordinator.priority_zone_data["up"]["status"] == "forcing_cooling"
        assert coordinator.priority_zone_data["up"]["active_room"] == "loft_xyz"

    @pytest.mark.asyncio
    async def test_zone_lockouts_are_independent(self, hass, mock_config_entry):
        """Ending the downstairs session must not lock out the upstairs zone."""
        self._setup_two_zones(hass, loft_temp="23.0")  # loft satisfied
        coordinator = _create_coordinator(hass, mock_config_entry)
        await coordinator._async_update_data()
        assert coordinator.priority_zone_data["down"]["forcing"] is True
        assert coordinator.priority_zone_data["up"]["forcing"] is False

        # Downstairs bedroom satisfied → restore; loft now hot → upstairs starts
        store = self._setup_two_zones(hass, loft_temp="25.5")
        base = hass.states.get.side_effect

        def states_get(eid):
            if eid == "sensor.bedroom_temp":
                s = MagicMock()
                s.state = "23.0"
                s.attributes = {}
                return s
            if eid == "climate.downstairs":
                s = MagicMock()
                s.state = "cool"
                s.attributes = _thermostat_attrs(temperature=20.7)
                return s
            return base(eid)

        hass.states.get = MagicMock(side_effect=states_get)
        hass.data = {"roommind_cc": {"store": store}}
        coordinator._zone_managers["down"]._session_started -= 3600

        await coordinator._async_update_data()

        assert coordinator.priority_zone_data["down"]["forcing"] is False
        assert coordinator.priority_zone_data["down"]["min_off_lockout"] is True
        assert coordinator.priority_zone_data["up"]["forcing"] is True

    @pytest.mark.asyncio
    async def test_each_zone_room_trains_from_its_own_thermostat(self, hass, mock_config_entry):
        """EKF labels come from the room's own zone thermostat."""
        down_attrs = _thermostat_attrs(hvac_action="cooling")
        self._setup_two_zones(hass, upstairs_attrs=_thermostat_attrs(hvac_action="idle"))
        base = hass.states.get.side_effect

        def states_get(eid):
            if eid == "climate.downstairs":
                s = MagicMock()
                s.state = "cool"
                s.attributes = down_attrs
                return s
            return base(eid)

        hass.states.get = MagicMock(side_effect=states_get)
        coordinator = _create_coordinator(hass, mock_config_entry)
        coordinator._ekf_training = MagicMock()
        await coordinator._async_update_data()

        labels = {
            c.kwargs["area_id"]: c.kwargs["ekf_mode"]
            for c in coordinator._ekf_training.process.call_args_list
        }
        assert labels["bedroom_abc"] == "cooling"  # downstairs is running
        assert labels["loft_xyz"] == "idle"  # upstairs is not

    @pytest.mark.asyncio
    async def test_removed_zone_restores_setpoint(self, hass, mock_config_entry):
        """Deleting a zone mid-session restores its thermostat setpoint."""
        store = self._setup_two_zones(hass)
        coordinator = _create_coordinator(hass, mock_config_entry)
        await coordinator._async_update_data()
        assert coordinator.priority_zone_data["up"]["forcing"] is True

        # Remove the upstairs zone from settings
        settings = dict(TWO_ZONE_SETTINGS)
        settings["priority_zones"] = [TWO_ZONE_SETTINGS["priority_zones"][0]]
        store.get_settings.return_value = {
            "outdoor_temp_sensor": "sensor.outdoor_temp",
            **settings,
        }
        # The upstairs entity now reflects the biased setpoint from cycle 1,
        # so the restore write is not deduped away.
        base = hass.states.get.side_effect

        def states_get(eid):
            if eid == "climate.upstairs":
                s = MagicMock()
                s.state = "cool"
                s.attributes = _thermostat_attrs(temperature=20.5)
                return s
            return base(eid)

        hass.states.get = MagicMock(side_effect=states_get)
        hass.services.async_call.reset_mock()

        await coordinator._async_update_data()

        up_calls = self._calls_for(hass, "climate.upstairs")
        assert len(up_calls) == 1
        assert up_calls[0].args[2]["temperature"] == pytest.approx(23.0)  # restored
        assert "up" not in coordinator.priority_zone_data
        assert "up" not in coordinator._zone_managers
