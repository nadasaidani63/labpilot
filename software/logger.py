import csv, json, os
from datetime import datetime

FIELDS = ["t", "protocol_id", "sample_id", "target_mL", "tolerance_mL", "commanded_mL",
          "weighed_mL", "camera", "container_offset_px", "plunger_travel_px", "status", "error", "snapshot"]

class RunLogger:
    def __init__(self, folder="logs"):
        os.makedirs(folder, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.folder = folder
        self.base = os.path.join(folder, f"run_{stamp}")
        self.records = []

    def write(self, rec):
        rec = {k: rec.get(k) for k in FIELDS}
        self.records.append(rec)
        with open(self.base + ".jsonl", "a") as f:          # append-only
            f.write(json.dumps(rec) + "\n")
        new = not os.path.exists(self.base + ".csv")
        with open(self.base + ".csv", "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if new:
                w.writeheader()
            w.writerow(rec)

    def summary(self):
        s = {"accepted": 0, "rejected": 0, "aborted": 0, "errors": []}
        for r in self.records:
            s[r["status"].lower()] += 1
            if r["error"] != "none":
                s["errors"].append(f'{r["sample_id"]}:{r["error"]}')
        with open(self.base + "_summary.json", "w") as f:
            json.dump(s, f, indent=2)
        return s
