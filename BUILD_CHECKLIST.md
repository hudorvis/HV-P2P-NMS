# Release checklist — v26.10.19.03

- [x] Legacy Python source reviewed and core monitoring/discovery/config behaviour mapped.
- [x] Stable device IDs used instead of row-index result routing.
- [x] Per-device in-flight suppression prevents overlapping ping jobs.
- [x] Bounded scan worker pool used instead of unbounded thread creation.
- [x] Real IPv4 subnet math used for discovery range/network scope.
- [x] Scan Mode continues independently of selected UI tab.
- [x] Discovery uses an independent interval and can run with background Scan Mode.
- [x] Three persistent favourite header tiles are assigned from Run.
- [x] Favourite tiles contain only Device/IP/Latency/compact trend plus health dot.
- [x] Run Event Log is a separate layout column and cannot be covered by Tools.
- [x] Global status strip state is tied to the real Scan Mode state: green active, red inactive.
- [x] Theme constants centralised and neutral greys sampled from SRVR reference.
- [x] Intel and Apple Silicon GitHub runner jobs are explicit and architecture-checked.
- [x] Compiled executable self-test is run before artifacts are uploaded.
- [x] Discovery streams early ARP/ping results instead of waiting for optional nmap.
- [x] Hostname resolution runs asynchronously and can update a discovered row after first display.
- [x] Discovery reuses the OS ping banner hostname from the same ICMP probe instead of discarding it.
- [x] Active mDNS reverse and Bonjour/DNS-SD service discovery can map friendly Device names and SRV host names without a conventional DNS PTR zone.
- [x] Direct NetBIOS node-status fallback works without requiring nmblookup/nbtscan to be installed.
- [x] Finder/minimal-PATH executable lookup checks Apple Silicon Homebrew, Intel Homebrew, MacPorts and system paths for nmap/resolver helpers.
- [x] macOS bundle declares Local Network privacy usage and Bonjour service usage.
- [x] Unnamed hosts are retried after cache warm-up rather than being permanently marked as attempted for the whole discovery session.
- [x] Discovery target limits are enforced before allocating large address ranges.
- [x] Setup sidebar navigation removed; all settings remain on one consolidated page.
- [x] Stale in-flight ping results are rejected after IP/config changes.
- [x] Backend/release regression suite passes locally: 39/39 tests.
- [x] SOURCE_MANIFEST regression test excludes Git checkout metadata (`.git/`) and passes inside a simulated Git repository.

- [x] DNS resolver status strings (including NXDOMAIN/SERVFAIL/REFUSED) are rejected at parser, resolver and model-write boundaries.

## v26.10.19.03 additions

- [x] Start from the user-uploaded `v26.09.03.01` source archive.
- [x] Preserve all approved UI source and SRVR neutral greys.
- [x] Prevent identity-only Discovery updates from downgrading a live ping.
- [x] Add native Windows x64 PE onefile build and executable/ZIP/checksums.
- [x] Add Windows runner and tests/self-test to GitHub Actions.
- [x] Keep separate macOS Apple Silicon and Intel architecture checks and packages.
- [ ] Confirm GitHub's three platform jobs complete successfully (requires hosted native runners).

## v26.10.19.03 fixes

- [x] UTF-8 explicit source reads in the release-structure tests.
- [x] Windows job sets `PYTHONUTF8=1` to avoid CP1252 defaults.
- [x] Spaced user-facing release package, extracted directory and GitHub artifact names.
- [x] Scan/discovery/favourites/layout functional sources remain unmodified.
- [ ] GitHub native runners build/install/launch Windows, Intel and Apple Silicon executables.

## v26.10.19.03 distribution-only delta

- All 3 native platform jobs must complete before the aggregate job runs.
- The aggregator checks platform checksum manifests for both Mac builds and Windows.
- All Platforms ZIP contains two native DMGs and one native Windows EXE, with checksums.
- A distinct Actions artifact stores the complete combined ZIP; a second stores direct installers.
- A GitHub Release publishes direct downloads for users whose browser cannot complete
  the GitHub Actions artifact redirect download.
- Existing app/backend/theme functionality is deliberately unchanged.
