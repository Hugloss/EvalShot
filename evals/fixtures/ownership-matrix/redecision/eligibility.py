def admitted(config):
    return config["enabled"] and not config["blocked"]

def warning(config):
    if config["enabled"] and not config["blocked"]:
        return "ready"
    return "blocked"

def start(config):
    if admitted(config):
        return {"started": True, "warning": warning(config)}
    return {"started": False, "warning": warning(config)}
