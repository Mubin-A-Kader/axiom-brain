"""Eval metrics unit tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from axiom.evals.metrics import contains, exact_match


@pytest.mark.asyncio
async def test_exact_match_pass() -> None:
    score = await exact_match("Paris", "Paris", {})
    assert score == 1.0


@pytest.mark.asyncio
async def test_exact_match_fail() -> None:
    score = await exact_match("London", "Paris", {})
    assert score == 0.0


@pytest.mark.asyncio
async def test_exact_match_case_insensitive() -> None:
    score = await exact_match("PARIS", "paris", {})
    assert score == 1.0


@pytest.mark.asyncio
async def test_contains_pass() -> None:
    score = await contains("The capital of France is Paris.", "Paris", {})
    assert score == 1.0


@pytest.mark.asyncio
async def test_contains_fail() -> None:
    score = await contains("I don't know.", "Paris", {})
    assert score == 0.0


@pytest.mark.asyncio
async def test_llm_judge_parses_score() -> None:
    from axiom.evals.metrics import llm_judge
    from axiom.llm.client import CompletionResult, TokenUsage

    mock_result = CompletionResult(
        text='{"score": 0.9, "rationale": "Answers the question correctly."}',
        usage=TokenUsage(10, 20),
    )
    with patch("axiom.evals.metrics.get_llm_client") as mock_factory:
        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(return_value=mock_result)
        mock_factory.return_value = mock_llm

        score = await llm_judge("Paris", "Paris", {"question": "Capital of France?"})

    assert abs(score - 0.9) < 0.001
