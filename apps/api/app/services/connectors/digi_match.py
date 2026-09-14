"""
DIGI orders matched to Decoinks sales orders, purchase orders and shipments.

BlankTex places orders with DIGI but stores no link back to the Decoinks sales
order or purchase order they were for. The link has to be found, and one rule is
not enough to find it, so there are four, tried in order of how certain they are:

  1. The DIGI order number written on a purchase order.            High
  2. DIGI's tracking number on a Decoinks shipment.                High
  3. The customer's name and ZIP code, with the dates close.       Medium
     A name spelled slightly differently, no ZIP to compare, or a
     sales order another DIGI order fulfilled over a week earlier.  Low
  4. A DIGI purchase order whose sales order has been deleted,
     by the customer's name and the PO's date.                     Medium / Low

Fuzzy names and date closeness cannot be written as a report join, which is why
this is code: it runs on every sync and writes an ordinary table that reports are
built on. Every query here only reads, on the same read-only connection as every
report.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, datetime, timezone

import sqlalchemy as sa

STATUS = {1: "Store Audit", 2: "Pending Push", 3: "Rejected", 4: "Factory Audit", 5: "In Production",
          12: "Shipped", 13: "Closed", 14: "Refunding", 15: "Refunded"}

ABBREVIATIONS = {"rd": "road", "st": "street", "ave": "avenue", "av": "avenue", "blvd": "boulevard",
                 "dr": "drive", "ln": "lane", "ct": "court", "hwy": "highway", "pkwy": "parkway",
                 "pl": "place", "cir": "circle", "ter": "terrace", "sq": "square", "apt": "apartment"}

#: How far a sales order's date may sit from the DIGI order it became.
DAYS_BEFORE, DAYS_AFTER = 45, 7

# ---------------------------------------------------------------------------
# Queries -- all reads
# ---------------------------------------------------------------------------
SINCE = "(select min(order_time) - interval '45 days' from blanktex.purchases)"

BLANKTEX_ORDERS_SQL = """
select p.order_no, p.supplier_status_str, p.order_time, p.recipient_name, p.address_line_1, p.address_line_2,
       p.city, p.state_province, p.postal_code, p.carrier, p.last_sync_error, u.email as created_by_email
from blanktex.purchases p left join blanktex.admin_users u on u.user_id = p.created_by
"""

LINES_SQL = """
select p.order_no, g ->> 'styleCode' as style_code, g ->> 'colorName' as color_name,
       g ->> 'sizeCode' as size, (g ->> 'num')::int as qty
from blanktex.purchases p,
     jsonb_array_elements(case when jsonb_typeof(p.supplier_payload -> 'goodsList') = 'array'
                               then p.supplier_payload -> 'goodsList' else '[]'::jsonb end) g
"""

CRM_ORDERS_SQL = f"""
select o.order_number, o.order_date, o.created_at, o.status::text as status, o.shipping_name,
       regexp_replace(coalesce(o.shipping_address, ''), '\\s+', ' ', 'g') as shipping_address,
       o.contact_name, o.total, c.customer_number, c.name as customer_name, c.zip as customer_zip,
       (select json_agg(json_build_object(
                  'po_number', po.po_number, 'vendor', po.vendor_name, 'status', po.status::text,
                  'order_date', po.order_date, 'total', po.grand_total,
                  'supplier_reference', po.supplier_reference,
                  'notes', left(coalesce(po.notes, '') || ' ' || coalesce(po.internal_notes, ''), 400),
                  'line_items', (select count(*) from public.purchase_order_items i where i.po_id = po.id))
                order by po.created_at)
          from public.purchase_orders po where po.order_id = o.id and po.deleted_at is null) as pos
from public.orders o left join public.customers c on c.id = o.customer_id
where o.deleted_at is null
  and (o.order_date >= {SINCE}::date or o.created_at >= {SINCE}
       or o.id in (select po.order_id from public.purchase_orders po
                   where po.deleted_at is null and po.created_at >= {SINCE} and po.vendor_name ilike 'digi%'))
