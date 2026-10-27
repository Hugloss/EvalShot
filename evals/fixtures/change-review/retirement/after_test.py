from after import charge

def test_reject_excess_discount():
    try:
        charge(5, 9)
    except ValueError:
        return
    raise AssertionError('must reject excess discount')
