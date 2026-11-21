"""Validate user input and persisted settings."""

import json
from pathlib import Path

import pytest

from settings import Hotkey, load_settings, save_settings, validate_settings


def test_hotkeys_are_normalized():
    settings = validate_settings(" shift + ctrl + f8 ", "Alt + esc", " 120 ")

    assert settings.activation == Hotkey("Ctrl+Shift+F8", 0x0006, 0x77)
    assert settings.failsafe == Hotkey("Alt+Esc", 0x0001, 0x1B)
    assert settings.interval_ms == 120


@pytest.mark.parametrize(
    "shortcut", ["", "F9", "Win+F9", "Ctrl+Ctrl+F9", "Ctrl++A", "Ctrl+F12"]
)
def test_invalid_failsafe_prevents_arming(shortcut):
    with pytest.raises(ValueError, match="failsafe"):
        validate_settings("Ctrl+F8", shortcut, "100")


def test_activation_is_required():
    with pytest.raises(ValueError, match="activation"):
        validate_settings("", "Ctrl+F9", "100")


def test_shortcuts_must_be_distinct_after_normalization():
    with pytest.raises(ValueError, match="different"):
        validate_settings("Shift+Ctrl+F8", "ctrl + shift + f8", "100")


@pytest.mark.parametrize(
    "interval", ["", "9", "-25", "10.5", 2_147_483_648, 10.0, True, None]
)
def test_only_whole_intervals_within_timer_range_are_valid(interval):
    with pytest.raises(ValueError, match="whole interval"):
        validate_settings("Ctrl+1", "Ctrl+2", interval)


@pytest.mark.parametrize("interval", [10, 2_147_483_647])
def test_timer_range_endpoints_are_valid(interval):
    assert validate_settings("Ctrl+1", "Ctrl+2", str(interval)).interval_ms == interval


def test_settings_are_replaced_and_restored(tmp_path):
    path = tmp_path / "Autoclicker" / "settings.json"
    first = validate_settings("Ctrl+F8", "Ctrl+F9", "100")
    updated = validate_settings("Shift+F10", "Shift+F11", "500")

    assert load_settings(path) is None
    save_settings(path, first)
    assert load_settings(path) == first
    save_settings(path, updated)
    assert load_settings(path) == updated
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "activation": "Shift+F10",
        "failsafe": "Shift+F11",
        "interval_ms": 500,
    }


@pytest.mark.parametrize("content", ["{", "[]", "{}", '{"interval_ms":true}'])
def test_corrupt_or_incomplete_settings_are_rejected(tmp_path, content):
    path = tmp_path / "settings.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError):
        load_settings(path)


def test_failed_replacement_preserves_previous_settings(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    settings = validate_settings("Ctrl+F8", "Ctrl+F9", "100")
    save_settings(path, settings)

    def denied_replace(self, target):
        raise PermissionError("file is locked")

    monkeypatch.setattr(Path, "replace", denied_replace)
    with pytest.raises(PermissionError):
        save_settings(path, validate_settings("Shift+F8", "Shift+F9", "200"))
    assert load_settings(path) == settings
    assert list(tmp_path.iterdir()) == [path]
