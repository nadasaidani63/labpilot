# LabPilot camera: what it checks and how to tune it

The camera is one witness next to the commanded steps and the scale. It never measures volume.

| Check | What it does | Result in the log |
|---|---|---|
| Image guard | Looks at the backlight level, clipping and sharpness before every judgement. If the image is bad the camera does not vote and the run continues on the scale alone. | `camera = UNRELIABLE` |
| Container position | The container is a dark silhouette on the backlight. The software finds it, and measures its centre against the target spot (px, or mm if `CONTAINER_MM_PER_PX` is set). | `container_offset_px`, `MISPLACED` when outside `CONTAINER_TOL_PX` |
| Plunger tracking | A tape marker on the pump's moving block is tracked. Travel (px) is compared with the commanded volume. | `plunger_travel_px`, `PLUNGER_STILL`, `PLUNGER_SLIP` |
| Evidence photo | `logs/run_..._<sample>.jpg` shows the boxes, offset line, marker path, image status and the verdict. The Camera tab shows the same overlay live. | `snapshot` |

Decision rules (decision.py): a plunger that did not move with no mass gained is `MOTOR_STALL`; a plunger that moved with no mass gained is `NO_FLOW`; a plunger that moved clearly less than commanded is `PLUNGER_SLIP` (checked before `MISMATCH`, because it explains it). If the camera is unreliable or off, none of the camera rules apply.

## Setup order (Camera tab)
1. Backlight on, container holder empty. Press **Check the empty view**. It must say the image is OK and that nothing is in the container area. Read `light` and `sharp` on the overlay and compare with the Settings under *Image guard*.
2. Put the container exactly where it should be. Press **Set target here**. The yellow cross is the target, the yellow circle is the tolerance (`CONTAINER_TOL_PX`).
3. Move the container by hand: the box turns red and the offset appears. Press **Test container check** to see the numbers.
4. Stick a high-contrast tape on the pump's moving block (white on dark, or set `PLUNGER_MARKER = dark`). The orange box (`PLUNGER_ROI`) must be larger than the whole travel of the marker and must not cut it. You should see an orange ring on the marker.
5. Hardware tab: enter a volume, tick a reference balance if you have one, press **Calibrate STEPS_PER_ML** (a test dose). The plunger travel is measured during the same dose; press **Apply plunger px/mL**. Until `PLUNGER_PX_PER_ML` is set, only "moved / did not move" is checked.
6. Optional: set `CONTAINER_MM_PER_PX` = real container width in mm / its width in px on the overlay.

## If something is wrong
* `UNRELIABLE (too dark / overexposed / blurry)`: fix lamp, exposure or focus first. Adjust the guard thresholds only if the image looks fine to you.
* Container "not seen" although it is there: raise `CONTAINER_DARK_FRAC` (a translucent container is less dark and needs a higher fraction), or check `CONTAINER_ROI`.
* Container touching the edge of the blue box counts as misplaced: widen `CONTAINER_ROI`.
* No orange ring: marker not found. Check contrast (`PLUNGER_MIN_CONTRAST`), polarity (`PLUNGER_MARKER`) and that the whole marker is inside `PLUNGER_ROI`. Without a marker the check falls back to plain frame difference.
* Simulation faults for the demo: `none, no_container, misplaced, dark, no_flow, stall, slip, overfill`.

Tests (no hardware): `python3 -m unittest discover -s tests -v`
