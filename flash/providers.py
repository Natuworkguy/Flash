"""Model providers that extensions add.

Flash talks to Ollama. An extension can add a provider for models that
live somewhere else, another local server or a hosted API, and those
models are used like any other: picked with /model, run by sparks and
sub-agents, shown in the web UI's model menu. A provider's models are
named @provider/model, which no Ollama model can be called, so a name
alone says where it goes.

A provider is a program. Flash runs it with one JSON request on stdin
and reads JSON back from stdout:

    {"action": "models"}
        -> {"models": [{"name": "big-model", "capabilities": ["completion",
            "tools", "vision"], "context": 400000}]}

    {"action": "chat", "model": "big-model", "messages": [...],
     "tools": [...], "options": {...}, "think": true, "stream": false}
        -> {"message": {"role": "assistant", "content": "...",
            "thinking": "...", "tool_calls": [{"function": {"name": "...",
            "arguments": {...}}}]}, "prompt_eval_count": 120,
            "eval_count": 40}

Messages and tools are Ollama's shapes, images in base64. A chat may be
answered in pieces instead, one JSON object a line, each with part of
the message, the last with "done": true; Flash shows them as they
arrive where it can. {"error": "..."} on stdout, or a non-zero exit,
is a failure, shown to the user as Ollama's own errors are.
"""

from __future__ import annotations

import base64
import json
import subprocess  # nosec B404 -- running the extension's program
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import ollama
from ollama import ChatResponse, ListResponse, ResponseError, ShowResponse

from . import extensions

PREFIX = "@"

# How long a provider's list of models is trusted before it is asked
# again: the model menu asks often, and a provider may be slow to say.
MODELS_SECONDS = 60
MODELS_TIMEOUT = 20

# What a provider model is assumed to do when its listing does not say.
DEFAULT_CAPABILITIES = ["completion", "tools"]


def routed(model: Any) -> bool:
    """Whether MODEL belongs to a provider rather than Ollama."""

    return str(model or "").startswith(PREFIX)


def split(model: str) -> tuple[str, str]:
    """@provider/model as (provider, model). ResponseError if it is not
    shaped like one."""

    provider, slash, name = str(model)[len(PREFIX):].partition("/")
    if not provider or not slash or not name:
        raise ResponseError(
            f"{model!r} is not a provider model: name one as "
            "@provider/model", 400,
        )
    return provider.lower(), name


def name_of(provider: str, model: str) -> str:
    return f"{PREFIX}{provider}/{model}"


def _find(provider: str):
    for extension, found in extensions.providers():
        if found.name == provider:
            return extension, found
    raise ResponseError(
        f"No provider called @{provider}. /extension lists what is "
        "installed.", 404,
    )


# Running the program ----------------------------------------------------


def _plain(value: Any) -> Any:
    """VALUE as plain JSON: ollama's typed messages and tool calls are
    pydantic models, and images may be paths or bytes."""

    if hasattr(value, "model_dump"):
        value = value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return {
            key: (_images(item) if key == "images" else _plain(item))
            for key, item in value.items() if item is not None
        }
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    return value


def _own(message: Any) -> Any:
    """A message without what Flash keeps on it for itself: which turn
    of a chat it began, so an edit can find it."""

    if isinstance(message, dict) and "turn" in message:
        return {k: v for k, v in message.items() if k != "turn"}
    return message


def _images(images: Any) -> list[str]:
    """Every image as base64, the way Ollama sends them."""

    found = []
    for image in images or []:
        if hasattr(image, "value"):
            image = image.value
        if isinstance(image, bytes):
            found.append(base64.b64encode(image).decode("ascii"))
            continue
        text = str(image)
        path = Path(text)
        try:
            is_file = len(text) < 4096 and path.is_file()
        except OSError:
            is_file = False
        found.append(
            base64.b64encode(path.read_bytes()).decode("ascii")
            if is_file else text
        )
    return found


