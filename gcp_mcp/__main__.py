import logging
import sys

import structlog
from pydantic import ValidationError
from pydantic_settings import CliApp

from gcp_mcp.server import build_server, serve
from gcp_mcp.settings import Settings


def main(argv: list[str] | None = None) -> int:
    try:
        settings = CliApp.run(Settings, cli_args=argv)
    except ValidationError as error:
        print(invalid_settings(error), file=sys.stderr)
        return 2
    except SystemExit as status:
        return status.code or 0
    server = build_server(settings)
    configure_logging(settings.log_level)
    serve(server, settings.config())
    return 0


def invalid_settings(error: ValidationError) -> str:
    problems = (
        f"{'.'.join(str(part) for part in problem['loc'])}: {problem['msg']}"
        for problem in error.errors()
    )
    return "gcp-mcp: invalid settings: " + "; ".join(problems)


def configure_logging(level: str) -> None:
    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processor=structlog.processors.JSONRenderer(), foreign_pre_chain=shared
        )
    )
    logging.basicConfig(handlers=[handler], level=level, force=True)


if __name__ == "__main__":
    sys.exit(main())
