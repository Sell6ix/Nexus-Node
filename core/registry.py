"""Plugin registry: auto-discover tool modules and build OpenAI schemas."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

from pydantic import BaseModel, Field, create_model

ATTR_IS_TOOL = "_is_assistant_tool"


def tool(func: Callable[..., Any]) -> Callable[..., Any]:
    """Mark a function as an LLM-callable tool for auto-discovery."""
    setattr(func, ATTR_IS_TOOL, True)
    return func


def is_tool(func: Callable[..., Any]) -> bool:
    return bool(getattr(func, ATTR_IS_TOOL, False))


class RegisteredTool:
    """A discovered tool with its callable and OpenAI function schema."""

    def __init__(
        self,
        name: str,
        func: Callable[..., Any],
        description: str,
        parameters_model: type[BaseModel],
        openai_schema: dict[str, Any],
    ) -> None:
        self.name = name
        self.func = func
        self.description = description
        self.parameters_model = parameters_model
        self.openai_schema = openai_schema

    def parse_arguments(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Validate and coerce raw LLM arguments via the Pydantic model."""
        return self.parameters_model.model_validate(raw or {}).model_dump()


class ToolRegistry:
    """Discovers ``@tool`` functions under ``tools/`` and exposes OpenAI schemas."""

    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, func: Callable[..., Any], *, name: str | None = None) -> RegisteredTool:
        tool_name = name or func.__name__
        if tool_name in self._tools:
            raise ValueError(f"Tool '{tool_name}' is already registered.")

        description = _docstring_summary(func)
        parameters_model = _build_parameters_model(func, tool_name)
        openai_schema = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": description,
                "parameters": parameters_model.model_json_schema(),
            },
        }
        registered = RegisteredTool(
            name=tool_name,
            func=func,
            description=description,
            parameters_model=parameters_model,
            openai_schema=openai_schema,
        )
        self._tools[tool_name] = registered
        return registered

    def discover(self, package_name: str = "tools") -> list[RegisteredTool]:
        """Import all modules in ``package_name`` and register ``@tool`` functions."""
        package = importlib.import_module(package_name)
        package_paths = [Path(p) for p in (package.__path__ or [])]

        for module_info in pkgutil.iter_modules([str(p) for p in package_paths]):
            if module_info.name.startswith("_"):
                continue
            module = importlib.import_module(f"{package_name}.{module_info.name}")
            for _, obj in inspect.getmembers(module, inspect.isfunction):
                if obj.__module__ != module.__name__:
                    continue
                if is_tool(obj):
                    self.register(obj)

        return list(self._tools.values())

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[RegisteredTool]:
        return list(self._tools.values())

    def openai_tools(self) -> list[dict[str, Any]]:
        """Return tool schemas in OpenAI Chat Completions function-calling format."""
        return [t.openai_schema for t in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools


def _docstring_summary(func: Callable[..., Any]) -> str:
    doc = inspect.getdoc(func) or ""
    if not doc:
        return f"Tool `{func.__name__}`."
    # Prefer the full docstring for LLM orientation; collapse excess blank lines.
    lines = [line.rstrip() for line in doc.strip().splitlines()]
    return "\n".join(lines).strip()


def _build_parameters_model(func: Callable[..., Any], tool_name: str) -> type[BaseModel]:
    """Create a Pydantic v2 model from the function signature for schema + validation."""
    hints = get_type_hints(func)
    signature = inspect.signature(func)
    fields: dict[str, Any] = {}

    for param_name, param in signature.parameters.items():
        if param_name in {"self", "cls"}:
            continue
        annotation = hints.get(param_name, Any)
        field_description = _param_description(func, param_name)

        if param.default is inspect.Parameter.empty:
            fields[param_name] = (
                annotation,
                Field(..., description=field_description),
            )
        else:
            fields[param_name] = (
                annotation,
                Field(default=param.default, description=field_description),
            )

    model_name = f"{_pascal(tool_name)}Params"
    return create_model(model_name, **fields)  # type: ignore[call-overload]


def _param_description(func: Callable[..., Any], param_name: str) -> str:
    """Pull a parameter description from an Args: section when present."""
    doc = inspect.getdoc(func) or ""
    in_args = False
    for line in doc.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("args:"):
            in_args = True
            continue
        if in_args:
            if not stripped:
                if in_args:
                    # blank line may end the section; keep scanning lightly
                    continue
            if stripped.lower().startswith(("returns:", "raises:", "example:", "examples:")):
                break
            if stripped.startswith(f"{param_name}:") or stripped.startswith(f"{param_name} "):
                # "name: desc" or "name (type): desc"
                if ":" in stripped:
                    return stripped.split(":", 1)[1].strip()
    return f"Parameter `{param_name}`."


def _pascal(name: str) -> str:
    return "".join(part.capitalize() for part in name.split("_") if part)


def annotation_to_json_compatible(annotation: Any) -> Any:
    """Helper kept for tests/debugging of typing → schema conversion."""
    origin = get_origin(annotation)
    if origin is None:
        return annotation
    return origin[get_args(annotation)] if get_args(annotation) else origin
