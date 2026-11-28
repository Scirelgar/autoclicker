"""Run a single armed session independently of the Tkinter event loop."""

import logging
from contextlib import ExitStack
from dataclasses import dataclass
from enum import Enum
from queue import SimpleQueue
from threading import Event, Lock, Thread, get_native_id

from settings import Settings
from windows_input import InputMessage, WindowsInput

_FAILSAFE_ID = 1
_ACTIVATION_ID = 2
_FOCUS_LOST_NOTICE = "Target window lost focus."


class State(Enum):
    DISARMED = "Disarmed"
    ARMED = "Armed"
    CLICKING = "Clicking"


@dataclass(frozen=True)
class SessionUpdate:
    state: State
    notice: str = ""
    settings: Settings | None = None


class ClickSession(Thread):
    """Own the shortcuts, focus watch, and timer until disarmed."""

    def __init__(self, settings: Settings, backend: WindowsInput) -> None:
        super().__init__(name="Autoclicker input")
        self.updates: SimpleQueue[SessionUpdate] = SimpleQueue()
        self._settings = settings
        self._backend = backend
        self._stop_requested = Event()
        self._thread_lock = Lock()
        self._thread_id: int | None = None
        self._timer_id: int | None = None
        self._target_window: int | None = None

    def request_stop(self) -> None:
        """Signal a stop even if the worker is still starting."""
        self._stop_requested.set()
        with self._thread_lock:
            if self._thread_id is not None:
                self._backend.wake_for_stop(self._thread_id)

    def run(self) -> None:
        notice = ""
        try:
            self._backend.prepare_thread()
            with self._thread_lock:
                self._thread_id = get_native_id()
            if not self._stop_requested.is_set():
                notice = self._arm_and_listen()
        except OSError as error:
            notice = str(error)
        except Exception:
            logging.exception("Autoclicker input worker failed")
            notice = "The input worker stopped unexpectedly."
        finally:
            with self._thread_lock:
                self._thread_id = None
            self.updates.put(SessionUpdate(State.DISARMED, notice))

    def _arm_and_listen(self) -> str:
        with ExitStack() as shortcuts:
            for identifier, label, hotkey in (
                (_FAILSAFE_ID, "failsafe", self._settings.failsafe),
                (_ACTIVATION_ID, "activation", self._settings.activation),
            ):
                try:
                    self._backend.register_hotkey(identifier, hotkey)
                except OSError as error:
                    raise OSError(
                        f"Could not register the {label} shortcut ({hotkey.label})."
                    ) from error
                shortcuts.callback(self._backend.unregister_hotkey, identifier)
            if self._stop_requested.is_set():
                return ""
            self.updates.put(SessionUpdate(State.ARMED, settings=self._settings))
            try:
                return self._listen()
            finally:
                self._stop_clicking()

    def _listen(self) -> str:
        while not self._stop_requested.is_set():
            message = self._backend.next_message()
            if message is None or self._stop_requested.is_set():
                break
            kind, identifier = message
            if kind == InputMessage.FOCUS_LOST or (
                self._target_window is not None
                and self._backend.foreground_window() != self._target_window
            ):
                return _FOCUS_LOST_NOTICE
            if kind == InputMessage.HOTKEY:
                if identifier == _FAILSAFE_ID:
                    return "Failsafe pressed. Click Arm to enable the shortcuts again."
                if identifier == _ACTIVATION_ID:
                    if self._timer_id is not None:
                        self._stop_clicking()
                        self.updates.put(SessionUpdate(State.ARMED))
                    elif notice := self._start_clicking():
                        return notice
            elif kind == InputMessage.TIMER and identifier == self._timer_id:
                self._backend.click()
        return ""

    def _start_clicking(self) -> str:
        target = self._backend.foreground_window()
        if target is None:
            return "Could not identify a foreground window."
        self._backend.watch_foreground(target)
        self._target_window = target
        if self._backend.foreground_window() != target:
            return _FOCUS_LOST_NOTICE
        self._timer_id = self._backend.start_timer(self._settings.interval_ms)
        self.updates.put(SessionUpdate(State.CLICKING))
        return ""

    def _stop_clicking(self) -> None:
        timer_id, self._timer_id = self._timer_id, None
        target, self._target_window = self._target_window, None
        try:
            if timer_id is not None:
                self._backend.stop_timer(timer_id)
        finally:
            if target is not None:
                self._backend.unwatch_foreground()
