from __future__ import annotations

import json
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace

import pytest

import hv_nms.backend as backend_module
import hv_nms.network as network_module
from hv_nms.backend import MonitorBackend
from hv_nms.config import load_config, save_config
from hv_nms.models import AppSettings, DeviceRecord
from hv_nms.network import (
    ArpEntry,
    NetworkInterface,
    discovery_targets,
    hostname_from_ping_output,
    normalize_hostname,
    interface_discovery_range,
    read_arp_entries,
    resolve_hostname,
    scan_interfaces,
)


def wait_until(predicate, timeout: float = 1.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def future_with(value):
    f = Future()
    f.set_result(value)
    return f


def test_discovery_targets_respects_real_subnet_boundaries():
    targets = discovery_targets("10.2.3.250", "10.2.4.5", "255.255.0.0")
    assert targets[0] == "10.2.3.250"
    assert targets[-1] == "10.2.4.5"
    assert "10.2.4.1" in targets


def test_discovery_targets_rejects_huge_range_before_allocation():
    started = time.monotonic()
    with pytest.raises(ValueError, match="limited"):
        discovery_targets("10.0.0.1", "10.255.255.254", "255.0.0.0")
    assert time.monotonic() - started < 0.5


def test_interface_discovery_range_handles_non_24_without_hosts_list():
    start, end, mask = interface_discovery_range(NetworkInterface("en0", "10.2.3.44", 16))
    assert start == "10.2.0.1"
    assert end == "10.2.255.254"
    assert mask == "255.255.0.0"


def test_interface_discovery_range_handles_31_and_32():
    assert interface_discovery_range(NetworkInterface("x", "192.0.2.10", 31))[:2] == ("192.0.2.10", "192.0.2.11")
    assert interface_discovery_range(NetworkInterface("x", "192.0.2.10", 32))[:2] == ("192.0.2.10", "192.0.2.10")


def test_default_route_inherits_real_interface_prefix(monkeypatch):
    monkeypatch.setattr(network_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(network_module, "get_local_ipv4", lambda: "172.20.7.5")
    monkeypatch.setattr(
        network_module,
        "_scan_macos_interfaces",
        lambda: [NetworkInterface("en7", "172.20.7.5", 16), NetworkInterface("en0", "192.168.1.5", 24)],
    )
    interfaces = scan_interfaces()
    assert interfaces[0].name == "Default Route"
    assert interfaces[0].ip == "172.20.7.5"
    assert interfaces[0].prefix == 16


def test_arp_parser_preserves_hostname_and_mac(monkeypatch):
    monkeypatch.setattr(network_module.shutil, "which", lambda _cmd: "/usr/sbin/arp")

    def fake_run(cmd, **_kwargs):
        if cmd[-1] == "-a":
            return SimpleNamespace(stdout="camera-3.local (172.20.1.82) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]\n")
        return SimpleNamespace(stdout="? (172.20.1.82) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]\n")

    monkeypatch.setattr(network_module.subprocess, "run", fake_run)
    entries = read_arp_entries({"172.20.1.82"})
    assert entries["172.20.1.82"] == ArpEntry(mac="AA:BB:CC:DD:EE:FF", hostname="camera-3.local")


def test_hostname_from_ping_output_windows_and_unix():
    ip = "172.20.1.82"
    assert hostname_from_ping_output(ip, f"Pinging CAMERA3 [{ip}] with 32 bytes of data:") == "CAMERA3"
    assert hostname_from_ping_output(ip, f"PING camera3.local ({ip}): 56 data bytes") == "camera3.local"


def test_resolve_hostname_uses_fast_hint_without_external_lookup(monkeypatch):
    monkeypatch.setattr(network_module.socket, "gethostbyaddr", lambda _ip: (_ for _ in ()).throw(AssertionError("must not run")))
    assert resolve_hostname("172.20.1.82", "camera.local") == "camera.local"



def test_resolver_status_tokens_are_never_hostnames(monkeypatch):
    for value in ("NXDOMAIN", "SERVFAIL", "REFUSED", "NOTFOUND", "NOERROR"):
        assert normalize_hostname(value) == ""

    # Reproduce the macOS dns-sd shape that exposed the v26.09.02.03 bug:
    # a reverse query line contains in-addr.arpa and ends with NXDOMAIN.
    monkeypatch.setattr(network_module.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(network_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    def fake_output(cmd, timeout=2.0):
        if cmd and cmd[0] == "dns-sd":
            return "21:24:12.123  Add  2  0  82.1.20.172.in-addr.arpa. PTR NXDOMAIN"
        if cmd and cmd[0] in {"dscacheutil", "host", "dig", "nslookup", "nmblookup", "nbtscan", "smbutil"}:
            return "NXDOMAIN"
        return ""

    monkeypatch.setattr(network_module, "_command_output", fake_output)
    assert resolve_hostname("172.20.1.82") == ""


def test_backend_rejects_invalid_hostname_hint(tmp_path):
    backend = MonitorBackend(AppSettings(), [], tmp_path / "config.json")
    backend.discovery_active = True
    backend._discovery_generation = 7
    device, _ = backend._upsert_discovery(7, "172.20.1.82", source="arp", hostname="NXDOMAIN")
    assert device is not None
    assert device.name == "172.20.1.82"
    assert device.hostname == ""
    backend._apply_hostname(7, "172.20.1.82", "NXDOMAIN")
    snapshot = backend.discovery_snapshot()
    assert snapshot[0].name == "172.20.1.82"
    assert snapshot[0].hostname == ""
    backend.shutdown()

def test_config_round_trip_preserves_stable_ids_and_favourites(tmp_path):
    a = DeviceRecord("CTRL", "172.20.1.101")
    b = DeviceRecord("W1P", "172.20.1.102")
    settings = AppSettings(favourite_device_ids=[b.device_id, a.device_id, None])
    path = tmp_path / "config.json"
    save_config(path, settings, [a, b])
    loaded_settings, devices = load_config(path)
    assert [d.device_id for d in devices] == [a.device_id, b.device_id]
    assert loaded_settings.normalised_favourites() == [b.device_id, a.device_id, None]


def test_malformed_optional_config_values_fall_back_safely(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "refresh_seconds": "nonsense",
                "ping_timeout_ms": None,
                "trend_graph_seconds": "bogus",
                "discovery_settings": {"start_ip": "not-an-ip", "end_ip": 5, "subnet": "bad", "interval_seconds": "no"},
                "thresholds": {"green_max_ms": "nan", "orange_max_ms": "inf"},
                "devices": [{"name": "A", "ip": "10.0.0.1"}],
            }
        )
    )
    settings, devices = load_config(path)
    assert settings.refresh_seconds == 1.0
    assert settings.ping_timeout_ms == 1000
    assert settings.trend_graph_seconds == 15 * 60
    assert settings.discovery_start_ip == "172.20.1.1"
    assert settings.discovery_end_ip == "172.20.1.254"
    assert settings.discovery_subnet == "255.255.255.0"
    assert len(devices) == 1


def test_duplicate_device_ids_are_repaired_and_bad_favourites_removed(tmp_path):
    path = tmp_path / "dupes.json"
    path.write_text(
        json.dumps(
            {
                "devices": [
                    {"device_id": "same", "name": "A", "ip": "10.0.0.1"},
                    {"device_id": "same", "name": "B", "ip": "10.0.0.2"},
                ],
                "favourite_device_ids": ["same", "missing", None],
            }
        )
    )
    settings, devices = load_config(path)
    assert len({d.device_id for d in devices}) == 2
    assert settings.favourite_device_ids[0] == "same"
    assert settings.favourite_device_ids[1] is None


def test_legacy_config_without_device_ids_is_accepted(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({"devices": [{"name": "SRVR", "hostname": "", "ip": "172.20.1.70"}]}))
    _settings, devices = load_config(path)
    assert len(devices) == 1
    assert devices[0].device_id
    assert devices[0].name == "SRVR"


def test_discovery_interval_round_trip(tmp_path):
    settings = AppSettings(discovery_interval_seconds=15.0)
    path = tmp_path / "config.json"
    save_config(path, settings, [DeviceRecord("A", "10.0.0.1")])
    loaded, _ = load_config(path)
    assert loaded.discovery_interval_seconds == 15.0


def test_ping_result_is_applied_by_device_id_not_row_index(tmp_path):
    a = DeviceRecord("A", "10.0.0.1")
    b = DeviceRecord("B", "10.0.0.2")
    backend = MonitorBackend(AppSettings(), [a, b], tmp_path / "config.json")
    backend.reorder_device(0, 1)
    backend._ping_done(a.device_id, a.ip, backend._scan_generation, future_with(3.25))
    assert a.latency_ms == 3.25
    assert b.latency_ms is None
    backend.shutdown()


def test_stale_ping_result_is_rejected_after_ip_edit(tmp_path):
    a = DeviceRecord("A", "10.0.0.1", hostname="old.local")
    backend = MonitorBackend(AppSettings(), [a], tmp_path / "config.json")
    a.add_sample(4.0)
    old_ip = a.ip
    generation = backend._scan_generation
    backend.update_device(a.device_id, name="A", ip="10.0.0.99")
    backend._ping_done(a.device_id, old_ip, generation, future_with(8.0))
    assert a.ip == "10.0.0.99"
    assert a.latency_ms is None
    assert a.hostname == ""
    assert list(a.history) == []
    assert a.last_failed_ts is None
    backend.shutdown()


def test_configuration_replacement_invalidates_old_ping_generation(tmp_path):
    a = DeviceRecord("A", "10.0.0.1")
    backend = MonitorBackend(AppSettings(), [a], tmp_path / "default.json")
    old_generation = backend._scan_generation
    replacement = DeviceRecord("NEW", "10.0.0.5", device_id=a.device_id)
    backend.replace_configuration(AppSettings(), [replacement], persist=False)
    backend._ping_done(a.device_id, "10.0.0.5", old_generation, future_with(2.0))
    assert replacement.latency_ms is None
    assert backend.config_path == tmp_path / "default.json"
    backend.shutdown()


def test_remove_device_clears_only_affected_favourite(tmp_path):
    a = DeviceRecord("A", "10.0.0.1")
    b = DeviceRecord("B", "10.0.0.2")
    settings = AppSettings(favourite_device_ids=[a.device_id, b.device_id, a.device_id])
    backend = MonitorBackend(settings, [a, b], tmp_path / "config.json")
    backend.remove_devices({a.device_id})
    assert settings.normalised_favourites() == [None, b.device_id, None]
    assert [d.device_id for d in backend.devices] == [b.device_id]
    backend.shutdown()


def test_invalid_favourite_assignment_is_ignored(tmp_path):
    a = DeviceRecord("A", "10.0.0.1")
    backend = MonitorBackend(AppSettings(), [a], tmp_path / "config.json")
    backend.set_favourite(0, "does-not-exist")
    assert backend.settings.normalised_favourites() == [None, None, None]
    backend.shutdown()


def test_high_latency_event_logs_on_transition_not_every_scan(tmp_path):
    a = DeviceRecord("A", "10.0.0.1")
    backend = MonitorBackend(AppSettings(orange_max_ms=50.0), [a], tmp_path / "config.json")
    gen = backend._scan_generation
    backend._ping_done(a.device_id, a.ip, gen, future_with(60.0))
    backend._ping_done(a.device_id, a.ip, gen, future_with(70.0))
    backend._ping_done(a.device_id, a.ip, gen, future_with(5.0))
    messages = [e.message for e in backend.event_snapshot()]
    assert sum("high latency" in m for m in messages) == 1
    assert sum("latency recovered" in m for m in messages) == 1
    backend.shutdown()


def test_discovery_streams_ping_result_before_slow_nmap_finishes(tmp_path, monkeypatch):
    settings = AppSettings(
        discovery_start_ip="10.0.0.1",
        discovery_end_ip="10.0.0.3",
        discovery_subnet="255.255.255.0",
        discovery_interval_seconds=60.0,
    )
    backend = MonitorBackend(settings, [], tmp_path / "config.json")
    nmap_finished = threading.Event()

    def fake_ping(ip, _timeout):
        if ip.endswith(".1"):
            return 1.25
        time.sleep(0.12)
        return None

    def fake_nmap(_targets, _cancelled=None):
        time.sleep(0.6)
        nmap_finished.set()
        return {}

    monkeypatch.setattr(backend_module, "ping_probe", lambda ip, timeout: (fake_ping(ip, timeout), ""))
    monkeypatch.setattr(backend_module, "nmap_discover", fake_nmap)
    monkeypatch.setattr(backend_module, "read_arp_entries", lambda _targets=None: {})
    monkeypatch.setattr(backend_module, "resolve_hostname", lambda _ip, _hint="": "")

    backend.start_discovery()
    try:
        assert wait_until(lambda: any(d.ip == "10.0.0.1" for d in backend.discovery_snapshot()), timeout=0.3)
        assert not nmap_finished.is_set(), "first UI-visible ping result must not wait for nmap"
    finally:
        backend.stop_discovery()
        backend.shutdown()


def test_discovery_hostname_populates_asynchronously_after_row_appears(tmp_path, monkeypatch):
    settings = AppSettings(
        discovery_start_ip="10.0.0.1",
        discovery_end_ip="10.0.0.1",
        discovery_subnet="255.255.255.0",
        discovery_interval_seconds=60.0,
    )
    backend = MonitorBackend(settings, [], tmp_path / "config.json")

    monkeypatch.setattr(backend_module, "ping_probe", lambda _ip, _timeout: (2.0, ""))
    monkeypatch.setattr(backend_module, "read_arp_entries", lambda _targets=None: {})
    monkeypatch.setattr(backend_module, "nmap_discover", lambda _targets, _cancelled=None: {})

    def slow_name(_ip, _hint=""):
        time.sleep(0.15)
        return "camera.local"

    monkeypatch.setattr(backend_module, "resolve_hostname", slow_name)
    backend.start_discovery()
    try:
        assert wait_until(lambda: len(backend.discovery_snapshot()) == 1, timeout=0.15)
        assert backend.discovery_snapshot()[0].hostname == ""
        assert wait_until(lambda: backend.discovery_snapshot()[0].hostname == "camera.local", timeout=0.6)
        assert backend.discovery_snapshot()[0].name == "camera"
    finally:
        backend.stop_discovery()
        backend.shutdown()


def test_arp_only_discovery_is_unknown_not_offline(tmp_path):
    backend = MonitorBackend(AppSettings(), [], tmp_path / "config.json")
    backend.discovery_active = True
    backend._discovery_generation = 1
    device, _new = backend._upsert_discovery(
        1,
        "10.0.0.8",
        source="arp",
        hostname="panel.local",
        mac="AA:BB:CC:DD:EE:FF",
    )
    assert device is not None
    assert device.last_seen_status == "unknown"
    assert device.latency_ms is None
    backend.stop_discovery()
    backend.shutdown()


def test_design_lock_neutral_palette_matches_srvr_reference():
    from hv_nms.constants import PANEL_BG, PANEL_BG_2, WINDOW_BG

    assert PANEL_BG.upper() == "#171D20"
    assert PANEL_BG_2.upper() == "#161C20"
    assert WINDOW_BG.upper() == "#0F1316"


def test_mdns_service_census_maps_friendly_device_and_hostname(monkeypatch):
    def fake_query(questions, *, local_ip="", timeout=0.7):
        names = {name.rstrip(".").lower(): qtype for name, qtype in questions}
        if "_services._dns-sd._udp.local" in names:
            return [network_module._DnsRecord("_services._dns-sd._udp.local.", 12, "_http._tcp.local.")]
        if "_http._tcp.local" in names:
            return [network_module._DnsRecord("_http._tcp.local.", 12, "Camera 3._http._tcp.local.")]
        if "camera 3._http._tcp.local" in names:
            return [
                network_module._DnsRecord("camera 3._HTTP._TCP.LOCAL.", 33, (0, 0, 80, "camera-3.local.")),
                network_module._DnsRecord("camera-3.local.", 1, "10.0.0.8"),
            ]
        return []

    monkeypatch.setattr(network_module, "_mdns_query", fake_query)
    result = network_module.mdns_discover_identities({"10.0.0.8"}, "10.0.0.2")
    assert result["10.0.0.8"].hostname == "camera-3.local"
    assert result["10.0.0.8"].device_name == "Camera 3"
    assert result["10.0.0.8"].source == "bonjour"


def test_mdns_reverse_ptr_can_supply_hostname_without_service(monkeypatch):
    def fake_query(questions, *, local_ip="", timeout=0.7):
        if any(name.rstrip(".").lower() == "8.0.0.10.in-addr.arpa" for name, _ in questions):
            return [network_module._DnsRecord("8.0.0.10.in-addr.arpa.", 12, "panel.local.")]
        return []

    monkeypatch.setattr(network_module, "_mdns_query", fake_query)
    result = network_module.mdns_discover_identities({"10.0.0.8"}, "10.0.0.2")
    assert result["10.0.0.8"].hostname == "panel.local"
    assert result["10.0.0.8"].device_name == "panel"


def test_backend_mdns_identity_updates_device_and_hostname(tmp_path):
    backend = MonitorBackend(AppSettings(), [], tmp_path / "config.json")
    backend.discovery_active = True
    backend._discovery_generation = 4
    backend._upsert_discovery(4, "10.0.0.8", source="ping", latency=1.0)
    backend._mdns_inflight.add(4)
    backend._mdns_done(
        4,
        future_with({"10.0.0.8": network_module.NetworkIdentity("camera-3.local", "Camera 3", "bonjour")}),
    )
    device = backend.discovery_snapshot()[0]
    assert device.hostname == "camera-3.local"
    assert device.name == "Camera 3"
    backend.stop_discovery()
    backend.shutdown()


def test_dns_parser_handles_compressed_mdns_ptr_answer():
    import struct

    qname = network_module._dns_encode_name("_http._tcp.local.")
    question = qname + struct.pack("!HH", 12, 1)
    rdata = b"\x06Camera\xc0\x0c"
    answer = b"\xc0\x0c" + struct.pack("!HHIH", 12, 1, 120, len(rdata)) + rdata
    packet = struct.pack("!HHHHHH", 0, 0x8400, 1, 1, 0, 0) + question + answer
    records = network_module._dns_parse_records(packet)
    assert len(records) == 1
    assert records[0].name == "_http._tcp.local."
    assert records[0].value == "Camera._http._tcp.local."


def test_ping_probe_reuses_ping_banner_hostname(monkeypatch):
    monkeypatch.setattr(network_module, "_find_executable", lambda name: f"/sbin/{name}")
    monkeypatch.setattr(network_module.platform, "system", lambda: "Darwin")

    def fake_run(cmd, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="PING camera-3.local (172.20.1.82): 56 data bytes\n64 bytes from 172.20.1.82: icmp_seq=0 ttl=64 time=1.250 ms\n",
            stderr="",
        )

    monkeypatch.setattr(network_module.subprocess, "run", fake_run)
    latency, hostname = network_module.ping_probe("172.20.1.82", 1000)
    assert latency == 1.25
    assert hostname == "camera-3.local"


def test_netbios_node_status_fallback_returns_workstation_name(monkeypatch):
    import struct

    class FakeSocket:
        def __init__(self, *_args, **_kwargs):
            self.sent = b""

        def settimeout(self, _timeout):
            pass

        def sendto(self, packet, _addr):
            self.sent = packet

        def recvfrom(self, _size):
            ident = struct.unpack("!H", self.sent[:2])[0]
            question = self.sent[12:]
            name = b"CAMERA" + b" " * 9
            rdata = bytes([1]) + name + b"\x00" + struct.pack("!H", 0) + b"\x00" * 6
            answer = b"\xc0\x0c" + struct.pack("!HHIH", 0x21, 1, 0, len(rdata)) + rdata
            response = struct.pack("!HHHHHH", ident, 0x8500, 1, 1, 0, 0) + question + answer
            return response, ("10.0.0.8", 137)

        def close(self):
            pass

    monkeypatch.setattr(network_module.socket, "socket", FakeSocket)
    assert network_module.netbios_node_name("10.0.0.8") == "CAMERA"
