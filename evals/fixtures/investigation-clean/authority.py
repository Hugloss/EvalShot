def identity(source):
    return source["manifest"]

def verify(source, resolved):
    if source["manifest"] != resolved:
        raise ValueError("identity changed")
    return True

def publish(source):
    resolved = identity(source)
    verify(source, resolved)
    return {"accepted": resolved}
