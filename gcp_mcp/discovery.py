"""Turn the bundled Google API discovery documents into a registry of GCP read methods.

google-api-python-client ships one discovery document per service version under
`googleapiclient.discovery_cache.documents`. Its `index.json` is stale and lists half of them,
so the catalog is derived from the documents and `index.json` is never read. The only
import from googleapiclient is the path of that directory.

Only `parse_service` constructs a `ReadMethod`, and only for methods `policy.admit` admits.
Holding a `ReadMethod` is proof that the read-only decision was made. Excluded methods are kept
by name with their reason so an error can say why a method is refused.

Nothing loads at import time. `Registry.services` scans the corpus once on first use and keeps
one `ServiceRef` per service. `Registry.service` parses one document on demand into a small LRU.
"""

import json
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

from googleapiclient.discovery_cache import DISCOVERY_DOC_DIR

from gcp_mcp.policy import Verdict, admit

CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

NOT_GCP: Mapping[str, str] = {
    "admin": "Workspace",
    "addressvalidation": "Maps Platform",
    "airquality": "Maps Platform",
    "areainsights": "Maps Platform",
    "places": "Maps Platform",
    "pollen": "Maps Platform",
    "solar": "Maps Platform",
    "playgrouping": "Play",
    "health": "consumer Google Health",
    "developerknowledge": "developer documentation",
    "fcm": "Firebase",
    "fcmdata": "Firebase",
    "firebase": "Firebase",
    "firebaseappcheck": "Firebase",
    "firebaseappdistribution": "Firebase",
    "firebaseapphosting": "Firebase",
    "firebasecrashlytics": "Firebase",
    "firebasedatabase": "Firebase",
    "firebasedataconnect": "Firebase",
    "firebasehosting": "Firebase",
    "firebaseml": "Firebase",
    "firebaserules": "Firebase",
    "firebasestorage": "Firebase",
}

VERSION_BANDS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"v\d+"), 5),
    (re.compile(r"v\d+beta\d*|v\d+b\d+|beta"), 4),
    (re.compile(r"v\d+p\d+beta\d*"), 3),
    (re.compile(r"v\d+alpha\d*|alpha"), 2),
    (re.compile(r"preview"), 1),
)


@dataclass(frozen=True, slots=True)
class Param:
    name: str
    location: Literal["path", "query"]
    type: str
    required: bool
    repeated: bool
    reserved: bool
    pattern: re.Pattern[str] | None
    enum: tuple[str, ...] | None
    description: str


@dataclass(frozen=True, slots=True)
class Pagination:
    token_param: str
    size_param: str | None
    size_max: int | None
    in_body: bool


@dataclass(frozen=True, slots=True)
class ReadMethod:
    service: str
    version: str
    name: str
    http_method: Literal["GET", "POST"]
    base_url: str
    path_template: str
    params: Mapping[str, Param]
    accepts_body: bool
    body_schema: str | None
    response_schema: str | None
    pagination: Pagination | None
    description: str
    scopes: tuple[str, ...]
    deprecated: bool


@dataclass(frozen=True, slots=True)
class ServiceSpec:
    name: str
    version: str
    title: str
    docs_url: str
    base_url: str
    global_params: Mapping[str, Param]
    methods: Mapping[str, ReadMethod]
    excluded: Mapping[str, str]
    schemas: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ServiceRef:
    name: str
    title: str
    description: str
    docs_url: str
    default_version: str
    versions: tuple[str, ...]
    read_methods: int


class Corpus(Protocol):
    def names(self) -> Iterable[tuple[str, str]]: ...

    def load(self, name: str, version: str) -> Mapping[str, Any]: ...


class BundledCorpus:
    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory or Path(DISCOVERY_DOC_DIR)

    def names(self) -> Iterator[tuple[str, str]]:
        for path in sorted(self._directory.glob("*.json")):
            if path.name != "index.json":
                name, _, version = path.stem.partition(".")
                yield name, version

    def load(self, name: str, version: str) -> Mapping[str, Any]:
        return json.loads((self._directory / f"{name}.{version}.json").read_bytes())


def is_gcp(doc: Mapping[str, Any]) -> bool:
    if doc["name"] in NOT_GCP:
        return False
    scopes = doc.get("auth", {}).get("oauth2", {}).get("scopes", {})
    host = urlparse(doc.get("documentationLink", "")).hostname or ""
    return CLOUD_PLATFORM_SCOPE in scopes or host.endswith("cloud.google.com")


def version_rank(version: str) -> tuple[int, ...]:
    band = next((rank for pattern, rank in VERSION_BANDS if pattern.fullmatch(version)), 0)
    return (band, *(int(number) for number in re.findall(r"\d+", version)))


def walk_methods(doc: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    nodes = [doc]
    while nodes:
        node = nodes.pop()
        nodes.extend(node.get("resources", {}).values())
        yield from node.get("methods", {}).values()


def judge(raw: Mapping[str, Any]) -> Verdict:
    return admit(
        raw["id"],
        raw["httpMethod"],
        "request" in raw,
        raw.get("response", {}).get("$ref"),
        raw["id"].rpartition(".")[0],
    )
