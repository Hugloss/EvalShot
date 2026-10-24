from common import accepted_identity

def restore(source):
    identity = source["artifact"].split(".")[0]
    return accepted_identity(identity)
