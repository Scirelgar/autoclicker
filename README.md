# Autoclicker

A small Windows desktop app that repeats left clicks at the mouse pointer using
two configurable global shortcuts. It uses Tkinter and the Windows input APIs.
The [Quantum ÉTS Python template](https://github.com/QuantumETS/template-python-with-ci)
provides the uv, pytest, Ruff, and GitHub Actions setup.

## Run

With [uv](https://docs.astral.sh/uv/) installed, run from the project directory.

```powershell
uv run python src/main.py
```

The project uses Python 3.14 or newer with Tkinter.

1. Enter the activation and failsafe shortcuts, for example `Ctrl+Alt+F8` and
   `Ctrl+Alt+F9`. Use one or more of Ctrl, Alt, or Shift with a letter, digit,
   F1–F11, or Esc. Both shortcuts must be different and available in Windows.
2. Enter a whole interval from 10 to 2,147,483,647 milliseconds and click **Arm**.
3. Move the pointer where you want to click. Press the activation shortcut to
   start clicking. Press it again to pause. Shortcuts work while another app
   is focused.
4. Press the failsafe at any time while armed to stop and disarm. Click **Arm**
   again in the window to resume using the shortcuts.

Successful arming saves the shortcuts and interval in
`%LOCALAPPDATA%\Autoclicker\settings.json`. On relaunch, the saved values are
filled in and the app starts disarmed.

## Development

```powershell
uv sync --locked --group dev
uv run --locked pytest tests/
uv run --locked ruff check .
uv run --locked ruff format --check .
```
