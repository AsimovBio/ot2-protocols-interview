"""Benchling API client for sequence lookups."""

from typing import Dict

from .base import APIError, BaseAPIClient


class BenchlingError(APIError):
    """Raised when Benchling operations fail."""


class BenchlingClient(BaseAPIClient):
    """Client for fetching sequences from Benchling."""

    error_class = BenchlingError
    env_prefix = 'BENCHLING'
    default_url = 'https://api.benchling.com/v2'

    def _headers(self) -> Dict[str, str]:
        return {
            'X-Benchling-Api-Key': self.api_key,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    def fetch_sequence(self, sample_name: str) -> str:
        """Fetch sequence for a sample by name."""
        data = self._request('get', 'oligos', params={'query': sample_name, 'limit': 1})
        results = data.get('data', [])
        if not results:
            raise BenchlingError(f'No sequence data found for {sample_name}')
        return results[0].get('sequence', '')
