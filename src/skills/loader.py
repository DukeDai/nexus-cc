"""
Skill Loader — Nexus Self-Improvement

Loads relevant skills for a task based on context and mistake patterns.
Agents query this loader before executing to get relevant guidance.
"""

import json
from pathlib import Path
from typing import Optional
from dataclasses import dataclass


@dataclass
class LoadedSkill:
    """A skill loaded for a specific task."""
    name: str
    content: str
    category: str
    trigger_reason: str  # Why this skill was selected


class SkillLoader:
    """
    Loads relevant skills for a task based on:
    1. Keywords in the task description
    2. Recent mistake patterns
    3. Project context (language, framework)
    
    Usage:
        loader = SkillLoader(project_path="/path/to/project")
        
        skills = loader.load_for_task(
            task_description="Add SQL query with user input sanitization",
            context={"language": "python", "framework": "fastapi"}
        )
        
        for skill in skills:
            print(f"Loaded skill: {skill.name}")
            print(skill.content)
    """
    
    def __init__(self, skills_dir: str = "~/.nexus/skills", project_path: Optional[str] = None):
        self.skills_dir = Path(skills_dir).expanduser()
        self.project_path = Path(project_path) if project_path else None
        self._registry: dict = {}
        self._load_registry()
    
    def _load_registry(self) -> None:
        """Load skill registry from disk."""
        registry_path = self.skills_dir / "registry.json"
        if registry_path.exists():
            with open(registry_path) as f:
                self._registry = json.load(f)
    
    def load_for_task(
        self,
        task_description: str,
        context: Optional[dict] = None,
        max_skills: int = 5,
    ) -> list[LoadedSkill]:
        """
        Load skills relevant to a task.
        
        Args:
            task_description: Natural language description of the task
            context: Optional context (language, framework, file types)
            max_skills: Maximum number of skills to load
        
        Returns:
            List of LoadedSkill objects ordered by relevance
        """
        loaded: list[LoadedSkill] = []
        task_lower = task_description.lower()
        context = context or {}
        
        # Score each skill by relevance
        scored_skills: list[tuple[float, LoadedSkill]] = []
        
        for skill_name, metadata in self._registry.items():
            score = 0.0
            trigger_reason = ""
            
            # Check trigger patterns
            for keyword in metadata.get("trigger_patterns", []):
                if keyword.lower() in task_lower:
                    score += 2.0
                    trigger_reason = f"matched keyword: {keyword}"
                    break
            
            # Boost for language match
            if "language" in context:
                lang_skill_names = ["python", "javascript", "typescript", "rust", "go"]
                for lang in lang_skill_names:
                    if lang in skill_name and context["language"].lower() == lang:
                        score += 1.5
            
            # Boost for framework match
            if "framework" in context:
                fw_skill_names = ["fastapi", "flask", "react", "django", "express"]
                for fw in fw_skill_names:
                    if fw in skill_name and context["framework"].lower() == fw:
                        score += 1.5
            
            # Boost for high confidence
            score += metadata.get("confidence", 0.5) * 0.5
            
            if score > 0:
                skill_path = self.skills_dir / f"{skill_name}.md"
                if skill_path.exists():
                    with open(skill_path) as f:
                        content = f.read()
                    
                    loaded_skill = LoadedSkill(
                        name=skill_name,
                        content=content,
                        category=metadata.get("category", "general"),
                        trigger_reason=trigger_reason,
                    )
                    scored_skills.append((score, loaded_skill))
        
        # Sort by score and return top N
        scored_skills.sort(key=lambda x: -x[0])
        return [s for _, s in scored_skills[:max_skills]]
    
    def load_for_mistake(
        self,
        mistake_category: str,
        mistake_description: str,
    ) -> list[LoadedSkill]:
        """
        Load skills specifically to prevent a recurring mistake.
        
        Args:
            mistake_category: Category of the mistake (e.g., "sql_injection")
            mistake_description: Description of what went wrong
        
        Returns:
            List of relevant prevention skills
        """
        loaded: list[LoadedSkill] = []
        desc_lower = mistake_description.lower()
        
        for skill_name, metadata in self._registry.items():
            # Check if skill category matches
            if metadata.get("category") == mistake_category:
                score = 1.0
            else:
                # Check if any trigger words in the description match
                score = 0.0
                for keyword in metadata.get("trigger_patterns", []):
                    if keyword.lower() in desc_lower:
                        score = 0.7
                        break
            
            if score > 0:
                skill_path = self.skills_dir / f"{skill_name}.md"
                if skill_path.exists():
                    with open(skill_path) as f:
                        content = f.read()
                    
                    loaded.append(LoadedSkill(
                        name=skill_name,
                        content=content,
                        category=metadata.get("category", "general"),
                        trigger_reason=f"mistake prevention: {mistake_category}",
                    ))
        
        return loaded
    
    def format_skill_context(self, skills: list[LoadedSkill]) -> str:
        """
        Format loaded skills into a context string for an agent prompt.
        
        Returns a markdown-formatted string to prepend to agent prompts.
        """
        if not skills:
            return ""
        
        formatted = "\n\n## Relevant Skills from Past Mistakes\n\n"
        formatted += "The following skills were learned from previous mistakes. Apply them to this task:\n\n"
        
        for skill in skills:
            formatted += f"---\n\n"
            formatted += f"### Skill: {skill.name}\n"
            formatted += f"**Category:** {skill.category} | **Why loaded:** {skill.trigger_reason}\n\n"
            formatted += skill.content
            formatted += "\n\n"
        
        formatted += "---\n"
        return formatted
