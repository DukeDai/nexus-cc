# Changelog

## v1.1.0 (2026-06-28)

### Added
- Sub-agent role wiring via SUBPLAN step kind + RoleRegistry (reuses existing role files unchanged)
- Three-layer memory: EpisodicIndex (WAL-derived), SemanticIndex (substring + opt-in embeddings), SkillIndex (wraps existing loader)
- Self-evolution feedback loop: Evolver + PromptTemplateRegistry with user approval gate
- Verification pipeline integration: VERIFY steps can reference named pipelines (security/tdd/test/review)
- Retry-with-feedback: `on_failure="retry_with_feedback"` feeds verifier errors back to LLM
- WAL v2 format with `format_version` header + `metadata` blocks; v1.0 WAL files still load
- New CLI commands: `nexus session migrate`, `nexus role`, `nexus memory`, `nexus skill`, `nexus prompt`, `nexus evolve`
- New TUI panels: VerifierPanel, MemoryPanel, ExecutionPanel
- New TUI modals: SkillPickerModal, EvolveApprovalModal, PromptHistoryViewerModal
- New TUI keybindings: V (verifier), M (memory), s (skill), E (evolve), Ctrl-r (re-run verifier)

### Changed
- `OnFailure` enum gains `RETRY_WITH_FEEDBACK`
- `PlanStepKind` enum gains `SUBPLAN`
- `PlanStep` gains optional `role`, `subplan_args`, `pipeline`, `pipeline_args` fields
- WAL records gain `format_version` and optional `metadata` blocks

### Migration
- v1.0 WAL files load in v1.1 without changes
- Optional: `nexus session migrate <plan_id>` produces a v2-normalized copy
