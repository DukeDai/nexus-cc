"""Model policy: declarative mapping of ModelHint -> model name.

Resolution precedence (highest wins):
    1. cli_override  — explicit --model flag from CLI (overrides everything)
    2. per_role      — from .nexus/policy.yaml under `per_role`
    3. env_overrides — NEXUS_MODEL_<HINT> environment variables
    4. defaults      — DEFAULT_POLICY baked into this module

If nothing resolves, raise ValueError so callers fail loudly rather than
silently fall back to an unexpected model.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class ModelHint(Enum):
    """Where a model is being invoked from — drives default model selection."""

    PLANNER = "planner"              # plan generation (AgentRuntime.plan)
    CRITIQUE = "critique"            # critique sub-steps
    VERIFIER_SECURITY = "verifier_security"   # security checks (deliberate cost-downgrade)
    VERIFIER_REVIEW = "verifier_review"       # general review/verification
    EVOLVER = "evolver"              # prompt evolution / self-improvement


# Hardcoded defaults. Centralized here so the runtime fallback is unambiguous.
# VERIFIER_SECURITY gets the cheap model intentionally (per v1.2 decision).
DEFAULT_POLICY: dict[ModelHint, str] = {
    ModelHint.PLANNER: "claude-sonnet-4-6",
    ModelHint.CRITIQUE: "claude-sonnet-4-6",
    ModelHint.VERIFIER_SECURITY: "claude-haiku-4-5",
    ModelHint.VERIFIER_REVIEW: "claude-sonnet-4-6",
    ModelHint.EVOLVER: "claude-sonnet-4-6",
}


def _hint_env_var(hint: ModelHint) -> str:
    """Map ModelHint -> NEXUS_MODEL_<HINT> env var name."""
    return f"NEXUS_MODEL_{hint.value.upper()}"


@dataclass
class ModelPolicy:
    """Resolved policy. Layered: cli_override > per_role > env_overrides > defaults."""

    cli_override: str | None = None
    per_role: dict[str, str] = field(default_factory=dict)
    defaults: dict[ModelHint, str] = field(default_factory=lambda: dict(DEFAULT_POLICY))
    env_overrides: dict[ModelHint, str] = field(default_factory=dict)

    @classmethod
    def load(
        cls,
        project_root: Path,
        cli_model: str | None = None,
    ) -> "ModelPolicy":
        """Construct a policy from .nexus/policy.yaml + env vars + CLI flag.

        Args:
            project_root: Project directory. `.nexus/policy.yaml` is read if present.
            cli_model: Value of `--model` flag if provided by the user.
        """
        defaults: dict[ModelHint, str] = dict(DEFAULT_POLICY)
        per_role: dict[str, str] = {}

        policy_yaml = project_root / ".nexus" / "policy.yaml"
        if policy_yaml.exists():
            try:
                import yaml  # type: ignore[import-untyped]

                with policy_yaml.open("r") as f:
                    data = yaml.safe_load(f) or {}
            except Exception as exc:  # malformed YAML → ignore, log, keep defaults
                logger.warning("Failed to parse %s: %s — using defaults", policy_yaml, exc)
                data = {}

            # Section: defaults (override DEFAULT_POLICY)
            for hint_name, model_name in (data.get("defaults") or {}).items():
                try:
                    hint = ModelHint(hint_name)
                except ValueError:
                    logger.warning("Unknown hint in policy.yaml defaults: %s", hint_name)
                    continue
                defaults[hint] = str(model_name)

            # Section: per_role
            per_role = {str(k): str(v) for k, v in (data.get("per_role") or {}).items()}

        # Section: env_overrides (highest priority after cli_override)
        env_overrides: dict[ModelHint, str] = {}
        for hint in ModelHint:
            raw = os.environ.get(_hint_env_var(hint))
            if raw:
                env_overrides[hint] = raw

        return cls(
            cli_override=cli_model,
            per_role=per_role,
            defaults=defaults,
            env_overrides=env_overrides,
        )

    def resolve(self, hint: ModelHint, role: str | None = None) -> str:
        """Resolve a model name for the given hint (+ optional role).

        Precedence: cli_override > per_role[role] > env_overrides[hint] > defaults[hint]
        """
        if self.cli_override:
            return self.cli_override
        if role and role in self.per_role:
            return self.per_role[role]
        if hint in self.env_overrides:
            return self.env_overrides[hint]
        if hint in self.defaults:
            return self.defaults[hint]
        raise ValueError(
            f"No model resolved for hint={hint.value!r} role={role!r}. "
            f"Set NEXUS_MODEL_{hint.value.upper()} or update policy.yaml."
        )

    @staticmethod
    def create_default_yaml(path: Path) -> None:
        """Write a starter .nexus/policy.yaml so users have a template.

        All sections are commented out — defaults remain active until edited.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        body = """\
# Nexus Model Policy (v1.2)
# Uncomment any section to override the baked-in defaults.
#
# Available model names:
#   Anthropic (default):  claude-haiku-4-5, claude-sonnet-4-6, claude-opus-4-8
#   MiniMax (opt-in):     MiniMax-M3, MiniMax-M2.7   (Anthropic-compatible API)
#
# For MiniMax, also set:
#   ANTHROPIC_BASE_URL=https://api.minimaxi.com/anthropic
#   ANTHROPIC_AUTH_TOKEN=sk-cp-...   (or MINIMAX_API_KEY=...)

# defaults:
#   planner: claude-sonnet-4-6
#   critique: claude-sonnet-4-6
#   verifier_security: claude-haiku-4-5
#   verifier_review: claude-sonnet-4-6
#   evolver: claude-sonnet-4-6

# per_role:
#   implementer: claude-sonnet-4-6
#   specifier: claude-sonnet-4-6
#   security: claude-haiku-4-5
"""
        path.write_text(body)