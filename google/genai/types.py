from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any


@dataclass
class FunctionCall:
    name: str
    args: dict[str, Any]


@dataclass
class FunctionResponse:
    name: str
    response: dict[str, Any]


@dataclass
class Part:
    text: str | None = None
    function_call: FunctionCall | None = None
    function_response: FunctionResponse | None = None

    @classmethod
    def from_function_response(cls, *, name: str, response: dict[str, Any]):
        return cls(function_response=FunctionResponse(name=name, response=response))


@dataclass
class Content:
    role: str
    parts: list[Part] = field(default_factory=list)


@dataclass
class GenerateContentConfig:
    system_instruction: str | None = None
    max_output_tokens: int | None = None
    tools: Any = None
