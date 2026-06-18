"""Tests for skills/shadow-frog-dream/dream-coverage.py.

The coverage script computes "is this source file shadowed?" stats for
a repo's tracked files, plus fan-in heuristics for prioritization.
Tests exercise the four pure-ish helpers plus a CLI smoke run against
the coupon-demo (which has a stable, committed `.shadow/` tree).
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = REPO_ROOT / "skills" / "shadow-frog-dream" / "dream-coverage.py"


def _git_env(home: Path) -> dict:
    return {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    }


def _seed_tree(repo: Path, files: dict[str, str]):
    """Write `files` (rel-path → content) into `repo`, commit, return SHA."""
    env = _git_env(repo)
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=repo, env=env, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=repo, env=env, check=True)


# ===========================================================================
# get_source_files — exclusion + scoping
# ===========================================================================

def test_get_source_files_matches_init_selection(dream_coverage, tmp_git_repo):
    """Coverage's source set mirrors shadow-init.py exactly: tests are
    INCLUDED (init shadows them); only EXCLUDE_DIRS components and minified/
    map/lock suffixes are dropped."""
    _seed_tree(tmp_git_repo, {
        "src/main.py": "print()\n",
        "src/util.py": "x\n",
        "tests/test_main.py": "x\n",   # tests INCLUDED (init shadows them)
        "test_util.py": "x\n",          # test_ prefix INCLUDED
        "util_test.py": "x\n",          # _test suffix INCLUDED
        "node_modules/x.js": "x\n",     # node_modules/ excluded
        "vendor/lib.py": "x\n",         # vendor/ excluded
        "dist/bundle.js": "x\n",        # dist/ excluded
        "src/app.min.js": "x\n",        # .min.js excluded
        "yarn.lock": "x\n",             # .lock excluded (but not a source ext anyway)
    })
    files = dream_coverage.get_source_files(str(tmp_git_repo))
    # Included (init shadows all of these):
    for good in ["src/main.py", "src/util.py", "tests/test_main.py",
                 "test_util.py", "util_test.py"]:
        assert good in files, f"{good!r} should be included (matches init)"
    # Excluded (EXCLUDE_DIRS / minified):
    for bad in ["node_modules/x.js", "vendor/lib.py", "dist/bundle.js",
                "src/app.min.js"]:
        assert bad not in files, f"{bad!r} should be excluded"


def test_get_source_files_excludes_non_source_files(dream_coverage, tmp_git_repo):
    """B17: docs/images/data files are tracked but NOT shadowed by init, so
    they must not inflate coverage's denominator."""
    _seed_tree(tmp_git_repo, {
        "src/main.py": "print()\n",
        "README.md": "# docs\n",          # markdown — not source
        "docs/guide.rst": "guide\n",       # rst — not source
        "assets/logo.png": "binary\n",     # image — not source
        "LICENSE": "MIT\n",                # no extension, not a basename
        "data.csv": "a,b\n",               # data — not source
    })
    files = dream_coverage.get_source_files(str(tmp_git_repo))
    assert "src/main.py" in files
    for bad in ["README.md", "docs/guide.rst", "assets/logo.png",
                "LICENSE", "data.csv"]:
        assert bad not in files, f"{bad!r} should not be counted as source"


@pytest.mark.parametrize("rel,is_source", [
    ("src/a.py", True),
    ("a.ts", True),
    ("Makefile", True),
    ("svc/Dockerfile", True),
    ("README.md", False),
    ("notes.txt", False),
    ("logo.png", False),
    ("LICENSE", False),
])
def test_is_source_file(dream_coverage, rel, is_source):
    assert dream_coverage._is_source_file(rel) is is_source


def test_get_source_files_filters_by_scope(dream_coverage, tmp_git_repo):
    _seed_tree(tmp_git_repo, {
        "src/auth/login.py": "x\n",
        "src/auth/token.py": "x\n",
        "src/db/conn.py": "x\n",
        "src/util.py": "x\n",
    })
    scoped = dream_coverage.get_source_files(
        str(tmp_git_repo), scopes=["src/auth/"]
    )
    assert "src/auth/login.py" in scoped
    assert "src/auth/token.py" in scoped
    assert "src/db/conn.py" not in scoped
    assert "src/util.py" not in scoped


