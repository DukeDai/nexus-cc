"""Prompt template registry with append-only version history.

Each template is stored at {path}/{name}.jsonl with one JSON record per version.
Revert writes a new version that copies the target version's prompt and resets
last_updated_walk_count to 0.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path


@dataclass
class PromptTemplate:
    name: str
    system_prompt: str
    version: int
    updated_at: datetime
    source_episodes: list[str] = field(default_factory=list)
    last_updated_walk_count: int = 0


class PromptTemplateRegistry:
    def __init__(self, path: Path):
        self._path = Path(path)
        self._path.mkdir(parents=True, exist_ok=True)

    def _file(self, name: str) -> Path:
        return self._path / f"{name}.jsonl"

    def get(self, name: str) -> PromptTemplate:
        """Return the current (latest) version of the named template."""
        records = self._read_all(name)
        if not records:
            raise KeyError(f"No template registered as {name!r}")
        return self._parse(records[-1])

    def update(self, name: str, template: PromptTemplate) -> None:
        """Append a new version to the template's history."""
        if template.name != name:
            raise ValueError(f"template.name={template.name} does not match key={name}")
        with self._file(name).open("a") as f:
            f.write(json.dumps(asdict(template), default=str) + "\n")

    def history(self, name: str) -> list[PromptTemplate]:
        """Return all versions of the template, oldest first."""
        records = self._read_all(name)
        return [self._parse(r) for r in records]

    def revert(self, name: str, target_version: int) -> PromptTemplate:
        """Write a new version that copies target_version's prompt; reset walk counter."""
        records = self._read_all(name)
        target = next((r for r in records if r["version"] == target_version), None)
        if target is None:
            raise ValueError(f"Version {target_version} not found in {name!r}")
        new_version = max(r["version"] for r in records) + 1
        reverted = PromptTemplate(
            name=name,
            system_prompt=target["system_prompt"],
            version=new_version,
            updated_at=datetime.now(),
            source_episodes=[f"revert@{target_version}"],
            last_updated_walk_count=0,
        )
        self.update(name, reverted)
        return reverted

    def _read_all(self, name: str) -> list[dict]:
        f = self._file(name)
        if not f.exists():
            return []
        return [json.loads(line) for line in f.read_text().splitlines() if line.strip()]

    @staticmethod
    def _parse(record: dict) -> PromptTemplate:
        return PromptTemplate(
            name=record["name"],
            system_prompt=record["system_prompt"],
            version=record["version"],
            updated_at=datetime.fromisoformat(record["updated_at"]),
            source_episodes=record.get("source_episodes", []),
            last_updated_walk_count=record.get("last_updated_walk_count", 0),
        )