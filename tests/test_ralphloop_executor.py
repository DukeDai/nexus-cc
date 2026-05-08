"""Tests for the RalphLoopExecutor — 6-layer unified executor.

These tests verify that all 6 layers are properly initialized and wired.
Since RalphLoopExecutor uses relative imports (from ..context.wal etc.),
we test it through the actual module imports and through the CLI run path.
"""

from __future__ import annotations

import sys
import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Add src/ to path (same pattern as test_cli.py)
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


# ─── Layer 1: WALManager ────────────────────────────────────────────────────

class TestWALManager:
    """Layer 1: WALManager write-ahead log."""

    def test_wal_init(self):
        from context.wal import WALManager
        wal = WALManager(wal_dir=Path("/tmp/test_wal"))
        assert wal is not None
        wal.close()

    def test_wal_records_transition(self):
        from context.wal import WALManager
        wal = WALManager(wal_dir=Path("/tmp/test_wal_transition"))
        wal.log_transition(from_state="PLAN", to_state="ACT", trigger="COMPLETE")
        wal.flush()
        # WAL should have recorded the transition
        plan = wal.get_recovery_plan()
        assert plan is not None
        wal.close()

    def test_wal_recovery_plan_generated_after_crash(self):
        from context.wal import WALManager
        wal = WALManager(wal_dir=Path("/tmp/test_wal_recovery"))
        wal.log_transition("PLAN", "ACT", "COMPLETE")
        wal.log_transition("ACT", "VERIFY", "COMPLETE")
        wal.log_transition("VERIFY", "REFLECT", "CRASH")
        wal.flush()
        plan = wal.get_recovery_plan()
        assert plan is not None
        wal.close()


# ─── Layer 2: CheckpointManager ─────────────────────────────────────────────

class TestCheckpointManager:
    """Layer 2: CheckpointManager save/restore."""

    def test_checkpoint_init(self):
        from context.checkpoint import CheckpointManager
        ckpt = CheckpointManager(db_path="/tmp/test_ckpt.db")
        assert ckpt is not None

    def test_checkpoint_save_and_restore(self):
        from context.checkpoint import CheckpointManager
        ckpt = CheckpointManager(db_path="/tmp/test_ckpt_save.db")
        state_json = '{"task": "test task", "state": "PLAN"}'
        ckpt_id = ckpt.save_checkpoint(
            state=state_json,
            task_index=0,
            retry_count=0,
            context_usage=0.5,
            task_queue=[],
            error_log=[],
        )
        assert ckpt_id is not None
        restored = ckpt.load_checkpoint(ckpt_id)
        assert restored is not None
        assert "PLAN" in restored["state"]


# ─── Layer 3: SelfEvolutionEngine ──────────────────────────────────────────

class TestSelfEvolutionEngine:
    """Layer 3: SelfEvolutionEngine."""

    def test_engine_init(self):
        from self_evolution.engine import SelfEvolutionEngine
        from pathlib import Path
        engine = SelfEvolutionEngine(
            skills_dir=Path("/tmp/test_skills"),
            error_log_path=Path("/tmp/test_errors.jsonl"),
        )
        # SelfEvolutionEngine has monitor_error, analyze_and_capture, etc.
        assert hasattr(engine, 'monitor_error')
        assert hasattr(engine, 'analyze_and_capture')

    def test_monitor_error_no_raise(self):
        from self_evolution.engine import SelfEvolutionEngine
        from pathlib import Path
        engine = SelfEvolutionEngine(
            skills_dir=Path("/tmp/test_skills2"),
            error_log_path=Path("/tmp/test_errors2.jsonl"),
        )
        # monitor_error(tool_name, tool_args, tool_result, task_context)
        result = engine.monitor_error(
            tool_name="bash",
            tool_args={"command": "ls"},
            tool_result="error: command not found",
            task_context="test task",
        )
        assert isinstance(result, bool)


# ─── Layer 4: ModelRouter ──────────────────────────────────────────────────

class TestModelRouter:
    """Layer 4: ModelRouter."""

    def test_router_init(self):
        from llm.model_router import ModelRouter
        from llm.client import Provider
        router = ModelRouter(
            api_keys={Provider.OPENAI: "test-key"},
            custom_models={},
        )
        assert router is not None

    def test_select_model_returns_str(self):
        from llm.model_router import ModelRouter, ModelConfig
        from llm.client import Provider
        router = ModelRouter(
            custom_models={
                "gpt-4o-mini": ModelConfig(
                    name="gpt-4o-mini",
                    provider=Provider.OPENAI,
                    supports_tools=True,
                ),
            },
        )
        result = router.select_model("NexusTaskType.SIMPLE")
        assert isinstance(result, str), f"Expected str, got {type(result)}"

    def test_select_model_returns_valid_key(self):
        from llm.model_router import ModelRouter, ModelConfig
        from llm.client import Provider
        router = ModelRouter(
            custom_models={
                "gpt-4o-mini": ModelConfig(
                    name="gpt-4o-mini",
                    provider=Provider.OPENAI,
                    supports_tools=True,
                ),
            },
        )
        result = router.select_model("NexusTaskType.SIMPLE")
        assert result in router.models, f"Model {result} not in router.models"


