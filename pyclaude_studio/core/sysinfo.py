"""Mesures système multiplateformes sans dépendance (psutil utilisé s'il existe)."""

from __future__ import annotations

import gc
import os
import platform
import sys
import threading
import time
from typing import Dict, Optional

try:  # pragma: no cover - optionnel
    import psutil  # type: ignore
except Exception:  # pragma: no cover
    psutil = None


def memory_rss() -> Optional[int]:
    """Mémoire résidente du processus en octets (ou None si indisponible)."""
    if psutil is not None:
        try:
            return int(psutil.Process().memory_info().rss)
        except Exception:
            pass
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/self/statm", "rb") as fh:
                pages = int(fh.read().split()[1])
            return pages * os.sysconf("SC_PAGE_SIZE")
        except (OSError, ValueError, IndexError):
            return None
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class PMC(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t),
                            ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]

            counters = PMC()
            counters.cb = ctypes.sizeof(PMC)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters),
                                                        counters.cb):
                return int(counters.WorkingSetSize)
        except Exception:
            return None
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(peak if sys.platform == "darwin" else peak * 1024)
    except Exception:
        return None


class CpuMeter:
    """Pourcentage CPU du processus entre deux appels (temps CPU / temps mur)."""

    def __init__(self) -> None:
        self._wall = time.perf_counter()
        self._cpu = time.process_time()

    def sample(self) -> float:
        wall, cpu = time.perf_counter(), time.process_time()
        dw, dc = wall - self._wall, cpu - self._cpu
        self._wall, self._cpu = wall, cpu
        if dw <= 0:
            return 0.0
        return max(0.0, min(100.0 * (os.cpu_count() or 1), 100.0 * dc / dw))


def load_average() -> Optional[float]:
    try:
        return os.getloadavg()[0]
    except (AttributeError, OSError):
        return None


def human_bytes(n: Optional[float]) -> str:
    if n is None:
        return "—"
    for unit in ("o", "Ko", "Mo", "Go", "To"):
        if abs(n) < 1024 or unit == "To":
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} To"


def static_info() -> Dict[str, str]:
    return {
        "Python": f"{platform.python_implementation()} {platform.python_version()}",
        "Système": f"{platform.system()} {platform.release()}",
        "Machine": platform.machine() or "?",
        "Processeurs": str(os.cpu_count() or "?"),
        "Exécutable": sys.executable,
    }


def runtime_info() -> Dict[str, object]:
    return {
        "threads": threading.active_count(),
        "gc_objects": len(gc.get_objects()),
        "gc_counts": gc.get_count(),
    }
