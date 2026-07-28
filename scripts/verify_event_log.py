#!/usr/bin/env python3
"""Validate sequence numbers and the SHA-256 chain of one session events.jsonl."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def verify(path: Path) -> int:
    previous = "0" * 64
    count = 0
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        record = json.loads(line)
        digest = record.pop("record_hash")
        if record["sequence"] != line_number:
            raise ValueError(f"line {line_number}: unexpected sequence {record['sequence']}")
        if record["previous_hash"] != previous:
            raise ValueError(f"line {line_number}: previous_hash mismatch")
        canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        actual = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if actual != digest:
            raise ValueError(f"line {line_number}: record_hash mismatch")
        previous = digest
        count += 1
    print(f"Valid hash chain: {count} records; final hash {previous}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("events_jsonl", type=Path)
    return verify(parser.parse_args().events_jsonl)


if __name__ == "__main__":
    raise SystemExit(main())

