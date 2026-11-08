import json
from collections.abc import Iterator, Mapping
from functools import cache
from importlib.resources import files
from typing import Any, NamedTuple

DOCUMENTS = files("googleapiclient") / "discovery_cache" / "documents"


class CorpusMethod(NamedTuple):
    id: str
    http_method: str
    has_request_body: bool
    response_schema: str | None
    resource: str


def bundled_documents() -> Iterator[tuple[Mapping[str, Any], tuple[CorpusMethod, ...]]]:
    """Yield each bundled document's schemas with its methods, one document at a time."""
    for document in sorted(DOCUMENTS.iterdir(), key=lambda path: path.name):
        if not document.name.endswith(".json") or document.name == "index.json":
            continue
        parsed = json.loads(document.read_bytes())
        methods = []
        nodes = [parsed]
        while nodes:
            node = nodes.pop()
            nodes.extend(node.get("resources", {}).values())
            for raw in node.get("methods", {}).values():
                methods.append(
                    CorpusMethod(
                        id=raw["id"],
                        http_method=raw["httpMethod"],
                        has_request_body="request" in raw,
                        response_schema=raw.get("response", {}).get("$ref"),
                        resource=raw["id"].rpartition(".")[0],
                    )
                )
        yield parsed.get("schemas", {}), tuple(methods)


@cache
def bundled_methods() -> tuple[CorpusMethod, ...]:
    return tuple(method for _, methods in bundled_documents() for method in methods)
