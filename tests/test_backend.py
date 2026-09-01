from __future__ import annotations

import json
from concurrent.futures import Future

from hv_nms.backend import MonitorBackend
from hv_nms.config import load_config, save_config
from hv_nms.models import AppSettings, DeviceRecord
from hv_nms.network import discovery_targets


def test_discovery_targets_respects_real_subnet_boundaries():
    targets = discovery_targets("10.2.3.250", "10.2.4.5", "255.255.0.0")
    assert targets[0] == "10.2.3.250"
    assert targets[-1] == "10.2.4.5"
    assert "10.2.4.1" in targets


def test_config_round_trip_preserves_stable_ids_and_favourites(tmp_path):
    a = DeviceRecord("CTRL", "172.20.1.101")
    b = DeviceRecord("W1P", "172.20.1.102")
    settings = AppSettings(favourite_device_ids=[b.device_id, a.device_id, None])
    path = tmp_path / "config.json"
    save_config(path, settings, [a, b])
    loaded_settings, devices = load_config(path)
    assert [d.device_id for d in devices] == [a.device_id, b.device_id]
    assert loaded_settings.normalised_favourites() == [b.device_id, a.device_id, None]


def test_ping_result_is_applied_by_device_id_not_row_index(tmp_path):
    a = DeviceRecord("A", "10.0.0.1")
    b = DeviceRecord("B", "10.0.0.2")
    backend = MonitorBackend(AppSettings(), [a, b], tmp_path / "config.json")
    # Reorder before an in-flight result returns. The result must still update A.
    backend.reorder_device(0, 1)
    future = Future()
    future.set_result(3.25)
    backend._ping_done(a.device_id, future)
    assert a.latency_ms == 3.25
    assert b.latency_ms is None
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


def test_legacy_config_without_device_ids_is_accepted(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({"devices": [{"name": "SRVR", "hostname": "", "ip": "172.20.1.70"}]}))
    settings, devices = load_config(path)
    assert len(devices) == 1
    assert devices[0].device_id
    assert devices[0].name == "SRVR"


def test_design_lock_neutral_palette_matches_srvr_reference():
    from hv_nms.constants import PANEL_BG, PANEL_BG_2, WINDOW_BG
    assert PANEL_BG.upper() == "#171D20"
    assert PANEL_BG_2.upper() == "#161C20"
    assert WINDOW_BG.upper() == "#0F1316"


def test_discovery_interval_round_trip(tmp_path):
    settings = AppSettings(discovery_interval_seconds=15.0)
    path = tmp_path / "config.json"
    save_config(path, settings, [DeviceRecord("A", "10.0.0.1")])
    loaded, _ = load_config(path)
    assert loaded.discovery_interval_seconds == 15.0
