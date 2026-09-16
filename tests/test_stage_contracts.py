"""Require behavioral contracts before later-stage implementations are added."""

import ast
import re
from pathlib import Path

import pytest

# Each implemented stage must retain these behavioral regression tests.
CONTRACTS = {
    "src/extract.py": {
        "test_model_output_validated_before_writes",
        "test_model_tools_cannot_write",
        "test_chunk_overlap",
        "test_triplet_deduplication",
    },
    "src/selfquiz.py": {
        "test_context_isolation_quiz",
        "test_empty_retriever_no_llm_call",
        "test_invalid_citations_rejected",
    },
    "src/core/mood.py": {
        "test_mood_inertia",
        "test_inertia_outward",
        "test_inertia_inward",
        "test_pierce",
        "test_decay_to_baseline",
        "test_clamp",
    },
    "src/validator.py": {"test_validator_rejects_cjk", "test_validator_strips_fences"},
    "src/writer.py": {
        "test_context_isolation_offtop",
        "test_context_isolation_quiz",
        "test_curator_text_uses_user_role",
        "test_token_budget_enforced",
        "test_people_facts_excluded_from_posts",
    },
    "src/core/llm_local.py": {"test_tokens_counted_with_tokenize"},
    "src/publish.py": {
        "test_outbox_worker_restart_does_not_duplicate_telegram",
        "test_outbox_crash_after_remote_acceptance_is_not_retried",
        "test_publication_trace_survives_enqueue_and_worker_restart",
    },
    "src/core/llm_vendor.py": {
        "test_claude_cli_uses_temporary_files_and_validates_json",
        "test_extract_json_rejects_ambiguous_or_invalid_results",
        "test_curator_validates_references_before_writing",
    },
    "src/core/schedule.py": {
        "test_sleep_plan_preserves_literal_complexity_and_mood_formula",
        "test_sleep_debt_uses_actual_hours_and_literal_clamp",
        "test_sleep_and_class_blackouts_include_boundaries_but_allow_breaks",
        "test_schedule_rejects_naive_times_and_measures_repeated_hour",
    },
    "src/orchestrator.py": {"test_state_machine_is_pure"},
    "src/bot.py": {"test_model_calls_use_task_queue"},
}


@pytest.mark.parametrize("module, required", CONTRACTS.items())
def test_behavioral_contracts_required_when_stage_is_implemented(module, required):
    if not Path(module).exists():
        return
    declared = {
        node.name
        for path in Path("tests").rglob("test_*.py")
        for node in ast.walk(ast.parse(path.read_text()))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert required <= declared, (
        f"Missing behavioral tests for {module}: {required - declared}"
    )


def test_open_decisions_match_code_registry():
    marker = re.compile(r"TODO\(([A-Z][A-Z0-9-]+)\)")
    tracked = set(marker.findall(Path("docs/TODO.md").read_text()))
    found = set()
    for directory in ("src", "tests", "scripts", "config", "prompts", "library"):
        for path in Path(directory).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".yaml", ".md"}:
                found.update(marker.findall(path.read_text()))
    assert found == tracked, (
        f"Unregistered: {found - tracked}; stale: {tracked - found}"
    )
