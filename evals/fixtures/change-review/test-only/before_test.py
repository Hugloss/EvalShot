from before import handle

def test_reject_empty_identity():
    try:
        handle('')
    except ValueError:
        return
    raise AssertionError('must reject empty identity')
