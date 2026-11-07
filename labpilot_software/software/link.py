"""Arduino link: a real serial link and an in-process mock with the same interface."""
import time
from dataclasses import dataclass, field
import config

class LinkError(Exception):
    pass

@dataclass
class DoseResult:
    status: str            # DONE | LIMIT | STALL | ENDSTOP
    mass_g: float
    steps: int
    samples: list = field(default_factory=list)

class SerialLink:
    def __init__(self, port, baud=config.SERIAL_BAUD):
        import serial
        self.ser = serial.Serial(port, baud, timeout=1)
        time.sleep(2.0)                      # the Uno resets when the port opens
        self.ser.reset_input_buffer()
        self._request("PING", "OK")

    def _send(self, line):
        self.ser.write((line + "\n").encode())

    def _request(self, cmd, prefix, timeout=3.0):
        self._send(cmd)
        end = time.time() + timeout
        while time.time() < end:
            line = self.ser.readline().decode(errors="ignore").strip()
            if line.startswith(prefix):
                return line.split()
        raise LinkError(f"no '{prefix}' reply to '{cmd}'")

    def clear_tare(self):
        """Clear the tare so MASS reports the absolute mass (needed to see a new, empty container)."""
        self._request("UNTARE", "OK")

    def tare(self):
        self._request("TARE", "OK")

    def read_mass(self):
        return float(self._request("MASS", "MASS")[1])

    def dose(self, target_g, max_steps, on_sample=None):
        self._send(f"DOSE {target_g:.2f} {max_steps}")
        samples, end = [], time.time() + config.DOSE_TIMEOUT_S
        while time.time() < end:
            p = self.ser.readline().decode(errors="ignore").strip().split()
            if not p:
                continue
            if p[0] == "DATA":
                t, m, s = int(p[1]), float(p[2]), int(p[3])
                samples.append((t, m, s))
                if on_sample:
                    on_sample(t, m, s)
            elif p[0] in ("DONE", "LIMIT", "STALL", "ENDSTOP"):
                return DoseResult(p[0], float(p[1]), int(p[2]), samples)
        self.stop()
        raise LinkError("dose timeout")

    def calibrate_scale(self, known_g):
        """Optional command: Arduino sets the load-cell factor using a known weight on the scale."""
        self._request(f"CAL {known_g:.2f}", "OK", timeout=8.0)

    def stop(self):
        self._send("STOP")

    def close(self):
        self.ser.close()


class MockLink:
    """Simulated Arduino + scale. faults = one entry per sample, e.g. ['none','no_container','no_flow'].
    Fault names: none, no_container, misplaced, dark (bad image), no_flow, stall, slip, overfill."""
    def __init__(self, faults=None, delay=0.0):
        self.delay = delay                   # seconds per simulated motor chunk (GUI uses a small delay)
        self.faults = list(faults or [])
        self.fault = "none"
        self.raw = 0.0
        self.offset = 0.0

    def simulate_placement(self):
        self.fault = self.faults.pop(0) if self.faults else "none"
        self.raw = 0.0 if self.fault == "no_container" else 45.0   # an empty container ~45 g
        self.offset = 0.0

    def clear_tare(self):
        self.offset = 0.0

    def tare(self):
        self.offset = self.raw

    def read_mass(self):
        return self.raw - self.offset if self.offset else self.raw

    def dose(self, target_g, max_steps, on_sample=None):
        steps, gained, t = 0, 0.0, 0
        samples = []
        while True:
            if target_g - gained <= 0.02:
                status = "DONE"; break
            if self.fault == "stall" and steps >= 300:
                status = "STALL"; break
            if steps >= max_steps:
                status = "LIMIT"; break
            chunk = 200 if target_g - gained > 1.0 else 20
            if self.fault == "stall":
                chunk = min(chunk, 300 - steps)
            chunk = min(chunk, max_steps - steps)
            if self.delay:
                time.sleep(self.delay)
            steps += chunk
            t += chunk
            if self.fault not in ("no_flow", "stall"):
                share = 0.4 if self.fault == "slip" else 1.0     # slip: the motor skips steps, only part of it pushes
                gained += chunk / config.STEPS_PER_ML * config.DENSITY_G_PER_ML * share
            samples.append((t, gained, steps))
            if on_sample:
                on_sample(t, gained, steps)
        if self.fault == "overfill":
            gained += 0.8                    # drips / scale glitch after the stop
        self.raw += gained
        return DoseResult(status, gained, steps, samples)

    def calibrate_scale(self, known_g):
        pass

    def stop(self):
        pass

    def close(self):
        pass