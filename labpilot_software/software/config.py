"""LabPilot settings. Everything you will need to tune on the real bench lives here."""
SERIAL_BAUD = 115200
DOSE_TIMEOUT_S = 120

# Pump / liquid
SYRINGE_CAPACITY_ML = 30.0
STEPS_PER_ML = 1000.0          # CALIBRATE on the real pump (steps/rev, microstepping, lead screw, syringe)
DENSITY_G_PER_ML = 1.0         # water only
MAX_STEPS_FACTOR = 1.3         # safety cap sent to the Arduino: 130% of the expected steps

# Decision thresholds
CONTAINER_MIN_G = 2.0          # raw scale reading that counts as "a container is on the scale"
MIN_GAIN_ML = 0.1              # below this the liquid did not really flow
MISMATCH_TOL_ML = 0.3          # allowed gap between commanded (steps) and weighed volume
SETTLE_INTERVAL_S = 0.2

# Camera (fractions of the image: x, y, width, height). TUNE with your camera view.
CAMERA_TYPE = "picamera"      # "picamera" = ribbon camera, "usb" = USB webcam
CAMERA_INDEX = 0               # USB only
CONTAINER_ROI = (0.20, 0.45, 0.60, 0.50)   # search area for the container silhouette (wider than the target spot)
PLUNGER_ROI = (0.40, 0.05, 0.20, 0.40)      # search area for the tape marker: must be bigger than its whole travel

# Container position (backlight: the container is a dark silhouette on a bright background)
CONTAINER_TARGET = (0.50, 0.72)   # where the container centre should be (x, y as fractions). Set it from the Camera tab.
CONTAINER_TOL_PX = 25.0           # allowed offset from the target, in pixels of the 640x480 working image
CONTAINER_MM_PER_PX = 0.0         # optional: container width in mm / its width in px. 0 = report pixels only
CONTAINER_DARK_FRAC = 0.60        # "dark" = below this fraction of the backlight level
CONTAINER_MIN_AREA_FRAC = 0.04    # silhouette must cover at least this fraction of the search area

# Image quality guard: if the image is bad the camera says UNRELIABLE and the run continues on the scale alone
QUALITY_MIN_BRIGHTNESS = 80.0     # backlight level (95th percentile of gray, 0-255) below this = too dark
QUALITY_MAX_SATURATED = 0.90      # more than this fraction of pure-white pixels = overexposed
QUALITY_MIN_SHARPNESS = 3.0       # edge sharpness below this = blurry (normalised to the lamp level; value shown on the live overlay)

# Plunger tracking (a high-contrast tape marker on the pump's moving block)
PLUNGER_MARKER = "bright"         # "bright" = light tape on a dark block, "dark" = dark tape on a light block
PLUNGER_MIN_CONTRAST = 60.0       # marker vs surroundings, gray levels
PLUNGER_MARKER_MIN_AREA_FRAC = 0.01   # marker blob size limits, as a fraction of the plunger search area
PLUNGER_MARKER_MAX_AREA_FRAC = 0.40
PLUNGER_MIN_PX = 3.0              # less travel than this = the plunger did not move
PLUNGER_PX_PER_ML = 0.0           # CALIBRATE with a test dose (Hardware tab). 0 = only "moved / did not move"
PLUNGER_SLIP_TOL = 0.35           # travel may differ from commanded by this fraction before PLUNGER_SLIP
MOTION_DIFF_THRESH = 4.0          # fallback when no marker is seen: mean gray change in PLUNGER_ROI while dosing

# ---- user settings saved by the GUI (settings.json overrides the values above) ----
import json as _json, os as _os
SETTINGS_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "settings.json")
DEFAULTS = {k: v for k, v in list(globals().items()) if k.isupper() and k != "SETTINGS_PATH"}

def _load():
    try:
        with open(SETTINGS_PATH) as f:
            globals().update(_json.load(f))
    except (OSError, ValueError):
        pass

def save_settings(values):
    cur = {}
    try:
        with open(SETTINGS_PATH) as f:
            cur = _json.load(f)
    except (OSError, ValueError):
        pass
    cur.update(values)
    with open(SETTINGS_PATH, "w") as f:
        _json.dump(cur, f, indent=2)
    globals().update(values)

def reset_settings():
    try:
        _os.remove(SETTINGS_PATH)
    except OSError:
        pass
    globals().update(DEFAULTS)

_load()
