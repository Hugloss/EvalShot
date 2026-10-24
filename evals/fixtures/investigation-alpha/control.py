from source import source_identity

def verify(source, resolved):
    if source_identity(source) != resolved:
        raise ValueError("identity mismatch")
    return resolved
