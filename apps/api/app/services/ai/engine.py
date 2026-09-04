"""
From a question to a report, with the compiler as the arbiter.

Every definition the model returns is put through the same build the report
builder uses before it is offered to anybody. That is what makes the whole
arrangement safe rather than merely careful: a suggestion naming a table that
does not exist, a column the user may not read, or an aggregation the type does
not allow comes back with the compiler's own diagnostics attached, and is
marked as not runnable.

Nothing is executed. A suggestion is a proposal; running it is the user opening
it in the builder and pressing the button themselves.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import Principal
from app.domain.report.engine import EngineOptions, ReportEngine
from app.domain.report.ir import ReportDefinition
from app.domain.schema.registry import SchemaRegistry
from app.services.ai import context
from app.services.ai.provider import AIError, build_provider

logger = logging.getLogger(__name__)

#: More than this and nobody reads them; fewer and the model plays safe.
MAX_SUGGESTIONS = 8


@dataclass
class Suggestion:
    title: str
    why: str
    definition: dict
    #: Whether the compiler accepted it. A suggestion that will not run is still
    #: shown, with the reason, rather than hidden -- it is usually one column
    #: away from working and the reason says which.
    runnable: bool = False
    problems: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    confidence: str | None = None
    assumptions: list[str] = field(default_factory=list)

    def as_payload(self) -> dict:
        return {
            "title": self.title,
            "why": self.why,
            "definition": self.definition,
            "runnable": self.runnable,
            "problems": self.problems,
            "summary": self.summary,
            "confidence": self.confidence,
            "assumptions": self.assumptions,
        }


def _engine(registry: SchemaRegistry) -> ReportEngine:
    return ReportEngine(
        registry,
        EngineOptions(
            max_joins=settings.query_max_joins,
            max_rows=settings.query_max_rows,
            max_subquery_depth=settings.query_max_subquery_depth + 1,
        ),
    )


def _check(registry: SchemaRegistry, raw: dict) -> tuple[bool, list[str], dict]:
    """
    Compile a proposed definition without running it.

    The model's output is not trusted at all: it is parsed by the same Pydantic
    model as a saved report, then built by the same engine. Both stages reject
    rather than repair.
    """
    try:
        definition = ReportDefinition.model_validate(raw)
    except ValidationError as error:
        return False, [_first_pydantic_message(error)], {}

    result = _engine(registry).build(definition)
    if result.ok:
        return True, [], result.summary

    problems = [
        item["message"] for item in result.diagnostics_payload()
        if item["severity"] == "error"
    ]
    return False, problems or ["This report could not be compiled."], result.summary


def _first_pydantic_message(error: ValidationError) -> str:
    """One readable sentence out of a validation error, not a stack of them."""
    for item in error.errors():
        location = ".".join(str(part) for part in item.get("loc", ()))
        message = item.get("msg", "is not valid")
        return f"{location or 'definition'}: {message}"
    return "The AI returned a report in a shape this tool does not accept."


# ---------------------------------------------------------------------------
def suggest(
    session: Session,
    registry: SchemaRegistry,
    principal: Principal,
    focus: list[str] | None = None,
    extra: str = "",
) -> list[Suggestion]:
    """
    Reports worth building, proposed from the schema.

    `focus` narrows the schema to particular tables, which is both cheaper and
    better: a model given sixty tables suggests something about all of them, and
    a model given three suggests something about those three.
    """
    provider = build_provider(session)
    schema = context.describe(registry, focus=focus)
    if not schema.strip():
        raise AIError("There are no tables you can read, so there is nothing to suggest.")

    user = (
        f"{context.SUGGEST_INSTRUCTION}\n\n"
        f"{context.aggregations_note()}\n\n"
        f"Return at most {MAX_SUGGESTIONS} suggestions.\n\n"
        + (f"What the user is interested in: {extra.strip()}\n\n" if extra.strip() else "")
        + f"SCHEMA\n------\n{schema}"
    )

    answer = provider.complete(context.SYSTEM_PROMPT, user)
    raw = answer.get("suggestions")
    if not isinstance(raw, list):
        raise AIError("The AI did not return a list of suggestions.")

    found: list[Suggestion] = []
    for item in raw[:MAX_SUGGESTIONS]:
        if not isinstance(item, dict) or not isinstance(item.get("definition"), dict):
            continue
        runnable, problems, summary = _check(registry, item["definition"])
        found.append(Suggestion(
            title=str(item.get("title") or "Untitled suggestion")[:190],
            why=str(item.get("why") or "")[:400],
            definition=item["definition"],
            runnable=runnable,
            problems=problems,
            summary=summary,
        ))

    if not found:
        raise AIError(
            "The AI answered, but none of what it returned was a report. "
            "Try again, or narrow it to a few tables."
        )
    return found


def ask(
    session: Session,
    registry: SchemaRegistry,
    principal: Principal,
    question: str,
    focus: list[str] | None = None,
) -> Suggestion:
    """One report answering one question."""
    if not question.strip():
        raise AIError("Ask a question first.")

    provider = build_provider(session)
    schema = context.describe(registry, focus=focus)
    user = (
        f"{context.ASK_INSTRUCTION}\n\n"
        f"{context.aggregations_note()}\n\n"
        f"QUESTION\n--------\n{question.strip()}\n\n"
        f"SCHEMA\n------\n{schema}"
    )

    answer = provider.complete(context.SYSTEM_PROMPT, user)
    raw = answer.get("definition")
    if not isinstance(raw, dict):
        raise AIError(
            "The AI answered without a report in it. Try rephrasing the question "
            "in terms of what you want to see: which figures, grouped by what."
        )

    runnable, problems, summary = _check(registry, raw)
    assumptions = answer.get("assumptions")
    return Suggestion(
        title=str(answer.get("title") or question.strip())[:190],
        why=str(answer.get("why") or "")[:400],
        definition=raw,
        runnable=runnable,
        problems=problems,
        summary=summary,
        confidence=str(answer.get("confidence") or "") or None,
        assumptions=[str(a)[:200] for a in assumptions][:6]
        if isinstance(assumptions, list) else [],
    )
