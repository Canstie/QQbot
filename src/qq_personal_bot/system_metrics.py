from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import psutil


@dataclass(frozen=True)
class SystemMetrics:
    cpu_percent: float
    physical_cpu_count: int
    logical_cpu_count: int
    load_average: tuple[float, float, float] | None
    memory_used: int
    memory_available: int
    memory_total: int
    memory_percent: float
    swap_used: int
    swap_total: int
    swap_percent: float
    disk_path: str
    disk_used: int
    disk_total: int
    disk_percent: float
    system_uptime_seconds: float
    process_cpu_percent: float
    process_memory: int
    process_threads: int
    process_uptime_seconds: float


def collect_system_metrics(*, sample_interval: float = 0.2) -> SystemMetrics:
    process = psutil.Process(os.getpid())
    process.cpu_percent(interval=None)
    cpu_percent = psutil.cpu_percent(interval=max(0.0, sample_interval))
    process_cpu_percent = process.cpu_percent(interval=None)

    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk_path = Path.cwd().anchor or "/"
    disk = psutil.disk_usage(disk_path)
    process_memory = process.memory_info().rss
    now = time.time()

    try:
        raw_load = os.getloadavg()
        load_average = (float(raw_load[0]), float(raw_load[1]), float(raw_load[2]))
    except (AttributeError, OSError):
        load_average = None

    return SystemMetrics(
        cpu_percent=float(cpu_percent),
        physical_cpu_count=int(psutil.cpu_count(logical=False) or 0),
        logical_cpu_count=int(psutil.cpu_count(logical=True) or 0),
        load_average=load_average,
        memory_used=int(memory.used),
        memory_available=int(memory.available),
        memory_total=int(memory.total),
        memory_percent=float(memory.percent),
        swap_used=int(swap.used),
        swap_total=int(swap.total),
        swap_percent=float(swap.percent),
        disk_path=str(disk_path),
        disk_used=int(disk.used),
        disk_total=int(disk.total),
        disk_percent=float(disk.percent),
        system_uptime_seconds=max(0.0, now - psutil.boot_time()),
        process_cpu_percent=float(process_cpu_percent),
        process_memory=int(process_memory),
        process_threads=int(process.num_threads()),
        process_uptime_seconds=max(0.0, now - process.create_time()),
    )


def format_system_metrics(metrics: SystemMetrics) -> str:
    cores = f"{metrics.physical_cpu_count} 物理核 / {metrics.logical_cpu_count} 逻辑核"
    lines = [
        "服务器性能",
        f"CPU：{metrics.cpu_percent:.1f}%（{cores}）",
    ]
    if metrics.load_average is not None:
        lines.append(
            "系统负载（1/5/15 分钟）："
            f"{metrics.load_average[0]:.2f} / "
            f"{metrics.load_average[1]:.2f} / "
            f"{metrics.load_average[2]:.2f}"
        )
    lines.extend(
        [
            (
                "内存："
                f"{_format_bytes(metrics.memory_used)} / {_format_bytes(metrics.memory_total)}"
                f"（{metrics.memory_percent:.1f}%），可用 {_format_bytes(metrics.memory_available)}"
            ),
            (
                "Swap："
                f"{_format_bytes(metrics.swap_used)} / {_format_bytes(metrics.swap_total)}"
                f"（{metrics.swap_percent:.1f}%）"
            ),
            (
                f"磁盘 {metrics.disk_path}："
                f"{_format_bytes(metrics.disk_used)} / {_format_bytes(metrics.disk_total)}"
                f"（{metrics.disk_percent:.1f}%）"
            ),
            f"系统运行时间：{_format_duration(metrics.system_uptime_seconds)}",
            (
                "Bot 进程："
                f"CPU {metrics.process_cpu_percent:.1f}%｜"
                f"内存 {_format_bytes(metrics.process_memory)}｜"
                f"线程 {metrics.process_threads}｜"
                f"运行 {_format_duration(metrics.process_uptime_seconds)}"
            ),
        ]
    )
    return "\n".join(lines)


def _format_bytes(value: int) -> str:
    amount = float(max(0, value))
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units[:-1]:
        if amount < 1024:
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} {units[-1]}"


def _format_duration(value: float) -> str:
    seconds = max(0, int(value))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}天")
    if hours or days:
        parts.append(f"{hours}小时")
    if minutes or hours or days:
        parts.append(f"{minutes}分钟")
    parts.append(f"{seconds}秒")
    return "".join(parts)