"""

SHIPMENTS_SQL = f"""
select s.shipment_number, s.status::text as status, s.tracking_number, s.ship_date, s.recipient_name,
       s.customer_name, regexp_replace(coalesce(s.address, ''), '\\s+', ' ', 'g') as address,
       s.ship_to_postal_code, o.order_number as sales_order
from public.shipments s left join public.orders o on o.id = s.order_id
where s.deleted_at is null and (s.created_at >= {SINCE} or o.order_date >= {SINCE}::date)
"""

DIGI_POS_SQL = f"""
select po.po_number, po.vendor_name, po.status::text as status, po.order_date, po.created_at, po.grand_total,
       po.supplier_reference, left(coalesce(po.notes, ''), 400) as notes,
       o.order_number as sales_order, (o.deleted_at is not null) as sales_order_deleted,
       c.name as customer_name,
       (select count(*) from public.purchase_order_items i where i.po_id = po.id) as line_items
from public.purchase_orders po
left join public.orders o on o.id = po.order_id
left join public.customers c on c.id = coalesce(po.customer_id, o.customer_id)
where po.deleted_at is null and po.vendor_name ilike 'digi%' and po.created_at >= {SINCE}
"""

READ_QUERIES = (BLANKTEX_ORDERS_SQL, LINES_SQL, CRM_ORDERS_SQL, SHIPMENTS_SQL, DIGI_POS_SQL)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def norm(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def street(text) -> str:
    """A street with its abbreviations spelled out: 'Larwin Rd' and 'Larwin Road' are one street."""
    return "".join(ABBREVIATIONS.get(w, w) for w in re.findall(r"[a-z0-9]+", str(text or "").lower()))


def zip5(text) -> str:
    found = re.findall(r"\b(\d{5})(?:-\d{4})?\b", str(text or ""))
    return found[-1] if found else ""


def when(value) -> datetime | None:
    """Anything date-like as a naive UTC datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value).replace("T", " ")
    for fmt, size in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            return datetime.strptime(text[:size], fmt)
        except ValueError:
            continue
    return None


