"""
A schema fixture shaped like the reference screenshot.

DEVELOPMENT ONLY. This is a stand-in so the engine can be tested before the real
database is connected -- it is NOT a claim about production table names. When
the real schema is introspected it replaces this entirely; nothing in the engine
references these names.
"""

from __future__ import annotations

from app.domain.schema.registry import (
    Cardinality,
    ColumnMeta,
    DataType,
    JoinType,
    RelationshipMeta,
    RelationshipSource,
    SchemaRegistry,
    TableMeta,
)


def column(
    table: str,
    name: str,
    data_type: DataType,
    *,
    pk: bool = False,
    fk: bool = False,
    nullable: bool = True,
    **kwargs,
) -> ColumnMeta:
    physical = {
        DataType.TEXT: "character varying",
        DataType.INTEGER: "integer",
        DataType.DECIMAL: "numeric",
        DataType.DATE: "date",
        DataType.DATETIME: "timestamp without time zone",
        DataType.BOOLEAN: "boolean",
    }.get(data_type, "text")
    return ColumnMeta(
        table=table,
        name=name,
        data_type=data_type,
        physical_type=physical,
        nullable=nullable and not pk,
        is_primary_key=pk,
        is_foreign_key=fk,
        **kwargs,
    )


def build_registry() -> SchemaRegistry:
    customers = TableMeta(
        name="customers",
        category="Customers",
        display_name="Customers",
        estimated_rows=1_240,
        columns=(
            column("customers", "customer_id", DataType.INTEGER, pk=True),
            column("customers", "customer_name", DataType.TEXT, nullable=False),
            column("customers", "email", DataType.TEXT),
            column("customers", "phone", DataType.TEXT),
            column("customers", "city", DataType.TEXT),
            column("customers", "created_at", DataType.DATETIME),
        ),
    )

    sales_orders = TableMeta(
        name="sales_orders",
        category="Sales",
        display_name="Sales Orders",
        estimated_rows=18_500,
        columns=(
            column("sales_orders", "order_id", DataType.INTEGER, pk=True),
            column("sales_orders", "order_no", DataType.TEXT, nullable=False),
            column("sales_orders", "customer_id", DataType.INTEGER, fk=True),
            column("sales_orders", "order_date", DataType.DATE),
            column("sales_orders", "status", DataType.TEXT),
            column("sales_orders", "payment_status", DataType.TEXT),
            column("sales_orders", "total_amount", DataType.DECIMAL),
            column("sales_orders", "currency", DataType.TEXT),
            column("sales_orders", "created_at", DataType.DATETIME),
            column("sales_orders", "updated_at", DataType.DATETIME),
        ),
    )

    sales_order_items = TableMeta(
        name="sales_order_items",
        category="Sales",
        display_name="Sales Order Items",
        estimated_rows=74_000,
        columns=(
            column("sales_order_items", "item_id", DataType.INTEGER, pk=True),
            column("sales_order_items", "order_id", DataType.INTEGER, fk=True),
            column("sales_order_items", "description", DataType.TEXT),
            column("sales_order_items", "quantity", DataType.INTEGER),
            column("sales_order_items", "unit_price", DataType.DECIMAL),
            column("sales_order_items", "line_total", DataType.DECIMAL),
        ),
    )

    artworks = TableMeta(
        name="artworks",
        category="Artwork",
        display_name="Artworks",
        estimated_rows=31_900,
        columns=(
            column("artworks", "artwork_id", DataType.INTEGER, pk=True),
            column("artworks", "order_id", DataType.INTEGER, fk=True),
            column("artworks", "file_name", DataType.TEXT),
            column("artworks", "status", DataType.TEXT),
            column("artworks", "approved_at", DataType.DATETIME),
        ),
    )

    invoices = TableMeta(
        name="invoices",
        category="Sales",
        display_name="Invoices",
        estimated_rows=17_100,
        columns=(
            column("invoices", "invoice_id", DataType.INTEGER, pk=True),
            column("invoices", "invoice_no", DataType.TEXT),
            column("invoices", "order_id", DataType.INTEGER, fk=True),
            column("invoices", "invoice_date", DataType.DATE),
            column("invoices", "total_amount", DataType.DECIMAL),
            column("invoices", "status", DataType.TEXT),
        ),
    )

    payments = TableMeta(
        name="payments",
        category="Payments",
        display_name="Payments",
        estimated_rows=16_400,
        columns=(
            column("payments", "payment_id", DataType.INTEGER, pk=True),
            column("payments", "invoice_id", DataType.INTEGER, fk=True),
            column("payments", "amount", DataType.DECIMAL),
            column("payments", "method", DataType.TEXT),
            column("payments", "paid_at", DataType.DATETIME),
        ),
    )

    shipments = TableMeta(
        name="shipments",
        category="Fulfillment",
        display_name="Shipments",
        estimated_rows=15_800,
        columns=(
            column("shipments", "shipment_id", DataType.INTEGER, pk=True),
            column("shipments", "order_id", DataType.INTEGER, fk=True),
            column("shipments", "tracking_no", DataType.TEXT),
            column("shipments", "shipped_at", DataType.DATETIME),
            column("shipments", "carrier", DataType.TEXT),
        ),
    )

    def relationship(
        rid: str,
        left: str,
        left_col: str,
        right: str,
        right_col: str,
        source: RelationshipSource = RelationshipSource.PHYSICAL,
    ) -> RelationshipMeta:
        return RelationshipMeta(
            id=rid,
            left_table=left,
            left_column=left_col,
            right_table=right,
            right_column=right_col,
            cardinality=Cardinality.ONE_TO_MANY,
            default_join_type=JoinType.LEFT,
            source=source,
        )

    return SchemaRegistry(
        tables=[
            customers, sales_orders, sales_order_items, artworks,
            invoices, payments, shipments,
        ],
        relationships=[
            relationship("r1", "customers", "customer_id", "sales_orders", "customer_id"),
            relationship("r2", "sales_orders", "order_id", "sales_order_items", "order_id"),
            relationship("r3", "sales_orders", "order_id", "artworks", "order_id"),
            relationship("r4", "sales_orders", "order_id", "invoices", "order_id"),
            relationship("r5", "invoices", "invoice_id", "payments", "invoice_id"),
            relationship("r6", "sales_orders", "order_id", "shipments", "order_id"),
        ],
        connection_id="fixture",
    )