def test_get_source_files_multiple_scopes(dream_coverage, tmp_git_repo):
    _seed_tree(tmp_git_repo, {
        "src/auth/login.py": "x\n",
        "src/db/conn.py": "x\n",
        "src/util.py": "x\n",
    })
    scoped = dream_coverage.get_source_files(
        str(tmp_git_repo), scopes=["src/auth/", "src/db/"]
    )
    assert set(scoped) == {"src/auth/login.py", "src/db/conn.py"}


def test_get_source_files_empty_scopes_returns_all(dream_coverage, tmp_git_repo):
    _seed_tree(tmp_git_repo, {"a.py": "x\n", "b.py": "x\n"})
    files = dream_coverage.get_source_files(str(tmp_git_repo), scopes=[])
    assert set(files) == {"a.py", "b.py"}


# ===========================================================================
# _count_discoveries — identical shape to dream-reconcile's version
# ===========================================================================

def test_count_discoveries_excludes_cross_references(dream_coverage, tmp_path):
    shadow = tmp_path / "foo.md"
    shadow.write_text(
        "## File-Level\n\n"
        "- fl one\n"
        "- fl two\n"
        "\n"
        "## `foo`\n\n"
        "- sym one\n"
        "- sym two\n"
        "\n"
        "## Cross-References\n\n"
        "- [bp one](_cross/a.md)\n"
        "- [bp two](_cross/b.md)\n"
    )
    assert dream_coverage._count_discoveries(str(shadow)) == 4


def test_count_discoveries_handles_lowercase_xref(dream_coverage, tmp_path):
    shadow = tmp_path / "foo.md"
    shadow.write_text(
        "## `foo`\n\n- real\n\n## cross-references\n\n- [bp](_cross/x.md)\n"
    )
    assert dream_coverage._count_discoveries(str(shadow)) == 1


def test_count_discoveries_matches_coupon_demo(dream_coverage, coupon_demo):
    """Same totals as committed _index.md: cart=14, inventory=10, test_cart=9."""
    assert dream_coverage._count_discoveries(
        str(coupon_demo / ".shadow" / "cart.py.md")) == 14
    assert dream_coverage._count_discoveries(
        str(coupon_demo / ".shadow" / "inventory.py.md")) == 10
    assert dream_coverage._count_discoveries(
        str(coupon_demo / ".shadow" / "test_cart.py.md")) == 9


# ===========================================================================
# check_coverage
# ===========================================================================

def test_check_coverage_classifies_files(dream_coverage, tmp_git_repo):
    # Three source files; one fully covered, one placeholder-only, one no shadow.
    _seed_tree(tmp_git_repo, {
        "a.py": "x\n",
        "b.py": "x\n",
        "c.py": "x\n",
    })
    (tmp_git_repo / ".shadow").mkdir()
    (tmp_git_repo / ".shadow" / "a.py.md").write_text(
        "## `a`\n\n- one\n- two\n"
    )
    (tmp_git_repo / ".shadow" / "b.py.md").write_text(
        "## `b`\n\n_No discoveries yet._\n"
    )
    # c.py: no shadow at all.

    files = dream_coverage.get_source_files(str(tmp_git_repo))
    covered, uncovered, saturated = dream_coverage.check_coverage(
        str(tmp_git_repo), files
    )
    covered_paths = {f for f, _ in covered}
    assert "a.py" in covered_paths
    assert "b.py" in uncovered
    assert "c.py" in uncovered
    assert saturated == []  # 2 discoveries < 8


def test_check_coverage_saturated_threshold(dream_coverage, tmp_git_repo):
    _seed_tree(tmp_git_repo, {"big.py": "x\n"})
    (tmp_git_repo / ".shadow").mkdir()
    (tmp_git_repo / ".shadow" / "big.py.md").write_text(
        "## `big`\n\n" + "\n".join(f"- discovery {i}" for i in range(8)) + "\n"
    )
    files = dream_coverage.get_source_files(str(tmp_git_repo))
    covered, uncovered, saturated = dream_coverage.check_coverage(
        str(tmp_git_repo), files
    )
    assert ("big.py", 8) in saturated


