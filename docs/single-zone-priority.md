# Priority Zones (single-thermostat supervisory control)

For homes where whole zones hang on **one central thermostat with remote
sensors only** — a single-stage ducted furnace/AC per floor, no dampers, no
smart vents, no TRVs. The only real actuator in each zone is that zone's
thermostat setpoint.

RoomMind acts as a **supervisory controller** per zone: when a *priority
room* (measured by a remote temperature sensor) is uncomfortable, it
temporarily biases that zone's thermostat setpoint to force HVAC runtime,
even if the thermostat's own area is already satisfied — then restores the
setpoint once the priority room approaches its target.

Multiple zones are fully independent: a two-system home (downstairs +
upstairs, two compressors) gets two zones, each with its own thermostat,
priority rooms, forcing sessions, anti-short-cycle timers, and entities.

```
Bedroom target 74°F, bedroom at 77°F, thermostat area at 74°F
→ RoomMind lowers that zone's thermostat to ~71–72°F until the bedroom approaches 74°F
→ setpoint restored, cooldown starts — the other zone is unaffected
```

All of RoomMind's comfort machinery keeps working for priority rooms:
schedules, manual overrides, presence/eco, occupancy — the priority logic acts
on the room's *resolved* heat/cool targets.

## How a forcing session works

1. **Start** — a gated priority room's error exceeds the *start threshold*
   (e.g. bedroom is 1.5°F above its cooling target). The thermostat's current
   setpoint is captured as *nominal* and a biased setpoint is written.
2. **Bias** — with `dynamic_bias` on (default), the bias is
   `error × (main response rate ÷ priority room response rate) + margin`,
   using the rates learned by the thermal model (EKF). A room that cools
   slower than the main area automatically gets a deeper bias. Falls back to
   1:1 until both rooms are calibrated; with `dynamic_bias` off, the fixed
   `cool_bias`/`heat_bias` is used.
3. **Clamps** — the setpoint never exceeds `max_cool_offset`/`max_heat_offset`
   from nominal, and never crosses `main_min_temp`/`main_max_temp` (so the
   main area cannot be overcooled/overheated past your bounds). If the clamps
   leave no authority to trigger runtime, forcing does not start and the
   status sensor explains why.
4. **Stop** — when the priority room error falls below the *stop threshold*
   (separate from start → hysteresis, no oscillation), the nominal setpoint is
   restored (`restore_behavior: restore`) or left alone (`leave`).
5. **Protection** — `min_run_minutes` holds every comfort-driven stop
   (compressor protection is absolute; overshoot is already bounded by the
   setpoint clamps). `min_off_minutes` blocks a new session after restore.
   Setpoints only *deepen* during a session — they never bounce. Timers are
   per zone; two compressors never share a lockout.
6. **Arbitration** — if the main area drifts more than 1.5°C past its own
   target, forcing stops early — unless `priority_wins: true`, in which case
   only the hard `main_min_temp`/`main_max_temp` bounds apply.
7. **Shared-system guard (Auto / heat_cool)** — one central system can only
   heat *or* cool at once. On a `heat_cool`/`auto` thermostat, RoomMind will
   not force cooling while the system is heating its own area (or about to,
   because the main area sits at its heat setpoint) — and vice versa. It
   stands down with an explanatory reason rather than fighting a call it can't
   win. So you can leave the thermostat in Auto year-round (set-and-forget):
   RoomMind forces cooling when the house isn't heating, forces heating when
   it isn't cooling, and safely defers otherwise. On these thermostats the
   forced setpoint is also kept the **changeover deadband** away from the
   opposite setpoint so it can never invert the band or make the thermostat
   re-adjust the other setpoint — set `band_deadband` to match (or slightly
   exceed) your thermostat's own auto-changeover deadband. This setting is
   ignored in single `cool`/`heat` mode, where no conflict is possible.
8. **Outcome safety net (mode-agnostic)** — whatever the reported mode, if the
   thermostat is *actually* doing the opposite of what's being forced (it
   reports heating while forcing cooling, or vice versa) for longer than a
   short changeover window, the zone aborts and restores — bypassing the
   minimum-runtime hold, because safety outranks compressor protection. This
   is a belt-and-suspenders check on the real `hvac_action`, so a misreported
   mode or an unexpected device response can't leave RoomMind forcing the
   wrong way. A final absolute setpoint clamp (7–35 °C / 45–95 °F) guards
   against any config or math error regardless of the comfort bounds.
9. **Manual override** — if you change a thermostat setpoint by hand during
   a session, that zone stands down immediately without restoring.

A zone never changes its thermostat's hvac_mode: cooling is only forced when
the thermostat is in `cool` (or `heat_cool`/`auto`), heating only in `heat`
(or `heat_cool`/`auto`). Global outdoor gates
(`outdoor_cooling_min` / `outdoor_heating_max`) are respected.

## Runtime learning with one actuator per zone

Rooms listed in a zone's `zone_rooms` that have **no climate devices of their
own** get their thermal-model training label from that zone's thermostat
`hvac_action` instead of (nonexistent) room devices. Each room's EKF learns
how fast its zone's system heats/cools *that room* — which is exactly what
the dynamic bias uses. List every room a zone serves (including the main area
room, if you model it); a room can belong to only one zone.

