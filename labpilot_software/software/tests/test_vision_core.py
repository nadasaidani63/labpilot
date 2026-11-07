"""Tests for the camera analysis on synthetic backlit scenes.  Run:  python3 -m unittest discover -s tests -v
(from the software folder). No camera or hardware needed."""
import os, sys, unittest
import numpy as np
import cv2

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import config
import vision_core as vc
from decision import decide
from protocol import Step

W, H = vc.WORK_W, vc.WORK_H


def scene(container_at=None, marker_y=60, light=1.0, noise=0.0, seed=0, container_wh=(110, 150)):
    """640x480 gray scene: bright backlight, dark container silhouette, dark pump block with a white tape marker."""
    img = np.full((H, W), 210, np.uint8)
    cv2.rectangle(img, (270, 24), (370, 230), 35, -1)                  # pump block (dark)
    if marker_y is not None:
        cv2.rectangle(img, (300, int(marker_y)), (340, int(marker_y) + 16), 245, -1)   # tape marker
    if container_at is not None:
        cx, cy = container_at
        w, h = container_wh
        cv2.rectangle(img, (int(cx - w / 2), int(cy - h / 2)), (int(cx + w / 2), int(cy + h / 2)), 45, -1)
        cv2.rectangle(img, (int(cx - w / 2) + 15, int(cy - h / 2) + 40), (int(cx + w / 2) - 15, int(cy - h / 2) + 70), 120, -1)  # label
    out = img.astype(float) * light
    if noise:
        out += np.random.default_rng(seed).normal(0, noise, out.shape)
    return np.clip(out, 0, 255).astype(np.uint8)


def target():
    return W * config.CONTAINER_TARGET[0], H * config.CONTAINER_TARGET[1]


class TestContainer(unittest.TestCase):
    def test_in_position_reports_small_offset(self):
        tx, ty = target()
        r = vc.find_container(vc.to_gray(scene((tx + 6, ty - 4))))
        self.assertTrue(r.reliable and r.present and r.in_position)
        self.assertAlmostEqual(r.dx, 6, delta=2)
        self.assertAlmostEqual(r.dy, -4, delta=2)

    def test_misplaced_gives_a_number(self):
        tx, ty = target()
        r = vc.find_container(vc.to_gray(scene((tx + 70, ty))))
        self.assertTrue(r.present and not r.in_position)
        self.assertAlmostEqual(r.offset_px, 70, delta=3)

    def test_absent(self):
        r = vc.find_container(vc.to_gray(scene(None)))
        self.assertTrue(r.reliable and not r.present)

    def test_lighting_change_does_not_matter(self):
        tx, ty = target()
        for light in (0.55, 0.8, 1.0, 1.15):
            r = vc.find_container(vc.to_gray(scene((tx, ty), light=light, noise=3)))
            self.assertTrue(r.reliable and r.present and r.in_position, f"light {light}: {r.summary()}")

    def test_mm_conversion(self):
        tx, ty = target()
        old = config.CONTAINER_MM_PER_PX
        config.CONTAINER_MM_PER_PX = 0.25
        try:
            r = vc.find_container(vc.to_gray(scene((tx + 40, ty))))
            self.assertAlmostEqual(r.offset_mm, 10.0, delta=1.0)
        finally:
            config.CONTAINER_MM_PER_PX = old

    def test_clipped_container_is_not_in_position(self):
        x, y, w, h = config.CONTAINER_ROI
        edge = W * x + 20                                   # container centre close to the left edge of the search area
        r = vc.find_container(vc.to_gray(scene((edge, H * config.CONTAINER_TARGET[1]))))
        self.assertTrue(r.present and r.clipped and not r.in_position)


class TestQuality(unittest.TestCase):
    def test_good(self):
        self.assertTrue(vc.check_quality(vc.to_gray(scene(target()))).reliable)

    def test_too_dark(self):
        q = vc.check_quality(vc.to_gray(scene(target(), light=0.25)))
        self.assertFalse(q.reliable)
        self.assertEqual(q.reason, "too dark")

    def test_overexposed(self):
        q = vc.check_quality(vc.to_gray(scene(target(), light=9.0)))
        self.assertEqual(q.reason, "overexposed")

    def test_blurry(self):
        blurred = cv2.GaussianBlur(scene(target()), (0, 0), 14)
        q = vc.check_quality(vc.to_gray(blurred))
        self.assertEqual(q.reason, "blurry", f"sharpness {q.sharpness:.1f}")

    def test_unreliable_image_is_not_judged(self):
        r = vc.find_container(vc.to_gray(scene(target(), light=0.25)))
        self.assertFalse(r.reliable)
        self.assertFalse(r.present)


