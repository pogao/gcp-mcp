import json
from functools import cache
from importlib.resources import files
from typing import NamedTuple

DOCUMENTS = files("googleapiclient") / "discovery_cache" / "documents"


class CorpusMethod(NamedTuple):
    id: str
    http_method: str
    has_request_body: bool
    response_schema: str | None
    resource: str


@cache
def bundled_methods() -> tuple[CorpusMethod, ...]:
    methods = []
    for document in sorted(DOCUMENTS.iterdir(), key=lambda path: path.name):
        if not document.name.endswith(".json") or document.name == "index.json":
            continue
        nodes = [json.loads(document.read_bytes())]
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
    return tuple(methods)
