# HV P2P NMS design lock — v26.09.02.05

The files in `design-reference/` are the locked visual references for this build. Do not redesign, reflow, recolour or substitute SRVR content unless a future change request explicitly asks for it.

## Locked NMS shell

- `HV P2P | NMS` heading text is removed. The left badge remains `HV P2P / NMS`.
- Three favourite-device tiles remain across the header and are visible on every page.
- A favourite tile contains only: health dot, Device, IP Address, Latency, and a compact Latency Trend Graph.
- Favourite slots 1 / 2 / 3 are assigned from the selected-device panel on Run.
- Scan Mode is application-level, not Run-page-level. If active, it continues while Setup, Discovery or Log is open.
- The global Network Monitor strip is green when Scan Mode is active and red when Scan Mode is inactive. On non-Run pages it explicitly says when Scan Mode is active in the background.
- Discovery can run at the same time as background Scan Mode.
- The Run-page Event Log is its own layout region and must never overlap or be clipped by the Tools sidebar.
- Approved Setup exception: the left General / Network / Scan / Discovery / Threshold / Config navigation column is removed. All existing Setup controls remain together on the single settings page; no other Setup redesign is approved.

## Locked theme

The neutral palette was sampled from the supplied SRVR reference rather than approximated from the earlier NMS concept. Primary panel grey is `#171D20`; surrounding background is `#0F1316`; secondary grey is `#161C20`. Cyan/green/red are reserved for headings and state/health information.

The compiled version string is `v26.09.02.05`; the design-reference images contain earlier concept version labels and are references for layout/appearance only.
