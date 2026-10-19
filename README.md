# HV P2P NMS v26.10.19.02

GitHub-ready source for the redesigned HV P2P Network Management System.

This release is based on the uploaded `v26.09.03.01` source and preserves the
approved Run / Setup / Discovery / Log layout and SRVR-sampled charcoal colour
palette. The current release adds native Windows x64 packaging alongside the
existing separate Mac builds and fixes a Discovery state regression where an
identity-only update could override a successful ping status.

## GitHub Actions outputs — three separate native builds

The workflow at `.github/workflows/build-macos.yml` builds and verifies:

| Job | Native runner | Downloaded artifact | Contents |
| --- | --- | --- | --- |
| macOS Apple Silicon | `macos-15` / arm64 | `HV P2P NMS v26.10.19.02 macOS Apple Silicon` | Native `.app` as `.zip` and `.dmg`, SHA-256 |
| macOS Intel | `macos-15-intel` / x86_64 | `HV P2P NMS v26.10.19.02 macOS Intel` | Native `.app` as `.zip` and `.dmg`, SHA-256 |
| Windows x64 | `windows-2022` / AMD64 | `HV P2P NMS v26.10.19.02 Windows x64` | Portable standalone `.exe`, zipped `.exe`, SHA-256 |

A fourth job runs regression tests on Ubuntu before the platform builds begin.
Each platform reruns tests, imports the actual PySide6 UI and runs the compiled
application's own `--self-test`. Mac builds additionally verify `lipo`-reported
architecture and ad-hoc bundle signature. Windows checks the executable's PE
machine type, rejecting anything other than `0x8664` (native AMD64).

## Upload to GitHub

1. Extract the provided `HV P2P NMS v26.10.19.02 GitHub.zip`.
2. Upload **all contents** of the extracted top-level folder, including the
   `.github` directory, to the root of the existing GitHub repository. Replace
   existing files rather than nesting inside another folder.
3. Commit to `main` or launch **Actions → Build HV P2P NMS - macOS Intel,
   Apple Silicon and Windows → Run workflow**.
4. Wait for **all four jobs** to pass and download the **three separate build
   artifacts** from the workflow run.

GitHub Actions executes native builds. No macOS or Windows binaries are
precompiled in this source ZIP. Windows and macOS distribution packages are
unsigned by a commercial code-signing certificate: macOS uses ad-hoc signing,
and Windows may show SmartScreen warnings until code-signing is configured.

## Local development

Python 3.12 is the pinned development/runtime version for the build pipeline.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-build.txt
PYTHONPATH=src python -m pytest tests -q
PYTHONPATH=src python run_hv_nms.py
```

On macOS, a local package build is:

```bash
bash scripts/make_icns.sh
TARGET_ARCH="$(uname -m)" bash scripts/build_macos.sh
```

On Windows PowerShell, a local package build is:

```powershell
py -3.12 -m pip install -r requirements-build.txt
./scripts/build_windows.ps1
```

## Runtime behaviour

The Scan Mode worker belongs to the application backend, not the Run page, so an active scan keeps running when Setup, Discovery or Log is selected. Discovery has a separate 15-second default cycle and can run concurrently with Scan Mode.

Three favourite devices are persistent. Select a device on Run and use `Assign to Favourite 1 / 2 / 3`. Each header tile is restricted to the approved fields: health dot, Device, IP Address, Latency and compact latency trend.

`nmap` is optional. Discovery streams cached ARP and successful ping results first and never waits for name resolution before showing a device. Name discovery now combines the hostname exposed by the operating-system `ping` resolver, human-readable ARP entries, conventional reverse DNS, active mDNS reverse lookup, Bonjour/DNS-SD service browsing, direct NetBIOS node-status lookup, optional nmap reverse resolution, and optional command-line resolver helpers. Finder-launched macOS apps also search the standard Homebrew/MacPorts locations so an installed `nmap` is not lost just because the GUI process has a minimal `PATH`.

Bonjour/DNS-SD service instances are used as the friendly **Device** label when available, while the service's SRV target is used as the **Host Name**. This is intentionally separate: a device can advertise a friendly name even when its conventional DNS PTR record is empty. Resolver failures such as `NXDOMAIN`, `SERVFAIL` and `REFUSED` are treated as lookup failures and are never displayed as device or host names. Unnamed hosts are retried during later discovery passes after the ARP/mDNS caches have had time to populate.

On macOS, the app bundle declares `NSLocalNetworkUsageDescription` and Bonjour usage. On first use, allow **HV P2P NMS** access to the local network when macOS asks. If access was previously denied, enable it in **System Settings → Privacy & Security → Local Network**. The discovery code retries, because macOS can reject the first local-network operation while the permission prompt is still awaiting a response.

There is no standards-compliant way to derive a hostname from an IP address when a device publishes no DNS PTR/mDNS/Bonjour/NetBIOS identity. In that case NMS deliberately leaves Host Name as `—` instead of inventing a value.

Application configuration is stored outside the installed application:

- macOS: `~/Library/Application Support/HV P2P NMS/config.json`
- Windows: `%APPDATA%\HV P2P NMS\config.json`

The app can still load/export JSON configuration files from arbitrary locations.

## Signing / Gatekeeper

The GitHub macOS workflow produces an ad-hoc signed application for architecture and bundle-integrity testing. It is **not Apple Developer ID notarization**. The Windows EXE is not Authenticode-signed. For unrestricted external distribution through Gatekeeper, add Developer ID signing and Apple notarization credentials as a later release step. No certificate or secret is embedded in this repository.

## Setup

The Setup page intentionally uses one consolidated settings page. The former General / Network / Scan / Discovery / Threshold / Config sidebar navigation was removed because all controls fit cleanly on one screen.

## Design lock

See `DESIGN_LOCK.md` and `design-reference/`. The neutral greys are sampled from the supplied SRVR reference, and the Run/Setup/Discovery layouts are the approved NMS layouts. Do not redesign those screens as part of build maintenance.

## v26.10.19.02 — Windows test/packaging naming hotfix

No UI, networking, scan-mode, discovery, favourite-device or configuration
behaviour was changed from v26.10.19.01.

- Fixed Windows GitHub tests that called `Path.read_text()` without an
  encoding. Source/test fixtures are UTF-8, but the hosted PowerShell runner
  used Windows CP1252 and failed with `UnicodeDecodeError` before assertions.
- Enabled `PYTHONUTF8=1` in the Windows GitHub Actions job as defence in depth.
- Added regression tests that fail if release-structure tests use implicit
  text decoding or if the release artifacts regress to underscored names.
- Release ZIP root directory, ZIP/DMG/EXE filenames and downloadable GitHub
  artifact names now use the approved `HV P2P NMS vYY.MM.DD.RR` prefix.
- Internal Python package (`hv_nms`), script paths and icon/spec filenames
  retain their stable technical identifiers because they are not displayed
  as application/release names.

Native Windows/macOS bundling still requires GitHub Actions to execute the
three platform-specific builds. An unsigned executable may trigger OS security
protections depending on local policy.
