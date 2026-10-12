"""Turn a driftcheck ``report.json`` into a shields.io endpoint ``badge.json``.

Usage:
    python demo/make_badge.py <report.json> <badge.json>

The badge is what the README points at, so the live pass/fail status is carried by
the badge content rather than the (always-"green") workflow run status.
"""

from __future__ import annotations

import json
import sys


def make_badge(report_path: str, badge_path: str) -> dict:
    """Read a driftcheck JSON report and write a shields.io endpoint badge."""
    with open(report_path, encoding="utf-8") as fh:
        report = json.load(fh)

    total = int(report.get("total", 0))
    conformant = int(report.get("conformant", 0))
    nonconformant = int(report.get("nonconformant", 0))

    if total == 0:
        message, color = "no checks", "lightgrey"
    elif nonconformant == 0:
        message, color = f"{conformant}/{total} passing", "brightgreen"
    else:
        message, color = f"{nonconformant}/{total} drifted", "red"

    badge = {
        "schemaVersion": 1,
        "label": "API conformance",
        "message": message,
        "color": color,
    }
    with open(badge_path, "w", encoding="utf-8") as fh:
        json.dump(badge, fh)
    return badge


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python demo/make_badge.py <report.json> <badge.json>", file=sys.stderr)
        raise SystemExit(2)
    result = make_badge(sys.argv[1], sys.argv[2])
    print(json.dumps(result))
