"""
Agent registry for orchestrating multi-agent dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentEntry:
    name: str
    agent: Any
    depends_on: list[str]


class AgentRegistry:
    """Registry for agents and their dependency order."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentEntry] = {}

    def register(self, name: str, agent: Any, depends_on: list[str] | None = None) -> None:
        self._agents[name] = AgentEntry(
            name=name,
            agent=agent,
            depends_on=list(depends_on or []),
        )

    def get(self, name: str) -> Any:
        return self._agents[name].agent

    def has(self, name: str) -> bool:
        return name in self._agents

    def set_dependencies(self, name: str, depends_on: list[str]) -> None:
        if name not in self._agents:
            raise KeyError(f"Unknown agent: {name}")
        entry = self._agents[name]
        self._agents[name] = AgentEntry(
            name=entry.name,
            agent=entry.agent,
            depends_on=list(depends_on),
        )

    def add_dependency(self, name: str, dependency: str) -> None:
        if name not in self._agents:
            raise KeyError(f"Unknown agent: {name}")
        entry = self._agents[name]
        deps = list(entry.depends_on)
        if dependency not in deps:
            deps.append(dependency)
        self._agents[name] = AgentEntry(
            name=entry.name,
            agent=entry.agent,
            depends_on=deps,
        )

    def execution_order(self, targets: list[str]) -> list[str]:
        order: list[str] = []
        visited: set[str] = set()
        visiting: set[str] = set()

        def visit(node: str) -> None:
            if node in visited:
                return
            if node in visiting:
                raise ValueError(f"Circular dependency detected at {node}")
            if node not in self._agents:
                return
            visiting.add(node)
            for dep in self._agents[node].depends_on:
                if dep in self._agents:
                    visit(dep)
            visiting.remove(node)
            visited.add(node)
            order.append(node)

        for target in targets:
            visit(target)

        return order

