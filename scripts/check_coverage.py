"""Enforce separate production line and branch coverage thresholds."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    """Read coverage JSON and fail if either production threshold is missed."""
    data = json.loads(Path("coverage.json").read_text(encoding="utf-8"))
    totals = data["totals"]
    line = totals["covered_lines"] / max(totals["num_statements"], 1) * 100
    branch = totals["covered_branches"] / max(totals["num_branches"], 1) * 100
    print(f"Production line coverage: {line:.2f}%")
    print(f"Production branch coverage: {branch:.2f}%")
    return 0 if line >= 80 and branch >= 80 else 1


if __name__ == "__main__":
    sys.exit(main())
