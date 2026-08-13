from wink.services.progress import _trend_label

def test_trend_label():
    assert _trend_label(5, 2) == "up"
    assert _trend_label(1, 5) == "down"
    assert _trend_label(3, 3) == "steady"
    assert _trend_label(1, 0) == "up"
