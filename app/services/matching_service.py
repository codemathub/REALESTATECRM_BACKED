import re
from typing import List, Tuple, Dict, Any
from motor.motor_asyncio import AsyncIOMotorDatabase
from backend.app.schemas.search import PropertyMatchItem
from backend.app.api.v1.endpoints.properties import property_doc_to_response


def evaluate_property_match(enquiry: dict, prop: dict) -> Tuple[int, List[str]]:
    """
    PRD Section 5.10 Deterministic Matching Engine:
    Rule-based scoring:
    1. Transaction type: Rent == Rent, Sale == Sale (Hard requirement)
    2. Status: Must be 'available' (Hard requirement)
    3. Property type / BHK compatibility (35 pts)
    4. Budget compatibility (35 pts)
    5. Society / Location preference (30 pts)
    """
    # Hard constraint: Transaction type
    enq_tx = enquiry.get("transaction_type", "rent").lower()
    prop_tx = prop.get("transaction_type", "rent").lower()
    if enq_tx != prop_tx:
        return 0, []

    # Hard constraint: Status must be available
    if prop.get("status", "available").lower() != "available":
        return 0, []

    score = 0
    reasons: List[str] = []

    # 1. BHK / Property Type compatibility
    looking_for = enquiry.get("looking_for", "").strip().lower()
    prop_type = prop.get("property_type", "").strip().lower()

    # Extract base BHK number if present (e.g. "2 bhk" -> "2")
    enq_bhk_match = re.search(r"(\d+)\s*bhk", looking_for)
    prop_bhk_match = re.search(r"(\d+)\s*bhk", prop_type)

    if looking_for == prop_type:
        score += 35
        reasons.append("Exact BHK/Type Match")
    elif enq_bhk_match and prop_bhk_match and enq_bhk_match.group(1) == prop_bhk_match.group(1):
        score += 35
        reasons.append(f"Compatible {enq_bhk_match.group(1)} BHK Variant")
    elif looking_for in prop_type or prop_type in looking_for:
        score += 25
        reasons.append("Property Type Compatible")
    else:
        # Non-matching type
        return 0, []

    # 2. Budget compatibility
    budget_min = float(enquiry.get("budget_min", 0.0))
    budget_max = float(enquiry.get("budget_max", 0.0))
    expected_amount = float(prop.get("expected_amount", 0.0))
    negotiable = bool(prop.get("negotiable", False))

    if budget_min <= expected_amount <= budget_max:
        score += 35
        reasons.append("Within Budget Range")
    elif negotiable and expected_amount <= (budget_max * 1.10):
        score += 25
        reasons.append(f"Negotiable (within 10% of max budget)")
    elif expected_amount < budget_min:
        score += 20
        reasons.append("Below Minimum Budget (Cost Advantage)")
    else:
        # Out of budget
        return 0, []

    # 3. Society / Location preference
    pref_societies = [s.strip().lower() for s in enquiry.get("preferred_societies", []) if s.strip()]
    pref_locations = [l.strip().lower() for l in enquiry.get("preferred_locations", []) if l.strip()]

    prop_society = prop.get("society_name", "").strip().lower()
    prop_soc_id = prop.get("society_id", "").strip().lower()
    prop_location = prop.get("location", "").strip().lower()

    soc_matched = any(s == prop_society or s == prop_soc_id for s in pref_societies) if pref_societies else False
    loc_matched = any(l in prop_location or prop_location in l for l in pref_locations) if pref_locations else False

    if soc_matched:
        score += 20
        reasons.append(f"Preferred Society Match ({prop.get('society_name')})")
    elif not pref_societies:
        score += 10

    if loc_matched:
        score += 10
        reasons.append(f"Preferred Area Match ({prop.get('location')})")
    elif not pref_locations:
        score += 5

    return score, reasons


async def find_matching_properties(
    db: AsyncIOMotorDatabase,
    enquiry: dict,
    limit: int = 20,
) -> List[PropertyMatchItem]:
    """Query available properties matching an enquiry deterministically."""
    tx_type = enquiry.get("transaction_type", "rent")
    
    # Pre-filter by transaction type and available status, newest first
    cursor = db.properties.find({
        "transaction_type": tx_type,
        "status": "available",
    }).sort("created_at", -1)
    candidate_props = await cursor.to_list(length=200)

    matches: List[PropertyMatchItem] = []
    for prop in candidate_props:
        score, reasons = evaluate_property_match(enquiry, prop)
        if score >= 50:
            matches.append(
                PropertyMatchItem(
                    property=property_doc_to_response(prop),
                    match_score=score,
                    match_reasons=reasons,
                )
            )

    # Sort descending by match score, secondary by created_at
    matches.sort(key=lambda m: (m.match_score, m.property.created_at), reverse=True)
    return matches[:limit]


async def find_matching_enquiries(
    db: AsyncIOMotorDatabase,
    prop: dict,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Query active enquiries matching a property deterministically."""
    tx_type = prop.get("transaction_type", "rent")
    
    # Pre-filter by transaction type and open/active status
    cursor = db.enquiries.find({
        "transaction_type": tx_type,
        "status": {"$in": ["open", "active", "pending", "in_progress", "claimed", "followup", "site_visit_scheduled", "matched"]},
    }).sort("created_at", -1)
    candidate_enquiries = await cursor.to_list(length=200)

    matches: List[Dict[str, Any]] = []
    for enq in candidate_enquiries:
        score, reasons = evaluate_property_match(enq, prop)
        if score >= 35:
            matches.append({
                "enquiry_id": enq["enquiry_id"],
                "customer_name": enq.get("customer_name", "Prospective Tenant"),
                "customer_phone": enq.get("customer_phone", ""),
                "looking_for": enq.get("looking_for", ""),
                "budget_min": float(enq.get("budget_min", 0.0)),
                "budget_max": float(enq.get("budget_max", 0.0)),
                "status": enq.get("status", "open"),
                "priority": enq.get("priority", "medium"),
                "tenant_type": enq.get("tenant_type"),
                "match_score": score,
                "match_reasons": reasons,
                "created_at": str(enq.get("created_at", "")),
            })

    matches.sort(key=lambda m: m["match_score"], reverse=True)
    return matches[:limit]
