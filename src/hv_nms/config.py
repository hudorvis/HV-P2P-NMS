from __future__ import annotations

import json
import os
import platform
from pathlib import Path

from .constants import (
    APP_TITLE,
    APP_VERSION,
    DEFAULT_DISCOVERY_END_IP,
    DEFAULT_DISCOVERY_INTERVAL_SECONDS,
    DEFAULT_DISCOVERY_START_IP,
    DEFAULT_DISCOVERY_SUBNET,
    DISCOVERY_INTERVAL_OPTIONS,
    DEFAULT_GREEN_MAX_MS,
    DEFAULT_ORANGE_MAX_MS,
    DEFAULT_REFRESH_SECONDS,
    DEFAULT_TREND_SECONDS,
    PING_TIMEOUT_MS,
    SCAN_INTERVAL_OPTIONS,
    SCAN_MODE_ALL_AT_ONCE,
    SCAN_MODE_ONE_BY_ONE,
)
from .models import AppSettings, DeviceRecord


def app_config_dir() -> Path:
    system = platform.system().lower()
    if system == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif system == "windows":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = base / "HV P2P NMS"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_config_path() -> Path:
    return app_config_dir() / "config.json"


def default_devices() -> list[DeviceRecord]:
    return [
        DeviceRecord("SRVR", "172.20.1.70"),
        DeviceRecord("CTRL", "172.20.1.71"),
        DeviceRecord("W1P", "172.20.1.72"),
        DeviceRecord("PDM LAN", "172.20.1.101"),
    ]


def serialize_config(settings: AppSettings, devices: list[DeviceRecord]) -> dict:
    return {
        "app_name": APP_TITLE,
        "app_version": APP_VERSION,
        "refresh_seconds": settings.refresh_seconds,
        "ping_timeout_ms": settings.ping_timeout_ms,
        "scan_mode": settings.scan_mode,
        "trend_graph_seconds": settings.trend_graph_seconds,
        "selected_interface_name": settings.selected_interface_name,
        "selected_interface_ip": settings.selected_interface_ip,
        "discovery_settings": {
            "start_ip": settings.discovery_start_ip,
            "end_ip": settings.discovery_end_ip,
            "subnet": settings.discovery_subnet,
            "interval_seconds": settings.discovery_interval_seconds,
        },
        "thresholds": {
            "green_max_ms": settings.green_max_ms,
            "orange_max_ms": settings.orange_max_ms,
        },
        "favourite_device_ids": settings.normalised_favourites(),
        "devices": [d.to_config() for d in devices],
    }


def save_config(path: Path | str, settings: AppSettings, devices: list[DeviceRecord]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(serialize_config(settings, devices), indent=2), encoding="utf-8")
    tmp.replace(path)


def load_config(path: Path | str) -> tuple[AppSettings, list[DeviceRecord]]:
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    settings = AppSettings()
    refresh = float(raw.get("refresh_seconds", DEFAULT_REFRESH_SECONDS))
    settings.refresh_seconds = refresh if refresh in SCAN_INTERVAL_OPTIONS else DEFAULT_REFRESH_SECONDS
    settings.ping_timeout_ms = max(100, min(10000, int(raw.get("ping_timeout_ms", PING_TIMEOUT_MS))))
    mode = raw.get("scan_mode", SCAN_MODE_ALL_AT_ONCE)
    settings.scan_mode = mode if mode in (SCAN_MODE_ALL_AT_ONCE, SCAN_MODE_ONE_BY_ONE) else SCAN_MODE_ALL_AT_ONCE
    settings.trend_graph_seconds = int(raw.get("trend_graph_seconds", DEFAULT_TREND_SECONDS))
    settings.selected_interface_name = str(raw.get("selected_interface_name", "Default Route"))
    settings.selected_interface_ip = str(raw.get("selected_interface_ip", ""))
    discovery = raw.get("discovery_settings", {}) or {}
    settings.discovery_start_ip = str(discovery.get("start_ip", DEFAULT_DISCOVERY_START_IP))
    settings.discovery_end_ip = str(discovery.get("end_ip", DEFAULT_DISCOVERY_END_IP))
    settings.discovery_subnet = str(discovery.get("subnet", DEFAULT_DISCOVERY_SUBNET))
    interval = float(discovery.get("interval_seconds", DEFAULT_DISCOVERY_INTERVAL_SECONDS))
    settings.discovery_interval_seconds = interval if interval in DISCOVERY_INTERVAL_OPTIONS else DEFAULT_DISCOVERY_INTERVAL_SECONDS
    thresholds = raw.get("thresholds", {}) or {}
    settings.green_max_ms = max(0.0, float(thresholds.get("green_max_ms", DEFAULT_GREEN_MAX_MS)))
    settings.orange_max_ms = max(settings.green_max_ms, float(thresholds.get("orange_max_ms", DEFAULT_ORANGE_MAX_MS)))
    favourites = list(raw.get("favourite_device_ids", [None, None, None]))
    settings.favourite_device_ids = (favourites + [None, None, None])[:3]
    devices = [DeviceRecord.from_config(item) for item in raw.get("devices", []) if item.get("ip")]
    return settings, devices


def load_or_create_default() -> tuple[AppSettings, list[DeviceRecord], Path]:
    path = default_config_path()
    if path.exists():
        try:
            settings, devices = load_config(path)
            if devices:
                return settings, devices, path
        except Exception:
            pass
    settings = AppSettings()
    devices = default_devices()
    # Useful first-run favourites; the user can overwrite them from the Run page.
    for i, device in enumerate(devices[:3]):
        settings.favourite_device_ids[i] = device.device_id
    save_config(path, settings, devices)
    return settings, devices, path
