import re
from typing import List, Optional, Any
from datetime import datetime, timezone
from bson import ObjectId
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from backend.app.core.database import get_database
from backend.app.core.deps import require_roles

router = APIRouter(prefix="/audit", tags=["Audit Log"])

ACTION_LABELS = {
    "user.deleted": "User Deleted",
    "user.reactivated": "User Reactivated",
    "user.deactivated": "User Deactivated",
    "user.created": "User Account Created",
    "user.updated": "User Updated",
    "auth.login_success": "User Logged In",
    "auth.login_override": "Emergency Super Admin Override",
    "auth.password_reset": "Password Reset Issued",
    "auth.token_revoke": "Global Sessions Purged",
    "auth.failed": "Failed Login Attempt",
    "property.created": "Property Listed",
    "property.updated": "Property Details Updated",
    "property.archived": "Property Archived",
    "property.unarchived": "Property Restored",
    "property.deleted": "Property Permanently Deleted",
    "customer.created": "Client Profile Registered",
    "customer.updated": "Client Details Updated",
    "enquiry.created": "New Enquiry Logged",
    "enquiry.status_updated": "Enquiry Status Changed",
    "visit.scheduled": "Site Visit Scheduled",
    "visit.completed": "Site Visit Completed",
    "visit.claimed": "Site Visit Claimed",
    "visit.cancelled": "Site Visit Cancelled",
    "directive.created": "Directive Dispatched",
    "directive.completed": "Directive Completed",
    "directive.cancelled": "Directive Cancelled",
}


def build_action_label(event_type: str) -> str:
    if event_type in ACTION_LABELS:
        return ACTION_LABELS[event_type]
    return event_type.replace(".", " ").replace("_", " ").title()


def build_activity_summary(
    event_type: str,
    action_label: str,
    actor_name: str,
    entity_name: str,
    entity_type: str,
    entity_id: str,
    diff: Any,
) -> str:
    target = entity_name or f"{entity_type} #{entity_id}"
    diff_dict = diff if isinstance(diff, dict) else {}

    if event_type == "user.deleted":
        return f"{actor_name} permanently deleted user account ({target})"
    elif event_type == "user.reactivated":
        return f"{actor_name} reactivated user account ({target})"
    elif event_type == "user.deactivated":
        return f"{actor_name} deactivated user account ({target})"
    elif event_type == "user.created":
        role_label = diff_dict.get("role", "staff").replace("_", " ").title()
        return f"{actor_name} created new {role_label} account for {target}"
    elif event_type == "auth.password_reset":
        return f"{actor_name} approved and issued temporary password for {target}"
    elif event_type == "auth.login_override":
        return f"{actor_name} used Super Admin credentials override"
    elif event_type == "auth.login_success":
        return f"{actor_name} logged in successfully"
    elif event_type == "property.created":
        return f"{actor_name} added property {target} to active inventory"
    elif event_type == "property.updated":
        fields = diff_dict.get("updated_fields", [])
        field_str = f" ({', '.join(fields)})" if fields else ""
        return f"{actor_name} updated property {target}{field_str}"
    elif event_type == "property.archived":
        return f"{actor_name} archived property {target}"
    elif event_type == "property.unarchived":
        return f"{actor_name} restored property {target} to active inventory"
    elif event_type == "property.deleted":
        return f"{actor_name} permanently deleted property {target}"
    elif event_type == "customer.created":
        return f"{actor_name} registered new customer profile {target}"
    elif event_type == "enquiry.created":
        return f"{actor_name} logged client enquiry for {target}"
    elif event_type == "visit.scheduled":
        return f"{actor_name} scheduled site visit for {target}"
    elif event_type == "visit.completed":
        return f"{actor_name} marked site visit for {target} as completed"
    elif event_type == "visit.claimed":
        return f"{actor_name} claimed site visit for {target}"
    elif event_type == "directive.created":
        return f"{actor_name} dispatched operational directive for {target}"
    return f"{actor_name} performed {action_label} on {target}"


class AuditEventResponse(BaseModel):
    id: str
    timestamp: datetime
    actor_id: Optional[str] = None
    actor_name: str
    actor_email: Optional[str] = None
    actor_role: str
    event_type: str
    action_label: Optional[str] = None
    entity_type: str
    entity_id: str
    entity_name: Optional[str] = None
    summary: Optional[str] = None
    diff: Optional[Any] = None
    ip_address: Optional[str] = None
    status: str


class AuditLogListResponse(BaseModel):
    items: List[AuditEventResponse]
    total: int


