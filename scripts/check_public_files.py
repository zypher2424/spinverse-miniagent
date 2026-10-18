#!/usr/bin/env python3
"""Check tracked public files without printing matched credential values."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    ("credential-like token", re.compile(r"(?:sk-[A-Za-z0-9]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("developer home/temp path", re.compile(r"/(?:Users|home)/[A-Za-z][^\s\"'<>]*|/(?:private/)?var/" r"folders/")),
]
BANNED = {".miniagent", ".venv", "venv", "__pycache__", ".pytest_cache"}
TEXT_SUFFIXES = {".py", ".md", ".svg", ".tcss", ".yml", ".yaml", ".txt", ".toml", ".sh", ".example"}


def main():
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    paths = [Path(p) for p in result.stdout.decode().split("\0") if p]
    if not paths:
        print("No tracked files to audit", file=sys.stderr)
        return 1
    findings = []
    for path in paths:
        if any(part in BANNED for part in path.parts) or (path.name.startswith(".env") and path.name != ".env.example"):
            findings.append((str(path), "private runtime/config path"))
            continue
        if path.suffix in TEXT_SUFFIXES or not path.suffix:
            text = (ROOT / path).read_text(encoding="utf-8")
            for description, pattern in PATTERNS:
                if pattern.search(text):
                    findings.append((str(path), description))
    if findings:
        for path, reason in findings:
            print(f"BLOCKED {path}: {reason}")
        return 1
    print(f"Public file audit passed: {len(paths)} tracked files; no credentials or private runtime paths found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
