from datetime import datetime, timezone
from typing import Any, Optional

def ensure_utc(dt: Any) -> Optional[datetime]:
    """
    Ensures that any datetime (naive or string from MongoDB) is explicitly
    tagged with UTC tzinfo, so that FastAPI/Pydantic serializes it with
    the trailing '+00:00' or 'Z', preventing frontend 5:30 hr timezone drift.
    """
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    if isinstance(dt, str):
        try:
            parsed = datetime.fromisoformat(dt)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return None
    return None
