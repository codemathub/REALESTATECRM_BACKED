import re
from typing import List, Optional
from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.app.core.deps import get_current_user
from backend.app.core.database import get_database
from backend.app.schemas.search import (
    GlobalSearchResponse,
    SearchResultItem,
    EnquiryMatchesResponse,
)
from backend.app.services.property_service import normalize_phone
from backend.app.services.matching_service import find_matching_properties, find_matching_enquiries
from backend.app.api.v1.endpoints.properties import property_doc_to_response, get_property_doc
from backend.app.api.v1.endpoints.customers import customer_doc_to_response
from backend.app.api.v1.endpoints.enquiries import enquiry_doc_to_response, get_enquiry_doc

router = APIRouter()


@router.get("", response_model=GlobalSearchResponse)
async def global_search(
    q: str = Query(..., min_length=1, description="Global search query"),
    type: str = Query("all", pattern="^(all|properties|customers|enquiries)$"),
    limit: int = Query(10, ge=1, le=50),
    current_user: dict = Depends(get_current_user),
):
    """
    PRD REQ-SRCH-01: Global search returning cross-entity results across properties, customers, and enquiries.
    """
    db = get_database()
    term = q.strip()
    escaped = re.escape(term)
    phone_norm = normalize_phone(term)

    matched_properties = []
    matched_customers = []
    matched_enquiries = []
    results: List[SearchResultItem] = []

    # 1. Search Properties
    if type in ("all", "properties"):
        prop_query_or = [
            {"property_id": {"$regex": escaped, "$options": "i"}},
            {"unit_number": {"$regex": escaped, "$options": "i"}},
            {"society_name": {"$regex": escaped, "$options": "i"}},
            {"society_id": {"$regex": escaped, "$options": "i"}},
            {"location": {"$regex": escaped, "$options": "i"}},
            {"property_type": {"$regex": escaped, "$options": "i"}},
            {"owner_name": {"$regex": escaped, "$options": "i"}},
        ]
        if phone_norm:
            prop_query_or.append({"owner_phone_normalized": {"$regex": phone_norm}})

        prop_docs = await db.properties.find({"$or": prop_query_or}).limit(limit).to_list(length=limit)
        for d in prop_docs:
            p_resp = property_doc_to_response(d)
            matched_properties.append(p_resp)
            results.append(
                SearchResultItem(
                    id=p_resp.property_id,
                    entity_type="property",
                    title=f"{p_resp.property_id} • {p_resp.property_type}",
                    subtitle=f"{p_resp.society_name}, {p_resp.location} • ₹{p_resp.expected_amount:,.0f} ({p_resp.transaction_type.upper()})",
                    badge=p_resp.status,
                    data=p_resp.model_dump(),
                )
            )

    # 2. Search Customers
    if type in ("all", "customers"):
        cust_query_or = [
            {"name": {"$regex": escaped, "$options": "i"}},
            {"customer_id": {"$regex": escaped, "$options": "i"}},
        ]
        if phone_norm:
            cust_query_or.append({"primary_phone_normalized": {"$regex": phone_norm}})

        cust_docs = await db.customers.find({"$or": cust_query_or}).limit(limit).to_list(length=limit)
        for d in cust_docs:
            cid = d["customer_id"]
            enq_count = await db.enquiries.count_documents({
                "$or": [{"customer_id": cid}, {"customer_id": str(d["_id"])}]
            })
            c_resp = customer_doc_to_response(d, enq_count)
            matched_customers.append(c_resp)
            results.append(
                SearchResultItem(
                    id=c_resp.customer_id,
                    entity_type="customer",
                    title=f"{c_resp.name} ({c_resp.customer_id})",
                    subtitle=f"Phone: {c_resp.primary_phone} • {c_resp.customer_type.capitalize()} • {enq_count} enquiries",
                    badge=c_resp.priority,
                    data=c_resp.model_dump(),
                )
            )

    # 3. Search Enquiries
    if type in ("all", "enquiries"):
        enq_query_or = [
            {"enquiry_id": {"$regex": escaped, "$options": "i"}},
            {"customer_name": {"$regex": escaped, "$options": "i"}},
            {"looking_for": {"$regex": escaped, "$options": "i"}},
            {"preferred_locations": {"$regex": escaped, "$options": "i"}},
        ]
        if phone_norm:
            enq_query_or.append({"customer_phone_normalized": {"$regex": phone_norm}})

        enq_docs = await db.enquiries.find({"$or": enq_query_or}).limit(limit).to_list(length=limit)
        for d in enq_docs:
            e_resp = enquiry_doc_to_response(d)
            matched_enquiries.append(e_resp)
            results.append(
                SearchResultItem(
                    id=e_resp.enquiry_id,
                    entity_type="enquiry",
                    title=f"{e_resp.enquiry_id} • {e_resp.customer_name}",
                    subtitle=f"Looking for: {e_resp.looking_for} • ₹{e_resp.budget_min:,.0f}-₹{e_resp.budget_max:,.0f} ({e_resp.transaction_type.upper()})",
                    badge=e_resp.status,
                    data=e_resp.model_dump(),
                )
            )

    return GlobalSearchResponse(
        query=term,
        total=len(results),
        properties=matched_properties,
        customers=matched_customers,
        enquiries=matched_enquiries,
        results=results,
    )


