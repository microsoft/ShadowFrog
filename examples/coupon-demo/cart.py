COUPON_CACHE = {}


def load_coupon(code):
    """Simulate loading coupon from database."""
    coupons = {
        "SAVE20": {"discount": 0.20, "min_total": 50},
        "HALF": {"discount": 0.50, "min_total": 100},
    }
    return coupons.get(code)


def get_coupon(code):
    if code not in COUPON_CACHE:
        COUPON_CACHE[code] = load_coupon(code)
    return COUPON_CACHE[code]


def calculate_total(items, coupon_code=None):
    subtotal = sum(item["price"] * item["qty"] for item in items)

    if coupon_code:
        coupon = get_coupon(coupon_code)
        if coupon and subtotal >= coupon["min_total"]:
            subtotal -= subtotal * coupon["discount"]

    tax = subtotal * 0.08
    return round(subtotal + tax, 2)
