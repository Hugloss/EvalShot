from after import handle

def test_valid_identity():
    assert handle('ok') == 'ok'
