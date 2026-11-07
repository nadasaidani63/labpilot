"""Whole-sample tests: the real camera analysis code driven by scripted frames (no camera, no Arduino).
Run:  python3 -m unittest discover -s tests -v   (from the software folder)"""
import os, sys, tempfile, threading, unittest
import cv2

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
from vision import Vision
from main import run_sample
from link import MockLink
from logger import RunLogger
from protocol import Step
from test_vision_core import scene, target

PX_PER_ML = 10.0


class ScriptedVision(Vision):
    """The real Vision analysis, with a fake sensor: the frame shows the marker where the pump has pushed it."""
    def __init__(self, link, container, light=1.0, slip=False):
        self.cv2, self.lock, self.picam, self.cap = cv2, threading.Lock(), None, None
        self.frame = self.last_container = self.last_plunger = None
        self._t = 0.0
        self._start_pt = self._start_gray = self._live_gray = self._live_marker = self._last_trail = self._start_bad = None
        self.link, self.container, self.light, self.slip = link, container, light, slip

    def _capture(self):
        y = 60 + self.link.dosed_ml * PX_PER_ML * (0.4 if self.slip else 1.0)
        self.frame = cv2.cvtColor(scene(self.container, marker_y=y, light=self.light, noise=2), cv2.COLOR_GRAY2BGR)
        return self.frame


class PumpLink(MockLink):
    dosed_ml = 0.0
    def clear_tare(self):
        super().clear_tare(); self.dosed_ml = 0.0
    def dose(self, *a, **k):
        r = super().dose(*a, **k); self.dosed_ml = r.steps / config.STEPS_PER_ML; return r


class TestRunSample(unittest.TestCase):
    def setUp(self):
        self.old = config.PLUNGER_PX_PER_ML
        config.PLUNGER_PX_PER_ML = PX_PER_ML
        self.log = RunLogger(tempfile.mkdtemp())

    def tearDown(self):
        config.PLUNGER_PX_PER_ML = self.old

    def run_one(self, fault="none", container="ok", **kw):
        tx, ty = target()
        spot = {"ok": (tx + 5, ty - 3), "far": (tx + 80, ty), "none": None}[container]
        link = PumpLink([fault]); link.simulate_placement()
        v = ScriptedVision(link, spot, **kw)
        return run_sample(Step("P", "A", 3.0, 0.2), link, v, self.log)

    def test_good_sample(self):
        r = self.run_one()
        self.assertEqual((r["status"], r["error"], r["camera"]), ("ACCEPTED", "none", "OK"))
        self.assertAlmostEqual(r["container_offset_px"], 6, delta=2)
        self.assertAlmostEqual(r["plunger_travel_px"], 30, delta=3)
        self.assertTrue(os.path.exists(os.path.join(self.log.folder, r["snapshot"])))

    def test_misplaced_container_is_a_number_and_nothing_is_dispensed(self):
        r = self.run_one(container="far")
        self.assertEqual(r["error"], "MISPLACED")
        self.assertAlmostEqual(r["container_offset_px"], 80, delta=3)
        self.assertIsNone(r["weighed_mL"])

    def test_dark_image_runs_on_the_scale_alone(self):
        r = self.run_one(light=0.2)
        self.assertEqual((r["status"], r["camera"]), ("ACCEPTED", "UNRELIABLE"))

    def test_plunger_slip(self):
        r = self.run_one(fault="slip", slip=True)
        self.assertEqual(r["error"], "PLUNGER_SLIP")

    def test_no_container_is_decided_by_the_scale(self):
        r = self.run_one(fault="no_container", container="none")
        self.assertEqual(r["error"], "NO_CONTAINER")


if __name__ == "__main__":
    unittest.main()
