"""Compare the witnesses (commanded steps, weighed mass, camera) and name the fault."""
import config

def decide(step, motor_status, commanded_ml, weighed_ml, plunger=None):
    """Return (status, error). status: ACCEPTED / REJECTED / ABORTED.
    plunger: a vision_core.PlungerCheck, or None when the camera is off. If it is not reliable the decision
    rests on the scale alone."""
    known = plunger is not None and plunger.reliable
    moved = plunger.moved if known else None            # None = camera cannot tell
    if motor_status in ("STALL", "ENDSTOP"):
        return "ABORTED", "MOTOR_STALL"
    if weighed_ml < config.MIN_GAIN_ML:
        return "ABORTED", ("MOTOR_STALL" if moved is False else "NO_FLOW")
    if weighed_ml > step.target_ml + step.tolerance_ml:
        return "REJECTED", "OVERFILL"
    if known and plunger.slip:                          # fourth cross-check: plunger travel vs commanded steps
        return "REJECTED", "PLUNGER_SLIP"
    if abs(weighed_ml - commanded_ml) > config.MISMATCH_TOL_ML:
        return "REJECTED", "MISMATCH"
    if abs(weighed_ml - step.target_ml) > step.tolerance_ml:
        return "REJECTED", "OUT_OF_TOLERANCE"
    return "ACCEPTED", "none"
