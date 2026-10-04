"""Check release inputs without printing the potentially sensitive matched values."""

import argparse
import json
import re
import subprocess
from pathlib import Path


ROOT_FILES = {
    ".gitignore",
    ".gitattributes",
    "README.md",
    "CONTRIBUTING.md",
    "pyproject.toml",
    "agentcost_cli.py",
    "start.sh",
    "start.command",
}
EXAMPLE_FILES = {"examples/events.jsonl", "examples/prices.json"}
TRANSIENT_DIRECTORIES = {".git", "__pycache__", "build", "dist", ".venv"}
PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "api_credential": re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16})\b"
    ),
    "personal_absolute_path": re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+(?:/|\b)"),
    "private_material_reference": re.compile(
        r"(?:(?:library|project)-file:[A-Za-z0-9]|https?://[a-z0-9.-]+\.larkoffice\.com/|\.codex/attachments/[A-Za-z0-9])",
        re.I,
    ),
}


def allowed_file(relative):
    parts = relative.parts
    name = relative.as_posix()
    if name in ROOT_FILES or name in EXAMPLE_FILES:
        return True
    if len(parts) == 2 and parts[0] == "agentcost":
        return parts[1].endswith(".py") or parts[1] in {"prices.json", "dashboard.html"}
    if len(parts) == 2 and parts[0] == "tests":
        return parts[1].startswith("test_") and parts[1].endswith(".py")
    return name == "scripts/check_release.py"


def check_tree(root, deny_text=()):
    root = Path(root).resolve()
    findings = []
    scanned = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.name == ".DS_Store":
            continue
        if any(
            part in TRANSIENT_DIRECTORIES or part.endswith(".egg-info") for part in relative.parts
        ):
            continue
        if path.is_symlink():
            findings.append({"file": relative.as_posix(), "rule": "symlink"})
            continue
        if path.is_dir():
            continue
        if not allowed_file(relative):
            findings.append({"file": relative.as_posix(), "rule": "not_release_allowlisted"})
            continue
        scanned.append(relative.as_posix())
        try:
            contents = path.read_text(encoding="utf8")
        except (UnicodeError, OSError):
            findings.append({"file": relative.as_posix(), "rule": "not_readable_utf8"})
            continue
        for name, pattern in PATTERNS.items():
            if pattern.search(contents):
                findings.append({"file": relative.as_posix(), "rule": name})
        if any(value and value.lower() in contents.lower() for value in deny_text):
            findings.append({"file": relative.as_posix(), "rule": "extra_denied_text"})
    # Inspect the exact staged blobs too: the index can differ from the worktree,
    # or contain ignored files that somebody force-added.
    if (root / ".git").exists():
        staged = (
            subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
            .stdout.decode("utf8")
            .split("\0")
        )
        for name in filter(None, staged):
            relative = Path(name)
            if not allowed_file(relative):
                findings.append({"file": name, "rule": "staged_not_release_allowlisted"})
                continue
            blob = subprocess.run(
                ["git", "show", ":" + name], cwd=root, capture_output=True, check=True
            ).stdout
            try:
                contents = blob.decode("utf8")
            except UnicodeError:
                findings.append({"file": name, "rule": "staged_not_readable_utf8"})
                continue
            for rule, pattern in PATTERNS.items():
                if pattern.search(contents):
                    findings.append({"file": name, "rule": "staged_" + rule})
            if any(value and value.lower() in contents.lower() for value in deny_text):
                findings.append({"file": name, "rule": "staged_extra_denied_text"})
    return {
        "status": "failed" if findings else "passed",
        "release_files": scanned,
        "findings": findings,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--deny-text",
        action="append",
        default=[],
        help="Additional private markers; matches are never printed",
    )
    args = parser.parse_args()
    result = check_tree(args.root, args.deny_text)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "passed" else 1)
