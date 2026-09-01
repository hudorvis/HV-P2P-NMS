from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path

from .config import save_config
from .constants import DISCOVERY_WORKERS, SCAN_MODE_ONE_BY_ONE
from .models import AppSettings, DeviceRecord, EventRecord
from .network import discovery_targets, nmap_discover, ping_once, read_arp_entries, resolve_hostname


class MonitorBackend:
    """Application-level scanner. It is independent of which UI tab is visible."""

    def __init__(self, settings: AppSettings, devices: list[DeviceRecord], config_path: Path):
        self.settings = settings
        self.devices = devices
        self.config_path = config_path
        self.events: queue.Queue[tuple] = queue.Queue()
        self.event_history: list[EventRecord] = []
        self.discovery_devices: list[DeviceRecord] = []
        self.discovery_total_scanned = 0
        self.scan_active = False
        self.discovery_active = False
        self._stop = threading.Event()
        self._scan_wakeup = threading.Event()
        self._scan_thread: threading.Thread | None = None
        self._discovery_thread: threading.Thread | None = None
        self._pool = ThreadPoolExecutor(max_workers=32, thread_name_prefix="nms-ping")
        self._lock = threading.RLock()
        self._inflight: set[str] = set()
        self._one_by_one_index = 0
        self._discovery_generation = 0

    def start(self) -> None:
        if self._scan_thread and self._scan_thread.is_alive():
            return
        self._stop.clear()
        self._scan_thread = threading.Thread(target=self._scan_loop, daemon=True, name="nms-scan-loop")
        self._scan_thread.start()

    def shutdown(self) -> None:
        self.scan_active = False
        self.discovery_active = False
        self._stop.set()
        self._scan_wakeup.set()
        self._discovery_generation += 1
        self._pool.shutdown(wait=False, cancel_futures=True)

    def save(self) -> None:
        save_config(self.config_path, self.settings, self.devices)

    def log(self, level: str, source: str, message: str) -> None:
        rec = EventRecord(time.time(), level, source, message)
        self.event_history.append(rec)
        if len(self.event_history) > 5000:
            self.event_history = self.event_history[-5000:]
        self.events.put(("event", rec))

    def set_scan_active(self, active: bool) -> None:
        self.scan_active = bool(active)
        self._scan_wakeup.set()
        self.log("INFO", "SCAN", "Scan Mode active" if active else "Scan Mode stopped")
        self.events.put(("scan_state", self.scan_active))

    def _scan_loop(self) -> None:
        while not self._stop.is_set():
            if not self.scan_active:
                self._scan_wakeup.wait(0.25)
                self._scan_wakeup.clear()
                continue
            cycle_start = time.monotonic()
            with self._lock:
                snapshot = list(self.devices)
            if self.settings.scan_mode == SCAN_MODE_ONE_BY_ONE:
                if snapshot:
                    idx = self._one_by_one_index % len(snapshot)
                    self._one_by_one_index = (idx + 1) % len(snapshot)
                    self._submit_ping(snapshot[idx])
            else:
                for device in snapshot:
                    self._submit_ping(device)
            elapsed = time.monotonic() - cycle_start
            wait_for = max(0.05, float(self.settings.refresh_seconds) - elapsed)
            self._scan_wakeup.wait(wait_for)
            self._scan_wakeup.clear()

    def _submit_ping(self, device: DeviceRecord) -> None:
        with self._lock:
            if device.device_id in self._inflight:
                return
            self._inflight.add(device.device_id)
        future = self._pool.submit(ping_once, device.ip, self.settings.ping_timeout_ms)
        future.add_done_callback(lambda f, device_id=device.device_id: self._ping_done(device_id, f))

    def _ping_done(self, device_id: str, future: Future) -> None:
        try:
            latency = future.result()
        except Exception:
            latency = None
        with self._lock:
            self._inflight.discard(device_id)
            device = next((d for d in self.devices if d.device_id == device_id), None)
            if not device:
                return
            previous = device.last_seen_status
            device.add_sample(latency)
        self.events.put(("device_update", device_id))
        if latency is None:
            if previous != "fail":
                self.log("ERROR", device.name, f"{device.name} ({device.ip}) no response")
        else:
            if previous != "ok":
                self.log("INFO", device.name, f"{device.name} ({device.ip}) online at {latency:.2f} ms")
            elif latency > self.settings.orange_max_ms:
                self.log("WARN", device.name, f"{device.name} ({device.ip}) high latency: {latency:.2f} ms")

    def clear_history(self) -> None:
        with self._lock:
            for d in self.devices:
                d.history.clear()
                d.latency_ms = None
                d.last_failed_ts = None
                d.last_seen_ts = None
                d.last_seen_status = "unknown"
        self.event_history.clear()
        self.events.put(("full_refresh",))

    def add_device(self, name: str, ip: str, hostname: str = "") -> DeviceRecord:
        with self._lock:
            device = DeviceRecord(name=name, ip=ip, hostname=hostname)
            self.devices.append(device)
        self.save()
        self.events.put(("device_list",))
        return device

    def update_device(self, device_id: str, *, name: str, ip: str) -> None:
        with self._lock:
            device = next(d for d in self.devices if d.device_id == device_id)
            device.name = name
            device.ip = ip
        self.save()
        self.events.put(("device_list",))

    def remove_devices(self, ids: set[str]) -> None:
        with self._lock:
            self.devices[:] = [d for d in self.devices if d.device_id not in ids]
            self.settings.favourite_device_ids = [None if x in ids else x for x in self.settings.normalised_favourites()]
        self.save()
        self.events.put(("device_list",))

    def reorder_device(self, source_index: int, dest_index: int) -> None:
        with self._lock:
            if not (0 <= source_index < len(self.devices)):
                return
            dest_index = max(0, min(len(self.devices) - 1, dest_index))
            device = self.devices.pop(source_index)
            self.devices.insert(dest_index, device)
        self.save()
        self.events.put(("device_list",))

    def set_favourite(self, slot: int, device_id: str) -> None:
        if slot not in (0, 1, 2):
            return
        favs = self.settings.normalised_favourites()
        favs[slot] = device_id
        self.settings.favourite_device_ids = favs
        self.save()
        self.events.put(("favourites",))

    def start_discovery(self) -> None:
        if self.discovery_active:
            return
        # Validate before starting the worker.
        discovery_targets(self.settings.discovery_start_ip, self.settings.discovery_end_ip, self.settings.discovery_subnet)
        self.discovery_active = True
        self._discovery_generation += 1
        generation = self._discovery_generation
        self.discovery_devices = []
        self.discovery_total_scanned = 0
        self.events.put(("discovery_state", True))
        self.events.put(("discovery_list",))
        self._discovery_thread = threading.Thread(target=self._discovery_loop, args=(generation,), daemon=True, name="nms-discovery")
        self._discovery_thread.start()

    def stop_discovery(self) -> None:
        self.discovery_active = False
        self._discovery_generation += 1
        self.events.put(("discovery_state", False))

    def clear_discovery(self) -> None:
        self.discovery_devices = []
        self.discovery_total_scanned = 0
        self.events.put(("discovery_list",))

    def _discovery_loop(self, generation: int) -> None:
        try:
            while self.discovery_active and generation == self._discovery_generation and not self._stop.is_set():
                targets = discovery_targets(self.settings.discovery_start_ip, self.settings.discovery_end_ip, self.settings.discovery_subnet)
                self.discovery_total_scanned = len(targets)
                existing_scan_ips = {d.ip for d in self.devices}
                targets = [ip for ip in targets if ip not in existing_scan_ips]
                target_set = set(targets)

                nmap_found = nmap_discover(targets)
                found: dict[str, tuple[float | None, str, str, str]] = {}

                for ip, host in nmap_found.items():
                    found[ip] = (None, host, "nmap", "")

                # Ping first so the host ARP cache is populated before we read it.
                with ThreadPoolExecutor(max_workers=min(DISCOVERY_WORKERS, max(1, len(targets)))) as executor:
                    futures = {executor.submit(ping_once, ip, self.settings.ping_timeout_ms): ip for ip in targets}
                    for future in as_completed(futures):
                        if not self.discovery_active or generation != self._discovery_generation:
                            return
                        ip = futures[future]
                        try:
                            latency = future.result()
                        except Exception:
                            latency = None
                        if latency is not None:
                            old = found.get(ip, (None, "", "ping", ""))
                            found[ip] = (latency, old[1], "ping", old[3])

                # Read ARP after pinging: this catches devices that answer L2/ARP but block ICMP.
                arp_found = read_arp_entries(target_set)
                for ip, mac in arp_found.items():
                    latency, host, source, _ = found.get(ip, (None, "", "arp", ""))
                    found[ip] = (latency, host, source if source in {"nmap", "ping"} else "arp", mac)

                # Resolve names only for hosts that were found by at least one method.
                for ip in sorted(found):
                    if not found[ip][1]:
                        latency, _, source, mac = found[ip]
                        found[ip] = (latency, resolve_hostname(ip), source, mac)

                found_ips = set(found)
                for ip, (latency, hostname, source, mac) in sorted(found.items(), key=lambda x: tuple(int(p) for p in x[0].split("."))):
                    if not self.discovery_active or generation != self._discovery_generation:
                        return
                    current = next((d for d in self.discovery_devices if d.ip == ip), None)
                    if current is None:
                        current = DeviceRecord(name=(hostname.split(".")[0] if hostname else ip), ip=ip, hostname=hostname)
                        self.discovery_devices.append(current)
                    current.mark_discovered(source=source, latency=latency, hostname=hostname, mac=mac)
                    if latency is None:
                        current.last_seen_status = "unknown"
                    self.events.put(("discovery_update", current.device_id))

                # A host that was discovered on a previous pass but disappears on this pass
                # becomes No Response; we do not add every unused IP in the subnet to the table.
                for current in self.discovery_devices:
                    if current.ip not in found_ips:
                        current.latency_ms = None
                        current.last_seen_status = "fail"

                self.events.put(("discovery_list",))
                pause_until = time.time() + max(1.0, float(self.settings.discovery_interval_seconds))
                while self.discovery_active and generation == self._discovery_generation and time.time() < pause_until:
                    time.sleep(0.1)
        except Exception as exc:
            self.events.put(("discovery_error", str(exc)))
        finally:
            if generation == self._discovery_generation:
                self.discovery_active = False
                self.events.put(("discovery_state", False))

    def add_discovery_to_scan(self, device_ids: set[str]) -> int:
        added = 0
        existing_ips = {d.ip for d in self.devices}
        for d in list(self.discovery_devices):
            if d.device_id in device_ids and d.ip not in existing_ips:
                self.devices.append(DeviceRecord(name=d.name, ip=d.ip, hostname=d.hostname))
                existing_ips.add(d.ip)
                added += 1
        if added:
            self.save()
            self.events.put(("device_list",))
        return added
