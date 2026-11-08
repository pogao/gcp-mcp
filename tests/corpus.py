from collections.abc import Iterator, Mapping
from functools import cache
from typing import Any, NamedTuple

from gcp_mcp.discovery import BundledCorpus, walk_methods


class CorpusMethod(NamedTuple):
    id: str
    http_method: str
    has_request_body: bool
    response_schema: str | None
    resource: str


def bundled_documents() -> Iterator[tuple[Mapping[str, Any], tuple[CorpusMethod, ...]]]:
    """Yield each bundled document's schemas with its methods, one document at a time."""
    corpus = BundledCorpus()
    for name, version in corpus.names():
        document = corpus.load(name, version)
        methods = tuple(
            CorpusMethod(
                id=raw["id"],
                http_method=raw["httpMethod"],
                has_request_body="request" in raw,
                response_schema=raw.get("response", {}).get("$ref"),
                resource=raw["id"].rpartition(".")[0],
            )
            for raw in walk_methods(document)
        )
        yield document.get("schemas", {}), methods


@cache
def bundled_methods() -> tuple[CorpusMethod, ...]:
    return tuple(method for _, methods in bundled_documents() for method in methods)
