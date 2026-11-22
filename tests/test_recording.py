"""Translate Tk key events to shortcuts the Windows worker can register."""

import pytest

from main import _pressed_modifiers
from settings import Hotkey, hotkey_from_keypress


@pytest.mark.parametrize(
    ("key_code", "state", "expected"),
    [
        (0x31, 0x0005, Hotkey("Ctrl+Shift+1", 0x0006, 0x31)),
        (0x77, 0x20005, Hotkey("Ctrl+Alt+Shift+F8", 0x0007, 0x77)),
        (0x1B, 0x0001, Hotkey("Shift+Esc", 0x0004, 0x1B)),
        (0x41, 0x2000A, Hotkey("Alt+A", 0x0001, 0x41)),
    ],
)
def test_recorded_key_is_normalized(key_code, state, expected):
    assert (
        hotkey_from_keypress(key_code, _pressed_modifiers(state), "activation")
        == expected
    )


@pytest.mark.parametrize(
    ("key_code", "state", "message"),
    [
        (0x77, 0x0000, "plus one key"),
        (0x7B, 0x0004, "F1–F11"),
        (0x61, 0x0004, "F1–F11"),
    ],
)
def test_invalid_recorded_key_is_rejected(key_code, state, message):
    with pytest.raises(ValueError, match=message):
        hotkey_from_keypress(key_code, _pressed_modifiers(state), "failsafe")
