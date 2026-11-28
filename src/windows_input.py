"""Win32 shortcuts, foreground events, timer messages, and mouse input."""

import ctypes
from ctypes import wintypes
from enum import IntEnum
from threading import get_native_id

from settings import Hotkey

_MOD_NOREPEAT = 0x4000
_WM_USER = 0x0400
_WM_STOP = 0x8000  # WM_APP
_PM_REMOVE = 0x0001
_LEFT_DOWN = 0x0002
_LEFT_UP = 0x0004
_ERROR_NOT_ENOUGH_QUOTA = 1816
_WINDOWS_KEYS = (0x5B, 0x5C)  # VK_LWIN, VK_RWIN
_EVENT_SYSTEM_FOREGROUND = 0x0003
_WINEVENT_OUTOFCONTEXT = 0


def windows_key_pressed() -> bool:
    """Check for a Windows modifier on the GUI thread during a key event."""
    get_key_state = ctypes.windll.user32.GetKeyState
    get_key_state.restype = ctypes.c_short
    return any(get_key_state(key) & 0x8000 for key in _WINDOWS_KEYS)


class InputMessage(IntEnum):
    TIMER = 0x0113
    HOTKEY = 0x0312
    FOCUS_LOST = _WM_STOP + 1


class _Message(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wparam", wintypes.WPARAM),
        ("lparam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("point", wintypes.POINT),
        ("private", wintypes.DWORD),
    ]


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("data", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("extra_info", ctypes.c_size_t),
    ]


class _Input(ctypes.Structure):
    # MOUSEINPUT sets the size and alignment of the Win32 INPUT union.
    _fields_ = [("type", wintypes.DWORD), ("mouse", _MouseInput)]


class WindowsInput:
    """Adapt the native input APIs to the click worker's thread."""

    def __init__(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)

        def bind(name, result, *arguments):
            function = getattr(user32, name)
            function.restype = result
            function.argtypes = arguments
            return function

        hwnd, uint, boolean = wintypes.HWND, wintypes.UINT, wintypes.BOOL
        message_pointer = ctypes.POINTER(_Message)
        self._register = bind("RegisterHotKey", boolean, hwnd, ctypes.c_int, uint, uint)
        self._unregister = bind("UnregisterHotKey", boolean, hwnd, ctypes.c_int)
        self._peek = bind(
            "PeekMessageW", boolean, message_pointer, hwnd, uint, uint, uint
        )
        self._get = bind("GetMessageW", boolean, message_pointer, hwnd, uint, uint)
        self._post = bind(
            "PostThreadMessageW",
            boolean,
            wintypes.DWORD,
            uint,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        self._get_foreground = bind("GetForegroundWindow", hwnd)
        self._win_event_proc = ctypes.WINFUNCTYPE(
            None,
            wintypes.HANDLE,
            wintypes.DWORD,
            hwnd,
            wintypes.LONG,
            wintypes.LONG,
            wintypes.DWORD,
            wintypes.DWORD,
        )
        self._set_win_event_hook = bind(
            "SetWinEventHook",
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HMODULE,
            self._win_event_proc,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
        )
        self._unhook_win_event = bind("UnhookWinEvent", boolean, wintypes.HANDLE)
        timer_id = ctypes.c_size_t
        self._set_timer = bind(
            "SetTimer", timer_id, hwnd, timer_id, uint, ctypes.c_void_p
        )
        self._kill_timer = bind("KillTimer", boolean, hwnd, timer_id)
        self._send = bind("SendInput", uint, uint, ctypes.POINTER(_Input), ctypes.c_int)

        self._message = _Message()
        self._focus_hook: int | None = None
        self._focus_callback = None
        self._focus_lost = False
        self._click = (_Input * 2)(
            _Input(mouse=_MouseInput(flags=_LEFT_DOWN)),
            _Input(mouse=_MouseInput(flags=_LEFT_UP)),
        )

    def prepare_thread(self) -> None:
        """Create the worker's message queue before advertising its thread ID."""
        self._peek(ctypes.byref(self._message), None, _WM_USER, _WM_USER, 0)

    def register_hotkey(self, identifier: int, hotkey: Hotkey) -> None:
        if not self._register(
            None, identifier, hotkey.modifiers | _MOD_NOREPEAT, hotkey.key_code
        ):
            raise ctypes.WinError(ctypes.get_last_error())

    def unregister_hotkey(self, identifier: int) -> None:
        if not self._unregister(None, identifier):
            raise ctypes.WinError(
                ctypes.get_last_error(), "Windows could not release a shortcut."
            )

    def next_message(self) -> tuple[int, int] | None:
        """Wait for a message, returning None when the worker should exit."""
        while not self._focus_lost:
            result = self._get(ctypes.byref(self._message), None, 0, 0)
            if result == -1:
                raise ctypes.WinError(
                    ctypes.get_last_error(), "Windows input became unavailable."
                )
            kind = self._message.message
            if result == 0 or kind == _WM_STOP:
                return None
            # The callback can latch a loss while GetMessage is waiting. Ignore
            # wake messages left by a watch that has already been removed.
            if not self._focus_lost and kind != InputMessage.FOCUS_LOST:
                return kind, self._message.wparam
        return InputMessage.FOCUS_LOST, 0

    def foreground_window(self) -> int | None:
        return self._get_foreground()

    def watch_foreground(self, target: int) -> None:
        """Watch the target on the worker's message loop until clicking stops."""
        thread_id = get_native_id()
        self._focus_lost = False

        @self._win_event_proc
        def on_foreground(_hook, _event, window, _object, _child, _thread, _time):
            if window != target and not self._focus_lost:
                self._focus_lost = True
                # Wake our own queue. If it is full, existing messages suffice.
                self._post(thread_id, InputMessage.FOCUS_LOST, 0, 0)

        hook = self._set_win_event_hook(
            _EVENT_SYSTEM_FOREGROUND,
            _EVENT_SYSTEM_FOREGROUND,
            None,
            on_foreground,
            0,
            0,
            _WINEVENT_OUTOFCONTEXT,
        )
        if not hook:
            raise OSError("Could not start watching the target window.")
        self._focus_hook = hook
        self._focus_callback = on_foreground

    def unwatch_foreground(self) -> None:
        if not self._unhook_win_event(self._focus_hook):
            raise OSError("Could not stop watching the target window.")
        self._focus_hook = None
        self._focus_callback = None
        self._focus_lost = False

    def wake_for_stop(self, thread_id: int) -> None:
        if not self._post(thread_id, _WM_STOP, 0, 0):
            error = ctypes.get_last_error()
            # If the queue is full, an existing message will wake the worker.
            if error != _ERROR_NOT_ENOUGH_QUOTA:
                raise ctypes.WinError(error, "Windows could not wake the input worker.")

    def start_timer(self, interval_ms: int) -> int:
        identifier = self._set_timer(None, 0, interval_ms, None)
        if not identifier:
            raise ctypes.WinError(
                ctypes.get_last_error(), "Windows could not start the click timer."
            )
        return identifier

    def stop_timer(self, identifier: int) -> None:
        if not self._kill_timer(None, identifier):
            raise ctypes.WinError(
                ctypes.get_last_error(), "Windows could not stop the click timer."
            )
        # KillTimer leaves posted ticks in the queue, and a new timer may reuse the ID.
        while self._peek(
            ctypes.byref(self._message),
            None,
            InputMessage.TIMER,
            InputMessage.TIMER,
            _PM_REMOVE,
        ):
            pass

    def click(self) -> None:
        input_size = ctypes.sizeof(_Input)
        ctypes.set_last_error(0)
        sent = self._send(len(self._click), self._click, input_size)
        if sent == len(self._click):
            return
        error = ctypes.get_last_error()
        if sent == 1:
            self._send(1, ctypes.byref(self._click[1]), input_size)
        raise ctypes.WinError(error, "Windows could not send a left click.")
