# Legacy HV NMS functionality carried forward

This build is based on the supplied legacy `HV NMS v26.06.19.03` behaviour, with the UI rebuilt in PySide6 and the scanner made safer for long-running use.

Carried forward: configurable device list; add/edit/remove; drag row reordering; scan start/stop; 0.25/0.5/1/2/5 second scan frequencies; All-at-Once and One-by-One scan modes; per-device latency history; 5/15/30 minute and 1/2 hour trend windows; green/orange/red latency thresholds; last-failed tracking; interface selection; Discovery Start IP / End IP / subnet; ping + optional nmap + ARP discovery; hostname resolution; adding discovered devices to Scan Mode; load/save JSON configuration.

New/strengthened: stable device IDs prevent an in-flight ping result from being applied to the wrong row after reordering; bounded workers prevent unlimited ping-thread growth; duplicate in-flight scans of a device are suppressed; real subnet math is used instead of assuming /24; app configuration is stored under the user's application-support/config directory rather than inside the `.app`; three persistent favourite devices update in the global header; scanning remains active across tabs; event logging is centralised; Discovery and Scan can operate concurrently.
