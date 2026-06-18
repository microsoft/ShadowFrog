"""Tests for skills/shadow-frog-dream/_worktree_safety.py.

The safety module is the *only* line of defense between a misconfigured
`$DREAM_WORKTREE_BASE` (or a buggy slug-derivation in the reconciler) and
`rm -rf`ing something we shouldn't. These tests are deliberately paranoid:
every adversarial input that could escape the gate gets its own assertion.
"""
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SKILL_DIR = REPO_ROOT / "skills" / "shadow-frog-dream"

sys.path.insert(0, str(SKILL_DIR))
from _worktree_safety import (  # noqa: E402
    UnsafePath, _FORBIDDEN_BASES, _strip_macos_private, safe_worktree_path
)
sys.path.pop(0)


# ===========================================================================
# Happy path — legitimate dream worktrees pass
# ===========================================================================

class TestHappyPath:
    def test_canonical_shape_under_default_base(self):
        p = safe_worktree_path(
            "/tmp/shadowfrog-dreams/proj/dream-foo",
            "/tmp/shadowfrog-dreams",
        )
        # On macOS the path may resolve through /private — both are fine.
        assert str(p).endswith("/proj/dream-foo")

    def test_custom_base_under_tmpdir(self, tmp_path):
        base = tmp_path / "my-dreams"
        target = base / "myproject" / "dream-t01-fuzzer"
        target.mkdir(parents=True)
        p = safe_worktree_path(str(target), str(base))
        assert p.exists()

    def test_slug_with_dots_and_underscores(self, tmp_path):
        # SAFE_RE allows [A-Za-z0-9._-], so v1.2.3 and snake_case are fine.
        base = tmp_path / "b"
        target = base / "ns_one" / "dream-v1.2.3"
        target.mkdir(parents=True)
        assert safe_worktree_path(str(target), str(base)) == target.resolve()


# ===========================================================================
# Rule 1: non-empty inputs
# ===========================================================================

class TestEmptyInputs:
    @pytest.mark.parametrize("path", ["", "   ", "\t\n"])
    def test_rejects_empty_path(self, path):
        with pytest.raises(UnsafePath, match="empty"):
            safe_worktree_path(path, "/tmp/shadowfrog-dreams")

    @pytest.mark.parametrize("base", ["", "   ", "\t\n"])
    def test_rejects_empty_base(self, base):
        with pytest.raises(UnsafePath, match="empty"):
            safe_worktree_path("/tmp/x/proj/dream-foo", base)

    @pytest.mark.parametrize("path", [None, 42, ["/tmp/x"]])
    def test_rejects_non_string_path(self, path):
        with pytest.raises(UnsafePath):
            safe_worktree_path(path, "/tmp/shadowfrog-dreams")


# ===========================================================================
# Rule 2: absolute paths only
# ===========================================================================

class TestRelativePaths:
    @pytest.mark.parametrize("path", [
        "tmp/shadowfrog-dreams/proj/dream-foo",
        "./proj/dream-foo",
        "../escape",
        "proj/dream-foo",
    ])
    def test_rejects_relative_path(self, path):
        with pytest.raises(UnsafePath, match="not absolute"):
            safe_worktree_path(path, "/tmp/shadowfrog-dreams")

    def test_rejects_relative_base(self):
        with pytest.raises(UnsafePath, match="not absolute"):
            safe_worktree_path(
                "/tmp/shadowfrog-dreams/proj/dream-foo",
                "tmp/shadowfrog-dreams",
            )


# ===========================================================================
# Rule 3: no ".." traversal in literal input
# ===========================================================================

