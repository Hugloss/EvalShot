def manifest_identity(source):
    return source["manifest_id"]

def recovery_identity(source):
    return source["archive_name"].split(".")[0]

def publish(source, route):
    if route == "release":
        return store_accepted_identity(manifest_identity(source))
    return store_accepted_identity(recovery_identity(source))

def store_accepted_identity(value):
    return {"accepted_identity": value}
