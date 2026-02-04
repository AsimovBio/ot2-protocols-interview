import os
from typing import Optional

import requests


class BenchlingError(Exception):
    pass


class BenchlingClient:
    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.base_url = os.environ.get('BENCHLING_API_URL', 'https://api.benchling.com/v2').rstrip('/')
        self.api_key = os.environ.get('BENCHLING_API_KEY')
        self.timeout = int(os.environ.get('BENCHLING_TIMEOUT', '10'))
        if not self.api_key:
            raise BenchlingError('Missing BENCHLING_API_KEY environment variable')
        self.headers = {
            'X-Benchling-Api-Key': self.api_key,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    def fetch_sequence(self, sample_name: str) -> str:
        url = f"{self.base_url}/oligos"
        params = {'query': sample_name, 'limit': 1}
        try:
            response = self.session.get(url, headers=self.headers, params=params, timeout=self.timeout)
        except requests.RequestException as exc:  # pragma: no cover - external
            raise BenchlingError(f'Benchling request failed: {exc}') from exc
        if response.status_code >= 400:
            raise BenchlingError(f'Benchling rejected the request ({response.status_code})')
        try:
            data = response.json().get('data', [])
        except ValueError as exc:
            raise BenchlingError(f'Benchling returned invalid JSON: {exc}') from exc
        if not data:
            raise BenchlingError(f'No sequence data found for {sample_name}')
        return data[0].get('sequence', '')
