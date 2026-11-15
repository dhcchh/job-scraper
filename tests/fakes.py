"""Fakes shared by the tests."""


class FakeResponse:
    """A successful JSON response from a job-board endpoint."""

    def __init__(self, payload=None):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload
