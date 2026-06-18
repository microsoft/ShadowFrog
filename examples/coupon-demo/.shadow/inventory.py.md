# Shadow: inventory.py

**Language**: Python | **Lines**: 15 | **Last modified**: 2026-04-20

## File-Level

- Imports get_coupon from cart, creating a dependency on cart's COUPON_CACHE. Any call to validate_coupon pollutes the shared cache as a side effect.
  _(verified, source: exploration)_
  Also involves: `cart.py::get_coupon`, `cart.py::COUPON_CACHE`

## `validate_coupon`

- Normalizes code to uppercase via `code.upper()` before lookup, but calculate_total does NOT — so a code that validates successfully may still produce no discount when passed directly to calculate_total.
  _(verified, source: exploration, labels: [bug])_
  Also involves: `cart.py::calculate_total`, `cart.py::load_coupon`
- Side effect: populates COUPON_CACHE with the uppercased code. Calling validate_coupon("save20") caches under key "SAVE20", but a later calculate_total("save20") looks up lowercase "save20" — a cache miss that then caches None under "save20".
  _(verified, source: exploration, labels: [bug])_
  Also involves: `cart.py::COUPON_CACHE`, `cart.py::get_coupon`
- Will crash with AttributeError if code is None (None has no .upper() method). No guard against non-string input.
  _(verified, source: exploration, labels: [bug])_
- Also crashes on any non-string type: int, list, dict all raise AttributeError on .upper(). Needs `isinstance(code, str)` guard.
  _(verified, source: exploration, labels: [security])_
  Dream report: `_dreams/20260420-142000Z-adversarial-inputs/`

## `apply_bulk_discount`

- Mutates items in-place — modifies the original dict objects' "price" keys. The caller's list is permanently altered. Returns the same list object (not a copy).
  _(verified, source: exploration)_
- Crashes with KeyError if items lack 'qty' key, and TypeError if 'qty' is a string. No input validation.
  _(verified, source: exploration, labels: [security])_
  Dream report: `_dreams/20260420-142000Z-adversarial-inputs/`
- Calling apply_bulk_discount twice on the same items compounds the discount: first call gives 0.90×, second gives 0.81×, third gives 0.729×. No idempotency guard.
  _(verified, source: exploration, labels: [bug])_
- Only applies discount to items with qty >= 5. Items with qty 4 or below are untouched, even if the total quantity across all items exceeds 5.
  _(verified, source: exploration)_
- Uses round(price * 0.90, 2) which can produce floating-point artifacts on certain prices. For example, 33.33 * 0.90 = 29.997 → rounds to 30.0, not 29.997.
  _(verified, source: exploration)_

## Cross-References

- [coupon-case-normalization-mismatch](_cross/coupon-case-normalization-mismatch.md)
  (involves `cart.py::calculate_total`, `inventory.py::validate_coupon`, `cart.py::load_coupon`)
- [global-coupon-cache-side-effects](_cross/global-coupon-cache-side-effects.md)
  (involves `cart.py::COUPON_CACHE`, `cart.py::get_coupon`, `inventory.py::validate_coupon`)
- [mutation-through-discount-pipeline](_cross/mutation-through-discount-pipeline.md)
  (involves `inventory.py::apply_bulk_discount`, `cart.py::calculate_total`, `test_cart.py::test_bulk_then_coupon`)
