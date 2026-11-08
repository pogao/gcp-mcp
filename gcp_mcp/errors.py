"""Errors the tool layer turns into messages for the model.

`for_llm` returns one or two sentences that name what was wrong and the next move. It never
carries credentials, inbound tokens, or stack traces. Only `tools.py` converts a `GcpMcpError`
into a fastmcp `ToolError`, so this module and everything that raises from it stay fastmcp-free.
"""

from collections.abc import Mapping, Sequence


class GcpMcpError(Exception):
    def for_llm(self) -> str:
        return f"{self}."


class UnknownService(GcpMcpError):
    def __init__(self, name: str, suggestions: Sequence[str]) -> None:
        super().__init__(f"unknown service {name!r}")
        self.name = name
        self.suggestions = tuple(suggestions)

    def for_llm(self) -> str:
        hint = (
            _closest(self.suggestions)
            if self.suggestions
            else "Call gcp_services to list the services this server knows."
        )
        return f"No Google Cloud service named {self.name!r}. {hint}"


class UnknownVersion(GcpMcpError):
    def __init__(self, service: str, version: str, versions: Sequence[str]) -> None:
        super().__init__(f"{service} has no version {version!r}")
        self.service = service
        self.version = version
        self.versions = tuple(versions)

    def for_llm(self) -> str:
        return (
            f"Service {self.service} has no bundled version {self.version!r}. "
            f"Available versions: {', '.join(self.versions)}."
        )


class UnknownMethod(GcpMcpError):
    def __init__(
        self,
        service: str,
        version: str,
        name: str,
        suggestions: Sequence[str],
        excluded_reason: str | None,
    ) -> None:
        super().__init__(f"{service} {version} has no read method {name!r}")
        self.service = service
        self.version = version
        self.name = name
        self.suggestions = tuple(suggestions)
        self.excluded_reason = excluded_reason

    def for_llm(self) -> str:
        where = f"{self.service} {self.version}"
        if self.excluded_reason is not None:
            return (
                f"{where} has {self.name}, but this server is read-only and refuses it: "
                f"{self.excluded_reason}."
            )
        hint = (
            _closest(self.suggestions)
            if self.suggestions
            else f"Call gcp_methods for {self.service} to list the read methods."
        )
        return f"{where} has no read method named {self.name!r}. {hint}"


class BadArguments(GcpMcpError):
    def __init__(self, service: str, method: str, problems: Sequence[str]) -> None:
        super().__init__(f"{service} {method}: {'; '.join(problems)}")
        self.service = service
        self.method = method
        self.problems = list(problems)

    def for_llm(self) -> str:
        return (
            f"The arguments for {self.service} {self.method} were rejected: "
            f"{'; '.join(self.problems)}. Call gcp_describe_method to see the parameters."
        )


class GcpApiError(GcpMcpError):
    def __init__(
        self,
        status: int,
        reason: str | None,
        message: str,
        details: Sequence[Mapping[str, object]] = (),
    ) -> None:
        super().__init__(f"HTTP {status}{f' {reason}' if reason else ''}: {message}")
        self.status = status
        self.reason = reason
        self.message = message
        self.details = tuple(details)

    def for_llm(self) -> str:
        return f"Google Cloud answered {self}."


class UnsupportedResponse(GcpMcpError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def for_llm(self) -> str:
        return f"The response cannot be relayed: {self.message}. This server returns JSON only."


def _closest(names: Sequence[str]) -> str:
    if len(names) == 1:
        return f"The closest name is {names[0]}."
    return f"The closest names are {', '.join(names)}."
