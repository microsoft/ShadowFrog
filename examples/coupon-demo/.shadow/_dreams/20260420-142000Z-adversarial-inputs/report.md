---
dream_id: "20260420-142000Z-adversarial-inputs"
category: security audit
verdict: useful
base_commit: 69de9194d8d30d980a41fa0e1bace8fc76d82e24
branch: "dream/coupon-demo/20260420-142000Z-adversarial-inputs"
parent_branch: "main"
remote: "origin"
related_symbols:
  - "cart.py::calculate_total"
  - "inventory.py::validate_coupon"
  - "inventory.py::apply_bulk_discount"
  - "cart.py::get_coupon"
builds_on: []
---

# Adversarial Input Audit

## Motivation

No function in the codebase validates input types or required keys. This experiment systematically tests what happens with adversarial, malformed, and edge-case inputs across all public functions.

## Hypothesis

Functions will crash with unhandled exceptions (AttributeError, KeyError, TypeError) on non-standard inputs, and will silently accept nonsensical values (negative prices, zero quantities) without error.

## Implementation

8 scripted checks sampling 5 adversarial categories:
1. **Coupon code type confusion**: None, int, list passed to validate_coupon
2. **Items with missing keys**: calculate_total missing 'price', apply_bulk_discount missing 'qty'
3. **Negative values**: calculate_total with negative price and negative quantity
4. **Non-string coupon codes and cache effects**: True, 42 as coupon codes — what gets cached?

(The broader surface — string/None numeric types, non-dict items, extreme
values — was additionally mapped by code inspection; the script exercises a
representative subset.)

## Commands Run

```
$ python dream_exp3_adversarial.py
Exit code: 0
Results: 5 expected crashes, 3 silent acceptances
```

### Scripted crashes (5):
- `validate_coupon(None)` → AttributeError (no .upper() on None)
- `validate_coupon(123)` → AttributeError (no .upper() on int)
- `validate_coupon(['SAVE20'])` → AttributeError (no .upper() on list)
- `calculate_total` with missing 'price' → KeyError
- `apply_bulk_discount` with missing 'qty' → KeyError

### Scripted silent acceptance (3):
- `calculate_total` with price=-50, qty=1 → -54.0 (negative total!)
- `calculate_total` with price=50, qty=-1 → -54.0 (negative total!)
- `get_coupon(True)` and `get_coupon(42)` cache `{True: None, 42: None}` — non-string keys pollute the cache

## Evaluation

The codebase has zero input validation. Every function trusts its caller completely:
- **validate_coupon**: crashes on any non-string input
- **calculate_total**: crashes on malformed items, silently accepts nonsensical numeric values
- **apply_bulk_discount**: crashes on missing/wrong-type keys
- **COUPON_CACHE**: accepts any hashable type as key, permanently caching garbage entries

The negative-price acceptance is particularly concerning — a cart with negative items produces negative totals (money owed TO the customer).

## Takeaways

- `validate_coupon` needs a type guard: `if not isinstance(code, str): return False`
- `calculate_total` needs item validation or at minimum a try/except with a clear error
- Non-string coupon codes silently pass the `if coupon_code:` truthiness check, hit the cache, and cache garbage — the guard should be `if isinstance(coupon_code, str) and coupon_code:`
- `apply_bulk_discount` with negative qty (<5, so no discount applied) is "correct" by accident — negative qty 5 should probably be rejected
- Empty items list returning 0.0 is actually reasonable behavior

## Verdict Details

Useful: Mapped the input-validation surface across all 4 public functions — 5 scripted crash vectors and 3 scripted silent-acceptance vectors, plus additional vectors identified by inspection. New discoveries for validate_coupon (type crashes), calculate_total (non-string coupons), and get_coupon/COUPON_CACHE (non-string key pollution).
