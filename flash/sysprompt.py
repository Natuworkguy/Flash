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


def _show(host: str, model: str) -> dict:
    """Ask Ollama for everything it knows about MODEL.

    Returns an empty dict when Ollama cannot be reached, so every caller
    degrades to "nothing known about this model" rather than an error.
    """

    if not model:
        return {}

    request = urllib.request.Request(
        _show_url(host),
        data=json.dumps({"model": model}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(  # nosec B310 -- scheme forced to http
            request, timeout=SHOW_TIMEOUT_SECONDS
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, ValueError):
        return {}

    return payload if isinstance(payload, dict) else {}


def get_model_system_prompt(host: str, model: str) -> str:
    """Get the system prompt baked into MODEL by its Modelfile.

    Returns an empty string when the model defines none, or when Ollama
    cannot be reached; the caller falls back to Flash's prompt alone.
    Ollama's typed client drops the `system` field, so read it from
    /api/show directly.
    """

    return str(_show(host, model).get("system") or "").strip()


def model_sees_images(host: str, model: str) -> bool:
    """Whether MODEL reports Ollama's `vision` capability.

    Fails open: an unreachable Ollama, or one too old to report
    capabilities at all, answers True so the image is still attempted
    rather than blocked on missing metadata.
    """

    capabilities = _show(host, model).get("capabilities")

    if not isinstance(capabilities, list) or not capabilities:
        return True

    return "vision" in capabilities
