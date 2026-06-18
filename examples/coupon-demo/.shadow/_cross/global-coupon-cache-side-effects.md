# Global coupon cache side effects

**Category**: behavior
**Refs**:
- `cart.py::COUPON_CACHE`
- `cart.py::get_coupon`
- `inventory.py::validate_coupon`
- `test_cart.py::test_coupon`

**Discovery**: COUPON_CACHE is a module-level global dict shared by all importers. Any call to get_coupon (directly or via validate_coupon) permanently populates the cache, including caching None for invalid codes. In tests, cache entries from one test persist into the next — there is no reset mechanism. validate_coupon caches under the uppercased key, while calculate_total would cache under the original-case key, so a single logical coupon code can produce two separate cache entries ("SAVE20" and "save20") with different values.

_(verified, source: exploration, labels: [bug])_
