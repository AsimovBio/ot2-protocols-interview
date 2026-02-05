"""GeneWiz API client for sequencing orders."""

import json
from typing import Any, Dict

from .base import APIError, BaseAPIClient


class GeneWizError(APIError):
    """Raised when GeneWiz operations fail."""


class GeneWizClient(BaseAPIClient):
    """Client for placing orders with GeneWiz."""

    error_class = GeneWizError
    env_prefix = 'GENEWIZ'
    default_url = 'https://api.genewiz.com'

    def _headers(self) -> Dict[str, str]:
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    def place_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Submit a sequencing order."""
        return self._request('post', 'sequencing/orders', data=json.dumps(payload))
