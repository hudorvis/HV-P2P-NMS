# GitHub Actions build debug — v26.10.19.02
## v26.10.19.02 CI manifest fix

GitHub Actions checks the repository out with a `.git/` metadata directory. The release-manifest regression test previously walked the entire repository root but did not exclude `.git/`, so it passed in the clean release ZIP yet failed on every normal GitHub checkout by treating Git internals as unexpected release files. The test now explicitly excludes `.git/` while continuing to compare every real release file against `SOURCE_MANIFEST.txt`. No application runtime, discovery, UI, or build-architecture behaviour was changed by this fix.


The first `v26.09.01.04` GitHub run produced a valid Intel artifact, while the Apple Silicon job never executed a workflow step. GitHub reported:

- `The job was not acquired by Runner of type hosted even after multiple attempts`
- `Internal server error` with a GitHub correlation ID

That failure occurs in GitHub's hosted-runner scheduler before repository code starts, so there is no application stack trace or shell log to debug from that job.

The CI hardening introduced after that failure and retained in this revision is:

1. moves Apple Silicon from `macos-26` to the mature native ARM64 `macos-15` standard pool;
2. moves Intel from `macos-26-intel` to `macos-15-intel` for a consistent runner generation;
3. updates `actions/checkout` to v6 and `actions/upload-artifact` to v6 so the workflow uses Node.js 24-native actions and removes the `upload-artifact@v4` Node.js 20 warning;
4. keeps the explicit `platform.machine()` and `lipo` architecture checks, so a mis-routed runner cannot silently produce the wrong build;
5. adds a 45-minute execution timeout after a runner is acquired.

Later NMS revisions also improve Discovery responsiveness/hostname resolution and simplify Setup, but those application changes are independent of the hosted-runner acquisition failure described above.

If GitHub again reports a hosted-runner acquisition error and there are no step logs, re-run the failed job. That class of failure is upstream of the repository and cannot be retried from inside a job because no runner has started executing the workflow yet.


## v26.10.19.02 discovery identity hardening

The v26.09.02.04 build correctly stopped displaying resolver errors such as `NXDOMAIN`, but a production LAN with no conventional reverse-DNS PTR records could still show IP-only Discovery rows. v26.09.02.05 keeps the fast ping/ARP stream and adds independent identity sources: ping-banner resolution, active mDNS reverse PTRs, Bonjour/DNS-SD service instance + SRV/A resolution, direct NetBIOS NBSTAT, repeat lookup attempts after cache warm-up, and explicit Homebrew/MacPorts executable lookup for GUI-launched macOS apps. The macOS bundle also now declares Local Network privacy usage so Bonjour/mDNS access can be granted by the user.

## v26.10.19.02 multi-platform build

The original macOS-only GitHub workflow now runs native builds on `macos-15`
(arm64), `macos-15-intel` (x86_64), and `windows-2022` (AMD64). The PyInstaller
spec selects a macOS `.app` BUNDLE on macOS or onefile `.exe` on Windows.
The Windows workflow verifies the PE COFF machine type (`0x8664`), runs the
frozen EXE `--self-test`, and packages .exe/.zip and checksum. Current sources
were validated on Linux; macOS/Windows native bundling must pass their
respective GitHub runners before these can be called release-tested binaries.

## v26.10.19.02 Windows GitHub CI failure

Windows PowerShell runner defaulted to CP1252 when `tests/test_release_structure.py`
called `Path.read_text()` without specifying an encoding. The UTF-8 UI source
contained byte `0x8f` that could not be decoded with CP1252; three design-lock
regression tests failed before reaching any assertion. All 3 failing reads
now explicitly use UTF-8, as do all other reads in that test module.
The Windows build job additionally sets `PYTHONUTF8=1`. This is a test-host
encoding failure, not evidence of a Qt import or application build failure.