class TestTraversal:
    @pytest.mark.parametrize("path", [
        "/tmp/shadowfrog-dreams/../etc/passwd",
        "/tmp/shadowfrog-dreams/proj/dream-foo/../../../etc",
        "/tmp/shadowfrog-dreams/../shadowfrog-dreams/proj/dream-foo",
    ])
    def test_rejects_traversal_in_path(self, path):
        with pytest.raises(UnsafePath, match=r"'\.\.'"):
            safe_worktree_path(path, "/tmp/shadowfrog-dreams")

    def test_rejects_traversal_in_base(self):
        with pytest.raises(UnsafePath, match=r"'\.\.'"):
            safe_worktree_path(
                "/tmp/shadowfrog-dreams/proj/dream-foo",
                "/tmp/shadowfrog-dreams/../shadowfrog-dreams",
            )


# ===========================================================================
# Rule 4: sensitive bases — refused regardless of path-shape validity
# ===========================================================================

class TestSensitiveBases:
    @pytest.mark.parametrize("base", [
        "/", "/tmp", "/var", "/var/folders", "/var/tmp", "/etc", "/home",
        "/Users", "/root", "/bin", "/sbin", "/dev", "/proc", "/sys",
        "/usr", "/lib", "/Library", "/System",
        # macOS /private prefixed forms — must ALSO refuse.
        "/private/tmp", "/private/etc", "/private/var",
    ])
    def test_rejects_sensitive_base(self, base):
        # Use a path shape that would pass shape-check, so only Rule 4 can fail.
        with pytest.raises(UnsafePath, match="sensitive root"):
            safe_worktree_path(f"{base}/proj/dream-foo", base)

    def test_rejects_home_as_base(self):
        home = os.path.expanduser("~")
        if not home or home == "~":
            pytest.skip("no HOME set")
        with pytest.raises(UnsafePath, match="sensitive root"):
            safe_worktree_path(f"{home}/proj/dream-foo", home)


# ===========================================================================
# Rule 5: strictly under base (no escape via symlinks or absolute paths)
# ===========================================================================

class TestUnderBase:
    def test_rejects_base_itself(self, tmp_path):
        base = tmp_path / "b"
        base.mkdir()
        with pytest.raises(UnsafePath, match="strictly under base"):
            safe_worktree_path(str(base), str(base))

    def test_rejects_path_above_base(self, tmp_path):
        base = tmp_path / "b"
        base.mkdir()
        # The base is /<tmp>/b; this path is /<tmp>/other
        other = tmp_path / "other" / "proj" / "dream-foo"
        with pytest.raises(UnsafePath, match="strictly under base"):
            safe_worktree_path(str(other), str(base))

    def test_rejects_symlinked_leaf_escaping_base(self, tmp_path):
        # base/ns/dream-evil → tmp_path/escape-target (outside base)
        base = tmp_path / "b"
        ns = base / "ns"
        ns.mkdir(parents=True)
        escape = tmp_path / "escape-target"
        escape.mkdir()
        link = ns / "dream-evil"
        link.symlink_to(escape)
        with pytest.raises(UnsafePath, match="strictly under base"):
            safe_worktree_path(str(link), str(base))

    def test_rejects_symlinked_parent_escaping_base(self, tmp_path):
        # base/escape-ns is a symlink to /etc. base/escape-ns/dream-x must
        # be refused even though the LITERAL input looks valid.
        base = tmp_path / "b"
        base.mkdir()
        (base / "escape-ns").symlink_to("/etc")
        with pytest.raises(UnsafePath, match="strictly under base"):
            safe_worktree_path(str(base / "escape-ns" / "dream-x"), str(base))


# ===========================================================================
# Rule 6: exact `<base>/<ns>/dream-<slug>` shape
# ===========================================================================

