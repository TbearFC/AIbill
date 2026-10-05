"""Download pinned public metadata and score tables, never personal logs."""

import hashlib
import json
import os
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"


def fetch_sources():
    os.umask(0o077)
    manifest = json.loads((ROOT / "source-manifest.json").read_text(encoding="utf-8"))
    for entry in manifest:
        relative = Path(entry["local_file"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid metadata destination")
        path = DATA / relative
        if not path.resolve().is_relative_to(DATA.resolve()):
            raise ValueError("Metadata destination escapes data directory")
        expected_prefix = (
            "https://huggingface.co/datasets/"
            + entry["dataset"]
            + "/resolve/"
            + entry["revision"]
            + "/"
        )
        if entry["url"] != expected_prefix + entry["file"]:
            raise ValueError("Source URL does not match pinned manifest")
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".part")
        for attempt in range(4):
            try:
                with urllib.request.urlopen(entry["url"], timeout=90) as response:
                    payload = response.read()
                if len(payload) != entry["bytes"]:
                    raise ValueError("Source size mismatch")
                if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
                    raise ValueError("Source hash mismatch")
                temporary.write_bytes(payload)
                temporary.replace(path)
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(2**attempt)
    print("Validated seven pinned public metadata / result files", flush=True)


if __name__ == "__main__":
    fetch_sources()
