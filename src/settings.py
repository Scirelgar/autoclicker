"""Validate and store the autoclicker's settings."""

import json
import os
import string
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

DEFAULT_INTERVAL_MS = 100
MIN_INTERVAL_MS = 10
MAX_INTERVAL_MS = 2_147_483_647

# Windows modifier flags and virtual-key codes accepted by RegisterHotKey.
_MODIFIERS = {"CTRL": 0x0002, "ALT": 0x0001, "SHIFT": 0x0004}
_KEYS = (
    {key: ord(key) for key in string.ascii_uppercase + string.digits}
    | {f"F{number}": 0x6F + number for number in range(1, 12)}
    | {"ESC": 0x1B}
)


@dataclass(frozen=True)
class Hotkey:
    label: str
    modifiers: int
    key_code: int


@dataclass(frozen=True)
class Settings:
    activation: Hotkey
    failsafe: Hotkey
    interval_ms: int


def _parse_hotkey(value: object, field: str) -> Hotkey:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Enter a {field} shortcut.")

    *modifiers, key = [part.strip().upper() for part in value.split("+")]
    unique_modifiers = set(modifiers)
    if (
        not modifiers
        or not unique_modifiers <= _MODIFIERS.keys()
        or len(unique_modifiers) != len(modifiers)
    ):
        raise ValueError(
            f"Use Ctrl, Alt, or Shift plus one key for the {field} shortcut."
        )
    if key not in _KEYS:
        raise ValueError(f"Use A–Z, 0–9, F1–F11, or Esc for the {field} shortcut key.")

    ordered_modifiers = [
        modifier for modifier in _MODIFIERS if modifier in unique_modifiers
    ]
    parts = [modifier.title() for modifier in ordered_modifiers]
    parts.append("Esc" if key == "ESC" else key)
    flags = sum(_MODIFIERS[modifier] for modifier in ordered_modifiers)
    return Hotkey("+".join(parts), flags, _KEYS[key])


def _parse_interval(value: object) -> int:
    message = (
        f"Enter a whole interval from {MIN_INTERVAL_MS:,} to {MAX_INTERVAL_MS:,} ms."
    )
    if isinstance(value, str):
        text = value.strip()
        if not text.isascii() or not text.isdecimal():
            raise ValueError(message)
        try:
            value = int(text)
        except ValueError:
            raise ValueError(message) from None
    if type(value) is not int or not MIN_INTERVAL_MS <= value <= MAX_INTERVAL_MS:
        raise ValueError(message)
    return value


def validate_settings(
    activation: object, failsafe: object, interval_ms: object
) -> Settings:
    """Return normalized settings that can be passed to the input worker."""
    activation_key = _parse_hotkey(activation, "activation")
    failsafe_key = _parse_hotkey(failsafe, "failsafe")
    if activation_key == failsafe_key:
        raise ValueError("Choose different activation and failsafe shortcuts.")
    return Settings(activation_key, failsafe_key, _parse_interval(interval_ms))


def settings_path() -> Path:
    """Return the per-user path used by the Windows app."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / "Autoclicker" / "settings.json"


def load_settings(path: Path) -> Settings | None:
    """Load and validate settings, returning None on first launch."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(data, dict):
        raise ValueError("The settings file must contain a JSON object.")
    return validate_settings(
        data.get("activation"), data.get("failsafe"), data.get("interval_ms")
    )


def save_settings(path: Path, settings: Settings) -> None:
    """Replace the saved settings with a complete file."""
    data = {
        "activation": settings.activation.label,
        "failsafe": settings.failsafe.label,
        "interval_ms": settings.interval_ms,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as file:
            temporary_path = Path(file.name)
            json.dump(data, file, indent=2)
            file.write("\n")
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
