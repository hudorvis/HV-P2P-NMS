"""Release aggregation and its GitHub workflow contracts (no platform binaries needed)."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_SCRIPT = ROOT / 'scripts/package_all_platforms.py'

spec = importlib.util.spec_from_file_location('package_all_platforms', PACKAGE_SCRIPT)
assert spec is not None and spec.loader is not None
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)

VERSION = 'v26.10.19.03'


def sample_artifacts(root: Path):
    for name, suffixes in {
        'macOS Apple Silicon': ('.dmg', '.zip'),
        'macOS Intel': ('.dmg', '.zip'),
        'Windows x64': ('.exe', '.zip'),
    }.items():
        folder = root / f'HV P2P NMS {VERSION} {name}'
        folder.mkdir(parents=True, exist_ok=True)
        hashes = []
        for suffix in suffixes:
            file = folder / f'HV P2P NMS {VERSION} {name}{suffix}'
            file.write_bytes(f'Fake native artifact for unit tests: {name} {suffix}'.encode())
            hashes.append(f'{hashlib.sha256(file.read_bytes()).hexdigest()}  {file.name}')
        (folder / 'SHA256_verified.txt').write_text('\n'.join(hashes) + '\n', encoding='utf-8')


def test_all_platforms_bundle_has_three_installers_and_checksums(tmp_path):
    incoming = tmp_path / 'incoming'
    sample_artifacts(incoming)
    archive, assets = package.build_package(incoming, tmp_path / 'released', VERSION)
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        names = z.namelist()
        assert sum(name.endswith('.dmg') for name in names) == 2
        assert sum(name.endswith('.exe') for name in names) == 1
        assert len(names) == 5
        assert 'READ ME FIRST.txt' in names
        assert 'SHA256SUMS.txt' in names
        assert len(z.read('SHA256SUMS.txt').decode().splitlines()) == 3
    assert sum(asset.suffix == '.dmg' for asset in assets) == 2
    assert sum(asset.suffix == '.exe' for asset in assets) == 1
    assert sum(asset.suffix == '.zip' for asset in assets) == 1
    assert len(assets) == 5  # Three installers, all-platform ZIP, checksum manifest.


def test_all_platforms_throws_if_native_platform_missing(tmp_path):
    incoming = tmp_path / 'incoming'
    sample_artifacts(incoming)
    import shutil
    shutil.rmtree(incoming / f'HV P2P NMS {VERSION} macOS Intel')
    with pytest.raises(FileNotFoundError, match='macOS Intel'):
        package.build_package(incoming, tmp_path / 'released', VERSION)
    assert not (tmp_path / 'released').exists()


def test_all_platforms_rejects_tampered_native_installers(tmp_path):
    incoming = tmp_path / 'incoming'
    sample_artifacts(incoming)
    suspect = next((incoming / f'HV P2P NMS {VERSION} Windows x64').glob('*.exe'))
    suspect.write_bytes(b'TAMPERED')
    with pytest.raises(ValueError, match='SHA256 mismatch'):
        package.build_package(incoming, tmp_path / 'released', VERSION)
    assert not (tmp_path / 'released').exists()


def test_workflow_offers_combined_download_and_direct_github_release():
    workflow = (ROOT / '.github/workflows/build-macos.yml').read_text(encoding='utf-8')
    assert 'package-all:' in workflow
    assert 'needs: [build-macos, build-windows]' in workflow
    assert 'actions/download-artifact@v6' in workflow
    assert 'merge-multiple: false' in workflow
    assert 'scripts/package_all_platforms.py' in workflow
    assert 'All Platforms.zip' in workflow
    assert 'publish-release:' in workflow
    assert 'needs: package-all' in workflow
    assert 'contents: write' in workflow
    assert 'gh release create' in workflow
    assert '--target "$GITHUB_SHA"' in workflow
    assert 'native-assets/*.dmg native-assets/*.exe' in workflow


def test_published_release_names_have_spaces_not_underscores():
    workflow = (ROOT / '.github/workflows/build-macos.yml').read_text(encoding='utf-8')
    assert 'HV P2P NMS v26.10.19.03 All Platforms' in workflow
    assert 'HV P2P NMS v26.10.19.03 Direct Installers' in workflow
    assert 'HV_P2P_NMS_v26.10.19.03' not in workflow
