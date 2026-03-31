from __future__ import annotations

import ctypes
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import winreg
from pathlib import Path
from typing import Callable

import pystray
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
TRAY_PID_FILE = ROOT / ".tray.pid"
TRAY_LOCK_FILE = ROOT / ".tray.lock"
TRAY_SETTINGS_FILE = ROOT / ".tray-settings.json"
POWERSHELL = os.environ.get("COMSPEC", "powershell.exe").replace("cmd.exe", "powershell.exe")
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
AUTOSTART_NAME = "LocalKimiApiTray"

ICON_SIZE = 64
STATUS_POLL_SECONDS = 5


def _env_value(key: str, default: str) -> str:
    if not ENV_FILE.exists():
        return default
    prefix = f"{key}="
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line.split("=", 1)[1].strip()
    return default


HOST = _env_value("HOST", "127.0.0.1")
PORT = _env_value("PORT", "8000")
HEALTH_URL = f"http://{HOST}:{PORT}/health"
DASHBOARD_URL = f"http://{HOST}:{PORT}/dashboard"
LOG_DIR = ROOT / _env_value("LOG_DIR", "logs")


def _tray_launch_command() -> str:
    pythonw = (ROOT.parent / ".venv" / "Scripts" / "pythonw.exe").resolve()
    script = (ROOT / "tray_app.py").resolve()
    return f'"{pythonw}" "{script}"'


def show_message(title: str, message: str, icon: int = 0x40) -> None:
    ctypes.windll.user32.MessageBoxW(None, message, title, icon)


def _load_tray_settings() -> dict[str, bool]:
    if not TRAY_SETTINGS_FILE.exists():
        return {"auto_start_service": False}
    try:
        payload = json.loads(TRAY_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"auto_start_service": False}
    return {
        "auto_start_service": bool(payload.get("auto_start_service", False)),
    }


