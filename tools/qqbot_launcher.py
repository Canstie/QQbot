from __future__ import annotations

import argparse
import ctypes
from datetime import date, datetime, timedelta
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


PORT = 8080
WEB_URL = f"http://127.0.0.1:{PORT}/qqbot/"
DEFAULT_LOG_RETENTION_DAYS = 7
SUPERVISOR_POLL_SECONDS = 30


def message_box(title: str, text: str, icon: int = 0x40) -> None:
    ctypes.windll.user32.MessageBoxW(None, text, title, icon)


def find_project_root() -> Path | None:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent

    candidates = [base, base.parent, base.parent.parent]
    for candidate in candidates:
        if (candidate / "bot.py").is_file() and (candidate / ".venv" / "Scripts" / "python.exe").is_file():
            return candidate
    return None


def is_port_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", PORT)) == 0


def is_web_ready() -> bool:
    try:
        with urllib.request.urlopen(WEB_URL, timeout=1.0) as response:
            return 200 <= response.status < 500
    except (OSError, urllib.error.URLError):
        return False


def creation_flags() -> int:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return flags


def read_retention_days(value: str | None) -> int:
    if value is None or not value.strip():
        return DEFAULT_LOG_RETENTION_DAYS
    try:
        days = int(value)
    except ValueError:
        return DEFAULT_LOG_RETENTION_DAYS
    return max(1, days)


def log_file_for_day(logs_dir: Path, day: date | None = None) -> Path:
    target_day = day or date.today()
    return logs_dir / f"qqbot-{target_day.isoformat()}.log"


def write_launcher_log(log_file: Path, message: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} [QQBotLauncher] {message}\n"
    with log_file.open("ab") as log:
        log.write(line.encode("utf-8", errors="replace"))


def cleanup_logs(logs_dir: Path, retention_days: int) -> None:
    logs_dir.mkdir(exist_ok=True)
    cutoff = date.today() - timedelta(days=retention_days - 1)

    for path in logs_dir.glob("qqbot-*.log"):
        try:
            day = date.fromisoformat(path.stem.removeprefix("qqbot-"))
        except ValueError:
            continue
        if day < cutoff:
            path.unlink(missing_ok=True)

    legacy_log = logs_dir / "qqbot.log"
    if legacy_log.exists():
        modified_day = datetime.fromtimestamp(legacy_log.stat().st_mtime).date()
        if modified_day < cutoff:
            legacy_log.unlink(missing_ok=True)


def listener_info() -> dict[str, str] | None:
    script = rf"""
$conn = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort {PORT} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $conn) {{ exit 1 }}
$proc = Get-CimInstance Win32_Process -Filter "ProcessId = $($conn.OwningProcess)"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
[PSCustomObject]@{{
  ProcessId = $conn.OwningProcess
  ExecutablePath = $proc.ExecutablePath
  CommandLine = $proc.CommandLine
}} | ConvertTo-Json -Compress
"""
    try:
        output = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            creationflags=creation_flags(),
            timeout=4,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if not output:
        return None
    try:
        data = json.loads(output.decode("utf-8-sig"))
    except json.JSONDecodeError:
        return None
    return {key: str(value or "") for key, value in data.items()}


def is_expected_python_process(info: dict[str, str] | None, python_path: Path) -> bool:
    if not info:
        return True
    expected = str(python_path).casefold()
    return expected in info.get("CommandLine", "").casefold()


def start_bot(root: Path, python_path: Path, log_file: Path) -> subprocess.Popen:
    logs_dir = root / "logs"
    logs_dir.mkdir(exist_ok=True)
    creationflags = creation_flags()
    with log_file.open("ab") as log:
        header = f"\n--- QQBotLauncher starting bot.py at {datetime.now().isoformat(timespec='seconds')} ---\n"
        log.write(header.encode("utf-8"))
        return subprocess.Popen(
            [str(python_path), "bot.py"],
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            close_fds=False,
        )


