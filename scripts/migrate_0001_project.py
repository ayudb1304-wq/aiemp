"""One-off migration: add the `project` column (and the other new columns) to tracker/actions.xlsx
and stamp the existing cards files with project / kind / origin.

Every existing row gets project = intelligent-tracker and origin = planned. Safe to re-run:
rows and files that already have a project are left alone.

  python scripts/migrate_0001_project.py
"""
import json

import tracker
from common import CARDS

DEFAULT_PROJECT = "intelligent-tracker"

if __name__ == "__main__":
    rows = tracker.load_rows()  # missing columns are read as ""
    changed = 0
    for r in rows:
        if not r["project"]:
            r["project"] = DEFAULT_PROJECT
            changed += 1
        if not r["origin"]:
            r["origin"] = "planned"
    tracker.save_rows(rows)  # writes the full column set
    files = 0
    for p in sorted(CARDS.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("project"):
            continue
        data.setdefault("kind", "transcript")
        data.setdefault("origin", "planned")
        data["project"] = DEFAULT_PROJECT
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        files += 1
    print(f"migrate_0001: {changed} rows given project={DEFAULT_PROJECT}, {len(rows)} rows total; "
          f"{files} cards file(s) stamped")
