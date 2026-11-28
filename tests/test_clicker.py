"""Exercise the worker against a deterministic input backend."""

from itertools import repeat
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
_FOCUS_LOST = (InputMessage.FOCUS_LOST, 0)
_SETTINGS = validate_settings("Ctrl+Alt+F8", "Ctrl+Alt+F9", "100")


class FakeInput(WindowsInput):
    def __init__(self, messages=(), *, failure=None, foregrounds=None):
        self.messages = Queue()
        for message in messages:
            self.messages.put(message)
        self.failure = failure
        self.calls = []
        self.waiting = Event()
        self.timer_count = 0
        self.foregrounds = repeat(100) if foregrounds is None else iter(foregrounds)

    def prepare_thread(self):
        self.calls.append(("prepare",))

    def register_hotkey(self, identifier, hotkey):
        self.calls.append(("register", identifier, hotkey))
        if self.failure == ("register", identifier):
            raise OSError("Shortcut is in use")

    def unregister_hotkey(self, identifier):
        self.calls.append(("unregister", identifier))

    def foreground_window(self):
        return next(self.foregrounds)

    def watch_foreground(self, target):
        self.calls.append(("watch", target))
        if self.failure == "watch":
            raise OSError("Could not start watching the target window.")

    def unwatch_foreground(self):
        self.calls.append(("unwatch",))

    def start_timer(self, interval_ms):
        self.calls.append(("start timer", interval_ms))
        if self.failure == "timer":
            raise OSError("Could not start timer")
        self.timer_count += 1
        return self.timer_count

    def stop_timer(self, identifier):
        self.calls.append(("stop timer", identifier))
        if self.failure == "stop timer":
            raise OSError("Could not stop timer")

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
        ("watch", 100),
        ("start timer", 100),
        ("click",),
        ("stop timer", 1),
        ("unwatch",),
        ("watch", 100),
        ("start timer", 100),
        ("click",),
        ("stop timer", 2),
        ("unwatch",),
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


def test_focus_loss_disarms_without_waiting_for_a_tick():
    backend = FakeInput((_ACTIVATION, _FOCUS_LOST, _FIRST_TICK, _ACTIVATION))
    updates = run_session(backend)

    assert [update.state for update in updates] == [
        State.ARMED,
        State.CLICKING,
        State.DISARMED,
    ]
    assert updates[-1].notice == "Target window lost focus."
    assert backend.calls[3:] == [
        ("watch", 100),
        ("start timer", 100),
        ("stop timer", 1),
        ("unwatch",),
        ("unregister", _ACTIVATION_ID),
        ("unregister", _FAILSAFE_ID),
    ]


@pytest.mark.parametrize("foreground", [200, None])
def test_tick_checks_foreground_before_sending_click(foreground):
    backend = FakeInput((_ACTIVATION, _FIRST_TICK), foregrounds=(100, 100, foreground))
    updates = run_session(backend)

    assert updates[-1].state is State.DISARMED
    assert updates[-1].notice == "Target window lost focus."
    assert ("click",) not in backend.calls


def test_activation_after_pause_watches_the_new_window():
    backend = FakeInput(
        (_ACTIVATION, _ACTIVATION, _FIRST_TICK, _ACTIVATION, _SECOND_TICK, _FAILSAFE),
        foregrounds=(100, 100, 100, 200, 200, 200, 200),
    )
    updates = run_session(backend)

    assert [update.state for update in updates] == [
        State.ARMED,
        State.CLICKING,
        State.ARMED,
        State.CLICKING,
        State.DISARMED,
    ]
    assert [call for call in backend.calls if call[0] == "watch"] == [
        ("watch", 100),
        ("watch", 200),
    ]
    assert backend.calls.count(("click",)) == 1


@pytest.mark.parametrize(
    ("foregrounds", "failure", "notice", "watch_calls"),
    [
        ((None,), None, "Could not identify a foreground window.", []),
        (
            (100,),
            "watch",
            "Could not start watching the target window.",
            [("watch", 100)],
        ),
        ((100, 200), None, "Target window lost focus.", [("watch", 100), ("unwatch",)]),
    ],
)
def test_clicking_requires_a_stable_watched_window(
    foregrounds, failure, notice, watch_calls
):
    backend = FakeInput((_ACTIVATION,), foregrounds=foregrounds, failure=failure)
    updates = run_session(backend)

    assert [update.state for update in updates] == [State.ARMED, State.DISARMED]
    assert updates[-1].notice == notice
    assert backend.calls[3:] == [
        *watch_calls,
        ("unregister", _ACTIVATION_ID),
        ("unregister", _FAILSAFE_ID),
    ]


@pytest.mark.parametrize(
    ("failure", "timer_stops"),
    [
        ("timer", []),
        ("click", [("stop timer", 1)]),
        ("stop timer", [("stop timer", 1)]),
    ],
)
def test_windows_input_failure_disarms(failure, timer_stops):
    backend = FakeInput((_ACTIVATION, _FIRST_TICK, _FAILSAFE), failure=failure)
    updates = run_session(backend)

    assert updates[-1].state is State.DISARMED
    assert "Could not" in updates[-1].notice
    assert backend.calls[-3:] == [
        ("unwatch",),
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
    assert backend.calls[-5][0] == "wake"
    assert backend.calls[-4:] == [
        ("stop timer", 1),
        ("unwatch",),
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


def test_native_focus_watch_latches_loss_until_the_watch_is_removed():
    backend = WindowsInput.__new__(WindowsInput)
    posted = []
    backend._win_event_proc = lambda callback: callback
    backend._set_win_event_hook = lambda *args: 1
    backend._unhook_win_event = lambda handle: handle == 1
    backend._post = lambda *args: posted.append(args)

    backend.watch_foreground(100)
    callback = backend._focus_callback
    callback(1, 3, 100, 0, 0, 0, 0)
    assert posted == []
    callback(1, 3, 200, 0, 0, 0, 0)
    callback(1, 3, 100, 0, 0, 0, 0)

    assert [message for _, message, _, _ in posted] == [InputMessage.FOCUS_LOST]
    assert backend.next_message() == _FOCUS_LOST
    backend.unwatch_foreground()
    assert backend._focus_callback is None
