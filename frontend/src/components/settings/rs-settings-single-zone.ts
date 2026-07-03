/**
 * rs-settings-single-zone – Priority zone configuration (multi-zone).
 *
 * Each zone pairs one central thermostat with the rooms it serves. When a
 * priority room is uncomfortable, RoomMind biases that zone's thermostat
 * setpoint to force HVAC runtime. Zones are fully independent (own manager,
 * own compressor timers, own entities on the backend). This section owns the
 * whole `priority_zones` list and fires it as one array; the parent saves it
 * via roommind_cc/settings/save.
 */
import { html, css, nothing } from "lit";
import { RsSettingsBase } from "./rs-settings-base";
import { customElement, property } from "lit/decorators.js";
import type {
  HomeAssistant,
  PriorityZone,
  RoomConfig,
  SingleZoneCondition,
  SingleZonePriorityRoom,
} from "../../types";
import { localize } from "../../utils/localize";
import { getSelectValue } from "../../utils/events";
import {
  tempUnit,
  tempStep,
  tempRange,
  toDisplay,
  toCelsius,
  toDisplayDelta,
  toCelsiusDelta,
} from "../../utils/temperature";
import { inputStyles } from "../../styles/input-styles";
import "../shared/rs-confirm-button";

export const ZONE_DEFAULTS: Omit<PriorityZone, "id"> = {
  name: "",
  enabled: false,
  thermostat_entity: "",
  zone_rooms: [],
  main_area_id: "",
  main_temp_sensor: "",
  priority_rooms: [],
  sleep_mode_entity: "",
  cool_start_threshold: 0.8,
  cool_stop_threshold: 0.2,
  heat_start_threshold: 0.8,
  heat_stop_threshold: 0.2,
  cool_bias: 1.5,
  heat_bias: 1.5,
  max_cool_offset: 2.5,
  max_heat_offset: 2.5,
  main_min_temp: 20.0,
  main_max_temp: 26.0,
  min_run_minutes: 10,
  min_off_minutes: 10,
  dynamic_bias: true,
  priority_wins: false,
  restore_behavior: "restore",
};

const MIN_THRESHOLD_GAP = 0.1; // °C between stop and start (backend requires stop < start)

@customElement("rmc-settings-single-zone")
export class RsSettingsSingleZone extends RsSettingsBase {
  @property({ attribute: false }) public hass!: HomeAssistant;
  @property({ attribute: false }) public rooms: Record<string, RoomConfig> = {};
  @property({ type: Array }) public zones: PriorityZone[] = [];

  render() {
    const l = this.hass.language;
    return html`
      ${this.zones.length === 0
        ? html`<div class="no-zones">${localize("single_zone.no_zones", l)}</div>`
        : this.zones.map((zone, idx) => this._renderZone(zone, idx))}
      <ha-button class="add-button" @click=${this._addZone}>
        <ha-icon icon="mdi:plus" slot="icon"></ha-icon>
        ${localize("single_zone.add_zone", l)}
      </ha-button>
    `;
  }

