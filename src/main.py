"""Launch the Windows autoclicker."""

import sys
import tkinter as tk
from pathlib import Path
from queue import Empty
from tkinter import ttk

from clicker import ClickSession, State
from settings import (
    DEFAULT_INTERVAL_MS,
    Settings,
    hotkey_from_keypress,
    load_settings,
    save_settings,
    settings_path,
    validate_settings,
)
from windows_input import WindowsInput, windows_key_pressed

_TAB_KEY = 0x09
_ESCAPE_KEY = 0x1B
_MODIFIER_KEYS = (0x10, 0x11, 0x12)  # VK_SHIFT, VK_CONTROL, VK_MENU
# Tk for Windows uses its logical Alt bit on native keyboard events.
_TK_MODIFIERS = {"CTRL": 0x0004, "ALT": 0x20000, "SHIFT": 0x0001}


def _pressed_modifiers(state: int) -> list[str]:
    return [name for name, mask in _TK_MODIFIERS.items() if state & mask]


class AutoclickerApp:
    def __init__(self, root: tk.Tk, settings_file: Path) -> None:
        self._root = root
        self._settings_file = settings_file
        self._session: ClickSession | None = None
        self._closing = False
        self._stopping = False
        self._settings_notice = ""
        self._recording: str | None = None
        self._shortcuts: dict[str, str] = {}
        self._shortcut_labels: dict[str, ttk.Label] = {}
        self._record_buttons: dict[str, ttk.Button] = {}

        root.title("Autoclicker")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self._close)
        panel = ttk.Frame(root, padding=18)
        panel.grid()

        self._interval = tk.StringVar(root, value=str(DEFAULT_INTERVAL_MS))
        self._status = tk.StringVar(root)
        for row, field in enumerate(("activation", "failsafe")):
            ttk.Label(panel, text=f"{field.title()} shortcut").grid(
                row=row, column=0, sticky="w", padx=(0, 16), pady=4
            )
            label = ttk.Label(panel, text="Not set", width=21)
            label.grid(row=row, column=1, sticky="w", pady=4)
            self._shortcut_labels[field] = label
            button = ttk.Button(
                panel, text="Record", command=lambda field=field: self._record(field)
            )
            button.grid(row=row, column=2, sticky="e", padx=(12, 0), pady=4)
            button.bind("<KeyPress>", self._record_key)
            button.bind(
                "<FocusOut>", lambda event, field=field: self._cancel_recording(field)
            )
            self._record_buttons[field] = button

        ttk.Label(panel, text="Interval (ms)").grid(row=2, column=0, sticky="w", pady=4)
        self._interval_entry = ttk.Entry(panel, textvariable=self._interval, width=21)
        self._interval_entry.grid(row=2, column=1, sticky="ew", pady=4)
        self._action = ttk.Button(panel, text="Arm", command=self._arm_or_disarm)
        self._action.grid(row=3, column=2, sticky="e", pady=(8, 0))
        ttk.Label(
            panel, textvariable=self._status, wraplength=350, justify="left"
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(12, 0))

        self._restore_settings()

    def run(self) -> None:
        try:
            self._root.mainloop()
        finally:
            if self._session is not None:
                self._session.request_stop()
                self._session.join()

    def _restore_settings(self) -> None:
        try:
            settings = load_settings(self._settings_file)
        except (OSError, ValueError) as error:
            self._settings_notice = f"Could not load settings. {error}"
            settings = None
        if settings is None:
            self._set_status("Disarmed. Record both shortcuts to arm.")
            self._record_buttons["activation"].focus_set()
        else:
            self._show_settings(settings)
            self._set_status("Disarmed. Click Arm to enable the shortcuts.")

    def _show_settings(self, settings: Settings) -> None:
        self._show_shortcut("activation", settings.activation.label)
        self._show_shortcut("failsafe", settings.failsafe.label)
        self._interval.set(str(settings.interval_ms))

    def _show_shortcut(self, field: str, label: str) -> None:
        self._shortcuts[field] = label
        self._shortcut_labels[field].configure(text=label)

    def _record(self, field: str) -> None:
        if self._session is not None:
            return
        if self._recording is not None:
            previous = self._recording
            self._cancel_recording(previous)
            if previous == field:
                return
        self._recording = field
        button = self._record_buttons[field]
        button.configure(text="Cancel")
        button.focus_set()
        self._action.state(["disabled"])
        self._set_status(
            f"Hold Ctrl, Alt, or Shift and press a key for {field}. Press Esc to cancel."
        )

    def _cancel_recording(self, field: str) -> None:
        if self._recording == field:
            self._finish_recording(field, "Recording canceled.")

    def _finish_recording(self, field: str, status: str) -> None:
        self._recording = None
        self._record_buttons[field].configure(text="Record")
        self._action.state(["!disabled"])
        self._set_status(status)

    def _record_key(self, event: tk.Event) -> str | None:
        field = self._recording
        if field is None or event.widget is not self._record_buttons[field]:
            return None
        if event.keycode in _MODIFIER_KEYS:
            return "break"
        if windows_key_pressed():
            self._set_status(
                "Use Ctrl, Alt, or Shift plus one key. Press Esc to cancel."
            )
            return "break"
        modifiers = _pressed_modifiers(event.state)
        if event.keycode == _TAB_KEY and modifiers in ([], ["SHIFT"]):
            return None
        if event.keycode == _ESCAPE_KEY and not modifiers:
            self._cancel_recording(field)
            return "break"
        try:
            hotkey = hotkey_from_keypress(event.keycode, modifiers, field)
        except ValueError as error:
            self._set_status(f"{error} Press Esc to cancel.")
        else:
            self._show_shortcut(field, hotkey.label)
            self._finish_recording(field, f"{field.title()} shortcut recorded.")
        return "break"

    def _set_status(self, status: str) -> None:
        self._status.set(
            " ".join(part for part in (status, self._settings_notice) if part)
        )

    def _arm_or_disarm(self) -> None:
        session = self._session
        if session is not None:
            self._request_stop(session)
            return
        if self._recording is not None:
            return
        try:
            settings = validate_settings(
                self._shortcuts.get("activation"),
                self._shortcuts.get("failsafe"),
                self._interval.get(),
            )
            session = ClickSession(settings, WindowsInput())
            session.start()
        except (ValueError, OSError, RuntimeError) as error:
            self._set_status(f"Disarmed. {error}")
            return
        self._session = session
        for button in self._record_buttons.values():
            button.state(["disabled"])
        self._interval_entry.state(["readonly"])
        self._action.configure(text="Disarm")
        self._set_status("Arming…")
        self._root.after(30, self._poll_updates)

    def _request_stop(self, session: ClickSession) -> None:
        self._stopping = True
        self._action.state(["disabled"])
        self._set_status("Disarming…")
        try:
            session.request_stop()
        except OSError as error:
            self._closing = False
            self._action.state(["!disabled"])
            self._set_status(f"{error} Try Disarm again.")

    def _poll_updates(self) -> None:
        session = self._session
        if session is None:
            return
        while True:
            try:
                update = session.updates.get_nowait()
            except Empty:
                break
            if update.state is State.DISARMED:
                session.join()
                self._session = None
                if self._closing:
                    self._root.destroy()
                    return
                self._stopping = False
                for button in self._record_buttons.values():
                    button.state(["!disabled"])
                self._interval_entry.state(["!readonly"])
                self._action.configure(text="Arm", state="normal")
                self._set_status(
                    f"Disarmed. {update.notice}" if update.notice else "Disarmed."
                )
                return
            if update.settings is not None:
                self._show_settings(update.settings)
                self._settings_notice = ""
                try:
                    save_settings(self._settings_file, update.settings)
                except OSError as error:
                    self._settings_notice = f"Could not save settings. {error}"
            if not self._stopping:
                self._set_status(update.state.value)
        self._root.after(30, self._poll_updates)

    def _close(self) -> None:
        if self._session is None:
            self._root.destroy()
            return
        self._closing = True
        self._request_stop(self._session)


def main() -> None:
    if sys.platform != "win32":
        raise SystemExit("Autoclicker requires Windows.")
    app = AutoclickerApp(tk.Tk(), settings_path())
    app.run()


if __name__ == "__main__":
    main()