# ─── Layer 5: SubagentIntegration ──────────────────────────────────────────

class TestSubagentIntegration:
    """Layer 5: SubagentIntegration parallel subagent execution."""

    def test_parallel_uses_threadpool(self):
        """run_implementer_with_review uses ThreadPoolExecutor for parallel subagents."""
        from ralphloop.subagent_integration import SubagentIntegration
        source = inspect.getsource(SubagentIntegration.run_implementer_with_review)
        assert "ThreadPoolExecutor" in source, "Should use ThreadPoolExecutor"
        assert "submit" in source, "Should submit tasks to executor"

    def test_run_agent_loop_no_typo(self):
        """run_agent_loop (lowercase) should be used, NOT run_agent_Loop."""
        from ralphloop import subagent_integration
        source = inspect.getsource(subagent_integration.SubagentIntegration)
        assert "run_agent_Loop" not in source, "Typo run_agent_Loop still present"
        assert "run_agent_loop" in source, "Correct run_agent_loop not found"


# ─── Layer 6: TDDEnforcer ─────────────────────────────────────────────────

class TestTDDEnforcer:
    """Layer 6: TDDEnforcer."""

    def test_tdd_enforcer_init(self):
        from ralphloop.tdd_enforcer import TDDEnforcer
        enforcer = TDDEnforcer()
        assert enforcer is not None
        # TDDEnforcer starts a cycle when initialized
        assert hasattr(enforcer, 'run_red')

    def test_run_red_method_exists(self):
        """TDDEnforcer should have run_red (RED phase of TDD)."""
        from ralphloop.tdd_enforcer import TDDEnforcer
        enforcer = TDDEnforcer()
        assert hasattr(enforcer, 'run_red')
        assert callable(enforcer.run_red)


# ─── WAL wiring to run_agent_loop ─────────────────────────────────────────

class TestWALWiring:
    """Verify WAL is passed to run_agent_loop in executor plan/review phases."""

    def test_wal_forwarded_in_execute_plan(self):
        from ralphloop.executor import RalphLoopExecutor
        source = inspect.getsource(RalphLoopExecutor._execute_plan)
        assert "wal=self._wal" in source

    def test_agent_loop_accepts_wal_param(self):
        from ralphloop.agent_loop import run_agent_loop
        sig = inspect.signature(run_agent_loop)
        assert "wal" in sig.parameters

    def test_wal_only_in_llm_driven_phases(self):
        """WAL forwarding only in LLM-driven phases (_execute_plan).
        _execute_reflect is sync, no run_agent_loop needed."""
        from ralphloop.executor import RalphLoopExecutor
        plan_src = inspect.getsource(RalphLoopExecutor._execute_plan)
        reflect_src = inspect.getsource(RalphLoopExecutor._execute_reflect)
        # Plan uses LLM → WAL forwarded
        assert "wal=self._wal" in plan_src
        # Reflect is sync → no WAL needed (doesn't call run_agent_loop)
        assert "wal=self._wal" not in reflect_src


class TestSixLayersVisible:
    """Verify all 6 layer attributes exist on RalphLoopExecutor.__init__."""

    def test_executor_init_signature_has_all_layer_flags(self):
        from ralphloop.executor import RalphLoopExecutor
        sig = list(inspect.signature(RalphLoopExecutor.__init__).parameters.keys())
        for p in ["enable_wal", "enable_checkpoint", "enable_self_evolution",
                  "enable_model_router", "enable_parallel_subagents", "enable_tdd"]:
            assert p in sig, f"Missing parameter: {p}"

    def test_executor_layer_creation_methods_exist(self):
        from ralphloop.executor import RalphLoopExecutor
        for m in ["_init_wal", "_init_checkpoint", "_init_self_evolution",
                  "_init_model_router", "_init_subagent_integration", "_init_tdd_enforcer"]:
            assert hasattr(RalphLoopExecutor, m), f"Missing method: {m}"

    def test_executor_all_six_layers_init(self):
        """Instantiate RalphLoopExecutor and verify all 6 layers are non-None."""
        from ralphloop.executor import RalphLoopExecutor
        ex = RalphLoopExecutor(workdir="/tmp/test_six_layers", enable_tdd=True)
        assert ex._wal is not None, "WAL not initialized"
        assert ex._ckpt is not None, "Checkpoint not initialized"
        assert ex._evo is not None, "SelfEvolution not initialized"
        assert ex._router is not None, "ModelRouter not initialized"
        assert ex._si is not None, "SubagentIntegration not initialized"
        assert ex._tdd is not None, "TDDEnforcer not initialized"