## Configuration

Configuration is split so each setting lives where you'd expect it:

**Zone-level — Settings → Priority Zones.** Add a zone per thermostat. Each
zone card holds the thermostat, the optional main area, a sleep-mode entity,
and (under **Advanced tuning**, collapsed by default) the thresholds, biases,
offsets, main-area bounds, and compressor protection. A live status card shows
the current decision (state, active room, bias, lockouts) while the zone is
enabled, and a chip list summarises which rooms belong to the zone (★ marks
priority rooms).

**Room-level — each room's page → Priority Zone.** Assign the room to a zone,
flip the *priority room* toggle, and pick its activation condition (always /
occupied / schedule / sleep). These sit right next to the room's sensor and
comfort targets — which is what the forcing acts on. A room belongs to only
one zone; the picker reflects that.

Temperatures are shown in your HA unit system throughout (converted to °C on
the wire).

Under the hood this edits the `priority_zones` settings list, which can also
be saved directly via the `roommind_cc/settings/save` WebSocket command. All
temperatures are **°C** on the wire, like the rest of RoomMind. A legacy
`single_zone` blob from earlier builds is migrated automatically to a
one-entry list with id `zone_1`.

```jsonc
{
  "type": "roommind_cc/settings/save",
  "priority_zones": [
    {
      "id": "downstairs",                 // stable slug, used in entity ids
      "name": "Downstairs",
      "enabled": true,
      "thermostat_entity": "climate.downstairs",
      "zone_rooms": ["bedroom_x1", "living_room_x2"],
      "main_area_id": "living_room_x2",   // RoomMind room at the thermostat (optional)
      "main_temp_sensor": "",             // else thermostat's own reading is used
      "priority_rooms": [
        { "area_id": "bedroom_x1", "condition": "sleep" }
        // condition: always | occupied | schedule | sleep
      ],
      "sleep_mode_entity": "input_boolean.sleep_mode",
      "cool_start_threshold": 0.8,        // °C above cool target → start forcing (~1.5°F)
      "cool_stop_threshold": 0.2,         // °C above cool target → stop (~0.35°F)
      "heat_start_threshold": 0.8,
      "heat_stop_threshold": 0.2,
      "cool_bias": 1.5,                   // static bias when dynamic_bias is false
      "heat_bias": 1.5,
      "max_cool_offset": 2.5,             // °C max setpoint offset from nominal
      "max_heat_offset": 2.5,
      "main_min_temp": 20.0,              // °C hard floor for the main area (~68°F)
      "main_max_temp": 26.0,              // °C hard ceiling (~79°F)
      "min_run_minutes": 10,
      "min_off_minutes": 10,
      "band_deadband": 1.0,               // °C gap for heat_cool/auto thermostats (match device changeover)
      "dynamic_bias": true,
      "priority_wins": false,
      "restore_behavior": "restore"       // restore | leave
    },
    {
      "id": "upstairs",
      "name": "Upstairs",
      "enabled": true,
      "thermostat_entity": "climate.upstairs",
      "zone_rooms": ["loft_x3", "kids_room_x4"],
      "priority_rooms": [{ "area_id": "kids_room_x4", "condition": "occupied" }]
    }
  ]
}
```

> **Important:** a zone's thermostat must **not** be assigned as a device
> in any RoomMind room with climate control enabled — the per-room controller
> would fight the bias. RoomMind detects this and disables the zone with an
> explanatory reason. Cross-zone rules are validated on save: each thermostat
> serves one zone, and each room belongs to (and is a priority room of) at
> most one zone.

## Entities (per zone)

| Entity | Description |
|--------|-------------|
| `sensor.roommind_cc_zone_{id}_status` | `disabled` / `idle` / `forcing_cooling` / `forcing_heating`, with the full decision as attributes: `active_room`, `room_error`, `bias`, `setpoint`, `nominal_setpoint`, `reason`, `min_run_lockout`, `min_off_lockout`, `lockout_remaining_s`, `main_protection_active`, `learned_ratio`, `response_rate` (temperature attributes in °C) |
| `binary_sensor.roommind_cc_zone_{id}_forcing` | On while a forcing session is active (`direction`, `active_room`, `reason` attributes) |
| `switch.roommind_cc_zone_{id}_enabled` | Quick per-zone enable/disable; turning it off mid-session restores the setpoint on the next cycle |

Entities are created and removed automatically when zones are added or
deleted. The current decisions also appear in `roommind_cc/rooms/list`
(`priority_zone_state`, keyed by zone id) and in the integration diagnostics.

## Front-page visibility

Because a single shared thermostat conditions every room in its zone together,
sensor-only zone rooms would otherwise always read `idle`. Instead, each zone
room's card reflects the thermostat's actual conditioning (heating/cooling), so
the dashboard heating/cooling counts include the zone. The room actively
driving a forcing session gets a **Priority cooling / Priority heating** badge,
so you can see at a glance which room the zone is running for.

## Multi-room users are unaffected

The feature is inert unless a zone is configured and enabled. Per-room device
control, MPC, weighting, covers, compressor groups and everything else behave
exactly as before.
