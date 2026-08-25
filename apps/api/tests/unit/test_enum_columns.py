"""Enum column handling. Inspects generated SQL, so no database is needed."""

from __future__ import annotations


def test_enum_columns_can_be_filtered():
    """
    A database enum reflects as VARCHAR, but PostgreSQL has no
    `enum = varchar` operator and no pattern-matching operator for enums at
    all. Every filter on a status column failed against the real database while
    passing every test that did not use one.
    """
    import sqlalchemy as sa

    from app.domain.report.engine import EngineOptions, ReportEngine
    from app.domain.report.ir import (
        FilterCondition,
        FilterGroup,
        ReportColumn,
        ReportDefinition,
    )
    from app.domain.schema.registry import ColumnMeta, DataType, SchemaRegistry, TableMeta

    table = TableMeta(
        name="orders",
        columns=(
            ColumnMeta(table="orders", name="id", data_type=DataType.INTEGER,
                       physical_type="integer", is_primary_key=True),
            ColumnMeta(table="orders", name="status", data_type=DataType.TEXT,
                       physical_type="VARCHAR(13)", is_enum=True),
        ),
    )
    engine = ReportEngine(SchemaRegistry([table]), EngineOptions())

    for operator, values in (
        ("equals", ["Delivered"]),
        ("in", ["Delivered", "Shipped"]),
        ("contains", ["Deliv"]),
    ):
        definition = ReportDefinition(
            primary_table="orders",
            tables=["orders"],
            columns=[ReportColumn(id="c1", table="orders", field="id")],
            filters=FilterGroup(children=[
                FilterCondition(table="orders", field="status",
                                operator=operator, values=values),
            ]),
        )
        result = engine.build(definition)
        assert result.ok, [d.message for d in result.diagnostics]
        sql = engine.render_sql(result.compiled, with_values=True).upper()
        assert "CAST(" in sql and "AS TEXT" in sql, f"{operator} did not cast the enum: {sql}"


def test_non_enum_text_columns_are_not_cast():
    """Casting every text column would cost index usage for no benefit."""
    from app.domain.report.engine import EngineOptions, ReportEngine
    from app.domain.report.ir import (
        FilterCondition,
        FilterGroup,
        ReportColumn,
        ReportDefinition,
    )
    from app.domain.schema.registry import ColumnMeta, DataType, SchemaRegistry, TableMeta

    table = TableMeta(
        name="customers",
        columns=(
            ColumnMeta(table="customers", name="name", data_type=DataType.TEXT,
                       physical_type="character varying"),
        ),
    )
    engine = ReportEngine(SchemaRegistry([table]), EngineOptions())
    definition = ReportDefinition(
        primary_table="customers",
        tables=["customers"],
        columns=[ReportColumn(id="c1", table="customers", field="name")],
        filters=FilterGroup(children=[
            FilterCondition(table="customers", field="name",
                            operator="equals", values=["Acme"]),
        ]),
    )
    result = engine.build(definition)
    assert result.ok
    assert "CAST" not in engine.render_sql(result.compiled, with_values=True).upper()
