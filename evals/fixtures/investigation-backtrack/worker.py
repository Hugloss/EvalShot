def execute(source, resolved):
    identity = source["artifact"].split(".")[0]
    return {"executed_identity": identity, "resolved_identity": resolved["identity"]}
