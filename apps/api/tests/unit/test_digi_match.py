"""
Matching DIGI orders to Decoinks sales orders and purchase orders.

BlankTex stores no link from a DIGI order back to what it was for, so the link is
inferred. These tests pin each rule to the real case it was written for, so a
change that makes one rule looser shows up as the specific mistake it would make.
"""

from __future__ import annotations

from datetime import datetime

from app.services.connectors import digi_match

NOW = datetime(2026, 9, 14, 23, 0)


def order(order_no, recipient, placed, postal="40906", street="2626 KY 930", status="Shipped"):
    return {"order_no": order_no, "recipient_name": recipient, "order_time": placed, "postal_code": postal,
            "address_line_1": street, "city": "Barbourville", "state_province": "KY",
            "supplier_status_str": status, "carrier": "USPS"}


def sales(number, customer, when, pos=(), address="", zip_=""):
    return {"order_number": number, "customer_name": customer, "shipping_name": customer, "order_date": when,
            "status": "Delivered", "shipping_address": address, "customer_zip": zip_, "pos": list(pos)}


def po(number, vendor="DIGI", status="Closed", ref="", notes="", items=0):
    return {"po_number": number, "vendor": vendor, "status": status, "total": 100, "supplier_reference": ref,
            "notes": notes, "line_items": items}


def run(**inputs):
    base = dict(orders=[], lines=[], api=[], crm_orders=[], shipments=[], digi_pos=[])
    base.update(inputs)
    return digi_match.match(base, NOW)


def row(rows, order_no):
    return next(r for r in rows if r["digi_order_no"] == order_no)


# ---------------------------------------------------------------------------
def test_the_digi_order_number_on_a_po_is_the_strongest_link():
    rows, _ = run(orders=[order("ORD-1", "Enrique Vasquez", "2026-07-20 10:00")],
                  crm_orders=[sales("ORD-2026-0059", "Enrique Vasquez", "2026-07-19",
                                    pos=[po("PO-68", notes="Placed as ORD-1")])])
    r = row(rows, "ORD-1")
    assert (r["confidence"], r["matched_on"], r["purchase_orders"]) == \
        ("High", "DIGI order number on the purchase order", "PO-68")


def test_a_tracking_number_on_a_shipment_is_exact():
    rows, _ = run(
        orders=[order("ORD-2", "Coach Armando", "2026-09-08 16:04", postal="90638", street="13902 Larwin Road")],
        api=[{"platformoid": "ORD-2", "orderstatus": 12, "trackingnumber": "9334620845500036816016"}],
        crm_orders=[sales("ORD-2026-0141", "Coach Armando", "2026-09-07", pos=[po("PO-161")])],
        shipments=[{"shipment_number": "SHP-165", "tracking_number": "9334620845500036816016",
                    "sales_order": "ORD-2026-0141", "address": "13902 Larwin Rd, La Mirada, CA, 90638-2833",
                    "ship_to_postal_code": "90638-2833", "recipient_name": "Coach Armando"}])
    r = row(rows, "ORD-2")
    assert (r["confidence"], r["sales_order"], r["shipment"]) == ("High", "ORD-2026-0141", "SHP-165")
    # Rd and Road, and ZIP+4, are the same address.
    assert r["same_address"] == "Yes"


def test_name_and_zip_close_in_date_is_medium():
    rows, _ = run(orders=[order("ORD-3", "Dennis Chavies", "2026-09-08 11:24", status="Closed")],
                  crm_orders=[sales("ORD-2026-0140", "Dennis Chavies", "2026-09-06",
                                    address="2626 KY 930, Barbourville KY 40906", pos=[po("PO-160")])])
    r = row(rows, "ORD-3")
    assert (r["confidence"], r["matched_on"]) == ("Medium", "Customer name + ZIP code + date")


def test_a_misspelled_name_still_matches_but_says_so():
    rows, _ = run(orders=[order("ORD-4", "Thomas Garica", "2026-08-26 21:03")],
                  crm_orders=[sales("ORD-2026-0200", "Thomas Garcia", "2026-08-25",
                                    address="2626 KY 930 40906", pos=[po("PO-200")])])
    r = row(rows, "ORD-4")
    assert r["confidence"] == "Low"
    assert 'DIGI "Thomas Garica", database "Thomas Garcia"' in r["notes"]


def test_a_sales_order_fulfilled_weeks_earlier_is_not_trusted():
    """Joseph Giles: a September order matched only to an August sales order already shipped."""
    rows, _ = run(
        orders=[order("ORD-OLD", "Joseph Giles", "2026-08-20 21:07"),
                order("ORD-NEW", "Joseph Giles", "2026-09-14 20:17", status="Factory Audit")],
        crm_orders=[sales("ORD-2026-0109", "Joseph Giles", "2026-08-20", address="x 40906",
                          pos=[po("PO-119", notes="ORD-OLD")])])
    new = row(rows, "ORD-NEW")
    assert new["confidence"] == "Low"
    assert "already fulfilled by ORD-OLD" in new["notes"]


def test_a_po_whose_sales_order_was_deleted_is_still_found():
    """Juan Moreno: FREE-2026-0013 was deleted; PO-2026-0128 was not."""
    rows, pos = run(orders=[order("ORD-5", "Juan Moreno", "2026-08-24 22:12")],
                    digi_pos=[{"po_number": "PO-128", "vendor_name": "DIGI", "status": "Sent",
                               "order_date": "2026-08-24", "customer_name": "Juan Moreno",
                               "sales_order": "FREE-2026-0013", "sales_order_deleted": True, "line_items": 0}])
    r = row(rows, "ORD-5")
    assert (r["confidence"], r["purchase_orders"], r["sales_order_status"]) == ("Medium", "PO-128", "Deleted")
    assert pos == []


def test_nothing_fits_says_why():
    rows, _ = run(orders=[order("ORD-6", "Muhammad Hassaan", "2026-07-15 07:20", status="Closed")])
    r = row(rows, "ORD-6")
    assert r["confidence"] == "Not matched"
    assert "possibly a test or cancelled order" in r["notes"]
    assert r["needs_check"] == "Yes"


def test_a_digi_po_with_no_digi_order_is_listed():
    # The first DIGI order predates the PO, so the PO is not from before BlankTex's time.
    _, pos = run(orders=[order("ORD-7", "Someone Else", "2026-07-15 07:20")],
                 digi_pos=[{"po_number": "PO-122", "vendor_name": "DIGI", "status": "In Production",
                            "order_date": "2026-08-21", "created_at": "2026-08-21 10:00",
                            "customer_name": "Blanca Mosqueda", "sales_order": "ORD-2026-0112",
                            "sales_order_deleted": False, "line_items": 0}])
    assert [p["purchase_order"] for p in pos] == ["PO-122"]
    assert pos[0]["before_first_digi_order"] == "No"


def test_every_query_only_reads():
    for sql in digi_match.READ_QUERIES:
        text = sql.lower()
        assert text.lstrip().startswith("select")
        assert ";" not in text
        for word in ("insert ", "update ", "delete ", "drop ", "alter ", "truncate "):
            assert word not in text, word
