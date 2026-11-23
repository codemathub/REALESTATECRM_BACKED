from motor.motor_asyncio import AsyncIOMotorDatabase


async def get_next_sequence_value(db: AsyncIOMotorDatabase, sequence_name: str) -> int:
    """Atomic counter increment using MongoDB find_one_and_update."""
    ret = await db.counters.find_one_and_update(
        {"_id": sequence_name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,
    )
    return ret["seq"]


async def generate_customer_id(db: AsyncIOMotorDatabase) -> str:
    """Generate sequential Customer ID e.g. CUS00001."""
    seq = await get_next_sequence_value(db, "customer_id")
    return f"CUS{seq:05d}"


async def generate_enquiry_id(db: AsyncIOMotorDatabase) -> str:
    """Generate sequential Enquiry ID e.g. ENQ00001."""
    seq = await get_next_sequence_value(db, "enquiry_id")
    return f"ENQ{seq:05d}"


async def generate_visit_id(db: AsyncIOMotorDatabase) -> str:
    """Generate sequential Visit ID e.g. VIS00001."""
    seq = await get_next_sequence_value(db, "visit_id")
    return f"VIS{seq:05d}"


async def generate_followup_id(db: AsyncIOMotorDatabase) -> str:
    """Generate sequential Follow-up ID e.g. FOL00001."""
    seq = await get_next_sequence_value(db, "followup_id")
    return f"FOL{seq:05d}"


async def generate_task_id(db: AsyncIOMotorDatabase) -> str:
    """Generate sequential Task ID e.g. TSK00001."""
    seq = await get_next_sequence_value(db, "task_id")
    return f"TSK{seq:05d}"


async def generate_deal_id(db: AsyncIOMotorDatabase) -> str:
    """Generate sequential Deal ID e.g. DEA00001."""
    seq = await get_next_sequence_value(db, "deal_id")
    return f"DEA{seq:05d}"


async def generate_property_id(db: AsyncIOMotorDatabase) -> str:
    """Generate sequential Property ID e.g. PROP00001."""
    seq = await get_next_sequence_value(db, "property_id")
    return f"PROP{seq:05d}"

