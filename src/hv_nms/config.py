from __future__ import annotations

import ipaddress
import json
import math
import os
import platform
import uuid
from pathlib import Path
from typing import Any

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
    TREND_GRAPH_OPTIONS,
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


def _safe_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else float(default)
    except (TypeError, ValueError, OverflowError):
        return float(default)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    try:
        return str(value)
    except Exception:
        return default


def _valid_discovery_value(start: str, end: str, subnet: str) -> bool:
    try:
        a = ipaddress.ip_address(start)
        b = ipaddress.ip_address(end)
        if a.version != 4 or b.version != 4:
            return False
        ipaddress.ip_network(f"{a}/{subnet}", strict=False)
        return True
    except Exception:
        return False


def load_config(path: Path | str) -> tuple[AppSettings, list[DeviceRecord]]:
    path = Path(path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Configuration root must be a JSON object")

    settings = AppSettings()

    refresh = _safe_float(raw.get("refresh_seconds"), DEFAULT_REFRESH_SECONDS)
    settings.refresh_seconds = refresh if refresh in SCAN_INTERVAL_OPTIONS else DEFAULT_REFRESH_SECONDS

    timeout = _safe_int(raw.get("ping_timeout_ms"), PING_TIMEOUT_MS)
    settings.ping_timeout_ms = max(100, min(10000, timeout))

    mode = _safe_text(raw.get("scan_mode"), SCAN_MODE_ALL_AT_ONCE)
    settings.scan_mode = mode if mode in (SCAN_MODE_ALL_AT_ONCE, SCAN_MODE_ONE_BY_ONE) else SCAN_MODE_ALL_AT_ONCE

    trend = _safe_int(raw.get("trend_graph_seconds"), DEFAULT_TREND_SECONDS)
    valid_trends = {seconds for _label, seconds in TREND_GRAPH_OPTIONS}
    settings.trend_graph_seconds = trend if trend in valid_trends else DEFAULT_TREND_SECONDS

    settings.selected_interface_name = _safe_text(raw.get("selected_interface_name"), "Default Route") or "Default Route"
    settings.selected_interface_ip = _safe_text(raw.get("selected_interface_ip"), "")

    discovery = raw.get("discovery_settings", {})
    if not isinstance(discovery, dict):
        discovery = {}
    start_ip = _safe_text(discovery.get("start_ip"), DEFAULT_DISCOVERY_START_IP).strip()
    end_ip = _safe_text(discovery.get("end_ip"), DEFAULT_DISCOVERY_END_IP).strip()
    subnet = _safe_text(discovery.get("subnet"), DEFAULT_DISCOVERY_SUBNET).strip()
    if not _valid_discovery_value(start_ip, end_ip, subnet):
        start_ip = DEFAULT_DISCOVERY_START_IP
        end_ip = DEFAULT_DISCOVERY_END_IP
        subnet = DEFAULT_DISCOVERY_SUBNET
    settings.discovery_start_ip = start_ip
    settings.discovery_end_ip = end_ip
    settings.discovery_subnet = subnet

    interval = _safe_float(discovery.get("interval_seconds"), DEFAULT_DISCOVERY_INTERVAL_SECONDS)
    settings.discovery_interval_seconds = interval if interval in DISCOVERY_INTERVAL_OPTIONS else DEFAULT_DISCOVERY_INTERVAL_SECONDS

    thresholds = raw.get("thresholds", {})
    if not isinstance(thresholds, dict):
        thresholds = {}
    green = max(0.0, _safe_float(thresholds.get("green_max_ms"), DEFAULT_GREEN_MAX_MS))
    orange = max(green, _safe_float(thresholds.get("orange_max_ms"), DEFAULT_ORANGE_MAX_MS))
    settings.green_max_ms = green
    settings.orange_max_ms = orange

    devices: list[DeviceRecord] = []
    seen_ids: set[str] = set()
    raw_devices = raw.get("devices", [])
    if not isinstance(raw_devices, list):
        raw_devices = []
    for item in raw_devices:
        if not isinstance(item, dict):
            continue
        ip = _safe_text(item.get("ip"), "").strip()
        if not ip:
            continue
        try:
            ipaddress.ip_address(ip)
        except Exception:
            continue
        device = DeviceRecord.from_config(item)
        if not device.name:
            device.name = ip
        if device.device_id in seen_ids:
            device.device_id = str(uuid.uuid4())
        seen_ids.add(device.device_id)
        devices.append(device)

    favourites_raw = raw.get("favourite_device_ids", [None, None, None])
    if not isinstance(favourites_raw, list):
        favourites_raw = [None, None, None]
    favourites: list[str | None] = []
    for value in (favourites_raw + [None, None, None])[:3]:
        fav = _safe_text(value, "") if value is not None else ""
        favourites.append(fav if fav in seen_ids else None)
    settings.favourite_device_ids = favourites

    return settings, devices


def load_or_create_default() -> tuple[AppSettings, list[DeviceRecord], Path]:
    path = default_config_path()
    if path.exists():
        try:
            settings, devices = load_config(path)
            if devices:
                return settings, devices, path
        except Exception:
            # Preserve launch reliability if an old/partially-written config is
            # malformed; the user's source file is not deleted or overwritten
            # until the fresh defaults are saved below.
            pass

    settings = AppSettings()
    devices = default_devices()
    for i, device in enumerate(devices[:3]):
        settings.favourite_device_ids[i] = device.device_id
    save_config(path, settings, devices)
    return settings, devices, path