def stop_process_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            creationflags=creation_flags(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def wait_for_ready(process: subprocess.Popen, seconds: float = 12.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if is_web_ready():
            return True
        if process.poll() is not None:
            return False
        time.sleep(0.5)
    return is_web_ready()


def notify(args: argparse.Namespace, title: str, text: str, icon: int = 0x40) -> None:
    if not args.silent:
        message_box(title, text, icon)


def supervise_bot(
    root: Path,
    python_path: Path,
    process: subprocess.Popen,
    current_day: date,
    retention_days: int,
) -> int:
    logs_dir = root / "logs"
    while True:
        time.sleep(SUPERVISOR_POLL_SECONDS)
        today = date.today()
        if today != current_day:
            old_log = log_file_for_day(logs_dir, current_day)
            write_launcher_log(old_log, "daily log rotation: stopping bot.py")
            stop_process_tree(process)
            cleanup_logs(logs_dir, retention_days)
            current_day = today
            log_file = log_file_for_day(logs_dir, current_day)
            write_launcher_log(log_file, "daily log rotation: restarting bot.py")
            process = start_bot(root, python_path, log_file)
            continue

        if process.poll() is None:
            continue

        log_file = log_file_for_day(logs_dir, current_day)
        write_launcher_log(log_file, f"bot.py exited with code {process.returncode}; restarting")
        cleanup_logs(logs_dir, retention_days)
        process = start_bot(root, python_path, log_file)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--silent", action="store_true", help="do not show message boxes")
    parser.add_argument(
        "--log-retention-days",
        type=int,
        default=read_retention_days(os.environ.get("QQBOT_LOG_RETENTION_DAYS")),
        help="number of daily bot logs to keep",
    )
    args = parser.parse_args(argv)
    args.log_retention_days = max(1, args.log_retention_days)

    root = find_project_root()
    if root is None:
        notify(
            args,
            "QQBot 启动失败",
            "没有找到 bot.py 或 .venv\\Scripts\\python.exe。\n请把 QQBotLauncher.exe 放在项目目录或 dist 目录中。",
            0x10,
        )
        return 2

    python_path = root / ".venv" / "Scripts" / "python.exe"
    if is_web_ready():
        info = listener_info()
        if not is_expected_python_process(info, python_path):
            detail = info.get("CommandLine", "未知进程") if info else "未知进程"
            notify(
                args,
                "QQBot 已由其他 Python 启动",
                "127.0.0.1:8080 已在运行，但不是当前项目虚拟环境。\n"
                "为避免 Lua 依赖缺失，请先关闭旧 bot，再双击启动器。\n\n"
                f"当前占用：{detail}",
                0x30,
            )
            return 3
        notify(args, "QQBot 已在运行", f"管理页：{WEB_URL}")
        return 0

    if is_port_open():
        info = listener_info()
        detail = info.get("CommandLine", "未知进程") if info else "未知进程"
        notify(
            args,
            "QQBot 启动失败",
            f"127.0.0.1:{PORT} 已被占用，但管理页没有响应。\n\n当前占用：{detail}",
            0x10,
        )
        return 4

    logs_dir = root / "logs"
    cleanup_logs(logs_dir, args.log_retention_days)
    current_day = date.today()
    log_file = log_file_for_day(logs_dir, current_day)
    process = start_bot(root, python_path, log_file)
    if wait_for_ready(process):
        notify(
            args,
            "QQBot 已启动",
            f"管理页：{WEB_URL}\n"
            f"今日日志：{log_file}\n"
            f"日志保留：最近 {args.log_retention_days} 天",
        )
        return supervise_bot(root, python_path, process, current_day, args.log_retention_days)

    if process.poll() is not None:
        notify(
            args,
            "QQBot 启动失败",
            f"bot.py 已退出，退出码：{process.returncode}\n请查看日志：{log_file}",
            0x10,
        )
        return 5

    notify(
        args,
        "QQBot 正在启动",
        f"进程已启动，但管理页暂时没有响应。\nPID：{process.pid}\n日志：{log_file}",
        0x30,
    )
    return supervise_bot(root, python_path, process, current_day, args.log_retention_days)


if __name__ == "__main__":
    raise SystemExit(main())
