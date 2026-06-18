from cart import calculate_total


def test_basic_total():
    items = [{"name": "Widget", "price": 25.00, "qty": 3}]
    total = calculate_total(items)
    assert total == 81.00, f"Expected 81.00, got {total}"


def test_coupon():
    items = [{"name": "Widget", "price": 25.00, "qty": 3}]
    total = calculate_total(items, coupon_code="SAVE20")
    assert total == 64.80, f"Expected 64.80, got {total}"


def test_bulk_then_coupon():
    from inventory import apply_bulk_discount
    items = [{"name": "Widget", "price": 25.00, "qty": 5}]
    items = apply_bulk_discount(items)
    total = calculate_total(items, coupon_code="HALF")
    assert total == 60.75, f"Expected 60.75, got {total}"


if __name__ == "__main__":
    test_basic_total()
    print("pass: test_basic_total")
    test_coupon()
    print("pass: test_coupon")
    test_bulk_then_coupon()
    print("pass: test_bulk_then_coupon")
    print("All tests passed!")