def _lines(extension, provider, request: dict) -> Iterator[dict]:
    """Run PROVIDER with REQUEST, and give back each JSON object it
    prints, as it prints it. A program that prints one document over
    several lines is read whole at the end."""

    try:
        process = subprocess.Popen(  # nosec B603
            extensions.argv(extension, provider.run),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=extensions.environment(extension),
        )
    except OSError as exc:
        raise ResponseError(
            f"Could not start the @{provider.name} provider: {exc}", 500,
        ) from None

    timer = threading.Timer(provider.timeout, process.kill)
    timer.daemon = True
    timer.start()
    errors: list[str] = []
    reader = threading.Thread(
        target=lambda: errors.append(process.stderr.read()), daemon=True,
    )
    reader.start()
    rest: list[str] = []
    try:
        try:
            process.stdin.write(json.dumps(request))
            process.stdin.close()
        except OSError:
            pass
        for line in process.stdout:
            if not line.strip():
                continue
            try:
                found = json.loads(line)
            except ValueError:
                rest.append(line)
                continue
            if isinstance(found, dict):
                _check(found, provider)
                yield found
        if rest:
            try:
                found = json.loads("".join(rest))
            except ValueError:
                found = None
            if not isinstance(found, dict):
                raise ResponseError(
                    f"The @{provider.name} provider did not answer in JSON.",
                    502,
                )
            _check(found, provider)
            yield found
        code = process.wait()
        reader.join(timeout=1)
        if code:
            said = "".join(errors).strip()
            raise ResponseError(
                f"The @{provider.name} provider failed (exit {code})"
                + (f": {said}" if said else
                   " after its timeout" if not timer.is_alive() else ""),
                502,
            )
    finally:
        timer.cancel()
        if process.poll() is None:
            process.kill()
            process.wait()


def _check(found: dict, provider) -> None:
    if found.get("error"):
        raise ResponseError(
            f"@{provider.name}: {found['error']}",
            int(found.get("status") or 502)
            if str(found.get("status") or "").isdigit() else 502,
        )


# What it offers ---------------------------------------------------------

_models: dict[str, tuple[float, list[dict]]] = {}
_models_lock = threading.Lock()


def provider_models(provider: str) -> list[dict]:
    """PROVIDER's models, each {"name", "capabilities", "context"},
    asked at most every MODELS_SECONDS. [] when it cannot say."""

    now = time.monotonic()
    with _models_lock:
        kept = _models.get(provider)
        if kept and now - kept[0] < MODELS_SECONDS:
            return kept[1]
    try:
        extension, found = _find(provider)
    except ResponseError:
        return []
    asked = type(found)(
        found.name, found.description, found.run,
        min(found.timeout, MODELS_TIMEOUT),
    )
    listed: list[dict] = []
    try:
        for answer in _lines(extension, asked, {"action": "models"}):
            for entry in answer.get("models") or []:
                if isinstance(entry, str):
                    entry = {"name": entry}
                if isinstance(entry, dict) and entry.get("name"):
                    listed.append(entry)
    except ResponseError:
        listed = []
    with _models_lock:
        _models[provider] = (now, listed)
    return listed


def forget_models() -> None:
    with _models_lock:
        _models.clear()


def every_model() -> list[str]:
    """Every provider model, by its full @provider/model name."""

    return [
        name_of(found.name, str(entry["name"]))
        for _, found in extensions.providers()
        for entry in provider_models(found.name)
    ]


def _entry(model: str) -> dict:
    provider, name = split(model)
    return next(
        (e for e in provider_models(provider) if str(e["name"]) == name),
        {"name": name},
    )


