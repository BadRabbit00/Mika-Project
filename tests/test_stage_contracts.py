"""Require behavioral contracts before later-stage implementations are added."""

import ast
from pathlib import Path

import pytest

# TODO(STAGE-CONTRACTS): each later stage must supply these behavioral tests.
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
