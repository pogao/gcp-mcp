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
from collections import OrderedDict, defaultdict
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path
from typing import Any, Literal, NamedTuple, Protocol
from urllib.parse import urlparse

from googleapiclient.discovery_cache import DISCOVERY_DOC_DIR

from gcp_mcp.errors import UnknownMethod, UnknownService, UnknownVersion
from gcp_mcp.policy import Admitted, Excluded, Verdict, admit

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


def parse_service(doc: Mapping[str, Any]) -> ServiceSpec:
    service, version = doc["name"], doc["version"]
    base_url = doc["rootUrl"] + doc["servicePath"]
    schemas = doc.get("schemas", {})
    methods: dict[str, ReadMethod] = {}
    excluded: dict[str, str] = {}
    for raw in walk_methods(doc):
        name = raw["id"].partition(".")[2]
        verdict = judge(raw)
        if isinstance(verdict, Excluded):
            excluded[name] = verdict.reason
            continue
        params = _params(raw.get("parameters", {}), raw["path"])
        methods[name] = ReadMethod(
            service=service,
            version=version,
            name=name,
            http_method=raw["httpMethod"],
            base_url=base_url,
            path_template=raw["path"],
            params=params,
            accepts_body=verdict.accepts_body,
            body_schema=raw.get("request", {}).get("$ref"),
            response_schema=raw.get("response", {}).get("$ref"),
            pagination=_pagination(raw, schemas),
            description=raw.get("description", ""),
            scopes=tuple(raw.get("scopes", ())),
            deprecated=raw.get("deprecated", False),
        )
    return ServiceSpec(
        name=service,
        version=version,
        title=doc["title"],
        docs_url=doc.get("documentationLink", ""),
        base_url=base_url,
        global_params=_params(doc.get("parameters", {}), ""),
        methods=dict(sorted(methods.items())),
        excluded=dict(sorted(excluded.items())),
        schemas=schemas,
    )


def _params(raw: Mapping[str, Mapping[str, Any]], path_template: str) -> dict[str, Param]:
    reserved = set(re.findall(r"\{\+([^}]*)\}", path_template))
    return {
        name: Param(
            name=name,
            location=spec["location"],
            type=spec["type"],
            required=spec.get("required", False),
            repeated=spec.get("repeated", False),
            reserved=name in reserved,
            pattern=re.compile(spec["pattern"]) if "pattern" in spec else None,
            enum=tuple(spec["enum"]) if "enum" in spec else None,
            description=spec.get("description", ""),
        )
        for name, spec in raw.items()
    }


def _pagination(raw: Mapping[str, Any], schemas: Mapping[str, Any]) -> Pagination | None:
    def properties(role: str) -> Mapping[str, Any]:
        return schemas.get(raw.get(role, {}).get("$ref"), {}).get("properties", {})

    if "nextPageToken" not in properties("response"):
        return None
    params = raw.get("parameters", {})
    in_body = "pageToken" not in params
    source = properties("request") if in_body else params
    if "pageToken" not in source:
        return None
    size_param = next((size for size in ("pageSize", "maxResults") if size in source), None)
    size_max = source[size_param].get("maximum") if size_param else None
    return Pagination(
        token_param="pageToken",
        size_param=size_param,
        size_max=int(size_max) if size_max is not None else None,
        in_body=in_body,
    )


class _Scanned(NamedTuple):
    version: str
    reads: int
    title: str
    description: str
    docs_url: str


class Registry:
    def __init__(self, corpus: Corpus | None = None, *, cache_size: int = 16) -> None:
        self._corpus = corpus if corpus is not None else BundledCorpus()
        self._cache_size = cache_size
        self._catalog: dict[str, ServiceRef] | None = None
        self._specs: OrderedDict[tuple[str, str], ServiceSpec] = OrderedDict()

    def services(self, query: str | None = None) -> tuple[ServiceRef, ...]:
        refs = tuple(self._refs().values())
        if query is None:
            return refs
        needle = query.lower()
        return tuple(
            ref for ref in refs if needle in ref.name.lower() or needle in ref.title.lower()
        )

    def service(self, name: str, version: str | None = None) -> ServiceSpec:
        refs = self._refs()
        ref = refs.get(name)
        if ref is None:
            raise UnknownService(name, get_close_matches(name, refs, n=5))
        version = version or ref.default_version
        if version not in ref.versions:
            raise UnknownVersion(name, version, ref.versions)
        key = (name, version)
        spec = self._specs.get(key)
        if spec is None:
            spec = parse_service(self._corpus.load(name, version))
            self._specs[key] = spec
            if len(self._specs) > self._cache_size:
                self._specs.popitem(last=False)
        else:
            self._specs.move_to_end(key)
        return spec

    def method(self, service: str, name: str, version: str | None = None) -> ReadMethod:
        spec = self.service(service, version)
        method = spec.methods.get(name)
        if method is not None:
            return method
        reason = spec.excluded.get(name)
        suggestions = () if reason else get_close_matches(name, spec.methods, n=5)
        raise UnknownMethod(service, spec.version, name, suggestions, reason)

    def _refs(self) -> dict[str, ServiceRef]:
        if self._catalog is None:
            self._catalog = self._scan()
        return self._catalog

    def _scan(self) -> dict[str, ServiceRef]:
        found: defaultdict[str, list[_Scanned]] = defaultdict(list)
        for name, version in self._corpus.names():
            doc = self._corpus.load(name, version)
            if not is_gcp(doc):
                continue
            reads = sum(isinstance(judge(raw), Admitted) for raw in walk_methods(doc))
            found[name].append(
                _Scanned(
                    version,
                    reads,
                    doc["title"],
                    doc.get("description", ""),
                    doc.get("documentationLink", ""),
                )
            )
        return {name: _ref(name, found[name]) for name in sorted(found)}


def _ref(name: str, scanned: list[_Scanned]) -> ServiceRef:
    scanned.sort(key=lambda row: version_rank(row.version), reverse=True)
    candidates = [row for row in scanned if row.reads] or scanned
    best_band = version_rank(candidates[0].version)[0]
    default = max(
        (row for row in candidates if version_rank(row.version)[0] == best_band),
        key=lambda row: (row.reads, version_rank(row.version)),
    )
    return ServiceRef(
        name=name,
        title=default.title,
        description=default.description,
        docs_url=default.docs_url,
        default_version=default.version,
        versions=tuple(row.version for row in scanned),
        read_methods=default.reads,
    )
