# utils/cpu_utils.py — Adaptive CPU throttle, works on any computer
#
# Strategy (in priority order):
#   1. Read CPU temperature via psutil  (Linux / macOS)
#   2. Read CPU temperature via WMI     (Windows — needs optional `wmi` package)
#   3. Proxy: sustained 97 %+ CPU load for 45 s → assume hot → reduce workers
#
# The module-level `throttler` singleton starts a daemon thread on import.
# Use `throttler.get_workers(ocr_engine)` to get the safe worker count.

import os
import time
import threading
import platform

try:
    import psutil
    # Warm up the cpu_percent baseline (first call always returns 0.0)
    psutil.cpu_percent(interval=None)
except ImportError:
    psutil = None

_LOGICAL_CORES = os.cpu_count() or 4


# ── Temperature readers ───────────────────────────────────────────────────────

def _read_temp_psutil():
    """Return CPU temp in °C via psutil (Linux / macOS). None if unavailable."""
    if psutil is None:
        return None
    try:
        temps = psutil.sensors_temperatures()
        if not temps:
            return None
        # Prefer labelled CPU/core readings
        for entries in temps.values():
            for e in entries:
                lbl = e.label.lower()
                if any(k in lbl for k in ('cpu', 'core', 'package', 'tdie', 'tccd', 'tctl')):
                    return e.current
        # Fallback: first available sensor
        for entries in temps.values():
            if entries:
                return entries[0].current
    except Exception:
        pass
    return None


def _read_temp_wmi():
    """Return CPU temp in °C via WMI (Windows). None if wmi package not installed."""
    try:
        import wmi  # optional: pip install wmi
        w = wmi.WMI(namespace='root\\wmi')
        for item in w.MSAcpi_ThermalZoneTemperature():
            celsius = (item.CurrentTemperature / 10.0) - 273.15
            if 0 < celsius < 120:   # sanity range
                return celsius
    except Exception:
        pass
    return None


def _read_cpu_load():
    """Return current CPU usage % (non-blocking). None if psutil unavailable."""
    if psutil is None:
        return None
    try:
        return psutil.cpu_percent(interval=None)
    except Exception:
        return None


# ── ThermalThrottler class ────────────────────────────────────────────────────

class ThermalThrottler:
    """
    Runs a daemon thread that monitors CPU temperature / load every few seconds.

    Starts at full CPU count for maximum parallelism.
    Reduces workers by 1 when the CPU gets hot; restores when it cools.
    Gracefully degrades: works even when temperature sensors are unavailable
    by using sustained high CPU load as a thermal proxy.

    Usage:
        from utils.cpu_utils import throttler
        workers = throttler.get_workers('gcv')   # respects EasyOCR serialization
    """

    def __init__(
        self,
        temp_high: float = 85.0,    # °C  — start throttling
        temp_low:  float = 75.0,    # °C  — start restoring
        load_high: float = 97.0,    # %   — proxy: sustained near-100% = hot
        load_low:  float = 72.0,    # %   — proxy: load eased, safe to restore
        load_sustain: float = 45.0, # seconds at load_high before throttling
        poll_secs: int = 6,
    ):
        self._max   = _LOGICAL_CORES
        self._min   = max(1, _LOGICAL_CORES // 4)   # floor: 25 % of cores
        self._cur   = self._max
        self._lock  = threading.Lock()

        self._temp_high     = temp_high
        self._temp_low      = temp_low
        self._load_high     = load_high
        self._load_low      = load_low
        self._load_sustain  = load_sustain
        self._poll          = poll_secs

        self._high_load_since = None   # monotonic timestamp
        self._is_win = platform.system() == 'Windows'

        t = threading.Thread(target=self._monitor, daemon=True, name='ThermalThrottler')
        t.start()

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def max_workers(self) -> int:
        """Maximum possible workers (= logical CPU count of this machine)."""
        return self._max

    @property
    def workers(self) -> int:
        """Current safe worker count after thermal adjustment."""
        with self._lock:
            return self._cur

    def get_workers(self, engine: str = 'gcv') -> int:
        """
        Return safe worker count for the given OCR engine.
        EasyOCR is NOT thread-safe → always returns 1 regardless of thermals.
        """
        if engine == 'easyocr':
            return 1
        return self.workers

    def status(self) -> str:
        """Human-readable status string for logging."""
        temp = _read_temp_psutil() or (_read_temp_wmi() if self._is_win else None)
        load = _read_cpu_load()
        parts = [f'workers={self.workers}/{self._max}']
        if temp is not None:
            parts.append(f'temp={temp:.0f}°C')
        if load is not None:
            parts.append(f'load={load:.0f}%')
        return '  '.join(parts)

    # ── Background monitor ────────────────────────────────────────────────────

    def _monitor(self):
        while True:
            time.sleep(self._poll)
            try:
                # Read temperature (best available method)
                temp = _read_temp_psutil()
                if temp is None and self._is_win:
                    temp = _read_temp_wmi()

                # Read CPU load (always available if psutil installed)
                load = _read_cpu_load()
                now  = time.monotonic()

                # Track how long load has been sustained above threshold
                if load is not None and load >= self._load_high:
                    if self._high_load_since is None:
                        self._high_load_since = now
                else:
                    self._high_load_since = None

                # ── Decide: hot or cool? ─────────────────────────────────────
                hot  = False
                cool = True

                if temp is not None:
                    hot  = hot  or (temp >= self._temp_high)
                    cool = cool and (temp <= self._temp_low)

                # Proxy thermal: if load stayed ≥97 % for 45 s, treat as hot
                if self._high_load_since is not None:
                    sustained = now - self._high_load_since
                    if sustained >= self._load_sustain:
                        hot  = True
                        cool = False

                if load is not None:
                    cool = cool and (load <= self._load_low)

                # ── Adjust workers ───────────────────────────────────────────
                with self._lock:
                    if hot and self._cur > self._min:
                        self._cur = max(self._min, self._cur - 1)
                        msg = (f'[CPU] Throttling: workers → {self._cur}/{self._max}'
                               + (f'  temp={temp:.0f}°C' if temp is not None else '')
                               + (f'  load={load:.0f}%'  if load is not None else ''))
                        print(msg)
                    elif cool and self._cur < self._max:
                        self._cur += 1
                        msg = (f'[CPU] Restored: workers → {self._cur}/{self._max}'
                               + (f'  temp={temp:.0f}°C' if temp is not None else ''))
                        print(msg)
            except Exception:
                pass   # daemon must never crash


# ── Module-level singleton ────────────────────────────────────────────────────
# Starts monitoring the moment this module is imported.

throttler = ThermalThrottler()


# ── Legacy generator API (backward-compat with config.py) ────────────────────

def auto_worker_count(max_workers=None, min_workers=1,
                      temp_limit=85, cool_limit=75, check_interval=10):
    """Kept for backward compatibility. Delegates to the module singleton."""
    while True:
        yield throttler.workers
        time.sleep(check_interval)

