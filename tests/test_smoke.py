"""Smoke test verifying the shared test infrastructure works.

If this test fails, no other test in the suite will be reliable —
fix the conftest fixtures first.
"""
import subprocess


def test_repo_root_resolves(repo_root):
    assert (repo_root / "claude.md").is_file()
    assert (repo_root / "skills").is_dir()


def test_all_script_fixtures_load(
    shadow_init, shadow_viewer, dream_reconcile, dream_validate,
    dream_coverage, dream_lineage, meditate_repair,
):
    for mod, expected_attr in [
        (shadow_init, "main"),
        (shadow_viewer, "main"),
        (dream_reconcile, "main"),
        (dream_validate, "main"),
        (dream_coverage, "main"),
        (dream_lineage, "parse_args"),
        (meditate_repair, "main"),
    ]:
        assert hasattr(mod, expected_attr), \
            f"{mod.__name__} missing expected attribute {expected_attr!r}"


def test_coupon_demo_src_intact(coupon_demo_src):
    assert (coupon_demo_src / ".shadow" / "_index.md").is_file()
    assert (coupon_demo_src / "cart.py").is_file()


def test_coupon_demo_copy_is_independent(coupon_demo, coupon_demo_src):
    assert coupon_demo != coupon_demo_src
    assert (coupon_demo / ".shadow" / "_index.md").is_file()
    # Mutate the copy and confirm the source is untouched.
    (coupon_demo / "cart.py").write_text("# mutated by test\n")
    assert (coupon_demo_src / "cart.py").read_text() != "# mutated by test\n"


def test_coupon_demo_is_git_repo(coupon_demo):
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=coupon_demo,
        capture_output=True, text=True, check=True,
    )
    assert len(out.stdout.strip()) == 40


def test_tmp_git_repo_is_empty(tmp_git_repo):
    assert tmp_git_repo.is_dir()
    assert (tmp_git_repo / ".git").is_dir()
    # No commits yet
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_git_repo,
        capture_output=True, text=True,
    )
    assert result.returncode != 0  # HEAD doesn't exist
