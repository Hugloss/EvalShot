def charge(total, discount, allow_negative=False):
    if discount > total and not allow_negative:
        raise ValueError('discount exceeds total')
    return total - discount
