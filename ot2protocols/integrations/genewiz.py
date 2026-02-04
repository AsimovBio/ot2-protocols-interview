import json
import os
from typing import Any, Dict, Optional

import requests


class GeneWizError(Exception):
    """Raised when an order can't be placed with GeneWiz."""


class GeneWizClient:
    """Thin client for GeneWiz sequencing orders."""

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.base_url = os.environ.get('GENEWIZ_API_URL', 'https://api.genewiz.com').rstrip('/')
        self.api_key = os.environ.get('GENEWIZ_API_KEY')
        self.timeout = int(os.environ.get('GENEWIZ_TIMEOUT', '10'))
        if not self.api_key:
            raise GeneWizError('Missing GENEWIZ_API_KEY environment variable')

    def place_order(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/sequencing/orders"
        headers = {
            'Authorization': f"Bearer {self.api_key}",
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        try:
            response = self.session.post(url, headers=headers, data=json.dumps(payload), timeout=self.timeout)
        except requests.RequestException as exc:  # pragma: no cover - external interaction
            raise GeneWizError(f'Failed to reach GeneWiz: {exc}') from exc
        if response.status_code >= 400:
            raise GeneWizError(f'GeneWiz rejected the request ({response.status_code}): {response.text}')
        try:
            return response.json()
        except ValueError as exc:
            raise GeneWizError(f'Invalid JSON response from GeneWiz: {exc}') from exc
