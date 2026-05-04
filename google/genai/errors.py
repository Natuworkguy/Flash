class APIError(Exception):
    pass


class ServerError(APIError):
    def __init__(self, code=None, payload=None):
        super().__init__(f"Server error: {code}")
        self.code = code
        self.payload = payload
