import json
import sys

from .graph import order_services


def main():
    with open(sys.argv[1], encoding="utf-8") as stream:
        print(json.dumps(order_services(json.load(stream))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
