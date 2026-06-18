---
dream_id: "20260420-141000Z-bulk-min-total-interaction"
category: investigation
verdict: useful
base_commit: 69de9194d8d30d980a41fa0e1bace8fc76d82e24
branch: "dream/coupon-demo/20260420-141000Z-bulk-min-total-interaction"
parent_branch: "main"
remote: "origin"
related_symbols:
  - "inventory.py::apply_bulk_discount"
  - "cart.py::calculate_total"
builds_on: []
---

# Bulk Discount vs Coupon min_total Interaction

## Motivation

The shadow contains an **uncertain** discovery on `cart.py::calculate_total`:
> "Coupon min_total check uses the pre-discount subtotal. If bulk discount reduces items below min_total, the coupon still applies because the check sees the already-reduced subtotal from apply_bulk_discount, not the original price."

This is contradictory — it says "pre-discount subtotal" but then says it "sees the already-reduced subtotal". The experiment resolves which interpretation is correct.

## Hypothesis

`calculate_total` computes subtotal from `items` as-is. Since `apply_bulk_discount` mutates item prices in-place BEFORE `calculate_total` runs, the subtotal seen by `calculate_total` IS the post-bulk-discount value. Therefore, if bulk discount drops the subtotal below `min_total`, the coupon will NOT apply.

## Implementation

2 scenarios testing the HALF coupon (min_total=100) and SAVE20 (min_total=50):
1. **Key test**: Bulk discount drops subtotal from $105 to $94.50 (below min_total=100, HALF coupon)
2. Boundary test: SAVE20 subtotal just barely above min_total ($50.05) after bulk discount

## Commands Run

```
$ python dream_exp2_bulk_min_total.py
Exit code: 0
All scenarios passed.
```

Key findings:
- Scenario 1: Original=$105, post-bulk=$94.50. Total WITH coupon = total WITHOUT coupon. **Coupon did NOT apply.**
- Scenario 2: Post-bulk=$50.05 (just above $50). Coupon applied correctly.

## Evaluation

The uncertain discovery is **incorrect in its conclusion**. The truth is:
- `calculate_total` sees whatever prices are in the `items` dicts at call time
- Since `apply_bulk_discount` mutates prices in-place, `calculate_total` sees post-bulk prices
- The min_total check correctly evaluates against the actual (post-mutation) subtotal
- If bulk discount drops subtotal below min_total, the coupon is correctly rejected

The boundary test (scenario 2) confirms the `>=` comparison works precisely.

## Takeaways

- The uncertain discovery should be **refuted** and replaced with the correct behavior
- The mutation-based pipeline actually produces correct min_total behavior — but only by accident. If someone refactored `apply_bulk_discount` to return new items instead of mutating, the behavior would change.
- The real risk is that the correctness depends on an implicit contract: "apply_bulk_discount must be called before calculate_total, and must mutate in place"

## Verdict Details

Useful: Resolved an uncertain discovery to refuted, and identified that the correct behavior depends on an implicit mutation contract rather than explicit design.