@router.get("", response_model=AuditLogListResponse, summary="Query append-only audit trail (Super Admin / Admin only)")
async def get_audit_logs(
    search: Optional[str] = None,
    event_type: Optional[str] = None,
    entity_type: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    skip: int = Query(default=0, ge=0),
    current_user: dict = Depends(require_roles(["super_admin", "admin"])),
):
    db = get_database()
    query = {}

    if event_type:
        if event_type.endswith("."):
            query["event_type"] = {"$regex": f"^{re.escape(event_type)}"}
        else:
            query["event_type"] = event_type

    if entity_type:
        query["entity_type"] = entity_type

    if search and search.strip():
        s = re.escape(search.strip())
        query["$or"] = [
            {"event_type": {"$regex": s, "$options": "i"}},
            {"entity_id": {"$regex": s, "$options": "i"}},
            {"actor_name": {"$regex": s, "$options": "i"}},
            {"actor_role": {"$regex": s, "$options": "i"}},
            {"ip_address": {"$regex": s, "$options": "i"}},
        ]

    total = await db.activity_events.count_documents(query)
    cursor = db.activity_events.find(query).sort("created_at", -1).skip(skip).limit(limit)
    raw_events = await cursor.to_list(length=limit)

    # Collect actor and target user ObjectIds for enrichment
    actor_ids = []
    target_user_ids = []
    target_property_ids = []

    for e in raw_events:
        aid = e.get("actor_id")
        if aid and isinstance(aid, ObjectId):
            actor_ids.append(aid)
        elif aid:
            try:
                actor_ids.append(ObjectId(str(aid)))
            except Exception:
                pass

        ent_type = e.get("entity_type")
        ent_id = e.get("entity_id")
        if ent_type == "user" and ent_id:
            if isinstance(ent_id, ObjectId):
                target_user_ids.append(ent_id)
            else:
                try:
                    target_user_ids.append(ObjectId(str(ent_id)))
                except Exception:
                    pass
        elif ent_type == "property" and ent_id:
            target_property_ids.append(str(ent_id).upper())

    user_map = {}
    all_user_ids = list(set(actor_ids + target_user_ids))
    if all_user_ids:
        users = await db.users.find({"_id": {"$in": all_user_ids}}).to_list(length=len(all_user_ids))
        for u in users:
            user_map[str(u["_id"])] = {
                "name": u.get("full_name") or u.get("email", "System User"),
                "email": u.get("email"),
                "role": u.get("role", "executive"),
            }

    prop_map = {}
    if target_property_ids:
        props = await db.properties.find({"property_id": {"$in": target_property_ids}}).to_list(length=len(target_property_ids))
        for p in props:
            prop_map[p["property_id"]] = {
                "society_name": p.get("society_name", ""),
                "property_type": p.get("property_type", ""),
            }

    items = []
    for e in raw_events:
        a_id = str(e["actor_id"]) if e.get("actor_id") else None
        user_info = user_map.get(a_id, {})
        actor_name = e.get("actor_name") or user_info.get("name")
        if not actor_name:
            role = e.get("actor_role", "system")
            if role == "super_admin":
                actor_name = "Super Admin (System)"
            elif role == "system":
                actor_name = "System Automated Engine"
            else:
                actor_name = role.replace("_", " ").title()

        ent_type = e.get("entity_type", "record")
        ent_id = str(e.get("entity_id", "—"))
        diff_payload = e.get("diff") or e.get("details")

        # Resolve Human-Friendly Entity Name
        entity_name = None
        if ent_type == "user":
            u_info = user_map.get(ent_id)
            if u_info:
                u_role = (u_info.get("role") or "").replace("_", " ").title()
                entity_name = f"{u_info['name']} • {u_info['email']}"
            elif isinstance(diff_payload, dict) and diff_payload.get("email"):
                d_role = diff_payload.get("role", "").replace("_", " ").title()
                d_email = diff_payload.get("email")
                d_name = diff_payload.get("name")
                if d_name:
                    entity_name = f"{d_name} • {d_email}"
                else:
                    entity_name = f"{d_email} ({d_role})" if d_role else d_email
            else:
                entity_name = f"User Account #{ent_id[:8]}..."
        elif ent_type == "property":
            pid = ent_id.upper()
            p_info = prop_map.get(pid)
            if p_info and p_info.get("society_name"):
                entity_name = f"{pid} ({p_info['society_name']})"
            else:
                entity_name = f"Property #{pid}"
        elif ent_type == "customer":
            if isinstance(diff_payload, dict) and diff_payload.get("customer_name"):
                entity_name = f"{diff_payload['customer_name']}"
            elif isinstance(diff_payload, dict) and diff_payload.get("name"):
                entity_name = diff_payload["name"]
            else:
                entity_name = f"Customer #{ent_id}"
        elif ent_type == "visit":
            entity_name = f"Site Visit #{ent_id}"
        else:
            entity_name = f"{ent_type.title()} #{ent_id}"

        ev_type = e.get("event_type", "action")
        action_label = build_action_label(ev_type)
        summary = build_activity_summary(
            event_type=ev_type,
            action_label=action_label,
            actor_name=actor_name,
            entity_name=entity_name,
            entity_type=ent_type,
            entity_id=ent_id,
            diff=diff_payload,
        )

        items.append(
            AuditEventResponse(
                id=str(e["_id"]),
                timestamp=e.get("created_at") or datetime.now(timezone.utc),
                actor_id=a_id,
                actor_name=actor_name,
                actor_email=user_info.get("email"),
                actor_role=e.get("actor_role", "system"),
                event_type=ev_type,
                action_label=action_label,
                entity_type=ent_type,
                entity_id=ent_id,
                entity_name=entity_name,
                summary=summary,
                diff=diff_payload,
                ip_address=e.get("ip_address") or "127.0.0.1",
                status=e.get("status", "success"),
            )
        )

    return AuditLogListResponse(items=items, total=total)
