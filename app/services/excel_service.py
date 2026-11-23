import io
import re
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from motor.motor_asyncio import AsyncIOMotorDatabase

from backend.app.schemas.excel import (
    ImportPreviewResponse,
    ImportPreviewRow,
    ImportErrorItem,
    ImportCommitResponse,
)
from backend.app.services.sequence_service import (
    generate_property_id,
    generate_customer_id,
    generate_enquiry_id,
)

# Header style for exported Excel workbooks
HEADER_FONT = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
HEADER_FILL = PatternFill(start_color="313350", end_color="313350", fill_type="solid")
CELL_FONT = Font(name="Calibri", size=10)
CELL_BORDER = Border(
    left=Side(style="thin", color="E5E7EB"),
    right=Side(style="thin", color="E5E7EB"),
    top=Side(style="thin", color="E5E7EB"),
    bottom=Side(style="thin", color="E5E7EB"),
)

LEGACY_STATUS_MAP = {
    "available": "available",
    "avail": "available",
    "ready": "available",
    "vacant": "available",
    "on hold": "under_negotiation",
    "hold": "under_negotiation",
    "under negotiation": "under_negotiation",
    "negotiation": "under_negotiation",
    "rented": "rented",
    "leased": "rented",
    "sold": "sold",
    "closed": "sold",
    "not available": "archived",
    "not connected": "archived",
    "cancelled": "archived",
    "archived": "archived",
    "active": "available",
}


async def export_properties_to_excel(db: AsyncIOMotorDatabase, filters: Dict[str, Any] = None) -> bytes:
    """PRD 5.19: Export properties collection to formatted Excel workbook."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Properties"

    headers = [
        "Property ID",
        "Transaction Type",
        "Property Type",
        "Society Name",
        "Location",
        "BHK",
        "Expected Price (₹)",
        "Negotiable",
        "Maintenance Mode",
        "Maintenance Amount (₹)",
        "Security Deposit (₹)",
        "Status",
        "Owner Name",
        "Owner Phone",
        "Tenant Preference",
        "Created At",
    ]

    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")

    query = filters or {}
    properties = await db.properties.find(query).sort("created_at", -1).to_list(10000)

    for p in properties:
        row = [
            p.get("property_id", ""),
            str(p.get("transaction_type", "")).upper(),
            p.get("property_type", ""),
            p.get("society_name", ""),
            p.get("location", ""),
            p.get("bhk", "N/A"),
            float(p.get("expected_amount", 0)),
            "Yes" if p.get("negotiable") else "No",
            p.get("maintenance_mode", "included"),
            float(p.get("maintenance_amount", 0) or 0),
            float(p.get("security_deposit", 0) or 0),
            str(p.get("status", "")).upper(),
            p.get("owner_name", ""),
            p.get("owner_phone", ""),
            p.get("tenant_type", "Family / Bachelors"),
            p.get("created_at").strftime("%Y-%m-%d %H:%M") if p.get("created_at") else "",
        ]
        ws.append(row)

    # Style rows & adjust column width
    for row in ws.iter_rows(min_row=2, max_col=len(headers)):
        for cell in row:
            cell.font = CELL_FONT
            cell.border = CELL_BORDER

    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


async def export_enquiries_to_excel(db: AsyncIOMotorDatabase, filters: Dict[str, Any] = None) -> bytes:
    """PRD 5.19: Export enquiries collection to formatted Excel workbook."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Enquiries"

    headers = [
        "Enquiry ID",
        "Customer ID",
        "Customer Name",
        "Customer Phone",
        "Transaction Type",
        "Looking For",
        "Preferred Societies",
        "Preferred Locations",
        "Budget Min (₹)",
        "Budget Max (₹)",
        "Priority",
        "Status",
        "Next Follow-up At",
        "Created At",
    ]

    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")

    query = filters or {}
    enquiries = await db.enquiries.find(query).sort("created_at", -1).to_list(10000)

    for e in enquiries:
        row = [
            e.get("enquiry_id", ""),
            e.get("customer_id", ""),
            e.get("customer_name", ""),
            e.get("customer_phone", ""),
            str(e.get("transaction_type", "")).upper(),
            e.get("looking_for", ""),
            ", ".join(e.get("preferred_societies", [])) if isinstance(e.get("preferred_societies"), list) else "",
            ", ".join(e.get("preferred_locations", [])) if isinstance(e.get("preferred_locations"), list) else "",
            float(e.get("budget_min", 0) or 0),
            float(e.get("budget_max", 0) or 0),
            str(e.get("priority", "warm")).capitalize(),
            str(e.get("status", "")).upper(),
            e.get("next_followup_at").strftime("%Y-%m-%d %H:%M") if e.get("next_followup_at") else "None",
            e.get("created_at").strftime("%Y-%m-%d %H:%M") if e.get("created_at") else "",
        ]
        ws.append(row)

    for row in ws.iter_rows(min_row=2, max_col=len(headers)):
        for cell in row:
            cell.font = CELL_FONT
            cell.border = CELL_BORDER

    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


