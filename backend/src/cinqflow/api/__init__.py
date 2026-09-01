"""The HTTP edge. The OpenAPI document this produces is the UI's contract."""

from cinqflow.api.app import API_PREFIX, create_app

__all__ = ["API_PREFIX", "create_app"]
