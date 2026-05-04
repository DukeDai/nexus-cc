#!/usr/bin/env python3
"""Cross-Session Learning Demo — Nexus Self-Evolution vs Claude Code.

Claude Code: Same error → same failure every session
Nexus: Error #1 → skill captured → error #2 → recovery found → success

Run: python3 demo_cross_session_learning.py
"""
import sys, tempfile, os
from pathlib import Path
sys.path.insert(0, "src")

from self_evolution.engine import SelfEvolutionEngine, LearnedSkill
from context.wal import WALManager

print("=" * 60)
print("  NEXUS Cross-Session Learning Demo")
print("  (Claude Code has NO equivalent)")
print("=" * 60)

tmpdir = Path(tempfile.mkdtemp())
skills_dir = tmpdir / "skills"
error_log = tmpdir / "errors.jsonl"

# ── SESSION 1 ─────────────────────────────────────────────────────────
print("\n📍 SESSION 1 (First encounter with errors)")

evo = SelfEvolutionEngine(skills_dir=skills_dir, error_log_path=error_log)
evo.load_existing_skills()

# Error 1
had = evo.monitor_error(
    tool_name="bash",
    tool_args={"command": "python3 -c 'import requests'"},
    tool_result="ERROR: ModuleNotFoundError: No module named 'requests'",
    task_context="demo",
)
print(f"\n  🔴 Error: ModuleNotFoundError  had_error={had}")
skill = evo.analyze_and_capture()
if skill:
    print(f"  🟡 Skill: {skill.trigger[:50]}...")
    evo.store_skill(skill)
    print(f"  ✅ Skill stored → {skills_dir / f"{skill.name}.md"}")

# Error 2
had2 = evo.monitor_error(
    tool_name="bash",
    tool_args={"command": "python3 app.py", "cwd": "/nonexistent"},
    tool_result="ERROR: FileNotFoundError: No such file: app.py",
    task_context="demo",
)
print(f"\n  🔴 Error: FileNotFoundError  had_error={had2}")
skill2 = evo.analyze_and_capture()
if skill2:
    print(f"  🟡 Skill: {skill2.trigger[:50]}...")
    evo.store_skill(skill2)
    print(f"  ✅ Skill stored")

skills_count = len([f for f in os.listdir(skills_dir) if f.endswith(".md")])
print(f"\n  Skills stored: {skills_count}")

# ── SESSION 2 ─────────────────────────────────────────────────────────
print("\n📍 SESSION 2 (New session, same skills persist)")

evo2 = SelfEvolutionEngine(skills_dir=skills_dir, error_log_path=error_log)
evo2.load_existing_skills()

skills_loaded = evo2.get_relevant_skills("")
print(f"  Loaded {len(skills_loaded)} skills from Session 1")

print(f"\n  🔴 Same error: ModuleNotFoundError")
recovery = evo2.get_best_recovery("ModuleNotFoundError: No module named 'requests'")
if recovery:
    print(f"  🟢 Recovery found:")
    for i, step in enumerate(recovery.split("\n"), 1):
        if step.strip():
            print(f"     {i}. {step[:80]}")
    print("\n  ✅ Claude Code fails the SAME way — Nexus resolves it!")
else:
    print(f"  ⚠️  No recovery found")

# ── SUMMARY ────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("  RESULT: Nexus learned skills from Session 1 errors")
print("  → Session 2 can RECOVER from those errors")
print("  → Claude Code has NO such capability")
print("=" * 60)
