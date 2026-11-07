# Data

## `example_protocol.csv`

Input file read by the Raspberry Pi software.

| Column | Meaning |
|---|---|
| `protocol_id` | Identifier of the run (for example `P-001`) |
| `sample_id` | Identifier of the sample or container |
| `target_mL` | Target volume to dispense |
| `tolerance_mL` | Allowed deviation for ACCEPTED |

## Log format

The software appends one timestamped record per operation. Adjust this table to the fields your code really writes.

| Field | Meaning |
|---|---|
| `protocol_id`, `sample_id` | Which run and sample |
| `target_mL` | Requested volume |
| `commanded_mL` | Volume implied by the motor steps |
| `weighed_mL` | Mass from the load cell, converted to volume (water density) |
| `camera` | Camera check result (OK or fault) |
| `error` | Error code (`none`, `NO_CONTAINER`, `NO_FLOW`, ...) |
| `status` | `ACCEPTED`, `REJECTED` or `ABORTED` |
| `t` | Timestamp |

## Adding data

- Put **real** logs from your runs in this folder (for example `example_log.csv`). Do not invent values.
- Put load-cell calibration measurements in `calibration/` (reference mass versus reading), with the date and setup.
- Do not commit camera frames showing people.
