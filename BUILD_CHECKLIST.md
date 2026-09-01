# Release checklist — v26.09.01.04

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
- [x] Backend regression tests pass locally in the supplied source environment.
