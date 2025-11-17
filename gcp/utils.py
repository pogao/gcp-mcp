import functools
from google.api_core import exceptions
import structlog

logger = structlog.get_logger(__name__)


def handle_gcp_exceptions(func):
    """A decorator to handle common GCP API exceptions."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            logger.info(
                "executing_gcp_logic", function=func.__name__, args=args, kwargs=kwargs
            )
            return func(*args, **kwargs)
        except exceptions.NotFound as e:
            logger.error(
                "gcp_resource_not_found",
                function=func.__name__,
                error=str(e),
                exc_info=True,
            )
            return [] if "list" in func.__name__ or "unsafe" in func.__name__ else {}
        except exceptions.PermissionDenied as e:
            logger.error(
                "gcp_permissions_denied",
                function=func.__name__,
                error=str(e),
                exc_info=True,
            )
            return [] if "list" in func.__name__ or "unsafe" in func.__name__ else {}

    return wrapper
