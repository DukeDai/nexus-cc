import pytest
from src.agent.control import ControlChannel
from src.agent.plan import Plan, PlanStep, PlanStepKind
from src.agents.base import AgentRole
from src.tui.plan_panel import PlanPanel


@pytest.mark.asyncio
async def test_subplan_node_renders_with_role_label():
    plan = Plan(plan_id="p1", spec="test", steps=[
        PlanStep(id="s1", kind=PlanStepKind.SUBPLAN,
                 intent="spec the auth flow", tool="spec the auth flow",
                 role=AgentRole.SPECIFIER),
    ])
    channel = ControlChannel()
    panel = PlanPanel(plan=plan, channel=channel)
    rendered = panel.render_plan_tree()
    assert "SUBPLAN" in rendered
    assert "SPECIFIER" in rendered
    assert "spec the auth flow" in rendered
