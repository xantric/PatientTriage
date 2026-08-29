"""Explicit tool registry: only registered tools may run.

Unknown names are rejected. Inputs are validated. Failures return ToolResult
with ok=False instead of crashing the agent loop.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Optional

from pydantic import BaseModel, Field, ValidationError

from app.agent.context import ToolContext


class ToolResult(BaseModel):
    """Uniform tool response: evidence payload plus a short operational summary."""

    ok: bool
    tool_name: str
    evidence: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    error: Optional[str] = None


class ToolSpec(BaseModel):
    """Metadata for one registered tool. Arguments schema is a Pydantic model."""

    name: str
    description: str
    # Class used to validate kwargs (must be a BaseModel subclass).
    args_model: type[BaseModel]

    model_config = {"arbitrary_types_allowed": True}


Handler = Callable[[ToolContext, BaseModel], ToolResult]


class ToolRegistry:
    """Name → (spec, handler). Execution validates args then calls the handler."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._handlers: dict[str, Handler] = {}

    def register(
        self,
        *,
        name: str,
        description: str,
        args_model: type[BaseModel],
        handler: Handler,
    ) -> None:
        if name in self._specs:
            raise ValueError(f"tool already registered: {name}")
        self._specs[name] = ToolSpec(
            name=name, description=description, args_model=args_model
        )
        self._handlers[name] = handler

    def names(self) -> list[str]:
        return sorted(self._specs.keys())

    def is_registered(self, name: str) -> bool:
        return name in self._specs

    def describe(self) -> list[dict[str, str]]:
        return [
            {"name": s.name, "description": s.description}
            for s in sorted(self._specs.values(), key=lambda x: x.name)
        ]

    def execute(
        self,
        name: str,
        context: ToolContext,
        arguments: Optional[dict[str, Any]] = None,
    ) -> ToolResult:
        if name not in self._specs:
            return ToolResult(
                ok=False,
                tool_name=name,
                error=f"unknown tool: {name}",
                summary=f"Rejected unknown tool '{name}'.",
            )

        args_model = self._specs[name].args_model
        raw = dict(arguments or {})
        try:
            validated = args_model.model_validate(raw)
        except ValidationError as exc:
            return ToolResult(
                ok=False,
                tool_name=name,
                error=f"invalid arguments: {exc.errors()}",
                summary=f"Tool '{name}' rejected invalid arguments.",
                evidence={"validation_errors": exc.errors()},
            )

        try:
            return self._handlers[name](context, validated)
        except Exception as exc:  # noqa: BLE001 — tools must fail safely
            return ToolResult(
                ok=False,
                tool_name=name,
                error=str(exc),
                summary=f"Tool '{name}' failed: {exc}",
            )


_DEFAULT: ToolRegistry | None = None


def get_default_registry() -> ToolRegistry:
    """Lazy singleton with all Phase 3 tools registered."""

    global _DEFAULT
    if _DEFAULT is None:
        from app.agent.tools import build_registry

        _DEFAULT = build_registry()
    return _DEFAULT


def reset_default_registry() -> None:
    """Test helper: clear the singleton so the next get rebuilds."""

    global _DEFAULT
    _DEFAULT = None
