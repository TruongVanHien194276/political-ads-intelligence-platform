"""Scan tracked files for common credential formats."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_NAMES = {".env", "credentials.json", "service-account.json", "key.json"}
PATTERNS = {
    "private key": re.compile("-----BEGIN " + ".*PRIVATE KEY-----"),
    "GitHub token": re.compile("gh" + r"[pousr]_[A-Za-z0-9_]{20,}"),
    "Google API key": re.compile("AI" + r"za[A-Za-z0-9_-]{35}"),
    "AWS access key": re.compile("AK" + r"IA[0-9A-Z]{16}"),
    "Slack token": re.compile("xo" + r"x[baprs]-[A-Za-z0-9-]{20,}"),
}


def scan_file(path: Path) -> list[str]:
    issues = []
    if path.name in FORBIDDEN_NAMES:
        issues.append("credential filename")
    if path.is_file():
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError, OSError:
            return issues
        if '"type": "service_account"' in content and '"private_key"' in content:
            issues.append("Google service account key")
        issues.extend(
            name for name, pattern in PATTERNS.items() if pattern.search(content)
        )
    return issues


def main() -> None:
    output = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    names = [Path(name.decode()) for name in output.split(b"\0") if name]
    findings = {
        str(name): issues for name in names if (issues := scan_file(ROOT / name))
    }
    if findings:
        for name, issues in findings.items():
            print(f"{name}: {', '.join(issues)}")
        raise SystemExit(1)
    print(f"Secret scan PASS: {len(names)} tracked files (high-signal patterns)")


if __name__ == "__main__":
    main()
