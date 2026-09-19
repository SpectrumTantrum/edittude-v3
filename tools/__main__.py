"""Call the portable tools without importing a particular agent harness."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools import get_tools


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", nargs="?", default="list", help="list or a tool name")
    parser.add_argument("arguments", nargs="?", default="{}", help="JSON object of tool arguments")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args()
    registered = {tool.__name__: tool for tool in get_tools(args.workspace)}
    if args.name == "list":
        result = [{"name": name, "description": tool.__doc__} for name, tool in registered.items()]
    else:
        if args.name not in registered:
            parser.error(f"Unknown tool: {args.name}")
        try:
            values = json.loads(args.arguments)
            if not isinstance(values, dict):
                raise ValueError("Tool arguments must be a JSON object")
            result = registered[args.name](**values)
        except (ValueError, TypeError) as exc:
            parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return int(isinstance(result, dict) and result.get("status") in {"error", "unavailable"})


if __name__ == "__main__":
    raise SystemExit(main())
