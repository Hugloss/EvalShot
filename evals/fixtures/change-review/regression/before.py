def charge(total, discount):
    if discount > total:
        raise ValueError('discount exceeds total')
    return total - discount
