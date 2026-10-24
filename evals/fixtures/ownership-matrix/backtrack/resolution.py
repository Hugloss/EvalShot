def resolve(source):
    return {"canonical": source["manifest_id"]}

def execute(resolved, source):
    identity = source["archive_name"].split(".")[0]
    return {"used_identity": identity, "expected": resolved["canonical"]}

def run(source):
    resolved = resolve(source)
    return execute(resolved, source)
