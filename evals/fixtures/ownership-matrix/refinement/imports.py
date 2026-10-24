def resolve_import(name, precise):
    if precise and name == "pathlib":
        return {"kind": "module", "name": name}
    return {"kind": "unknown", "name": name}

def observes_filesystem(binding):
    if binding["kind"] != "unknown":
        return False
    return binding["name"] == "pathlib"

def evaluate(precise):
    return observes_filesystem(resolve_import("pathlib", precise))
