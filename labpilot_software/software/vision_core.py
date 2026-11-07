"""Camera analysis for LabPilot: pure OpenCV/numpy functions, no hardware, so they can be tested on any image.

Scene assumed: a fixed camera, a backlight behind the container (the container is a DARK SILHOUETTE on a
BRIGHT background) and a high-contrast marker (tape) on the pump's moving block.

All coordinates are in pixels of the working frame (WORK_W x WORK_H). Settings come from config.py.
"""
from dataclasses import dataclass, field
import math
import cv2
import numpy as np
import config

WORK_W, WORK_H = 640, 480

GREEN, RED, ORANGE, BLUE, YELLOW, WHITE = (60, 200, 60), (50, 50, 230), (31, 154, 255), (255, 139, 59), (0, 220, 255), (255, 255, 255)


# ----------------------------------------------------------------------------- results
@dataclass
class Quality:
    reliable: bool = True
    reason: str = "ok"          # "ok", "too dark", "overexposed", "blurry"
    brightness: float = 0.0     # 95th percentile of the gray image (the backlight level)
    saturated: float = 0.0      # fraction of pixels at full white
    sharpness: float = 0.0      # variance of the Laplacian (higher = sharper)


@dataclass
class ContainerCheck:
    reliable: bool = True        # False -> the image is not good enough to judge (see quality.reason)
    present: bool = False        # a dark silhouette of plausible size was found in the search region
    in_position: bool = False    # present AND within the tolerance of the target spot
    dx: float = 0.0              # silhouette centre minus target, px (+x = right, +y = down)
    dy: float = 0.0
    offset_px: float = 0.0       # distance from the target spot
    offset_mm: float = None      # only when CONTAINER_MM_PER_PX is set
    bbox: tuple = None           # (x, y, w, h)
    center: tuple = None
    clipped: bool = False        # silhouette touches the left/right edge of the search region
    quality: Quality = field(default_factory=Quality)

    def summary(self):
        if not self.reliable:
            return f"UNRELIABLE ({self.quality.reason})"
        if not self.present:
            return "container not seen"
        mm = f" = {self.offset_mm:.1f} mm" if self.offset_mm is not None else ""
        return f"offset {self.offset_px:.0f} px{mm} (dx {self.dx:+.0f}, dy {self.dy:+.0f})"


@dataclass
class PlungerCheck:
    reliable: bool = False       # marker tracked at the start and at the end
    method: str = "marker"       # "marker" or "diff" (fallback: no marker seen, frame difference only)
    moved: bool = None           # None when not reliable
    travel_px: float = 0.0
    expected_px: float = None    # from the commanded volume; None until PLUNGER_PX_PER_ML is calibrated
    ratio: float = None          # travel / expected
    slip: bool = False           # moved clearly less (or more) than commanded
    reason: str = ""

    def summary(self):
        if not self.reliable:
            return f"plunger unknown ({self.reason})"
        exp = f" (exp {self.expected_px:.0f})" if self.expected_px is not None else ""
        return f"plunger {self.travel_px:.0f} px{exp}"


# ----------------------------------------------------------------------------- helpers
def to_work_size(frame):
    if frame.shape[1] != WORK_W or frame.shape[0] != WORK_H:
        frame = cv2.resize(frame, (WORK_W, WORK_H), interpolation=cv2.INTER_AREA)
    return frame


def to_gray(frame):
    g = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(g, (5, 5), 0)


def roi_px(roi, shape):
    """Fractions (x, y, w, h) -> integer pixel box (x0, y0, x1, y1) inside the image."""
    h, w = shape[:2]
    x, y, rw, rh = roi
    x0, y0 = max(0, int(x * w)), max(0, int(y * h))
    x1, y1 = min(w, int((x + rw) * w)), min(h, int((y + rh) * h))
    return x0, y0, max(x0 + 1, x1), max(y0 + 1, y1)


def target_px(shape):
    h, w = shape[:2]
    tx, ty = config.CONTAINER_TARGET
    return tx * w, ty * h