@router.get("/suggestions")
async def get_search_suggestions(
    q: str = Query("", min_length=0, description="Live query matching 1-3 chars/numbers or empty for recent"),
    entity: str = Query("all", pattern="^(all|property|customer|enquiry)$"),
    limit: int = Query(8, ge=1, le=30),
    current_user: dict = Depends(get_current_user),
):
    """
    Enterprise fast autocomplete endpoint designed for real-time keystroke suggestions.
    Matches across ID, name, phone, society, unit, location with no restriction on prefix.
    """
    db = get_database()
    term = q.strip()
    escaped = re.escape(term)
    phone_norm = normalize_phone(term)
    suggestions = []

    # 1. Properties
    if entity in ("all", "property"):
        if term:
            prop_query = [
                {"property_id": {"$regex": escaped, "$options": "i"}},
                {"unit_number": {"$regex": escaped, "$options": "i"}},
                {"society_name": {"$regex": escaped, "$options": "i"}},
                {"location": {"$regex": escaped, "$options": "i"}},
                {"property_type": {"$regex": escaped, "$options": "i"}},
                {"owner_name": {"$regex": escaped, "$options": "i"}},
            ]
            if phone_norm:
                prop_query.append({"owner_phone_normalized": {"$regex": phone_norm}})
            p_find = {"$or": prop_query}
        else:
            p_find = {"status": "available"}

        p_docs = await db.properties.find(p_find).sort("created_at", -1).limit(limit).to_list(length=limit)
        for p in p_docs:
            suggestions.append({
                "id": p.get("property_id", ""),
                "entity_type": "property",
                "title": f"{p.get('property_id')} • {p.get('society_name', '')}",
                "subtitle": f"{p.get('property_type', '')} • ₹{p.get('expected_amount', 0):,.0f} ({p.get('transaction_type', '').upper()})",
                "badge": p.get("status", "available").upper(),
                "extra": {
                    "property_id": p.get("property_id"),
                    "society_name": p.get("society_name"),
                    "property_type": p.get("property_type"),
                    "unit_number": p.get("unit_number"),
                    "expected_amount": p.get("expected_amount"),
                    "owner_name": p.get("owner_name"),
                    "owner_phone": p.get("owner_phone"),
                }
            })

    # 2. Enquiries
    if entity in ("all", "enquiry"):
        if term:
            enq_query = [
                {"enquiry_id": {"$regex": escaped, "$options": "i"}},
                {"customer_name": {"$regex": escaped, "$options": "i"}},
                {"looking_for": {"$regex": escaped, "$options": "i"}},
                {"preferred_locations": {"$regex": escaped, "$options": "i"}},
            ]
            if phone_norm:
                enq_query.append({"customer_phone_normalized": {"$regex": phone_norm}})
            e_find = {"$or": enq_query}
        else:
            e_find = {"status": {"$in": ["new", "in_progress", "visit_scheduled"]}}

        e_docs = await db.enquiries.find(e_find).sort("created_at", -1).limit(limit).to_list(length=limit)
        for e in e_docs:
            suggestions.append({
                "id": e.get("enquiry_id", ""),
                "entity_type": "enquiry",
                "title": f"{e.get('enquiry_id')} • {e.get('customer_name', '')}",
                "subtitle": f"Looking for: {e.get('looking_for', '')} • ₹{e.get('budget_min', 0):,.0f}-₹{e.get('budget_max', 0):,.0f} ({e.get('transaction_type', '').upper()})",
                "badge": e.get("status", "new").upper(),
                "extra": {
                    "enquiry_id": e.get("enquiry_id"),
                    "customer_name": e.get("customer_name"),
                    "customer_phone": e.get("customer_phone"),
                    "customer_id": e.get("customer_id"),
                }
            })

    # 3. Customers
    if entity in ("all", "customer"):
        if term:
            cust_query = [
                {"customer_id": {"$regex": escaped, "$options": "i"}},
                {"name": {"$regex": escaped, "$options": "i"}},
            ]
            if phone_norm:
                cust_query.append({"primary_phone_normalized": {"$regex": phone_norm}})
            c_find = {"$or": cust_query}
        else:
            c_find = {}

        c_docs = await db.customers.find(c_find).sort("created_at", -1).limit(limit).to_list(length=limit)
        for c in c_docs:
            suggestions.append({
                "id": c.get("customer_id", ""),
                "entity_type": "customer",
                "title": f"{c.get('name', '')} ({c.get('customer_id', '')})",
                "subtitle": f"{c.get('primary_phone', '')} • {c.get('customer_type', 'buyer').capitalize()}",
                "badge": c.get("priority", "medium").upper(),
                "extra": {
                    "customer_id": c.get("customer_id"),
                    "name": c.get("name"),
                    "phone": c.get("primary_phone"),
                }
            })

    return {"query": term, "total": len(suggestions), "suggestions": suggestions}


