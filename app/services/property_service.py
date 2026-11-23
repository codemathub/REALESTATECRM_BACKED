import re
import secrets
import string
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from motor.motor_asyncio import AsyncIOMotorDatabase


def generate_property_id() -> str:
    """
    PRD REQ-PROP-01 & Section 8.1:
    Format: exactly 5 uppercase alphabetic characters + 3 numeric digits.
    Example: ABCDE123.
    Generated using cryptographically secure random generator.
    """
    letters = "".join(secrets.choice(string.ascii_uppercase) for _ in range(5))
    digits = "".join(secrets.choice(string.digits) for _ in range(3))
    return f"{letters}{digits}"


def normalize_phone(phone: Optional[str]) -> str:
    """Normalize phone numbers by stripping whitespace, hyphens, and non-digit characters."""
    if not phone:
        return ""
    digits_only = re.sub(r"\D", "", phone)
    # Strip leading 91 if 12 digits (standard Indian international format)
    if len(digits_only) == 12 and digits_only.startswith("91"):
        digits_only = digits_only[2:]
    # Strip leading 0 if 11 digits (STD/trunk prefix)
    elif len(digits_only) == 11 and digits_only.startswith("0"):
        digits_only = digits_only[1:]
    return digits_only


def validate_real_mobile(phone: Optional[str], field_name: str = "Mobile number") -> str:
    """
    Strict real mobile number validation:
    - Compulsory (cannot be empty)
    - Must be 10 digits starting with 6, 7, 8, 9 (standard Indian mobile) or 10-15 digits international
    - Rejects dummy/fake numbers (e.g. 0000000000, 1111111111, 1234567890)
    """
    if not phone or not str(phone).strip():
        raise ValueError(f"{field_name} is compulsory and cannot be empty.")

    clean_raw = str(phone).strip()
    digits = normalize_phone(clean_raw)

    if len(digits) < 10:
        raise ValueError(f"{field_name} must contain at least 10 digits (got '{clean_raw}').")

    if len(digits) > 15:
        raise ValueError(f"{field_name} cannot exceed 15 digits.")

    # Check for all repeated digits (e.g. 0000000000, 9999999999)
    if len(set(digits)) <= 2:
        raise ValueError(f"{field_name} '{clean_raw}' appears to be an invalid/dummy number.")

    # Check for sequential dummy numbers
    if digits in ("1234567890", "0123456789", "9876543210", "0987654321"):
        raise ValueError(f"{field_name} '{clean_raw}' cannot be a generic sequential placeholder.")

    # Standard 10-digit validation: in India, numbers start with 6, 7, 8, 9
    if len(digits) == 10 and digits[0] not in "6789":
        raise ValueError(f"{field_name} must begin with 6, 7, 8, or 9 for a valid 10-digit mobile number.")

    return clean_raw


async def log_activity(
    db: AsyncIOMotorDatabase,
    event_type: str,
    entity_type: str,
    entity_id: str,
    actor_id: str,
    actor_name: str,
    actor_role: str,
    details: Dict[str, Any],
) -> None:
    """Log immutable activity audit event."""
    now = datetime.now(timezone.utc)
    await db.activity_events.insert_one({
        "event_type": event_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "actor_id": actor_id,
        "actor_name": actor_name,
        "actor_role": actor_role,
        "details": details,
        "created_at": now,
    })
