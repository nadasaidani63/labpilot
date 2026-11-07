import csv, json
from dataclasses import dataclass
import config

@dataclass
class Step:
    protocol_id: str
    sample_id: str
    target_ml: float
    tolerance_ml: float

def validate(steps):
    errors = []
    for n, s in enumerate(steps, start=1):
        if not s.sample_id:
            errors.append(f"sample {n}: missing sample id")
        if s.target_ml <= 0 or s.tolerance_ml <= 0:
            errors.append(f"sample {n}: target and tolerance must be > 0")
        elif s.target_ml > config.SYRINGE_CAPACITY_ML:
            errors.append(f"sample {n}: {s.target_ml} mL exceeds syringe capacity")
    total = sum(s.target_ml for s in steps)
    if total > config.SYRINGE_CAPACITY_ML:
        errors.append(f"total {total:g} mL exceeds syringe capacity (no refill in v1)")
    if not steps:
        errors.append("protocol is empty")
    if errors:
        raise ValueError("; ".join(errors))

def load_protocol(path):
    """Read a .csv or .json protocol and validate it. Raises ValueError with a readable message."""
    need = {"protocol_id", "sample_id", "target_mL", "tolerance_mL"}
    if path.lower().endswith(".json"):
        with open(path) as f:
            rows = json.load(f)
        if not isinstance(rows, list):
            raise ValueError("JSON protocol must be a list of samples")
    else:
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames or not need <= set(reader.fieldnames):
                raise ValueError(f"CSV must have columns: {sorted(need)}")
            rows = list(reader)
    steps, errors = [], []
    for n, row in enumerate(rows, start=1):
        try:
            steps.append(Step(str(row["protocol_id"]).strip(), str(row["sample_id"]).strip(),
                              float(row["target_mL"]), float(row["tolerance_mL"])))
        except (KeyError, ValueError, TypeError):
            errors.append(f"sample {n}: missing or non-numeric value")
    if errors:
        raise ValueError("; ".join(errors))
    validate(steps)
    return steps
