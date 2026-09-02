from __future__ import annotations

import platform
import sys


def self_test() -> int:
    # Import the actual Qt UI modules as part of the packaged self-test. This
    # catches missing PySide6/Qt bundle content that a backend-only self-test
    # would not detect. No QApplication/window is created.
    import PySide6
    from .constants import APP_VERSION
    from .main_window import MainWindow  # noqa: F401
    from .models import AppSettings, DeviceRecord
    from .network import discovery_targets

    settings = AppSettings()
    device = DeviceRecord("SELFTEST", "127.0.0.1")
    device.add_sample(0.1)
    targets = discovery_targets("172.20.1.1", "172.20.1.3", "255.255.255.0")
    assert targets == ["172.20.1.1", "172.20.1.2", "172.20.1.3"]
    assert settings.normalised_favourites() == [None, None, None]
    print(f"HV P2P NMS {APP_VERSION} self-test PASS ({platform.machine()}, PySide6 {PySide6.__version__})")
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()

    from PySide6.QtWidgets import QApplication
    from .backend import MonitorBackend
    from .config import load_or_create_default
    from .main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("HV P2P NMS")
    app.setOrganizationName("Hudor Visual")
    settings, devices, config_path = load_or_create_default()
    backend = MonitorBackend(settings, devices, config_path)
    window = MainWindow(backend)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
