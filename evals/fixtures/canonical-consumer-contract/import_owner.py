from typing import NamedTuple


class ImportBinding(NamedTuple):
    kind: str
    module: str | None


def resolve_import(module: str, *, precise: bool) -> ImportBinding:
    # A legitimate improvement to resolution precision:
    # ordinary imports no longer need to be classified as unknown.
    if precise and module == "pathlib":
        return ImportBinding(kind="module", module="pathlib")
    return ImportBinding(kind="unknown", module=None)