# ----------------------------------------------------------------------------- 2. image quality guard
def check_quality(gray):
    """Is the image good enough to trust? Looks at the backlight level, clipping and sharpness."""
    bright = float(np.percentile(gray, 95))
    sat = float((gray >= 250).mean())
    # sharpness is measured on the image normalised to the backlight level, so a dimmer lamp is not "blurry"
    sharp = float(cv2.Laplacian(gray.astype(np.float64) * (200.0 / max(bright, 1.0)), cv2.CV_64F).var())
    q = Quality(True, "ok", bright, sat, sharp)
    if bright < config.QUALITY_MIN_BRIGHTNESS:
        q.reliable, q.reason = False, "too dark"
    elif sat > config.QUALITY_MAX_SATURATED:
        q.reliable, q.reason = False, "overexposed"
    elif sharp < config.QUALITY_MIN_SHARPNESS:
        q.reliable, q.reason = False, "blurry"
    return q


# ----------------------------------------------------------------------------- 1. container position
def find_container(gray, quality=None):
    """Find the dark silhouette in CONTAINER_ROI and measure its offset from CONTAINER_TARGET."""
    q = quality or check_quality(gray)
    res = ContainerCheck(reliable=q.reliable, quality=q)
    if not q.reliable:
        return res
    x0, y0, x1, y1 = roi_px(config.CONTAINER_ROI, gray.shape)
    region = gray[y0:y1, x0:x1]
    # "dark" is relative to the backlight, so a brighter or dimmer lamp does not change the result
    level = config.CONTAINER_DARK_FRAC * q.brightness
    mask = (region < level).astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)        # drop specks
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)       # close small holes (labels, bubbles)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return res
    c = max(contours, key=cv2.contourArea)
    if cv2.contourArea(c) < config.CONTAINER_MIN_AREA_FRAC * region.size:
        return res
    bx, by, bw, bh = cv2.boundingRect(c)
    res.present = True
    res.bbox = (bx + x0, by + y0, bw, bh)
    res.center = (res.bbox[0] + bw / 2.0, res.bbox[1] + bh / 2.0)
    res.clipped = bx <= 0 or bx + bw >= region.shape[1]
    tx, ty = target_px(gray.shape)
    res.dx, res.dy = res.center[0] - tx, res.center[1] - ty
    res.offset_px = math.hypot(res.dx, res.dy)
    if config.CONTAINER_MM_PER_PX > 0:
        res.offset_mm = res.offset_px * config.CONTAINER_MM_PER_PX
    res.in_position = (res.offset_px <= config.CONTAINER_TOL_PX) and not res.clipped
    return res


