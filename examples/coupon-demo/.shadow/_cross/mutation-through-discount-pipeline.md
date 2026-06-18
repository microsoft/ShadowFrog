# Mutation through discount pipeline

**Category**: edge-case
**Refs**:
- `inventory.py::apply_bulk_discount`
- `cart.py::calculate_total`
- `test_cart.py::test_bulk_then_coupon`

**Discovery**: apply_bulk_discount mutates item dicts in-place (modifying "price" keys) and returns the same list object. When piped into calculate_total, the mutation is invisible — calculate_total sees already-reduced prices. But any code holding a reference to the original items list now sees the discounted prices permanently. Calling apply_bulk_discount multiple times compounds discounts (0.90^N multiplier). The test_bulk_then_coupon test avoids this by creating fresh items, but real usage with shared item references would silently corrupt prices.

_(verified, source: exploration, labels: [bug])_
