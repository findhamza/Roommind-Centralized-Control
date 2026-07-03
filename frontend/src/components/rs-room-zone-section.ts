/**
 * rs-room-zone-section – Assign a room to a priority zone from the room view.
 *
 * The zone-level behaviour (thermostat, thresholds, bias) lives in Settings →
 * Priority Zones. This section keeps the *per-room* bits — which zone a room
 * belongs to and whether it's a priority room — next to the room's sensor and
 * comfort targets, where they belong. It edits the global `priority_zones`
 * list and bubbles the whole updated array up via `priority-zones-changed`;
 * the panel persists it.
 */
import { LitElement, html, css, nothing } from "lit";
import { customElement, property } from "lit/decorators.js";
import type { HomeAssistant, PriorityZone, SingleZoneCondition } from "../types";
import { localize } from "../utils/localize";
import { getSelectValue } from "../utils/events";
import { inputStyles } from "../styles/input-styles";

@customElement("rmc-room-zone-section")
export class RsRoomZoneSection extends LitElement {
  @property({ attribute: false }) public hass!: HomeAssistant;
  @property({ type: String }) public areaId = "";
  @property({ type: Array }) public zones: PriorityZone[] = [];

  private get _currentZoneId(): string {
    return this.zones.find((z) => (z.zone_rooms ?? []).includes(this.areaId))?.id ?? "";
  }

  private get _priorityEntry() {
    const zone = this.zones.find((z) => z.id === this._currentZoneId);
    return zone?.priority_rooms?.find((p) => p.area_id === this.areaId);
  }

  render() {
    const l = this.hass.language;
    if (this.zones.length === 0) {
      return html`<div class="empty-hint">${localize("room.zone.no_zones", l)}</div>`;
    }

    const currentZoneId = this._currentZoneId;
    const priority = this._priorityEntry;

    return html`
      <ha-select
        .label=${localize("room.zone.assign", l)}
        .value=${currentZoneId || "__none__"}
        .options=${[
          { value: "__none__", label: localize("room.zone.none", l) },
          ...this.zones.map((z) => ({ value: z.id, label: z.name || z.id })),
        ]}
        fixedMenuPosition
        @selected=${(e: Event) => {
          const v = getSelectValue(e);
          if (v !== undefined) this._assignZone(v === "__none__" ? "" : v);
        }}
        @closed=${(e: Event) => e.stopPropagation()}
        style="width: 100%;"
      >
        <ha-list-item value="__none__">${localize("room.zone.none", l)}</ha-list-item>
        ${this.zones.map((z) => html`<ha-list-item value=${z.id}>${z.name || z.id}</ha-list-item>`)}
      </ha-select>
      <div class="field-hint">${localize("room.zone.assign_hint", l)}</div>

      ${currentZoneId
        ? html`
            <div class="toggle-row">
              <div class="toggle-text">
                <span class="toggle-label">${localize("room.zone.priority", l)}</span>
                <span class="toggle-hint">${localize("room.zone.priority_hint", l)}</span>
              </div>
              <ha-switch
                .checked=${!!priority}
                @change=${(e: Event) => this._setPriority((e.target as HTMLInputElement).checked)}
              ></ha-switch>
            </div>
            ${priority
              ? html`
                  <ha-select
                    .label=${localize("room.zone.condition", l)}
                    .value=${priority.condition}
                    .options=${[
                      { value: "always", label: localize("single_zone.condition_always", l) },
                      { value: "occupied", label: localize("single_zone.condition_occupied", l) },
                      { value: "schedule", label: localize("single_zone.condition_schedule", l) },
                      { value: "sleep", label: localize("single_zone.condition_sleep", l) },
                    ]}
                    fixedMenuPosition
                    @selected=${(e: Event) => {
                      const v = getSelectValue(e) as SingleZoneCondition;
                      if (v && v !== priority.condition) this._setCondition(v);
                    }}
                    @closed=${(e: Event) => e.stopPropagation()}
                    style="width: 100%; margin-top: 12px;"
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
                    <ha-list-item value="sleep"
                      >${localize("single_zone.condition_sleep", l)}</ha-list-item
                    >
                  </ha-select>
                  ${priority.condition === "schedule"
                    ? html`
                        <ha-entity-picker
                          .hass=${this.hass}
                          .value=${priority.schedule_entity}
                          .includeDomains=${["schedule", "input_boolean", "binary_sensor"]}
                          .label=${localize("single_zone.condition_schedule_entity", l)}
                          @value-changed=${(e: CustomEvent) =>
                            this._updatePriority({
                              schedule_entity: (e.detail?.value as string) ?? "",
                            })}
                          style="margin-top: 12px;"
                        ></ha-entity-picker>
                      `
                    : nothing}
                `
              : nothing}
          `
        : nothing}
    `;
  }

  /** Move this room into *zoneId* (or remove it when empty), one zone only. */
  private _assignZone(zoneId: string) {
    const zones = this.zones.map((z) => ({
      ...z,
      zone_rooms: (z.zone_rooms ?? []).filter((r) => r !== this.areaId),
      priority_rooms: (z.priority_rooms ?? []).filter((p) => p.area_id !== this.areaId),
    }));
    if (zoneId) {
      const target = zones.find((z) => z.id === zoneId);
      if (target) target.zone_rooms = [...target.zone_rooms, this.areaId];
    }
    this._fire(zones);
  }

  private _setPriority(on: boolean) {
    const zones = this.zones.map((z) => {
      if (z.id !== this._currentZoneId) return z;
      const others = (z.priority_rooms ?? []).filter((p) => p.area_id !== this.areaId);
      return {
        ...z,
        priority_rooms: on
          ? [
              ...others,
              {
                area_id: this.areaId,
                condition: "always" as SingleZoneCondition,
                schedule_entity: "",
              },
            ]
          : others,
      };
    });
    this._fire(zones);
  }

  private _setCondition(condition: SingleZoneCondition) {
    this._updatePriority({ condition });
  }

  private _updatePriority(
    changes: Partial<{ condition: SingleZoneCondition; schedule_entity: string }>,
  ) {
    const zones = this.zones.map((z) => {
      if (z.id !== this._currentZoneId) return z;
      return {
        ...z,
        priority_rooms: (z.priority_rooms ?? []).map((p) =>
          p.area_id === this.areaId ? { ...p, ...changes } : p,
        ),
      };
    });
    this._fire(zones);
  }

  private _fire(zones: PriorityZone[]) {
    this.dispatchEvent(
      new CustomEvent("priority-zones-changed", {
        detail: { zones },
        bubbles: true,
        composed: true,
      }),
    );
  }

  static styles = [
    inputStyles,
    css`
      :host {
        display: block;
      }
      .field-hint {
        font-size: 12px;
        color: var(--secondary-text-color);
        margin: 4px 0 8px;
        line-height: 1.4;
      }
      .empty-hint {
        font-size: 13px;
        color: var(--secondary-text-color);
        line-height: 1.4;
      }
      .toggle-row {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 16px;
        margin-top: 12px;
      }
      .toggle-text {
        display: flex;
        flex-direction: column;
        gap: 4px;
        flex: 1;
      }
      .toggle-label {
        font-size: 14px;
        font-weight: 500;
        color: var(--primary-text-color);
      }
      .toggle-hint {
        font-size: 13px;
        color: var(--secondary-text-color);
        line-height: 1.4;
      }
    `,
  ];
}

declare global {
  interface HTMLElementTagNameMap {
    "rmc-room-zone-section": RsRoomZoneSection;
  }
}
