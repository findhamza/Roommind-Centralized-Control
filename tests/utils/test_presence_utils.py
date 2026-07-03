"""Tests for presence_utils.py."""

from __future__ import annotations

from unittest.mock import MagicMock

from custom_components.roommind_cc.utils.presence_utils import is_presence_away


def _make_state(entity_id: str, state: str) -> MagicMock:
    s = MagicMock()
    s.entity_id = entity_id
    s.state = state
    return s


def test_presence_enabled_but_no_persons_returns_false():
    """When presence_enabled but presence_persons is empty, return False."""
    hass = MagicMock()
    settings = {"presence_enabled": True, "presence_persons": []}
    assert is_presence_away(hass, {}, settings) is False


def test_binary_sensor_off_counts_as_away():
    """binary_sensor 'off' means not home → if all away, returns True."""
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=_make_state("binary_sensor.motion", "off"))
    settings = {"presence_enabled": True, "presence_persons": ["binary_sensor.motion"]}
    assert is_presence_away(hass, {}, settings) is True


def test_binary_sensor_on_counts_as_home():
    """binary_sensor 'on' means home → returns False."""
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=_make_state("binary_sensor.motion", "on"))
    settings = {"presence_enabled": True, "presence_persons": ["binary_sensor.motion"]}
    assert is_presence_away(hass, {}, settings) is False


def test_presence_disabled_returns_false():
    """When presence_enabled is False, always return False regardless of person states."""
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=_make_state("person.kevin", "not_home"))
    settings = {"presence_enabled": False, "presence_persons": ["person.kevin"]}
    assert is_presence_away(hass, {}, settings) is False


def test_person_domain_home():
    """person.kevin with state 'home' means not away."""
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=_make_state("person.kevin", "home"))
    settings = {"presence_enabled": True, "presence_persons": ["person.kevin"]}
    assert is_presence_away(hass, {}, settings) is False


def test_person_domain_not_home():
    """person.kevin with state 'not_home' means away."""
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=_make_state("person.kevin", "not_home"))
    settings = {"presence_enabled": True, "presence_persons": ["person.kevin"]}
    assert is_presence_away(hass, {}, settings) is True


def test_device_tracker_home():
    """device_tracker.phone with state 'home' means not away."""
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=_make_state("device_tracker.phone", "home"))
    settings = {"presence_enabled": True, "presence_persons": ["device_tracker.phone"]}
    assert is_presence_away(hass, {}, settings) is False


def test_device_tracker_away():
    """device_tracker.phone with state 'not_home' means away."""
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=_make_state("device_tracker.phone", "not_home"))
    settings = {"presence_enabled": True, "presence_persons": ["device_tracker.phone"]}
    assert is_presence_away(hass, {}, settings) is True


def test_input_boolean_on_is_home():
    """input_boolean.guest with state 'on' means home (not away)."""
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=_make_state("input_boolean.guest", "on"))
    settings = {"presence_enabled": True, "presence_persons": ["input_boolean.guest"]}
    assert is_presence_away(hass, {}, settings) is False


def test_input_boolean_off_is_away():
    """input_boolean.guest with state 'off' means away."""
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=_make_state("input_boolean.guest", "off"))
    settings = {"presence_enabled": True, "presence_persons": ["input_boolean.guest"]}
    assert is_presence_away(hass, {}, settings) is True


def test_multiple_persons_one_home():
    """Two persons, one home one away. Someone is home so not away."""
    hass = MagicMock()
    states = {
        "person.kevin": _make_state("person.kevin", "home"),
        "person.lisa": _make_state("person.lisa", "not_home"),
    }
    hass.states.get = MagicMock(side_effect=states.get)
    settings = {"presence_enabled": True, "presence_persons": ["person.kevin", "person.lisa"]}
    assert is_presence_away(hass, {}, settings) is False


def test_multiple_persons_all_away():
    """Two persons, both away. Everyone is away."""
    hass = MagicMock()
    states = {
        "person.kevin": _make_state("person.kevin", "not_home"),
        "person.lisa": _make_state("person.lisa", "not_home"),
    }
    hass.states.get = MagicMock(side_effect=states.get)
    settings = {"presence_enabled": True, "presence_persons": ["person.kevin", "person.lisa"]}
    assert is_presence_away(hass, {}, settings) is True


def test_person_unavailable_treated_as_home():
    """Unavailable state is fail-safe treated as home (returns False)."""
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=_make_state("person.kevin", "unavailable"))
    settings = {"presence_enabled": True, "presence_persons": ["person.kevin"]}
    assert is_presence_away(hass, {}, settings) is False


def test_person_unknown_treated_as_home():
    """Unknown state is fail-safe treated as home (returns False)."""
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=_make_state("person.kevin", "unknown"))
    settings = {"presence_enabled": True, "presence_persons": ["person.kevin"]}
    assert is_presence_away(hass, {}, settings) is False


def test_person_entity_missing_treated_as_home():
    """When hass.states.get returns None, fail-safe treats as home."""
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=None)
    settings = {"presence_enabled": True, "presence_persons": ["person.kevin"]}
    assert is_presence_away(hass, {}, settings) is False


def test_room_persons_override_global():
    """Per-room presence_persons take precedence over global."""
    hass = MagicMock()
    states = {
        "person.kevin": _make_state("person.kevin", "home"),
        "person.lisa": _make_state("person.lisa", "not_home"),
    }
    hass.states.get = MagicMock(side_effect=states.get)
    settings = {"presence_enabled": True, "presence_persons": ["person.kevin"]}
    room = {"presence_persons": ["person.lisa"]}
    # Room overrides global. Only lisa checked, she is away.
    assert is_presence_away(hass, room, settings) is True


def test_room_persons_empty_falls_back_to_global():
    """Empty room presence_persons falls back to global."""
    hass = MagicMock()
    hass.states.get = MagicMock(return_value=_make_state("person.kevin", "not_home"))
    settings = {"presence_enabled": True, "presence_persons": ["person.kevin"]}
    room = {"presence_persons": []}
    assert is_presence_away(hass, room, settings) is True


def test_presence_enabled_missing_defaults_false():
    """When presence_enabled key is missing from settings, defaults to False."""
    hass = MagicMock()
    settings = {"presence_persons": ["person.kevin"]}
    assert is_presence_away(hass, {}, settings) is False
