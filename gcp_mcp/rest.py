"""Only `prepare` constructs a `PreparedRequest`, and `tests/test_layering.py` enforces it.

`{+name}` expansion passes `:` and `?` through unescaped, so without the `prepare` guards
`versions/1:access` reaches the refused `access` method and `projects/p:setIamPolicy?x=` turns a
POST `getIamPolicy` into `setIamPolicy` with the caller's body. Firestore and Datastore reads also
accept a body that opens a read-write transaction, which `prepare` refuses in any spelling Google's
JSON parsers accept.
"""

import asyncio
import json
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from http.client import responses
from typing import Any, Literal, Protocol

import google.auth
import google.auth.credentials
import httpx2
import uritemplate
from google.auth.exceptions import DefaultCredentialsError, RefreshError, TransportError
from google.auth.transport.requests import Request

from gcp_mcp.discovery import CLOUD_PLATFORM_SCOPE, Param, ReadMethod, Registry
from gcp_mcp.errors import (
    BadArguments,
    CredentialsUnavailable,
    GcpApiError,
    GcpUnreachable,
    UnsupportedResponse,
)
from gcp_mcp.policy import REDACTED_FIELDS

GLOBAL_PARAMS = frozenset(
    {
        "$.xgafv",
        "access_token",
        "alt",
        "bearer_token",
        "callback",
        "key",
        "oauth_token",
        "pp",
        "prettyPrint",
        "quotaUser",
        "uploadType",
        "upload_protocol",
        "userIp",
    }
)
FIELDS = Param(
    name="fields",
    location="query",
    type="string",
    required=False,
    repeated=False,
    reserved=False,
    pattern=None,
    enum=None,
    description="",
)
PLAIN_JSON = (("alt", "json"), ("prettyPrint", "false"))
TRANSACTION_KEYS = frozenset({"newtransaction", "transaction", "transactionoptions"})
UNSAFE_IN_RESERVED = "?#%\\"
CUSTOM_METHOD = re.compile(r":[A-Za-z]+(?=/|$)")
REDACTED = "[redacted]"


@dataclass(frozen=True, slots=True)
class Limits:
    default_page_size: int
    max_response_bytes: int
    request_timeout_s: float


@dataclass(frozen=True, slots=True)
class PreparedRequest:
    method: Literal["GET", "POST"]
    url: str
    query: tuple[tuple[str, str], ...]
    body: bytes | None


@dataclass(frozen=True, slots=True)
class CallResult:
    service: str
    version: str
    method: str
    response: Any
    next_page_token: str | None
    truncated: bool
    omitted_items: int
    redacted_fields: int


class CredentialSource(Protocol):
    async def headers(self) -> Mapping[str, str]: ...


class AdcCredentials:
    def __init__(self) -> None:
        self._credentials: google.auth.credentials.Credentials | None = None
        self._lock = asyncio.Lock()

    async def headers(self) -> Mapping[str, str]:
        async with self._lock:
            if self._credentials is None:
                self._credentials, _ = await asyncio.to_thread(
                    google.auth.default, scopes=[CLOUD_PLATFORM_SCOPE]
                )
            if not self._credentials.valid:
                await asyncio.to_thread(self._credentials.refresh, Request())
            headers: dict[str, str] = {}
            self._credentials.apply(headers)
        return headers


class StaticCredentials:
    def __init__(self, token: str) -> None:
        self._token = token

    async def headers(self) -> Mapping[str, str]:
        return {"authorization": f"Bearer {self._token}"}


