def handle(identity):
    if not identity:
        raise ValueError('empty identity')
    return identity
