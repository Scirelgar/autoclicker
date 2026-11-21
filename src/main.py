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
    load_settings,
    save_settings,
    settings_path,
    validate_settings,
)
from windows_input import WindowsInput


class AutoclickerApp:
    def __init__(self, root: tk.Tk, settings_file: Path) -> None:
        self._root = root
        self._settings_file = settings_file
        self._session: ClickSession | None = None
        self._closing = False
        self._stopping = False
        self._settings_notice = ""

        root.title("Autoclicker")
        root.resizable(False, False)
        root.protocol("WM_DELETE_WINDOW", self._close)
        panel = ttk.Frame(root, padding=18)
        panel.grid()

        self._activation = tk.StringVar(root)
        self._failsafe = tk.StringVar(root)
        self._interval = tk.StringVar(root, value=str(DEFAULT_INTERVAL_MS))
        self._status = tk.StringVar(root)
        self._entries: list[ttk.Entry] = []
        for row, (label, variable) in enumerate(
            (
                ("Activation shortcut", self._activation),
                ("Failsafe shortcut", self._failsafe),
                ("Interval (ms)", self._interval),
            )
        ):
            ttk.Label(panel, text=label).grid(
                row=row, column=0, sticky="w", padx=(0, 16), pady=4
            )
            entry = ttk.Entry(panel, textvariable=variable, width=25)
            entry.grid(row=row, column=1, sticky="ew", pady=4)
            self._entries.append(entry)

        ttk.Label(panel, text="For example, Ctrl+Alt+F8").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(3, 12)
        )
        self._action = ttk.Button(panel, text="Arm", command=self._arm_or_disarm)
        self._action.grid(row=4, column=1, sticky="e")
        ttk.Label(
            panel, textvariable=self._status, wraplength=300, justify="left"
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(12, 0))

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
            self._set_status("Disarmed. Set both shortcuts to arm.")
            self._entries[0].focus_set()
        else:
            self._show_settings(settings)
            self._set_status("Disarmed. Click Arm to enable the shortcuts.")

    def _show_settings(self, settings: Settings) -> None:
        self._activation.set(settings.activation.label)
        self._failsafe.set(settings.failsafe.label)
        self._interval.set(str(settings.interval_ms))

    def _set_status(self, status: str) -> None:
        self._status.set(
            " ".join(part for part in (status, self._settings_notice) if part)
        )

    def _arm_or_disarm(self) -> None:
        session = self._session
        if session is not None:
            self._request_stop(session)
            return
        try:
            settings = validate_settings(
                self._activation.get(), self._failsafe.get(), self._interval.get()
            )
            session = ClickSession(settings, WindowsInput())
            session.start()
        except (ValueError, OSError, RuntimeError) as error:
            self._set_status(f"Disarmed. {error}")
            return
        self._session = session
        for entry in self._entries:
            entry.configure(state="readonly")
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
                for entry in self._entries:
                    entry.configure(state="normal")
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
