from __future__ import annotations

from pathlib import Path

from hv_nms.constants import APP_VERSION, PANEL_BG, PANEL_BG_2, WINDOW_BG


ROOT = Path(__file__).resolve().parents[1]


def test_release_version_is_consistent_across_build_files():
    assert APP_VERSION == "v26.10.19.02"
    assert 'APP_VERSION = "v26.10.19.02"' in (ROOT / "src/hv_nms/__init__.py").read_text(encoding="utf-8")
    assert 'VERSION="v26.10.19.02"' in (ROOT / "scripts/build_macos.sh").read_text(encoding="utf-8")
    spec = (ROOT / "HV_P2P_NMS.spec").read_text(encoding="utf-8")
    assert '"CFBundleShortVersionString": "26.10.19.02"' in spec
    assert '"CFBundleVersion": "26.10.19.02"' in spec


def test_workflow_builds_native_apple_silicon_and_intel():
    workflow = (ROOT / ".github/workflows/build-macos.yml").read_text(encoding="utf-8")
    assert "runner: macos-15\n            arch: arm64" in workflow
    assert "runner: macos-15-intel\n            arch: x86_64" in workflow
    assert "actions/upload-artifact@v6" in workflow
    assert "actions/setup-python@v6" in workflow


def test_build_script_rejects_accidental_universal_binary_and_stages_app_in_dmg():
    script = (ROOT / "scripts/build_macos.sh").read_text(encoding="utf-8")
    assert 'test "$ACTUAL_ARCHS" = "$TARGET_ARCH"' in script
    assert 'DMG_ROOT="release/dmg-root"' in script
    assert 'ditto "$APP" "$DMG_ROOT/HV P2P NMS.app"' in script


def test_locked_theme_values_remain_unchanged():
    assert WINDOW_BG.upper() == "#0F1316"
    assert PANEL_BG.upper() == "#171D20"
    assert PANEL_BG_2.upper() == "#161C20"


def test_setup_sidebar_navigation_has_been_removed_only_as_requested():
    source = (ROOT / "src/hv_nms/main_window.py").read_text(encoding="utf-8")
    assert "setup_side_buttons" not in source
    assert "_setup_navigate" not in source
    for panel in (
        'Panel("NETWORK INTERFACE")',
        'Panel("SCAN FREQUENCY")',
        'Panel("SCAN MODE")',
        'Panel("TREND GRAPH WINDOW")',
        'Panel("PING TIMEOUT")',
        'Panel("DISCOVERY RANGE")',
        'Panel("LATENCY THRESHOLDS")',
        'Panel("CONFIG ACTIONS")',
    ):
        assert panel in source


def test_header_favourites_and_status_strip_design_contract_remain_present():
    source = (ROOT / "src/hv_nms/main_window.py").read_text(encoding="utf-8")
    assert 'badge = QLabel("HV P2P\\nNMS")' in source
    assert "HV P2P | NMS" not in source
    assert "self.favourite_tiles = [FavouriteTile(), FavouriteTile(), FavouriteTile()]" in source
    assert "Network Monitor |" in source
    assert "GREEN_BORDER" in source
    assert "RED_BORDER" in source


def test_macos_bundle_declares_local_network_privacy_for_discovery():
    spec = (ROOT / "HV_P2P_NMS.spec").read_text(encoding="utf-8")
    assert '"NSLocalNetworkUsageDescription"' in spec
    assert '"_services._dns-sd._udp"' in spec


def test_source_manifest_matches_release_tree():
    ignored_dirs = {'.git', '.pytest_cache', '__pycache__', 'build', 'dist', 'release'}
    actual = set()
    for path in ROOT.rglob('*'):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        if any(part in ignored_dirs for part in rel.parts):
            continue
        if path.suffix in {'.pyc', '.pyo'} or path.name == '.DS_Store':
            continue
        actual.add(rel.as_posix())
    manifest = {line.strip() for line in (ROOT / 'SOURCE_MANIFEST.txt').read_text(encoding="utf-8").splitlines() if line.strip()}
    assert actual == manifest