@router.get("/matches/{enquiry_id}", response_model=EnquiryMatchesResponse)
async def get_enquiry_property_matches(
    enquiry_id: str,
    limit: int = Query(20, ge=1, le=50),
    current_user: dict = Depends(get_current_user),
):
    """
    PRD Section 5.10 & REQ-MATCH-01:
    Deterministic, rule-based matching engine suggesting available properties for an enquiry.
    Advisory only; never auto-reserves or auto-messages.
    """
    db = get_database()
    enquiry = await get_enquiry_doc(db, enquiry_id)
    if not enquiry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Enquiry '{enquiry_id}' not found.",
        )

    matched_items = await find_matching_properties(db, enquiry, limit=limit)

    return EnquiryMatchesResponse(
        enquiry_id=enquiry["enquiry_id"],
        customer_name=enquiry.get("customer_name", "Unknown"),
        looking_for=enquiry.get("looking_for", ""),
        transaction_type=enquiry.get("transaction_type", "rent").upper(),
        budget_range=f"₹{enquiry.get('budget_min', 0):,.0f} - ₹{enquiry.get('budget_max', 0):,.0f}",
        match_count=len(matched_items),
        matches=matched_items,
    )


@router.get("/property-matches/{property_id}")
async def get_property_enquiry_matches(
    property_id: str,
    limit: int = Query(20, ge=1, le=50),
    current_user: dict = Depends(get_current_user),
):
    """
    Reverse matching engine: returns active prospective enquiries matching a given property.
    """
    db = get_database()
    prop = await get_property_doc(db, property_id)
    if not prop:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Property '{property_id}' not found.",
        )

    matched_items = await find_matching_enquiries(db, prop, limit=limit)
    return {
        "property_id": prop["property_id"],
        "society_name": prop.get("society_name", ""),
        "property_type": prop.get("property_type", ""),
        "expected_amount": prop.get("expected_amount", 0),
        "total_matches": len(matched_items),
        "matches": matched_items,
    }
