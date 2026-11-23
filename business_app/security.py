"""Small security primitives shared by customer and developer web paths."""
import hmac


def constant_time_text_equal(left, right):
    """Compare text values in constant time without the ASCII-only str restriction."""
    if not isinstance(left, str) or not isinstance(right, str):
        return False
    try:
        left_bytes = left.encode("utf-8", errors="strict")
        right_bytes = right.encode("utf-8", errors="strict")
    except UnicodeError:
        return False
    return hmac.compare_digest(left_bytes, right_bytes)
