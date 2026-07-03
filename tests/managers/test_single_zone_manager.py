"""Tests for the single-zone priority room decision engine.

All temperatures are °C. The headline scenario mirrors the single-stage
residential case: bedroom (remote sensor) is hot, the thermostat's own area
is already satisfied, and the only actuator is the central setpoint.
"""

from __future__ import annotations

import pytest

from custom_components.roommind_cc.const import (
    SZ_STATUS_DISABLED,
    SZ_STATUS_FORCING_COOLING,
    SZ_STATUS_FORCING_HEATING,
    SZ_STATUS_IDLE,
)
from custom_components.roommind_cc.managers.single_zone_manager import (
    SLOT_HIGH,
    SLOT_TEMPERATURE,
    PriorityRoomConfig,
    PriorityRoomState,
    SingleZoneConfig,
    SingleZoneManager,
    ZoneSnapshot,
    zones_from_settings,
)

T0 = 1_000_000.0  # arbitrary monotonic anchor


def make_config(**overrides) -> SingleZoneConfig:
    """Config with one always-on priority bedroom and a linked main area."""
    cfg = SingleZoneConfig(
        enabled=True,
        thermostat_entity="climate.downstairs",
        priority_rooms=[PriorityRoomConfig(area_id="bedroom")],
        main_area_id="living_room",
        min_run_seconds=600,
        min_off_seconds=600,
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def make_snapshot(
    bedroom_temp=25.0,
    bedroom_cool_target=23.0,
    bedroom_heat_target=18.0,
    main_temp=23.0,
    nominal=23.0,
    hvac_mode="cool",
    thermostat_current=23.0,
    outdoor=30.0,
    **overrides,
) -> ZoneSnapshot:
    """Snapshot for the bedroom-hot / main-satisfied cooling scenario."""
    snap = ZoneSnapshot(
        thermostat_available=True,
        thermostat_hvac_mode=hvac_mode,
        thermostat_current_temp=thermostat_current,
        thermostat_setpoint=nominal,
        main_temp=main_temp,
        main_cool_target=23.0,
        main_heat_target=20.0,
        outdoor_temp=outdoor,
        rooms={
            "bedroom": PriorityRoomState(
                area_id="bedroom",
                current_temp=bedroom_temp,
                heat_target=bedroom_heat_target,
                cool_target=bedroom_cool_target,
            )
        },
    )
    for key, value in overrides.items():
        setattr(snap, key, value)
    return snap


class TestBedroomHotMainSatisfied:
    """The headline case: priority room hot, thermostat area at target."""

    def test_forces_cooling_below_nominal(self):
        mgr = SingleZoneManager()
        # Bedroom 2.0°C above target (≈77 vs 74°F), main area satisfied
        dec = mgr.evaluate(make_config(), make_snapshot(), now=T0)

        assert dec.status == SZ_STATUS_FORCING_COOLING
        assert dec.forcing is True
        assert dec.active_room == "bedroom"
        assert dec.room_error == pytest.approx(2.0)
        assert dec.command is not None
        assert dec.command.slot == SLOT_TEMPERATURE
        # dynamic bias: (2.0 - 0.2) * 1.0 + 0.5 = 2.3 → setpoint 23.0 - 2.3
        assert dec.command.value == pytest.approx(20.7)
        assert dec.setpoint == pytest.approx(20.7)
        assert dec.nominal_setpoint == pytest.approx(23.0)
        assert dec.bias == pytest.approx(2.3)

    def test_no_forcing_below_start_threshold(self):
        mgr = SingleZoneManager()
        # Error 0.5°C < start threshold 0.8°C
        dec = mgr.evaluate(make_config(), make_snapshot(bedroom_temp=23.5), now=T0)
        assert dec.status == SZ_STATUS_IDLE
        assert dec.forcing is False
        assert dec.command is None

    def test_setpoint_respects_max_offset_and_main_min(self):
        mgr = SingleZoneManager()
        # Huge error → bias clamps to max_cool_offset (2.5), then the floor
        # is max(nominal - 2.5, main_min_temp) = max(20.5, 21.0) = 21.0
        cfg = make_config(main_min_temp=21.0)
        dec = mgr.evaluate(cfg, make_snapshot(bedroom_temp=28.0), now=T0)
        assert dec.forcing is True
        assert dec.command.value == pytest.approx(21.0)

    def test_static_bias_when_dynamic_disabled(self):
        mgr = SingleZoneManager()
        cfg = make_config(dynamic_bias=False, cool_bias=1.0)
        dec = mgr.evaluate(cfg, make_snapshot(), now=T0)
        assert dec.forcing is True
        assert dec.bias == pytest.approx(1.0)
        assert dec.command.value == pytest.approx(22.0)


class TestHysteresisAndSessionEnd:
    def test_keeps_forcing_between_stop_and_start(self):
        mgr = SingleZoneManager()
        cfg = make_config()
        mgr.evaluate(cfg, make_snapshot(), now=T0)
        # Error 0.5°C: below start (0.8) but above stop (0.2) → keep forcing.
        # nominal=20.7 mimics the entity reflecting our commanded setpoint.
        dec = mgr.evaluate(cfg, make_snapshot(bedroom_temp=23.5, nominal=20.7), now=T0 + 700)
        assert dec.status == SZ_STATUS_FORCING_COOLING
        assert dec.command is not None

    def test_stops_and_restores_at_stop_threshold(self):
        mgr = SingleZoneManager()
        cfg = make_config()
        mgr.evaluate(cfg, make_snapshot(), now=T0)
        # Error 0.1°C ≤ stop threshold, past min-run → restore nominal
        dec = mgr.evaluate(cfg, make_snapshot(bedroom_temp=23.1, nominal=20.7), now=T0 + 700)
        assert dec.forcing is False
        assert dec.status == SZ_STATUS_IDLE
        assert dec.command is not None
        assert dec.command.value == pytest.approx(23.0)
        assert "stop threshold" in dec.reason

    def test_restore_behavior_leave_sends_nothing(self):
        mgr = SingleZoneManager()
        cfg = make_config(restore_behavior="leave")
        mgr.evaluate(cfg, make_snapshot(), now=T0)
        dec = mgr.evaluate(cfg, make_snapshot(bedroom_temp=23.1, nominal=20.7), now=T0 + 700)
        assert dec.forcing is False
        assert dec.command is None

    def test_setpoint_only_deepens_never_bounces(self):
        mgr = SingleZoneManager()
        cfg = make_config(max_cool_offset=5.0, main_min_temp=15.0)
        first = mgr.evaluate(cfg, make_snapshot(), now=T0)
        # Error shrinks → candidate setpoint would rise; command must hold
        dec = mgr.evaluate(cfg, make_snapshot(bedroom_temp=24.0, nominal=20.7), now=T0 + 60)
        assert dec.command.value == pytest.approx(first.command.value)
        # Error grows past the deadband → setpoint deepens
        dec2 = mgr.evaluate(cfg, make_snapshot(bedroom_temp=27.0, nominal=20.7), now=T0 + 120)
        assert dec2.command.value < first.command.value
        assert dec2.command.value == pytest.approx(18.7)  # (4.0-0.2)+0.5 bias


class TestAntiShortCycle:
    def test_min_run_holds_stop(self):
        mgr = SingleZoneManager()
        cfg = make_config()
        start = mgr.evaluate(cfg, make_snapshot(), now=T0)
        # Satisfied after 60s, but min_run is 600s → hold the setpoint
        dec = mgr.evaluate(cfg, make_snapshot(bedroom_temp=23.0, nominal=20.7), now=T0 + 60)
        assert dec.forcing is True
        assert dec.min_run_lockout is True
        assert dec.lockout_remaining_s == pytest.approx(540, abs=1)
        assert dec.command.value == pytest.approx(start.command.value)

    def test_min_off_blocks_restart(self):
        mgr = SingleZoneManager()
        cfg = make_config()
        mgr.evaluate(cfg, make_snapshot(), now=T0)
        mgr.evaluate(cfg, make_snapshot(bedroom_temp=23.0, nominal=20.7), now=T0 + 700)  # ends
        # New demand right away → blocked by min-off
        dec = mgr.evaluate(cfg, make_snapshot(bedroom_temp=26.0), now=T0 + 760)
        assert dec.forcing is False
        assert dec.min_off_lockout is True
        assert dec.active_room == "bedroom"
        # After min-off expires → forcing resumes
        dec2 = mgr.evaluate(cfg, make_snapshot(bedroom_temp=26.0), now=T0 + 1400)
        assert dec2.forcing is True


class TestMainAreaProtection:
    def test_hard_bound_stops_forcing(self):
        mgr = SingleZoneManager()
        cfg = make_config(priority_wins=True)
        mgr.evaluate(cfg, make_snapshot(), now=T0)
        # Main area hits the hard minimum (past min-run) → stop + protect
        dec = mgr.evaluate(cfg, make_snapshot(main_temp=19.9, nominal=20.7), now=T0 + 700)
        assert dec.forcing is False
        assert dec.main_protection_active is True
        assert dec.command.value == pytest.approx(23.0)  # restored

    def test_soft_arbitration_stops_when_main_overcooled(self):
        mgr = SingleZoneManager()
        cfg = make_config(priority_wins=False)
        mgr.evaluate(cfg, make_snapshot(), now=T0)
        # Main 21.0 < its own cool target 23.0 - 1.5 tolerance → stop
        dec = mgr.evaluate(cfg, make_snapshot(main_temp=21.0, nominal=20.7), now=T0 + 700)
        assert dec.forcing is False
        assert dec.main_protection_active is True

    def test_priority_wins_continues_to_hard_bound(self):
        mgr = SingleZoneManager()
        cfg = make_config(priority_wins=True)
        mgr.evaluate(cfg, make_snapshot(), now=T0)
        # Same overcooled main area, but priority_wins → keep forcing
        dec = mgr.evaluate(cfg, make_snapshot(main_temp=21.0, nominal=20.7), now=T0 + 700)
        assert dec.forcing is True

    def test_no_start_when_main_already_overcooled(self):
        mgr = SingleZoneManager()
        dec = mgr.evaluate(make_config(), make_snapshot(main_temp=21.0), now=T0)
        assert dec.forcing is False
        assert dec.main_protection_active is True

    def test_no_start_without_setpoint_authority(self):
        mgr = SingleZoneManager()
        # main_min_temp at the thermostat reading → no room to bias
        cfg = make_config(main_min_temp=23.0)
        dec = mgr.evaluate(cfg, make_snapshot(), now=T0)
        assert dec.forcing is False
        assert dec.main_protection_active is True
        assert "authority" in dec.reason


class TestGating:
    def test_occupancy_condition(self):
        cfg = make_config(priority_rooms=[PriorityRoomConfig(area_id="bedroom", condition="occupied")])
        snap = make_snapshot()
        dec = SingleZoneManager().evaluate(cfg, snap, now=T0)
        assert dec.forcing is False  # not occupied

        snap.rooms["bedroom"].occupied = True
        dec2 = SingleZoneManager().evaluate(cfg, snap, now=T0)
        assert dec2.forcing is True

    def test_schedule_condition(self):
        cfg = make_config(priority_rooms=[PriorityRoomConfig(area_id="bedroom", condition="schedule")])
        snap = make_snapshot()
        snap.rooms["bedroom"].schedule_on = False
        assert SingleZoneManager().evaluate(cfg, snap, now=T0).forcing is False
        snap.rooms["bedroom"].schedule_on = True
        assert SingleZoneManager().evaluate(cfg, snap, now=T0).forcing is True

    def test_sleep_condition(self):
        cfg = make_config(priority_rooms=[PriorityRoomConfig(area_id="bedroom", condition="sleep")])
        assert SingleZoneManager().evaluate(cfg, make_snapshot(sleep_mode_on=False), now=T0).forcing is False
        assert SingleZoneManager().evaluate(cfg, make_snapshot(sleep_mode_on=True), now=T0).forcing is True

    def test_largest_error_room_wins(self):
        cfg = make_config(
            priority_rooms=[
                PriorityRoomConfig(area_id="bedroom"),
                PriorityRoomConfig(area_id="office"),
            ]
        )
        snap = make_snapshot()
        snap.rooms["office"] = PriorityRoomState(
            area_id="office",
            current_temp=26.5,
            cool_target=23.0,
        )
        dec = SingleZoneManager().evaluate(cfg, snap, now=T0)
        assert dec.active_room == "office"
        assert dec.room_error == pytest.approx(3.5)


class TestThermostatAndOutdoorGates:
    def test_no_cooling_when_thermostat_in_heat_mode(self):
        dec = SingleZoneManager().evaluate(make_config(), make_snapshot(hvac_mode="heat"), now=T0)
        assert dec.forcing is False

    def test_no_forcing_when_thermostat_off(self):
        dec = SingleZoneManager().evaluate(make_config(), make_snapshot(hvac_mode="off"), now=T0)
        assert dec.forcing is False

    def test_outdoor_gate_blocks_cooling(self):
        # Outdoor 10°C < outdoor_cooling_min 16°C → never force cooling
        dec = SingleZoneManager().evaluate(make_config(), make_snapshot(outdoor=10.0), now=T0)
        assert dec.forcing is False

    def test_heat_cool_thermostat_uses_high_slot(self):
        snap = make_snapshot(hvac_mode="heat_cool")
        snap.thermostat_setpoint = None
        snap.thermostat_setpoint_low = 20.0
        snap.thermostat_setpoint_high = 23.0
        dec = SingleZoneManager().evaluate(make_config(), snap, now=T0)
        assert dec.forcing is True
        assert dec.command.slot == SLOT_HIGH
        # Raw bias would give 20.7, but the deadband keeps the cool setpoint
        # ≥ heat setpoint (20.0) + 1.0°C → clamped to 21.0.
        assert dec.command.value == pytest.approx(21.0)

    def test_disabled_returns_disabled_status(self):
        dec = SingleZoneManager().evaluate(make_config(enabled=False), make_snapshot(), now=T0)
        assert dec.status == SZ_STATUS_DISABLED

    def test_disable_mid_session_restores(self):
        mgr = SingleZoneManager()
        mgr.evaluate(make_config(), make_snapshot(), now=T0)
        dec = mgr.evaluate(make_config(enabled=False), make_snapshot(), now=T0 + 60)
        assert dec.forcing is False
        assert dec.status == SZ_STATUS_DISABLED
        assert dec.command is not None
        assert dec.command.value == pytest.approx(23.0)

    def test_thermostat_conflict_blocks(self):
        dec = SingleZoneManager().evaluate(make_config(), make_snapshot(thermostat_conflict=True), now=T0)
        assert dec.status == SZ_STATUS_DISABLED
        assert "controlled by a RoomMind room" in dec.reason


class TestSharedSystemConflict:
    """One central thermostat can't cool the bedroom while heating the house."""

    def test_no_cooling_when_system_is_heating(self):
        # heat_cool thermostat actively heating its own area
        snap = make_snapshot(hvac_mode="heat_cool")
        snap.thermostat_hvac_action = "heating"
        snap.thermostat_setpoint = None
        snap.thermostat_setpoint_low = 21.0
        snap.thermostat_setpoint_high = 24.0
        dec = SingleZoneManager().evaluate(make_config(), snap, now=T0)
        assert dec.forcing is False
        assert dec.main_protection_active is True
        assert "heating its own area" in dec.reason

    def test_no_cooling_when_main_at_heat_setpoint(self):
        # Not heating yet, but the main area sits at the heat setpoint, so a
        # heat call is imminent — forcing cooling would fight it.
        snap = make_snapshot(hvac_mode="heat_cool", main_temp=21.0)
        snap.thermostat_setpoint = None
        snap.thermostat_setpoint_low = 21.0
        snap.thermostat_setpoint_high = 24.0
        dec = SingleZoneManager().evaluate(make_config(), snap, now=T0)
        assert dec.forcing is False
        assert "heat setpoint" in dec.reason

    def test_cool_setpoint_kept_above_heat_setpoint(self):
        # Large error would push the cool setpoint below the heat setpoint;
        # the deadband clamp keeps it at heat_low + 1.0°C.
        snap = make_snapshot(hvac_mode="heat_cool", bedroom_temp=28.0, main_temp=25.0)
        snap.thermostat_setpoint = None
        snap.thermostat_setpoint_low = 20.0
        snap.thermostat_setpoint_high = 24.0
        cfg = make_config(max_cool_offset=8.0, main_min_temp=15.0)
        dec = SingleZoneManager().evaluate(cfg, snap, now=T0)
        assert dec.forcing is True
        assert dec.command.slot == SLOT_HIGH
        assert dec.command.value == pytest.approx(21.0)  # 20.0 low + 1.0 deadband

    def test_configurable_deadband_widens_the_gap(self):
        # A 3°F (~1.7°C) T6 changeover deadband → cool setpoint clamps to
        # heat_low + 1.7 instead of the default 1.0.
        snap = make_snapshot(hvac_mode="heat_cool", bedroom_temp=28.0, main_temp=25.0)
        snap.thermostat_setpoint = None
        snap.thermostat_setpoint_low = 20.0
        snap.thermostat_setpoint_high = 24.0
        cfg = make_config(max_cool_offset=8.0, main_min_temp=15.0, band_deadband=1.7)
        dec = SingleZoneManager().evaluate(cfg, snap, now=T0)
        assert dec.forcing is True
        assert dec.command.value == pytest.approx(21.7)  # 20.0 + 1.7

    def test_session_ends_when_main_drops_to_heat_setpoint(self):
        # Setpoint-based mid-session stop: the main area falls onto its heat
        # setpoint → a heat call is imminent → stand down. priority_wins keeps
        # the main-area soft arbitration out of the way so we isolate this path.
        mgr = SingleZoneManager()
        cfg = make_config(priority_wins=True, main_min_temp=15.0)
        snap = make_snapshot(hvac_mode="heat_cool")
        snap.thermostat_setpoint = None
        snap.thermostat_setpoint_low = 18.0
        snap.thermostat_setpoint_high = 23.0
        mgr.evaluate(cfg, snap, now=T0)
        assert mgr.forcing is True
        # Main area now sits at the heat setpoint (20.0)
        snap2 = make_snapshot(hvac_mode="heat_cool", main_temp=20.1, nominal=20.7)
        snap2.thermostat_setpoint = None
        snap2.thermostat_setpoint_low = 20.0
        snap2.thermostat_setpoint_high = 20.7
        dec = mgr.evaluate(cfg, snap2, now=T0 + 700)
        assert dec.forcing is False
        assert dec.main_protection_active is True
        assert "heat setpoint" in dec.reason

    @staticmethod
    def _ongoing_snap(mgr, action, *, bedroom_temp=25.0):
        """A heat_cool snapshot mid-session whose high setpoint matches what the
        manager actually commanded (so manual-override detection stays quiet)."""
        s = make_snapshot(hvac_mode="heat_cool", bedroom_temp=bedroom_temp, main_temp=22.0)
        s.thermostat_hvac_action = action
        s.thermostat_setpoint = None
        s.thermostat_setpoint_low = 18.0
        s.thermostat_setpoint_high = mgr._commanded
        return s

    def test_opposite_action_aborts_after_grace(self):
        # Outcome-based safety net: the thermostat reports it's heating while
        # we force cooling, but the main area is NOT at the heat setpoint
        # (setpoint guard doesn't fire). We tolerate a changeover window, then
        # abort — bypassing min-run.
        mgr = SingleZoneManager()
        cfg = make_config()  # min_run 600s
        snap = make_snapshot(hvac_mode="heat_cool", main_temp=22.0)
        snap.thermostat_setpoint = None
        snap.thermostat_setpoint_low = 18.0
        snap.thermostat_setpoint_high = 23.0
        mgr.evaluate(cfg, snap, now=T0)
        assert mgr.forcing is True

        # First cycle seeing the opposite: within grace → keep holding
        dec1 = mgr.evaluate(cfg, self._ongoing_snap(mgr, "heating"), now=T0 + 30)
        assert dec1.forcing is True
        # Past the 180s grace: abort + restore even though min-run (600s) is
        # not met — safety overrides the compressor hold.
        dec2 = mgr.evaluate(cfg, self._ongoing_snap(mgr, "heating"), now=T0 + 220)
        assert dec2.forcing is False
        assert dec2.min_run_lockout is False
        assert dec2.main_protection_active is True
        assert "despite the forced setpoint" in dec2.reason

    def test_transient_opposite_action_does_not_abort(self):
        # A brief opposite reading that clears before the grace must NOT abort.
        mgr = SingleZoneManager()
        cfg = make_config()
        snap = make_snapshot(hvac_mode="heat_cool", main_temp=22.0)
        snap.thermostat_setpoint = None
        snap.thermostat_setpoint_low = 18.0
        snap.thermostat_setpoint_high = 23.0
        mgr.evaluate(cfg, snap, now=T0)

        # Transient opposite reading
        assert mgr.evaluate(cfg, self._ongoing_snap(mgr, "heating"), now=T0 + 30).forcing is True
        # Clears back to cooling before the grace expires → timer resets
        assert mgr.evaluate(cfg, self._ongoing_snap(mgr, "cooling"), now=T0 + 60).forcing is True
        # Well past the original window, still forcing (timer was reset)
        assert mgr.evaluate(cfg, self._ongoing_snap(mgr, "cooling"), now=T0 + 400).forcing is True

    def test_absolute_setpoint_backstop(self):
        # A pathological config can't drive the setpoint below the 7°C floor.
        # priority_wins bypasses the main-area soft arbitration so we exercise
        # the absolute clamp itself.
        snap = make_snapshot(bedroom_temp=40.0, main_temp=8.0, nominal=8.0)
        cfg = make_config(max_cool_offset=50.0, main_min_temp=0.0, priority_wins=True)
        dec = SingleZoneManager().evaluate(cfg, snap, now=T0)
        assert dec.forcing is True
        assert dec.command.value >= 7.0

    def test_no_heating_when_system_is_cooling(self):
        snap = make_snapshot(
            hvac_mode="heat_cool",
            bedroom_temp=20.0,
            bedroom_heat_target=22.0,
            bedroom_cool_target=26.0,
            main_temp=24.0,
            outdoor=5.0,
        )
        snap.thermostat_hvac_action = "cooling"
        snap.thermostat_setpoint = None
        snap.thermostat_setpoint_low = 19.0
        snap.thermostat_setpoint_high = 23.0
        dec = SingleZoneManager().evaluate(make_config(), snap, now=T0)
        assert dec.forcing is False
        assert "cooling its own area" in dec.reason

    def test_cooling_allowed_when_system_idle(self):
        # heat_cool, idle action, main comfortably above heat setpoint → OK
        snap = make_snapshot(hvac_mode="heat_cool", main_temp=23.0)
        snap.thermostat_hvac_action = "idle"
        snap.thermostat_setpoint = None
        snap.thermostat_setpoint_low = 19.0
        snap.thermostat_setpoint_high = 23.0
        dec = SingleZoneManager().evaluate(make_config(), snap, now=T0)
        assert dec.forcing is True
        assert dec.direction == "cool"


class TestManualOverride:
    def test_manual_setpoint_change_aborts_without_restore(self):
        mgr = SingleZoneManager()
        cfg = make_config()
        mgr.evaluate(cfg, make_snapshot(), now=T0)  # commands 20.7
        # Past the grace period the thermostat reports a user-set 22.5
        snap = make_snapshot(nominal=22.5)
        dec = mgr.evaluate(cfg, snap, now=T0 + 120)
        assert dec.forcing is False
        assert dec.command is None  # user takes over — no restore
        assert "manual" in dec.reason

    def test_setpoint_drift_ignored_within_grace(self):
        mgr = SingleZoneManager()
        cfg = make_config()
        mgr.evaluate(cfg, make_snapshot(), now=T0)
        # 30s later the entity still shows the pre-force setpoint (slow device)
        dec = mgr.evaluate(cfg, make_snapshot(nominal=23.0), now=T0 + 30)
        assert dec.forcing is True


class TestDynamicBiasLearning:
    def test_slow_priority_room_gets_deeper_bias(self):
        snap = make_snapshot()
        # Bedroom responds at 0.8°C/h, main area at 1.6°C/h → ratio 2.0
        snap.cool_rates = {"bedroom": 0.8, "living_room": 1.6}
        dec = SingleZoneManager().evaluate(make_config(), snap, now=T0)
        assert dec.forcing is True
        assert dec.learned_ratio == pytest.approx(2.0)
        assert dec.response_rate == pytest.approx(0.8)
        # bias (2.0-0.2)*2 + 0.5 = 4.1 → clamped to max offset 2.5
        assert dec.bias == pytest.approx(2.5)
        assert dec.command.value == pytest.approx(20.5)

    def test_missing_rates_fall_back_to_unity_ratio(self):
        dec = SingleZoneManager().evaluate(make_config(), make_snapshot(), now=T0)
        assert dec.learned_ratio is None
        assert dec.bias == pytest.approx(2.3)  # (2.0-0.2)*1.0 + 0.5


class TestHeatingDirection:
    def test_forces_heating_above_nominal(self):
        snap = make_snapshot(
            bedroom_temp=20.0,
            bedroom_heat_target=22.0,
            bedroom_cool_target=26.0,
            main_temp=21.5,
            nominal=21.5,
            hvac_mode="heat",
            thermostat_current=21.5,
            outdoor=5.0,
        )
        dec = SingleZoneManager().evaluate(make_config(), snap, now=T0)
        assert dec.status == SZ_STATUS_FORCING_HEATING
        assert dec.direction == "heat"
        assert dec.room_error == pytest.approx(2.0)
        # bias (2.0-0.2)*1.0 + 0.5 = 2.3 → setpoint 21.5 + 2.3 = 23.8
        assert dec.command.value == pytest.approx(23.8)

    def test_heating_respects_main_max(self):
        snap = make_snapshot(
            bedroom_temp=18.0,
            bedroom_heat_target=22.0,
            bedroom_cool_target=26.0,
            main_temp=21.5,
            nominal=21.5,
            hvac_mode="heat",
            thermostat_current=21.5,
            outdoor=5.0,
        )
        cfg = make_config(main_max_temp=22.5)
        dec = SingleZoneManager().evaluate(cfg, snap, now=T0)
        assert dec.forcing is True
        assert dec.command.value == pytest.approx(22.5)


class TestConfigParsing:
    def test_zones_from_settings_empty(self):
        assert zones_from_settings({}) == []

    def test_from_zone_defaults(self):
        cfg = SingleZoneConfig.from_zone({"id": "z1"}, {})
        assert cfg.id == "z1"
        assert cfg.enabled is False
        assert cfg.priority_rooms == []

    def test_zones_from_settings_full(self):
        zones = zones_from_settings(
            {
                "climate_control_active": True,
                "outdoor_cooling_min": 18.0,
                "priority_zones": [
                    {
                        "id": "down",
                        "name": "Downstairs",
                        "enabled": True,
                        "thermostat_entity": "climate.downstairs",
                        "priority_rooms": [{"area_id": "bedroom", "condition": "sleep"}],
                        "min_run_minutes": 15,
                        "priority_wins": True,
                    },
                    {
                        "id": "up",
                        "name": "Upstairs",
                        "enabled": True,
                        "thermostat_entity": "climate.upstairs",
                        "priority_rooms": [{"area_id": "loft"}],
                    },
                ],
            }
        )
        assert [z.id for z in zones] == ["down", "up"]
        cfg = zones[0]
        assert cfg.name == "Downstairs"
        assert cfg.enabled is True
        assert cfg.thermostat_entity == "climate.downstairs"
        assert cfg.priority_rooms[0].condition == "sleep"
        assert cfg.min_run_seconds == 900
        assert cfg.priority_wins is True
        assert cfg.outdoor_cooling_min == 18.0
        assert cfg.band_deadband == 1.0  # default when unset
        assert zones[1].thermostat_entity == "climate.upstairs"
        assert zones[1].priority_rooms[0].condition == "always"

    def test_zone_without_id_is_skipped(self):
        zones = zones_from_settings({"priority_zones": [{"thermostat_entity": "climate.x"}]})
        assert zones == []

    def test_global_climate_kill_switch_disables(self):
        zones = zones_from_settings(
            {
                "climate_control_active": False,
                "priority_zones": [{"id": "z1", "enabled": True, "thermostat_entity": "climate.x"}],
            }
        )
        assert zones[0].enabled is False

    def test_independent_manager_state_per_zone(self):
        """Two zones force independently — timers and sessions never interact."""
        down = SingleZoneManager()
        up = SingleZoneManager()
        cfg = make_config()
        down.evaluate(cfg, make_snapshot(), now=T0)
        assert down.forcing is True
        assert up.forcing is False
        # Ending downstairs starts ITS off-lockout only; upstairs can start
        down.evaluate(cfg, make_snapshot(bedroom_temp=23.0, nominal=20.7), now=T0 + 700)
        dec_up = up.evaluate(cfg, make_snapshot(), now=T0 + 710)
        assert dec_up.forcing is True
        dec_down = down.evaluate(cfg, make_snapshot(bedroom_temp=26.0), now=T0 + 720)
        assert dec_down.forcing is False
        assert dec_down.min_off_lockout is True
