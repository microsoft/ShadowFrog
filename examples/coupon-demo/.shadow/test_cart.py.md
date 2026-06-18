# Shadow: test_cart.py

**Language**: Python | **Lines**: 31 | **Last modified**: 2026-04-20

## File-Level

- Uses a manual `if __name__ == "__main__"` test runner, not pytest or unittest. Tests are plain functions with assert statements and no setup/teardown.
  _(verified, source: exploration)_
- No test isolation: COUPON_CACHE is a shared global. test_coupon populates the cache with "SAVE20", and test_bulk_then_coupon populates it with "HALF". If test order changes or tests are rerun in the same process, cached values persist from earlier tests.
  _(verified, source: exploration, labels: [bug])_
  Also involves: `cart.py::COUPON_CACHE`
- No negative-path tests: no test for invalid coupon codes, negative prices, empty items, or the case-sensitivity mismatch between validate_coupon and calculate_total.
  _(verified, source: exploration, labels: [feature-gap])_

## `test_basic_total`

- Verifies: 25.00 × 3 = 75.00 subtotal + 8% tax = 81.00. Tests the simplest happy path with no coupon.
  _(verified, source: exploration)_

## `test_coupon`

- Verifies SAVE20 on 75.00 subtotal: 75.00 − 20% = 60.00 + 4.80 tax = 64.80. The 75.00 subtotal exceeds SAVE20's min_total of 50.
  _(verified, source: exploration)_
- Only tests with uppercase coupon code "SAVE20". Does not test lowercase, revealing nothing about the case-normalization bug.
  _(verified, source: exploration)_
  Also involves: `cart.py::calculate_total`

## `test_bulk_then_coupon`

- Tests the full pipeline: apply_bulk_discount mutates items (25.00 → 22.50), then calculate_total applies HALF coupon on 112.50 subtotal → 56.25 + 4.50 tax = 60.75.
  _(verified, source: exploration)_
- Creates fresh items list, avoiding the mutation-persistence issue. But if this test's items object were reused in a subsequent test, prices would already be 22.50, not 25.00.
  _(verified, source: exploration)_
  Also involves: `inventory.py::apply_bulk_discount`, `cart.py::calculate_total`
- Imports apply_bulk_discount inside the function body (lazy import), unlike the module-level import of calculate_total. This is inconsistent but functionally irrelevant.
  _(verified, source: exploration, labels: [tech-debt])_

## Cross-References

- [global-coupon-cache-side-effects](_cross/global-coupon-cache-side-effects.md)
  (involves `cart.py::COUPON_CACHE`, `cart.py::get_coupon`, `inventory.py::validate_coupon`)
- [mutation-through-discount-pipeline](_cross/mutation-through-discount-pipeline.md)
  (involves `inventory.py::apply_bulk_discount`, `cart.py::calculate_total`, `test_cart.py::test_bulk_then_coupon`)