  private _renderZone(zoneRaw: PriorityZone, idx: number) {
    const l = this.hass.language;
    const sz: PriorityZone = { ...ZONE_DEFAULTS, ...zoneRaw };
    const canEnable = !!sz.thermostat_entity;

    return html`
      <div class="zone-card">
        <div class="zone-header">
          <ha-textfield
            .value=${sz.name}
            .label=${localize("single_zone.zone_name", l)}
            .placeholder=${sz.id}
            @change=${(e: Event) =>
              this._updateZone(idx, { name: (e.target as HTMLInputElement).value })}
          ></ha-textfield>
          <rmc-confirm-button
            .label=${localize("single_zone.delete_zone", l)}
            .confirmMessage=${localize("single_zone.delete_zone_confirm", l).replace(
              "{name}",
              sz.name || sz.id,
            )}
            destructive
            @confirmed=${() => this._fireZones(this.zones.filter((_, i) => i !== idx))}
          ></rmc-confirm-button>
        </div>

        <!-- Enable + live status -->
        <div class="settings-section">
          <div class="toggle-row">
            <div class="toggle-text">
              <span class="toggle-label">${localize("single_zone.enabled", l)}</span>
              <span class="toggle-hint">
                ${canEnable
                  ? localize("single_zone.enabled_hint", l)
                  : localize("single_zone.enabled_needs_thermostat", l)}
              </span>
            </div>
            <ha-switch
              .checked=${sz.enabled}
              .disabled=${!canEnable && !sz.enabled}
              @change=${(e: Event) =>
                this._updateZone(idx, { enabled: (e.target as HTMLInputElement).checked })}
            ></ha-switch>
          </div>
          ${sz.enabled ? this._renderStatus(sz) : nothing}
        </div>

        <!-- Central thermostat -->
        <div class="settings-section">
          <span class="section-label">${localize("single_zone.thermostat", l)}</span>
          <ha-entity-picker
            .hass=${this.hass}
            .value=${sz.thermostat_entity}
            .includeDomains=${["climate"]}
            .entityFilter=${this._thermostatFilter(sz.id)}
            @value-changed=${(e: CustomEvent) => {
              const v = (e.detail?.value as string) ?? "";
              this._updateZone(
                idx,
                v ? { thermostat_entity: v } : { thermostat_entity: "", enabled: false },
              );
            }}
          ></ha-entity-picker>
          <div class="field-hint">${localize("single_zone.thermostat_hint", l)}</div>
        </div>

        <!-- Zone rooms -->
        <div class="settings-section">
          <span class="section-label">${localize("single_zone.zone_rooms", l)}</span>
          <div class="field-hint">${localize("single_zone.zone_rooms_hint", l)}</div>
          <div class="room-toggles">
            ${this._configuredRooms().map((room) => {
              const otherZone = this._zoneRoomOwner(room.areaId, sz.id);
              return html`
                <div class="room-toggle-row">
                  <span class="room-toggle-name">
                    ${room.name}
                    ${otherZone
                      ? html`<span class="room-toggle-other"
                          >${localize("single_zone.room_in_other_zone", l).replace(
                            "{zone}",
                            otherZone,
                          )}</span
                        >`
                      : nothing}
                  </span>
                  <ha-switch
                    .checked=${sz.zone_rooms.includes(room.areaId)}
                    .disabled=${!!otherZone}
                    @change=${(e: Event) => {
                      const set = new Set(sz.zone_rooms);
                      if ((e.target as HTMLInputElement).checked) set.add(room.areaId);
                      else set.delete(room.areaId);
                      this._updateZone(idx, { zone_rooms: [...set] });
                    }}
                  ></ha-switch>
                </div>
              `;
            })}
          </div>
        </div>

        <!-- Priority rooms -->
        <div class="settings-section">
          <span class="section-label">${localize("single_zone.priority_rooms", l)}</span>
          <div class="field-hint">${localize("single_zone.priority_rooms_hint", l)}</div>
          ${sz.priority_rooms.map((pr, prIdx) => this._renderPriorityRoom(sz, idx, pr, prIdx))}
          <ha-button
            class="add-button"
            .disabled=${this._availablePriorityRooms(sz.id, "").length === 0}
            @click=${() => this._addPriorityRoom(idx)}
          >
            <ha-icon icon="mdi:plus" slot="icon"></ha-icon>
            ${localize("single_zone.add_priority_room", l)}
          </ha-button>
          ${sz.priority_rooms.some((pr) => pr.condition === "sleep")
            ? html`
                <div class="field-row">
                  <ha-entity-picker
                    .hass=${this.hass}
                    .value=${sz.sleep_mode_entity}
                    .includeDomains=${["input_boolean", "binary_sensor", "switch"]}
                    .label=${localize("single_zone.sleep_entity", l)}
                    @value-changed=${(e: CustomEvent) =>
                      this._updateZone(idx, {
                        sleep_mode_entity: (e.detail?.value as string) ?? "",
                      })}
                  ></ha-entity-picker>
                  <div class="field-hint">${localize("single_zone.sleep_entity_hint", l)}</div>
                </div>
              `
            : nothing}
        </div>

        <!-- Main area -->
        <div class="settings-section">
          <span class="section-label">${localize("single_zone.main_area", l)}</span>
          <div class="field-hint">${localize("single_zone.main_area_hint", l)}</div>
          <ha-select
            .label=${localize("single_zone.main_area_room", l)}
            .value=${sz.main_area_id || "__none__"}
            .options=${[
              { value: "__none__", label: localize("single_zone.main_area_none", l) },
              ...this._configuredRooms().map((room) => ({ value: room.areaId, label: room.name })),
            ]}
            fixedMenuPosition
            @selected=${(e: Event) => {
              const v = getSelectValue(e);
              if (v !== undefined)
                this._updateZone(idx, { main_area_id: v === "__none__" ? "" : v });
            }}
            @closed=${(e: Event) => e.stopPropagation()}
            style="width: 100%;"
          >
            <ha-list-item value="__none__"
              >${localize("single_zone.main_area_none", l)}</ha-list-item
            >
            ${this._configuredRooms().map(
              (room) => html`<ha-list-item value=${room.areaId}>${room.name}</ha-list-item>`,
            )}
          </ha-select>
          <div class="field-row">
            <ha-entity-picker
              .hass=${this.hass}
              .value=${sz.main_temp_sensor}
              .includeDomains=${["sensor"]}
              .label=${localize("single_zone.main_temp_sensor", l)}
              @value-changed=${(e: CustomEvent) =>
                this._updateZone(idx, { main_temp_sensor: (e.detail?.value as string) ?? "" })}
            ></ha-entity-picker>
            <div class="field-hint">${localize("single_zone.main_temp_sensor_hint", l)}</div>
          </div>
        </div>

        <!-- Start/stop thresholds -->
        <div class="settings-section">
          <span class="section-label">${localize("single_zone.thresholds", l)}</span>
          <div class="field-hint">${localize("single_zone.thresholds_hint", l)}</div>
          <div class="number-fields">
            ${this._deltaField("single_zone.cool_start", sz.cool_start_threshold, 0.1, 5, (v) =>
              this._updateZone(idx, {
                cool_start_threshold: v,
                cool_stop_threshold: Math.min(sz.cool_stop_threshold, v - MIN_THRESHOLD_GAP),
              }),
            )}
            ${this._deltaField("single_zone.cool_stop", sz.cool_stop_threshold, 0, 5, (v) =>
              this._updateZone(idx, {
                cool_stop_threshold: Math.min(v, sz.cool_start_threshold - MIN_THRESHOLD_GAP),
              }),
            )}
            ${this._deltaField("single_zone.heat_start", sz.heat_start_threshold, 0.1, 5, (v) =>
              this._updateZone(idx, {
                heat_start_threshold: v,
                heat_stop_threshold: Math.min(sz.heat_stop_threshold, v - MIN_THRESHOLD_GAP),
              }),
            )}
            ${this._deltaField("single_zone.heat_stop", sz.heat_stop_threshold, 0, 5, (v) =>
              this._updateZone(idx, {
                heat_stop_threshold: Math.min(v, sz.heat_start_threshold - MIN_THRESHOLD_GAP),
              }),
            )}
          </div>
        </div>

        <!-- Bias -->
        <div class="settings-section">
          <div class="toggle-row">
            <div class="toggle-text">
              <span class="toggle-label">${localize("single_zone.dynamic_bias", l)}</span>
              <span class="toggle-hint">${localize("single_zone.dynamic_bias_hint", l)}</span>
            </div>
            <ha-switch
              .checked=${sz.dynamic_bias}
              @change=${(e: Event) =>
                this._updateZone(idx, { dynamic_bias: (e.target as HTMLInputElement).checked })}
            ></ha-switch>
          </div>
          <div class="number-fields">
            ${sz.dynamic_bias
              ? nothing
              : html`
                  ${this._deltaField("single_zone.cool_bias", sz.cool_bias, 0.5, 10, (v) =>
                    this._updateZone(idx, { cool_bias: v }),
                  )}
                  ${this._deltaField("single_zone.heat_bias", sz.heat_bias, 0.5, 10, (v) =>
                    this._updateZone(idx, { heat_bias: v }),
                  )}
                `}
            ${this._deltaField("single_zone.max_cool_offset", sz.max_cool_offset, 0.5, 10, (v) =>
              this._updateZone(idx, { max_cool_offset: v }),
            )}
            ${this._deltaField("single_zone.max_heat_offset", sz.max_heat_offset, 0.5, 10, (v) =>
              this._updateZone(idx, { max_heat_offset: v }),
            )}
          </div>
        </div>

        <!-- Main-area comfort bounds -->
        <div class="settings-section">
          <span class="section-label">${localize("single_zone.main_bounds", l)}</span>
          <div class="field-hint">${localize("single_zone.main_bounds_hint", l)}</div>
          <div class="number-fields">
            ${this._absTempField("single_zone.main_min_temp", sz.main_min_temp, (v) =>
              this._updateZone(idx, { main_min_temp: Math.min(v, sz.main_max_temp - 0.5) }),
            )}
            ${this._absTempField("single_zone.main_max_temp", sz.main_max_temp, (v) =>
              this._updateZone(idx, { main_max_temp: Math.max(v, sz.main_min_temp + 0.5) }),
            )}
          </div>
        </div>

        <!-- Compressor protection -->
        <div class="settings-section">
          <span class="section-label">${localize("single_zone.protection", l)}</span>
          <div class="field-hint">${localize("single_zone.protection_hint", l)}</div>
          <div class="number-fields">
            <div>
              <ha-textfield
                type="number"
                .value=${String(sz.min_run_minutes)}
                .label=${localize("single_zone.min_run", l)}
                .suffix=${localize("single_zone.minutes_suffix", l)}
                min="1"
                max="60"
                step="1"
                @change=${(e: Event) => {
                  const v = parseInt((e.target as HTMLInputElement).value, 10);
                  if (!isNaN(v) && v >= 1 && v <= 60) this._updateZone(idx, { min_run_minutes: v });
                }}
              ></ha-textfield>
              <div class="field-hint">${localize("single_zone.min_run_hint", l)}</div>
            </div>
            <div>
              <ha-textfield
                type="number"
                .value=${String(sz.min_off_minutes)}
                .label=${localize("single_zone.min_off", l)}
                .suffix=${localize("single_zone.minutes_suffix", l)}
                min="1"
                max="60"
                step="1"
                @change=${(e: Event) => {
                  const v = parseInt((e.target as HTMLInputElement).value, 10);
                  if (!isNaN(v) && v >= 1 && v <= 60) this._updateZone(idx, { min_off_minutes: v });
                }}
              ></ha-textfield>
              <div class="field-hint">${localize("single_zone.min_off_hint", l)}</div>
            </div>
          </div>
        </div>

        <!-- Behavior -->
        <div class="settings-section">
          <div class="toggle-row">
            <div class="toggle-text">
              <span class="toggle-label">${localize("single_zone.priority_wins", l)}</span>
              <span class="toggle-hint">${localize("single_zone.priority_wins_hint", l)}</span>
            </div>
            <ha-switch
              .checked=${sz.priority_wins}
              @change=${(e: Event) =>
                this._updateZone(idx, { priority_wins: (e.target as HTMLInputElement).checked })}
            ></ha-switch>
          </div>
          <div class="field-row">
            <ha-select
              .label=${localize("single_zone.restore_behavior", l)}
              .value=${sz.restore_behavior}
              .options=${[
                { value: "restore", label: localize("single_zone.restore_restore", l) },
                { value: "leave", label: localize("single_zone.restore_leave", l) },
              ]}
              fixedMenuPosition
              @selected=${(e: Event) => {
                const v = getSelectValue(e) as "restore" | "leave";
                if (v && v !== sz.restore_behavior) this._updateZone(idx, { restore_behavior: v });
              }}
              @closed=${(e: Event) => e.stopPropagation()}
              style="width: 100%;"
            >
              <ha-list-item value="restore"
                >${localize("single_zone.restore_restore", l)}</ha-list-item
              >
              <ha-list-item value="leave">${localize("single_zone.restore_leave", l)}</ha-list-item>
            </ha-select>
            <div class="field-hint">${localize("single_zone.restore_behavior_hint", l)}</div>
          </div>
        </div>
      </div>
    `;
  }