class TestShape:
    def test_rejects_too_shallow_one_level(self, tmp_path):
        base = tmp_path / "b"
        base.mkdir()
        with pytest.raises(UnsafePath, match="2 levels under"):
            safe_worktree_path(str(base / "dream-foo"), str(base))

    def test_rejects_too_deep_three_levels(self, tmp_path):
        base = tmp_path / "b"
        base.mkdir()
        with pytest.raises(UnsafePath, match="2 levels under"):
            safe_worktree_path(
                str(base / "ns" / "dream-foo" / "extra"), str(base),
            )

    def test_rejects_leaf_without_dream_prefix(self, tmp_path):
        base = tmp_path / "b"
        base.mkdir()
        with pytest.raises(UnsafePath, match="dream-"):
            safe_worktree_path(str(base / "ns" / "notdream-foo"), str(base))

    def test_rejects_empty_slug(self, tmp_path):
        base = tmp_path / "b"
        base.mkdir()
        with pytest.raises(UnsafePath, match="slug"):
            safe_worktree_path(str(base / "ns" / "dream-"), str(base))

    @pytest.mark.parametrize("bad_slug", [
        "foo bar",         # space
        "foo;rm -rf /",    # shell metachar
        "foo$evil",        # shell metachar
        "foo/bar",         # subdir embedded — would land at depth 3 anyway
        "foo\nbar",        # newline
    ])
    def test_rejects_unsafe_slug(self, tmp_path, bad_slug):
        base = tmp_path / "b"
        base.mkdir()
        with pytest.raises(UnsafePath):
            safe_worktree_path(
                str(base / "ns" / f"dream-{bad_slug}"), str(base),
            )

    @pytest.mark.parametrize("bad_ns", [
        "n s", "n;s", "n$s", "n\ts", "n/s",
    ])
    def test_rejects_unsafe_ns(self, tmp_path, bad_ns):
        base = tmp_path / "b"
        base.mkdir()
        with pytest.raises(UnsafePath):
            safe_worktree_path(
                str(base / bad_ns / "dream-foo"), str(base),
            )


# ===========================================================================
# CLI exit codes (used by dream-cleanup.sh and dream-gc.sh)
# ===========================================================================

class TestCLI:
    def _run(self, *args):
        import subprocess
        return subprocess.run(
            [sys.executable, str(SKILL_DIR / "_worktree_safety.py"), *args],
            capture_output=True, text=True,
        )

    def test_exit_0_when_safe_and_exists(self, tmp_path):
        base = tmp_path / "b"
        target = base / "ns" / "dream-foo"
        target.mkdir(parents=True)
        r = self._run(str(target), str(base))
        assert r.returncode == 0

    def test_exit_2_when_safe_and_missing(self, tmp_path):
        base = tmp_path / "b"
        base.mkdir()
        r = self._run(str(base / "ns" / "dream-foo"), str(base))
        assert r.returncode == 2

    def test_exit_1_when_unsafe(self):
        r = self._run("/tmp/proj/dream-foo", "/tmp")
        assert r.returncode == 1
        assert "ERROR" in r.stderr

    def test_exit_1_on_usage_error(self):
        r = self._run()
        assert r.returncode == 1


# ===========================================================================
# Internal helper: _strip_macos_private
# ===========================================================================

class TestMacosPrivateStripping:
    @pytest.mark.parametrize("inp,expected", [
        ("/private/tmp", "/tmp"),
        ("/private/var/folders", "/var/folders"),
        ("/private/etc", "/etc"),
        ("/private", "/private"),            # standalone — don't strip
        ("/var/private/folders", "/var/private/folders"),  # not a prefix
        ("/tmp", "/tmp"),                    # no-op
    ])
    def test_strips_only_leading_private_segment(self, inp, expected):
        assert _strip_macos_private(inp) == expected


# ===========================================================================
# Regression guard: the forbidden-base list must include the macOS /private
# variants for /tmp, /var, /etc — the original implementation missed these.
# ===========================================================================

class TestForbiddenBaseListInvariants:
    def test_tmp_var_etc_are_forbidden(self):
        for must_be_listed in ("/tmp", "/var", "/etc", "/home", "/Users"):
            assert must_be_listed in _FORBIDDEN_BASES, (
                f"{must_be_listed} must stay in _FORBIDDEN_BASES to keep "
                f"the safety gate trustworthy"
            )
