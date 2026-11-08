import re
from dataclasses import dataclass
from typing import Annotated, Literal, Self

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from pydantic import Field, field_validator, model_validator
from pydantic_core import PydanticCustomError
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

MCP_PATH = "/mcp"
SCOPE_TOKEN = re.compile(r"[\x21\x23-\x5b\x5d-\x7e]+")


@dataclass(frozen=True, slots=True)
class StdioConfig:
    pass


@dataclass(frozen=True, slots=True)
class AuthConfig:
    issuer: str
    jwks_uri: str | None
    public_key: str | None
    audience: str
    required_scopes: tuple[str, ...]
    allowed_subjects: tuple[str, ...]
    base_url: str


@dataclass(frozen=True, slots=True)
class HttpConfig:
    host: str
    port: int
    base_url: str | None
    auth: AuthConfig | None


ServerConfig = StdioConfig | HttpConfig


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GCP_MCP_",
        cli_prog_name="gcp-mcp",
        cli_kebab_case=True,
        cli_implicit_flags=True,
    )

    transport: Literal["stdio", "http"] = "stdio"
    host: str = "127.0.0.1"
    port: int = Field(8888, ge=1, le=65535)
    base_url: str | None = None
    require_auth: bool = False
    auth_issuer: str | None = None
    auth_jwks_uri: str | None = None
    auth_public_key: str | None = None
    auth_audience: str | None = None
    auth_required_scopes: Annotated[list[str], NoDecode] = []
    auth_allowed_subjects: Annotated[list[str], NoDecode] = []
    max_response_bytes: int = Field(100_000, ge=1)
    default_page_size: int = Field(100, ge=1)
    request_timeout_s: float = Field(60.0, gt=0)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    @field_validator("auth_required_scopes", "auth_allowed_subjects", mode="before")
    @classmethod
    def split_list(cls, value: object) -> object:
        if isinstance(value, str):
            return [item for part in value.split(",") if (item := part.strip())]
        return value

    @field_validator("auth_required_scopes")
    @classmethod
    def check_scopes(cls, scopes: list[str]) -> list[str]:
        for scope in scopes:
            if not SCOPE_TOKEN.fullmatch(scope):
                raise PydanticCustomError(
                    "scope_token",
                    "{scope} is not an RFC 6749 scope token. Give a comma-separated list "
                    "such as gcp:read,gcp:list",
                    {"scope": repr(scope)},
                )
        return scopes

    @field_validator("auth_allowed_subjects")
    @classmethod
    def check_subjects(cls, subjects: list[str]) -> list[str]:
        if "" in subjects:
            raise PydanticCustomError(
                "subject",
                "an allowed subject cannot be empty. Give a comma-separated list of sub or "
                "email claims",
            )
        return subjects

    @field_validator("auth_public_key")
    @classmethod
    def check_public_key(cls, value: str | None) -> str | None:
        if value and not is_rsa_public_key(value):
            raise PydanticCustomError(
                "public_key",
                "needs a PEM RSA public key. If you used --auth-public-key, set "
                "GCP_MCP_AUTH_PUBLIC_KEY instead, because the command line keeps only "
                "the first line of PEM text",
            )
        return value

    @model_validator(mode="after")
    def check_auth(self) -> Self:
        if problems := self.auth_problems():
            raise PydanticCustomError("auth_settings", "; ".join(problems))
        return self

    def auth_problems(self) -> list[str]:
        if not self.require_auth:
            stray = [
                name
                for name in type(self).model_fields
                if name.startswith("auth_") and getattr(self, name)
            ]
            if stray:
                return [f"auth settings are set but require_auth is false: {', '.join(stray)}"]
            return []
        problems = []
        if self.transport != "http":
            problems.append("require_auth needs transport=http")
        if not self.base_url:
            problems.append("require_auth needs base_url")
        if not self.auth_issuer:
            problems.append("require_auth needs auth_issuer")
        if bool(self.auth_jwks_uri) == bool(self.auth_public_key):
            problems.append("require_auth needs exactly one of auth_jwks_uri and auth_public_key")
        return problems

    def config(self) -> ServerConfig:
        if self.transport == "stdio":
            return StdioConfig()
        return HttpConfig(
            host=self.host, port=self.port, base_url=self.base_url, auth=self.auth_config()
        )

    def auth_config(self) -> AuthConfig | None:
        if not self.require_auth:
            return None
        resource = self.base_url.rstrip("/") + MCP_PATH
        return AuthConfig(
            issuer=self.auth_issuer,
            jwks_uri=self.auth_jwks_uri,
            public_key=self.auth_public_key,
            audience=self.auth_audience or resource,
            required_scopes=tuple(self.auth_required_scopes),
            allowed_subjects=tuple(self.auth_allowed_subjects),
            base_url=self.base_url,
        )


def is_rsa_public_key(text: str) -> bool:
    try:
        return isinstance(load_pem_public_key(text.encode()), RSAPublicKey)
    except (ValueError, UnsupportedAlgorithm):
        return False