class TestPlunger(unittest.TestCase):
    def marker(self, y, **kw):
        return vc.find_marker(vc.to_gray(scene(None, marker_y=y, **kw)))

    def test_marker_found_and_tracks_position(self):
        a, b = self.marker(60), self.marker(100)
        self.assertIsNotNone(a)
        self.assertAlmostEqual(b[1] - a[1], 40, delta=1.0)

    def test_marker_with_other_lighting(self):
        a, b = self.marker(60, light=0.6, noise=3), self.marker(100, light=0.6, noise=3)
        self.assertAlmostEqual(b[1] - a[1], 40, delta=1.5)

    def test_no_marker(self):
        self.assertIsNone(vc.find_marker(vc.to_gray(scene(None, marker_y=None))))

    def test_dark_marker_mode(self):
        img = scene(None, marker_y=None)
        img[24:230, 270:370] = 220                          # light block
        cv2.rectangle(img, (300, 80), (340, 96), 20, -1)    # dark tape
        old = config.PLUNGER_MARKER
        config.PLUNGER_MARKER = "dark"
        try:
            p = vc.find_marker(vc.to_gray(img))
        finally:
            config.PLUNGER_MARKER = old
        self.assertIsNotNone(p)
        self.assertAlmostEqual(p[1], 88, delta=1.5)

    def judge(self, start_y, end_y, commanded_ml, px_per_ml=10.0):
        old = config.PLUNGER_PX_PER_ML
        config.PLUNGER_PX_PER_ML = px_per_ml
        try:
            s = [self.marker(start_y)] * 3
            e = [self.marker(end_y)] * 3
            return vc.judge_plunger(s, e, commanded_ml)
        finally:
            config.PLUNGER_PX_PER_ML = old

    def test_normal_travel(self):
        p = self.judge(60, 90, 3.0)                         # 3 mL x 10 px/mL = 30 px expected
        self.assertTrue(p.reliable and p.moved and not p.slip)
        self.assertAlmostEqual(p.ratio, 1.0, delta=0.1)

    def test_slip_is_caught(self):
        p = self.judge(60, 72, 3.0)                         # moved 12 px of 30
        self.assertTrue(p.moved and p.slip)

    def test_full_stall(self):
        p = self.judge(60, 60, 3.0)
        self.assertTrue(p.reliable and not p.moved and not p.slip)

    def test_uncalibrated_only_checks_motion(self):
        p = self.judge(60, 72, 3.0, px_per_ml=0.0)
        self.assertTrue(p.moved and not p.slip and p.expected_px is None)

    def test_marker_missing_is_unreliable_without_diff(self):
        p = vc.judge_plunger([None], [None], 3.0)
        self.assertFalse(p.reliable)

    def test_marker_missing_falls_back_to_frame_difference(self):
        p = vc.judge_plunger([None], [None], 3.0, diff_score=9.0)
        self.assertTrue(p.reliable and p.moved and p.method == "diff")


class TestDecision(unittest.TestCase):
    step = Step("P", "A", 3.0, 0.2)

    def pc(self, **kw):
        return vc.PlungerCheck(**{"reliable": True, "moved": True, **kw})

    def test_accepted(self):
        self.assertEqual(decide(self.step, "DONE", 3.0, 3.0, self.pc()), ("ACCEPTED", "none"))

    def test_slip_names_the_cause(self):
        self.assertEqual(decide(self.step, "LIMIT", 3.9, 1.6, self.pc(slip=True)), ("REJECTED", "PLUNGER_SLIP"))

    def test_no_flow_vs_stall(self):
        self.assertEqual(decide(self.step, "DONE", 3.0, 0.0, self.pc())[1], "NO_FLOW")
        self.assertEqual(decide(self.step, "DONE", 3.0, 0.0, self.pc(moved=False))[1], "MOTOR_STALL")

    def test_unreliable_camera_runs_on_scale_alone(self):
        bad = vc.PlungerCheck(reliable=False, slip=True)    # even a stale slip flag is ignored
        self.assertEqual(decide(self.step, "DONE", 3.0, 3.0, bad), ("ACCEPTED", "none"))
        self.assertEqual(decide(self.step, "DONE", 3.0, 0.0, bad)[1], "NO_FLOW")

    def test_camera_off(self):
        self.assertEqual(decide(self.step, "DONE", 3.0, 3.0, None), ("ACCEPTED", "none"))


class TestAnnotate(unittest.TestCase):
    def test_overlay_draws_and_keeps_size(self):
        tx, ty = target()
        gray = vc.to_gray(scene((tx + 70, ty)))
        c = vc.find_container(gray)
        frame = cv2.cvtColor(scene((tx + 70, ty)), cv2.COLOR_GRAY2BGR)
        out = vc.annotate(frame, c, (320, 80), (320, 60), vc.PlungerCheck(reliable=True, moved=True, travel_px=20), "A: MISPLACED", False)
        self.assertEqual(out.shape, frame.shape)
        self.assertGreater(int(np.abs(out.astype(int) - frame.astype(int)).sum()), 0)


if __name__ == "__main__":
    unittest.main()
