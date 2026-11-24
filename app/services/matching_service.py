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
    3. BHK / Property type compatibility (up to 40 pts)
    4. Budget compatibility (up to 35 pts)
    5. Society / Location preference (up to 25 pts)
    """
    # Hard constraint: Transaction type
    enq_tx = enquiry.get("transaction_type", "rent").lower()
    prop_tx = prop.get("transaction_type", "rent").lower()
    if enq_tx != prop_tx:
        return 0, []

    # Hard constraint: Status must be available (case-insensitive)
    prop_status = str(prop.get("status", "available")).lower()
    if prop_status not in ["available", "reserved"]:
        return 0, []

    score = 0
    reasons: List[str] = []

    # 1. BHK / Property Type compatibility
    looking_for = str(enquiry.get("looking_for", "")).strip().lower()
    enq_bhk_field = str(enquiry.get("bhk", "")).strip().lower()
    prop_type = str(prop.get("property_type", "")).strip().lower()
    prop_bhk_field = str(prop.get("bhk", "")).strip().lower()

    # Extract digits for BHK (e.g. "2 BHK" -> "2", "3bhk" -> "3")
    enq_bhk_match = re.search(r"(\d+)\s*(?:bhk|bedroom)?", f"{looking_for} {enq_bhk_field}")
    prop_bhk_match = re.search(r"(\d+)\s*(?:bhk|bedroom)?", f"{prop_type} {prop_bhk_field}")

    if enq_bhk_match and prop_bhk_match and enq_bhk_match.group(1) == prop_bhk_match.group(1):
        score += 40
        reasons.append(f"Exact {enq_bhk_match.group(1)} BHK Match")
    elif looking_for and (looking_for in prop_type or prop_type in looking_for):
        score += 35
        reasons.append("Compatible Property Type")
    elif not enq_bhk_match and not prop_bhk_match:
        score += 25
        reasons.append("Standard Property Match")
    else:
        # Mismatched BHK (e.g. wants 2 BHK, property is 4 BHK)
        # Check if adjacent (e.g., 2 vs 3 BHK)
        if enq_bhk_match and prop_bhk_match:
            diff = abs(int(enq_bhk_match.group(1)) - int(prop_bhk_match.group(1)))
            if diff == 1:
                score += 15
                reasons.append(f"Near Variant ({prop_bhk_match.group(1)} BHK)")
            else:
                return 0, []
        else:
            score += 15

    # 2. Budget compatibility
    try:
        budget_min = float(enquiry.get("budget_min") or 0.0)
    except (ValueError, TypeError):
        budget_min = 0.0

    try:
        budget_max = float(enquiry.get("budget_max") or 0.0)
    except (ValueError, TypeError):
        budget_max = 0.0

    try:
        expected_amount = float(prop.get("expected_amount") or 0.0)
    except (ValueError, TypeError):
        expected_amount = 0.0

    negotiable = bool(prop.get("negotiable", False))

    if budget_max > 0:
        if budget_min <= expected_amount <= budget_max:
            score += 35
            reasons.append("Within Target Budget")
        elif expected_amount < budget_min:
            score += 25
            reasons.append("Cost Advantage (Below Min Budget)")
        elif negotiable and expected_amount <= (budget_max * 1.15):
            score += 25
            reasons.append("Negotiable Price (<15% variance)")
        elif expected_amount <= (budget_max * 1.25):
            score += 15
            reasons.append("Slightly Above Budget Range")
        elif expected_amount > (budget_max * 1.5):
            # Far beyond budget
            return 0, []
    else:
        score += 25
        reasons.append("Open Budget Range")

    # 3. Society / Location preference
    pref_societies = [s.strip().lower() for s in enquiry.get("preferred_societies", []) if s and str(s).strip()]
    pref_locations = [l.strip().lower() for l in enquiry.get("preferred_locations", []) if l and str(l).strip()]

    prop_society = str(prop.get("society_name", "")).strip().lower()
    prop_soc_id = str(prop.get("society_id", "")).strip().lower()
    prop_location = str(prop.get("location", "")).strip().lower()

    soc_matched = any(s in prop_society or prop_society in s or s == prop_soc_id for s in pref_societies) if pref_societies else False
    loc_matched = any(l in prop_location or prop_location in l for l in pref_locations) if pref_locations else False

    if soc_matched:
        score += 25
        reasons.append(f"Preferred Society Match ({prop.get('society_name')})")
    elif loc_matched:
        score += 20
        reasons.append(f"Preferred Area Match ({prop.get('location')})")
    elif not pref_societies and not pref_locations:
        score += 15
        reasons.append("Open Location Preference")
    else:
        score += 10
        reasons.append(f"Prime Area ({prop.get('location', 'Local Hub')})")

    return score, reasons


async def find_matching_properties(
    db: AsyncIOMotorDatabase,
    enquiry: dict,
    limit: int = 20,
) -> List[PropertyMatchItem]:
    """Query available properties matching an enquiry deterministically."""
    tx_type = enquiry.get("transaction_type", "rent")
    
    # Pre-filter by transaction type (case-insensitive regex) and status
    cursor = db.properties.find({
        "transaction_type": {"$regex": f"^{tx_type}$", "$options": "i"},
        "status": {"$in": ["available", "Available", "reserved", "Reserved"]},
    }).sort("created_at", -1)
    candidate_props = await cursor.to_list(length=200)

    matches: List[PropertyMatchItem] = []
    for prop in candidate_props:
        score, reasons = evaluate_property_match(enquiry, prop)
        if score >= 35:
            matches.append(
                PropertyMatchItem(
                    property=property_doc_to_response(prop),
                    match_score=min(100, score),
                    match_reasons=reasons,
                )
            )

    # Sort descending by match score, secondary by created_at
    matches.sort(key=lambda m: (m.match_score, str(m.property.created_at)), reverse=True)
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
