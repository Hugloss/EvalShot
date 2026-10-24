def resolve(source):
    return {"canonical": source["manifest_id"]}

def verify(resolved, source):
    if source["manifest_id"] != resolved["canonical"]:
        raise ValueError("identity changed")
    return True

def execute(resolved, source):
    verify(resolved, source)
    return {"accepted_identity": resolved["canonical"]}

def run(source):
    return execute(resolve(source), source)
