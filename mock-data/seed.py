"""
DEVELOPMENT DATA ONLY -- NOT PRODUCTION.

Builds a realistic operational schema and seeds it, including a manifest of
deliberately planted anomalies so the anomaly engine can be tested against known
answers rather than "looks about right".

This is a stand-in, not a specification. When the real database is connected it
is introspected and this schema is irrelevant -- nothing in the application
references these table names.

Usage
    python mock-data/seed.py --url sqlite:///mock-data/decoinks_demo.db
    python mock-data/seed.py --url postgresql+psycopg://ops_owner:pw@localhost:5434/decoinks_mock
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa

SEED = 20260823
RNG = random.Random(SEED)

metadata = sa.MetaData()


def table(name: str, *columns: sa.Column, comment: str | None = None) -> sa.Table:
    return sa.Table(name, metadata, *columns, comment=comment)


# ---------------------------------------------------------------------------
# Schema -- a DTF transfer printing business, shaped like the spec's flow:
# Lead -> Quotation -> Sales Order -> Items -> Artwork -> Invoice -> Payment
#      -> Production -> Shipment
# ---------------------------------------------------------------------------
users = table(
    "users",
    sa.Column("user_id", sa.Integer, primary_key=True),
    sa.Column("full_name", sa.String(120), nullable=False),
    sa.Column("email", sa.String(160)),
    sa.Column("role", sa.String(40)),
    sa.Column("is_active", sa.Boolean, default=True),
    comment="Internal staff accounts",
)

customers = table(
    "customers",
    sa.Column("customer_id", sa.Integer, primary_key=True),
    sa.Column("customer_name", sa.String(160), nullable=False),
    sa.Column("email", sa.String(160)),
    sa.Column("phone", sa.String(40)),
    sa.Column("company", sa.String(160)),
    sa.Column("city", sa.String(80)),
    sa.Column("country", sa.String(80)),
    sa.Column("credit_limit", sa.Numeric(12, 2)),
    sa.Column("created_at", sa.DateTime),
    sa.Column("updated_at", sa.DateTime),
    comment="Customer master records",
)

contacts = table(
    "contacts",
    sa.Column("contact_id", sa.Integer, primary_key=True),
    sa.Column("customer_id", sa.Integer, sa.ForeignKey("customers.customer_id")),
    sa.Column("contact_name", sa.String(120)),
    sa.Column("email", sa.String(160)),
    sa.Column("phone", sa.String(40)),
    sa.Column("is_primary", sa.Boolean),
)

leads = table(
    "leads",
    sa.Column("lead_id", sa.Integer, primary_key=True),
    sa.Column("company_name", sa.String(160)),
    sa.Column("contact_email", sa.String(160)),
    sa.Column("source", sa.String(60)),
    sa.Column("status", sa.String(40)),
    sa.Column("converted_customer_id", sa.Integer, sa.ForeignKey("customers.customer_id")),
    sa.Column("created_at", sa.DateTime),
)

quotations = table(
    "quotations",
    sa.Column("quotation_id", sa.Integer, primary_key=True),
    sa.Column("quotation_no", sa.String(32), nullable=False),
    sa.Column("customer_id", sa.Integer, sa.ForeignKey("customers.customer_id")),
    sa.Column("quotation_date", sa.Date),
    sa.Column("valid_until", sa.Date),
    sa.Column("total_amount", sa.Numeric(12, 2)),
    sa.Column("status", sa.String(40)),
    sa.Column("created_by", sa.Integer, sa.ForeignKey("users.user_id")),
)

sales_orders = table(
    "sales_orders",
    sa.Column("order_id", sa.Integer, primary_key=True),
    sa.Column("order_no", sa.String(32), nullable=False),
    sa.Column("customer_id", sa.Integer, sa.ForeignKey("customers.customer_id")),
    sa.Column("quotation_id", sa.Integer, sa.ForeignKey("quotations.quotation_id")),
    sa.Column("order_date", sa.Date),
    sa.Column("required_date", sa.Date),
    sa.Column("status", sa.String(40)),
    sa.Column("payment_status", sa.String(40)),
    sa.Column("total_amount", sa.Numeric(12, 2)),
    sa.Column("currency", sa.String(8)),
    sa.Column("created_by", sa.Integer, sa.ForeignKey("users.user_id")),
    sa.Column("created_at", sa.DateTime),
    sa.Column("updated_at", sa.DateTime),
    comment="Confirmed customer orders",
)

sales_order_items = table(
    "sales_order_items",
    sa.Column("item_id", sa.Integer, primary_key=True),
    sa.Column("order_id", sa.Integer, sa.ForeignKey("sales_orders.order_id")),
    sa.Column("description", sa.String(200)),
    sa.Column("quantity", sa.Integer),
    sa.Column("unit_price", sa.Numeric(12, 2)),
    sa.Column("line_total", sa.Numeric(12, 2)),
)

artworks = table(
    "artworks",
    sa.Column("artwork_id", sa.Integer, primary_key=True),
    sa.Column("order_id", sa.Integer, sa.ForeignKey("sales_orders.order_id")),
    sa.Column("file_name", sa.String(200)),
    sa.Column("width_cm", sa.Numeric(8, 2)),
    sa.Column("height_cm", sa.Numeric(8, 2)),
    sa.Column("status", sa.String(40)),
    sa.Column("approved_at", sa.DateTime),
)

gang_sheets = table(
    "gang_sheets",
    sa.Column("gang_sheet_id", sa.Integer, primary_key=True),
    sa.Column("order_id", sa.Integer, sa.ForeignKey("sales_orders.order_id")),
    sa.Column("sheet_no", sa.String(32)),
    sa.Column("total_area_cm2", sa.Numeric(12, 2)),
    sa.Column("printed_at", sa.DateTime),
)

invoices = table(
    "invoices",
    sa.Column("invoice_id", sa.Integer, primary_key=True),
    sa.Column("invoice_no", sa.String(32), nullable=False),
    sa.Column("order_id", sa.Integer, sa.ForeignKey("sales_orders.order_id")),
    sa.Column("customer_id", sa.Integer, sa.ForeignKey("customers.customer_id")),
    sa.Column("invoice_date", sa.Date),
    sa.Column("due_date", sa.Date),
    sa.Column("total_amount", sa.Numeric(12, 2)),
    sa.Column("tax_amount", sa.Numeric(12, 2)),
    sa.Column("status", sa.String(40)),
)

invoice_items = table(
    "invoice_items",
    sa.Column("invoice_item_id", sa.Integer, primary_key=True),
    sa.Column("invoice_id", sa.Integer, sa.ForeignKey("invoices.invoice_id")),
    sa.Column("description", sa.String(200)),
    sa.Column("quantity", sa.Integer),
    sa.Column("unit_price", sa.Numeric(12, 2)),
    sa.Column("line_total", sa.Numeric(12, 2)),
)

payments = table(
    "payments",
    sa.Column("payment_id", sa.Integer, primary_key=True),
    sa.Column("payment_no", sa.String(32)),
    sa.Column("invoice_id", sa.Integer, sa.ForeignKey("invoices.invoice_id")),
    sa.Column("customer_id", sa.Integer, sa.ForeignKey("customers.customer_id")),
    sa.Column("amount", sa.Numeric(12, 2)),
    sa.Column("method", sa.String(40)),
    sa.Column("paid_at", sa.DateTime),
    sa.Column("reference", sa.String(60)),
)

production_jobs = table(
    "production_jobs",
    sa.Column("job_id", sa.Integer, primary_key=True),
    sa.Column("order_id", sa.Integer, sa.ForeignKey("sales_orders.order_id")),
    sa.Column("stage", sa.String(40)),
    sa.Column("started_at", sa.DateTime),
    sa.Column("completed_at", sa.DateTime),
    sa.Column("operator_id", sa.Integer, sa.ForeignKey("users.user_id")),
)

shipments = table(
    "shipments",
    sa.Column("shipment_id", sa.Integer, primary_key=True),
    sa.Column("order_id", sa.Integer, sa.ForeignKey("sales_orders.order_id")),
    sa.Column("carrier", sa.String(60)),
    sa.Column("tracking_no", sa.String(60)),
    sa.Column("shipped_at", sa.DateTime),
    sa.Column("delivered_at", sa.DateTime),
    sa.Column("status", sa.String(40)),
)

shipment_tracking = table(
    "shipment_tracking",
    sa.Column("tracking_id", sa.Integer, primary_key=True),
    sa.Column("shipment_id", sa.Integer, sa.ForeignKey("shipments.shipment_id")),
    sa.Column("event", sa.String(80)),
    sa.Column("location", sa.String(80)),
    sa.Column("event_at", sa.DateTime),
)

suppliers = table(
    "suppliers",
    sa.Column("supplier_id", sa.Integer, primary_key=True),
    sa.Column("supplier_name", sa.String(160)),
    sa.Column("email", sa.String(160)),
    sa.Column("phone", sa.String(40)),
    sa.Column("country", sa.String(80)),
)

purchase_orders = table(
    "purchase_orders",
    sa.Column("po_id", sa.Integer, primary_key=True),
    sa.Column("po_no", sa.String(32)),
    sa.Column("supplier_id", sa.Integer, sa.ForeignKey("suppliers.supplier_id")),
    sa.Column("po_date", sa.Date),
    sa.Column("total_amount", sa.Numeric(12, 2)),
    sa.Column("status", sa.String(40)),
)

order_status_history = table(
    "order_status_history",
    sa.Column("history_id", sa.Integer, primary_key=True),
    sa.Column("order_id", sa.Integer, sa.ForeignKey("sales_orders.order_id")),
    sa.Column("from_status", sa.String(40)),
    sa.Column("to_status", sa.String(40)),
    sa.Column("changed_at", sa.DateTime),
    sa.Column("changed_by", sa.Integer, sa.ForeignKey("users.user_id")),
)


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
FIRST = ["Hector", "Jessica", "Gaspar", "Tanner", "McKenzie", "Priya", "Omar", "Lena",
         "Diego", "Aisha", "Nils", "Rosa", "Kwame", "Yuki", "Marta", "Ivan", "Chloe",
         "Samir", "Elena", "Tomas"]
LAST = ["Garcia", "Pacheco", "Erosa", "Trujillo", "Caldwell", "Nair", "Haddad", "Vogel",
        "Marino", "Okafor", "Berg", "Silva", "Mensah", "Tanaka", "Kowalski", "Petrov",
        "Dubois", "Rahman", "Costa", "Novak"]
COMPANIES = ["Apex Apparel", "BrightPrint Co", "Custom Threads", "DTF Direct", "Evergreen Tees",
             "FlexWear", "Gildan Resellers", "Heat Press Pros", "InkLab", "Jetset Merch",
             "Kinetic Sports", "Loft Uniforms", "Motif Studio", "NorthStar Prints"]
CITIES = [("Madrid", "Spain"), ("Lisbon", "Portugal"), ("Berlin", "Germany"),
          ("Dublin", "Ireland"), ("Milan", "Italy"), ("Lyon", "France"),
          ("Rotterdam", "Netherlands"), ("Krakow", "Poland")]
ORDER_STATUS = ["Draft", "Confirmed", "In Production", "Ready", "Shipped", "Completed",
                "Cancelled"]
PAYMENT_STATUS = ["Unpaid", "Partially Paid", "Paid"]
CARRIERS = ["DHL", "UPS", "GLS", "FedEx", "Correos"]
ITEM_DESCRIPTIONS = ["DTF Transfer A3", "DTF Transfer A4", "Gang Sheet 60x100cm",
                     "UV DTF Sticker Sheet", "Heat Transfer Vinyl Roll",
                     "Custom Logo Transfer", "Full Colour Print 30x40cm"]

TODAY = date(2026, 8, 23)


def money(value: float) -> Decimal:
    return Decimal(f"{value:.2f}")


def build_rows() -> tuple[dict[str, list[dict]], list[dict]]:
    """Return (table_name -> rows, anomaly_manifest)."""
    data: dict[str, list[dict]] = {}
    manifest: list[dict] = []

    def plant(kind: str, entity: str, key, detail: str, severity: str = "high") -> None:
        manifest.append({"rule": kind, "entity": entity, "key": key,
                         "detail": detail, "expected_severity": severity})

    # -- users ---------------------------------------------------------
    data["users"] = [
        {"user_id": i, "full_name": f"{RNG.choice(FIRST)} {RNG.choice(LAST)}",
         "email": f"user{i}@decoinks.example", "role": role, "is_active": True}
        for i, role in enumerate(
            ["Admin", "Sales", "Sales", "Production", "Production", "Finance", "Logistics"], 1
        )
    ]

    # -- customers -----------------------------------------------------
    customer_rows = []
    for i in range(1, 121):
        first, last = RNG.choice(FIRST), RNG.choice(LAST)
        city, country = RNG.choice(CITIES)
        created = datetime(2024, 1, 1) + timedelta(days=RNG.randint(0, 600))
        customer_rows.append({
            "customer_id": i,
            "customer_name": f"{first} {last}",
            "email": f"{first.lower()}.{last.lower()}@{RNG.choice(COMPANIES).split()[0].lower()}.example",
            "phone": f"+34 6{RNG.randint(10, 99)} {RNG.randint(100, 999)} {RNG.randint(100, 999)}",
            "company": RNG.choice(COMPANIES),
            "city": city, "country": country,
            "credit_limit": money(RNG.choice([1000, 2500, 5000, 10000])),
            "created_at": created, "updated_at": created + timedelta(days=RNG.randint(0, 90)),
        })

    # ANOMALY: duplicate customer (same person, same email, different record).
    original = customer_rows[6]
    customer_rows.append({
        **original,
        "customer_id": 121,
        "customer_name": original["customer_name"].upper(),
        "phone": original["phone"].replace(" ", ""),
        "created_at": original["created_at"] + timedelta(days=45),
        "updated_at": original["created_at"] + timedelta(days=45),
    })
    plant("duplicate_customer", "customers", [original["customer_id"], 121],
          f"Same email and phone as customer {original['customer_id']}", "medium")

    # ANOMALY: customer with no contact details at all.
    customer_rows.append({
        "customer_id": 122, "customer_name": "Walk-in Customer", "email": None, "phone": None,
        "company": None, "city": None, "country": None, "credit_limit": None,
        "created_at": datetime(2026, 3, 4), "updated_at": datetime(2026, 3, 4),
    })
    plant("missing_critical_data", "customers", 122,
          "Customer has neither email nor phone", "medium")
    data["customers"] = customer_rows

    # -- contacts / leads ---------------------------------------------
    data["contacts"] = [
        {"contact_id": i, "customer_id": RNG.randint(1, 120),
         "contact_name": f"{RNG.choice(FIRST)} {RNG.choice(LAST)}",
         "email": f"contact{i}@example.com",
         "phone": f"+34 9{RNG.randint(10, 99)} {RNG.randint(100000, 999999)}",
         "is_primary": i % 4 == 0}
        for i in range(1, 141)
    ]
    data["leads"] = [
        {"lead_id": i, "company_name": RNG.choice(COMPANIES),
         "contact_email": f"lead{i}@example.com",
         "source": RNG.choice(["Website", "Referral", "Trade Show", "Cold Call"]),
         "status": RNG.choice(["New", "Contacted", "Qualified", "Converted", "Lost"]),
         "converted_customer_id": RNG.randint(1, 120) if i % 3 == 0 else None,
         "created_at": datetime(2025, 6, 1) + timedelta(days=RNG.randint(0, 400))}
        for i in range(1, 91)
    ]

    # -- quotations ----------------------------------------------------
    quotation_rows = []
    for i in range(1, 181):
        quotation_date = TODAY - timedelta(days=RNG.randint(30, 500))
        quotation_rows.append({
            "quotation_id": i, "quotation_no": f"QT-{10000 + i}",
            "customer_id": RNG.randint(1, 120),
            "quotation_date": quotation_date,
            "valid_until": quotation_date + timedelta(days=30),
            "total_amount": money(RNG.uniform(150, 3200)),
            "status": RNG.choice(["Draft", "Sent", "Accepted", "Rejected", "Expired"]),
            "created_by": RNG.randint(1, 7),
        })
    data["quotations"] = quotation_rows

    # -- sales orders and everything hanging off them ------------------
    order_rows, item_rows, artwork_rows, gang_rows = [], [], [], []
    invoice_rows, invoice_item_rows, payment_rows = [], [], []
    shipment_rows, tracking_rows, production_rows, history_rows = [], [], [], []

    item_id = artwork_id = gang_id = invoice_id = invoice_item_id = 1
    payment_id = shipment_id = tracking_id = production_id = history_id = 1

    for order_id in range(1, 601):
        order_no = f"SO-{10000 + order_id}"
        customer_id = RNG.randint(1, 120)
        order_date = TODAY - timedelta(days=RNG.randint(0, 420))
        status = RNG.choices(
            ORDER_STATUS, weights=[3, 12, 14, 8, 18, 42, 3], k=1
        )[0]
        created = datetime.combine(order_date, datetime.min.time()) + timedelta(
            hours=RNG.randint(8, 18)
        )

        line_count = RNG.randint(1, 5)
        order_total = Decimal("0.00")
        for _ in range(line_count):
            quantity = RNG.randint(5, 150)
            unit_price = money(RNG.uniform(1.2, 14.5))
            line_total = money(float(unit_price) * quantity)
            item_rows.append({
                "item_id": item_id, "order_id": order_id,
                "description": RNG.choice(ITEM_DESCRIPTIONS),
                "quantity": quantity, "unit_price": unit_price, "line_total": line_total,
            })
            order_total += line_total
            item_id += 1

        payment_status = (
            "Paid" if status in ("Shipped", "Completed")
            else RNG.choice(PAYMENT_STATUS)
        )

        order_rows.append({
            "order_id": order_id, "order_no": order_no, "customer_id": customer_id,
            "quotation_id": RNG.randint(1, 180) if RNG.random() < 0.55 else None,
            "order_date": order_date,
            "required_date": order_date + timedelta(days=RNG.randint(3, 21)),
            "status": status, "payment_status": payment_status,
            "total_amount": order_total, "currency": "EUR",
            "created_by": RNG.randint(1, 7),
            "created_at": created,
            "updated_at": created + timedelta(days=RNG.randint(0, 20)),
        })

        history_rows.append({
            "history_id": history_id, "order_id": order_id, "from_status": "Draft",
            "to_status": status, "changed_at": created + timedelta(hours=2),
            "changed_by": RNG.randint(1, 7),
        })
        history_id += 1

        # Artwork: most orders have it.
        if RNG.random() < 0.88:
            for _ in range(RNG.randint(1, 4)):
                artwork_rows.append({
                    "artwork_id": artwork_id, "order_id": order_id,
                    "file_name": f"art_{order_id}_{artwork_id}.png",
                    "width_cm": money(RNG.uniform(10, 60)),
                    "height_cm": money(RNG.uniform(10, 90)),
                    "status": RNG.choice(["Pending", "Approved", "Approved", "Rework"]),
                    "approved_at": created + timedelta(days=RNG.randint(0, 5)),
                })
                artwork_id += 1
            gang_rows.append({
                "gang_sheet_id": gang_id, "order_id": order_id,
                "sheet_no": f"GS-{5000 + gang_id}",
                "total_area_cm2": money(RNG.uniform(1200, 9000)),
                "printed_at": created + timedelta(days=RNG.randint(1, 8)),
            })
            gang_id += 1

        # Production
        if status in ("In Production", "Ready", "Shipped", "Completed"):
            started = created + timedelta(days=RNG.randint(1, 4))
            completed = (
                started + timedelta(days=RNG.randint(1, 6))
                if status != "In Production" else None
            )
            production_rows.append({
                "job_id": production_id, "order_id": order_id,
                "stage": "Printing" if status == "In Production" else "Finished",
                "started_at": started, "completed_at": completed,
                "operator_id": RNG.randint(1, 7),
            })
            production_id += 1

        # Invoice
        if status not in ("Draft", "Cancelled") and RNG.random() < 0.94:
            invoice_date = order_date + timedelta(days=RNG.randint(0, 6))
            invoice_total = order_total
            invoice_rows.append({
                "invoice_id": invoice_id, "invoice_no": f"INV-{20000 + invoice_id}",
                "order_id": order_id, "customer_id": customer_id,
                "invoice_date": invoice_date,
                "due_date": invoice_date + timedelta(days=30),
                "total_amount": invoice_total,
                "tax_amount": money(float(invoice_total) * 0.21),
                "status": "Cancelled" if status == "Cancelled" else "Issued",
            })
            invoice_item_rows.append({
                "invoice_item_id": invoice_item_id, "invoice_id": invoice_id,
                "description": "Order total", "quantity": 1,
                "unit_price": invoice_total, "line_total": invoice_total,
            })
            invoice_item_id += 1

            if payment_status == "Paid":
                payment_rows.append({
                    "payment_id": payment_id, "payment_no": f"PAY-{30000 + payment_id}",
                    "invoice_id": invoice_id, "customer_id": customer_id,
                    "amount": invoice_total,
                    "method": RNG.choice(["Card", "Transfer", "PayPal", "Cash"]),
                    "paid_at": datetime.combine(invoice_date, datetime.min.time())
                    + timedelta(days=RNG.randint(1, 25)),
                    "reference": f"REF{RNG.randint(100000, 999999)}",
                })
                payment_id += 1
            elif payment_status == "Partially Paid":
                payment_rows.append({
                    "payment_id": payment_id, "payment_no": f"PAY-{30000 + payment_id}",
                    "invoice_id": invoice_id, "customer_id": customer_id,
                    "amount": money(float(invoice_total) * RNG.uniform(0.2, 0.7)),
                    "method": RNG.choice(["Card", "Transfer"]),
                    "paid_at": datetime.combine(invoice_date, datetime.min.time())
                    + timedelta(days=RNG.randint(1, 25)),
                    "reference": f"REF{RNG.randint(100000, 999999)}",
                })
                payment_id += 1
            invoice_id += 1

        # Shipment
        if status in ("Shipped", "Completed"):
            shipped = created + timedelta(days=RNG.randint(4, 15))
            shipment_rows.append({
                "shipment_id": shipment_id, "order_id": order_id,
                "carrier": RNG.choice(CARRIERS),
                "tracking_no": f"TRK{RNG.randint(10**9, 10**10 - 1)}",
                "shipped_at": shipped,
                "delivered_at": shipped + timedelta(days=RNG.randint(1, 6))
                if status == "Completed" else None,
                "status": "Delivered" if status == "Completed" else "In Transit",
            })
            tracking_rows.append({
                "tracking_id": tracking_id, "shipment_id": shipment_id,
                "event": "Picked up", "location": RNG.choice(CITIES)[0],
                "event_at": shipped,
            })
            tracking_id += 1
            shipment_id += 1

    # ------------------------------------------------------------------
    # Planted anomalies. Each is a real, recognisable business failure.
    # ------------------------------------------------------------------
    by_order = {row["order_id"]: row for row in order_rows}
    invoices_by_order = {row["order_id"]: row for row in invoice_rows}

    # 1. Invoice total does not match its sales order (over by 150.00).
    target = next(o for o in (12, 13, 14) if o in invoices_by_order)
    invoice = invoices_by_order[target]
    invoice["total_amount"] = money(float(invoice["total_amount"]) + 150)
    plant("invoice_order_mismatch", "invoices", invoice["invoice_no"],
          f"Invoice is 150.00 higher than order {by_order[target]['order_no']}", "high")

    # 2. Order marked Paid with no payment transaction at all.
    paid_without_payment = None
    for row in order_rows:
        if row["payment_status"] == "Paid" and row["order_id"] in invoices_by_order:
            candidate_invoice = invoices_by_order[row["order_id"]]["invoice_id"]
            if any(p["invoice_id"] == candidate_invoice for p in payment_rows):
                payment_rows[:] = [p for p in payment_rows if p["invoice_id"] != candidate_invoice]
                paid_without_payment = row
                break
    if paid_without_payment:
        plant("paid_without_payment", "sales_orders", paid_without_payment["order_no"],
              "payment_status is Paid but no payment record exists", "critical")

    # 3. Overpayment: payments exceed the invoice total.
    overpaid = payment_rows[5]
    overpaid["amount"] = money(float(overpaid["amount"]) * 1.6)
    plant("overpayment", "payments", overpaid["payment_no"],
          "Payments received exceed the invoice total", "high")

    # 4. Orphan foreign key: invoice pointing at an order that does not exist.
    invoice_rows.append({
        "invoice_id": invoice_id, "invoice_no": f"INV-{20000 + invoice_id}",
        "order_id": 999_999, "customer_id": 5,
        "invoice_date": TODAY - timedelta(days=14),
        "due_date": TODAY + timedelta(days=16),
        "total_amount": money(880.00), "tax_amount": money(184.80), "status": "Issued",
    })
    plant("invoice_without_order", "invoices", f"INV-{20000 + invoice_id}",
          "Invoice references order_id 999999, which does not exist", "critical")
    invoice_id += 1

    # 5. Sales order with no items.
    empty_order_id = 601
    order_rows.append({
        "order_id": empty_order_id, "order_no": f"SO-{10000 + empty_order_id}",
        "customer_id": 22, "quotation_id": None,
        "order_date": TODAY - timedelta(days=9),
        "required_date": TODAY + timedelta(days=5),
        "status": "Confirmed", "payment_status": "Unpaid",
        "total_amount": money(0), "currency": "EUR", "created_by": 2,
        "created_at": datetime.combine(TODAY - timedelta(days=9), datetime.min.time()),
        "updated_at": datetime.combine(TODAY - timedelta(days=9), datetime.min.time()),
    })
    plant("order_without_items", "sales_orders", f"SO-{10000 + empty_order_id}",
          "Sales order has zero line items and a zero total", "high")

    # 6. Stale production: still In Production well past the threshold.
    stale_order_id = 602
    stale_created = datetime.combine(TODAY - timedelta(days=26), datetime.min.time())
    order_rows.append({
        "order_id": stale_order_id, "order_no": f"SO-{10000 + stale_order_id}",
        "customer_id": 31, "quotation_id": None,
        "order_date": TODAY - timedelta(days=26),
        "required_date": TODAY - timedelta(days=12),
        "status": "In Production", "payment_status": "Paid",
        "total_amount": money(1450.00), "currency": "EUR", "created_by": 3,
        "created_at": stale_created, "updated_at": stale_created,
    })
    item_rows.append({
        "item_id": item_id, "order_id": stale_order_id, "description": "DTF Transfer A3",
        "quantity": 200, "unit_price": money(7.25), "line_total": money(1450.00),
    })
    item_id += 1
    production_rows.append({
        "job_id": production_id, "order_id": stale_order_id, "stage": "Printing",
        "started_at": stale_created + timedelta(days=1), "completed_at": None,
        "operator_id": 4,
    })
    production_id += 1
    plant("stale_order", "sales_orders", f"SO-{10000 + stale_order_id}",
          "In Production for 26 days, far beyond the 7-day threshold", "high")

    # 7. Status contradiction: cancelled order that shipped anyway.
    contradiction_id = 603
    contradiction_created = datetime.combine(TODAY - timedelta(days=20), datetime.min.time())
    order_rows.append({
        "order_id": contradiction_id, "order_no": f"SO-{10000 + contradiction_id}",
        "customer_id": 44, "quotation_id": None,
        "order_date": TODAY - timedelta(days=20),
        "required_date": TODAY - timedelta(days=6),
        "status": "Cancelled", "payment_status": "Unpaid",
        "total_amount": money(620.00), "currency": "EUR", "created_by": 2,
        "created_at": contradiction_created, "updated_at": contradiction_created,
    })
    item_rows.append({
        "item_id": item_id, "order_id": contradiction_id, "description": "Gang Sheet 60x100cm",
        "quantity": 40, "unit_price": money(15.50), "line_total": money(620.00),
    })
    item_id += 1
    shipment_rows.append({
        "shipment_id": shipment_id, "order_id": contradiction_id, "carrier": "DHL",
        "tracking_no": "TRK9999999999",
        "shipped_at": contradiction_created + timedelta(days=3),
        "delivered_at": contradiction_created + timedelta(days=6), "status": "Delivered",
    })
    shipment_id += 1
    plant("status_contradiction", "sales_orders", f"SO-{10000 + contradiction_id}",
          "Order is Cancelled but a shipment was delivered", "critical")

    # 8. Statistical outlier: a small customer with one enormous order.
    outlier_customer = 77
    outlier_id = 604
    outlier_created = datetime.combine(TODAY - timedelta(days=5), datetime.min.time())
    for i in range(1, 7):
        order_rows.append({
            "order_id": 604 + i * 100, "order_no": f"SO-{10000 + 604 + i * 100}",
            "customer_id": outlier_customer, "quotation_id": None,
            "order_date": TODAY - timedelta(days=30 * i),
            "required_date": TODAY - timedelta(days=30 * i - 7),
            "status": "Completed", "payment_status": "Paid",
            "total_amount": money(RNG.uniform(200, 800)), "currency": "EUR",
            "created_by": 2,
            "created_at": outlier_created - timedelta(days=30 * i),
            "updated_at": outlier_created - timedelta(days=30 * i),
        })
    order_rows.append({
        "order_id": outlier_id, "order_no": f"SO-{10000 + outlier_id}",
        "customer_id": outlier_customer, "quotation_id": None,
        "order_date": TODAY - timedelta(days=5),
        "required_date": TODAY + timedelta(days=9),
        "status": "Confirmed", "payment_status": "Unpaid",
        "total_amount": money(12000.00), "currency": "EUR", "created_by": 2,
        "created_at": outlier_created, "updated_at": outlier_created,
    })
    item_rows.append({
        "item_id": item_id, "order_id": outlier_id, "description": "DTF Transfer A3",
        "quantity": 1600, "unit_price": money(7.50), "line_total": money(12000.00),
    })
    item_id += 1
    plant("amount_outlier", "sales_orders", f"SO-{10000 + outlier_id}",
          f"Customer {outlier_customer} normally orders 200-800; this order is 12,000",
          "medium")

    # 9. Invalid dates: invoice dated before its order.
    backdated = invoice_rows[30]
    backdated_order = by_order[backdated["order_id"]]
    backdated["invoice_date"] = backdated_order["order_date"] - timedelta(days=11)
    plant("invalid_date_order", "invoices", backdated["invoice_no"],
          "Invoice date precedes the order date", "medium")

    # 10. Negative order total.
    negative = order_rows[88]
    negative["total_amount"] = money(-340.00)
    plant("negative_amount", "sales_orders", negative["order_no"],
          "Order total is negative", "high")

    # 11. Shipment with no tracking number.
    untracked = shipment_rows[3]
    untracked["tracking_no"] = None
    plant("missing_tracking", "shipments", untracked["shipment_id"],
          "Shipment has no tracking number", "low")

    # 12. Duplicate invoice number.
    duplicate_source = invoice_rows[10]
    invoice_rows.append({
        **duplicate_source,
        "invoice_id": invoice_id,
        "invoice_date": duplicate_source["invoice_date"] + timedelta(days=2),
    })
    plant("duplicate_invoice_no", "invoices", duplicate_source["invoice_no"],
          "Same invoice number used on two records", "critical")
    invoice_id += 1

    # -- suppliers and purchasing -------------------------------------
    data["suppliers"] = [
        {"supplier_id": i, "supplier_name": f"{RNG.choice(COMPANIES)} Supply",
         "email": f"supplier{i}@example.com",
         "phone": f"+49 {RNG.randint(100, 999)} {RNG.randint(100000, 999999)}",
         "country": RNG.choice(CITIES)[1]}
        for i in range(1, 19)
    ]
    data["purchase_orders"] = [
        {"po_id": i, "po_no": f"PO-{40000 + i}", "supplier_id": RNG.randint(1, 18),
         "po_date": TODAY - timedelta(days=RNG.randint(1, 400)),
         "total_amount": money(RNG.uniform(400, 9000)),
         "status": RNG.choice(["Draft", "Sent", "Received", "Cancelled"])}
        for i in range(1, 121)
    ]

    data["sales_orders"] = order_rows
    data["sales_order_items"] = item_rows
    data["artworks"] = artwork_rows
    data["gang_sheets"] = gang_rows
    data["invoices"] = invoice_rows
    data["invoice_items"] = invoice_item_rows
    data["payments"] = payment_rows
    data["production_jobs"] = production_rows
    data["shipments"] = shipment_rows
    data["shipment_tracking"] = tracking_rows
    data["order_status_history"] = history_rows

    return data, manifest


INSERT_ORDER = [
    "users", "customers", "contacts", "leads", "suppliers", "purchase_orders",
    "quotations", "sales_orders", "sales_order_items", "artworks", "gang_sheets",
    "invoices", "invoice_items", "payments", "production_jobs", "shipments",
    "shipment_tracking", "order_status_history",
]


def _suspend_foreign_keys(connection, dialect: str) -> None:
    if dialect == "sqlite":
        connection.execute(sa.text("PRAGMA foreign_keys = OFF"))
    elif dialect == "postgresql":
        # Disables trigger-based constraint checks for this session only.
        connection.execute(sa.text("SET session_replication_role = replica"))
    elif dialect == "mysql":
        connection.execute(sa.text("SET FOREIGN_KEY_CHECKS = 0"))


def _restore_foreign_keys(connection, dialect: str) -> None:
    if dialect == "postgresql":
        connection.execute(sa.text("SET session_replication_role = origin"))
    elif dialect == "mysql":
        connection.execute(sa.text("SET FOREIGN_KEY_CHECKS = 1"))


def seed(url: str, manifest_path: Path | None = None) -> None:
    engine = sa.create_engine(url)
    is_sqlite = engine.dialect.name == "sqlite"

    with engine.begin() as connection:
        if is_sqlite:
            connection.execute(sa.text("PRAGMA foreign_keys = OFF"))
        metadata.drop_all(connection)
        metadata.create_all(connection)

    data, manifest = build_rows()

    with engine.begin() as connection:
        # One of the planted anomalies is an orphan foreign key -- an invoice
        # pointing at an order that does not exist. A real database rejects that
        # on insert, which is precisely why the anomaly is worth detecting, so
        # constraint enforcement is suspended for the load and restored after.
        _suspend_foreign_keys(connection, engine.dialect.name)
        try:
            for name in INSERT_ORDER:
                rows = data.get(name) or []
                if rows:
                    connection.execute(metadata.tables[name].insert(), rows)
        finally:
            _restore_foreign_keys(connection, engine.dialect.name)

    total = sum(len(rows) for rows in data.values())
    print(f"Seeded {total:,} rows across {len(INSERT_ORDER)} tables into {engine.url.render_as_string()}")
    print(f"Planted {len(manifest)} anomalies for testing:")
    for entry in manifest:
        print(f"  [{entry['expected_severity']:8}] {entry['rule']:26} {entry['detail']}")

    if manifest_path:
        manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        print(f"\nManifest written to {manifest_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the development database.")
    parser.add_argument(
        "--url",
        default="sqlite:///mock-data/decoinks_demo.db",
        help="SQLAlchemy URL of the database to seed (DEVELOPMENT ONLY)",
    )
    parser.add_argument("--manifest", default="mock-data/anomaly_manifest.json")
    args = parser.parse_args()

    if "prod" in args.url.lower():
        return print("Refusing to seed a URL containing 'prod'.") or 2

    Path(args.url.split("///")[-1]).parent.mkdir(parents=True, exist_ok=True) if args.url.startswith(
        "sqlite"
    ) else None
    seed(args.url, Path(args.manifest) if args.manifest else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
