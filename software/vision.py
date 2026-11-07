"""Camera witness. Real OpenCV version, a mock that follows the MockLink fault, and a 'camera off' stand-in.

What the real camera reports (all the image maths is in vision_core.py):
  check_container()  -> ContainerCheck: present? in position? offset from the target in px (and mm)?
                        or UNRELIABLE when the image is too dark, overexposed or blurry.
  start_watch() ... tick() ... plunger_result(commanded_ml) -> PlungerCheck: marker travel in px vs commanded.
  snapshot(path, verdict, ok) -> annotated evidence photo (same drawing as the live preview).
"""
import threading, time
import config
import vision_core as vc
from vision_core import ContainerCheck, PlungerCheck, Quality


class Vision:
    disabled = False

    def __init__(self, index=config.CAMERA_INDEX):
        import cv2
        self.cv2 = cv2
        self.lock = threading.Lock()
        self.picam = self.cap = None
        if config.CAMERA_TYPE == "picamera":          # Raspberry Pi ribbon camera
            from picamera2 import Picamera2
            self.picam = Picamera2()
            self.picam.configure(self.picam.create_video_configuration(
                main={"size": (vc.WORK_W, vc.WORK_H), "format": "RGB888"}))
            self.picam.start()
            time.sleep(1.5)                           # let auto-exposure settle...
            try:                                      # ...then lock it so lighting changes don't fake a difference
                md = self.picam.capture_metadata()
                self.picam.set_controls({"AeEnable": False, "AwbEnable": False,
                                         "ExposureTime": md["ExposureTime"],
                                         "AnalogueGain": md["AnalogueGain"]})
            except Exception as e:
                print("could not lock exposure:", e)
        else:                                         # USB webcam
            self.cap = cv2.VideoCapture(index)
            if not self.cap.isOpened():
                raise RuntimeError("camera not found")
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, vc.WORK_W)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, vc.WORK_H)
        self.frame = None                             # last captured frame (working size, BGR)
        self.last_container = None                    # last ContainerCheck
        self.last_plunger = None                      # last PlungerCheck
        self._t = 0.0
        self._start_pt = None                         # marker position at the start of a dose (None = no dose running)
        self._start_gray = None
        self._live_gray = None
        self._live_marker = None
        self._last_trail = None
        self._start_bad = None

    # ---------------------------------------------------------------- capture
    def _capture(self):
        with self.lock:                               # the preview thread and the run share the camera
            if self.picam is not None:
                frame = self.picam.capture_array()
            else:
                for _ in range(2):                    # flush the driver buffer
                    self.cap.read()
                ok, frame = self.cap.read()
                if not ok:
                    raise RuntimeError("camera read failed")
        frame = vc.to_work_size(frame)
        self.frame = frame
        return frame

    # ---------------------------------------------------------------- container
    def needs_calibration(self):
        return False                                  # silhouette detection needs no empty reference photo

    def calibrate_empty(self):
        """Optional sanity check with the holder EMPTY: the image must be good and show no silhouette."""
        frame = self._capture()
        res = vc.find_container(vc.to_gray(frame))
        if not res.reliable:
            raise RuntimeError(f"image is {res.quality.reason}: fix the lighting/focus first")
        if res.present:
            raise RuntimeError("something is already in the container area; empty the holder and try again")
        return res.quality

    def check_container(self):
        res = vc.find_container(vc.to_gray(self._capture()))
        self.last_container = res
        return res

    def set_target_here(self):
        """Use the container currently in view as the target spot. Returns (x, y) as fractions."""
        res = self.check_container()
        if not res.reliable:
            raise RuntimeError(f"image is {res.quality.reason}")
        if not res.present:
            raise RuntimeError("no container silhouette in view")
        h, w = self.frame.shape[:2]
        return (round(res.center[0] / w, 4), round(res.center[1] / h, 4))

    # ---------------------------------------------------------------- plunger
    def _marker_now(self):
        """(marker position or None, gray image, image quality). No marker is searched in a bad image."""
        gray = vc.to_gray(self._capture())
        q = vc.check_quality(gray)
        return (vc.find_marker(gray) if q.reliable else None), gray, q

    def start_watch(self):
        """Call just before the pump starts: remember where the marker is (median of 3 quick looks)."""
        pts, grays, bad = [], [], None
        for _ in range(3):
            p, g, q = self._marker_now()
            pts.append(p); grays.append(g)
            bad = bad or (None if q.reliable else q.reason)
            time.sleep(0.05)
        self._start_pt = vc.median_point(pts)
        self._start_gray = grays[0]
        self._start_bad = bad
        self._live_marker = self._start_pt
        self.last_plunger = None

    def tick(self):
        """Called during dosing (rate limited): keeps the live marker position for the overlay."""
        if time.time() - self._t < 0.3:
            return
        self._t = time.time()
        self._live_marker, self._live_gray, _ = self._marker_now()

    def plunger_result(self, commanded_ml):
        """Call after the pump stopped: how far did the marker travel, and is that what was commanded?"""
        pts, last, bad = [], None, self._start_bad
        for _ in range(3):
            p, last, q = self._marker_now()
            pts.append(p)
            bad = bad or (None if q.reliable else q.reason)
            time.sleep(0.1)
        if bad:                                       # a bad image at the start or end: the camera does not vote
            self.last_plunger = PlungerCheck(reason=f"image {bad}")
            self._last_trail, self._start_pt = None, None
            return self.last_plunger
        diff = None
        if self._start_gray is not None and last is not None:
            diff = self._diff(self._start_gray, last)
        pc = vc.judge_plunger([self._start_pt], pts, commanded_ml, diff)
        self._last_trail = (self._start_pt, vc.median_point(pts))   # kept for the evidence photo
        self.last_plunger = pc
        self._start_pt = None
        return pc

    def _diff(self, a, b):
        x0, y0, x1, y1 = vc.roi_px(config.PLUNGER_ROI, a.shape)
        return float(abs(a[y0:y1, x0:x1].astype(float) - b[y0:y1, x0:x1].astype(float)).mean())

    # ---------------------------------------------------------------- pictures
    def _overlay(self, frame, verdict=None, ok=True, size=None, evidence=False):
        gray = vc.to_gray(frame)
        q = vc.check_quality(gray)
        container = vc.find_container(gray, q)
        marker = vc.find_marker(gray) if q.reliable else None
        trail, plunger = self._start_pt, None         # live: path from where the dose started to the marker now
        if evidence:                                  # evidence photo: start -> end path and the plunger verdict
            plunger = self.last_plunger
            if self._last_trail and self._last_trail[0] and self._last_trail[1]:
                trail, marker = self._last_trail
        img = vc.annotate(frame, container, marker, trail, plunger, verdict, ok)
        if size:
            img = self.cv2.resize(img, size, interpolation=self.cv2.INTER_AREA)
        return img

    def preview_png(self, size=(480, 360)):
        """Live camera view with the same overlay as the evidence photo."""
        img = self._overlay(self._capture(), size=size)
        ok, buf = self.cv2.imencode(".png", img)
        return buf.tobytes() if ok else None

    def snapshot(self, path, verdict=None, ok=True):
        """Save an annotated evidence photo (fresh frame + boxes + offsets + verdict)."""
        img = self._overlay(self._capture(), verdict, ok, evidence=True)
        return bool(self.cv2.imwrite(path, img))

    def close(self):
        if self.picam is not None:
            self.picam.stop()
        else:
            self.cap.release()


