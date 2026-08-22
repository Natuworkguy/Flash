import json
import os
import urllib.error
import urllib.request

SHOW_TIMEOUT_SECONDS = 5


def get_system_prompt():
    """Get system prompt for the AI model."""

    with open(
        os.path.join(
            os.path.dirname(__file__),
            "system_prompt.txt"
        ),
        "r",
        encoding="utf-8"
    ) as f:
        return f.read().strip()


def _show_url(host: str) -> str:
    """Build the /api/show URL, tolerating a scheme-less OLLAMA_HOST.

    Ollama's own client accepts a bare `localhost:11434`, so Flash has to
    accept it too; urllib needs the scheme spelled out.
    """

    host = host.strip().rstrip("/")

    if "://" not in host:
        host = f"http://{host}"

    return f"{host}/api/show"


def get_model_system_prompt(host: str, model: str) -> str:
    """Get the system prompt baked into MODEL by its Modelfile.

    Returns an empty string when the model defines none, or when Ollama
    cannot be reached; the caller falls back to Flash's prompt alone.
    Ollama's typed client drops the `system` field, so read it from
    /api/show directly.
    """

    if not model:
        return ""

    request = urllib.request.Request(
        f"{host.rstrip('/')}/api/show",
        data=json.dumps({"model": model}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            request, timeout=SHOW_TIMEOUT_SECONDS
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError):
        return ""

    return str(payload.get("system") or "").strip()