def test_check_coverage_against_coupon_demo(dream_coverage, coupon_demo):
    """All 3 known source files are covered (cart, inventory, test_cart)."""
    files = dream_coverage.get_source_files(str(coupon_demo))
    covered, uncovered, saturated = dream_coverage.check_coverage(
        str(coupon_demo), files
    )
    covered_paths = {f for f, _ in covered}
    # All 3 are shadowed by init, so all 3 must be in the coverage denominator.
    assert "cart.py" in covered_paths
    assert "inventory.py" in covered_paths
    assert "test_cart.py" in covered_paths


# ===========================================================================
# compute_fan_in
# ===========================================================================

def test_compute_fan_in_counts_cross_file_references(dream_coverage, tmp_git_repo):
    # `git grep -F -l <basename>` counts files whose CONTENT mentions the
    # basename — so put the literal string into multiple files.
    _seed_tree(tmp_git_repo, {
        "src/auth.py": "# auth module\ndef login(): pass\n",  # mentions auth
        "src/api.py": "from src.auth import login\n",          # mentions auth
        "src/admin.py": "import src.auth as a\n",              # mentions auth
        "src/unrelated.py": "x = 1\n",
    })
    uncovered = ["src/auth.py"]
    fan = dream_coverage.compute_fan_in(str(tmp_git_repo), uncovered)
    # `git grep -F -l auth` should match auth.py + api.py + admin.py = 3.
    assert fan["src/auth.py"] == 3


def test_compute_fan_in_returns_zero_for_unreferenced(dream_coverage, tmp_git_repo):
    _seed_tree(tmp_git_repo, {
        "src/lonely.py": "x = 1\n",
        "src/other.py": "y = 2\n",
    })
    fan = dream_coverage.compute_fan_in(str(tmp_git_repo), ["src/lonely.py"])
    assert fan["src/lonely.py"] == 0


def test_compute_fan_in_empty_input(dream_coverage, tmp_git_repo):
    assert dream_coverage.compute_fan_in(str(tmp_git_repo), []) == {}


def test_compute_fan_in_respects_max_files_cap(dream_coverage, tmp_git_repo):
    """Files beyond the cap are not measured."""
    _seed_tree(tmp_git_repo, {
        "src/a.py": "alpha\n",
        "src/b.py": "beta\n",
        "src/c.py": "gamma\n",
    })
    uncovered = ["src/a.py", "src/b.py", "src/c.py"]
    fan = dream_coverage.compute_fan_in(str(tmp_git_repo), uncovered, max_files=1)
    # Only a.py was measured.
    assert "src/a.py" in fan
    assert "src/b.py" not in fan
    assert "src/c.py" not in fan


# ===========================================================================
# CLI smoke
# ===========================================================================

@pytest.mark.slow
@pytest.mark.integration
def test_cli_help_exits_zero():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "Dream coverage" in result.stdout or "coverage" in result.stdout.lower()


@pytest.mark.slow
@pytest.mark.integration
def test_cli_runs_against_coupon_demo(coupon_demo):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(coupon_demo)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "EXPLORATION COVERAGE MAP" in result.stdout
    assert "Total source files:" in result.stdout
    assert "Covered:" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_cli_scope_with_no_matches_exits_nonzero(coupon_demo):
    """Empty scope match is a hard exit-code-2 so typos in --scope are visible."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(coupon_demo),
         "--scope", "no-such-prefix/"],
        capture_output=True, text=True,
    )
    assert result.returncode == 2
    assert "No source files matched" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_cli_scope_filters_to_subset(coupon_demo):
    """A scope that matches at least one file → exits 0 with scoped totals."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(coupon_demo), "--scope", "cart.py"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Total source files: 1" in result.stdout
    assert "cart.py" in result.stdout
