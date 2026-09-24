#!/usr/bin/env python3
"""Browse and visualize collected shadow knowledge for users in the terminal.

Run without arguments for an overview, or use --search, --prefs, --recent,
--labels and --check-invariants. The separate dream-lineage.py renders HTML.
Agent workflows use direct file/symbol navigation, with optional bounded
retrieval through the core shadow-frog/shadow-read.py helper.
"""

import sys
from pathlib import Path


_bytecode = sys.dont_write_bytecode
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shadow-frog"))
try:
    import _knowledge
except ImportError as exc:
    raise SystemExit("ERROR: Missing core knowledge helper; reinstall the full ShadowFrog skill set") from exc
finally:
    sys.path.pop(0)
    sys.dont_write_bytecode = _bytecode


def main():
    return _knowledge.main()


if __name__ == "__main__":
    sys.exit(main())
