# Coupon case normalization mismatch

**Category**: edge-case
**Refs**:
- `inventory.py::validate_coupon`
- `cart.py::calculate_total`
- `cart.py::load_coupon`

**Discovery**: validate_coupon normalizes coupon codes to uppercase via code.upper() before lookup, but calculate_total passes coupon_code directly to get_coupon without normalization. A user who validates "save20" (returns True) and then passes "save20" to calculate_total gets no discount — the coupon silently fails because load_coupon's keys are uppercase. This creates a validate-then-use inconsistency where validated codes don't work.

_(verified, source: exploration, labels: [bug])_
