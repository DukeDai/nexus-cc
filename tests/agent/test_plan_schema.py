from src.agent.plan import PlanStep, PlanStepKind, OnFailure
from src.agents.base import AgentRole


def test_subplan_kind_exists():
    assert PlanStepKind.SUBPLAN.value == "subplan"


def test_retry_with_feedback_enum_exists():
    assert OnFailure.RETRY_WITH_FEEDBACK.value == "retry_with_feedback"


def test_plan_step_has_role_and_subplan_args():
    step = PlanStep(
        id="step-1",
        kind=PlanStepKind.SUBPLAN,
        intent="spec the new feature",
        role=AgentRole.SPECIFIER,
        subplan_args={"scope": "src/auth/"},
        on_failure=OnFailure.ASK,
    )
    assert step.role == AgentRole.SPECIFIER
    assert step.subplan_args == {"scope": "src/auth/"}


def test_plan_step_role_optional_for_non_subplan_kinds():
    step = PlanStep(
        id="step-2",
        kind=PlanStepKind.TOOL,
        intent="Read",
        tool="Read",
        args={"path": "config.yml"},
    )
    assert step.role is None
    assert step.subplan_args is None