@dataclass(frozen=True, slots=True)
class Gcp:
    registry: Registry
    limits: Limits
    credentials: CredentialSource
    http: httpx2.AsyncClient

    async def call(
        self,
        service: str,
        method: str,
        args: Mapping[str, object] | None = None,
        body: Mapping[str, object] | None = None,
        version: str | None = None,
    ) -> CallResult:
        read_method = self.registry.method(service, method, version)
        schemas = self.registry.service(service, read_method.version).schemas
        request = prepare(read_method, args or {}, body, self.limits, schemas)
        try:
            headers = dict(await self.credentials.headers())
        except (DefaultCredentialsError, RefreshError) as error:
            raise CredentialsUnavailable() from error
        except TransportError as error:
            problem = "failed on the network while refreshing the server's credentials"
            raise GcpUnreachable(read_method.service, problem) from error
        if request.body is not None:
            headers["content-type"] = "application/json"
        try:
            response = await self.http.request(
                request.method,
                request.url,
                params=request.query,
                content=request.body,
                headers=headers,
                timeout=self.limits.request_timeout_s,
            )
        except httpx2.TimeoutException as error:
            problem = "timed out before Google Cloud answered"
            raise GcpUnreachable(read_method.service, problem) from error
        except httpx2.TransportError as error:
            problem = "failed on the network before Google Cloud answered"
            raise GcpUnreachable(read_method.service, problem) from error
        if not response.is_success:
            raise parse_error(response.status_code, response.content)
        try:
            payload = json.loads(response.content)
        except ValueError:
            kind = response.headers.get("content-type", "untyped")
            raise UnsupportedResponse(f"the response is {kind}, not JSON") from None
        payload, redacted = redact(payload)
        payload, omitted = bound(payload, self.limits.max_response_bytes)
        token = payload.get("nextPageToken") if isinstance(payload, dict) else None
        return CallResult(
            service=read_method.service,
            version=read_method.version,
            method=read_method.name,
            response=payload,
            next_page_token=token if isinstance(token, str) else None,
            truncated=omitted > 0,
            omitted_items=omitted,
            redacted_fields=redacted,
        )


def make_gcp(limits: Limits) -> Gcp:
    return Gcp(Registry(), limits, AdcCredentials(), httpx2.AsyncClient())


def prepare(
    method: ReadMethod,
    args: Mapping[str, object],
    body: Mapping[str, object] | None,
    limits: Limits,
    schemas: Mapping[str, Any],
) -> PreparedRequest:
    problems: list[str] = []
    path: dict[str, str] = {}
    query: list[tuple[str, str]] = []
    unknown: list[str] = []
    for name, value in args.items():
        if value is None:
            continue
        if name in GLOBAL_PARAMS:
            problems.append(f"{name!r} is a global parameter; of those only 'fields' is accepted")
            continue
        param = method.params.get(name) or (FIELDS if name == "fields" else None)
        if param is None:
            unknown.append(name)
            continue
        texts, wrong = _texts(param, value)
        problems.extend(wrong)
        if param.location == "query":
            query.extend((name, text) for text in texts)
        elif texts:
            problems.extend(_path_problems(param, texts[0]))
            path[name] = texts[0]
    missing = [
        name for name, param in method.params.items() if param.required and args.get(name) is None
    ]
    problems.extend(f"missing required {name!r}" for name in missing)
    accepted = ", ".join(sorted([*method.params, FIELDS.name]))
    if unknown:
        problems.append(
            f"unknown {', '.join(map(repr, unknown))}; the accepted names are {accepted}"
        )
    elif missing:
        problems.append(f"the accepted names are {accepted}")
    content = None
    if body and not method.accepts_body:
        problems.append(f"{method.name} is a {method.http_method} method and takes no request body")
    elif body:
        content = dict(body)
        problems.extend(_body_problems(content, schemas.get(method.body_schema, {}), schemas, ""))
    if problems:
        raise BadArguments(method.service, method.name, problems)

    paging = method.pagination
    if paging is not None and paging.size_param is not None:
        size = limits.default_page_size
        if paging.size_max is not None:
            size = min(size, paging.size_max)
        if paging.in_body:
            content = dict(content or {})
            if all(_normalize(key) != _normalize(paging.size_param) for key in content):
                content[paging.size_param] = size
        elif args.get(paging.size_param) is None:
            query.append((paging.size_param, str(size)))
    return PreparedRequest(
        method=method.http_method,
        url=method.base_url + uritemplate.expand(method.path_template, path),
        query=(*PLAIN_JSON, *sorted(query, key=lambda pair: pair[0])),
        body=None if content is None else json.dumps(content, separators=(",", ":")).encode(),
    )


def redact(payload: Any) -> tuple[Any, int]:
    count = 0

    def walk(node: Any) -> Any:
        nonlocal count
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        clean = {}
        for key, value in node.items():
            if REDACTED_FIELDS.search(key) and isinstance(value, str | dict | list):
                clean[key] = REDACTED
                count += 1
            else:
                clean[key] = walk(value)
        return clean

    return walk(payload), count


def bound(payload: Any, max_bytes: int) -> tuple[Any, int]:
    encoded = _compact(payload)
    if len(encoded) <= max_bytes:
        return payload, 0
    payload = json.loads(encoded)
    size, omitted = len(encoded), 0
    lists = list(_lists(payload))
    while size > max_bytes and (largest := max(lists, key=len, default=None)):
        item = largest.pop()
        size -= len(_compact(item)) + (1 if largest else 0)
        omitted += 1
        if detached := {id(node) for node in _lists(item)}:
            lists = [node for node in lists if id(node) not in detached]
    return payload, omitted


