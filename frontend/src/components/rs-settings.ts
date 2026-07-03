/**
 * rs-settings – Global RoomMind settings page (orchestrator).
 * Owns all state, loads/saves settings, delegates rendering to sub-components
 * wrapped in ha-expansion-panel accordion sections.
 */
import { LitElement, html, css } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import type {
  HomeAssistant,
  GlobalSettings,
  RoomConfig,
  NotificationTarget,
  CompressorGroup,
  PriorityZone,
} from "../types";
import { localize } from "../utils/localize";
import { fireSaveStatus } from "../utils/events";
import { VACATION_SENTINEL } from "../utils/constants";
import "./settings/rs-settings-panel";
import "./settings/rs-settings-general";
import "./settings/rs-settings-sensors";
import "./settings/rs-settings-control";
import "./settings/rs-settings-presence";
import "./settings/rs-settings-vacation";
import "./settings/rs-settings-valve";
import "./settings/rs-settings-compressor";
import "./settings/rs-settings-single-zone";
import "./settings/rs-settings-mold";
import "./settings/rs-settings-notifications";
import "./settings/rs-settings-learning";
import "./settings/rs-settings-reset";

@customElement("rmc-settings")
export class RsSettings extends LitElement {
  @property({ attribute: false }) public hass!: HomeAssistant;
  @property({ attribute: false }) public rooms: Record<string, RoomConfig> = {};

  @state() private _groupByFloor = false;
  @state() private _climateControlActive = true;
  @state() private _learningDisabledRooms: string[] = [];
  @state() private _outdoorTempSensor = "";
  @state() private _outdoorHumiditySensor = "";
  @state() private _outdoorCoolingMin = 16;
  @state() private _outdoorHeatingMax = 22;
  @state() private _controlMode: "mpc" | "bangbang" = "mpc";
  @state() private _comfortWeight = 70;
  @state() private _weatherEntity = "";
  @state() private _outdoorUnavailableNotify = true;
  @state() private _predictionEnabled = true;
  @state() private _vacationActive = false;
  @state() private _vacationTemp = 15;
  @state() private _vacationUntil = "";
  @state() private _presenceEnabled = false;
  @state() private _presencePersons: string[] = [];
  @state() private _presenceAwayAction: "eco" | "off" = "eco";
  @state() private _presenceClearsOverride = false;
  @state() private _scheduleOffAction: "eco" | "off" = "eco";
  @state() private _valveProtectionEnabled = false;
  @state() private _valveProtectionInterval = 7;
  @state() private _moldDetectionEnabled = false;
  @state() private _moldHumidityThreshold = 70;
  @state() private _moldSustainedMinutes = 30;
  @state() private _moldNotificationCooldown = 60;
  @state() private _moldNotificationsEnabled = true;
  @state() private _moldNotificationTargets: NotificationTarget[] = [];
  @state() private _moldPreventionEnabled = false;
  @state() private _moldPreventionIntensity: "light" | "medium" | "strong" = "medium";
  @state() private _moldPreventionNotify = false;
  @state() private _compressorGroups: CompressorGroup[] = [];
  @state() private _priorityZones: PriorityZone[] = [];
  @state() private _boostAppliedAt: Record<string, number> = {};
  @state() private _loaded = false;

  private _saveDebounce?: ReturnType<typeof setTimeout>;

