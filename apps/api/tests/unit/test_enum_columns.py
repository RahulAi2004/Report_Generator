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


def test_uuid_in_a_spreadsheet_is_recognised():
    """
    A CSV can only present a uuid as text. Leaving it as text means a file of
    order ids can never link to the orders it names -- which is most of the
    point of uploading one.
    """
    from app.domain.uploads.parser import infer_type
    from app.domain.schema.registry import DataType

    ids = [
        "0cde7c47-a460-4e9d-bf54-6120b514a6e5",
        "EFDFFBA4-5E8A-4CC1-9AAF-581DDA60F2E8",
    ]
    assert infer_type(ids) is DataType.UUID
    # One value that is not a uuid makes the whole column text.
    assert infer_type([*ids, "not-an-id"]) is DataType.TEXT


def test_relationship_inference_accepts_compatible_types():
    from app.domain.schema.registry import DataType
    from app.services.schema_service import _compatible

    assert _compatible(DataType.UUID, DataType.TEXT)
    assert _compatible(DataType.INTEGER, DataType.DECIMAL)
    assert not _compatible(DataType.TEXT, DataType.DATE)
    assert not _compatible(DataType.INTEGER, DataType.TEXT)


def test_join_between_differing_types_is_cast():
    """
    PostgreSQL has no `uuid = text` operator, so joining an uploaded file of
    order ids to the orders themselves fails unless both sides are cast.
    """
    from app.domain.report.engine import EngineOptions, ReportEngine
    from app.domain.report.ir import ReportColumn, ReportDefinition
    from app.domain.schema.registry import (
        ColumnMeta, DataType, RelationshipMeta, SchemaRegistry, TableMeta,
    )

    orders = TableMeta(
        name="orders",
        columns=(
            ColumnMeta(table="orders", name="id", data_type=DataType.UUID,
                       physical_type="uuid", is_primary_key=True),
            ColumnMeta(table="orders", name="total", data_type=DataType.DECIMAL,
                       physical_type="numeric"),
        ),
    )
    sheet = TableMeta(
        name="upload_x", schema="uploads", kind="upload",
        columns=(
            ColumnMeta(table="upload_x", name="order_id", data_type=DataType.UUID,
                       physical_type="uuid"),
            ColumnMeta(table="upload_x", name="commission", data_type=DataType.DECIMAL,
                       physical_type="numeric"),
        ),
    )
    # The upload stores uuids as text, which is the mismatch that matters.
    sheet_as_text = TableMeta(
        **{**{f: getattr(sheet, f) for f in sheet.__slots__},
           "columns": (
               ColumnMeta(table="upload_x", name="order_id", data_type=DataType.TEXT,
                          physical_type="text"),
               sheet.columns[1],
           )},
    )
    registry = SchemaRegistry(
        [orders, sheet_as_text],
        [RelationshipMeta(id="r1", left_table="orders", left_column="id",
                          right_table="upload_x", right_column="order_id")],
    )
    engine = ReportEngine(registry, EngineOptions())
    definition = ReportDefinition(
        primary_table="orders",
        tables=["orders", "upload_x"],
        columns=[
            ReportColumn(id="c1", table="orders", field="total"),
            ReportColumn(id="c2", table="upload_x", field="commission"),
        ],
    )
    result = engine.build(definition)
    assert result.ok, [d.message for d in result.diagnostics]
    sql = engine.render_sql(result.compiled, with_values=True).upper()
    assert "CAST" in sql, f"join keys of differing types were not cast: {sql}"
