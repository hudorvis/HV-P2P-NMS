#!/usr/bin/env python3
"""Validate 3 native artifacts, publish an all-platform installer bundle and direct Release assets.

Platform build artifacts are downloaded by actions/download-artifact into subfolders
named after each artifact. This script fails closed if any platform or checksum is
missing/invalid; it never silently emits an incomplete combined ZIP.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import shutil
import zipfile

PLATFORMS = {
    "macOS Apple Silicon": {"installer": ".dmg", "required": (".dmg", ".zip", ".txt")},
    "macOS Intel": {"installer": ".dmg", "required": (".dmg", ".zip", ".txt")},
    "Windows x64": {"installer": ".exe", "required": (".exe", ".zip", ".txt")},
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_checksums(folder: Path, required_files: list[Path]) -> None:
    records = sorted(folder.glob('SHA256_*.txt'))
    if len(records) != 1:
        raise ValueError(f"Expected exactly one platform checksum file in {folder}, got {records}")
    seen: dict[str, str] = {}
    for raw in records[0].read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        fields = line.split(maxsplit=1)
        if len(fields) != 2 or len(fields[0]) != 64:
            raise ValueError(f"Malformed checksum entry in {records[0]}: {line!r}")
        expected, filename = fields
        filename = filename.lstrip("* ").strip()
        if not filename or Path(filename).name != filename or filename in seen:
            raise ValueError(f"Invalid checksum filename: {filename!r}")
        seen[filename] = expected.lower()
    for path in required_files:
        if path.stat().st_size == 0:
            raise ValueError(f"Empty native build artifact: {path}")
        expected = seen.get(path.name)
        if expected is None:
            raise ValueError(f"Missing SHA256 for {path}")
        if sha256(path) != expected:
            raise ValueError(f"SHA256 mismatch for {path}")


def build_package(input_dir: Path, output_dir: Path, version: str) -> tuple[Path, list[Path]]:
    """Returns the complete ZIP and public assets. No partial output on validation failures."""
    expected_prefix = f"HV P2P NMS {version} "
    selected: list[tuple[str, Path]] = []
    for platform, config in PLATFORMS.items():
        folder = input_dir / f"{expected_prefix}{platform}"
        if not folder.is_dir():
            raise FileNotFoundError(f"Required compiled artifact missing: {folder}")
        files = [p for p in folder.iterdir() if p.is_file()]
        for suffix in config["required"]:
            if not any(p.suffix.lower() == suffix for p in files):
                raise FileNotFoundError(f"Missing {suffix} in {folder}")
        installers = [p for p in files if p.suffix.lower() == config["installer"]]
        if len(installers) != 1:
            raise ValueError(f"Expected exactly 1 installer for {platform}, got {installers}")
        payloads = [p for p in files if p.suffix.lower() in ('.dmg', '.zip', '.exe')]
        verify_checksums(folder, payloads)
        selected.append((platform, installers[0]))

    output_dir.mkdir(parents=True, exist_ok=True)
    combined_path = output_dir / f"HV P2P NMS {version} All Platforms.zip"
    readme = (
        f"HV P2P NMS {version} — All Platforms\\n\\n"
        "macOS Apple Silicon/ : install the DMG on an Apple Silicon Mac (M1/M2/M3/M4/etc.)\\n"
        "macOS Intel/         : install the DMG on an Intel Mac\\n"
        "Windows x64/        : run the portable EXE on 64-bit Windows\\n\\n"
        "All installers are built on native GitHub runners and checked before bundling.\\n"
        "macOS apps are ad-hoc signed, not Developer ID notarized. Windows is unsigned.\\n"
        "If your computer blocks an unsigned application, follow your organization's security policy.\\n"
    ).replace('\\n','\n')
    hashes = []
    with zipfile.ZipFile(combined_path, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        archive.writestr('READ ME FIRST.txt', readme)
        for platform, installer in selected:
            dest = f"{platform}/{installer.name}"
            archive.write(installer, dest)
            hashes.append(f"{sha256(installer)}  {dest}")
        archive.writestr('SHA256SUMS.txt', '\n'.join(hashes) + '\n')

    with zipfile.ZipFile(combined_path) as archive:
        if archive.testzip() is not None:
            raise ValueError('ZIP integrity check failed')
        actual_names = set(archive.namelist())
        assert len(actual_names) == 5, f'Unexpected combined ZIP entries: {actual_names}'

    assets = [combined_path]
    for _, installer in selected:
        destination = output_dir / installer.name
        if destination == installer:
            raise ValueError('Input directory and output directory must be separate')
        if destination.exists():
            raise ValueError(f'Conflicting release filename {destination}')
        shutil.copyfile(installer, destination)
        assets.append(destination)

    sums = output_dir / 'SHA256SUMS.txt'
    sums.write_text('\n'.join(f"{sha256(item)}  {item.name}" for item in assets) + '\n', encoding='utf-8')
    assets.append(sums)
    return combined_path, assets


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--version', default='v26.10.19.03')
    args = parser.parse_args()
    bundle, assets = build_package(args.input, args.output, args.version)
    print(f'Combined all-platforms ZIP: {bundle} ({bundle.stat().st_size:,} bytes)')
    for asset in assets:
        print(f'  {asset.name}: {asset.stat().st_size:,} bytes')


if __name__ == '__main__':
    main()
