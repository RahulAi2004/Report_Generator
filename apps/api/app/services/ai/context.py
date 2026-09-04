"""
What the model is told about the database.

Its shape and nothing else. Table names, column names, types, which columns can
be aggregated and which tables are related -- enough to write a report, and not
one row of anybody's data.

That line is deliberate and worth defending. Sending sample values would help
the model write better filters, and it would also mean this company's customer
records leaving for somebody else's servers every time a suggestion is asked
for. The columns already excluded from the registry -- credentials -- are not
here either, because this reads the same RBAC-filtered registry the report
builder does rather than the raw schema.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.schema.registry import Aggregation, DataType, SchemaRegistry

#: The schema has to fit in one request alongside the prompt and the answer.
#: A hundred and sixty tables does not: describing them all came to nearly
#: 37,000 characters, which providers refuse outright as too large and which
#: exhausts a per-minute token budget in three or four questions.
#:
#: Measured in characters rather than tokens because the ratio is close enough
#: (roughly four to one) and counting tokens would mean carrying a tokeniser
#: for every model anyone might configure.
MAX_CHARACTERS = 12_000
MAX_TABLES = 40
MAX_COLUMNS_PER_TABLE = 30

#: Types worth mentioning as measurable, so the model does not try to sum a name.
_NUMERIC = (DataType.INTEGER, DataType.DECIMAL)
_TEMPORAL = (DataType.DATE, DataType.DATETIME)


@dataclass
class Described:
    """The schema text, and what had to be left out of it."""

    text: str
    included: int
    total: int

    @property
    def trimmed(self) -> bool:
        return self.included < self.total


def describe(
    registry: SchemaRegistry,
    focus: list[str] | None = None,
    budget: int = MAX_CHARACTERS,
) -> str:
    """The schema as the model sees it."""
    return describe_in_full(registry, focus, budget).text


def describe_in_full(
    registry: SchemaRegistry,
    focus: list[str] | None = None,
    budget: int = MAX_CHARACTERS,
) -> Described:
    """
    The schema as the model sees it, and how much of it fitted.

    Written as compact text rather than JSON: the same information costs
    noticeably fewer tokens, and models follow a table-per-line layout more
    reliably than deeply nested objects.

    Bounded by size rather than only by count, because a table with sixty
    columns costs as much as ten small ones and the provider counts characters
    rather than tables.
    """
    every = registry.tables
    tables = every
    if focus:
        wanted = {name.lower() for name in focus}
        chosen = [t for t in tables if t.name.lower() in wanted]
        tables = chosen or tables

    # Most useful first: rows matter, and so does being connected to anything.
    # A large table nothing joins to is rarely what a report is about, and a
    # small one at the centre of the schema often is.
    joined: dict[str, int] = {}
    for relationship in registry.relationships:
        joined[relationship.left_table] = joined.get(relationship.left_table, 0) + 1
        joined[relationship.right_table] = joined.get(relationship.right_table, 0) + 1

    tables = sorted(
        tables,
        key=lambda t: (-(joined.get(t.name, 0)), -(t.estimated_rows or 0), t.name),
    )[:MAX_TABLES]
    names = {table.name for table in tables}

    lines: list[str] = []
    used = 0
    included = 0
    for table in tables:
        header = f"TABLE {table.name}"
        if table.display_name and table.display_name != table.name:
            header += f'  ("{table.display_name}")'
        if table.estimated_rows:
            header += f"  ~{table.estimated_rows} rows"
        lines.append(header)

        for column in table.columns[:MAX_COLUMNS_PER_TABLE]:
            marks = []
            if column.is_primary_key:
                marks.append("pk")
            if column.is_foreign_key:
                marks.append("fk")
            if column.data_type in _NUMERIC:
                marks.append("measurable")
            if column.data_type in _TEMPORAL:
                marks.append("date")
            if column.mask_policy.value != "none":
                # Said out loud so the model does not build a report whose whole
                # point is a column that comes back obscured.
                marks.append("masked")
            suffix = f"  [{', '.join(marks)}]" if marks else ""
            lines.append(f"  {column.name}: {column.data_type.value}{suffix}")

        if len(table.columns) > MAX_COLUMNS_PER_TABLE:
            lines.append(
                f"  … and {len(table.columns) - MAX_COLUMNS_PER_TABLE} more columns"
            )
        lines.append("")

        # Stop at the budget rather than being refused by the provider for
        # being too large -- a partial schema produces a usable answer, and a
        # rejected request produces none.
        used = sum(len(line) + 1 for line in lines)
        included += 1
        if used >= budget:
            break

    described = {table.name for table in tables[:included]}
    joins = [
        f"  {r.left_table}.{r.left_column} = {r.right_table}.{r.right_column}"
        for r in registry.relationships
        if r.left_table in described and r.right_table in described
    ]
    if joins:
        lines.append("RELATIONSHIPS (these tables can be joined):")
        lines.extend(joins[:100])

    if included < len(every):
        lines.append("")
        lines.append(
            f"NOTE: this is {included} of {len(every)} tables -- the ones most "
            "connected to the rest. Ask about others by name and they will be "
            "included."
        )

    return Described(text="\n".join(lines), included=included, total=len(every))


def aggregations_note() -> str:
    """What the compiler will accept, so the model does not propose what it will not."""
    return (
        "Aggregations: "
        + ", ".join(a.value for a in Aggregation if a is not Aggregation.NONE)
        + ". Only measurable columns accept sum and avg; anything can be counted."
    )


def definition_schema() -> dict:
    """
    The report IR as JSON Schema, for providers that can decode against one.

    Hand-written rather than taken from Pydantic: the generated schema carries
    every optional field, every $ref and every enum in the IR, which is large
    enough that some providers refuse it outright. This is the part a model
    needs to get right, and nothing else.
    """
    column = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "table": {"type": "string"},
            "field": {"type": "string"},
            "display_name": {"type": "string"},
            "aggregation": {
                "type": "string",
                "enum": ["none", "count", "count_distinct", "sum", "avg", "min", "max"],
            },
        },
        "required": ["id", "table", "field"],
    }
    table_field = {
        "type": "object",
        "properties": {"table": {"type": "string"}, "field": {"type": "string"}},
        "required": ["table", "field"],
    }
    return {
        "type": "object",
        "properties": {
            "primary_table": {"type": "string"},
            "tables": {"type": "array", "items": {"type": "string"}},
            "columns": {"type": "array", "items": column},
            "group_by": {"type": "array", "items": table_field},
            # A list, not one object. Declared wrongly the first time, and the
            # model dutifully returned exactly what the schema asked for.
            "sort_by": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "column_id": {"type": "string"},
                        "direction": {"type": "string", "enum": ["asc", "desc"]},
                    },
                    "required": ["column_id"],
                },
            },
            "row_limit": {"type": "integer"},
        },
        "required": ["primary_table", "tables", "columns"],
    }


def suggestions_schema() -> dict:
    """Several reports, each with the title and reason a person reads first."""
    return {
        "type": "object",
        "properties": {
            "suggestions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "why": {"type": "string"},
                        "definition": definition_schema(),
                    },
                    "required": ["title", "definition"],
                },
            }
        },
        "required": ["suggestions"],
    }


def answer_schema() -> dict:
    """One report, with what the model had to assume to build it."""
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "why": {"type": "string"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "definition": definition_schema(),
        },
        "required": ["title", "definition"],
    }


SYSTEM_PROMPT = """You design reports for a business intelligence tool.

