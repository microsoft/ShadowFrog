"""The CLI helpers must stay UTF-8-safe under a narrow (cp1252) codec, not only
on a UTF-8 platform.

Linux/macOS default to UTF-8, so a missing explicit encoding only fails where
the platform default codec is narrow (e.g. Windows ``cp1252``). These tests
force that narrow-codec failure mode on *any* OS:

  * ``PYTHONIOENCODING=cp1252`` pins a narrow stdout/stderr codec, exercising
    the streams' ``reconfigure(encoding="utf-8")`` path.
  * ``LC_ALL=C`` + ``PYTHONUTF8=0`` makes the default *file* codec narrow on
    POSIX too, exercising the explicit ``encoding="utf-8"`` on file and
    subprocess I/O; on Windows the ANSI code page already makes it ``cp1252``.

A helper that emits non-ASCII without pinning UTF-8 raises
``UnicodeEncodeError`` / ``UnicodeDecodeError`` here and exits non-zero; with
UTF-8 pinned, both pass on every platform.
"""
import os
import subprocess
import sys

import pytest


def _narrow_env():
    """Environment that forces a narrow (non-UTF-8) default codec everywhere."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp1252"  # narrow stdout/stderr codec
    env["PYTHONUTF8"] = "0"             # ensure UTF-8 Mode isn't masking it
    env["LC_ALL"] = "C"                 # narrow default file codec on POSIX
    return env


@pytest.mark.slow
@pytest.mark.integration
class TestNarrowCodecOutput:
    """Run the helpers under a forced narrow codec; non-ASCII output must survive."""

    def test_shadow_viewer_invariants(self, repo_root, coupon_demo_src):
        """shadow-viewer prints a non-ASCII check mark; it must survive a pipe."""
        viewer = repo_root / "skills/shadow-frog-viewer/shadow-viewer.py"
        r = subprocess.run(
            [sys.executable, str(viewer), "--check-invariants",
             "--shadow-dir", str(coupon_demo_src / ".shadow")],
            capture_output=True, encoding="utf-8", env=_narrow_env(),
        )
        assert r.returncode == 0, f"stderr:\n{r.stderr}"
        assert "\u2713 Invariants OK" in r.stdout  # the U+2713 check mark survived

    def test_dream_lineage_writes_utf8_html(self, repo_root, coupon_demo_src, tmp_path):
        """dream-lineage embeds a frog emoji + box glyphs in the HTML it writes."""
        lineage = repo_root / "skills/shadow-frog-viewer/dream-lineage.py"
        out = tmp_path / "lineage.html"
        r = subprocess.run(
            [sys.executable, str(lineage), "-o", str(out),
             "--shadow-dir", str(coupon_demo_src / ".shadow")],
            capture_output=True, encoding="utf-8", env=_narrow_env(),
        )
        assert r.returncode == 0, f"stderr:\n{r.stderr}"
        # The U+1F438 frog must round-trip as UTF-8 bytes regardless of locale.
        assert b"\xf0\x9f\x90\xb8" in out.read_bytes()
