"""Base API client with shared functionality."""

import os
from typing import Any, Dict, Optional

import requests


class APIError(Exception):
    """Base exception for API client errors."""


class BaseAPIClient:
    """Base class for external API clients with common configuration."""

    error_class = APIError
    env_prefix = ''  # Subclasses set this (e.g., 'BENCHLING', 'GENEWIZ')

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.base_url = os.environ.get(f'{self.env_prefix}_API_URL', self.default_url).rstrip('/')
        self.api_key = os.environ.get(f'{self.env_prefix}_API_KEY')
        self.timeout = int(os.environ.get(f'{self.env_prefix}_TIMEOUT', '10'))
        if not self.api_key:
            raise self.error_class(f'Missing {self.env_prefix}_API_KEY environment variable')

    @property
    def default_url(self) -> str:
        raise NotImplementedError

    def _request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Make an API request with standard error handling."""
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        kwargs.setdefault('timeout', self.timeout)
        kwargs.setdefault('headers', self._headers())

        try:
            response = getattr(self.session, method)(url, **kwargs)
        except requests.RequestException as exc:
            raise self.error_class(f'{self.env_prefix} request failed: {exc}') from exc

        if response.status_code >= 400:
            raise self.error_class(f'{self.env_prefix} rejected request ({response.status_code}): {response.text}')

        try:
            return response.json()
        except ValueError as exc:
            raise self.error_class(f'{self.env_prefix} returned invalid JSON: {exc}') from exc

    def _headers(self) -> Dict[str, str]:
        """Return request headers. Override in subclasses for custom auth."""
        return {'Content-Type': 'application/json', 'Accept': 'application/json'}