def _levenshtein(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def name_match(a, b) -> int:
    """2 = the same name, 1 = the same name spelled slightly differently, 0 = different."""
    if not a or not b:
        return 0
    if norm(a) == norm(b):
        return 2
    ta = {t for t in re.findall(r"[a-z]+", str(a).lower()) if len(t) > 1}
    tb = {t for t in re.findall(r"[a-z]+", str(b).lower()) if len(t) > 1}
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(short) >= 2 and short <= long_:
        return 2
    na, nb = norm(a), norm(b)
    if min(len(na), len(nb)) >= 6 and _levenshtein(na, nb) <= 2:
        return 1
    if len(short) >= 2 and all(any(_levenshtein(s, l) <= 1 for l in long_) for s in short):
        return 1
    return 0


def _stamp(value) -> str:
    moment = when(value)
    return moment.strftime("%Y-%m-%d %H:%M") if moment else ""


def _day(value) -> str:
    moment = when(value)
    return moment.strftime("%Y-%m-%d") if moment else ""


def _yes(flag) -> str:
    return "Yes" if flag else "No"


# ---------------------------------------------------------------------------
# Matching -- a pure function of its inputs, so it can be tested without a database
# ---------------------------------------------------------------------------
def match(inputs: dict, now: datetime) -> tuple[list[dict], list[dict]]:
    """
    Rows for both tables: one per DIGI order, and one per DIGI purchase order no
    DIGI order was matched to.

    inputs: orders, lines, api, crm_orders, shipments, digi_pos -- lists of dicts.
    """
    orders = sorted(inputs["orders"], key=lambda b: when(b["order_time"]) or datetime.min)
    api = {r["platformoid"]: r for r in inputs.get("api", [])}
    lines_by_order = defaultdict(list)
    for line in inputs["lines"]:
        lines_by_order[line["order_no"]].append(line)
    crm_orders = inputs["crm_orders"]
    shipments = inputs["shipments"]
    digi_pos = inputs["digi_pos"]

    shipment_by_tracking = {str(s["tracking_number"]).strip(): s for s in shipments if s.get("tracking_number")}
    order_by_number = {o["order_number"]: o for o in crm_orders}
    shipments_by_order = defaultdict(list)
    for s in shipments:
        if s.get("sales_order"):
            shipments_by_order[s["sales_order"]].append(s)

    def order_zips(o):
        zips = {zip5(o.get("shipping_address")), zip5(o.get("shipping_name")), str(o.get("customer_zip") or "")[:5]}
        zips |= {zip5(s.get("ship_to_postal_code")) for s in shipments_by_order[o["order_number"]]}
        return {z for z in zips if z}

    def order_names(o):
        names = [o.get("customer_name"), o.get("shipping_name"), o.get("contact_name")]
        for s in shipments_by_order[o["order_number"]]:
            names += [s.get("recipient_name"), s.get("customer_name")]
        return [n for n in names if n]

    def pos_for(o):
        pos = o.get("pos") or []
        digi = [p for p in pos if norm(p.get("vendor")).startswith("digi")]
        return digi or pos

    def tracking_of(order_no):
        return str((api.get(order_no) or {}).get("trackingnumber") or "").strip()

    # -- 1 and 2: exact links -------------------------------------------------------
    links: dict[str, dict] = {}
    for b in orders:
        order_no = b["order_no"]
        for o in crm_orders:
            if any(order_no in str(p.get("supplier_reference") or "") or order_no in str(p.get("notes") or "")
                   for p in o.get("pos") or []):
                links[order_no] = dict(order=o, method="DIGI order number on the purchase order",
                                       confidence="High", shipment=None, notes=[], pos=None)
                break
        tracking = tracking_of(order_no)
        if order_no not in links and tracking in shipment_by_tracking:
            shipment = shipment_by_tracking[tracking]
            o = order_by_number.get(shipment.get("sales_order"))
            if o:
                links[order_no] = dict(order=o, method="Tracking number", confidence="High",
                                       shipment=shipment, notes=[], pos=None)

    placed_by_order = {b["order_no"]: when(b["order_time"]) for b in orders}
    exact_on_order = defaultdict(list)
    for order_no, link in links.items():
        exact_on_order[link["order"]["order_number"]].append((order_no, placed_by_order.get(order_no)))

    # -- 3: name, ZIP and date --------------------------------------------------------
    for b in orders:
        order_no = b["order_no"]
        placed = placed_by_order[order_no]
        if order_no in links or not placed:
            continue
        scored = []
        for o in crm_orders:
            similarity = max((name_match(b["recipient_name"], n) for n in order_names(o)), default=0)
            od = when(o.get("order_date")) or when(o.get("created_at"))
            if not similarity or not od:
                continue
            gap = (placed - od).days
            if not -DAYS_AFTER <= gap <= DAYS_BEFORE:
                continue
            zip_ok = zip5(b.get("postal_code")) in order_zips(o)
            reorder = [x for x in exact_on_order.get(o["order_number"], [])
                       if x[1] and abs((placed - x[1]).days) > 7]
            score = (similarity * 2 + (3 if zip_ok else 0)
                     + (3 if abs(gap) <= 1 else 2 if abs(gap) <= 7 else 1 if abs(gap) <= 21 else 0)
                     + (1 if pos_for(o) else 0) - (3 if reorder else 0))
            scored.append(dict(score=score, gap=abs(gap), order=o, zip_ok=zip_ok, similarity=similarity,
                               reorder=reorder))
        if not scored:
            continue
        scored.sort(key=lambda c: (-c["score"], c["gap"]))
        best = scored[0]
        notes = []
        if best["similarity"] == 1:
            spelled = next(n for n in order_names(best["order"]) if name_match(b["recipient_name"], n) == 1)
            notes.append(f'Name spelled differently: DIGI "{b["recipient_name"]}", database "{spelled}".')
        if best["reorder"]:
            earlier = ", ".join(x[0] for x in best["reorder"])
            notes.append(f'{best["order"]["order_number"]} was already fulfilled by {earlier}, placed more than '
                         "a week apart. This may be a new order that is not in the database yet.")
        if len(scored) > 1 and scored[1]["score"] == best["score"]:
            notes.append(f'{scored[1]["order"]["order_number"]} fits equally well; the closest by date was used.')
        links[order_no] = dict(
            order=best["order"],
            method="Customer name + ZIP code + date" if best["zip_ok"] else "Customer name + date (no ZIP to compare)",
            confidence="Medium" if best["similarity"] == 2 and best["zip_ok"] and not best["reorder"] else "Low",
            shipment=None, notes=notes, pos=None)

    # -- 4: a DIGI purchase order whose sales order was deleted -------------------------
    claimed = set()
    for link in links.values():
        if link["confidence"] in ("High", "Medium"):
            claimed.update(p["po_number"] for p in pos_for(link["order"]))
    orphans = [p for p in digi_pos if p.get("sales_order_deleted") or p.get("sales_order") not in order_by_number]
    for b in orders:
        order_no = b["order_no"]
        placed = placed_by_order[order_no]
        link = links.get(order_no)
        if (link and link["confidence"] == "High") or not placed:
            continue
        best = None
        for po in orphans:
            if po["po_number"] in claimed:
                continue
            similarity = name_match(b["recipient_name"], po.get("customer_name"))
            po_date = when(po.get("order_date")) or when(po.get("created_at"))
            if not similarity or not po_date:
                continue
            gap = abs((placed - po_date).days)
            if gap <= 3 and (best is None or (-similarity, gap) < best[0]):
                best = ((-similarity, gap), po, similarity, gap)
        if not best:
            continue
        _, po, similarity, gap = best
        if link:
            link_date = when(link["order"].get("order_date")) or when(link["order"].get("created_at"))
            link_gap = abs((placed - link_date).days) if link_date else 999
            fulfilled = any("already fulfilled" in n for n in link["notes"])
            if not (fulfilled or (gap <= 1 and gap < link_gap)):
                continue
        claimed.add(po["po_number"])
        notes = []
        if similarity == 1:
            notes.append(f'Name spelled differently: DIGI "{b["recipient_name"]}", database "{po["customer_name"]}".')
        notes.append(f'Sales order {po.get("sales_order") or "(none)"} has been deleted, but purchase order '
                     f'{po["po_number"]} still exists.')
        links[order_no] = dict(
            order={"order_number": po.get("sales_order"), "customer_name": po.get("customer_name"),
                   "status": "Deleted", "stub": True},
            method="Customer name + purchase order date",
            confidence="Medium" if gap <= 1 and similarity == 2 else "Low",
            shipment=None, notes=notes,
            pos=[{"po_number": po["po_number"], "vendor": po.get("vendor_name"), "status": po.get("status"),
                  "total": po.get("grand_total"), "line_items": po.get("line_items"),
                  "supplier_reference": po.get("supplier_reference"), "notes": po.get("notes")}])

    # -- rows --------------------------------------------------------------------------
    order_rows, used_pos = [], set()
    for b in orders:
        order_no = b["order_no"]
        a = api.get(order_no) or {}
        placed = placed_by_order[order_no]
        tracking = tracking_of(order_no)
        order_lines = lines_by_order[order_no]
        status_name = STATUS.get(a.get("orderstatus"), b.get("supplier_status_str") or "")
        link = links.get(order_no)
        sales = link["order"] if link else None
        notes = list(link["notes"]) if link else []
        pos = (link["pos"] or pos_for(sales)) if link else []
        used_pos.update(p["po_number"] for p in pos)

        shipment = (link or {}).get("shipment")
        if sales and not shipment and tracking:
            shipment = next((s for s in shipments_by_order[sales["order_number"]]
                             if str(s.get("tracking_number") or "").strip() == tracking), None)
        db_address = (shipment or {}).get("address") or ""
        if not db_address and sales and not sales.get("stub"):
            others = shipments_by_order[sales["order_number"]]
            db_address = (others[0].get("address") if others else "") or sales.get("shipping_address") or ""

        same_address = ""
        if sales and not sales.get("stub"):
            zip_ok = zip5(b.get("postal_code")) in order_zips(sales)
            street_ok = bool(norm(db_address).replace("unitedstates", "")) and street(b.get("address_line_1")) in street(db_address)
            same_address = "Yes" if zip_ok and street_ok else "ZIP only" if zip_ok else "Different"

        ship_date_differs = False
        if shipment and shipment.get("ship_date") and a.get("shippingtime"):
            d1, d2 = when(shipment["ship_date"]), when(a["shippingtime"])
            ship_date_differs = bool(d1 and d2 and d1.date() != d2.date())

        if not sales:
            if status_name == "Closed":
                notes.append("Closed at DIGI, and no matching customer order is in the database "
                             "(possibly a test or cancelled order).")
            elif placed and (now - placed).days <= 2:
                notes.append("Placed in the last two days; the sales order may not be in the database yet, "
                             "or the name differs.")
            else:
                notes.append("No sales order or shipment in the database matches this DIGI order.")
        else:
            if not pos:
                same_customer = [p["po_number"] for p in digi_pos if p["po_number"] not in used_pos
                                 and name_match(b["recipient_name"], p.get("customer_name")) == 2]
                notes.append(f"Sales order {sales['order_number']} has no purchase order."
                             + (f" This customer has DIGI purchase order(s) with no DIGI order: "
                                f"{', '.join(same_customer)}." if same_customer else ""))
            for p in pos:
                if p.get("status") == "Draft" and status_name == "Shipped":
                    notes.append(f"{p['po_number']} is still Draft, but DIGI has shipped.")
            if sales.get("status") in ("Draft", "Confirmed") and status_name == "Shipped":
                notes.append(f"Sales order {sales['order_number']} is {sales['status']}, but DIGI has shipped.")
            if same_address == "Different":
                notes.append("The address in the database is different from the DIGI address.")
        if status_name == "Shipped" and not tracking:
            notes.append("DIGI says shipped but gave no tracking number.")
        if b.get("last_sync_error"):
            notes.append(f"Last sync error: {str(b['last_sync_error'])[:120]}")

        confidence = link["confidence"] if link else "Not matched"
        order_rows.append({
            "digi_order_no": order_no,
            "placed_at": _stamp(placed),
            "month": placed.strftime("%Y-%m") if placed else "",
            "digi_status": status_name,
            "shipped_by_digi_at": _stamp(a.get("shippingtime")),
            "carrier": a.get("express") or b.get("carrier"),
            "tracking_number": tracking,
            "label_pdf": a.get("waybilldatapath"),
            "lines": len(order_lines),
            "pieces": sum(int(l.get("qty") or 0) for l in order_lines),
            "items": "; ".join(f"{l.get('style_code')} {l.get('color_name')} {l.get('size')} x{l.get('qty')}"
                               for l in order_lines),
            "recipient": b.get("recipient_name"),
            "digi_address": ", ".join(x for x in (b.get("address_line_1"), b.get("address_line_2"), b.get("city"),
                                                   f'{b.get("state_province") or ""} {b.get("postal_code") or ""}'.strip())
                                      if x),
            "placed_by": b.get("created_by_email"),
            "customer": (sales or {}).get("customer_name"),
            "customer_no": (sales or {}).get("customer_number"),
            "sales_order": (sales or {}).get("order_number"),
            "sales_order_date": _day((sales or {}).get("order_date")),
            "sales_order_status": (sales or {}).get("status"),
            "sales_order_total": (sales or {}).get("total"),
            "purchase_orders": ", ".join(p["po_number"] for p in pos),
            "po_vendor": ", ".join(sorted({str(p.get("vendor") or "") for p in pos})),
            "po_status": ", ".join(str(p.get("status") or "") for p in pos),
            "po_total": round(sum(float(p.get("total") or 0) for p in pos), 2) if pos else None,
            "po_line_items": ", ".join(str(p.get("line_items")) for p in pos),
            "shipment": (shipment or {}).get("shipment_number"),
            "shipment_status": (shipment or {}).get("status"),
            "ship_date_in_database": _day((shipment or {}).get("ship_date")),
            "database_address": db_address,
            "same_address": same_address,
            "matched_on": (link or {}).get("method", ""),
            "confidence": confidence,
            "needs_check": _yes(confidence in ("Low", "Not matched") or bool(notes)),
            "po_has_line_items": _yes(any((p.get("line_items") or 0) > 0 for p in pos)) if pos else "",
            "digi_order_no_on_po": _yes(any(order_no in str(p.get("supplier_reference") or "")
                                            or order_no in str(p.get("notes") or "") for p in pos)) if pos else "",
            "sales_order_has_address": (_yes(norm((sales or {}).get("shipping_address")) not in ("", "unitedstates"))
                                        if sales and not sales.get("stub") else ""),
            "ship_date_differs": _yes(ship_date_differs) if shipment else "",
            "notes": " ".join(notes),
        })

    first_placed = min((p for p in placed_by_order.values() if p), default=None)
    po_rows = []
    for p in digi_pos:
        if p["po_number"] in used_pos:
            continue
        created = when(p.get("created_at"))
        po_rows.append({
            "purchase_order": p["po_number"],
            "po_date": _day(p.get("order_date")),
            "created_at": _stamp(created),
            "status": p.get("status"),
            "customer": p.get("customer_name"),
            "sales_order": p.get("sales_order"),
            "sales_order_deleted": _yes(p.get("sales_order_deleted")),
            "po_total": p.get("grand_total"),
            "line_items": p.get("line_items"),
            "before_first_digi_order": _yes(bool(created and first_placed and created < first_placed)),
            "customer_digi_orders": ", ".join(r["digi_order_no"] for r in order_rows
                                              if name_match(r["recipient"], p.get("customer_name")) == 2),
            "notes": p.get("notes"),
        })
    return order_rows, po_rows


# ---------------------------------------------------------------------------
# Loading -- called by the sync with its own session and dataset
# ---------------------------------------------------------------------------
def _operational(session, sql: str) -> list[dict]:
    from app.services import schema_service  # deferred: schema_service imports connector_service

    result = schema_service.adapter_for(session).execute(sa.text(sql), max_rows=200_000)
    return [dict(zip(result.columns, row)) for row in result.rows]


def _api_rows(session, dataset) -> list[dict]:
    """Status, dispatch time and tracking from the synced DIGI API tables, when they exist."""
    from app.core.db import get_engine
    from app.models.metadata_models import ConnectorDataset
    from app.services.connector_service import CONNECTOR_SCHEMA

    tables = {}
    for key in ("order_info", "order_delivery"):
        sibling = session.scalar(sa.select(ConnectorDataset).where(
            ConnectorDataset.connector_id == dataset.connector_id, ConnectorDataset.dataset_key == key))
        if sibling is None or not sibling.physical_table or not re.fullmatch(r"api_[a-z0-9]+", sibling.physical_table):
            return []  # without them, tracking numbers are unavailable and those matches fall to name and ZIP
        tables[key] = sibling.physical_table
    sql = (f'select i.platformoid, i.orderstatus, i.shippingtime, d.trackingnumber, d.express, d.waybilldatapath '
           f'from "{CONNECTOR_SCHEMA}"."{tables["order_info"]}" i '
           f'left join "{CONNECTOR_SCHEMA}"."{tables["order_delivery"]}" d on d.platformoid = i.platformoid')
    with get_engine().connect() as connection:
        return [dict(r._mapping) for r in connection.execute(sa.text(sql))]


def load(session, dataset) -> dict:
    return {
        "orders": _operational(session, BLANKTEX_ORDERS_SQL),
        "lines": _operational(session, LINES_SQL),
        "crm_orders": _operational(session, CRM_ORDERS_SQL),
        "shipments": _operational(session, SHIPMENTS_SQL),
        "digi_pos": _operational(session, DIGI_POS_SQL),
        "api": _api_rows(session, dataset),
    }


def order_rows(session, dataset) -> list[dict]:
    return match(load(session, dataset), datetime.utcnow())[0]


def po_rows(session, dataset) -> list[dict]:
    return match(load(session, dataset), datetime.utcnow())[1]
