import json
from pathlib import Path
import sys

from .policy import compile_policy


def main():
    config = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    policy = compile_policy(config['rules'], default=config.get('default', 'deny'))
    for line in Path(sys.argv[2]).read_text(encoding='utf-8').splitlines():
        print(json.dumps(policy.decide(line)))


if __name__ == '__main__':
    main()
