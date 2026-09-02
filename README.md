# HV P2P NMS v26.09.02.05

GitHub-ready source for the redesigned HV P2P Network Management System.

This revision rebuilds the supplied legacy NMS logic into a PySide6 desktop application while keeping the approved NMS visual design locked. It deliberately builds two native macOS applications rather than relying on Rosetta:

- **Apple Silicon / arm64** on GitHub's `macos-15` runner.
- **Intel / x86_64** on GitHub's `macos-15-intel` runner.

The current PySide6 macOS wheel is Universal2 and requires macOS 13 or later, so the application bundle is intentionally configured with `LSMinimumSystemVersion = 13.0`. See `BUILD_DEBUG.md` for the earlier v26.09.01.04 hosted-runner scheduling failure and the macOS CI hardening retained in this revision.

## What GitHub Actions produces

Each successful run creates two Actions artifacts:

- `HV-P2P-NMS-macOS-Apple-Silicon`
- `HV-P2P-NMS-macOS-Intel`

Each artifact contains an architecture-specific `.zip`, a `.dmg`, and SHA-256 checksums. The build script verifies the Mach-O architecture with `lipo`, verifies the app bundle with `codesign`, then runs the compiled executable's `--self-test` before packaging it.

## Exact GitHub upload procedure

1. Create an empty GitHub repository.
2. Upload **the contents of this folder** to the repository root. Do not upload this outer folder as an extra directory level.
3. Commit to the `main` branch.
4. Open **Actions** → **Build macOS Intel + Apple Silicon**.
5. Run the workflow manually, or let the push to `main` start it.
6. When both macOS jobs are green, download the two artifacts from the workflow run.

The workflow is in `.github/workflows/build-macos.yml`.

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

## Runtime behaviour

The Scan Mode worker belongs to the application backend, not the Run page, so an active scan keeps running when Setup, Discovery or Log is selected. Discovery has a separate 15-second default cycle and can run concurrently with Scan Mode.

Three favourite devices are persistent. Select a device on Run and use `Assign to Favourite 1 / 2 / 3`. Each header tile is restricted to the approved fields: health dot, Device, IP Address, Latency and compact latency trend.

`nmap` is optional. Discovery streams cached ARP and successful ping results first and never waits for name resolution before showing a device. Name discovery now combines the hostname exposed by the operating-system `ping` resolver, human-readable ARP entries, conventional reverse DNS, active mDNS reverse lookup, Bonjour/DNS-SD service browsing, direct NetBIOS node-status lookup, optional nmap reverse resolution, and optional command-line resolver helpers. Finder-launched macOS apps also search the standard Homebrew/MacPorts locations so an installed `nmap` is not lost just because the GUI process has a minimal `PATH`.

Bonjour/DNS-SD service instances are used as the friendly **Device** label when available, while the service's SRV target is used as the **Host Name**. This is intentionally separate: a device can advertise a friendly name even when its conventional DNS PTR record is empty. Resolver failures such as `NXDOMAIN`, `SERVFAIL` and `REFUSED` are treated as lookup failures and are never displayed as device or host names. Unnamed hosts are retried during later discovery passes after the ARP/mDNS caches have had time to populate.

On macOS, the app bundle declares `NSLocalNetworkUsageDescription` and Bonjour usage. On first use, allow **HV P2P NMS** access to the local network when macOS asks. If access was previously denied, enable it in **System Settings → Privacy & Security → Local Network**. The discovery code retries, because macOS can reject the first local-network operation while the permission prompt is still awaiting a response.

There is no standards-compliant way to derive a hostname from an IP address when a device publishes no DNS PTR/mDNS/Bonjour/NetBIOS identity. In that case NMS deliberately leaves Host Name as `—` instead of inventing a value.

Application configuration is stored outside the `.app` bundle under:

`~/Library/Application Support/HV P2P NMS/config.json`

The app can still load/export JSON configuration files from arbitrary locations.

## Signing / Gatekeeper

The GitHub workflow produces an ad-hoc signed application, which is sufficient for architecture and bundle-integrity testing. It is **not Apple Developer ID notarization**. For unrestricted external distribution through Gatekeeper, add Developer ID signing and Apple notarization credentials as a later release step. No certificate or secret is embedded in this repository.

## Setup

The Setup page intentionally uses one consolidated settings page. The former General / Network / Scan / Discovery / Threshold / Config sidebar navigation was removed because all controls fit cleanly on one screen.

## Design lock

See `DESIGN_LOCK.md` and `design-reference/`. The neutral greys are sampled from the supplied SRVR reference, and the Run/Setup/Discovery layouts are the approved NMS layouts. Do not redesign those screens as part of build maintenance.
