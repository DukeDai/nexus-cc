"""Tests for StepEditModal - Textual ModalScreen for editing one PlanStep."""
from __future__ import annotations

import json

import pytest

from src.agent.control import ControlChannel
from src.agent.plan import OnFailure, Plan, PlanStep, PlanStepKind
from src.tui.app import NexusApp
from src.tui.step_edit_modal import StepEditModal


def _make_step() -> PlanStep:
    return PlanStep(
        id="step_abc",
        kind=PlanStepKind.TOOL,
        intent="read the README",
        tool="Read",
        args={"path": "README.md", "start_line": 1},
        success_criteria="file contains 'Nexus'",
        on_failure=OnFailure.ABORT,
        timeout_s=42,
    )


def _make_plan_with_step() -> Plan:
    step = _make_step()
    return Plan(plan_id="plan_xyz", spec="test", steps=[step])


@pytest.mark.asyncio
async def test_modal_renders_with_six_fields_prepopulated():
    """StepEditModal opens with all 6 fields pre-populated from the step."""
    channel = ControlChannel()
    app = NexusApp(channel=channel)
    step = _make_step()

    async with app.run_test() as pilot:
        modal = StepEditModal(step=step, on_save=lambda s: None)
        await app.push_screen(modal)
        await pilot.pause()

        # Intent Input
        intent_input = modal.query_one("#intent-input")
        assert intent_input.value == "read the README"

        # Tool Select — value should match step.tool
        tool_select = modal.query_one("#tool-select")
        assert tool_select.value == "Read"

        # Args TextArea — should contain JSON of args
        args_area = modal.query_one("#args-textarea")
        parsed = json.loads(args_area.text)
        assert parsed == {"path": "README.md", "start_line": 1}

        # Success criteria
        success_input = modal.query_one("#success-input")
        assert success_input.value == "file contains 'Nexus'"

        # On failure Select
        failure_select = modal.query_one("#failure-select")
        assert failure_select.value == OnFailure.ABORT

        # Timeout Input
        timeout_input = modal.query_one("#timeout-input")
        assert timeout_input.value == "42"

        await pilot.press("ctrl+c")


@pytest.mark.asyncio
async def test_save_button_returns_modified_step():
    """Save dismisses the modal with a new PlanStep reflecting the edits."""
    channel = ControlChannel()
    app = NexusApp(channel=channel)
    step = _make_step()

    received: list[PlanStep] = []

    async with app.run_test() as pilot:
        modal = StepEditModal(step=step, on_save=received.append)
        await app.push_screen(modal)
        await pilot.pause()

        # Modify the intent field
        intent_input = modal.query_one("#intent-input")
        intent_input.value = "read the LICENSE"
        await pilot.pause()

        # Click the Save button
        save_button = modal.query_one("#save-button")
        save_button.press()
        await pilot.pause()
        await pilot.pause()

        # on_save was invoked exactly once
        assert len(received) == 1
        new_step = received[0]
        assert isinstance(new_step, PlanStep)
        # Intent reflects the edit
        assert new_step.intent == "read the LICENSE"
        # Other fields preserved
        assert new_step.id == step.id
        assert new_step.kind == step.kind
        assert new_step.tool == step.tool
        assert new_step.args == step.args
        assert new_step.success_criteria == step.success_criteria
        assert new_step.on_failure == step.on_failure
        assert new_step.timeout_s == step.timeout_s

        await pilot.press("ctrl+c")
