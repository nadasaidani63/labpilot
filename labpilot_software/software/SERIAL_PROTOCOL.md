# Pi <-> Arduino serial contract (115200 baud, ASCII, one line per message, "\n" terminated)

Pi -> Arduino            Arduino -> Pi
PING                     OK PONG
UNTARE                   OK UNTARE               (clear the tare: MASS then reports the absolute mass,
                                                  e.g. an empty container ~45 g. Sent before every container check.)
TARE                     OK TARE                 (zero the scale)
MASS                     MASS <grams>            (current tared mass)
DOSE <target_g> <max_steps>
                         DATA <t_ms> <mass_g> <steps>      (repeat, about every 100-200 ms)
                         then ONE final line:
                         DONE  <mass_g> <steps>   mass reached target (Arduino ran fast -> slow -> stop locally)
                         LIMIT <mass_g> <steps>   max_steps reached before the mass target (nothing flowing?)
                         STALL <mass_g> <steps>   motor stalled / blocked
                         ENDSTOP <mass_g> <steps> plunger end-stop hit
STOP                     (stop the motor immediately; no reply needed)
CAL <known_g>            OK CAL                  (OPTIONAL: set the load-cell factor using a known weight
                                                  on the scale; the screen's 'Calibrate scale' button sends it)

Notes for the Arduino side
- The Arduino stops on the mass threshold by itself; the Pi never closes the loop in real time.
- max_steps is a safety cap computed by the Pi. Never move more than that.
- mass_g in DATA/final lines is the mass gained since the last TARE.
- Pi converts mass to volume (water density) and steps to volume (STEPS_PER_ML in config.py).