def parse_error(status: int, body: bytes) -> GcpApiError:
    text = body.decode(errors="replace")
    error = _envelope(text)
    phrase = responses.get(status)
    if error is None:
        return GcpApiError(status, phrase, text[:200].strip() or phrase or "no response body")
    legacy = next(iter(error.get("errors") or ()), None)
    return GcpApiError(
        status,
        error.get("status") or (legacy.get("reason") if isinstance(legacy, dict) else None),
        str(error.get("message") or phrase or "no message"),
        tuple(detail for detail in error.get("details") or () if isinstance(detail, dict)),
    )


def _envelope(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    return error if isinstance(error, dict) else None


def _compact(payload: Any) -> str:
    return json.dumps(payload, separators=(",", ":"))


def _lists(node: Any) -> Iterator[list[Any]]:
    stack = [node]
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            yield node
            stack.extend(node)
        elif isinstance(node, dict):
            stack.extend(node.values())


def _texts(param: Param, value: object) -> tuple[list[str], list[str]]:
    if isinstance(value, list | tuple) and not param.repeated:
        return [], [f"{param.name!r} takes one value, not a list"]
    convert, kind = COERCIONS.get(param.type, COERCIONS["string"])
    texts: list[str] = []
    problems: list[str] = []
    for item in value if isinstance(value, list | tuple) else [value]:
        text = convert(item)
        if text is None:
            problems.append(f"{param.name!r} must be {kind}, not {item!r}")
        elif param.enum is not None and text not in param.enum:
            problems.append(f"{param.name!r} must be one of {', '.join(param.enum)}, not {text!r}")
        else:
            texts.append(text)
    return texts, problems


def _path_problems(param: Param, value: str) -> list[str]:
    name = repr(param.name)
    if not param.reserved:
        return [f"{name} cannot be empty, '.', or '..'"] if value in ("", ".", "..") else []
    if not value:
        return [f"{name} is empty"]
    problems = []
    if param.pattern is not None and not param.pattern.fullmatch(value):
        problems.append(f"{name} must match the pattern {param.pattern.pattern}")
    if value.startswith("/"):
        problems.append(f"{name} must not start with '/'")
    problems.extend(
        f"{name} must not contain {char!r}" for char in UNSAFE_IN_RESERVED if char in value
    )
    dots = sorted({segment for segment in value.split("/") if segment in (".", "..")})
    problems.extend(f"{name} must not contain a {segment!r} segment" for segment in dots)
    problems.extend(
        f"{name} must not end a segment with a custom method suffix such as {suffix!r}"
        for suffix in CUSTOM_METHOD.findall(value)
    )
    return problems


def _body_problems(
    node: object, schema: Mapping[str, Any], schemas: Mapping[str, Any], path: str
) -> Iterator[str]:
    seen: set[str] = set()
    while (ref := schema.get("$ref")) and ref not in seen:
        seen.add(ref)
        schema = schemas.get(ref, {})
    if isinstance(node, list):
        for index, item in enumerate(node):
            yield from _body_problems(item, schema.get("items", {}), schemas, f"{path}[{index}]")
        return
    if not isinstance(node, Mapping):
        return
    properties = schema.get("properties")
    fields = {_normalize(name): field for name, field in (properties or {}).items()}
    unknown = []
    for key, value in node.items():
        here = f"{path}.{key}" if path else key
        if _normalize(key) in TRANSACTION_KEYS:
            yield f"body key {here!r} opens a transaction, and this server only reads"
        field = fields.get(_normalize(key), schema.get("additionalProperties"))
        if field is None and properties is not None:
            unknown.append(here)
        yield from _body_problems(value, field or {}, schemas, here)
    if unknown:
        where = schema.get("id", "the request schema")
        yield (
            f"body {', '.join(map(repr, unknown))} not in {where}; "
            f"the accepted body fields are {', '.join(properties)}"
        )


def _normalize(name: str) -> str:
    return name.replace("_", "").lower()


def _string(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _integer(value: object) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value):
        return value
    return None


def _number(value: object) -> str | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]+(\.[0-9]+)?([eE][-+]?[0-9]+)?", value):
        return value
    return None


def _boolean(value: object) -> str | None:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower()
    return None


COERCIONS: Mapping[str, tuple[Callable[[object], str | None], str]] = {
    "string": (_string, "a string"),
    "integer": (_integer, "an integer"),
    "number": (_number, "a number"),
    "boolean": (_boolean, "a boolean"),
}
