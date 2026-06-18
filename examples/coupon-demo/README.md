# Coupon Demo — what a real `.shadow/` looks like

A tiny, self-contained example of what ShadowFrog's `.shadow/` knowledge
base looks like on a real (very small) codebase. **Not** an eval — the
systematic eval lives in [`eval/`](../../eval/). This is here so you can
see the shape of the artifact ShadowFrog produces before installing
anything yourself.

## The codebase

Three files, ~75 lines total:

- **`cart.py`** — `load_coupon()` defines coupons (uppercase keys),
  `get_coupon()` caches them, `calculate_total()` applies a discount and
  8 % tax.
- **`inventory.py`** — `validate_coupon()` checks existence (calls
  `.upper()` before lookup), `apply_bulk_discount()` mutates item prices
  in place when qty ≥ 5.
- **`test_cart.py`** — three happy-path tests using `assert` (no pytest).

These three files contain several real defects (case-normalization
mismatch, cache poisoning, missing input validation, in-place mutation
contracts). All of them appear in `.shadow/` as `verified` discoveries
labeled `bug`, `security`, or `performance`.

## What's in `.shadow/`

```
.shadow/
├── _index.md                    Top-level summary (files / symbols / counts)
├── _prefs.md                    Project preferences (empty in this demo)
├── _meta/state.json             Tracking state (HEAD sha, last update, totals)
├── cart.py.md                   Per-symbol discoveries for cart.py
├── inventory.py.md              Per-symbol discoveries for inventory.py
├── test_cart.py.md              Per-symbol discoveries for test_cart.py
├── _cross/                      Cross-cutting discoveries (3 files)
│   ├── coupon-case-normalization-mismatch.md
│   ├── global-coupon-cache-side-effects.md
│   └── mutation-through-discount-pipeline.md
└── _dreams/                     Dream experiment archive (3 experiments)
    ├── _index.md
    ├── 20260420-140000Z-cache-poison-sequence/
    │   ├── report.md            Narrative + verdict
    │   ├── manifest.json        Machine-readable discoveries
    │   └── patch.diff           Demonstration script (vs. base_commit)
    ├── 20260420-141000Z-bulk-min-total-interaction/
    └── 20260420-142000Z-adversarial-inputs/
```

## Exploring it

Use the viewer to inspect the shadow exactly the way an installed agent
would:

```bash
# Summary across the whole shadow
python3 ../../skills/shadow-frog-viewer/shadow-viewer.py --summary

# Actionable discoveries for a single file (what the preToolUse hook
# inlines before edits to cart.py)
python3 ../../skills/shadow-frog-viewer/shadow-viewer.py --top cart.py

# All discoveries labeled `bug`
python3 ../../skills/shadow-frog-viewer/shadow-viewer.py --labels bug
```

Or just open `.shadow/cart.py.md` in your editor — every per-symbol
discovery is a plain markdown bullet.

## Where the real eval lives

See [`eval/README.md`](../../eval/README.md) and
[`eval/results_dashboard.html`](../../eval/results_dashboard.html) for
the systematic SWE-Smith eval (100 bugs × 5 models × ablations). That is
the source of truth for "does the shadow help"; this folder exists only
to show what the shadow itself looks like.
