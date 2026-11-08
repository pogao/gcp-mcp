from dataclasses import dataclass
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True, slots=True)
class StdioConfig:
    pass


@dataclass(frozen=True, slots=True)
class HttpConfig:
    host: str
    port: int
    base_url: str | None


ServerConfig = StdioConfig | HttpConfig


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GCP_MCP_", cli_prog_name="gcp-mcp", cli_kebab_case=True
    )

    transport: Literal["stdio", "http"] = "stdio"
    host: str = "127.0.0.1"
    port: int = Field(8888, ge=1, le=65535)
    base_url: str | None = None
    max_response_bytes: int = Field(100_000, ge=1)
    default_page_size: int = Field(100, ge=1)
    request_timeout_s: float = Field(60.0, gt=0)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    def config(self) -> ServerConfig:
        if self.transport == "stdio":
            return StdioConfig()
        return HttpConfig(host=self.host, port=self.port, base_url=self.base_url)
