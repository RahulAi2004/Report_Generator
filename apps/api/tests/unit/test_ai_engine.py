"""
The AI layer.

The model is a stub throughout. What is being tested is not that a language
model works but that this application does not trust it: that what it returns is
compiled before anybody sees it, that a bad answer produces a refusal rather
than a broken report, and that no row of anybody's data is in what it is sent.
"""

from __future__ import annotations

import json

import pytest

from app.services.ai import context, engine
from app.services.ai.provider import AIError, OpenAICompatibleProvider
from tests.fixtures.schema import build_registry


@pytest.fixture
def registry():
    return build_registry()


class StubProvider:
    """Returns whatever it was given, as the model would."""

    def __init__(self, answer: dict):
        self.answer = answer
        self.prompts: list[tuple[str, str]] = []

    def complete(self, system, user, schema=None):
        self.prompts.append((system, user))
        return self.answer


def stub(monkeypatch, answer: dict) -> StubProvider:
    provider = StubProvider(answer)
    monkeypatch.setattr(engine, "build_provider", lambda session: provider)
    return provider


GOOD = {
    "primary_table": "sales_orders",
    "tables": ["sales_orders", "customers"],
    "columns": [
        {"id": "c1", "table": "customers", "field": "customer_name"},
        {"id": "c2", "table": "sales_orders", "field": "total_amount",
         "aggregation": "sum"},
    ],
    "group_by": [{"table": "customers", "field": "customer_name"}],
    "sort_by": [{"column_id": "c2", "direction": "desc"}],
}


# ---------------------------------------------------------------------------
# What the model is told
# ---------------------------------------------------------------------------
def test_the_schema_description_contains_no_data(registry):
    """
    The line this whole layer is built around. Sending sample values would help
    the model write filters, and would also mean this company's records leaving
    for somebody else's servers every time a suggestion is asked for.
    """
    described = context.describe(registry)

    assert "TABLE customers" in described
    assert "customer_name" in described
    # Nothing from the fixture's actual rows.
    for value in ("Acme", "@", "Ltd", "2026-"):
        assert value not in described


def test_measurable_and_date_columns_are_marked(registry):
    """So the model does not propose summing a name or grouping a total by day."""
    described = context.describe(registry)
    lines = {line.strip().split(":")[0]: line for line in described.splitlines()}

    assert "measurable" in lines["total_amount"]
    assert "date" in lines["order_date"]
    assert "measurable" not in lines["customer_name"]


def test_relationships_are_included_so_joins_are_possible(registry):
    described = context.describe(registry)
    assert "RELATIONSHIPS" in described
    assert "customers.customer_id" in described


def test_narrowing_to_a_few_tables_leaves_the_others_out(registry):
    described = context.describe(registry, focus=["customers"])
    assert "TABLE customers" in described
    assert "TABLE sales_orders" not in described


def test_asking_for_a_table_that_does_not_exist_falls_back_to_everything(registry):
    """
    A stale table name in a saved filter should not produce an empty prompt and
    a mystifying "the AI returned nothing".
    """
    described = context.describe(registry, focus=["no_such_table"])
    assert "TABLE customers" in described


# ---------------------------------------------------------------------------
# The model is not trusted
# ---------------------------------------------------------------------------
def test_a_valid_suggestion_is_marked_runnable(monkeypatch, registry):
    stub(monkeypatch, {"suggestions": [
        {"title": "Revenue by customer", "why": "Who is worth most.", "definition": GOOD},
    ]})
    found = engine.suggest(None, registry, None)

    assert len(found) == 1
    assert found[0].runnable is True
    assert found[0].problems == []
    assert found[0].summary["data_sources"] == 2


def test_a_suggestion_naming_a_table_that_does_not_exist_is_not_runnable(
    monkeypatch, registry
):
    """
    The point of compiling before offering. The model invents table names, and
    the user should learn that here rather than from a failed run.
    """
    bad = {**GOOD, "primary_table": "invented_table",
           "tables": ["invented_table"],
           "columns": [{"id": "c1", "table": "invented_table", "field": "x"}],
           "group_by": [], "sort_by": []}
    stub(monkeypatch, {"suggestions": [
        {"title": "Nonsense", "why": "", "definition": bad},
    ]})
    found = engine.suggest(None, registry, None)

    assert found[0].runnable is False
    assert any("invented_table" in problem for problem in found[0].problems)


