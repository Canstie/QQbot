from __future__ import annotations

from qq_personal_bot.system_metrics import (
    SystemMetrics,
    collect_system_metrics,
    format_system_metrics,
)


def test_collect_system_metrics_smoke():
    metrics = collect_system_metrics(sample_interval=0)

    assert 0 <= metrics.cpu_percent <= 100
    assert metrics.logical_cpu_count >= 1
    assert metrics.memory_total > 0
    assert metrics.disk_total > 0
    assert metrics.process_memory > 0
    assert metrics.process_threads >= 1


def test_format_system_metrics_contains_key_server_values():
    metrics = SystemMetrics(
        cpu_percent=12.3,
        physical_cpu_count=4,
        logical_cpu_count=8,
        load_average=(0.1, 0.2, 0.3),
        memory_used=4 * 1024**3,
        memory_available=4 * 1024**3,
        memory_total=8 * 1024**3,
        memory_percent=50.0,
        swap_used=0,
        swap_total=2 * 1024**3,
        swap_percent=0.0,
        disk_path="/",
        disk_used=40 * 1024**3,
        disk_total=100 * 1024**3,
        disk_percent=40.0,
        system_uptime_seconds=90061,
        process_cpu_percent=1.2,
        process_memory=64 * 1024**2,
        process_threads=9,
        process_uptime_seconds=3661,
    )

    report = format_system_metrics(metrics)

    assert "CPU：12.3%（4 物理核 / 8 逻辑核）" in report
    assert "内存：4.0 GiB / 8.0 GiB（50.0%）" in report
    assert "磁盘 /：40.0 GiB / 100.0 GiB（40.0%）" in report
    assert "系统运行时间：1天1小时1分钟1秒" in report
    assert "Bot 进程：CPU 1.2%｜内存 64.0 MiB｜线程 9｜运行 1小时1分钟1秒" in report
