from before import charge

def test_legacy_option():
    assert charge(5, 9, allow_negative=True) == -4
