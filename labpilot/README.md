# LabPilot

**A self-verifying syringe pump for automated sample preparation.**

🥈 2nd place, **Laboratory 4.0 Challenge** (Axis 1: Automated Sample Preparation), IEEE TRSYP 3.0, with Adwya and Orange Fablab.

> LabPilot runs a digital protocol on a syringe pump and accepts a sample only when **three independent witnesses agree**: the commanded motion, the mass measured by a load cell, and what a camera sees. Any disagreement stops the run with a named fault and is logged.

<!-- Add a photo or GIF of the demo here: ![LabPilot demo](docs/images/demo.gif) -->

---

## Why

Repetitive manual dispensing in the lab leads to volume errors, differences between operators and steps that are rarely recorded. Most low-cost dispensers run *blind*: they assume a command always gives the right result. LabPilot compares what was commanded with what was weighed and what was seen.

## The three witnesses

| Witness | Answers | Sensor |
|---|---|---|
| Motor command | "What did I try to do?" | Stepper steps (Arduino) |
| Load cell | "How much was actually delivered?" | Load cell + HX711 |
| Camera | "What is physically happening?" | Raspberry Pi camera + OpenCV |

The camera does **not** measure volume. It checks that the container is present and in position, and that the plunger moves. The load cell gives the quantitative result.

| Command | Camera | Load cell | Decision |
|---|---|---|---|
| Push | Plunger moves | Mass matches command | Continue, then ACCEPTED |
| Push | Plunger moves | No mass gain | `NO_FLOW` |
| Push | Plunger still | No mass gain | `MOTOR_STALL` |
| Push | Plunger moves | Mass differs from command | `MISMATCH` / `OVERFILL` |
| Any | Container absent or misplaced | n/a | `NO_CONTAINER` / `MISPLACED` (nothing dispensed) |

> Update the fault names above to match what the code actually reports.

## How it works

```
protocol.csv ──► Raspberry Pi ──USB serial──► Arduino Uno + CNC Shield V3
 (targets)       protocol, OpenCV,             stepper, load cell (HX711),
                 decisions, log, display        end-stop, dosing loop
                      ▲                                │
                 Pi camera                       syringe pump + scale
```

- **Arduino (hardware layer):** drives the stepper, reads the load cell and end-stop, and runs the fast / slow / stop dosing loop locally.
- **Raspberry Pi (intelligence layer):** reads the protocol, sends each target to the Arduino, receives mass and plunger readings, analyses the camera image, compares the witnesses and writes the log.

## Hardware

- Arduino Uno + CNC Shield V3 (stepper on the X axis)
- Stepper driver module on the shield, NEMA 17 stepper, lead screw syringe carriage
- Load cell + HX711 amplifier (HX711 on A0 / A1)
- End-stop switch (D9)
- Raspberry Pi 4 Model B with Raspberry Pi OS, ribbon camera (OV5647)
- Syringe and tubing, sample containers, spill tray
- Emergency stop on the motor power supply

See `hardware/` for CAD files and the bill of materials.

## Repository structure

```
firmware/    Arduino sketch (LabPilot.ino)
software/    Raspberry Pi Python code (camera checks, protocol, logging)
data/        Example protocol, log format, calibration data
hardware/    CAD files, wiring diagram, bill of materials
docs/        Concept book (PDF) and images
```

## Getting started

### 1. Arduino firmware

1. Open `firmware/LabPilot.ino` in the Arduino IDE.
2. Install the libraries listed at the top of the sketch (for example a stepper library and an HX711 library).
3. Select **Arduino Uno**, choose the serial port, and upload.
4. Check the wiring: stepper on the CNC Shield X axis, HX711 on A0 / A1, end-stop on D9.

### 2. Raspberry Pi software

```bash
cd software
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install -r requirements.txt
python3 <main_script>.py --protocol ../data/example_protocol.csv
```

> Replace `<main_script>.py` and the options with your real entry point. On Raspberry Pi OS, install the camera and OpenCV packages with `apt` if `pip` versions do not work (for example `sudo apt install python3-opencv python3-picamera2`).

## Protocol format

```csv
protocol_id,sample_id,target_mL,tolerance_mL
P-001,A,5.0,0.2
P-001,B,3.0,0.2
P-001,C,5.0,0.2
```

Targets and tolerances come from the protocol. See `data/example_protocol.csv`.

## Log format

Every operation appends one timestamped record (see `data/README.md`): protocol and sample ID, target, commanded and weighed volume, camera status, error code, final status (ACCEPTED / REJECTED / ABORTED) and time.

## Status and limitations

> **Fill in honestly.** Example structure:
>
> - Working: _…_
> - Partly working: _…_
> - Not finished: _…_ (for example, parts of the Arduino-side integration)
>
> Known limits: load-cell calibration, drift and settling time; camera sensitivity to lighting; the water-density assumption (mass converted to volume); containers placed one at a time by the operator.

## Safety

- Use only water and harmless coloured solutions.
- Keep liquids away from electronics; use a spill tray.
- The emergency stop cuts motor power.
- This is a **proof of concept**, not a certified laboratory instrument.

## Credits and license

LabPilot builds on the open-source **Poseidon** syringe pump (Pachter Lab, BSD-2-Clause) and other open-source work. See [`CREDITS.md`](CREDITS.md).

This project is released under the BSD-2-Clause license. See [`LICENSE`](LICENSE).

## Event

IEEE TRSYP 3.0, Laboratory 4.0 Challenge, presented by the IEEE INSAT Student Branch, in partnership with Adwya and Orange Fablab. 18-hour makeathon, Le Royal, Yasmine Hammamet, October 2026.

## Team

_Add team members and links._
