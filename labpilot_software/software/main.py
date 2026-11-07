"""LabPilot controller (Raspberry Pi side).
Run on the bench:   python3 main.py --protocol protocols/P-001.csv --port /dev/ttyACM0
Run without hardware: python3 main.py --protocol protocols/P-001.csv --mock --faults none,no_container,no_flow
"""
import argparse, os, sys, time
from datetime import datetime
import config
from protocol import load_protocol
from link import SerialLink, MockLink, LinkError
from vision import Vision, MockVision
from decision import decide
from logger import RunLogger

def settle(link, tol=0.05, need=3, timeout=5.0):
    """Wait until the mass stops changing (drops fall after the pump stops)."""
    vals, end = [], time.time() + timeout
    while time.time() < end:
        vals = (vals + [link.read_mass()])[-need:]
        if len(vals) == need and max(vals) - min(vals) <= tol:
            break
        time.sleep(config.SETTLE_INTERVAL_S)
    return vals[-1]

def run_sample(step, link, vision, log, on_progress=None):
    rec = dict(t=datetime.now().isoformat(timespec="seconds"), protocol_id=step.protocol_id,
               sample_id=step.sample_id, target_mL=step.target_ml, tolerance_mL=step.tolerance_ml,
               commanded_mL=None, weighed_mL=None, camera="-", container_offset_px=None,
               plunger_travel_px=None, status="ABORTED", error="none")
    off = getattr(vision, "disabled", False)
    def on_sample(t, m, s):
        vision.tick()
        if on_progress:
            on_progress(t, m, s)
    try:
        link.clear_tare()                                           # a new container: read its absolute mass
        if link.read_mass() < config.CONTAINER_MIN_G:               # scale witness
            rec["error"] = "NO_CONTAINER"
        elif off:
            rec["camera"] = "OFF"
        else:                                                       # camera witness: position, with a number
            cc = vision.check_container()
            if not cc.reliable:                                     # bad image: run on the scale alone
                rec["camera"] = "UNRELIABLE"
                print(f"  camera unreliable ({cc.quality.reason}): continuing on the scale alone")
            elif not cc.present or not cc.in_position:
                rec["error"], rec["camera"] = "MISPLACED", "FAIL"
                if cc.present:
                    rec["container_offset_px"] = round(cc.offset_px, 1)
                print(f"  container {cc.summary()}")
            else:
                rec["camera"] = "OK"
                rec["container_offset_px"] = round(cc.offset_px, 1)
        if rec["error"] == "none":
            link.tare()
            vision.start_watch()
            res = link.dose(step.target_ml * config.DENSITY_G_PER_ML,
                            int(step.target_ml * config.STEPS_PER_ML * config.MAX_STEPS_FACTOR),
                            on_sample=on_sample)
            commanded = res.steps / config.STEPS_PER_ML
            plunger = vision.plunger_result(commanded)
            if not off:
                if not plunger.reliable:
                    rec["camera"] = "UNRELIABLE"
                else:
                    rec["plunger_travel_px"] = round(plunger.travel_px, 1)
                    if not plunger.moved:
                        rec["camera"] = "PLUNGER_STILL"
                    elif plunger.slip:
                        rec["camera"] = "SLIP"
            weighed = settle(link) / config.DENSITY_G_PER_ML
            rec["commanded_mL"], rec["weighed_mL"] = round(commanded, 2), round(weighed, 2)
            rec["status"], rec["error"] = decide(step, res.status, commanded, weighed, plunger)
    except LinkError as e:
        link.stop()
        rec["error"] = "LINK_ERROR"
        print("  link problem:", e)
    shown = "ACCEPTED" if rec["status"] == "ACCEPTED" else rec["error"]
    try:                                                            # annotated evidence photo for the record
        path = f"{log.base}_{step.sample_id}.jpg"
        if vision.snapshot(path, f'{step.sample_id}: {shown}', rec["status"] == "ACCEPTED"):
            rec["snapshot"] = os.path.basename(path)
    except Exception:
        pass
    log.write(rec)
    vol = f'{rec["weighed_mL"]} mL' if rec["weighed_mL"] is not None else "-"
    print(f'{step.protocol_id} | {step.sample_id} | {vol} | {shown}')
    return rec

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--protocol", required=True)
    ap.add_argument("--port", default="/dev/ttyACM0")
    ap.add_argument("--mock", action="store_true", help="simulate Arduino + camera")
    ap.add_argument("--faults", default="", help="mock only, one per sample: none,no_container,misplaced,dark,no_flow,stall,slip,overfill")
    args = ap.parse_args()

    try:
        steps = load_protocol(args.protocol)
    except ValueError as e:
        sys.exit(f"Protocol refused: {e}")

    if args.mock:
        link = MockLink([f for f in args.faults.split(",") if f])
        vision = MockVision(link)
    else:
        link, vision = SerialLink(args.port), Vision()

    log = RunLogger()
    print(f"Running {len(steps)} samples\n")
    for step in steps:
        if args.mock:
            link.simulate_placement()
        else:
            input(f"Place container for sample {step.sample_id} ({step.target_ml} mL), then press Enter... ")
        run_sample(step, link, vision, log)
    print("\nSummary:", log.summary())
    link.close(); vision.close()

if __name__ == "__main__":
    main()