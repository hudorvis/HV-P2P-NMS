from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path

from .config import save_config
from .constants import DISCOVERY_WORKERS, SCAN_MODE_ONE_BY_ONE
from .models import AppSettings, DeviceRecord, EventRecord
from .network import (
    ArpEntry, NetworkIdentity, discovery_targets, mdns_discover_identities,
    nmap_discover, normalize_device_name, normalize_hostname, ping_once, ping_probe,
    read_arp_entries, resolve_hostname,
)


class MonitorBackend:
    """Application-level scanner independent of the currently visible UI tab."""

    def __init__(self, settings: AppSettings, devices: list[DeviceRecord], config_path: Path):
        self.settings = settings
        self.devices = devices
        self.config_path = Path(config_path)
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

        # Separate pools prevent a discovery sweep from starving the always-on
        # Scan Mode workers or the hostname-resolution jobs.
        self._scan_pool = ThreadPoolExecutor(max_workers=32, thread_name_prefix="nms-ping")
        self._discovery_pool = ThreadPoolExecutor(max_workers=DISCOVERY_WORKERS, thread_name_prefix="nms-discovery-ping")
        self._name_pool = ThreadPoolExecutor(max_workers=10, thread_name_prefix="nms-name")
        self._aux_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="nms-aux")

        self._lock = threading.RLock()
        self._save_lock = threading.Lock()
        self._inflight: set[str] = set()
        self._name_inflight: set[tuple[int, str]] = set()
        self._name_attempted: dict[tuple[int, str], float] = {}
        self._nmap_inflight: set[int] = set()
        self._mdns_inflight: set[int] = set()
        self._one_by_one_index = 0
        self._scan_generation = 0
        self._discovery_generation = 0

    # ---------- lifecycle / persistence ----------
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
        self._scan_generation += 1
        self._discovery_generation += 1
        for pool in (self._scan_pool, self._discovery_pool, self._name_pool, self._aux_pool):
            pool.shutdown(wait=False, cancel_futures=True)

    def save(self) -> None:
        # UI actions can occur close together; one temp-file writer at a time
        # avoids two atomic-save operations racing on config.json.tmp.
        with self._save_lock:
            with self._lock:
                settings = self.settings
                devices = list(self.devices)
            save_config(self.config_path, settings, devices)

    def replace_configuration(self, settings: AppSettings, devices: list[DeviceRecord], *, persist: bool = True) -> None:
        """Atomically replace runtime configuration without adopting an import path.

        In-flight ping results from the previous configuration are invalidated,
        and Discovery is stopped because its range/interface may have changed.
        """
        self.stop_discovery(log_event=False)
        with self._lock:
            self._scan_generation += 1
            self._inflight.clear()
            self.settings = settings
            self.devices = devices
            valid_ids = {d.device_id for d in devices}
            self.settings.favourite_device_ids = [
                fav if fav in valid_ids else None for fav in self.settings.normalised_favourites()
            ]
            self._one_by_one_index = 0
        if persist:
            self.save()
        self._scan_wakeup.set()
        self.events.put(("full_refresh",))

    # ---------- event log ----------
    def log(self, level: str, source: str, message: str) -> None:
        rec = EventRecord(time.time(), level, source, message)
        with self._lock:
            self.event_history.append(rec)
            if len(self.event_history) > 5000:
                del self.event_history[:-5000]
        self.events.put(("event", rec))

    def event_snapshot(self) -> list[EventRecord]:
        with self._lock:
            return list(self.event_history)

    def clear_event_history(self) -> None:
        with self._lock:
            self.event_history.clear()
        self.events.put(("events_cleared",))

    # ---------- scan mode ----------
    def set_scan_active(self, active: bool) -> None:
        active = bool(active)
        if self.scan_active == active:
            return
        self.scan_active = active
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
                mode = self.settings.scan_mode
                refresh = float(self.settings.refresh_seconds)

            if mode == SCAN_MODE_ONE_BY_ONE:
                if snapshot:
                    idx = self._one_by_one_index % len(snapshot)
                    self._one_by_one_index = (idx + 1) % len(snapshot)
                    self._submit_ping(snapshot[idx])
            else:
                for device in snapshot:
                    self._submit_ping(device)

            elapsed = time.monotonic() - cycle_start
            wait_for = max(0.05, refresh - elapsed)
            self._scan_wakeup.wait(wait_for)
            self._scan_wakeup.clear()

    def _submit_ping(self, device: DeviceRecord) -> None:
        with self._lock:
            if device.device_id in self._inflight:
                return
            self._inflight.add(device.device_id)
            device_id = device.device_id
            submitted_ip = device.ip
            generation = self._scan_generation
            timeout_ms = self.settings.ping_timeout_ms
        future = self._scan_pool.submit(ping_once, submitted_ip, timeout_ms)
        future.add_done_callback(
            lambda f, did=device_id, ip=submitted_ip, gen=generation: self._ping_done(did, ip, gen, f)
        )

    def _ping_done(self, device_id: str, submitted_ip: str, generation: int, future: Future) -> None:
        try:
            latency = future.result()
        except Exception:
            latency = None

        with self._lock:
            self._inflight.discard(device_id)
            if generation != self._scan_generation:
                return
            device = next((d for d in self.devices if d.device_id == device_id), None)
            # If the IP was edited while this ping was in flight, discard the
            # old address's result instead of applying it to the edited row.
            if not device or device.ip != submitted_ip:
                return
            previous_status = device.last_seen_status
            previous_latency = device.latency_ms
            device.add_sample(latency)
            name = device.name
            ip = device.ip
            orange_max = self.settings.orange_max_ms

        self.events.put(("device_update", device_id))
        if latency is None:
            if previous_status != "fail":
                self.log("ERROR", name, f"{name} ({ip}) no response")
            return

        if previous_status != "ok":
            if latency > orange_max:
                self.log("WARN", name, f"{name} ({ip}) online with high latency: {latency:.2f} ms")
            else:
                self.log("INFO", name, f"{name} ({ip}) online at {latency:.2f} ms")
        elif latency > orange_max and (previous_latency is None or previous_latency <= orange_max):
            self.log("WARN", name, f"{name} ({ip}) high latency: {latency:.2f} ms")
        elif previous_latency is not None and previous_latency > orange_max >= latency:
            self.log("INFO", name, f"{name} ({ip}) latency recovered: {latency:.2f} ms")

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
        self.events.put(("events_cleared",))

    # ---------- device list ----------
    def add_device(self, name: str, ip: str, hostname: str = "") -> DeviceRecord:
        with self._lock:
            device = DeviceRecord(name=name, ip=ip, hostname=hostname)
            self.devices.append(device)
        self.save()
        self.events.put(("device_list",))
        return device

    def update_device(self, device_id: str, *, name: str, ip: str) -> None:
        with self._lock:
            device = next((d for d in self.devices if d.device_id == device_id), None)
            if device is None:
                raise KeyError(f"Unknown device id: {device_id}")
            old_ip = device.ip
            device.name = name
            device.ip = ip
            # Hostname from a prior IP must not be retained when the address changes.
            if old_ip != ip:
                device.hostname = ""
                device.history.clear()
                device.latency_ms = None
                device.last_failed_ts = None
                device.last_seen_ts = None
                device.last_seen_status = "unknown"
        self.save()
        self.events.put(("device_list",))
        self._scan_wakeup.set()

    def remove_devices(self, ids: set[str]) -> None:
        with self._lock:
            self.devices[:] = [d for d in self.devices if d.device_id not in ids]
            self._inflight.difference_update(ids)
            self.settings.favourite_device_ids = [
                None if x in ids else x for x in self.settings.normalised_favourites()
            ]
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
        with self._lock:
            if not any(d.device_id == device_id for d in self.devices):
                return
            favs = self.settings.normalised_favourites()
            favs[slot] = device_id
            self.settings.favourite_device_ids = favs
        self.save()
        self.events.put(("favourites",))

    # ---------- discovery ----------
    def start_discovery(self) -> None:
        if self.discovery_active:
            return
        # Validate and enforce the range limit before creating any workers.
        discovery_targets(
            self.settings.discovery_start_ip,
            self.settings.discovery_end_ip,
            self.settings.discovery_subnet,
        )
        self.discovery_active = True
        self._discovery_generation += 1
        generation = self._discovery_generation
        with self._lock:
            self.discovery_devices = []
            self.discovery_total_scanned = 0
            self._name_inflight.clear()
            self._name_attempted.clear()
            self._nmap_inflight.clear()
            self._mdns_inflight.clear()
        self.events.put(("discovery_state", True))
        self.events.put(("discovery_list",))
        self.log(
            "INFO",
            "DISCOVERY",
            f"Discovery started: {self.settings.discovery_start_ip} - {self.settings.discovery_end_ip}",
        )
        self._discovery_thread = threading.Thread(
            target=self._discovery_loop,
            args=(generation,),
            daemon=True,
            name="nms-discovery",
        )
        self._discovery_thread.start()

    def stop_discovery(self, *, log_event: bool = True) -> None:
        was_active = self.discovery_active
        self.discovery_active = False
        self._discovery_generation += 1
        self.events.put(("discovery_state", False))
        if was_active and log_event:
            self.log("INFO", "DISCOVERY", "Discovery stopped")

    def clear_discovery(self) -> None:
        with self._lock:
            self.discovery_devices = []
            self.discovery_total_scanned = 0
        self.events.put(("discovery_list",))

    def discovery_snapshot(self) -> list[DeviceRecord]:
        with self._lock:
            return list(self.discovery_devices)

    def _discovery_cancelled(self, generation: int) -> bool:
        return (
            self._stop.is_set()
            or not self.discovery_active
            or generation != self._discovery_generation
        )

    @staticmethod
    def _source_rank(source: str) -> int:
        return {"arp": 1, "mdns": 2, "bonjour": 2, "nmap": 2, "ping": 3}.get(source, 0)

    def _upsert_discovery(
        self,
        generation: int,
        ip: str,
        *,
        source: str,
        latency: float | None = None,
        hostname: str = "",
        mac: str = "",
    ) -> tuple[DeviceRecord | None, bool]:
        # Treat every discovery-source hostname as untrusted resolver text.
        # Only a syntactically valid, non-status hostname may reach the model.
        hostname = normalize_hostname(hostname)
        if self._discovery_cancelled(generation):
            return None, False
        with self._lock:
            if generation != self._discovery_generation:
                return None, False
            current = next((d for d in self.discovery_devices if d.ip == ip), None)
            is_new = current is None
            if current is None:
                current = DeviceRecord(name=(hostname.split(".")[0] if hostname else ip), ip=ip, hostname=hostname)
                self.discovery_devices.append(current)
            old_source = current.discovery_source
            chosen_source = source if self._source_rank(source) >= self._source_rank(old_source) else old_source
            current.mark_discovered(source=chosen_source, latency=latency, hostname=hostname, mac=mac)
            if latency is None:
                # Presence by ARP/nmap without a successful ICMP echo is not an
                # offline state. It is explicitly Unknown/Ping unavailable.
                current.last_seen_status = "unknown"
        self.events.put(("discovery_update", current.device_id))
        if is_new:
            self.log("INFO", "DISCOVERY", f"Discovered {hostname or ip} ({ip}) via {source}")
        return current, is_new

    def _schedule_hostname(self, generation: int, ip: str, hint: str = "") -> None:
        if hint:
            self._apply_hostname(generation, ip, hint)
            return
        key = (generation, ip)
        now = time.monotonic()
        with self._lock:
            current = next((d for d in self.discovery_devices if d.ip == ip), None)
            if current is not None and current.hostname:
                return
            last_attempt = self._name_attempted.get(key, 0.0)
            # A name lookup performed before ARP/mDNS caches have warmed can
            # legitimately return nothing. Retry unnamed hosts periodically
            # instead of permanently suppressing all later attempts.
            if key in self._name_inflight or now - last_attempt < 5.0 or generation != self._discovery_generation:
                return
            self._name_inflight.add(key)
            self._name_attempted[key] = now
        future = self._name_pool.submit(resolve_hostname, ip, hint)
        future.add_done_callback(lambda f, gen=generation, address=ip, k=key: self._hostname_done(gen, address, k, f))

    def _hostname_done(self, generation: int, ip: str, key: tuple[int, str], future: Future) -> None:
        with self._lock:
            self._name_inflight.discard(key)
        if self._discovery_cancelled(generation):
            return
        try:
            hostname = future.result() or ""
        except Exception:
            hostname = ""
        if hostname:
            self._apply_hostname(generation, ip, hostname)

    def _apply_hostname(self, generation: int, ip: str, hostname: str) -> None:
        # Sanitize at the final write boundary as well as inside individual
        # resolvers. This prevents resolver status strings (for example
        # NXDOMAIN) from ever becoming a Device/Host Name even if a future
        # resolver or direct discovery hint returns malformed text.
        hostname = normalize_hostname(hostname)
        if self._discovery_cancelled(generation) or not hostname:
            return
        changed = False
        device_id = None
        with self._lock:
            current = next((d for d in self.discovery_devices if d.ip == ip), None)
            if current is None or generation != self._discovery_generation:
                return
            if current.hostname != hostname:
                current.hostname = hostname
                if not current.name or current.name == current.ip:
                    current.name = hostname.split(".")[0]
                changed = True
            device_id = current.device_id
        if changed and device_id:
            self.events.put(("discovery_update", device_id))
            self.log("INFO", "DISCOVERY", f"Hostname resolved: {ip} = {hostname}")

    def _process_arp_entries(self, generation: int, entries: dict[str, ArpEntry], seen: set[str]) -> None:
        for ip, entry in entries.items():
            if self._discovery_cancelled(generation):
                return
            seen.add(ip)
            self._upsert_discovery(
                generation,
                ip,
                source="arp",
                hostname=entry.hostname,
                mac=entry.mac,
            )
            self._schedule_hostname(generation, ip, entry.hostname)

    def _nmap_done(self, generation: int, future: Future) -> None:
        with self._lock:
            self._nmap_inflight.discard(generation)
        if self._discovery_cancelled(generation):
            return
        try:
            found = future.result()
        except Exception:
            found = {}
        for ip, hostname in found.items():
            if self._discovery_cancelled(generation):
                return
            self._upsert_discovery(generation, ip, source="nmap", hostname=hostname)
            self._schedule_hostname(generation, ip, hostname)

    def _mdns_done(self, generation: int, future: Future) -> None:
        with self._lock:
            self._mdns_inflight.discard(generation)
        if self._discovery_cancelled(generation):
            return
        try:
            identities = future.result() or {}
        except Exception:
            identities = {}
        for ip, identity in identities.items():
            if self._discovery_cancelled(generation):
                return
            if not isinstance(identity, NetworkIdentity):
                continue
            current, _ = self._upsert_discovery(
                generation, ip, source=identity.source or "mdns", hostname=identity.hostname
            )
            if current is None:
                continue
            friendly = normalize_device_name(identity.device_name)
            changed = False
            with self._lock:
                live = next((d for d in self.discovery_devices if d.device_id == current.device_id), None)
                if live is None:
                    continue
                if identity.hostname and live.hostname != normalize_hostname(identity.hostname):
                    live.hostname = normalize_hostname(identity.hostname)
                    changed = True
                if friendly and (not live.name or live.name == live.ip or live.name == live.hostname.split(".")[0]):
                    if live.name != friendly:
                        live.name = friendly
                        changed = True
            if changed:
                self.events.put(("discovery_update", current.device_id))
                self.log("INFO", "DISCOVERY", f"Network identity: {ip} = {friendly or identity.hostname}")

    def _discovery_loop(self, generation: int) -> None:
        try:
            while not self._discovery_cancelled(generation):
                all_targets = discovery_targets(
                    self.settings.discovery_start_ip,
                    self.settings.discovery_end_ip,
                    self.settings.discovery_subnet,
                )
                with self._lock:
                    self.discovery_total_scanned = len(all_targets)
                    existing_scan_ips = {d.ip for d in self.devices}
                targets = [ip for ip in all_targets if ip not in existing_scan_ips]
                target_set = set(targets)
                seen_this_pass: set[str] = set()

                if not targets:
                    self.events.put(("discovery_list",))
                else:
                    # 1) Show cached ARP neighbours immediately. This makes a
                    # repeated scan populate before any timeout-based work.
                    self._process_arp_entries(generation, read_arp_entries(target_set), seen_this_pass)

                    # 2) nmap is optional and fully asynchronous. It can never
                    # block ping/ARP results from reaching the UI.
                    with self._lock:
                        start_nmap = generation not in self._nmap_inflight
                        if start_nmap:
                            self._nmap_inflight.add(generation)
                    if start_nmap:
                        nmap_future = self._aux_pool.submit(
                            nmap_discover,
                            targets,
                            lambda gen=generation: self._discovery_cancelled(gen),
                        )
                        nmap_future.add_done_callback(lambda f, gen=generation: self._nmap_done(gen, f))

                    # Active mDNS/DNS-SD identity discovery runs in parallel
                    # with nmap and ping. This is what supplies friendly Device
                    # and Host Name values on LANs that have no DNS PTR zone.
                    with self._lock:
                        start_mdns = generation not in self._mdns_inflight
                        if start_mdns:
                            self._mdns_inflight.add(generation)
                        interface_ip = self.settings.selected_interface_ip
                    if start_mdns:
                        mdns_future = self._aux_pool.submit(
                            mdns_discover_identities,
                            target_set,
                            interface_ip,
                            cancelled=lambda gen=generation: self._discovery_cancelled(gen),
                        )
                        mdns_future.add_done_callback(lambda f, gen=generation: self._mdns_done(gen, f))

                    # 3) Stream each successful ping as soon as it completes.
                    timeout_ms = self.settings.ping_timeout_ms
                    futures = {self._discovery_pool.submit(ping_probe, ip, timeout_ms): ip for ip in targets}
                    last_arp_poll = time.monotonic()
                    completed = 0
                    cancelled = False
                    for future in as_completed(futures):
                        if self._discovery_cancelled(generation):
                            cancelled = True
                            break
                        ip = futures[future]
                        completed += 1
                        try:
                            latency, ping_hostname = future.result()
                        except Exception:
                            latency, ping_hostname = None, ""
                        if latency is not None:
                            seen_this_pass.add(ip)
                            self._upsert_discovery(
                                generation, ip, source="ping", latency=latency, hostname=ping_hostname
                            )
                            self._schedule_hostname(generation, ip, ping_hostname)

                        # Ping to a same-subnet host has already triggered ARP,
                        # even when that host blocks ICMP. Poll ARP periodically
                        # so those devices also appear during, not after, a sweep.
                        now = time.monotonic()
                        if completed % 16 == 0 or now - last_arp_poll >= 0.45:
                            self._process_arp_entries(
                                generation,
                                read_arp_entries(target_set),
                                seen_this_pass,
                            )
                            last_arp_poll = now

                    if cancelled:
                        for pending in futures:
                            pending.cancel()
                        return

                    # Final ARP harvest catches late neighbour-cache entries.
                    self._process_arp_entries(generation, read_arp_entries(target_set), seen_this_pass)

                # Only previously shown hosts absent from the completed primary
                # ping/ARP pass become No Response. If optional nmap is still
                # running, defer that transition to avoid a red/unknown flicker
                # for devices that intentionally block both ICMP and ARP probes.
                with self._lock:
                    identity_pending = generation in self._nmap_inflight or generation in self._mdns_inflight
                    changed_ids: list[str] = []
                    if not identity_pending:
                        for current in self.discovery_devices:
                            if current.ip in target_set and current.ip not in seen_this_pass and current.discovery_source != "nmap":
                                if current.last_seen_status != "fail":
                                    current.latency_ms = None
                                    current.last_seen_status = "fail"
                                    changed_ids.append(current.device_id)
                for device_id in changed_ids:
                    self.events.put(("discovery_update", device_id))

                self.events.put(("discovery_list",))
                pause_until = time.monotonic() + max(1.0, float(self.settings.discovery_interval_seconds))
                while not self._discovery_cancelled(generation) and time.monotonic() < pause_until:
                    time.sleep(0.1)
        except Exception as exc:
            self.events.put(("discovery_error", str(exc)))
            self.log("ERROR", "DISCOVERY", f"Discovery error: {exc}")
        finally:
            if generation == self._discovery_generation:
                self.discovery_active = False
                self.events.put(("discovery_state", False))

    def add_discovery_to_scan(self, device_ids: set[str]) -> int:
        added = 0
        with self._lock:
            existing_ips = {d.ip for d in self.devices}
            for d in list(self.discovery_devices):
                if d.device_id in device_ids and d.ip not in existing_ips:
                    self.devices.append(DeviceRecord(name=d.name, ip=d.ip, hostname=d.hostname))
                    existing_ips.add(d.ip)
                    added += 1
        if added:
            self.save()
            self.events.put(("device_list",))
            self.log("INFO", "DISCOVERY", f"Added {added} discovered device(s) to Scan Mode")
        return added
