import argparse
import difflib
import json
import sys
from collections.abc import Callable, Mapping
from functools import cache
from pathlib import Path

from gcp_mcp.discovery import Registry
from gcp_mcp.policy import Admitted, admit
from tests.corpus import bundled_methods

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "tests" / "golden"


@cache
def registry() -> Registry:
    return Registry()


def render_policy() -> str:
    admitted_post, refused_get = set(), set()
    for method in bundled_methods():
        admitted = isinstance(admit(*method), Admitted)
        if admitted and method.http_method == "POST":
            admitted_post.add(method.id)
        if not admitted and method.http_method == "GET":
            refused_get.add(method.id)
    golden = {"admitted_post": sorted(admitted_post), "refused_get": sorted(refused_get)}
    return json.dumps(golden, indent=2) + "\n"


def render_catalog() -> str:
    catalog = [
        {
            "name": ref.name,
            "default_version": ref.default_version,
            "versions": list(ref.versions),
            "read_methods": ref.read_methods,
        }
        for ref in registry().services()
    ]
    return json.dumps(catalog, indent=2) + "\n"


def render_read_methods() -> str:
    lines = sorted(
        f"{ref.name}.{name}"
        for ref in registry().services()
        for name in registry().service(ref.name).methods
    )
    return "\n".join(lines) + "\n"


TARGETS: Mapping[str, Callable[[], str]] = {
    "policy.json": render_policy,
    "catalog.json": render_catalog,
    "read_methods.txt": render_read_methods,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pin policy verdicts and the service catalog under tests/golden."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument("targets", nargs="*", metavar="target", choices=list(TARGETS))
    args = parser.parse_args(argv)
    targets = args.targets or list(TARGETS)
    stale = False
    for target in targets:
        current = TARGETS[target]()
        path = GOLDEN_DIR / target
        if args.write:
            path.write_text(current)
            continue
        pinned = path.read_text()
        if pinned == current:
            continue
        stale = True
        diff = difflib.unified_diff(
            pinned.splitlines(keepends=True),
            current.splitlines(keepends=True),
            f"tests/golden/{target}",
            "current",
        )
        sys.stderr.writelines(diff)
    if stale:
        print("Review the diff, then run: uv run python -m scripts.golden --write", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
