from common import accepted_identity
from source import source_identity

def publish_release(source):
    identity = source_identity(source)
    return accepted_identity(identity)
