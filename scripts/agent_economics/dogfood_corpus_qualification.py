from __future__ import annotations

import json
import tempfile
from pathlib import Path

from .dogfood_corpus import DEFAULT_TASKS, default_corpus, write_corpus


def main() -> None:
    payload = default_corpus()
    assert payload["schema"]["version"] == 1
    assert len(payload["tasks"]) == 13
    assert [row["task_id"] for row in payload["tasks"]] == [
        row[0] for row in DEFAULT_TASKS
    ]
    assert len({row["fixture_identity"] for row in payload["tasks"]}) == 13
    assert payload["protocol"]["freeze_before_oracle"] is True
    assert payload["protocol"]["independent_qualification_during_repair_loop"] is False
    assert payload["protocol"]["scripts_may_repair_source"] is False
    with tempfile.TemporaryDirectory() as temp:
        target = Path(temp) / "corpus.json"
        first = write_corpus(target)
        second = json.loads(target.read_text(encoding="utf-8"))
        assert first == second
        assert default_corpus()["identity"] == first["identity"]
    print(
        json.dumps(
            {"status": "PASS", "tasks": 13, "identity": payload["identity"]},
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
