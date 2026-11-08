import argparse
import difflib
import json
import sys
from pathlib import Path

from gcp_mcp.policy import Admitted, admit
from tests.corpus import bundled_methods

GOLDEN = Path(__file__).resolve().parents[1] / "tests" / "golden" / "policy.json"


def render() -> str:
    admitted_post, refused_get = set(), set()
    for method in bundled_methods():
        admitted = isinstance(admit(*method), Admitted)
        if admitted and method.http_method == "POST":
            admitted_post.add(method.id)
        if not admitted and method.http_method == "GET":
            refused_get.add(method.id)
    golden = {"admitted_post": sorted(admitted_post), "refused_get": sorted(refused_get)}
    return json.dumps(golden, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pin policy verdicts in tests/golden/policy.json.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    current = render()
    if args.write:
        GOLDEN.write_text(current)
        return 0
    pinned = GOLDEN.read_text()
    if pinned == current:
        return 0
    diff = difflib.unified_diff(
        pinned.splitlines(keepends=True),
        current.splitlines(keepends=True),
        "tests/golden/policy.json",
        "current verdicts",
    )
    sys.stderr.writelines(diff)
    print("Review the diff, then run: uv run python -m scripts.golden --write", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