  /** Live decision state from the zone's status sensor entity. */
  private _renderStatus(sz: PriorityZone) {
    const l = this.hass.language;
    const state = this.hass.states[`sensor.roommind_cc_zone_${sz.id}_status`];
    if (!state) return nothing;
    const attrs = state.attributes as Record<string, unknown>;
    const status = state.state;
    const forcing = status === "forcing_cooling" || status === "forcing_heating";
    const bias = attrs.bias as number | null;
    const activeRoom = attrs.active_room as string | null;
    const roomName = activeRoom ? this._roomName(activeRoom) : "";
    const statusKey = `single_zone.status_${status}` as Parameters<typeof localize>[0];

    return html`
      <div class="status-card ${forcing ? "status-forcing" : ""}">
        <div class="status-line">
          <ha-icon icon=${forcing ? "mdi:fan-alert" : "mdi:fan"}></ha-icon>
          <span class="status-state">${localize(statusKey, l)}</span>
          ${activeRoom ? html`<span class="status-room">${roomName}</span>` : nothing}
          ${forcing && bias != null
            ? html`<span class="status-bias">
                ${toDisplayDelta(bias, this.hass).toFixed(1)}${tempUnit(this.hass)}
              </span>`
            : nothing}
        </div>
        ${attrs.reason ? html`<div class="status-reason">${String(attrs.reason)}</div>` : nothing}
        ${attrs.min_run_lockout || attrs.min_off_lockout
          ? html`<div class="status-lockout">
              <ha-icon icon="mdi:timer-lock-outline"></ha-icon>
              ${localize(
                attrs.min_run_lockout
                  ? "single_zone.lockout_min_run"
                  : "single_zone.lockout_min_off",
                l,
              )}
              (${Math.round(((attrs.lockout_remaining_s as number) ?? 0) / 60)}
              ${localize("single_zone.minutes_suffix", l)})
            </div>`
          : nothing}
        ${attrs.main_protection_active
          ? html`<div class="status-lockout">
              <ha-icon icon="mdi:shield-alert-outline"></ha-icon>
              ${localize("single_zone.protection_active", l)}
            </div>`
          : nothing}
      </div>
    `;
  }

