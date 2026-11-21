"""Fakes shared by the tests."""

import requests


class FakeResponse:
    """A successful JSON response from a job-board endpoint."""

    def __init__(self, payload=None):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class ErrorResponse:
    """A fake response that raises an HTTPError carrying the given status."""

    def __init__(self, status_code, headers=None):
        self._status_code = status_code
        self.headers = headers or {}

    @property
    def status_code(self):
        return self._status_code

    def raise_for_status(self):
        error = requests.HTTPError(f"{self._status_code} error")
        error.response = self
        raise error
