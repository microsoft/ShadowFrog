from cart import get_coupon


def validate_coupon(code):
    """Check if a coupon code is valid. Returns True/False."""
    coupon = get_coupon(code.upper())
    return coupon is not None


def apply_bulk_discount(items):
    """Apply 10% discount if buying 5+ of any single item."""
    for item in items:
        if item["qty"] >= 5:
            item["price"] = round(item["price"] * 0.90, 2)
    return items
