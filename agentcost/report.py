"""Render the offline research workspace from aggregate data."""

import json
from pathlib import Path


def write_report(data, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "usage.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf8"
    )
    payload = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    template = Path(__file__).with_name("dashboard.html").read_text(encoding="utf8")
    output = out / "report.html"
    output.write_text(template.replace("__PAYLOAD__", payload), encoding="utf8")
    return output
