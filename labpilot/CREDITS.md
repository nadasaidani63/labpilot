# Credits and third-party work

LabPilot is built on the work of others. Thank you to everyone below.

## Poseidon syringe pump

- **Project:** poseidon, open-source syringe pumps and microscope for laboratories
- **Repository:** https://github.com/pachterlab/poseidon
- **Authors:** Pachter Lab, Caltech (A. Sina Booeshaghi, Eduardo da Veiga Beltrame, Dylan Bannon, Jase Gehring, Lior Pachter)
- **Paper:** A. S. Booeshaghi et al., "Principles of open source bioinstrumentation applied to the poseidon syringe pump system," *Scientific Reports*, vol. 9, art. 12385, 2019.
- **License:** BSD-2-Clause

**What we reused:** _list exactly what you took, for example the pump mechanical design and the Arduino CNC-shield approach._
**What we changed:** the microscope was removed; a Raspberry Pi camera, a load cell with HX711, and camera-based verification were added.

> **BSD-2-Clause obligation.** Where files are copied or adapted from Poseidon, keep its copyright notice and license text. Copy the original `LICENSE` text from the Poseidon repository into `third_party/poseidon-LICENSE.txt` (create the folder) and keep the original header comments in any adapted source files.

## Poseidon's own prior work

Poseidon's pump design is based on the open-source syringe pumps of the Pearce Research Group:

- B. Wijnen, E. J. Hunt, G. C. Anzalone and J. M. Pearce, "Open-source syringe pump library," *PLoS ONE*, vol. 9, no. 9, art. e107216, 2014.

## Libraries and tools

List every library you actually use, with its license, for example:

| Component | Used for | License |
|---|---|---|
| OpenCV | Image processing | _check_ |
| pySerial | Pi to Arduino serial link | _check_ |
| NumPy | Numerical processing | _check_ |
| HX711 Arduino library (name and author) | Load cell reading | _check_ |
| Stepper library (name and author) | Motor control | _check_ |
| Raspberry Pi OS / Picamera2 | Camera capture | _check_ |

## Event and support

- IEEE TRSYP 3.0, Laboratory 4.0 Challenge, presented by the IEEE INSAT Student Branch
- Adwya and Orange Fablab (challenge partners)
- Mentors and teammates: _add names_