def test_windows_x64_job_and_distribution_contract():
    workflow = (ROOT / ".github/workflows/build-macos.yml").read_text(encoding="utf-8")
    assert "runner: macos-15\n            arch: arm64" in workflow
    assert "runner: macos-15-intel\n            arch: x86_64" in workflow
    assert "runs-on: windows-2022" in workflow
    assert 'name: "HV P2P NMS v26.10.19.02 Windows x64"' in workflow
    assert "actions/upload-artifact@v6" in workflow
    assert "scripts/build_windows.ps1" in workflow
    assert "python -m pytest tests -q" in workflow
    win = (ROOT / "scripts/build_windows.ps1").read_text(encoding="utf-8")
    assert "python -m PyInstaller --noconfirm --clean HV_P2P_NMS.spec" in win
    assert "python scripts/verify_pe_arch.py $Executable" in win
    assert "--self-test" in win
    assert "Compress-Archive" in win
    spec = (ROOT / "HV_P2P_NMS.spec").read_text(encoding="utf-8")
    assert 'sys.platform == "darwin"' in spec
    assert 'sys.platform == "win32"' in spec
    assert 'icon=str(WIN_ICON)' in spec
    assert (ROOT / "assets/HV_P2P_NMS.ico").is_file()


def test_release_keeps_approved_single_source_of_truth_theme_and_ui_layout():
    constants = (ROOT / "src/hv_nms/constants.py").read_text(encoding="utf-8")
    for color in ("#0f1316", "#171d20", "#161c20"):
        assert color in constants
    ui = (ROOT / "src/hv_nms/main_window.py").read_text(encoding="utf-8")
    assert 'self.favourite_tiles = [FavouriteTile(), FavouriteTile(), FavouriteTile()]' in ui
    assert 'self.status_strip.setFixedHeight(38)' in ui
    assert 'self._build_run_page()' in ui
    assert 'self._build_setup_page()' in ui
    assert 'self._build_discovery_page()' in ui
    assert 'self._build_log_page()' in ui
    assert 'setup_side_buttons' not in ui


def test_windows_pe_architecture_guard_rejects_wrong_machine(tmp_path):
    import importlib.util
    import struct
    source = ROOT / "scripts/verify_pe_arch.py"
    spec = importlib.util.spec_from_file_location("verify_pe_arch", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for machine in (0x8664, 0x14C, 0xAA64):
        blob = bytearray(128)
        blob[:2] = b"MZ"
        struct.pack_into("<I", blob, 0x3C, 0x50)
        blob[0x50:0x54] = b"PE\x00\x00"
        struct.pack_into("<H", blob, 0x54, machine)
        path = tmp_path / "app.exe"
        path.write_bytes(blob)
        assert module.machine_type(path) == machine
    import pytest
    path.write_bytes(b"not a PE file")
    with pytest.raises(ValueError):
        module.machine_type(path)


def test_release_source_reads_explicit_utf8_on_windows():
    """No release-structure test may depend on the host OS text encoding."""
    import ast

    path = ROOT / "tests/test_release_structure.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "read_text":
            assert any(kw.arg == "encoding" and isinstance(kw.value, ast.Constant)
                       and kw.value.value == "utf-8" for kw in node.keywords), (
                f"File read without encoding at line {node.lineno}"
            )


def test_windows_workflow_enables_utf8_python_mode():
    workflow = (ROOT / ".github/workflows/build-macos.yml").read_text(encoding="utf-8")
    assert 'PYTHONUTF8: "1"' in workflow


def test_distribution_uses_spaced_release_names():
    """Release names must match the approved app title, not module filenames."""
    mac = (ROOT / "scripts/build_macos.sh").read_text(encoding="utf-8")
    win = (ROOT / "scripts/build_windows.ps1").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/build-macos.yml").read_text(encoding="utf-8")
    assert 'HV P2P NMS ${VERSION} macOS ${SUFFIX}' in mac
    assert 'HV P2P NMS ${Version} Windows x64' in win
    assert 'HV P2P NMS v26.10.19.02 macOS Apple Silicon' in workflow
    assert 'HV P2P NMS v26.10.19.02 macOS Intel' in workflow
    assert 'HV P2P NMS v26.10.19.02 Windows x64' in workflow
