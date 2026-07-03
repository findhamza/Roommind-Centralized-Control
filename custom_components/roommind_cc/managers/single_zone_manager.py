"""Single-zone priority room supervisor.

For homes with one central thermostat and no per-room actuators (single-stage
ducted systems), RoomMind can bias the central thermostat setpoint to force
HVAC runtime when a *priority room* — measured by a remote sensor — is
uncomfortable, even when the thermostat's own area is already satisfied.

Layering (keep it this way):
  - Comfort calculation stays upstream: the priority room's heat/cool targets
    arrive here already resolved by the coordinator (schedules, overrides,
    presence, eco, mold).
  - This module makes the *decision*: a pure state machine with no Home
    Assistant imports.  All temperatures are °C.
  - The coordinator translates the decision into climate service calls and
    builds the ``ZoneSnapshot`` from live entity state.

Session model: a *forcing session* starts when a gated priority room's error
exceeds the start threshold.  The thermostat's current setpoint is captured as
*nominal*; a biased setpoint is commanded; the session ends (setpoint
restored) when the error falls below the stop threshold.  Min-run holds every
comfort-driven stop (the setpoint floor already bounds overshoot; compressor
protection is absolute), min-off blocks the next session.  A manual setpoint
change on the thermostat aborts the session without restoring.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from time import monotonic

from ..const import (
    DEFAULT_OUTDOOR_COOLING_MIN,
    DEFAULT_OUTDOOR_HEATING_MAX,
    DEFAULT_SZ_BAND_DEADBAND,
    DEFAULT_SZ_COOL_START_THRESHOLD,
    DEFAULT_SZ_COOL_STOP_THRESHOLD,
    DEFAULT_SZ_HEAT_START_THRESHOLD,
    DEFAULT_SZ_HEAT_STOP_THRESHOLD,
    DEFAULT_SZ_MAIN_MAX_TEMP,
    DEFAULT_SZ_MAIN_MIN_TEMP,
    DEFAULT_SZ_MAX_OFFSET,
    DEFAULT_SZ_MIN_OFF_MINUTES,
    DEFAULT_SZ_MIN_RUN_MINUTES,
    DEFAULT_SZ_STATIC_BIAS,
    SZ_ABS_MAX_SETPOINT,
    SZ_ABS_MIN_SETPOINT,
    SZ_CONDITION_ALWAYS,
    SZ_CONDITION_OCCUPIED,
    SZ_CONDITION_SCHEDULE,
    SZ_CONDITION_SLEEP,
    SZ_MAIN_OVERCOOL_TOLERANCE,
    SZ_MANUAL_CHANGE_TOLERANCE,
    SZ_MANUAL_GRACE_SECONDS,
    SZ_MIN_BIAS,
    SZ_OPPOSITE_ACTION_GRACE_SECONDS,
    SZ_RATE_RATIO_MAX,
    SZ_RATE_RATIO_MIN,
    SZ_SETPOINT_DEADBAND,
    SZ_STATUS_DISABLED,
    SZ_STATUS_FORCING_COOLING,
    SZ_STATUS_FORCING_HEATING,
    SZ_STATUS_IDLE,
    SZ_TRIGGER_MARGIN,
)

_LOGGER = logging.getLogger(__name__)

DIRECTION_COOL = "cool"
DIRECTION_HEAT = "heat"

# Setpoint slots on the thermostat entity
SLOT_TEMPERATURE = "temperature"  # single-setpoint thermostats
SLOT_HIGH = "high"  # target_temp_high on heat_cool/auto thermostats
SLOT_LOW = "low"  # target_temp_low on heat_cool/auto thermostats


@dataclass
class PriorityRoomConfig:
    """One room allowed to force central HVAC runtime."""

    area_id: str
    condition: str = SZ_CONDITION_ALWAYS
    schedule_entity: str = ""


@dataclass
class SingleZoneConfig:
    """Parsed configuration for one priority zone (all temperatures °C).

    A home may have several independent zones (e.g. downstairs and upstairs,
    each with its own thermostat and compressor). Each zone gets its own
    config and its own :class:`SingleZoneManager` instance, so forcing
    sessions and anti-short-cycle timers never interact across zones.
    """

    id: str = ""
    name: str = ""
    enabled: bool = False
    thermostat_entity: str = ""
    zone_rooms: list[str] = field(default_factory=list)
    main_area_id: str = ""
    main_temp_sensor: str = ""
    priority_rooms: list[PriorityRoomConfig] = field(default_factory=list)
    sleep_mode_entity: str = ""
    cool_start_threshold: float = DEFAULT_SZ_COOL_START_THRESHOLD
    cool_stop_threshold: float = DEFAULT_SZ_COOL_STOP_THRESHOLD
    heat_start_threshold: float = DEFAULT_SZ_HEAT_START_THRESHOLD
    heat_stop_threshold: float = DEFAULT_SZ_HEAT_STOP_THRESHOLD
    cool_bias: float = DEFAULT_SZ_STATIC_BIAS
    heat_bias: float = DEFAULT_SZ_STATIC_BIAS
    max_cool_offset: float = DEFAULT_SZ_MAX_OFFSET
    max_heat_offset: float = DEFAULT_SZ_MAX_OFFSET
    main_min_temp: float = DEFAULT_SZ_MAIN_MIN_TEMP
    main_max_temp: float = DEFAULT_SZ_MAIN_MAX_TEMP
    min_run_seconds: float = DEFAULT_SZ_MIN_RUN_MINUTES * 60
    min_off_seconds: float = DEFAULT_SZ_MIN_OFF_MINUTES * 60
    band_deadband: float = DEFAULT_SZ_BAND_DEADBAND
    dynamic_bias: bool = True
    priority_wins: bool = False
    restore_behavior: str = "restore"
    outdoor_cooling_min: float = DEFAULT_OUTDOOR_COOLING_MIN
    outdoor_heating_max: float = DEFAULT_OUTDOOR_HEATING_MAX

    @classmethod
    def from_zone(cls, sz: dict, settings: dict) -> SingleZoneConfig:
        """Build config from one ``priority_zones`` entry + global settings.

        ``enabled`` also honours the global climate-control kill switch so
        zone forcing never touches devices in learn-only mode.
        """
        return cls(
            id=sz.get("id", ""),
            name=sz.get("name", ""),
            enabled=bool(sz.get("enabled", False)) and settings.get("climate_control_active", True),
            thermostat_entity=sz.get("thermostat_entity", ""),
            zone_rooms=list(sz.get("zone_rooms", [])),
            main_area_id=sz.get("main_area_id", ""),
            main_temp_sensor=sz.get("main_temp_sensor", ""),
            priority_rooms=[
                PriorityRoomConfig(
                    area_id=p["area_id"],
                    condition=p.get("condition", SZ_CONDITION_ALWAYS),
                    schedule_entity=p.get("schedule_entity", ""),
                )
                for p in sz.get("priority_rooms", [])
                if p.get("area_id")
            ],
            sleep_mode_entity=sz.get("sleep_mode_entity", ""),
            cool_start_threshold=float(sz.get("cool_start_threshold", DEFAULT_SZ_COOL_START_THRESHOLD)),
            cool_stop_threshold=float(sz.get("cool_stop_threshold", DEFAULT_SZ_COOL_STOP_THRESHOLD)),
            heat_start_threshold=float(sz.get("heat_start_threshold", DEFAULT_SZ_HEAT_START_THRESHOLD)),
            heat_stop_threshold=float(sz.get("heat_stop_threshold", DEFAULT_SZ_HEAT_STOP_THRESHOLD)),
            cool_bias=float(sz.get("cool_bias", DEFAULT_SZ_STATIC_BIAS)),
            heat_bias=float(sz.get("heat_bias", DEFAULT_SZ_STATIC_BIAS)),
            max_cool_offset=float(sz.get("max_cool_offset", DEFAULT_SZ_MAX_OFFSET)),
            max_heat_offset=float(sz.get("max_heat_offset", DEFAULT_SZ_MAX_OFFSET)),
            main_min_temp=float(sz.get("main_min_temp", DEFAULT_SZ_MAIN_MIN_TEMP)),
            main_max_temp=float(sz.get("main_max_temp", DEFAULT_SZ_MAIN_MAX_TEMP)),
            min_run_seconds=float(sz.get("min_run_minutes", DEFAULT_SZ_MIN_RUN_MINUTES)) * 60,
            min_off_seconds=float(sz.get("min_off_minutes", DEFAULT_SZ_MIN_OFF_MINUTES)) * 60,
            band_deadband=float(sz.get("band_deadband", DEFAULT_SZ_BAND_DEADBAND)),
            dynamic_bias=bool(sz.get("dynamic_bias", True)),
            priority_wins=bool(sz.get("priority_wins", False)),
            restore_behavior=sz.get("restore_behavior", "restore"),
            outdoor_cooling_min=float(settings.get("outdoor_cooling_min", DEFAULT_OUTDOOR_COOLING_MIN)),
            outdoor_heating_max=float(settings.get("outdoor_heating_max", DEFAULT_OUTDOOR_HEATING_MAX)),
        )


def zones_from_settings(settings: dict) -> list[SingleZoneConfig]:
    """Parse all configured priority zones from the global settings dict."""
    return [SingleZoneConfig.from_zone(z, settings) for z in settings.get("priority_zones") or [] if z.get("id")]


@dataclass
class PriorityRoomState:
    """Live state of one priority room (built by the coordinator)."""

    area_id: str
    current_temp: float | None = None
    heat_target: float | None = None
    cool_target: float | None = None
    occupied: bool = False
    schedule_on: bool = True


@dataclass
class ZoneSnapshot:
    """Everything the decision engine needs for one cycle (all °C)."""

    thermostat_available: bool = False
    thermostat_hvac_mode: str | None = None
    thermostat_hvac_action: str | None = None  # "heating"/"cooling"/"idle"/... — what it's doing now
    thermostat_current_temp: float | None = None
    thermostat_setpoint: float | None = None  # "temperature" attribute
    thermostat_setpoint_low: float | None = None  # heat_cool thermostats
    thermostat_setpoint_high: float | None = None
    thermostat_conflict: bool = False  # thermostat is a device of a RoomMind-controlled room
    main_temp: float | None = None
    main_heat_target: float | None = None
    main_cool_target: float | None = None
    outdoor_temp: float | None = None
    sleep_mode_on: bool = False
    rooms: dict[str, PriorityRoomState] = field(default_factory=dict)
    # Learned response rates (°C/h at full power), only for calibrated rooms
    heat_rates: dict[str, float] = field(default_factory=dict)
    cool_rates: dict[str, float] = field(default_factory=dict)


@dataclass
class SetpointCommand:
    """One thermostat setpoint write (value in °C)."""

    slot: str  # SLOT_TEMPERATURE | SLOT_HIGH | SLOT_LOW
    value: float


@dataclass
class SingleZoneDecision:
    """Outcome of one evaluation cycle — also the transparency payload."""

    status: str = SZ_STATUS_IDLE
    forcing: bool = False
    direction: str | None = None
    active_room: str | None = None
    room_error: float | None = None
    bias: float | None = None
    setpoint: float | None = None  # currently commanded biased setpoint (°C)
    nominal_setpoint: float | None = None
    command: SetpointCommand | None = None
    reason: str = ""
    min_run_lockout: bool = False
    min_off_lockout: bool = False
    lockout_remaining_s: float = 0.0
    main_protection_active: bool = False
    learned_ratio: float | None = None
    response_rate: float | None = None  # active room's learned rate (°C/h)

    def as_dict(self) -> dict:
        """Serialize for coordinator state / websocket / sensor attributes."""
        return {
            "status": self.status,
            "forcing": self.forcing,
            "direction": self.direction,
            "active_room": self.active_room,
            "room_error": round(self.room_error, 2) if self.room_error is not None else None,
            "bias": round(self.bias, 2) if self.bias is not None else None,
            "setpoint": round(self.setpoint, 2) if self.setpoint is not None else None,
            "nominal_setpoint": (round(self.nominal_setpoint, 2) if self.nominal_setpoint is not None else None),
            "reason": self.reason,
            "min_run_lockout": self.min_run_lockout,
            "min_off_lockout": self.min_off_lockout,
            "lockout_remaining_s": round(self.lockout_remaining_s),
            "main_protection_active": self.main_protection_active,
            "learned_ratio": (round(self.learned_ratio, 2) if self.learned_ratio is not None else None),
            "response_rate": (round(self.response_rate, 2) if self.response_rate is not None else None),
        }


@dataclass
class _Demand:
    """A gated priority-room demand for one direction."""

    area_id: str
    direction: str
    error: float


class SingleZoneManager:
    """State machine deciding when/how to bias the central thermostat."""

    def __init__(self) -> None:
        self._forcing = False
        self._direction: str = DIRECTION_COOL
        self._active_room: str | None = None
        self._slot: str = SLOT_TEMPERATURE
        self._nominal: float | None = None
        self._commanded: float | None = None
        self._session_started: float = 0.0
        self._last_session_end: float | None = None
        # Outcome-based safety net: when did the thermostat start doing the
        # opposite of what we're forcing (mode-agnostic). None = not opposite.
        self._opposite_since: float | None = None

    @property
    def forcing(self) -> bool:
        """True while a forcing session is active."""
        return self._forcing

    # ------------------------------------------------------------------
    # Evaluation entry point
    # ------------------------------------------------------------------

    def evaluate(
        self,
        config: SingleZoneConfig,
        snapshot: ZoneSnapshot,
        now: float | None = None,
    ) -> SingleZoneDecision:
        """Run one decision cycle. Returns the decision incl. any command."""
        if now is None:
            now = monotonic()

        if not config.enabled or not config.thermostat_entity:
            if self._forcing:
                return self._end_session(config, now, restore=True, reason="single-zone mode disabled")
            return SingleZoneDecision(status=SZ_STATUS_DISABLED, reason="single-zone mode disabled")

        if snapshot.thermostat_conflict:
            if self._forcing:
                return self._end_session(
                    config,
                    now,
                    restore=True,
                    reason="thermostat is controlled by a RoomMind room — remove it or disable that room",
                )
            return SingleZoneDecision(
                status=SZ_STATUS_DISABLED,
                reason="thermostat is controlled by a RoomMind room — remove it or disable that room",
            )

        if not snapshot.thermostat_available:
            if self._forcing:
                # Cannot restore an unavailable entity; drop the session and
                # start the off-lockout so we don't hammer it on return.
                return self._end_session(config, now, restore=False, reason="thermostat unavailable")
            return SingleZoneDecision(status=SZ_STATUS_IDLE, reason="thermostat unavailable")

        if self._forcing:
            return self._evaluate_forcing(config, snapshot, now)
        return self._evaluate_idle(config, snapshot, now)

    # ------------------------------------------------------------------
    # Idle: look for a reason to start forcing
    # ------------------------------------------------------------------

    def _evaluate_idle(
        self,
        config: SingleZoneConfig,
        snapshot: ZoneSnapshot,
        now: float,
    ) -> SingleZoneDecision:
        demands = [
            d
            for d in self._gated_demands(config, snapshot)
            if d.error >= self._start_threshold(config, d.direction)
            and d.direction in self._allowed_directions(config, snapshot)
        ]
        if not demands:
            return self._idle_decision(config, now, reason="no priority room demand")

        best = max(demands, key=lambda d: d.error)

        # Min-off lockout: demand exists but we recently ended a session
        off_remaining = self._off_lockout_remaining(config, now)
        if off_remaining > 0:
            dec = self._idle_decision(
                config,
                now,
                reason=(f"'{best.area_id}' needs {best.direction}ing but min off-time is active"),
            )
            dec.active_room = best.area_id
            dec.room_error = best.error
            return dec

        sp, bias, ratio, rate, protection, why = self._compute_setpoint(config, snapshot, best, nominal=None)
        if sp is None:
            dec = self._idle_decision(config, now, reason=why)
            dec.active_room = best.area_id
            dec.room_error = best.error
            dec.main_protection_active = protection
            dec.learned_ratio = ratio
            dec.response_rate = rate
            return dec

        # Start the session
        nominal, slot = self._nominal_for(best.direction, snapshot)
        self._forcing = True
        self._direction = best.direction
        self._active_room = best.area_id
        self._slot = slot
        self._nominal = nominal
        self._commanded = sp
        self._session_started = now
        self._opposite_since = None

        status = SZ_STATUS_FORCING_COOLING if best.direction == DIRECTION_COOL else SZ_STATUS_FORCING_HEATING
        _LOGGER.info(
            "Single-zone: forcing %sing for '%s' (error %.2f°C, bias %.2f°C, setpoint %.1f→%.1f°C)",
            best.direction,
            best.area_id,
            best.error,
            bias,
            nominal if nominal is not None else float("nan"),
            sp,
        )
        return SingleZoneDecision(
            status=status,
            forcing=True,
            direction=best.direction,
            active_room=best.area_id,
            room_error=best.error,
            bias=bias,
            setpoint=sp,
            nominal_setpoint=nominal,
            command=SetpointCommand(slot=slot, value=sp),
            reason=(f"'{best.area_id}' is {best.error:.1f}°C past its {best.direction} target"),
            learned_ratio=ratio,
            response_rate=rate,
        )

    # ------------------------------------------------------------------
    # Forcing: continue, deepen, or end the session
    # ------------------------------------------------------------------

    def _evaluate_forcing(
        self,
        config: SingleZoneConfig,
        snapshot: ZoneSnapshot,
        now: float,
    ) -> SingleZoneDecision:
        direction = self._direction
        commanded = self._commanded
        if commanded is None:  # invariant: set at session start; defensive only
            return self._end_session(config, now, restore=True, reason="internal state lost")

        # Thermostat mode changed away from our direction → abort with restore
        if direction not in self._allowed_directions(config, snapshot, outdoor_gated=False):
            return self._end_session(config, now, restore=True, reason="thermostat mode changed")

        # Manual setpoint change → user takes over, abort without restore
        current_sp = self._snapshot_setpoint(snapshot, self._slot)
        if (
            now - self._session_started > SZ_MANUAL_GRACE_SECONDS
            and current_sp is not None
            and abs(current_sp - commanded) > SZ_MANUAL_CHANGE_TOLERANCE
        ):
            return self._end_session(
                config,
                now,
                restore=False,
                reason="manual thermostat adjustment detected — standing down",
            )

        # Current demand in the active direction (room with the largest error)
        demands = [d for d in self._gated_demands(config, snapshot) if d.direction == direction]
        best = max(demands, key=lambda d: d.error) if demands else None

        stop_reason: str | None = None
        protection = False
        if best is None:
            stop_reason = "no gated priority room demand remains"
        elif best.error <= self._stop_threshold(config, direction):
            stop_reason = f"'{best.area_id}' reached its {direction} stop threshold"

        # Main-area protection (hard bound, then soft arbitration)
        main = snapshot.main_temp
        if main is not None:
            if direction == DIRECTION_COOL and main <= config.main_min_temp:
                stop_reason = f"main area at minimum bound ({config.main_min_temp:.1f}°C)"
                protection = True
            elif direction == DIRECTION_HEAT and main >= config.main_max_temp:
                stop_reason = f"main area at maximum bound ({config.main_max_temp:.1f}°C)"
                protection = True
            elif not config.priority_wins:
                if (
                    direction == DIRECTION_COOL
                    and snapshot.main_cool_target is not None
                    and main < snapshot.main_cool_target - SZ_MAIN_OVERCOOL_TOLERANCE
                ):
                    stop_reason = "main area overcooled past its own target"
                    protection = True
                elif (
                    direction == DIRECTION_HEAT
                    and snapshot.main_heat_target is not None
                    and main > snapshot.main_heat_target + SZ_MAIN_OVERCOOL_TOLERANCE
                ):
                    stop_reason = "main area overheated past its own target"
                    protection = True

        # Outdoor gating flipped mid-session
        if stop_reason is None and direction not in self._allowed_directions(config, snapshot):
            stop_reason = "outdoor temperature gate closed"

        # Main area now needs the opposite (setpoint-based) — stand down rather
        # than fight a call we can't win.
        if stop_reason is None:
            needs = self._needs_opposite(snapshot, direction)
            if needs is not None:
                stop_reason = needs
                protection = True

        # Outcome-based safety net (mode-agnostic): the thermostat is actually
        # doing the OPPOSITE of what we're forcing. Tolerate a heat↔cool
        # changeover transient, then abort and restore — this is a safety stop
        # that bypasses the minimum-runtime hold (there's no cool call to
        # short-cycle; the unit is doing the other thing entirely).
        safety_abort = False
        if stop_reason is None:
            if self._observed_opposite(snapshot, direction):
                if self._opposite_since is None:
                    self._opposite_since = now
                elif now - self._opposite_since >= SZ_OPPOSITE_ACTION_GRACE_SECONDS:
                    other = "heating" if direction == DIRECTION_COOL else "cooling"
                    stop_reason = f"thermostat is {other} despite the forced setpoint — standing down for safety"
                    protection = True
                    safety_abort = True
            else:
                self._opposite_since = None

        if stop_reason is not None or best is None:
            run_elapsed = now - self._session_started
            if not safety_abort and run_elapsed < config.min_run_seconds:
                # Hold for compressor protection; keep the current setpoint.
                dec = self._forcing_decision(best, protection)
                dec.min_run_lockout = True
                dec.lockout_remaining_s = config.min_run_seconds - run_elapsed
                dec.reason = f"stop pending ({stop_reason}) — holding for minimum runtime"
                dec.command = SetpointCommand(slot=self._slot, value=commanded)
                return dec
            return self._end_session(config, now, restore=True, reason=stop_reason or "", protection=protection)

        # Continue: possibly switch active room and deepen the setpoint.
        # Setpoints only move *away* from nominal during a session; recovery
        # happens via restore. This prevents setpoint bouncing.
        self._active_room = best.area_id
        sp, bias, ratio, rate, _prot, _why = self._compute_setpoint(config, snapshot, best, nominal=self._nominal)
        if sp is not None:
            deeper = (direction == DIRECTION_COOL and sp < commanded - SZ_SETPOINT_DEADBAND) or (
                direction == DIRECTION_HEAT and sp > commanded + SZ_SETPOINT_DEADBAND
            )
            if deeper:
                _LOGGER.debug(
                    "Single-zone: deepening setpoint %.1f→%.1f°C for '%s'",
                    commanded,
                    sp,
                    best.area_id,
                )
                commanded = sp
                self._commanded = sp

        dec = self._forcing_decision(best, False)
        dec.bias = bias
        dec.learned_ratio = ratio
        dec.response_rate = rate
        dec.reason = f"'{best.area_id}' is {best.error:.1f}°C past its {direction} target"
        dec.command = SetpointCommand(slot=self._slot, value=commanded)
        return dec

    # ------------------------------------------------------------------
    # Decision builders
    # ------------------------------------------------------------------

    def _idle_decision(self, config: SingleZoneConfig, now: float, reason: str) -> SingleZoneDecision:
        off_remaining = self._off_lockout_remaining(config, now)
        return SingleZoneDecision(
            status=SZ_STATUS_IDLE,
            reason=reason,
            min_off_lockout=off_remaining > 0,
            lockout_remaining_s=off_remaining,
        )

    def _forcing_decision(
        self,
        best: _Demand | None,
        protection: bool,
    ) -> SingleZoneDecision:
        status = SZ_STATUS_FORCING_COOLING if self._direction == DIRECTION_COOL else SZ_STATUS_FORCING_HEATING
        return SingleZoneDecision(
            status=status,
            forcing=True,
            direction=self._direction,
            active_room=self._active_room,
            room_error=best.error if best else None,
            setpoint=self._commanded,
            nominal_setpoint=self._nominal,
            main_protection_active=protection,
        )

    def _end_session(
        self,
        config: SingleZoneConfig,
        now: float,
        *,
        restore: bool,
        reason: str,
        protection: bool = False,
    ) -> SingleZoneDecision:
        command: SetpointCommand | None = None
        if restore and config.restore_behavior == "restore" and self._nominal is not None:
            command = SetpointCommand(slot=self._slot, value=self._nominal)

        _LOGGER.info("Single-zone: ending forced %sing — %s", self._direction, reason)
        self._forcing = False
        self._last_session_end = now
        self._opposite_since = None
        nominal = self._nominal
        self._nominal = None
        self._commanded = None
        self._active_room = None

        return SingleZoneDecision(
            status=SZ_STATUS_IDLE if config.enabled else SZ_STATUS_DISABLED,
            reason=f"forcing ended: {reason}",
            nominal_setpoint=nominal,
            command=command,
            main_protection_active=protection,
            min_off_lockout=True,
            lockout_remaining_s=config.min_off_seconds,
        )

    # ------------------------------------------------------------------
    # Gating and demand computation
    # ------------------------------------------------------------------

    def _gated_demands(self, config: SingleZoneConfig, snapshot: ZoneSnapshot) -> list[_Demand]:
        demands: list[_Demand] = []
        for prc in config.priority_rooms:
            room = snapshot.rooms.get(prc.area_id)
            if room is None or room.current_temp is None:
                continue
            if not self._gate_open(prc, room, snapshot):
                continue
            if room.cool_target is not None:
                demands.append(
                    _Demand(prc.area_id, DIRECTION_COOL, room.current_temp - room.cool_target),
                )
            if room.heat_target is not None:
                demands.append(
                    _Demand(prc.area_id, DIRECTION_HEAT, room.heat_target - room.current_temp),
                )
        return demands

    @staticmethod
    def _gate_open(prc: PriorityRoomConfig, room: PriorityRoomState, snapshot: ZoneSnapshot) -> bool:
        if prc.condition == SZ_CONDITION_OCCUPIED:
            return room.occupied
        if prc.condition == SZ_CONDITION_SCHEDULE:
            return room.schedule_on
        if prc.condition == SZ_CONDITION_SLEEP:
            return snapshot.sleep_mode_on
        return True  # always

    def _allowed_directions(
        self,
        config: SingleZoneConfig,
        snapshot: ZoneSnapshot,
        outdoor_gated: bool = True,
    ) -> set[str]:
        """Directions the thermostat mode (and optionally outdoor temp) permits."""
        mode = snapshot.thermostat_hvac_mode
        if mode == "cool":
            allowed = {DIRECTION_COOL}
        elif mode == "heat":
            allowed = {DIRECTION_HEAT}
        elif mode in ("heat_cool", "auto"):
            allowed = {DIRECTION_COOL, DIRECTION_HEAT}
        else:  # off / fan_only / dry / unknown — never force
            return set()
        if outdoor_gated and snapshot.outdoor_temp is not None:
            if snapshot.outdoor_temp < config.outdoor_cooling_min:
                allowed.discard(DIRECTION_COOL)
            if snapshot.outdoor_temp > config.outdoor_heating_max:
                allowed.discard(DIRECTION_HEAT)
        return allowed

    # ------------------------------------------------------------------
    # Setpoint computation
    # ------------------------------------------------------------------

    @staticmethod
    def _needs_opposite(snapshot: ZoneSnapshot, direction: str) -> str | None:
        """Reason the thermostat's own area needs the opposite (setpoint-based).

        On a heat_cool/auto thermostat the main area sitting at its heat
        setpoint means a heat call is imminent — forcing cooling would fight
        it. Symmetric for heating. Setpoint comparison, so it works even when
        the device doesn't report hvac_action.
        """
        main = snapshot.main_temp
        if direction == DIRECTION_COOL:
            low = snapshot.thermostat_setpoint_low
            if low is not None and main is not None and main <= low + SZ_TRIGGER_MARGIN:
                return "main area is at its heat setpoint — forcing cooling would fight the heat call"
        else:
            high = snapshot.thermostat_setpoint_high
            if high is not None and main is not None and main >= high - SZ_TRIGGER_MARGIN:
                return "main area is at its cool setpoint — forcing heating would fight the cool call"
        return None

    @staticmethod
    def _observed_opposite(snapshot: ZoneSnapshot, direction: str) -> bool:
        """True when the thermostat is *actually doing* the opposite right now.

        Mode-agnostic outcome signal: whatever the reported hvac_mode or our
        model says, if the unit reports it's heating while we force cooling
        (or cooling while we force heating), that's ground truth.
        """
        action = snapshot.thermostat_hvac_action
        if direction == DIRECTION_COOL:
            return action == "heating"
        return action == "cooling"

    def _start_conflict(self, snapshot: ZoneSnapshot, direction: str) -> str | None:
        """Reason NOT to begin forcing *direction* (both signals, no grace)."""
        if self._observed_opposite(snapshot, direction):
            other = "heating" if direction == DIRECTION_COOL else "cooling"
            verb = "cooling" if direction == DIRECTION_COOL else "heating"
            return f"thermostat is {other} its own area — one system can't force {verb} at the same time"
        return self._needs_opposite(snapshot, direction)

    def _compute_setpoint(
        self,
        config: SingleZoneConfig,
        snapshot: ZoneSnapshot,
        demand: _Demand,
        nominal: float | None,
    ) -> tuple[float | None, float | None, float | None, float | None, bool, str]:
        """Compute the biased setpoint for *demand*.

        *nominal* is the captured pre-force setpoint when a session is active;
        None means "read it from the thermostat now" (session start).

        Returns (setpoint, bias, learned_ratio, response_rate, protection, reason).
        setpoint is None when forcing is not possible within the limits.
        """
        if nominal is None:
            nominal, _slot = self._nominal_for(demand.direction, snapshot)
        if nominal is None:
            return None, None, None, None, False, "thermostat setpoint unreadable"

        main = snapshot.main_temp
        if main is None:
            return None, None, None, None, False, "no main-area temperature available"

        # Shared-system guard (start only): one air handler can't cool the
        # priority room while it's heating its own area (heat_cool/auto).
        # Refuse the losing fight instead of biasing a setpoint that does
        # nothing (or worse). Mid-session conflicts are handled with a settle
        # window in _evaluate_forcing.
        if not self._forcing:
            conflict = self._start_conflict(snapshot, demand.direction)
            if conflict is not None:
                return None, None, None, None, True, conflict

        # Soft arbitration before starting: don't force when the main area is
        # already past its own comfort target (unless priority_wins).
        if not config.priority_wins and not self._forcing:
            if (
                demand.direction == DIRECTION_COOL
                and snapshot.main_cool_target is not None
                and main < snapshot.main_cool_target - SZ_MAIN_OVERCOOL_TOLERANCE
            ):
                return None, None, None, None, True, "main area already overcooled past its own target"
            if (
                demand.direction == DIRECTION_HEAT
                and snapshot.main_heat_target is not None
                and main > snapshot.main_heat_target + SZ_MAIN_OVERCOOL_TOLERANCE
            ):
                return None, None, None, None, True, "main area already overheated past its own target"

        bias, ratio, rate = self._compute_bias(config, snapshot, demand)

        if demand.direction == DIRECTION_COOL:
            sp = min(nominal, main) - bias
            floor = max(nominal - config.max_cool_offset, config.main_min_temp)
            # On a heat_cool thermostat keep the cool setpoint above the heat
            # setpoint by the changeover deadband so lowering it can never
            # invert the band (which would make the device heat instead of
            # cool) or make the thermostat re-adjust the heat setpoint.
            if snapshot.thermostat_setpoint_low is not None:
                floor = max(floor, snapshot.thermostat_setpoint_low + config.band_deadband)
            sp = max(sp, floor)
        else:
            sp = max(nominal, main) + bias
            ceiling = min(nominal + config.max_heat_offset, config.main_max_temp)
            if snapshot.thermostat_setpoint_high is not None:
                ceiling = min(ceiling, snapshot.thermostat_setpoint_high - config.band_deadband)
            sp = min(sp, ceiling)

        # Absolute backstop: never command outside a sane range, whatever the
        # config or math produced.
        sp = max(SZ_ABS_MIN_SETPOINT, min(SZ_ABS_MAX_SETPOINT, sp))

        # Authority check: the clamped setpoint must still be past the
        # thermostat's *own* reading, or the HVAC will never fire.
        ref = snapshot.thermostat_current_temp if snapshot.thermostat_current_temp is not None else main
        if demand.direction == DIRECTION_COOL and sp > ref - SZ_TRIGGER_MARGIN:
            return (
                None,
                bias,
                ratio,
                rate,
                True,
                "comfort bounds leave no setpoint authority to force cooling",
            )
        if demand.direction == DIRECTION_HEAT and sp < ref + SZ_TRIGGER_MARGIN:
            return (
                None,
                bias,
                ratio,
                rate,
                True,
                "comfort bounds leave no setpoint authority to force heating",
            )

        return round(sp, 1), bias, ratio, rate, False, ""

    def _compute_bias(
        self,
        config: SingleZoneConfig,
        snapshot: ZoneSnapshot,
        demand: _Demand,
    ) -> tuple[float, float | None, float | None]:
        """Return (bias, learned_ratio, priority_room_rate).

        Dynamic bias models the physics: the thermostat runs until *its* area
        drops by ``priority_error × (main_rate / priority_rate)`` — a room
        that responds slower than the main area needs a deeper setpoint.
        """
        if demand.direction == DIRECTION_COOL:
            stop = config.cool_stop_threshold
            static = config.cool_bias
            max_offset = config.max_cool_offset
            rates = snapshot.cool_rates
        else:
            stop = config.heat_stop_threshold
            static = config.heat_bias
            max_offset = config.max_heat_offset
            rates = snapshot.heat_rates

        if not config.dynamic_bias:
            return min(static, max_offset), None, rates.get(demand.area_id)

        room_rate = rates.get(demand.area_id)
        main_rate = rates.get(config.main_area_id) if config.main_area_id else None
        ratio = None
        if room_rate and main_rate:
            ratio = max(SZ_RATE_RATIO_MIN, min(SZ_RATE_RATIO_MAX, main_rate / room_rate))
        effective_ratio = ratio if ratio is not None else 1.0

        # Aim to close the whole error down past the stop threshold, plus a
        # margin so the thermostat's internal hysteresis doesn't cut it short.
        bias = (demand.error - stop) * effective_ratio + SZ_MIN_BIAS
        bias = max(SZ_MIN_BIAS, min(bias, max_offset))
        return bias, ratio, room_rate

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _start_threshold(config: SingleZoneConfig, direction: str) -> float:
        return config.cool_start_threshold if direction == DIRECTION_COOL else config.heat_start_threshold

    @staticmethod
    def _stop_threshold(config: SingleZoneConfig, direction: str) -> float:
        return config.cool_stop_threshold if direction == DIRECTION_COOL else config.heat_stop_threshold

    def _off_lockout_remaining(self, config: SingleZoneConfig, now: float) -> float:
        if self._last_session_end is None:
            return 0.0
        return max(0.0, config.min_off_seconds - (now - self._last_session_end))

    @staticmethod
    def _nominal_for(direction: str, snapshot: ZoneSnapshot) -> tuple[float | None, str]:
        """Return (nominal setpoint °C, slot) for *direction* on this thermostat.

        Dual-setpoint (heat_cool/auto) thermostats often ALSO expose a single
        ``temperature`` attribute; writing that back does nothing useful, so
        prefer the direction's own high/low slot whenever it exists.
        """
        if direction == DIRECTION_COOL and snapshot.thermostat_setpoint_high is not None:
            return snapshot.thermostat_setpoint_high, SLOT_HIGH
        if direction == DIRECTION_HEAT and snapshot.thermostat_setpoint_low is not None:
            return snapshot.thermostat_setpoint_low, SLOT_LOW
        if snapshot.thermostat_setpoint is not None:
            return snapshot.thermostat_setpoint, SLOT_TEMPERATURE
        return None, SLOT_TEMPERATURE

    @staticmethod
    def _snapshot_setpoint(snapshot: ZoneSnapshot, slot: str) -> float | None:
        if slot == SLOT_HIGH:
            return snapshot.thermostat_setpoint_high
        if slot == SLOT_LOW:
            return snapshot.thermostat_setpoint_low
        return snapshot.thermostat_setpoint
