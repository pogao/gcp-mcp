import itertools
import re
import typing
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping

import proto
from fastmcp.exceptions import ToolError
from google.cloud import compute_v1

from gcp.utils import handle_gcp_exceptions

READ_METHOD_NAMES = frozenset({"get", "list", "aggregated_list"})
READ_METHOD_PREFIXES = ("get_", "list_")


@dataclass(frozen=True)
class ReadMethod:
    name: str
    request_type: type
    required: tuple[str, ...]
    optional: tuple[str, ...]


@dataclass(frozen=True)
class ResourceType:
    name: str
    client_name: str
    methods: Mapping[str, ReadMethod]


def _is_read_method(client: type, name: str) -> bool:
    # A `_unary` name, or a `<name>_unary` twin, means the call starts a
    # long-running Operation.
    return (
        (name in READ_METHOD_NAMES or name.startswith(READ_METHOD_PREFIXES))
        and not name.endswith("_unary")
        and not hasattr(client, f"{name}_unary")
        and "request" in typing.get_type_hints(getattr(client, name))
    )


def _read_method(client: type, name: str) -> ReadMethod:
    hint = typing.get_type_hints(getattr(client, name))["request"]
    request_type = next(
        arg
        for arg in typing.get_args(hint)
        if isinstance(arg, type) and issubclass(arg, proto.Message)
    )
    fields = request_type.meta.fields
    return ReadMethod(
        name=name,
        request_type=request_type,
        required=tuple(f for f, field in fields.items() if not field.optional),
        optional=tuple(f for f, field in fields.items() if field.optional),
    )


def _resource_type_name(client_name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", client_name.removesuffix("Client")).lower()


def _build_registry() -> Mapping[str, ResourceType]:
    registry = {}
    for client_name in sorted(dir(compute_v1)):
        if not client_name.endswith("Client"):
            continue
        client = getattr(compute_v1, client_name)
        methods = {
            name: _read_method(client, name)
            for name in sorted(dir(client))
            if not name.startswith("_") and _is_read_method(client, name)
        }
        if methods:
            name = _resource_type_name(client_name)
            registry[name] = ResourceType(
                name=name,
                client_name=client_name,
                methods=MappingProxyType(methods),
            )
    return MappingProxyType(registry)


RESOURCE_TYPES = _build_registry()


def _to_dict(message: proto.Message) -> dict:
    return type(message).to_dict(
        message, preserving_proto_field_name=False, use_integers_for_enums=False
    )


def _pager_items(pager) -> typing.Iterator[dict]:
    for item in pager:
        if isinstance(item, tuple):
            scope, scoped_list = item
            (items_field,) = (
                f for f in type(scoped_list).meta.fields if f != "warning"
            )
            for scoped_item in getattr(scoped_list, items_field):
                yield {**_to_dict(scoped_item), "scope": scope}
        else:
            yield _to_dict(item)


def _choices(values) -> str:
    return ", ".join(sorted(values))


@handle_gcp_exceptions
def call_read_method(
    resource_type: str, method: str, params: dict, limit: int | None = None
) -> Any:
    rt = RESOURCE_TYPES.get(resource_type)
    if rt is None:
        raise ToolError(
            f"Unknown resource type '{resource_type}'. "
            f"Valid choices: {_choices(RESOURCE_TYPES)}"
        )
    read_method = rt.methods.get(method)
    if read_method is None:
        raise ToolError(
            f"'{method}' is not a read method of '{resource_type}'. "
            f"Valid choices: {_choices(rt.methods)}"
        )
    allowed = read_method.required + read_method.optional
    unknown = set(params) - set(allowed)
    if unknown:
        raise ToolError(
            f"Unknown parameters for {resource_type}.{method}: {_choices(unknown)}. "
            f"Allowed: {_choices(allowed)}"
        )
    missing = set(read_method.required) - set(params)
    if missing:
        raise ToolError(
            f"Missing required parameters for {resource_type}.{method}: "
            f"{_choices(missing)}"
        )
    try:
        request = read_method.request_type(**params)
    except (TypeError, ValueError) as e:
        raise ToolError(f"Invalid parameters for {resource_type}.{method}: {e}") from e

    with getattr(compute_v1, rt.client_name)() as client:
        result = getattr(client, method)(request=request)
        if isinstance(result, proto.Message):
            return _to_dict(result)
        return list(itertools.islice(_pager_items(result), limit))