  private _renderPriorityRoom(
    sz: PriorityZone,
    zoneIdx: number,
    pr: SingleZonePriorityRoom,
    prIdx: number,
  ) {
    const l = this.hass.language;
    return html`
      <div class="priority-card">
        <div class="priority-row">
          <ha-select
            .label=${localize("single_zone.priority_room", l)}
            .value=${pr.area_id}
            .options=${this._availablePriorityRooms(sz.id, pr.area_id).map((room) => ({
              value: room.areaId,
              label: room.name,
            }))}
            fixedMenuPosition
            @selected=${(e: Event) => {
              const v = getSelectValue(e);
              if (v && v !== pr.area_id) this._updatePriorityRoom(zoneIdx, prIdx, { area_id: v });
            }}
            @closed=${(e: Event) => e.stopPropagation()}
          >
            ${this._availablePriorityRooms(sz.id, pr.area_id).map(
              (room) => html`<ha-list-item value=${room.areaId}>${room.name}</ha-list-item>`,
            )}
          </ha-select>
          <ha-select
            .label=${localize("single_zone.condition", l)}
            .value=${pr.condition}
            .options=${[
              { value: "always", label: localize("single_zone.condition_always", l) },
              { value: "occupied", label: localize("single_zone.condition_occupied", l) },
              { value: "schedule", label: localize("single_zone.condition_schedule", l) },
              { value: "sleep", label: localize("single_zone.condition_sleep", l) },
            ]}
            fixedMenuPosition
            @selected=${(e: Event) => {
              const v = getSelectValue(e) as SingleZoneCondition;
              if (v && v !== pr.condition)
                this._updatePriorityRoom(zoneIdx, prIdx, { condition: v });
            }}
            @closed=${(e: Event) => e.stopPropagation()}
          >
            <ha-list-item value="always"
              >${localize("single_zone.condition_always", l)}</ha-list-item
            >
            <ha-list-item value="occupied"
              >${localize("single_zone.condition_occupied", l)}</ha-list-item
            >
            <ha-list-item value="schedule"
              >${localize("single_zone.condition_schedule", l)}</ha-list-item
            >
            <ha-list-item value="sleep">${localize("single_zone.condition_sleep", l)}</ha-list-item>
          </ha-select>
          <ha-icon-button
            .path=${"M19,6.41L17.59,5L12,10.59L6.41,5L5,6.41L10.59,12L5,17.59L6.41,19L12,13.41L17.59,19L19,17.59L13.41,12L19,6.41Z"}
            @click=${() =>
              this._updateZone(zoneIdx, {
                priority_rooms: sz.priority_rooms.filter((_, i) => i !== prIdx),
              })}
          ></ha-icon-button>
        </div>
        ${pr.condition === "schedule"
          ? html`
              <ha-entity-picker
                .hass=${this.hass}
                .value=${pr.schedule_entity}
                .includeDomains=${["schedule", "input_boolean", "binary_sensor"]}
                .label=${localize("single_zone.condition_schedule_entity", l)}
                @value-changed=${(e: CustomEvent) =>
                  this._updatePriorityRoom(zoneIdx, prIdx, {
                    schedule_entity: (e.detail?.value as string) ?? "",
                  })}
              ></ha-entity-picker>
            `
          : nothing}
      </div>
    `;
  }

