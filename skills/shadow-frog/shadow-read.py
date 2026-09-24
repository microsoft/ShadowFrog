#!/usr/bin/env python3
"""Optional bounded knowledge retrieval for agents that already navigate files.

Examples:
    python shadow-read.py src/auth.py
    python shadow-read.py src/auth.py::UserAuth.validate --limit 5
    python shadow-read.py --search "token expiry"

Direct .shadow/<source-path>.md reads remain the primary navigation method.
This helper selects and pages large sections; it does not replace their paths.
"""

import sys
from pathlib import Path


_bytecode = sys.dont_write_bytecode
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    import _knowledge
except ImportError as exc:
    raise SystemExit("ERROR: Missing core knowledge helper; reinstall the full ShadowFrog skill set") from exc
finally:
    sys.path.pop(0)
    sys.dont_write_bytecode = _bytecode


def main():
    return _knowledge.main(agent=True)


if __name__ == "__main__":
    sys.exit(main())
