from import_owner import resolve_import


def observes_filesystem_import(module: str, *, precise: bool) -> bool:
    binding = resolve_import(module, precise=precise)
    # BUG: assumes ordinary imports are only interesting while "unknown".
    if binding.kind != "unknown":
        return False
    # This also duplicates part of the module identity decision.
    return module == "pathlib"


def before_after_refinement() -> tuple[bool, bool]:
    return (
        observes_filesystem_import("pathlib", precise=False),
        observes_filesystem_import("pathlib", precise=True),
    )
    # Actual pair: (True, False); required observation: (True, True).
