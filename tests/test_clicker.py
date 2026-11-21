"""Exercise the worker against a deterministic input backend."""

from queue import Queue
from threading import Event

import pytest

from clicker import _ACTIVATION_ID, _FAILSAFE_ID, ClickSession, State
from settings import validate_settings
from windows_input import InputMessage, WindowsInput

_ACTIVATION = (InputMessage.HOTKEY, _ACTIVATION_ID)
_FAILSAFE = (InputMessage.HOTKEY, _FAILSAFE_ID)
_FIRST_TICK = (InputMessage.TIMER, 1)
_SECOND_TICK = (InputMessage.TIMER, 2)
_SETTINGS = validate_settings("Ctrl+Alt+F8", "Ctrl+Alt+F9", "100")


class FakeInput(WindowsInput):
    def __init__(self, messages=(), *, failure=None):
        self.messages = Queue()
        for message in messages:
            self.messages.put(message)
        self.failure = failure
        self.calls = []
        self.waiting = Event()
        self.timer_count = 0

    def prepare_thread(self):
        self.calls.append(("prepare",))

    def register_hotkey(self, identifier, hotkey):
        self.calls.append(("register", identifier, hotkey))
        if self.failure == ("register", identifier):
            raise OSError("Shortcut is in use")

    def unregister_hotkey(self, identifier):
        self.calls.append(("unregister", identifier))

    def start_timer(self, interval_ms):
        self.calls.append(("start timer", interval_ms))
        if self.failure == "timer":
            raise OSError("Could not start timer")
        self.timer_count += 1
        return self.timer_count

    def stop_timer(self, identifier):
        self.calls.append(("stop timer", identifier))

    def click(self):
        self.calls.append(("click",))
        if self.failure == "click":
            raise OSError("Could not click")

    def next_message(self):
        self.waiting.set()
        return self.messages.get(timeout=3)

    def wake_for_stop(self, thread_id):
        self.calls.append(("wake", thread_id))
        self.messages.put(None)


def run_session(backend):
    session = ClickSession(_SETTINGS, backend)
    session.run()
    return [session.updates.get_nowait() for _ in range(session.updates.qsize())]


@pytest.mark.parametrize("conflict", [_FAILSAFE_ID, _ACTIVATION_ID])
def test_registration_failure_leaves_no_shortcuts_armed(conflict):
    backend = FakeInput(failure=("register", conflict))
    updates = run_session(backend)

    assert [update.state for update in updates] == [State.DISARMED]
    assert "Could not register" in updates[0].notice
    assert backend.calls[1] == ("register", _FAILSAFE_ID, _SETTINGS.failsafe)
    if conflict == _ACTIVATION_ID:
        assert backend.calls[2:] == [
            ("register", _ACTIVATION_ID, _SETTINGS.activation),
            ("unregister", _FAILSAFE_ID),
        ]
    else:
        assert len(backend.calls) == 2


def test_activation_toggles_and_failsafe_releases_shortcuts():
    backend = FakeInput(
        (
            _ACTIVATION,
            _FIRST_TICK,
            _ACTIVATION,
            _FIRST_TICK,
            _ACTIVATION,
            _FIRST_TICK,
            _SECOND_TICK,
            _FAILSAFE,
            _ACTIVATION,
            _SECOND_TICK,
        )
    )
    updates = run_session(backend)

    assert [update.state for update in updates] == [
        State.ARMED,
        State.CLICKING,
        State.ARMED,
        State.CLICKING,
        State.DISARMED,
    ]
    assert updates[0].settings == _SETTINGS
    assert "Failsafe pressed" in updates[-1].notice
    assert backend.calls[3:] == [
        ("start timer", 100),
        ("click",),
        ("stop timer", 1),
        ("start timer", 100),
        ("click",),
        ("stop timer", 2),
        ("unregister", _ACTIVATION_ID),
        ("unregister", _FAILSAFE_ID),
    ]


def test_failsafe_disarms_while_idle():
    backend = FakeInput((_FAILSAFE, _ACTIVATION))
    updates = run_session(backend)

    assert [update.state for update in updates] == [State.ARMED, State.DISARMED]
    assert backend.calls[3:] == [
        ("unregister", _ACTIVATION_ID),
        ("unregister", _FAILSAFE_ID),
    ]


@pytest.mark.parametrize(
    ("failure", "timer_stops"), [("timer", []), ("click", [("stop timer", 1)])]
)
def test_windows_input_failure_disarms(failure, timer_stops):
    backend = FakeInput((_ACTIVATION, _FIRST_TICK), failure=failure)
    updates = run_session(backend)

    assert updates[-1].state is State.DISARMED
    assert "Could not" in updates[-1].notice
    assert backend.calls[-2:] == [
        ("unregister", _ACTIVATION_ID),
        ("unregister", _FAILSAFE_ID),
    ]
    assert [call for call in backend.calls if call[0] == "stop timer"] == timer_stops


def test_stop_requested_before_start_prevents_registration():
    backend = FakeInput()
    session = ClickSession(_SETTINGS, backend)
    session.request_stop()
    session.run()

    assert session.updates.get_nowait().state is State.DISARMED
    assert backend.calls == [("prepare",)]


def test_stop_wakes_worker_and_releases_timer_and_shortcuts():
    backend = FakeInput((_ACTIVATION,))
    session = ClickSession(_SETTINGS, backend)
    session.start()
    try:
        assert session.updates.get(timeout=3).state is State.ARMED
        assert session.updates.get(timeout=3).state is State.CLICKING
        assert backend.waiting.wait(timeout=3)
    finally:
        session.request_stop()
        session.join(timeout=3)

    assert not session.is_alive()
    assert session.updates.get_nowait().state is State.DISARMED
    assert backend.calls[-4][0] == "wake"
    assert backend.calls[-3:] == [
        ("stop timer", 1),
        ("unregister", _ACTIVATION_ID),
        ("unregister", _FAILSAFE_ID),
    ]


def test_native_registration_disables_repeat():
    backend = WindowsInput.__new__(WindowsInput)
    calls = []

    def record_registration(*arguments):
        calls.append(arguments)
        return True

    backend._register = record_registration
    backend.register_hotkey(_ACTIVATION_ID, _SETTINGS.activation)

    assert calls == [(None, _ACTIVATION_ID, 0x4003, 0x77)]
