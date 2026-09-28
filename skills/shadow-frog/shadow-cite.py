#!/usr/bin/env python3
"""Record visits to existing Markdown discoveries after reading them normally.

Example:
    python shadow-cite.py .shadow/src/auth.py.md --symbol login --text "Rejects expired tokens."

Repeat --text to cite several entries in the same file/section atomically.
Cite each deliberately consulted entry once per task. Repeated invocations
increment again; task-level deduplication belongs to the agent/coordinator.
"""

import argparse
from pathlib import Path
import sys


_bytecode = sys.dont_write_bytecode
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from _citations import record_citations
except ImportError as exc:
    raise SystemExit("ERROR: Missing core citation helper; reinstall the full skill set") from exc
finally:
    sys.path.pop(0)
    sys.dont_write_bytecode = _bytecode


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="The .shadow Markdown file already read")
    parser.add_argument("--symbol", help="Exact source symbol, or File-Level (per-file shadows only)")
    parser.add_argument("--text", action="append", required=True, help="Exact consulted claim; repeat for several")
    parser.add_argument("--shadow-dir", type=Path, help="Explicit root for a nonstandard shadow location")
    args = parser.parse_args()
    try:
        updates = record_citations(args.file, args.text, symbol=args.symbol, shadow_dir=args.shadow_dir)
    except (ValueError, OSError, UnicodeError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Recorded {len(updates)} citation(s) in {args.file}:")
    for update in updates:
        print(f"  {update['before']} -> {update['after']}: {update['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
