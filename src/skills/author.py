"""
Skill Authoring System — Nexus Self-Improvement

Automatically authors skills based on captured mistake patterns.
These skills are loaded by agents before executing similar tasks.
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from datetime import datetime


@dataclass
class SkillMetadata:
    """Metadata for an authored skill."""
    name: str
    description: str
    category: str
    trigger_patterns: list[str]
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    author: str = "nexus_self_improvement"
    version: str = "1.0.0"
    confidence: float = 0.8  # Based on how often this pattern occurred
    times_triggered: int = 0


class SkillAuthor:
    """
    Authors new skills from mistake patterns and successful recoveries.
    
    Usage:
        author = SkillAuthor("/path/to/skills/")
        
        # After a mistake was fixed
        author.author_from_fix(
            mistake_description="SQL injection via string formatting",
            fix_applied="Used parameterized queries",
            file_context="database queries",
            trigger_keywords=["sql", "query", "execute", "database"]
        )
    """
    
    SKILL_TEMPLATE = '''---
name: {name}
description: "{description}"
version: {version}
author: {author}
metadata:
  nexus:
    category: {category}
    trigger_patterns: {trigger_patterns}
    confidence: {confidence}
    times_triggered: {times_triggered}
    created_at: "{created_at}"
---

# {title}

## When to Apply

This skill activates when:
{when_to_apply}

## What to Do

### Diagnosis
{diagnosis}

### Resolution
{resolution}

## Verification

After applying the fix:
- [ ] Security scan passes with no findings
- [ ] Related tests still pass
- [ ] No regression in other areas

## Related Mistakes

{related_mistakes}
'''
    
    def __init__(self, skills_dir: str = "~/.nexus/skills"):
        self.skills_dir = Path(skills_dir).expanduser()
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._skill_registry: dict[str, SkillMetadata] = {}
        self._load_registry()
    
    def _load_registry(self) -> None:
        """Load existing skill metadata into memory."""
        registry_path = self.skills_dir / "registry.json"
        if registry_path.exists():
            with open(registry_path) as f:
                data = json.load(f)
                for name, meta in data.items():
                    self._skill_registry[name] = SkillMetadata(**meta)
    
    def _save_registry(self) -> None:
        """Persist skill registry to disk."""
        registry_path = self.skills_dir / "registry.json"
        data = {name: asdict(meta) for name, meta in self._skill_registry.items()}
        with open(registry_path, 'w') as f:
            json.dump(data, f, indent=2)
    
    def author_from_fix(
        self,
        mistake_description: str,
        fix_applied: str,
        file_context: str,
        trigger_keywords: list[str],
        category: str = "general",
    ) -> str:
        """
        Author a new skill from a mistake-fix pair.
        
        Returns the skill name that was created.
        """
        # Generate skill name from keywords
        name_base = "_".join(trigger_keywords[:3]).lower().replace(" ", "_")
        name = f"{name_base}_{int(time.time() % 10000)}"
        
        metadata = SkillMetadata(
            name=name,
            description=f"Auto-generated skill from mistake: {mistake_description[:100]}",
            category=category,
            trigger_patterns=trigger_keywords,
        )
        
        # Build the skill content
        skill_content = self.SKILL_TEMPLATE.format(
            name=name,
            description=metadata.description,
            version=metadata.version,
            author=metadata.author,
            category=category,
            trigger_patterns=json.dumps(trigger_keywords),
            confidence=metadata.confidence,
            times_triggered=metadata.times_triggered,
            created_at=metadata.created_at,
            title=mistake_description[:60],
            when_to_apply=self._generate_when_to_apply(trigger_keywords),
            diagnosis=self._generate_diagnosis(mistake_description, file_context),
            resolution=self._generate_resolution(fix_applied),
            related_mistakes=self._generate_related_mistakes(mistake_description),
        )
        
        # Write the skill file
        skill_path = self.skills_dir / f"{name}.md"
        with open(skill_path, 'w') as f:
            f.write(skill_content)
        
        # Update registry
        self._skill_registry[name] = metadata
        self._save_registry()
        
        return name
    
    def _generate_when_to_apply(self, keywords: list[str]) -> str:
        """Generate 'when to apply' section from keywords."""
        lines = []
        for kw in keywords:
            lines.append(f"- Task involves `{kw}`")
        return "\n".join(lines) if lines else "- Task matches related keywords"
    
    def _generate_diagnosis(self, mistake: str, context: str) -> str:
        """Generate diagnosis section."""
        return f"""Check for the following patterns in {context}:
1. Direct string interpolation in queries/commands
2. User input used without validation
3. Missing parameterized alternatives

Pattern detected: {mistake}"""
    
    def _generate_resolution(self, fix: str) -> str:
        """Generate resolution section."""
        return f"""1. Identify the vulnerable code path
2. Replace string formatting with parameterized approach
3. Add input validation
4. Verify fix with security scan
5. Run related tests to ensure no regression

Fix applied: {fix}"""
    
    def _generate_related_mistakes(self, mistake: str) -> str:
        """Generate related mistakes section."""
        return f"- This mistake: {mistake[:100]}"
    
    def increment_trigger(self, skill_name: str) -> None:
        """Increment the trigger count when a skill was useful."""
        if skill_name in self._skill_registry:
            self._skill_registry[skill_name].times_triggered += 1
            # Increase confidence slightly when triggered
            meta = self._skill_registry[skill_name]
            meta.confidence = min(0.99, meta.confidence + 0.01)
            self._save_registry()
    
    def get_skill(self, name: str) -> Optional[dict]:
        """Get a skill by name, returns None if not found."""
        skill_path = self.skills_dir / f"{name}.md"
        if not skill_path.exists():
            return None
        
        with open(skill_path) as f:
            content = f.read()
        
        meta = self._skill_registry.get(name)
        return {
            "name": name,
            "content": content,
            "metadata": asdict(meta) if meta else None,
        }
    
    def find_relevant_skills(self, task_description: str) -> list[dict]:
        """Find skills relevant to a task description."""
        relevant = []
        task_lower = task_description.lower()
        
        for name, meta in self._skill_registry.items():
            # Check if any trigger keyword is in the task description
            for keyword in meta.trigger_patterns:
                if keyword.lower() in task_lower:
                    skill = self.get_skill(name)
                    if skill:
                        relevant.append(skill)
                    break
        
        return relevant
    
    def list_skills(self, category: Optional[str] = None) -> list[dict]:
        """List all skills, optionally filtered by category."""
        skills = []
        for name, meta in self._skill_registry.items():
            if category and meta.category != category:
                continue
            skill = self.get_skill(name)
            if skill:
                skills.append(skill)
        return skills


def asdict(obj: Any) -> Any:
    """Helper to convert dataclass to dict."""
    if hasattr(obj, '__dataclass_fields__'):
        return {f: asdict(getattr(obj, f)) for f in obj.__dataclass_fields__}
    elif isinstance(obj, list):
        return [asdict(i) for i in obj]
    elif isinstance(obj, dict):
        return {k: asdict(v) for k, v in obj.items()}
    return obj
