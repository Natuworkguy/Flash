"""What happens around each model call: presets, shortcuts, health,
the response cache, the activity log and trace export.

Every model call goes through providers.Client.chat, which asks this
module four things:

- prepare(): the model field as written, turned into a real model and
  the extras that come with it. "@name" is a preset (a model, its
  fallbacks, a system prompt and options, saved once and used by
  name); "@fast" and "@small" are shortcuts to the quickest model
  measured so far and the smallest one installed; "model:online" adds
  what a web search found to the question, for models that cannot
  search for themselves.
- order(): the fallback chain with models that keep failing moved to
  the back for a while, so a dead host is not waited on every time.
- cached() / remember(): an answer given before to the very same
  question, for calls that ask for it (titles, picks) or for every
  plain call when the cache is on in Settings.
- record() / watch(): how the call went (how long to the first word,
  words a second, tokens, the error), kept per model for health and
  "@fast", written to the activity log, and sent on as an
  OpenTelemetry span when trace export is on.

structured() is here too: an answer as JSON that matches a schema,
checked, and asked again with what was wrong when it does not.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable, Optional

from . import workspace
from .paths import FLASH_DIR

PRESETS_FILE = "presets.json"
HEALTH_FILE = "model-health.json"


def _activity_path() -> Path:
    return FLASH_DIR / "activity.jsonl"


def _cache_dir() -> Path:
    return FLASH_DIR / "cache" / "responses"


def _traces_dir() -> Path:
    return FLASH_DIR / "traces"


CACHE_SETTING = "RESPONSE_CACHE"
CACHE_HOURS_SETTING = "RESPONSE_CACHE_HOURS"
TRACE_SETTING = "TRACE_EXPORT"
HEALTH_SETTING = "HEALTH_ROUTING"

SHORTCUTS = ("fast", "small")
ONLINE = ":online"
PRESET_RE = re.compile(r"^@([a-z0-9][a-z0-9_-]{0,39})$")
MOST_PRESETS = 64

# A model that failed this many times in a row is moved to the back of
# its chain for a while: a minute at first, twice as long each time it
# fails again, at most a quarter of an hour.
TRIPS_AFTER = 3
FIRST_REST = 60.0
LONGEST_REST = 900.0
# How much a new measurement moves the running average.
EASE = 0.3
# Measurements a model needs before "@fast" believes it.
FAST_SAMPLES = 3

ACTIVITY_MOST_BYTES = 4_000_000
ACTIVITY_KEPT_LINES = 6000
CACHE_MOST_FILES = 2000

_lock = threading.Lock()
_health: Optional[dict[str, dict]] = None
_small: dict[str, tuple[float, str]] = {}


# --- Settings ------------------------------------------------------------


def _on(name: str, default: str = "0") -> bool:
    return (os.getenv(name) or default).strip().lower() in (
        "1", "on", "true", "yes")


def cache_on() -> bool:
    return _on(CACHE_SETTING)


def cache_hours() -> float:
    try:
        return max(0.05, float(os.getenv(CACHE_HOURS_SETTING) or 24))
    except ValueError:
        return 24.0


def health_on() -> bool:
    return _on(HEALTH_SETTING, "1")


def trace_target() -> str:
    """Where spans go: "" for nowhere, "file" for ~/.flash/traces, or
    an http(s) OTLP endpoint (".../v1/traces")."""

    said = (os.getenv(TRACE_SETTING) or "").strip()
    if said.lower() in ("", "0", "off", "no", "false"):
        return ""
    if said.lower() in ("1", "on", "yes", "true", "file"):
        return "file"
    return said if said.startswith(("http://", "https://")) else ""


# --- Presets -------------------------------------------------------------


def presets() -> dict[str, dict]:
    found = workspace._read(PRESETS_FILE, {})  # pylint: disable=W0212
    return found if isinstance(found, dict) else {}


def _clean_preset(given: dict) -> dict:
    model = str(given.get("model") or "").strip()
    if not model:
        raise ValueError("A preset needs a model.")
    if model.startswith("@"):
        raise ValueError("A preset's model cannot be another preset.")
    out: dict[str, Any] = {"model": model}
    fallbacks = [str(m).strip() for m in given.get("fallbacks") or []
                 if str(m).strip()]
    if fallbacks:
        out["fallbacks"] = fallbacks[:6]
    system = str(given.get("system") or "").strip()
    if system:
        out["system"] = system[:8000]
    options = given.get("options") or {}
    if not isinstance(options, dict):
        raise ValueError("A preset's options are name: value pairs.")
    kept = {}
    for key, value in options.items():
        if not re.fullmatch(r"[a-z_]{1,32}", str(key)):
            raise ValueError(f"{key!r} is not an option name.")
        if not isinstance(value, (int, float, str, bool)):
            raise ValueError(f"{key} needs a number, word or yes/no.")
        kept[str(key)] = value
    if kept:
        out["options"] = kept
    note = str(given.get("note") or "").strip()
    if note:
        out["note"] = note[:200]
    return out


def save_preset(name: str, given: dict) -> dict:
    name = name.strip().lstrip("@").lower()
    if not PRESET_RE.match("@" + name):
        raise ValueError("A preset's name is letters, digits, - and _.")
    if name in SHORTCUTS:
        raise ValueError(f"@{name} is a shortcut already.")
    clean = _clean_preset(given)
    with _lock:
        held = presets()
        if name not in held and len(held) >= MOST_PRESETS:
            raise ValueError(f"At most {MOST_PRESETS} presets.")
        held[name] = clean
        workspace._write(PRESETS_FILE, held)  # pylint: disable=W0212
    return clean


def remove_preset(name: str) -> bool:
    name = name.strip().lstrip("@").lower()
    with _lock:
        held = presets()
        if held.pop(name, None) is None:
            return False
        workspace._write(PRESETS_FILE, held)  # pylint: disable=W0212
    return True


def is_preset(model: Any) -> bool:
    return bool(PRESET_RE.match(str(model or "").strip().lower()))


# --- Shortcuts -----------------------------------------------------------


def fastest(default: str = "") -> str:
    """The model that has given the most words a second, of those measured
    enough times and not resting after failures."""

    best, speed = "", 0.0
    now = time.time()
    for name, held in _load_health().items():
        rate = float(held.get("tok_s") or 0)
        if held.get("samples", 0) >= FAST_SAMPLES and rate > speed \
                and float(held.get("rest_until") or 0) <= now:
            best, speed = name, rate
    return best or default


def smallest(client: Any, default: str = "") -> str:
    """The smallest model the host has, by size on disk; providers'
    models (size 0) are not counted."""

    host = str(getattr(client, "_host", "") or "")
    held = _small.get(host)
    if held and time.time() - held[0] < 300:
        return held[1] or default
    name = ""
    try:
        sizes = [(int(getattr(m, "size", 0) or 0), str(m.model))
                 for m in client.list().models]
        sizes = [s for s in sizes if s[0] > 0 and "embed" not in s[1]]
        if sizes:
            name = min(sizes)[1]
    except Exception:  # noqa: BLE001 -- host down: the default answers
        name = ""
    _small[host] = (time.time(), name)
    return name or default


# --- Preparing a call ----------------------------------------------------


def _last_user_text(messages: Any) -> str:
    for message in reversed(list(messages or [])):
        role = message.get("role") if isinstance(message, dict) \
            else getattr(message, "role", "")
        if role == "user":
            content = message.get("content") if isinstance(message, dict) \
                else getattr(message, "content", "")
            return str(content or "")
    return ""


URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")


def web_context(question: str,
                search: Optional[Callable[[str, int], str]] = None,
                fetch: Optional[Callable[[str], str]] = None) -> str:
    """What the web says about QUESTION, for a model that cannot look:
    the pages it links, else the top search results."""

    if not question.strip():
        return ""
    if search is None or fetch is None:
        from . import tools  # deferred: tools is large
        search = search or tools.web_search
        fetch = fetch or tools.fetch
    parts: list[str] = []
    for url in URL_RE.findall(question)[:2]:
        try:
            parts.append(f"From {url}:\n{str(fetch(url))[:6000]}")
        except Exception:  # noqa: BLE001 -- a page that fails is skipped
            continue
    if not parts:
        try:
            parts.append(str(search(question[:300], 5)))
        except Exception:  # noqa: BLE001 -- no results is no context
            return ""
    return "\n\n".join(parts).strip()


def prepare(client: Any, model: str, kwargs: dict,
            default: str = "") -> tuple[str, list[str]]:
    """MODEL as written turned into the model to ask, with KWARGS
    changed in place for what comes with it (a preset's system prompt
    and options, web results). Returns the model and the preset's
    fallbacks."""

    model = (model or "").strip()
    fallbacks: list[str] = []
    lowered = model.lower()
    if lowered in ("@fast", "@small") and lowered[1:] not in presets():
        model = fastest(default) if lowered == "@fast" \
            else smallest(client, default)
        return model or default, fallbacks
    if is_preset(model):
        preset = presets().get(lowered[1:])
        if preset is None:
            raise ValueError(f"There is no preset called {model}.")
        model = preset["model"]
        fallbacks = list(preset.get("fallbacks") or [])
        if preset.get("system"):
            messages = list(kwargs.get("messages") or [])
            kwargs["messages"] = [{"role": "system",
                                   "content": preset["system"]}] + messages
        if preset.get("options"):
            kwargs["options"] = {**preset["options"],
                                 **dict(kwargs.get("options") or {})}
        lowered = model.lower()
    if lowered.endswith(ONLINE):
        model = model[:-len(ONLINE)]
        found = web_context(_last_user_text(kwargs.get("messages")))
        if found:
            messages = list(kwargs.get("messages") or [])
            note = {"role": "system", "content": (
                "Web results for the user's last message, found just "
                "now. Use them where they help and say where a fact "
                "came from.\n\n" + found)}
            kwargs["messages"] = messages[:-1] + [note] + messages[-1:] \
                if messages else [note]
    return model, fallbacks


# --- Health --------------------------------------------------------------


def _load_health() -> dict[str, dict]:
    global _health  # pylint: disable=W0603
    if _health is None:
        found = workspace._read(HEALTH_FILE, {})  # pylint: disable=W0212
        _health = found if isinstance(found, dict) else {}
    return _health


def health() -> dict[str, dict]:
    with _lock:
        return {k: dict(v) for k, v in _load_health().items()}


def resting(model: str, now: Optional[float] = None) -> bool:
    held = _load_health().get(model) or {}
    return float(held.get("rest_until") or 0) > (now or time.time())


def order(chain: list[str]) -> list[str]:
    """CHAIN with models resting after failures moved to the back, in
    their order: never dropped, so the last one is still tried."""

    if not health_on() or len(chain) < 2:
        return chain
    now = time.time()
    with _lock:
        awake = [m for m in chain if not resting(m, now)]
        tired = [m for m in chain if resting(m, now)]
    return awake + tired


def _ease(old: Any, new: float) -> float:
    return new if not old else round(float(old) * (1 - EASE) + new * EASE, 3)


def _note_health(model: str, ok: bool, ttft: Optional[float],
                 rate: Optional[float], error: str) -> None:
    with _lock:
        every = _load_health()
        held = every.setdefault(model, {})
        now = time.time()
        if ok:
            held["fails"] = 0
            held["rest_until"] = 0
            held["rest"] = 0
            held["samples"] = int(held.get("samples") or 0) + 1
            if ttft is not None:
                held["ttft"] = _ease(held.get("ttft"), ttft)
            if rate:
                held["tok_s"] = _ease(held.get("tok_s"), rate)
            held["last_ok"] = now
        else:
            held["fails"] = int(held.get("fails") or 0) + 1
            held["failures"] = int(held.get("failures") or 0) + 1
            held["last_error"] = error[:200]
            held["last_fail"] = now
            if held["fails"] >= TRIPS_AFTER:
                rest = min(LONGEST_REST,
                           float(held.get("rest") or 0) * 2 or FIRST_REST)
                held["rest"] = rest
                held["rest_until"] = now + rest
        held["calls"] = int(held.get("calls") or 0) + 1
        try:
            workspace._write(HEALTH_FILE, every)  # pylint: disable=W0212
        except OSError:
            pass


def forget_health(model: str = "") -> None:
    global _health  # pylint: disable=W0603
    with _lock:
        every = _load_health()
        if model:
            every.pop(model, None)
        else:
            every.clear()
        workspace._write(HEALTH_FILE, every)  # pylint: disable=W0212
        _health = every


# --- Cache ---------------------------------------------------------------


def _plain(value: Any) -> Any:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return _plain(dump(exclude_none=True))
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()
                if v is not None}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    return str(value)


def cache_key(model: str, kwargs: dict) -> str:
    asked = {"model": model,
             **{k: _plain(kwargs.get(k)) for k in
                ("messages", "options", "format", "think")
                if kwargs.get(k) is not None}}
    text = json.dumps(asked, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:40]


def cacheable(kwargs: dict, asked: Optional[bool]) -> bool:
    """A call whose answer can be given again: not streamed, no tools,
    and asked for by the caller or by the setting."""

    if kwargs.get("stream") or kwargs.get("tools"):
        return False
    return cache_on() if asked is None else bool(asked)


def cached(model: str, kwargs: dict) -> Any:
    path = _cache_dir() / f"{cache_key(model, kwargs)}.json"
    try:
        if time.time() - path.stat().st_mtime > cache_hours() * 3600:
            return None
        held = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    from ollama import ChatResponse
    try:
        return ChatResponse.model_validate(held)
    except Exception:  # noqa: BLE001 -- an old shape: asked again
        return None


def remember(model: str, kwargs: dict, answer: Any) -> None:
    try:
        _cache_dir().mkdir(parents=True, exist_ok=True)
        path = _cache_dir() / f"{cache_key(model, kwargs)}.json"
        path.write_text(json.dumps(_plain(answer)), encoding="utf-8")
        held = sorted(_cache_dir().glob("*.json"),
                      key=lambda p: p.stat().st_mtime)
        for old in held[:max(0, len(held) - CACHE_MOST_FILES)]:
            old.unlink(missing_ok=True)
    except (OSError, TypeError, ValueError):
        pass


def clear_cache() -> int:
    gone = 0
    for path in _cache_dir().glob("*.json") if _cache_dir().exists() else []:
        try:
            path.unlink()
            gone += 1
        except OSError:
            pass
    return gone


# --- Recording -----------------------------------------------------------


def _usage(answer: Any) -> dict:
    get = (lambda k: answer.get(k)) if isinstance(answer, dict) \
        else (lambda k: getattr(answer, k, None))
    out = {}
    for key, name in (("prompt_eval_count", "tokens_in"),
                      ("eval_count", "tokens_out"),
                      ("eval_duration", "eval_ns")):
        value = get(key)
        if isinstance(value, (int, float)) and value:
            out[name] = int(value)
    return out


def record(model: str, started: float, *, ok: bool, answer: Any = None,
           error: str = "", ttft: Optional[float] = None,
           stream: bool = False, tools: bool = False,
           cached_hit: bool = False, words: int = 0) -> dict:
    """How one call went: kept for health, the log and traces."""

    ended = time.time()
    took = max(0.0, time.monotonic() - started)
    usage = _usage(answer) if answer is not None else {}
    out_tokens = usage.get("tokens_out") or 0
    rate = None
    if usage.get("eval_ns") and out_tokens:
        rate = out_tokens / (usage["eval_ns"] / 1e9)
    elif out_tokens or words:
        spent = took - (ttft or 0)
        if spent > 0.05:
            rate = (out_tokens or words) / spent
    entry = {
        "at": round(ended, 3), "model": model, "ok": ok,
        "ms": round(took * 1000), "stream": stream, "tools": tools,
    }
    if ttft is not None:
        entry["ttft_ms"] = round(ttft * 1000)
    if rate:
        entry["tok_s"] = round(rate, 1)
    for key in ("tokens_in", "tokens_out"):
        if usage.get(key):
            entry[key] = usage[key]
    if cached_hit:
        entry["cached"] = True
    if error:
        entry["error"] = error[:300]
    if not cached_hit:
        _note_health(model, ok, ttft if ttft is not None else
                     (took if ok and not stream else None), rate, error)
    _log(entry)
    _trace(entry, ended - took, ended)
    return entry


def watch(model: str, pieces: Iterator, started: float, *,
          tools: bool = False) -> Iterator:
    """PIECES as they come, recorded once the stream ends or fails."""

    first: Optional[float] = None
    last = None
    words = 0
    try:
        for piece in pieces:
            if first is None:
                first = time.monotonic() - started
            message = getattr(piece, "message", None)
            said = getattr(message, "content", "") or ""
            words += len(said.split()) if said else 0
            last = piece
            yield piece
    except GeneratorExit:
        record(model, started, ok=True, answer=last, ttft=first,
               stream=True, tools=tools, words=words)
        raise
    except Exception as exc:
        record(model, started, ok=False, error=str(exc), ttft=first,
               stream=True, tools=tools)
        raise
    record(model, started, ok=True, answer=last, ttft=first,
           stream=True, tools=tools, words=words)


def _log(entry: dict) -> None:
    try:
        _activity_path().parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with _activity_path().open("a", encoding="utf-8") as out:
                out.write(json.dumps(entry) + "\n")
            if _activity_path().stat().st_size > ACTIVITY_MOST_BYTES:
                lines = _activity_path().read_text(
                    encoding="utf-8").splitlines()[-ACTIVITY_KEPT_LINES:]
                _activity_path().write_text("\n".join(lines) + "\n",
                                         encoding="utf-8")
    except OSError:
        pass


def activity(days: float = 7.0, model: str = "",
             most: int = 500) -> list[dict]:
    """The calls of the last DAYS, newest first."""

    since = time.time() - days * 86400
    try:
        lines = _activity_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("at", 0) < since:
            break
        if model and entry.get("model") != model:
            continue
        out.append(entry)
        if len(out) >= most:
            break
    return out


def summary(days: float = 7.0) -> list[dict]:
    """Per model over the last DAYS: calls, failures, cache hits,
    tokens, the middle time to first word and words a second."""

    per: dict[str, dict] = {}
    for entry in activity(days, most=100000):
        held = per.setdefault(entry["model"], {
            "model": entry["model"], "calls": 0, "failed": 0, "cached": 0,
            "tokens_in": 0, "tokens_out": 0, "_ttft": [], "_rate": []})
        held["calls"] += 1
        held["failed"] += 0 if entry.get("ok") else 1
        held["cached"] += 1 if entry.get("cached") else 0
        held["tokens_in"] += entry.get("tokens_in", 0)
        held["tokens_out"] += entry.get("tokens_out", 0)
        if entry.get("ttft_ms") is not None:
            held["_ttft"].append(entry["ttft_ms"])
        if entry.get("tok_s"):
            held["_rate"].append(entry["tok_s"])
    out = []
    for held in per.values():
        ttfts, rates = sorted(held.pop("_ttft")), sorted(held.pop("_rate"))
        held["ttft_ms"] = ttfts[len(ttfts) // 2] if ttfts else None
        held["tok_s"] = rates[len(rates) // 2] if rates else None
        out.append(held)
    return sorted(out, key=lambda h: -h["calls"])


def clear_activity() -> None:
    try:
        _activity_path().unlink()
    except OSError:
        pass


# --- Traces --------------------------------------------------------------


def _attr(key: str, value: Any) -> dict:
    if isinstance(value, bool):
        return {"key": key, "value": {"boolValue": value}}
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    if isinstance(value, float):
        return {"key": key, "value": {"doubleValue": value}}
    return {"key": key, "value": {"stringValue": str(value)}}


def span(entry: dict, start: float, end: float) -> dict:
    """ENTRY as an OTLP/JSON trace with one span, named and tagged the
    way the OpenTelemetry GenAI conventions name them."""

    attrs = [_attr("gen_ai.operation.name", "chat"),
             _attr("gen_ai.request.model", entry["model"]),
             _attr("flash.stream", bool(entry.get("stream")))]
    for key, name in (("tokens_in", "gen_ai.usage.input_tokens"),
                      ("tokens_out", "gen_ai.usage.output_tokens"),
                      ("ttft_ms", "flash.time_to_first_token_ms"),
                      ("tok_s", "flash.tokens_per_second")):
        if entry.get(key) is not None:
            attrs.append(_attr(name, entry[key]))
    if entry.get("cached"):
        attrs.append(_attr("flash.cached", True))
    status = {"code": 1} if entry.get("ok") else \
        {"code": 2, "message": entry.get("error", "")}
    return {"resourceSpans": [{
        "resource": {"attributes": [_attr("service.name", "flash")]},
        "scopeSpans": [{
            "scope": {"name": "flash.routing"},
            "spans": [{
                "traceId": secrets.token_hex(16),
                "spanId": secrets.token_hex(8),
                "name": f"chat {entry['model']}",
                "kind": 3,
                "startTimeUnixNano": str(int(start * 1e9)),
                "endTimeUnixNano": str(int(end * 1e9)),
                "attributes": attrs,
                "status": status,
            }],
        }],
    }]}


def _send(target: str, body: dict) -> None:
    data = json.dumps(body).encode("utf-8")
    if target == "file":
        try:
            _traces_dir().mkdir(parents=True, exist_ok=True)
            day = time.strftime("%Y-%m-%d")
            with (_traces_dir() / f"{day}.jsonl").open("a",
                                                    encoding="utf-8") as out:
                out.write(data.decode("utf-8") + "\n")
        except OSError:
            pass
        return
    request = urllib.request.Request(
        target, data=data, method="POST",
        headers={"Content-Type": "application/json"})
    headers = (os.getenv("TRACE_EXPORT_HEADERS") or "").strip()
    for pair in headers.split(","):
        key, _, value = pair.partition("=")
        if key.strip() and value.strip():
            request.add_header(key.strip(), value.strip())
    try:
        with urllib.request.urlopen(request, timeout=5):  # nosec B310
            pass
    except Exception:  # noqa: BLE001 -- a collector that is down
        pass


def _trace(entry: dict, start: float, end: float) -> None:
    target = trace_target()
    if not target:
        return
    body = span(entry, start, end)
    if target == "file":
        _send(target, body)
    else:
        threading.Thread(target=_send, args=(target, body),
                         daemon=True).start()


# --- Structured answers --------------------------------------------------


_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool,
          "integer": int, "number": (int, float), "null": type(None)}


def problems(value: Any, schema: dict, where: str = "$") -> list[str]:
    """What is wrong with VALUE against a JSON SCHEMA (the common
    parts: type, properties, required, items, enum, bounds)."""

    found: list[str] = []
    kind = schema.get("type")
    if kind:
        kinds = kind if isinstance(kind, list) else [kind]
        ok = False
        for name in kinds:
            want = _TYPES.get(name)
            if want is None:
                ok = True
            elif isinstance(value, bool) and name in ("integer", "number"):
                continue
            elif isinstance(value, want):
                ok = True
        if not ok:
            return [f"{where} should be {' or '.join(kinds)}"]
    if "enum" in schema and value not in schema["enum"]:
        found.append(f"{where} should be one of {schema['enum']}")
    if isinstance(value, dict):
        for name in schema.get("required", []):
            if name not in value:
                found.append(f"{where}.{name} is missing")
        props = schema.get("properties", {})
        for name, sub in props.items():
            if name in value and isinstance(sub, dict):
                found += problems(value[name], sub, f"{where}.{name}")
        if schema.get("additionalProperties") is False:
            for name in value:
                if name not in props:
                    found.append(f"{where}.{name} is not allowed")
    if isinstance(value, list):
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                found += problems(item, schema["items"], f"{where}[{index}]")
        if len(value) < schema.get("minItems", 0):
            found.append(f"{where} needs at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            found.append(f"{where} has more than {schema['maxItems']} items")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            found.append(f"{where} is below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            found.append(f"{where} is above {schema['maximum']}")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            found.append(f"{where} is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            found.append(f"{where} is too long")
    return found


def _json_in(text: str) -> Any:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except ValueError:
        start = min([i for i in (text.find("{"), text.find("["))
                     if i >= 0], default=-1)
        if start < 0:
            raise
        return json.loads(text[start:max(text.rfind("}"),
                                         text.rfind("]")) + 1])


def structured(client: Any, model: str, messages: list, schema: dict,
               tries: int = 3, **kwargs: Any) -> Any:
    """MODEL's answer to MESSAGES as JSON matching SCHEMA: asked with the
    schema as the format, checked, and asked again with what was wrong,
    up to TRIES times. Raises ValueError when it never fits."""

    asked = list(messages)
    asked.insert(0, {"role": "system", "content": (
        "Answer with only JSON that matches this schema, no other "
        "words:\n" + json.dumps(schema))})
    wrong = "no answer"
    for _ in range(max(1, tries)):
        answer = client.chat(model=model, messages=asked, format=schema,
                             stream=False, **kwargs)
        message = getattr(answer, "message", None)
        text = str(getattr(message, "content", "") or "")
        try:
            value = _json_in(text)
        except ValueError:
            wrong = "that was not JSON"
        else:
            found = problems(value, schema)
            if not found:
                return value
            wrong = "; ".join(found[:8])
        asked += [{"role": "assistant", "content": text},
                  {"role": "user", "content": (
                      f"That does not fit the schema: {wrong}. Answer "
                      "again with only the corrected JSON.")}]
    raise ValueError(f"{model} never gave JSON that fits: {wrong}")


def files() -> dict[str, Path]:
    return {"activity": _activity_path(), "cache": _cache_dir(),
            "traces": _traces_dir()}
