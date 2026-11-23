from datetime import datetime, timezone, timedelta
from typing import Optional, List
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.app.core.deps import get_current_user, require_roles
from backend.app.core.database import get_database
from backend.app.schemas.report import (
    ReportKPIsResponse,
    ExecutivePerformanceResponse,
    ExecutivePerformanceItem,
    DrilldownResponse,
    DrilldownItem,
)

router = APIRouter()


@router.get("/kpis", response_model=ReportKPIsResponse)
async def get_report_kpis(
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    """
    PRD Section 5.18 & Phase 12:
    Aggregated operational KPIs computed strictly via backend MongoDB queries.
    """
    db = get_database()
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    week_start = now - timedelta(days=7)

    # 1. Properties
    total_props = await db.properties.count_documents({})
    avail_props = await db.properties.count_documents({"status": "available"})
    rented_props = await db.properties.count_documents({"status": "rented"})
    sold_props = await db.properties.count_documents({"status": "sold"})
    arch_props = await db.properties.count_documents({"status": "archived"})

    # 2. Enquiries
    total_enqs = await db.enquiries.count_documents({})
    new_enqs_week = await db.enquiries.count_documents({"created_at": {"$gte": week_start}})
    active_enqs = await db.enquiries.count_documents({"status": {"$nin": ["deal_closed", "aborted", "archived"]}})
    closed_deals = await db.enquiries.count_documents({"status": "deal_closed"})
    aborted_deals = await db.enquiries.count_documents({"status": "aborted"})

    # 3. Visits
    today_visits = await db.visits.count_documents({
        "scheduled_at": {"$gte": today_start, "$lt": today_end},
        "status": {"$in": ["scheduled", "in_progress", "completed"]},
    })
    completed_visits = await db.visits.count_documents({"status": "completed"})
    open_unclaimed = await db.visits.count_documents({"claim_status": "unclaimed", "status": "scheduled"})

    # 4. Follow-ups
    today_followups = await db.followups.count_documents({
        "scheduled_at": {"$gte": today_start, "$lt": today_end},
        "status": {"$in": ["scheduled", "due"]},
    })
    upcoming_followups = await db.followups.count_documents({
        "scheduled_at": {"$gte": today_end},
        "status": "scheduled",
    })
    overdue_followups = await db.followups.count_documents({
        "scheduled_at": {"$lt": today_start},
        "status": {"$in": ["scheduled", "due", "missed"]},
    })

    # 5. Financial volume from deals collection
    pipeline = [
        {
            "$group": {
                "_id": "$transaction_type",
                "total_amount": {"$sum": "$deal_amount"},
            }
        }
    ]
    deal_volume_agg = await db.deals.aggregate(pipeline).to_list(10)
    rent_vol = 0.0
    sale_vol = 0.0
    for row in deal_volume_agg:
        if row["_id"] == "rent":
            rent_vol = float(row["total_amount"])
        elif row["_id"] == "sale":
            sale_vol = float(row["total_amount"])
    total_vol = rent_vol + sale_vol

    return ReportKPIsResponse(
        total_properties=total_props,
        available_properties=avail_props,
        rented_properties=rented_props,
        sold_properties=sold_props,
        archived_properties=arch_props,
        total_enquiries=total_enqs,
        new_enquiries_this_week=new_enqs_week,
        active_enquiries=active_enqs,
        closed_deals=closed_deals,
        aborted_deals=aborted_deals,
        today_scheduled_visits=today_visits,
        completed_visits=completed_visits,
        open_unclaimed_visits=open_unclaimed,
        today_followups=today_followups,
        upcoming_followups=upcoming_followups,
        overdue_followups=overdue_followups,
        total_deal_volume=total_vol,
        rent_deal_volume=rent_vol,
        sale_deal_volume=sale_vol,
    )


@router.get("/executive-performance", response_model=ExecutivePerformanceResponse)
async def get_executive_performance(
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    """
    PRD Section 5.18 & Phase 12:
    Executive-wise operational performance computed across users, visits, followups, tasks, and deals.
    """
    db = get_database()
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Fetch all executives and branch admins
    users_cursor = db.users.find({"role": {"$in": ["executive", "admin"]}}).sort("full_name", 1)
    staff_members = await users_cursor.to_list(100)

    items: List[ExecutivePerformanceItem] = []

    for staff in staff_members:
        u_id = str(staff["_id"])
        name = staff.get("full_name", "Staff Member")
        email = staff.get("email", "")
        role = staff.get("role", "executive")

        # 1. Enquiries assigned
        enqs_count = await db.enquiries.count_documents({
            "$or": [{"responsible_employee_id": u_id}, {"created_by": u_id}]
        })

        # 2. Visits completed
        visits_completed = await db.visits.count_documents({
            "assigned_to": u_id,
            "status": "completed",
        })

        # 3. Overdue follow-ups
        overdue_fol = await db.followups.count_documents({
            "responsible_employee": u_id,
            "scheduled_at": {"$lt": today_start},
            "status": {"$in": ["scheduled", "due", "missed"]},
        })

        # 4. Pending tasks
        pending_tasks = await db.tasks.count_documents({
            "assigned_to": u_id,
            "status": {"$in": ["pending", "acknowledged", "in_progress"]},
        })

        # 5. Closed deals & total deal volume
        deals_cursor = db.deals.find({"responsible_executive_id": u_id})
        deals_list = await deals_cursor.to_list(100)
        closed_deals_count = len(deals_list)
        total_volume = sum(float(d.get("deal_amount", 0.0)) for d in deals_list)

        # 6. Conversion rate
        conv_rate = (closed_deals_count / enqs_count * 100) if enqs_count > 0 else 0.0

        items.append(
            ExecutivePerformanceItem(
                id=u_id,
                user_id=u_id,
                name=name,
                email=email,
                role=role.capitalize(),
                assigned_enquiries=enqs_count,
                completed_visits=visits_completed,
                overdue_followups=overdue_fol,
                pending_tasks=pending_tasks,
                closed_deals=closed_deals_count,
                total_deal_volume=total_volume,
                conversion_rate=round(conv_rate, 1),
            )
        )

    # Sort descending by closed deals then completed visits
    items.sort(key=lambda x: (x.closed_deals, x.completed_visits), reverse=True)

    return ExecutivePerformanceResponse(
        items=items,
        total_executives=len(items),
    )


@router.get("/drilldown", response_model=DrilldownResponse)
async def get_report_drilldown(
    metric: str = Query(..., description="e.g. overdue_followups, closed_deals, today_scheduled_visits, etc."),
    limit: int = Query(50, ge=1, le=100),
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    """
    PRD Section 5.18:
    Drill down from any high-level KPI card to review the exact underlying records.
    """
    db = get_database()
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    week_start = now - timedelta(days=7)

    items: List[DrilldownItem] = []
    title = metric.replace("_", " ").title()

    if metric == "overdue_followups":
        docs = await db.followups.find({
            "scheduled_at": {"$lt": today_start},
            "status": {"$in": ["scheduled", "due", "missed"]},
        }).sort("scheduled_at", 1).limit(limit).to_list(limit)

        for d in docs:
            items.append(
                DrilldownItem(
                    id=d.get("followup_id", str(d["_id"])),
                    entity_type="followup",
                    title=f"Follow-up: {d.get('customer_name', 'Customer')}",
                    subtitle=f"Scheduled: {d.get('scheduled_at')} • {d.get('notes', '')[:60]}",
                    badge="OVERDUE",
                    date=str(d.get("scheduled_at", "")),
                    data={"customer_phone": d.get("customer_phone"), "enquiry_id": d.get("enquiry_id")},
                )
            )

    elif metric == "closed_deals":
        docs = await db.deals.find({}).sort("deal_date", -1).limit(limit).to_list(limit)
        for d in docs:
            items.append(
                DrilldownItem(
                    id=d.get("deal_id", str(d["_id"])),
                    entity_type="deal",
                    title=f"Deal #{d.get('deal_id')}: {d.get('customer_name')}",
                    subtitle=f"{d.get('property_id')} • Closed for ₹{d.get('deal_amount'):,.0f} by {d.get('responsible_executive_name')}",
                    badge=d.get("transaction_type", "rent").upper(),
                    date=str(d.get("deal_date", "")),
                    data={"enquiry_id": d.get("enquiry_id"), "property_id": d.get("property_id")},
                )
            )

    elif metric == "today_scheduled_visits":
        docs = await db.visits.find({
            "scheduled_at": {"$gte": today_start, "$lt": today_end},
        }).sort("scheduled_at", 1).limit(limit).to_list(limit)
        for d in docs:
            items.append(
                DrilldownItem(
                    id=d.get("visit_id", str(d["_id"])),
                    entity_type="visit",
                    title=f"Visit #{d.get('visit_id')}: {d.get('customer_name')}",
                    subtitle=f"{d.get('property_id')} ({d.get('property_society')}) • Status: {d.get('status')}",
                    badge=d.get("status", "scheduled").upper(),
                    date=str(d.get("scheduled_at", "")),
                    data={"property_id": d.get("property_id"), "enquiry_id": d.get("enquiry_id")},
                )
            )

    elif metric == "new_enquiries_this_week":
        docs = await db.enquiries.find({"created_at": {"$gte": week_start}}).sort("created_at", -1).limit(limit).to_list(limit)
        for d in docs:
            items.append(
                DrilldownItem(
                    id=d.get("enquiry_id", str(d["_id"])),
                    entity_type="enquiry",
                    title=f"Enquiry #{d.get('enquiry_id')}: {d.get('customer_name')}",
                    subtitle=f"Looking for: {d.get('looking_for')} • Budget ₹{d.get('budget_min'):,.0f}-₹{d.get('budget_max'):,.0f}",
                    badge=d.get("status", "new").upper(),
                    date=str(d.get("created_at", "")),
                    data={"customer_id": d.get("customer_id")},
                )
            )

    elif metric in ["available_properties", "rented_properties", "sold_properties"]:
        status_target = metric.replace("_properties", "")
        docs = await db.properties.find({"status": status_target}).sort("updated_at", -1).limit(limit).to_list(limit)
        for d in docs:
            items.append(
                DrilldownItem(
                    id=d.get("property_id", str(d["_id"])),
                    entity_type="property",
                    title=f"Property {d.get('property_id')} • {d.get('property_type')}",
                    subtitle=f"{d.get('society_name')} • ₹{d.get('expected_amount'):,.0f} {d.get('currency')} ({d.get('transaction_type')})",
                    badge=d.get("status", status_target).upper(),
                    date=str(d.get("created_at", "")),
                    data={"owner_phone": d.get("owner_phone"), "society_id": d.get("society_id")},
                )
            )

    return DrilldownResponse(
        metric=metric,
        title=title,
        total=len(items),
        items=items,
    )