# ----------------------------------------------------------------------------- 4. plunger marker
def find_marker(gray):
    """Centre (x, y) of the tape marker inside PLUNGER_ROI, or None. Uses Otsu inside the region, so it
    adapts to the lighting; the marker must be clearly brighter (or darker) than its surroundings."""
    x0, y0, x1, y1 = roi_px(config.PLUNGER_ROI, gray.shape)
    region = gray[y0:y1, x0:x1]
    t, mask = cv2.threshold(region, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    hi, lo = region[mask > 0], region[mask == 0]
    if hi.size == 0 or lo.size == 0 or float(hi.mean() - lo.mean()) < config.PLUNGER_MIN_CONTRAST:
        return None                                          # nothing high-contrast in the region
    if config.PLUNGER_MARKER == "dark":
        mask = cv2.bitwise_not(mask)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    lo_a, hi_a = config.PLUNGER_MARKER_MIN_AREA_FRAC * region.size, config.PLUNGER_MARKER_MAX_AREA_FRAC * region.size
    rh, rw = region.shape
    ok = []
    for c in contours:
        bx, by, bw, bh = cv2.boundingRect(c)
        area = cv2.contourArea(c)
        if not lo_a <= area <= hi_a:
            continue
        if bx <= 0 or by <= 0 or bx + bw >= rw or by + bh >= rh:
            continue                                         # touches the search box edge: background, not the marker
        if area < 0.5 * bw * bh:
            continue                                         # not a compact blob
        ok.append(c)
    if not ok:
        return None
    c = max(ok, key=cv2.contourArea)
    m = cv2.moments(c)
    if m["m00"] == 0:
        return None
    return (m["m10"] / m["m00"] + x0, m["m01"] / m["m00"] + y0)


def median_point(points):
    pts = [p for p in points if p is not None]
    if not pts:
        return None
    return (float(np.median([p[0] for p in pts])), float(np.median([p[1] for p in pts])))


def judge_plunger(start_pts, end_pts, commanded_ml, diff_score=None):
    """Compare the marker's travel with the commanded volume.
    start_pts / end_pts: marker positions seen at the start / end of the dose (None = not found)."""
    s, e = median_point(start_pts), median_point(end_pts)
    pc = PlungerCheck()
    if s is None or e is None:                               # no marker: fall back to plain motion detection
        if diff_score is not None:
            pc.method, pc.reliable = "diff", True
            pc.moved = diff_score > config.MOTION_DIFF_THRESH
            pc.reason = "marker not seen, using frame difference"
        else:
            pc.reason = "marker not seen"
        return pc
    pc.reliable = True
    pc.travel_px = math.hypot(e[0] - s[0], e[1] - s[1])
    pc.moved = pc.travel_px >= config.PLUNGER_MIN_PX
    if config.PLUNGER_PX_PER_ML > 0 and commanded_ml and commanded_ml > 0:
        pc.expected_px = commanded_ml * config.PLUNGER_PX_PER_ML
        pc.ratio = pc.travel_px / pc.expected_px
        pc.slip = abs(pc.ratio - 1.0) > config.PLUNGER_SLIP_TOL and pc.moved
    return pc


# ----------------------------------------------------------------------------- 3. annotated evidence photo
def _text(img, s, org, color, scale=0.6, thick=2):
    """Text on a dark chip so it stays readable on a bright backlit image."""
    (tw, th), base = cv2.getTextSize(s, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    x, y = int(org[0]), int(org[1])
    x = max(2, min(x, img.shape[1] - tw - 4))
    cv2.rectangle(img, (x - 3, y - th - 3), (x + tw + 3, y + base + 1), (20, 20, 20), -1)
    cv2.putText(img, s, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def annotate(frame, container=None, marker=None, trail=None, plunger=None, verdict=None, verdict_ok=True):
    """Draw boxes, offsets, marker trail, image-quality status and the verdict on a copy of the frame."""
    img = frame.copy()
    h, w = img.shape[:2]
    for roi, col, name in ((config.CONTAINER_ROI, BLUE, "container area"), (config.PLUNGER_ROI, ORANGE, "plunger area")):
        a, b, c, d = roi_px(roi, img.shape)
        cv2.rectangle(img, (a, b), (c, d), col, 1)
        _text(img, name, (a + 3, b + 14), col, 0.45, 1)
    # target spot and its tolerance circle
    tx, ty = target_px(img.shape)
    cv2.circle(img, (int(tx), int(ty)), int(config.CONTAINER_TOL_PX), YELLOW, 1, cv2.LINE_AA)
    cv2.drawMarker(img, (int(tx), int(ty)), YELLOW, cv2.MARKER_CROSS, 14, 1)
    if container is not None:
        q = container.quality
        qcol = GREEN if q.reliable else RED
        _text(img, f"image {q.reason.upper() if not q.reliable else 'OK'}  light {q.brightness:.0f}  sharp {q.sharpness:.0f}",
              (8, 18), qcol, 0.5, 1)
        if container.present:
            bx, by, bw, bh = container.bbox
            col = GREEN if container.in_position else RED
            cv2.rectangle(img, (bx, by), (bx + bw, by + bh), col, 2)
            cx, cy = container.center
            cv2.circle(img, (int(cx), int(cy)), 4, col, -1)
            cv2.line(img, (int(tx), int(ty)), (int(cx), int(cy)), col, 1, cv2.LINE_AA)
            _text(img, container.summary() + (" CLIPPED" if container.clipped else ""), (bx, max(16, by - 8)), col, 0.5, 1)
        elif container.reliable:
            _text(img, "no container silhouette", (int(w * 0.30), int(h * 0.62)), ORANGE, 0.6, 2)
    if trail is not None and marker is not None:
        cv2.line(img, (int(trail[0]), int(trail[1])), (int(marker[0]), int(marker[1])), YELLOW, 2, cv2.LINE_AA)
    if marker is not None:
        cv2.circle(img, (int(marker[0]), int(marker[1])), 7, ORANGE, 2, cv2.LINE_AA)
    if plunger is not None:
        col = GREEN if plunger.reliable and plunger.moved and not plunger.slip else RED
        a, b, c, d = roi_px(config.PLUNGER_ROI, img.shape)
        _text(img, plunger.summary() + (" SLIP" if plunger.slip else ""), (c + 8, b + 34), col, 0.5, 1)
    if verdict:
        cv2.rectangle(img, (0, h - 30), (w, h), (0, 0, 0), -1)
        _text(img, verdict, (8, h - 9), GREEN if verdict_ok else RED, 0.7, 2)
    return img