You are given the shape of a company's database: its tables, their columns and
the relationships between them. You never see the data itself.

You answer only with JSON describing reports, in the format asked for. You do
not write SQL, and there is nowhere to put SQL if you did -- the JSON you return
is compiled by the tool itself.

The JSON field names are fixed and are not negotiable:

  primary_table   one table name, the one the report is rooted at. REQUIRED.
  tables          every table used, including the primary one. REQUIRED.
  columns         a list. Each has id, table, field, and optionally
                  aggregation and display_name. REQUIRED.
  group_by        a list of {table, field}. Every non-aggregated column must
                  appear here when anything is aggregated.
  sort_by         a LIST of {column_id, direction}, each referring to a
                  column by its id.
  row_limit       a number.

Do not invent other names for these. In particular there is no "dimensions",
no "metrics", no "column" and no "alias" -- a grouped attribute and an
aggregated measure are both entries in "columns", told apart by whether they
carry an aggregation. Use "field", not "column". Use "display_name", not
"alias". Omitting primary_table makes the report unusable.

Rules that matter:
- Only use table and column names that appear in the schema you were given.
  Inventing one produces an error the user has to understand, not a report.
- A column marked "measurable" can be summed or averaged. Anything else can only
  be counted, or grouped by.
- A column marked "masked" comes back obscured. Do not build a report whose
  entire point is that column.
- When you group, every non-aggregated column must be in the grouping.
- Prefer few columns that answer one question over many that answer none. A
  report with six well-chosen columns is more useful than one with twenty.
- Titles are what someone reads in a menu six months from now. "Revenue by
  customer, this year" is a title; "Report 1" is not.
"""


SUGGEST_INSTRUCTION = """Suggest reports this business would find useful, based on
what its database actually contains.

Look at what the tables are and how they relate, and propose reports that answer
questions a manager would actually ask of this data. Vary them: some about
volume, some about money, some about time, some about what is going wrong.

Return JSON of this shape:

{
  "suggestions": [
    {
      "title": "Revenue by customer, this year",
      "why": "One sentence on the question this answers and who would ask it.",
      "definition": {
        "primary_table": "orders",
        "tables": ["orders", "customers"],
        "columns": [
          {"id": "c1", "table": "customers", "field": "name"},
          {"id": "c2", "table": "orders", "field": "total", "aggregation": "sum"}
        ],
        "group_by": [{"table": "customers", "field": "name"}],
        "sort_by": [{"column_id": "c2", "direction": "desc"}],
        "row_limit": 50
      }
    }
  ]
}

Every column needs a unique "id". Sorting refers to a column by its id."""


ASK_INSTRUCTION = """Build one report that answers the question below.

Return JSON of this shape:

{
  "title": "A title someone would recognise in a menu",
  "why": "One sentence on how this answers the question.",
  "confidence": "high" | "medium" | "low",
  "assumptions": ["Anything you had to decide that the question did not say"],
  "definition": { ...as described in the schema above... }
}

If the question is ambiguous in a way that changes the answer -- two columns
could plausibly be meant, or "unpaid" could mean two different things -- say so
in "assumptions" and set confidence to "low". Do not guess silently: a report
that quietly answers a different question than the one asked is worse than one
that says what it assumed."""
