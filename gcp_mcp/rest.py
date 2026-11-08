"""The one way this server talks to Google Cloud.

`prepare` is the only constructor of `PreparedRequest`, so every request passes its guards.
`{+name}` expansion passes `:` and `?` through unescaped, so without them `versions/1:access`
reaches the refused `access` method and `projects/p:setIamPolicy?x=` turns a POST `getIamPolicy`
into `setIamPolicy` with the caller's body. Firestore and Datastore reads also accept a body
that opens a read-write transaction, which `prepare` refuses.
"""

import json
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal

import uritemplate

from gcp_mcp.discovery import Param, ReadMethod
from gcp_mcp.errors import BadArguments

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
TRANSACTION_KEYS = frozenset({"newTransaction", "transaction", "transactionOptions"})
UNSAFE_IN_RESERVED = "?#%\\"
CUSTOM_METHOD = re.compile(r":[A-Za-z]+(?=/|$)")


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
    problems.extend(
        f"missing required {name!r}"
        for name, param in method.params.items()
        if param.required and args.get(name) is None
    )
    if unknown:
        accepted = ", ".join(sorted([*method.params, FIELDS.name]))
        problems.append(
            f"unknown {', '.join(map(repr, unknown))}; the accepted names are {accepted}"
        )
    content = dict(body) if body and method.accepts_body else None
    if body and not method.accepts_body:
        problems.append(f"{method.name} is a {method.http_method} method and takes no request body")
    if content is not None:
        problems.extend(_body_problems(method, content, schemas))
    if problems:
        raise BadArguments(method.service, method.name, problems)

    paging = method.pagination
    if paging is not None and paging.size_param is not None:
        size = limits.default_page_size
        if paging.size_max is not None:
            size = min(size, paging.size_max)
        if paging.in_body:
            content = dict(content or {})
            content.setdefault(paging.size_param, size)
        elif args.get(paging.size_param) is None:
            query.append((paging.size_param, str(size)))
    return PreparedRequest(
        method=method.http_method,
        url=method.base_url + uritemplate.expand(method.path_template, path),
        query=(*PLAIN_JSON, *sorted(query, key=lambda pair: pair[0])),
        body=None if content is None else json.dumps(content, separators=(",", ":")).encode(),
    )


def _texts(param: Param, value: object) -> tuple[list[str], list[str]]:
    if isinstance(value, list | tuple) and not param.repeated:
        return [], [f"{param.name!r} takes one value, not a list"]
    texts: list[str] = []
    problems: list[str] = []
    for item in value if isinstance(value, list | tuple) else [value]:
        convert, kind = COERCIONS.get(param.type, COERCIONS["string"])
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
    method: ReadMethod, body: Mapping[str, object], schemas: Mapping[str, Any]
) -> list[str]:
    properties = schemas.get(method.body_schema, {}).get("properties", {})
    problems = []
    if unknown := [key for key in body if key not in properties]:
        problems.append(
            f"body {', '.join(map(repr, unknown))} not in {method.body_schema}; "
            f"the accepted body fields are {', '.join(properties)}"
        )
    problems.extend(
        f"body key {key!r} opens a transaction, and this server only reads"
        for key in _transaction_keys(body, ())
    )
    return problems


def _transaction_keys(node: object, path: tuple[str, ...]) -> Iterator[str]:
    if isinstance(node, Mapping):
        for key, value in node.items():
            here = (*path, key)
            if key in TRANSACTION_KEYS:
                yield ".".join(here)
            yield from _transaction_keys(value, here)
    elif isinstance(node, list):
        for item in node:
            yield from _transaction_keys(item, path)


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
