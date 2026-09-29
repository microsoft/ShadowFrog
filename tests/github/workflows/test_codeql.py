"""Keep CodeQL stages on one immutable action revision."""

import re


def test_codeql_action_pins_are_synchronized(repo_root):
    workflow = (repo_root / ".github/workflows/codeql.yml").read_text(encoding="utf-8")
    actions = re.findall(
        r"""^\s*(?:-\s*)?uses:\s*['"]?(github/codeql-action/[\w-]+)@([^\s'"]+)""",
        workflow,
        re.MULTILINE,
    )
    assert {"github/codeql-action/init", "github/codeql-action/analyze"} <= {
        name for name, _ in actions
    }
    pins = {pin for _, pin in actions}
    assert all(re.fullmatch(r"[0-9a-f]{40}", pin) for pin in pins), \
        "Pin every CodeQL action to a full commit SHA"
    assert len(pins) == 1, \
        "CodeQL stages share versioned configuration; update all pins together"
