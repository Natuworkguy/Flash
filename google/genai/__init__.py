from . import types, errors


class _Models:
    def generate_content(self, **kwargs):
        raise NotImplementedError


class Client:
    def __init__(self, *, api_key: str):
        self.api_key = api_key
        self.models = _Models()