  /** Number field for a temperature *delta* (°C wire, display-unit UI). */
  private _deltaField(
    labelKey: string,
    celsiusValue: number,
    minC: number,
    maxC: number,
    onChange: (celsius: number) => void,
  ) {
    const l = this.hass.language;
    return html`
      <div>
        <ha-textfield
          type="number"
          .value=${toDisplayDelta(celsiusValue, this.hass).toFixed(1)}
          .label=${localize(labelKey as Parameters<typeof localize>[0], l)}
          .suffix=${tempUnit(this.hass)}
          min=${toDisplayDelta(minC, this.hass).toFixed(1)}
          max=${toDisplayDelta(maxC, this.hass).toFixed(1)}
          step="0.1"
          @change=${(e: Event) => {
            const v = parseFloat((e.target as HTMLInputElement).value);
            if (isNaN(v)) return;
            const c = Math.max(minC, Math.min(maxC, toCelsiusDelta(v, this.hass)));
            onChange(Math.round(c * 100) / 100);
          }}
        ></ha-textfield>
        <div class="field-hint">
          ${localize(`${labelKey}_hint` as Parameters<typeof localize>[0], l)}
        </div>
      </div>
    `;
  }

  /** Number field for an *absolute* temperature (°C wire, display-unit UI). */
  private _absTempField(
    labelKey: string,
    celsiusValue: number,
    onChange: (celsius: number) => void,
  ) {
    const l = this.hass.language;
    const range = tempRange(5, 35, this.hass);
    return html`
      <div>
        <ha-textfield
          type="number"
          .value=${toDisplay(celsiusValue, this.hass).toFixed(1)}
          .label=${localize(labelKey as Parameters<typeof localize>[0], l)}
          .suffix=${tempUnit(this.hass)}
          min=${range.min}
          max=${range.max}
          step=${tempStep(this.hass)}
          @change=${(e: Event) => {
            const v = parseFloat((e.target as HTMLInputElement).value);
            if (isNaN(v)) return;
            const c = Math.max(5, Math.min(35, toCelsius(v, this.hass)));
            onChange(Math.round(c * 100) / 100);
          }}
        ></ha-textfield>
        <div class="field-hint">
          ${localize(`${labelKey}_hint` as Parameters<typeof localize>[0], l)}
        </div>
      </div>
    `;
  }

  private _configuredRooms(): { areaId: string; name: string }[] {
    return Object.entries(this.rooms)
      .filter(([, r]) => !r.is_outdoor)
      .map(([areaId, r]) => ({
        areaId,
        name: r.display_name || this.hass.areas?.[areaId]?.name || areaId,
      }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }

  private _roomName(areaId: string): string {
    return this.rooms[areaId]?.display_name || this.hass.areas?.[areaId]?.name || areaId;
  }

  /** Zone label if *areaId* is a zone room of a zone other than *zoneId*. */
  private _zoneRoomOwner(areaId: string, zoneId: string): string | null {
    for (const z of this.zones) {
      if (z.id !== zoneId && (z.zone_rooms ?? []).includes(areaId)) {
        return z.name || z.id;
      }
    }
    return null;
  }

  /** Rooms selectable as priority room in a zone: not used by ANY zone (except *current*). */
  private _availablePriorityRooms(
    zoneId: string,
    current: string,
  ): { areaId: string; name: string }[] {
    const used = new Set<string>();
    for (const z of this.zones) {
      for (const p of z.priority_rooms ?? []) used.add(p.area_id);
    }
    used.delete(current);
    return this._configuredRooms().filter((r) => !used.has(r.areaId));
  }

  private _thermostatFilter(zoneId: string) {
    return (entity: { entity_id: string }): boolean => {
      const id = entity.entity_id;
      if (id.substring(id.indexOf(".") + 1).startsWith("roommind_")) return false;
      // A thermostat can serve only one zone
      for (const z of this.zones) {
        if (z.id !== zoneId && z.thermostat_entity === id) return false;
      }
      return true;
    };
  }

  private _addZone() {
    const suffix =
      self.crypto?.randomUUID?.()?.replace(/-/g, "").slice(0, 8) ??
      Math.random().toString(36).slice(2, 10);
    this._fireZones([...this.zones, { ...ZONE_DEFAULTS, id: `zone_${suffix}` }]);
  }

  private _addPriorityRoom(zoneIdx: number) {
    const zone = { ...ZONE_DEFAULTS, ...this.zones[zoneIdx] };
    const available = this._availablePriorityRooms(zone.id, "");
    if (available.length === 0) return;
    this._updateZone(zoneIdx, {
      priority_rooms: [
        ...zone.priority_rooms,
        { area_id: available[0].areaId, condition: "always", schedule_entity: "" },
      ],
    });
  }

  private _updatePriorityRoom(
    zoneIdx: number,
    prIdx: number,
    changes: Partial<SingleZonePriorityRoom>,
  ) {
    const zone = { ...ZONE_DEFAULTS, ...this.zones[zoneIdx] };
    const updated = [...zone.priority_rooms];
    updated[prIdx] = { ...updated[prIdx], ...changes };
    this._updateZone(zoneIdx, { priority_rooms: updated });
  }

  private _updateZone(idx: number, changes: Partial<PriorityZone>) {
    const updated = [...this.zones];
    updated[idx] = { ...ZONE_DEFAULTS, ...updated[idx], ...changes };
    this._fireZones(updated);
  }

  private _fireZones(zones: PriorityZone[]) {
    this._fire("priorityZones", zones);
  }

  static styles = [
    RsSettingsBase.settingsBaseStyles,
    inputStyles,
    css`
      .no-zones {
        color: var(--secondary-text-color);
        font-size: 14px;
        padding: 8px 0;
      }
      .zone-card {
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        padding: 16px;
        margin-bottom: 16px;
      }
      .zone-header {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 8px;
      }
      .zone-header ha-textfield {
        flex: 1;
      }
      .section-label {
        display: block;
        font-size: 14px;
        font-weight: 500;
        color: var(--primary-text-color);
        margin-bottom: 4px;
      }
      .field-hint {
        font-size: 12px;
        color: var(--secondary-text-color);
        margin: 4px 0 8px;
        line-height: 1.4;
      }
      .field-row {
        margin-top: 12px;
      }
      .add-button {
        margin-top: 8px;
      }

      .number-fields {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 12px;
        margin-top: 12px;
      }
      @media (max-width: 500px) {
        .number-fields {
          grid-template-columns: 1fr;
        }
      }

      .room-toggles {
        display: flex;
        flex-direction: column;
        gap: 4px;
        margin-top: 8px;
      }
      .room-toggle-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 4px 0;
      }
      .room-toggle-name {
        font-size: 14px;
        color: var(--primary-text-color);
      }
      .room-toggle-other {
        font-size: 12px;
        color: var(--secondary-text-color);
        margin-left: 6px;
      }

      .priority-card {
        border: 1px solid var(--divider-color);
        border-radius: 8px;
        padding: 12px;
        margin-top: 12px;
      }
      .priority-row {
        display: flex;
        align-items: center;
        gap: 8px;
      }
      .priority-row ha-select {
        flex: 1;
        min-width: 0;
      }
      .priority-card ha-entity-picker {
        margin-top: 8px;
      }

      .status-card {
        margin-top: 12px;
        padding: 10px 12px;
        border-radius: 8px;
        background: var(--secondary-background-color);
        border-left: 3px solid var(--divider-color);
      }
      .status-card.status-forcing {
        border-left-color: var(--primary-color);
      }
      .status-line {
        display: flex;
        align-items: center;
        gap: 8px;
        --mdc-icon-size: 18px;
        color: var(--secondary-text-color);
      }
      .status-state {
        font-size: 14px;
        font-weight: 500;
        color: var(--primary-text-color);
      }
      .status-room {
        font-size: 13px;
        color: var(--secondary-text-color);
      }
      .status-bias {
        margin-left: auto;
        font-size: 13px;
        font-weight: 500;
        color: var(--primary-color);
      }
      .status-reason {
        margin-top: 4px;
        font-size: 12px;
        color: var(--secondary-text-color);
        line-height: 1.4;
      }
      .status-lockout {
        display: flex;
        align-items: center;
        gap: 6px;
        margin-top: 6px;
        font-size: 12px;
        color: var(--warning-color, #ff9800);
        --mdc-icon-size: 14px;
      }
    `,
  ];
}

declare global {
  interface HTMLElementTagNameMap {
    "rmc-settings-single-zone": RsSettingsSingleZone;
  }
}