def _save_tray_settings(settings: dict[str, bool]) -> None:
    TRAY_SETTINGS_FILE.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def _create_image(running: bool) -> Image.Image:
    image = Image.new("RGBA", (ICON_SIZE, ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    bg = (34, 41, 47, 255)
    fg = (47, 125, 74, 255) if running else (173, 106, 31, 255)
    dot = (255, 248, 239, 255)
    draw.rounded_rectangle((4, 4, ICON_SIZE - 4, ICON_SIZE - 4), radius=16, fill=bg)
    draw.rounded_rectangle((18, 14, 28, ICON_SIZE - 14), radius=5, fill=fg)
    draw.rounded_rectangle((34, 14, 46, ICON_SIZE - 14), radius=5, fill=fg)
    draw.ellipse((44, 10, 56, 22), fill=dot)
    return image


class TrayController:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._settings = _load_tray_settings()
        self._running = self._check_service_running()
        self._busy = False
        self._icon = pystray.Icon(
            "local-kimi-api",
            _create_image(self._running),
            self._title,
            menu=pystray.Menu(
                pystray.MenuItem(lambda _: self._status_line, None, enabled=False),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Start service", self._wrap_thread(self.start_service), enabled=lambda _: not self._running and not self._busy),
                pystray.MenuItem("Stop service", self._wrap_thread(self.stop_service), enabled=lambda _: self._running and not self._busy),
                pystray.MenuItem("Show status", self._wrap_thread(self.show_status)),
                pystray.MenuItem("Open dashboard", self._wrap_thread(self.open_dashboard)),
                pystray.MenuItem("Open log directory", self._wrap_thread(self.open_log_directory)),
                pystray.MenuItem("Launch at startup", self._wrap_thread(self.toggle_autostart), checked=lambda _: self.is_autostart_enabled()),
                pystray.MenuItem("Start service when tray launches", self._wrap_thread(self.toggle_auto_start_service), checked=lambda _: self.is_auto_start_service_enabled()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit tray", self.exit_tray),
            ),
        )

    @property
    def _title(self) -> str:
        return f"local-kimi-api ({'running' if self._running else 'stopped'})"

    @property
    def _status_line(self) -> str:
        if self._busy:
            return "Service: working..."
        return f"Service: {'running' if self._running else 'stopped'}"

    def _check_service_running(self) -> bool:
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=2) as response:
                return response.status == 200
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    def _run_script(self, script_name: str) -> subprocess.CompletedProcess[str]:
        script_path = ROOT / script_name
        return subprocess.run(
            [
                POWERSHELL,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            check=False,
        )

    def is_autostart_enabled(self) -> bool:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                value, _ = winreg.QueryValueEx(key, AUTOSTART_NAME)
                return value == _tray_launch_command()
        except FileNotFoundError:
            return False
        except OSError:
            return False

    def set_autostart(self, enabled: bool) -> None:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, _tray_launch_command())
            else:
                try:
                    winreg.DeleteValue(key, AUTOSTART_NAME)
                except FileNotFoundError:
                    pass

    def is_auto_start_service_enabled(self) -> bool:
        return bool(self._settings.get("auto_start_service", False))

    def set_auto_start_service(self, enabled: bool) -> None:
        self._settings["auto_start_service"] = enabled
        _save_tray_settings(self._settings)

    def _set_busy(self, busy: bool) -> None:
        with self._lock:
            self._busy = busy
        self._refresh_icon()

    def _refresh_icon(self) -> None:
        self._running = self._check_service_running()
        self._icon.icon = _create_image(self._running)
        self._icon.title = self._title
        self._icon.update_menu()

    def _wrap_thread(self, func: Callable[[], None]) -> Callable[[pystray.Icon, pystray.MenuItem], None]:
        def runner(icon: pystray.Icon, item: pystray.MenuItem) -> None:
            _ = icon, item
            thread = threading.Thread(target=func, daemon=True)
            thread.start()

        return runner

    def start_service(self) -> None:
        self._set_busy(True)
        try:
            result = self._run_script("start.ps1")
            self._refresh_icon()
            if result.returncode != 0:
                output = (result.stderr or result.stdout or "Unknown error").strip()
                show_message("local-kimi-api", f"Start failed.\n\n{output}", 0x10)
        finally:
            self._set_busy(False)

    def stop_service(self) -> None:
        self._set_busy(True)
        try:
            result = self._run_script("stop.ps1")
            self._refresh_icon()
            if result.returncode != 0:
                output = (result.stderr or result.stdout or "Unknown error").strip()
                show_message("local-kimi-api", f"Stop failed.\n\n{output}", 0x10)
        finally:
            self._set_busy(False)

    def show_status(self) -> None:
        self._run_script("health-status.ps1")
        self._refresh_icon()

    def open_dashboard(self) -> None:
        if not self._running:
            show_message("local-kimi-api", "Service is not running. Start it first.", 0x30)
            return
        webbrowser.open(DASHBOARD_URL)

    def open_log_directory(self) -> None:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(LOG_DIR))

    def toggle_autostart(self) -> None:
        enabled = not self.is_autostart_enabled()
        self.set_autostart(enabled)
        self._icon.update_menu()
        message = "Launch at startup enabled." if enabled else "Launch at startup disabled."
        show_message("local-kimi-api", message)

    def toggle_auto_start_service(self) -> None:
        enabled = not self.is_auto_start_service_enabled()
        self.set_auto_start_service(enabled)
        self._icon.update_menu()
        message = "Auto-start service on tray launch enabled." if enabled else "Auto-start service on tray launch disabled."
        show_message("local-kimi-api", message)

    def exit_tray(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        _ = item
        icon.stop()

    def _poll_status(self) -> None:
        while True:
            time.sleep(STATUS_POLL_SECONDS)
            if not self._icon.visible:
                return
            if not self._busy:
                self._refresh_icon()

    def run(self) -> None:
        thread = threading.Thread(target=self._poll_status, daemon=True)
        thread.start()
        if self.is_auto_start_service_enabled() and not self._running:
            auto_start_thread = threading.Thread(target=self.start_service, daemon=True)
            auto_start_thread.start()
        self._icon.run()


def acquire_single_instance() -> int | None:
    if TRAY_LOCK_FILE.exists() and TRAY_PID_FILE.exists():
        try:
            existing_pid = int(TRAY_PID_FILE.read_text(encoding="ascii").strip())
            os.kill(existing_pid, 0)
            return None
        except (ValueError, OSError):
            TRAY_LOCK_FILE.unlink(missing_ok=True)
            TRAY_PID_FILE.unlink(missing_ok=True)
    elif TRAY_LOCK_FILE.exists() and not TRAY_PID_FILE.exists():
        TRAY_LOCK_FILE.unlink(missing_ok=True)

    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
    try:
        handle = os.open(TRAY_LOCK_FILE, flags)
    except FileExistsError:
        return None
    os.write(handle, str(os.getpid()).encode("ascii"))
    return handle


def main() -> None:
    lock_handle = acquire_single_instance()
    if lock_handle is None:
        return
    TRAY_PID_FILE.write_text(str(os.getpid()), encoding="ascii")
    controller = TrayController()
    try:
        controller.run()
    finally:
        if TRAY_PID_FILE.exists():
            TRAY_PID_FILE.unlink(missing_ok=True)
        if TRAY_LOCK_FILE.exists():
            TRAY_LOCK_FILE.unlink(missing_ok=True)
        os.close(lock_handle)


if __name__ == "__main__":
    main()
