from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Iterable

from .constants import MAX_HISTORY_POINTS, MAX_HISTORY_SECONDS


@dataclass
class DeviceRecord:
    name: str
    ip: str
    hostname: str = ""
    device_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    latency_ms: float | None = None
    last_failed_ts: float | None = None
    last_seen_ts: float | None = None
    last_seen_status: str = "unknown"
    discovery_source: str = ""
    mac_address: str = ""
    history: Deque[tuple[float, float | None]] = field(default_factory=lambda: deque(maxlen=MAX_HISTORY_POINTS))

    def add_sample(self, latency: float | None, *, ts: float | None = None) -> None:
        now = time.time() if ts is None else float(ts)
        self.history.append((now, latency))
        cutoff = now - MAX_HISTORY_SECONDS
        while self.history and self.history[0][0] < cutoff:
            self.history.popleft()
        self.latency_ms = latency
        self.last_seen_ts = now
        if latency is None:
            self.last_failed_ts = now
            self.last_seen_status = "fail"
        else:
            self.last_seen_status = "ok"

    def mark_discovered(self, *, source: str, latency: float | None = None, hostname: str = "", mac: str = "") -> None:
        now = time.time()
        self.discovery_source = source or self.discovery_source
        if hostname:
            self.hostname = hostname
            if not self.name or self.name == self.ip:
                self.name = hostname.split(".")[0]
        if mac:
            self.mac_address = mac
        self.last_seen_ts = now
        if latency is not None:
            self.add_sample(latency, ts=now)
        elif self.last_seen_status == "unknown":
            self.last_seen_status = "unknown"

    def history_points(self, window_seconds: float) -> list[tuple[float, float | None]]:
        cutoff = time.time() - float(window_seconds)
        return [(ts, value) for ts, value in self.history if ts >= cutoff]

    def to_config(self) -> dict:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "hostname": self.hostname,
            "ip": self.ip,
        }

    @classmethod
    def from_config(cls, payload: dict) -> "DeviceRecord":
        return cls(
            name=str(payload.get("name", "")),
            hostname=str(payload.get("hostname", "")),
            ip=str(payload.get("ip", "")),
            device_id=str(payload.get("device_id") or uuid.uuid4()),
        )


@dataclass(frozen=True)
class EventRecord:
    ts: float
    level: str
    source: str
    message: str


@dataclass
class AppSettings:
    refresh_seconds: float = 1.0
    ping_timeout_ms: int = 1000
    scan_mode: str = "All at Once"
    trend_graph_seconds: int = 15 * 60
    selected_interface_name: str = "Default Route"
    selected_interface_ip: str = ""
    discovery_start_ip: str = "172.20.1.1"
    discovery_end_ip: str = "172.20.1.254"
    discovery_subnet: str = "255.255.255.0"
    discovery_interval_seconds: float = 15.0
    green_max_ms: float = 10.0
    orange_max_ms: float = 50.0
    favourite_device_ids: list[str | None] = field(default_factory=lambda: [None, None, None])

    def normalised_favourites(self) -> list[str | None]:
        values = list(self.favourite_device_ids[:3])
        while len(values) < 3:
            values.append(None)
        return values


def find_device(devices: Iterable[DeviceRecord], device_id: str | None) -> DeviceRecord | None:
    if not device_id:
        return None
    return next((d for d in devices if d.device_id == device_id), None)