def show_payload(model: str) -> dict:
    """What Ollama's /api/show would say of MODEL, from what its provider
    listed: what it can do, and its window. It runs elsewhere, so it is
    remote, whatever machine the provider talks to."""

    entry = _entry(model)
    capabilities = entry.get("capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        capabilities = DEFAULT_CAPABILITIES
    context = entry.get("context")
    payload: dict = {
        "capabilities": [str(c) for c in capabilities],
        "remote_host": split(model)[0],
        "system": str(entry.get("system") or ""),
        "model_info": {},
        "parameters": "",
    }
    if isinstance(context, int) and context > 0:
        payload["parameters"] = f"num_ctx {context}"
        payload["model_info"] = {"provider.context_length": context}
    return payload


# Chat -------------------------------------------------------------------


def _response(model: str, found: dict, started: float,
              done: bool = True) -> ChatResponse:
    message = found.get("message") or {}
    if not isinstance(message, dict):
        message = {}
    message = {"role": "assistant", "content": "", **message}
    for call in message.get("tool_calls") or []:
        function = call.get("function") if isinstance(call, dict) else None
        if isinstance(function, dict) and isinstance(
            function.get("arguments"), str
        ):
            # Some APIs give the arguments as a JSON string.
            try:
                function["arguments"] = json.loads(function["arguments"])
            except ValueError:
                function["arguments"] = {}
    took = int((time.monotonic() - started) * 1e9) if done else None
    return ChatResponse.model_validate({
        "model": model,
        "created_at": None,
        "done": bool(found.get("done", done)),
        "done_reason": found.get("done_reason") or ("stop" if done else None),
        "prompt_eval_count": found.get("prompt_eval_count"),
        "eval_count": found.get("eval_count"),
        "eval_duration": found.get("eval_duration") or took,
        "total_duration": found.get("total_duration") or took,
        "message": message,
    })


def chat(
    model: str,
    messages: Any,
    tools: Any = None,
    options: Any = None,
    think: Any = None,
    stream: bool = False,
    **_: Any,
):
    """MODEL's answer to MESSAGES, as ollama.Client.chat gives one: a
    ChatResponse, or with STREAM, an iterator of them."""

    provider, name = split(model)
    extension, found = _find(provider)
    request = {
        "action": "chat",
        "model": name,
        "messages": _plain([_own(m) for m in messages or []]),
        "stream": bool(stream),
    }
    if tools:
        request["tools"] = _plain(list(tools))
    if options:
        request["options"] = _plain(options)
    if think is not None:
        request["think"] = think
    started = time.monotonic()

    if stream:
        def pieces() -> Iterator[ChatResponse]:
            last = None
            for answer in _lines(extension, found, request):
                last = _response(model, answer, started,
                                 done=bool(answer.get("done")))
                yield last
            if last is None or not last.done:
                yield _response(model, {"done": True}, started)
        return pieces()

    # Answered in pieces when not asked to be: put them together.
    content, thinking, calls = [], [], []
    last: dict = {}
    for answer in _lines(extension, found, request):
        message = answer.get("message") or {}
        if isinstance(message, dict):
            content.append(str(message.get("content") or ""))
            thinking.append(str(message.get("thinking") or ""))
            calls += list(message.get("tool_calls") or [])
        last = answer
    whole = {**last, "message": {
        "role": "assistant", "content": "".join(content),
        **({"thinking": "".join(thinking)} if any(thinking) else {}),
        **({"tool_calls": calls} if calls else {}),
    }}
    return _response(model, whole, started)


# The client -------------------------------------------------------------


class Client:
    """An ollama.Client that also knows the providers' models: a chat
    with one of them goes to its provider, they are listed beside
    Ollama's, and shown from what their provider said. Everything else
    is Ollama's, untouched."""

    def __init__(self, inner: ollama.Client) -> None:
        self._inner = inner

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    def chat(self, *args, **kwargs):
        model = kwargs.get("model", args[0] if args else "")
        if not routed(model):
            return self._inner.chat(*args, **kwargs)
        kwargs.pop("model", None)
        rest = dict(zip(("messages", "tools", "stream", "think"), args[1:]))
        return chat(model, **{**rest, **kwargs})

    def list(self) -> ListResponse:
        extra = [
            ListResponse.Model(model=name, size=0)
            for name in every_model()
        ]
        if not extra:
            return self._inner.list()
        try:
            found = list(self._inner.list().models)
        except (OSError, ResponseError, ValueError):
            # Ollama down, with providers to offer: offer those.
            found = []
        # Ollama's listing as it came, not checked over again.
        return ListResponse.model_construct(models=[*found, *extra])

    def show(self, model: str) -> ShowResponse:
        if not routed(model):
            return self._inner.show(model)
        payload = show_payload(model)
        return ShowResponse.model_validate({
            "capabilities": payload["capabilities"],
            "parameters": payload["parameters"],
            "modelinfo": payload["model_info"],
        })

    def pull(self, model: str, *args, **kwargs):
        if routed(model):
            raise ResponseError(
                f"{model} comes from a provider, with nothing to download.",
                400,
            )
        return self._inner.pull(model, *args, **kwargs)


def client(host: str) -> Client:
    """The client for HOST, with every provider's models alongside."""

    from . import workspace  # deferred: only a client needs its key

    return Client(ollama.Client(host=host, **workspace.client_options(host)))
