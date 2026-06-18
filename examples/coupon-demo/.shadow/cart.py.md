# Shadow: cart.py

**Language**: Python | **Lines**: 28 | **Last modified**: 2026-04-20

## File-Level

- COUPON_CACHE is a module-level mutable global dict shared across all importers — any module that imports from cart.py shares the same cache instance, causing cross-module state pollution.
  _(verified, source: exploration)_
  Also involves: `inventory.py::validate_coupon`

## `COUPON_CACHE`

- Never cleared or evicted — grows monotonically for the lifetime of the process. In a long-running server, every unique coupon code ever queried remains cached forever.
  _(verified, source: exploration, labels: [performance])_
- Caches None for invalid codes. Once an invalid code is looked up, the None result is permanently cached, preventing any future lookup even if the underlying data changes.
  _(verified, source: exploration, labels: [bug])_
- Case-variant lookups create duplicate cache entries for the same logical coupon. validate_coupon("save20") caches "SAVE20" → valid, then calculate_total("save20") caches "save20" → None. Cache grows 2× faster with mixed-case usage.
  _(verified, source: exploration, labels: [bug, performance])_
  Dream report: `_dreams/20260420-140000Z-cache-poison-sequence/`

## `load_coupon`

- Case-sensitive lookup against uppercase keys ("SAVE20", "HALF"). Passing lowercase (e.g., "save20") returns None even though the coupon conceptually exists.
  _(verified, source: exploration)_
- Returns None for unknown codes (via dict.get default), not an exception. Callers must handle None.
  _(verified, source: exploration)_

## `get_coupon`

- The `if code not in COUPON_CACHE` guard means each code is loaded exactly once per process. But because None is a valid cached value, invalid codes are also "loaded once" and permanently considered invalid.
  _(verified, source: exploration, labels: [bug])_
  Also involves: `cart.py::COUPON_CACHE`, `cart.py::load_coupon`
- Accepts any hashable type as key (True, 42, lists-as-errors). Non-string keys permanently cache None entries that can never resolve to valid coupons — silent cache pollution.
  _(verified, source: exploration, labels: [security])_
  Dream report: `_dreams/20260420-142000Z-adversarial-inputs/`
  Also involves: `cart.py::COUPON_CACHE`

## `calculate_total`

- Does NOT normalize coupon_code to uppercase before lookup. Lowercase codes silently produce no discount (coupon returns None from cache or load_coupon). This contradicts validate_coupon which does normalize.
  _(verified, source: exploration, labels: [bug])_
  Also involves: `inventory.py::validate_coupon`
- `if coupon_code:` is falsy for empty string "", None, 0, and False — all skip coupon lookup silently. No distinction between "no coupon" and "invalid coupon".
  _(verified, source: exploration)_
- Accepts negative prices and quantities without validation. Negative subtotals still have 8% tax applied, producing negative totals (e.g., price=-10, qty=1 → total=-10.80).
  _(verified, source: exploration, labels: [bug])_
- Tax rate (0.08 = 8%) is hardcoded with no configuration mechanism. Changing tax requires editing source code.
  _(verified, source: exploration, labels: [tech-debt])_
- Coupon min_total check uses the actual subtotal computed from items at call time. Since apply_bulk_discount mutates prices in-place before calculate_total runs, the min_total check sees post-bulk-discount prices. If bulk discount drops subtotal below min_total, the coupon is correctly rejected.
  _(verified, source: exploration)_
  Dream report: `_dreams/20260420-141000Z-bulk-min-total-interaction/`
  Also involves: `inventory.py::apply_bulk_discount`
- Non-string coupon_code values (True, 42, etc.) pass the `if coupon_code:` truthiness check, reach get_coupon, cache None under the non-string key, and silently produce no discount. No type validation exists.
  _(verified, source: exploration, labels: [security])_
  Dream report: `_dreams/20260420-142000Z-adversarial-inputs/`
  Also involves: `cart.py::get_coupon`, `cart.py::COUPON_CACHE`

## Cross-References

- [coupon-case-normalization-mismatch](_cross/coupon-case-normalization-mismatch.md)
  (involves `cart.py::calculate_total`, `inventory.py::validate_coupon`, `cart.py::load_coupon`)
- [global-coupon-cache-side-effects](_cross/global-coupon-cache-side-effects.md)
  (involves `cart.py::COUPON_CACHE`, `cart.py::get_coupon`, `inventory.py::validate_coupon`)
- [mutation-through-discount-pipeline](_cross/mutation-through-discount-pipeline.md)
  (involves `inventory.py::apply_bulk_discount`, `cart.py::calculate_total`, `test_cart.py::test_bulk_then_coupon`)
