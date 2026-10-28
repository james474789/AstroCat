"""
Turn a failed CI step's log into a GitHub error annotation.

Job logs need a signed-in user to read; annotations are public through the API, so this makes a
failed dry run diagnosable without anyone downloading logs.

    python desktop/scripts/ci_annotate.py "<title>" <log file or folder> [...]

For each log, emits one ::error:: annotation with the lines around the first errors (Rust
`error[...]`/`error:`, Python tracebacks, npm/tauri failures), or the log's tail if none match.
"""

import re
import sys
from pathlib import Path

ERROR = re.compile(r"(^|\s)(error(\[E\d+\])?:|Error:|Traceback \(most recent call last\)|FAILED|failed to|\bpanicked\b)",
                   re.IGNORECASE)
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
CONTEXT_BEFORE, CONTEXT_AFTER, MAX_LINES = 3, 25, 120


def excerpt(lines: list[str]) -> list[str]:
    picked, last = [], -1
    for i, line in enumerate(lines):
        if ERROR.search(line) and i > last:
            start, end = max(i - CONTEXT_BEFORE, last + 1), min(i + CONTEXT_AFTER, len(lines))
            if picked:
                picked.append("…")
            picked.extend(lines[start:end])
            last = end - 1
            if len(picked) >= MAX_LINES:
                break
    return picked[:MAX_LINES] or lines[-60:]


def encode(text: str) -> str:
    # workflow-command escaping: https://github.com/actions/toolkit/blob/main/packages/core/src/command.ts
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main():
    title, targets = sys.argv[1], [Path(p) for p in sys.argv[2:]]
    files = []
    for t in targets:
        files += sorted(t.glob("*.log")) if t.is_dir() else [t]
    for f in files:
        if not f.is_file() or f.stat().st_size == 0:
            continue
        text = ANSI.sub("", f.read_text(encoding="utf-8", errors="replace"))  # cargo colours "error"
        lines = text.splitlines()
        body = "\n".join(excerpt(lines))[-60000:]
        # property values additionally escape ":" and ","
        prop = encode(f"{title} - {f.name}").replace(":", "%3A").replace(",", "%2C")
        print(f"::error title={prop}::{encode(body)}")


if __name__ == "__main__":
    main()