  connectedCallback() {
    super.connectedCallback();
    this._loadSettings();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._saveDebounce) clearTimeout(this._saveDebounce);
  }

  private async _loadSettings() {
    try {
      const result = await this.hass.callWS<{ settings: GlobalSettings }>({
        type: "roommind_cc/settings/get",
      });
      const s = result.settings;
      this._groupByFloor = s.group_by_floor ?? false;
      this._climateControlActive = s.climate_control_active ?? true;
      this._learningDisabledRooms = s.learning_disabled_rooms ?? [];
      this._outdoorTempSensor = s.outdoor_temp_sensor ?? "";
      this._outdoorHumiditySensor = s.outdoor_humidity_sensor ?? "";
      this._outdoorCoolingMin = s.outdoor_cooling_min ?? 16;
      this._outdoorHeatingMax = s.outdoor_heating_max ?? 22;
      this._controlMode = s.control_mode ?? "mpc";
      this._comfortWeight = s.comfort_weight ?? 70;
      this._weatherEntity = s.weather_entity ?? "";
      this._outdoorUnavailableNotify = s.outdoor_unavailable_notify ?? true;
      this._predictionEnabled = s.prediction_enabled ?? true;
      const vUntil = s.vacation_until;
      this._vacationActive = !!(vUntil && vUntil > Date.now() / 1000);
      this._vacationTemp = s.vacation_temp ?? 15;
      if (vUntil && vUntil > Date.now() / 1000 && vUntil < VACATION_SENTINEL) {
        this._vacationUntil = this._tsToDatetimeLocal(vUntil);
      } else {
        this._vacationUntil = "";
      }
      this._presenceEnabled = s.presence_enabled ?? false;
      this._presencePersons = s.presence_persons ?? [];
      this._presenceAwayAction = s.presence_away_action ?? "eco";
      this._presenceClearsOverride = s.presence_clears_override ?? false;
      this._scheduleOffAction = s.schedule_off_action ?? "eco";
      this._valveProtectionEnabled = s.valve_protection_enabled ?? false;
      this._valveProtectionInterval = s.valve_protection_interval_days ?? 7;
      this._moldDetectionEnabled = s.mold_detection_enabled ?? false;
      this._moldHumidityThreshold = s.mold_humidity_threshold ?? 70;
      this._moldSustainedMinutes = s.mold_sustained_minutes ?? 30;
      this._moldNotificationCooldown = s.mold_notification_cooldown ?? 60;
      this._moldNotificationsEnabled = s.mold_notifications_enabled ?? true;
      this._moldNotificationTargets = s.mold_notification_targets ?? [];
      this._moldPreventionEnabled = s.mold_prevention_enabled ?? false;
      this._moldPreventionIntensity = s.mold_prevention_intensity ?? "medium";
      this._moldPreventionNotify = s.mold_prevention_notify_enabled ?? false;
      this._compressorGroups = s.compressor_groups ?? [];
      this._priorityZones = s.priority_zones ?? [];
      this._boostAppliedAt = s.boost_applied_at ?? {};
    } catch (err) {
      // eslint-disable-next-line no-console
      console.debug("[RoomMind] loadSettings:", err);
    } finally {
      this._loaded = true;
    }
  }

  protected render() {
    if (!this._loaded) {
      return html`<div class="loading">${localize("panel.loading", this.hass.language)}</div>`;
    }

    const l = this.hass.language;

    return html`
      <rmc-settings-panel
        icon="mdi:power"
        .heading=${localize("settings.general_title", l)}
        .intro=${localize("settings.intro.general", l)}
      >
        <rmc-settings-general
          .hass=${this.hass}
          .groupByFloor=${this._groupByFloor}
          .climateControlActive=${this._climateControlActive}
          @setting-changed=${this._onSettingChanged}
        ></rmc-settings-general>
      </rmc-settings-panel>

      <rmc-settings-panel
        icon="mdi:thermometer"
        .heading=${localize("settings.sensors_title", l)}
        .intro=${localize("settings.intro.sensors", l)}
      >
        <rmc-settings-sensors
          .hass=${this.hass}
          .outdoorTempSensor=${this._outdoorTempSensor}
          .outdoorHumiditySensor=${this._outdoorHumiditySensor}
          .weatherEntity=${this._weatherEntity}
          .outdoorUnavailableNotify=${this._outdoorUnavailableNotify}
          @setting-changed=${this._onSettingChanged}
        ></rmc-settings-sensors>
      </rmc-settings-panel>

      <rmc-settings-panel
        icon="mdi:tune-variant"
        .heading=${localize("settings.control_title", l)}
        .intro=${localize("settings.intro.control", l)}
      >
        <rmc-settings-control
          .hass=${this.hass}
          .controlMode=${this._controlMode}
          .comfortWeight=${this._comfortWeight}
          .outdoorCoolingMin=${this._outdoorCoolingMin}
          .outdoorHeatingMax=${this._outdoorHeatingMax}
          .predictionEnabled=${this._predictionEnabled}
          .scheduleOffAction=${this._scheduleOffAction}
          @setting-changed=${this._onSettingChanged}
        ></rmc-settings-control>
      </rmc-settings-panel>

      <rmc-settings-panel
        icon="mdi:home-account"
        .heading=${localize("presence.title", l)}
        .intro=${localize("settings.intro.presence", l)}
      >
        <rmc-settings-presence
          .hass=${this.hass}
          .presenceEnabled=${this._presenceEnabled}
          .presencePersons=${this._presencePersons}
          .presenceAwayAction=${this._presenceAwayAction}
          .presenceClearsOverride=${this._presenceClearsOverride}
          @setting-changed=${this._onSettingChanged}
        ></rmc-settings-presence>
      </rmc-settings-panel>

      <rmc-settings-panel
        icon="mdi:airplane"
        .heading=${localize("vacation.title", l)}
        .intro=${localize("settings.intro.vacation", l)}
      >
        <rmc-settings-vacation
          .hass=${this.hass}
          .vacationActive=${this._vacationActive}
          .vacationTemp=${this._vacationTemp}
          .vacationUntil=${this._vacationUntil}
          @setting-changed=${this._onSettingChanged}
        ></rmc-settings-vacation>
      </rmc-settings-panel>

      <rmc-settings-panel
        icon="mdi:shield-refresh"
        .heading=${localize("valve_protection.title", l)}
        .intro=${localize("settings.intro.valve", l)}
      >
        <rmc-settings-valve
          .hass=${this.hass}
          .valveProtectionEnabled=${this._valveProtectionEnabled}
          .valveProtectionInterval=${this._valveProtectionInterval}
          @setting-changed=${this._onSettingChanged}
        ></rmc-settings-valve>
      </rmc-settings-panel>

      <rmc-settings-panel
        icon="mdi:heat-pump-outline"
        .heading=${localize("compressor.title", l)}
        .intro=${localize("settings.intro.compressor", l)}
      >
        <rmc-settings-compressor
          .hass=${this.hass}
          .compressorGroups=${this._compressorGroups}
          @setting-changed=${this._onSettingChanged}
        ></rmc-settings-compressor>
      </rmc-settings-panel>

      <rmc-settings-panel
        icon="mdi:home-thermometer"
        .heading=${localize("single_zone.title", l)}
        .intro=${localize("settings.intro.single_zone", l)}
      >
        <rmc-settings-single-zone
          .hass=${this.hass}
          .rooms=${this.rooms}
          .zones=${this._priorityZones}
          @setting-changed=${this._onSettingChanged}
        ></rmc-settings-single-zone>
      </rmc-settings-panel>

      <rmc-settings-panel
        icon="mdi:water-alert"
        .heading=${localize("mold.title", l)}
        .intro=${localize("settings.intro.mold", l)}
      >
        <rmc-settings-mold
          .hass=${this.hass}
          .moldDetectionEnabled=${this._moldDetectionEnabled}
          .moldHumidityThreshold=${this._moldHumidityThreshold}
          .moldSustainedMinutes=${this._moldSustainedMinutes}
          .moldPreventionEnabled=${this._moldPreventionEnabled}
          .moldPreventionIntensity=${this._moldPreventionIntensity}
          @setting-changed=${this._onSettingChanged}
        ></rmc-settings-mold>
      </rmc-settings-panel>

      <rmc-settings-panel
        icon="mdi:bell-outline"
        .heading=${localize("notifications.title", l)}
        .intro=${localize("settings.intro.notifications", l)}
        .badge=${localize("badge.beta", l)}
        .badgeHint=${localize("badge.beta_hint", l)}
      >
        <rmc-settings-notifications
          .hass=${this.hass}
          .notificationsEnabled=${this._moldNotificationsEnabled}
          .notificationTargets=${this._moldNotificationTargets}
          .notificationCooldown=${this._moldNotificationCooldown}
          .moldPreventionEnabled=${this._moldPreventionEnabled}
          .moldPreventionNotify=${this._moldPreventionNotify}
          @setting-changed=${this._onSettingChanged}
        ></rmc-settings-notifications>
      </rmc-settings-panel>

      <rmc-settings-panel
        icon="mdi:brain"
        .heading=${localize("settings.learning_title", l)}
        .intro=${localize("settings.intro.learning", l)}
      >
        <rmc-settings-learning
          .hass=${this.hass}
          .rooms=${this.rooms}
          .learningDisabledRooms=${this._learningDisabledRooms}
          .boostAppliedAt=${this._boostAppliedAt}
          .roomsLive=${Object.fromEntries(
            // eslint-disable-next-line @typescript-eslint/no-explicit-any -- HA room data includes untyped live state
            Object.entries(this.rooms).map(([id, r]) => [id, (r as any).live ?? {}]),
          )}
          @setting-changed=${this._onSettingChanged}
          @boost-applied=${this._onBoostApplied}
        ></rmc-settings-learning>
      </rmc-settings-panel>

      <rmc-settings-panel
        icon="mdi:restart"
        .heading=${localize("settings.reset_title", l)}
        .intro=${localize("settings.intro.reset", l)}
      >
        <rmc-settings-reset .hass=${this.hass} .rooms=${this.rooms}></rmc-settings-reset>
      </rmc-settings-panel>
    `;
  }

  private _onBoostApplied(e: CustomEvent<{ area_id: string; n_observations: number }>) {
    const { area_id, n_observations } = e.detail;
    this._boostAppliedAt = { ...this._boostAppliedAt, [area_id]: n_observations };
  }

  private _onSettingChanged(e: CustomEvent<{ key: string; value: unknown }>) {
    const { key, value } = e.detail;
    (this as Record<string, unknown>)[`_${key}`] = value;
    this._autoSave();
  }

  private _tsToDatetimeLocal(ts: number): string {
    const d = new Date(ts * 1000);
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  private _autoSave() {
    if (this._saveDebounce) clearTimeout(this._saveDebounce);
    this._saveDebounce = setTimeout(() => this._doSave(), 500);
  }

  private async _doSave() {
    fireSaveStatus(this, "saving");

    try {
      await this.hass.callWS({
        type: "roommind_cc/settings/save",
        group_by_floor: this._groupByFloor,
        climate_control_active: this._climateControlActive,
        learning_disabled_rooms: this._learningDisabledRooms,
        outdoor_temp_sensor: this._outdoorTempSensor,
        outdoor_humidity_sensor: this._outdoorHumiditySensor,
        outdoor_cooling_min: this._outdoorCoolingMin,
        outdoor_heating_max: this._outdoorHeatingMax,
        control_mode: this._controlMode,
        comfort_weight: this._comfortWeight,
        weather_entity: this._weatherEntity,
        outdoor_unavailable_notify: this._outdoorUnavailableNotify,
        prediction_enabled: this._predictionEnabled,
        vacation_temp: this._vacationTemp,
        vacation_until: this._vacationActive
          ? this._vacationUntil
            ? new Date(this._vacationUntil).getTime() / 1000
            : VACATION_SENTINEL
          : null,
        presence_enabled: this._presenceEnabled,
        presence_persons: this._presencePersons.filter((p) => p),
        presence_away_action: this._presenceAwayAction,
        presence_clears_override: this._presenceClearsOverride,
        schedule_off_action: this._scheduleOffAction,
        valve_protection_enabled: this._valveProtectionEnabled,
        valve_protection_interval_days: this._valveProtectionInterval,
        compressor_groups: this._compressorGroups.filter((g) => g.members.length > 0),
        priority_zones: this._priorityZones.filter((z) => z.id),
        mold_detection_enabled: this._moldDetectionEnabled,
        mold_humidity_threshold: this._moldHumidityThreshold,
        mold_sustained_minutes: this._moldSustainedMinutes,
        mold_notification_cooldown: this._moldNotificationCooldown,
        mold_notifications_enabled: this._moldNotificationsEnabled,
        mold_notification_targets: this._moldNotificationTargets.filter((t) => t.entity_id),
        mold_prevention_enabled: this._moldPreventionEnabled,
        mold_prevention_intensity: this._moldPreventionIntensity,
        mold_prevention_notify_enabled: this._moldPreventionNotify,
        mold_prevention_notify_targets: this._moldPreventionNotify
          ? this._moldNotificationTargets.filter((t) => t.entity_id)
          : [],
      });
      fireSaveStatus(this, "saved");
    } catch {
      fireSaveStatus(this, "error");
    }
  }

  static styles = css`
    :host {
      display: flex;
      flex-direction: column;
      gap: 12px;
      padding: 0 16px;
    }

    .loading {
      padding: 80px 16px;
      text-align: center;
      color: var(--secondary-text-color);
    }
  `;
}

declare global {
  interface HTMLElementTagNameMap {
    "rmc-settings": RsSettings;
  }
}
