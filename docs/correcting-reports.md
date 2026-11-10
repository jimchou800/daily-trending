# Correcting a Published Report

Corrections update the existing dated archive. They do not run collection or analysis, change the selected repositories or heat metrics, or add a successful inclusion event.

Load the published JSON, correct factual fields on existing entries, and call `daily_trending.publisher.correct_report` with that report, the static site root, a timezone-aware ISO timestamp, and a concise correction note. The function validates the immutable report identity and source links, renders the page from the corrected JSON, and atomically replaces the archive files. When the corrected date is still the latest issue, an existing root `index.html` is updated too. Historical corrections leave the latest homepage and index metadata untouched.

Example:

```python
import json
from datetime import datetime, timezone
from pathlib import Path

from daily_trending.publisher import correct_report

site = Path("public")
path = site / "archive" / "2026-11-09" / "report.json"
report = json.loads(path.read_text(encoding="utf-8"))
report["entries"][0]["introduction"] = "Corrected, source-backed description."
correct_report(
    report,
    site,
    datetime.now(timezone.utc).isoformat(),
    "Corrected the project description against its README.",
)
```

The original publication timestamp, period URL, selected repositories, source snapshot, and heat metrics remain fixed. Keep the correction note specific enough for readers to understand what changed.