async def export_deals_to_excel(db: AsyncIOMotorDatabase) -> bytes:
    """PRD 5.19: Export closed deals & financial settlements to Excel."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Deals"

    headers = [
        "Deal ID",
        "Enquiry ID",
        "Property ID",
        "Transaction Type",
        "Customer Name",
        "Customer Phone",
        "Society",
        "Deal Amount (₹)",
        "Commission Expected (₹)",
        "Settlement Status",
        "Executive Name",
        "Deal Date",
    ]

    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")

    deals = await db.deals.find({}).sort("deal_date", -1).to_list(5000)

    for d in deals:
        row = [
            d.get("deal_id", ""),
            d.get("enquiry_id", ""),
            d.get("property_id", ""),
            str(d.get("transaction_type", "")).upper(),
            d.get("customer_name", ""),
            d.get("customer_phone", ""),
            d.get("property_society", ""),
            float(d.get("deal_amount", 0) or 0),
            float(d.get("commission_amount", 0) or 0),
            str(d.get("settlement_status", "pending")).capitalize(),
            d.get("responsible_executive_name", ""),
            d.get("deal_date").strftime("%Y-%m-%d") if d.get("deal_date") else "",
        ]
        ws.append(row)

    for row in ws.iter_rows(min_row=2, max_col=len(headers)):
        for cell in row:
            cell.font = CELL_FONT
            cell.border = CELL_BORDER

    for col in ws.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = openpyxl.utils.get_column_letter(col[0].column)
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output.getvalue()


async def parse_and_preview_excel(
    file_bytes: bytes,
    filename: str,
    db: AsyncIOMotorDatabase,
) -> ImportPreviewResponse:
    """
    PRD 5.19: Parse Excel workbook, perform row-level validation, legacy status mapping,
    and duplicate checks. Return dry-run preview report.
    """
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as e:
        raise ValueError(f"Unable to read Excel workbook: {str(e)}")

    sheet_names = wb.sheetnames
    ws = wb.active

    # Detect sheet type from sheet name or headers
    first_row = [str(cell.value or "").strip().lower() for cell in next(ws.iter_rows(min_row=1, max_row=1))]

    sheet_type = "properties"
    if any("enquiry" in h or "lead" in h or "looking" in h for h in first_row) or "enquiry" in ws.title.lower():
        sheet_type = "enquiries"

    headers_raw = [str(cell.value or "").strip() for cell in next(ws.iter_rows(min_row=1, max_row=1))]
    headers = [h.lower().replace(" ", "_").replace("(", "").replace(")", "").replace("₹", "").strip("_") for h in headers_raw]

    rows_data: List[Dict[str, Any]] = []
    for r_idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not any(row):
            continue
        row_dict = {}
        for h, val in zip(headers, row):
            if h:
                row_dict[h] = val
        row_dict["_excel_row"] = r_idx
        rows_data.append(row_dict)

    preview_rows: List[ImportPreviewRow] = []
    error_items: List[ImportErrorItem] = []
    valid_count = 0
    error_count = 0
    warning_count = 0
    status_mapping_applied: Dict[str, str] = {}

    for row in rows_data:
        r_num = row["_excel_row"]
        row_errors: List[str] = []
        row_warnings: List[str] = []
        cleaned_data: Dict[str, Any] = {}

        if sheet_type == "properties":
            # 1. Society Name / Society ID
            society_name = str(row.get("society_name") or row.get("society") or "").strip()
            if not society_name:
                row_errors.append("Society name is required.")
                error_items.append(ImportErrorItem(row_number=r_num, field="society_name", error="Society name is required"))
            cleaned_data["society_name"] = society_name

            # 2. Location
            location = str(row.get("location") or row.get("area") or "").strip()
            cleaned_data["location"] = location or "Prime Operational Zone"

            # 3. Transaction Type
            tx_raw = str(row.get("transaction_type") or row.get("type") or "rent").strip().lower()
            cleaned_data["transaction_type"] = "sale" if "sale" in tx_raw or "buy" in tx_raw else "rent"

            # 4. Property Type & BHK
            prop_type = str(row.get("property_type") or "Apartment").strip()
            bhk = str(row.get("bhk") or row.get("bedrooms") or "2 BHK").strip()
            cleaned_data["property_type"] = prop_type
            cleaned_data["bhk"] = bhk

            # 5. Expected Amount
            amt_raw = row.get("expected_price") or row.get("expected_amount") or row.get("price") or row.get("rent") or 0
            try:
                cleaned_data["expected_amount"] = float(amt_raw)
            except (ValueError, TypeError):
                row_errors.append(f"Invalid price value '{amt_raw}'.")
                error_items.append(ImportErrorItem(row_number=r_num, field="expected_amount", error="Price must be numeric", raw_value=str(amt_raw)))
                cleaned_data["expected_amount"] = 0.0

            # 6. Legacy Status Mapping
            status_raw = str(row.get("status") or "available").strip().lower()
            mapped_status = LEGACY_STATUS_MAP.get(status_raw, "available")
            if status_raw not in ["available", "rented", "sold", "archived"]:
                status_mapping_applied[status_raw] = mapped_status
                row_warnings.append(f"Legacy status '{status_raw}' mapped to '{mapped_status}'.")
            cleaned_data["status"] = mapped_status

            # 7. Owner Info
            owner_name = str(row.get("owner_name") or row.get("owner") or "Verified Owner").strip()
            owner_phone = str(row.get("owner_phone") or row.get("phone") or row.get("mobile") or "").strip()
            cleaned_data["owner_name"] = owner_name
            cleaned_data["owner_phone"] = owner_phone

            # 8. Property ID preservation
            existing_pid = str(row.get("property_id") or "").strip()
            if existing_pid:
                cleaned_data["property_id"] = existing_pid
                # Check for existing conflict
                exists = await db.properties.find_one({"property_id": existing_pid})
                if exists:
                    row_warnings.append(f"Property ID {existing_pid} already exists. Record will update existing.")

        else:
            # Enquiries Sheet
            cust_name = str(row.get("customer_name") or row.get("name") or row.get("client") or "").strip()
            cust_phone = str(row.get("customer_phone") or row.get("phone") or row.get("mobile") or "").strip()
            if not cust_name:
                row_errors.append("Customer name is required.")
                error_items.append(ImportErrorItem(row_number=r_num, field="customer_name", error="Customer name missing"))
            if not cust_phone:
                row_errors.append("Customer phone is required.")
                error_items.append(ImportErrorItem(row_number=r_num, field="customer_phone", error="Customer phone missing"))

            cleaned_data["customer_name"] = cust_name
            cleaned_data["customer_phone"] = cust_phone

            tx_raw = str(row.get("transaction_type") or "rent").strip().lower()
            cleaned_data["transaction_type"] = "sale" if "sale" in tx_raw or "buy" in tx_raw else "rent"

            cleaned_data["looking_for"] = str(row.get("looking_for") or row.get("requirement") or "3 BHK").strip()

            try:
                cleaned_data["budget_min"] = float(row.get("budget_min") or row.get("min_budget") or 0)
                cleaned_data["budget_max"] = float(row.get("budget_max") or row.get("max_budget") or 0)
            except Exception:
                cleaned_data["budget_min"] = 0.0
                cleaned_data["budget_max"] = 0.0

            status_raw = str(row.get("status") or "new").strip().lower()
            cleaned_data["status"] = "deal_closed" if "closed" in status_raw else "new"

        is_valid = len(row_errors) == 0
        if is_valid:
            valid_count += 1
        else:
            error_count += 1
        if row_warnings:
            warning_count += len(row_warnings)

        preview_rows.append(
            ImportPreviewRow(
                row_number=r_num,
                is_valid=is_valid,
                data=cleaned_data,
                errors=row_errors,
                warnings=row_warnings,
            )
        )

    return ImportPreviewResponse(
        sheet_type=sheet_type,
        file_name=filename,
        total_rows=len(rows_data),
        valid_count=valid_count,
        error_count=error_count,
        warning_count=warning_count,
        status_mapping_applied=status_mapping_applied,
        preview_rows=preview_rows[:50],  # Return up to 50 preview rows
        errors=error_items[:50],
    )


async def commit_excel_import(
    sheet_type: str,
    file_name: str,
    rows: List[Dict[str, Any]],
    user_id: str,
    user_role: str,
    db: AsyncIOMotorDatabase,
) -> ImportCommitResponse:
    """
    PRD 5.19: Commit validated batch rows into database with sequential ID preservation,
    duplicate handling, and audit event logging.
    """
    now = datetime.now(timezone.utc)
    imported = 0
    skipped = 0

    if sheet_type == "properties":
        for r in rows:
            data = r.get("data", r)
            if not data.get("society_name"):
                skipped += 1
                continue

            # Preserved or new Property ID
            pid = data.get("property_id")
            if not pid:
                pid = await generate_property_id(db)

            doc = {
                "property_id": pid,
                "transaction_type": data.get("transaction_type", "rent"),
                "property_type": data.get("property_type", "Apartment"),
                "society_name": data.get("society_name", ""),
                "society_id": data.get("society_id", "SOC001"),
                "location": data.get("location", "Prime Area"),
                "bhk": data.get("bhk", "2 BHK"),
                "expected_amount": float(data.get("expected_amount", 0)),
                "currency": "INR",
                "negotiable": True,
                "maintenance_mode": "included",
                "maintenance_amount": 0,
                "security_deposit": 0,
                "additional_charges": [],
                "owner_name": data.get("owner_name", "Verified Owner"),
                "owner_phone": data.get("owner_phone", "9811234901"),
                "tenant_type": data.get("tenant_type", "Family Only"),
                "status": data.get("status", "available"),
                "source": "excel_import",
                "imported_file": file_name,
                "created_by": user_id,
                "created_at": now,
                "updated_at": now,
            }

            await db.properties.update_one(
                {"property_id": pid},
                {"$set": doc},
                upsert=True,
            )
            imported += 1

    else:
        # Enquiries & Customers
        for r in rows:
            data = r.get("data", r)
            phone = data.get("customer_phone", "").strip()
            name = data.get("customer_name", "").strip()
            if not phone or not name:
                skipped += 1
                continue

            # Check if customer profile exists by phone
            cust = await db.customers.find_one({"primary_phone": phone})
            if cust:
                cid = cust["customer_id"]
            else:
                cid = await generate_customer_id(db)
                await db.customers.insert_one({
                    "customer_id": cid,
                    "name": name,
                    "primary_phone": phone,
                    "primary_phone_normalized": phone.replace(" ", "").replace("+91", ""),
                    "customer_type": "tenant",
                    "priority": "warm",
                    "source": "excel_import",
                    "created_by": user_id,
                    "created_at": now,
                    "updated_at": now,
                })

            eid = await generate_enquiry_id(db)
            enq_doc = {
                "enquiry_id": eid,
                "customer_id": cid,
                "customer_name": name,
                "customer_phone": phone,
                "transaction_type": data.get("transaction_type", "rent"),
                "looking_for": data.get("looking_for", "3 BHK"),
                "preferred_locations": [],
                "preferred_societies": [],
                "budget_min": float(data.get("budget_min", 0)),
                "budget_max": float(data.get("budget_max", 0)),
                "status": data.get("status", "new"),
                "priority": "warm",
                "source": "excel_import",
                "imported_file": file_name,
                "created_by": user_id,
                "created_at": now,
                "updated_at": now,
            }
            await db.enquiries.insert_one(enq_doc)
            imported += 1

    # Audit log import event (PRD 5.19)
    await db.activity_events.insert_one({
        "event_type": "import.excel",
        "entity_type": sheet_type,
        "actor_id": user_id,
        "actor_role": user_role,
        "details": {
            "file_name": file_name,
            "sheet_type": sheet_type,
            "imported_count": imported,
            "skipped_count": skipped,
        },
        "created_at": now,
    })

    return ImportCommitResponse(
        sheet_type=sheet_type,
        file_name=file_name,
        imported_count=imported,
        skipped_count=skipped,
        message=f"Successfully committed {imported} {sheet_type} records from {file_name}.",
    )
