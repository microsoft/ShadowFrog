---
dream_id: "20260420-140000Z-cache-poison-sequence"
category: bug hunting
verdict: useful
base_commit: 69de9194d8d30d980a41fa0e1bace8fc76d82e24
branch: "dream/coupon-demo/20260420-140000Z-cache-poison-sequence"
parent_branch: "main"
remote: "origin"
related_symbols:
  - "cart.py::COUPON_CACHE"
  - "cart.py::get_coupon"
  - "cart.py::calculate_total"
  - "inventory.py::validate_coupon"
builds_on: []
---

# Cache Poisoning via Validate-Then-Calculate Sequence

## Motivation

The shadow documents a case-normalization mismatch between `validate_coupon` (uppercases) and `calculate_total` (does not). This experiment quantifies the exact runtime behavior: what gets cached, in what order, and how the cache ends up with contradictory entries for the same logical coupon code.

## Hypothesis

Calling `validate_coupon("save20")` then `calculate_total(items, coupon_code="save20")` will produce TWO separate cache entries — `"SAVE20"` → valid coupon and `"save20"` → None — and the user gets no discount despite a successful validation.

## Implementation

Wrote 3 scenarios testing:
1. validate → calculate (lowercase)
2. calculate → validate (reverse order)
3. Both uppercase (control)

Each scenario resets `COUPON_CACHE` between runs and checks cache state, return values, and final totals.

## Commands Run

```
$ python dream_exp1_cache_poison.py
Exit code: 0
All 3 scenarios passed.
```

Key output:
- Scenario 1: Cache has 2 entries after validate+calculate. `SAVE20` → valid, `save20` → None. Total = $81.00 (no discount).
- Scenario 2: Same result in reverse order. Both paths create independent cache entries.
- Scenario 3: Uppercase only → 1 cache entry, total = $64.80 (discount applied correctly).

## Evaluation

All assertions passed. The cache poisoning is deterministic and order-independent:
- `validate_coupon` always caches under the uppercased key
- `calculate_total` always caches under the original-case key
- These are separate cache slots, so one doesn't prevent the other
- A user who validates "save20" (True) and passes it to calculate_total gets zero discount

The dual-entry cache pollution also means the cache grows 2× faster for case-variant lookups.

## Takeaways

- The root cause is `calculate_total` not calling `.upper()` on `coupon_code`. A one-line fix (`coupon_code = coupon_code.upper()` at the top of `calculate_total`) would resolve both the normalization mismatch and the cache pollution.
- The cache poisoning is invisible — no error, no warning. The discount silently vanishes. This is the worst kind of bug: it looks like it works.
- Order of operations doesn't matter — both paths poison independently.

## Verdict Details

Useful: Confirmed the exact cache state across validate-first and calculate-first orderings, proving the bug is deterministic and silent. The shadow already documented this bug, but this experiment adds concrete proof with cache snapshots.