class MockVision:
    """Follows the MockLink fault: none, no_container, misplaced, dark, slip, stall, no_flow, overfill."""
    disabled = False
    def __init__(self, link):
        self.link = link
    def _fault(self):
        return getattr(self.link, "fault", "none")
    def needs_calibration(self): return False
    def calibrate_empty(self): return Quality()
    def check_container(self):
        f = self._fault()
        if f == "dark":
            return ContainerCheck(reliable=False, quality=Quality(False, "too dark", 30.0, 0.0, 40.0))
        if f == "no_container":
            return ContainerCheck(quality=Quality(True, "ok", 200.0, 0.1, 80.0))
        bad = f == "misplaced"
        dx = 60.0 if bad else 4.0
        return ContainerCheck(present=True, in_position=not bad, dx=dx, dy=0.0, offset_px=abs(dx),
                              center=(320 + dx, 340), bbox=(270, 280, 100, 120),
                              quality=Quality(True, "ok", 200.0, 0.1, 80.0))
    def set_target_here(self): return config.CONTAINER_TARGET
    def start_watch(self): pass
    def tick(self): pass
    def plunger_result(self, commanded_ml):
        f = self._fault()
        if f == "dark":
            return PlungerCheck(reason="too dark")
        ppm = config.PLUNGER_PX_PER_ML or 10.0
        ratio = 0.0 if f == "stall" else 0.4 if f == "slip" else 1.0
        exp = commanded_ml * ppm
        travel = exp * ratio
        moved = travel >= config.PLUNGER_MIN_PX
        return PlungerCheck(reliable=True, moved=moved, travel_px=travel, expected_px=exp, ratio=ratio,
                            slip=moved and abs(ratio - 1.0) > config.PLUNGER_SLIP_TOL)
    def preview_png(self, size=None): return None
    def snapshot(self, path, verdict=None, ok=True): return False
    def close(self): pass


class NullVision:
    """Used when 'Camera checks' is off: the run relies on the scale alone."""
    disabled = True
    def needs_calibration(self): return False
    def calibrate_empty(self): return Quality()
    def check_container(self): return ContainerCheck(present=True, in_position=True)
    def set_target_here(self): return config.CONTAINER_TARGET
    def start_watch(self): pass
    def tick(self): pass
    def plunger_result(self, commanded_ml): return PlungerCheck(reason="camera off")
    def preview_png(self, size=None): return None
    def snapshot(self, path, verdict=None, ok=True): return False
    def close(self): pass