def test_an_illegal_aggregation_is_caught_before_it_is_offered(monkeypatch, registry):
    """Summing a name is a thing models do; the compiler is what notices."""
    bad = {
        "primary_table": "customers", "tables": ["customers"],
        "columns": [{"id": "c1", "table": "customers", "field": "customer_name",
                     "aggregation": "sum"}],
    }
    stub(monkeypatch, {"suggestions": [{"title": "Sum of names", "why": "",
                                        "definition": bad}]})
    found = engine.suggest(None, registry, None)

    assert found[0].runnable is False
    assert found[0].problems


def test_a_definition_in_the_wrong_shape_produces_one_readable_problem(
    monkeypatch, registry
):
    """Not a stack of validation errors nobody will read."""
    stub(monkeypatch, {"suggestions": [
        {"title": "Malformed", "why": "", "definition": {"columns": "not a list"}},
    ]})
    found = engine.suggest(None, registry, None)

    assert found[0].runnable is False
    assert len(found[0].problems) == 1


def test_an_answer_with_no_reports_in_it_is_refused(monkeypatch, registry):
    stub(monkeypatch, {"suggestions": [{"title": "just words", "why": "no definition"}]})
    with pytest.raises(AIError) as raised:
        engine.suggest(None, registry, None)
    assert "none of what it returned was a report" in str(raised.value)


def test_an_answer_that_is_not_a_list_is_refused(monkeypatch, registry):
    stub(monkeypatch, {"suggestions": "a lovely idea"})
    with pytest.raises(AIError):
        engine.suggest(None, registry, None)


# ---------------------------------------------------------------------------
# Asking a question
# ---------------------------------------------------------------------------
def test_a_question_becomes_a_compiled_report(monkeypatch, registry):
    stub(monkeypatch, {
        "title": "Revenue by customer", "why": "Answers who spends most.",
        "confidence": "high", "assumptions": [], "definition": GOOD,
    })
    answer = engine.ask(None, registry, None, "Which customers spend the most?")

    assert answer.runnable is True
    assert answer.confidence == "high"
    assert answer.title == "Revenue by customer"


def test_the_question_reaches_the_model(monkeypatch, registry):
    provider = stub(monkeypatch, {"definition": GOOD, "title": "x"})
    engine.ask(None, registry, None, "Which customers spend the most?")

    _, user = provider.prompts[0]
    assert "Which customers spend the most?" in user
    assert "TABLE sales_orders" in user


def test_assumptions_and_low_confidence_survive(monkeypatch, registry):
    """
    A report that quietly answers a different question than the one asked is
    worse than one that says what it assumed.
    """
    stub(monkeypatch, {
        "title": "Unpaid invoices", "why": "", "confidence": "low",
        "assumptions": ["Took 'unpaid' to mean status is not Paid",
                        "Ignored partially paid invoices"],
        "definition": GOOD,
    })
    answer = engine.ask(None, registry, None, "Show me unpaid invoices")

    assert answer.confidence == "low"
    assert len(answer.assumptions) == 2
    assert "unpaid" in answer.assumptions[0]


def test_an_answer_with_no_definition_says_how_to_ask_better(monkeypatch, registry):
    stub(monkeypatch, {"title": "I am not sure what you mean"})
    with pytest.raises(AIError) as raised:
        engine.ask(None, registry, None, "something vague")
    assert "rephrasing" in str(raised.value)


def test_an_empty_question_is_refused_before_the_model_is_called(monkeypatch, registry):
    provider = stub(monkeypatch, {"definition": GOOD})
    with pytest.raises(AIError):
        engine.ask(None, registry, None, "   ")
    assert provider.prompts == []


# ---------------------------------------------------------------------------
# Parsing what a model actually returns
# ---------------------------------------------------------------------------
def test_json_wrapped_in_a_fence_is_still_read():
    """
    Models wrap JSON in fences even when told not to. Failing here would discard
    a perfectly good answer over formatting.
    """
    read = OpenAICompatibleProvider._as_json
    assert read('```json\n{"a": 1}\n```') == {"a": 1}
    assert read('```\n{"a": 1}\n```') == {"a": 1}
    assert read('{"a": 1}') == {"a": 1}


def test_json_buried_in_prose_is_still_read():
    read = OpenAICompatibleProvider._as_json
    assert read('Here is your report:\n{"a": 1}\nHope that helps!') == {"a": 1}


def test_prose_with_no_json_at_all_says_what_to_do():
    with pytest.raises(AIError) as raised:
        OpenAICompatibleProvider._as_json("I am afraid I cannot help with that.")
    assert "more specific" in str(raised.value)


def test_a_json_array_is_not_an_answer():
    """The shape is agreed; a list where an object belongs is a failure."""
    with pytest.raises(AIError):
        OpenAICompatibleProvider._as_json(json.dumps([1, 2, 3]))
