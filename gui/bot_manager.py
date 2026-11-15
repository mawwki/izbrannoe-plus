"""Управление процессом бота из GUI: старт/стоп/логи (subprocess + pid-файл)."""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUN_SCRIPT = PROJECT_ROOT / "run_bot.py"
PID_PATH = PROJECT_ROOT / "data" / "bot.pid"


def _no_window_flags() -> int:
    if sys.platform == "win32":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace", timeout=10).stdout
            return str(pid) in out
        except Exception:  # noqa: BLE001
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _external_bot_pids() -> list[int]:
    """PID чужих процессов run_bot.py (иначе Telegram отдаёт 409 Conflict)."""
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object {$_.CommandLine -like '*run_bot.py*'} | "
             "Select-Object -ExpandProperty ProcessId"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
            creationflags=_no_window_flags(),
        ).stdout
        return [int(x) for x in re.findall(r"\d+", out)]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


class BotManager(QObject):
    status_changed = pyqtSignal(bool)
    log_line = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.proc: subprocess.Popen | None = None
        self.started_at: float | None = None
        self._stop_requested = False

    # ---------- состояние ----------

    def is_running(self) -> bool:
        if self.proc is not None and self.proc.poll() is None:
            return True
        # бот мог быть запущен раньше (или из другого места) — проверим pid-файл
        if self.proc is None and PID_PATH.exists():
            try:
                pid = int(PID_PATH.read_text(encoding="utf-8").strip() or 0)
                if _pid_alive(pid):
                    return True
            except (ValueError, OSError):
                pass
        return False

    def uptime(self) -> str:
        if self.started_at is None:
            return "—"
        s = int(time.time() - self.started_at)
        return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"

    # ---------- управление ----------

    def start(self) -> bool:
        if self.is_running():
            self.log_line.emit("⚠️ Бот уже запущен — не запускаю повторно.")
            return False
        external = _external_bot_pids()
        if external:
            self.log_line.emit(
                f"⚠️ Обнаружен другой запущенный бот (PID {', '.join(map(str, external))}) — "
                f"два инстанса конфликтуют за Telegram (409). Остановите его и попробуйте снова.")
            return False
        self._stop_requested = False
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        try:
            self.proc = subprocess.Popen(
                [sys.executable, str(RUN_SCRIPT)],
                cwd=str(PROJECT_ROOT),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=_no_window_flags(),
                text=True, encoding="utf-8", errors="replace",
            )
        except OSError as e:
            self.log_line.emit(f"❌ Не удалось запустить бота: {e}")
            return False
        self.started_at = time.time()
        PID_PATH.parent.mkdir(parents=True, exist_ok=True)
        PID_PATH.write_text(str(self.proc.pid), encoding="utf-8")
        threading.Thread(target=self._pump_logs, daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()
        self.status_changed.emit(True)
        self.log_line.emit("▶️ Процесс бота запущен.")
        return True

    def stop(self) -> bool:
        self._stop_requested = True
        stopped = False
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=6)
                stopped = True
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)
                stopped = True
        elif PID_PATH.exists():
            try:
                pid = int(PID_PATH.read_text(encoding="utf-8").strip() or 0)
                if _pid_alive(pid) and sys.platform == "win32":
                    subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                                   capture_output=True, encoding="utf-8", errors="replace", timeout=10)
                    stopped = True
            except (ValueError, OSError, subprocess.SubprocessError):
                pass
        PID_PATH.unlink(missing_ok=True)
        self.proc = None
        self.started_at = None
        self.status_changed.emit(False)
        if stopped:
            self.log_line.emit("⏹ Бот остановлен.")
        return stopped

    def restart(self) -> None:
        self.stop()
        time.sleep(0.4)
        self.start()

    # ---------- потоки ----------

    def _pump_logs(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in iter(proc.stdout.readline, ""):
                if not line:
                    break
                self.log_line.emit(line.rstrip())
        except (ValueError, OSError):
            pass

    def _watch(self) -> None:
        proc = self.proc
        if proc is None:
            return
        code = proc.wait()
        if not self._stop_requested:
            self.log_line.emit(f"⚠️ Процесс бота завершился с кодом {code}.")
            self.status_changed.emit(False)
            self.started_at = None
