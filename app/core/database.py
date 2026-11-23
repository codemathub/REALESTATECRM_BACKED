import logging
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from backend.app.core.config import settings

logger = logging.getLogger(__name__)


class Database:
    client: AsyncIOMotorClient | None = None
    db: AsyncIOMotorDatabase | None = None


db_state = Database()


import logging
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from backend.app.core.config import settings
from backend.app.core.security import get_password_hash

logger = logging.getLogger(__name__)


class Database:
    client: AsyncIOMotorClient | None = None
    db: AsyncIOMotorDatabase | None = None


db_state = Database()


async def init_db_indexes(db: AsyncIOMotorDatabase):
    """Enforces server-side database indexes and initial seed records."""
    logger.info("Initializing MongoDB indexes...")

    # Users indexes
    await db.users.create_index("email_normalized", unique=True)
    await db.users.create_index([("role", 1), ("account_status", 1)])

    # Sessions indexes
    await db.sessions.create_index("refresh_token_hash", unique=True)
    await db.sessions.create_index([("user_id", 1), ("status", 1)])
    await db.sessions.create_index("last_activity_at")

    # Invitations indexes
    await db.invitations.create_index("token_hash", unique=True)
    await db.invitations.create_index([("user_id", 1), ("is_used", 1)])

    # Activity Events (Audit) indexes
    await db.activity_events.create_index([("created_at", -1)])
    await db.activity_events.create_index([("entity_type", 1), ("entity_id", 1)])
    await db.activity_events.create_index("actor_id")

    # Societies indexes
    await db.societies.create_index("society_id", unique=True)
    await db.societies.create_index("name_normalized", unique=True)

    # Master Data Options indexes
    await db.master_data_options.create_index([("category", 1), ("value_normalized", 1)], unique=True)
    await db.master_data_options.create_index([("category", 1), ("is_active", 1), ("sort_order", 1)])

    # Properties indexes
    await db.properties.create_index("property_id", unique=True)
    await db.properties.create_index([("status", 1), ("created_at", -1)])
    await db.properties.create_index("society_id")
    await db.properties.create_index("transaction_type")
    await db.properties.create_index("expected_amount")

    # Customers indexes
    await db.customers.create_index("customer_id", unique=True)
    await db.customers.create_index("primary_phone_normalized")
    await db.customers.create_index("alternate_phone_normalized")
    await db.customers.create_index("whatsapp_phone_normalized")
    await db.customers.create_index([("created_at", -1)])

    # Enquiries indexes
    await db.enquiries.create_index("enquiry_id", unique=True)
    await db.enquiries.create_index("customer_id")
    await db.enquiries.create_index([("status", 1), ("created_at", -1)])
    await db.enquiries.create_index("transaction_type")
    await db.enquiries.create_index([("budget_min", 1), ("budget_max", 1)])
    await db.enquiries.create_index("responsible_employee_id")

    # Seed Initial Authorized Super Admin if configured in environment
    if settings.INITIAL_SUPER_ADMIN_USERNAME and settings.INITIAL_SUPER_ADMIN_PASSWORD:
        sa_username = settings.INITIAL_SUPER_ADMIN_USERNAME.strip().lower()
        existing_user = await db.users.find_one({"email_normalized": sa_username})
        sa_hash = get_password_hash(settings.INITIAL_SUPER_ADMIN_PASSWORD)
        now_utc = datetime.now(timezone.utc)
        if not existing_user:
            logger.info("Initializing configured Super Admin account...")
            await db.users.insert_one({
                "full_name": "Super Admin",
                "email": settings.INITIAL_SUPER_ADMIN_USERNAME,
                "email_normalized": sa_username,
                "mobile": "+91 9999999999",
                "role": "super_admin",
                "account_status": "active",
                "password_hash": sa_hash,
                "created_by": None,
                "invited_at": now_utc,
                "activated_at": now_utc,
                "last_login_at": None,
                "created_at": now_utc,
                "updated_at": now_utc,
            })
            logger.info("Super Admin account seeded from environment.")
        else:
            await db.users.update_one(
                {"email_normalized": sa_username},
                {"$set": {
                    "password_hash": sa_hash,
                    "account_status": "active",
                    "role": "super_admin",
                    "updated_at": now_utc,
                }}
            )
            logger.info("Super Admin account updated from environment.")

    # Seed Societies from PRD Section 7.1 if empty
    societies_count = await db.societies.count_documents({})
    if societies_count == 0:
        logger.info("Seeding initial societies from baseline workbook...")
        workbook_societies = [
            "Centurial Park Terrace Homes",
            "AIMS Green Avenue",
            "Ajnara Homes",
            "RG Luxury Homes",
            "Dream Valley 2",
            "Supertech Eco village1",
            "Saviour GreenARCH",
            "AIM Green Avenue 2",
            "Galaxy vega",
            "Supertech eco village 2",
        ]
        now = datetime.now(timezone.utc)
        for idx, soc_name in enumerate(workbook_societies, start=1):
            soc_id = f"SOC{idx:04d}"
            await db.societies.insert_one({
                "society_id": soc_id,
                "name": soc_name,
                "name_normalized": soc_name.strip().lower(),
                "is_active": True,
                "created_by": None,
                "created_at": now,
                "updated_at": now,
            })
        logger.info("Seeded 10 baseline societies successfully.")

    # Seed Master Data Categories from PRD Section 7 if empty
    master_count = await db.master_data_options.count_documents({})
    if master_count == 0:
        logger.info("Seeding initial master data categories...")
        now = datetime.now(timezone.utc)
        seed_categories = {
            "property_type": [
                "1 BHK", "2 BHK", "2 BHK + 2T", "2 BHK + Study",
                "3 BHK", "3 BHK + 3T", "4 BHK", "5 BHK",
                "Villa", "Independent House", "Builder Floor", "Plot", "Commercial"
            ],
            "additional_room": [
                "Study", "Store Room", "Servant Room", "Terrace", "Balcony", "Pooja Room"
            ],
            "location": [
                "Techzone 4", "Sector 4 Greater Noida", "Noida Extension", "Greater noida west"
            ],
            "tenant_type": [
                "Family", "Bachelor", "Family + Bachelor", "Company Lease"
            ],
            "lead_source": [
                "Reference", "Website", "WhatsApp", "Walk-in", "Advertisement"
            ],
            "customer_type": [
                "Tenant", "Buyer", "Agent", "Owner"
            ],
            "maintenance_mode": [
                "Include", "Extra"
            ],
        }

        for cat, values in seed_categories.items():
            for s_idx, val in enumerate(values, start=1):
                await db.master_data_options.insert_one({
                    "category": cat,
                    "value": val,
                    "value_normalized": val.strip().lower(),
                    "is_active": True,
                    "sort_order": s_idx,
                    "metadata": {},
                    "created_by": None,
                    "created_at": now,
                    "updated_at": now,
                })
        logger.info("Seeded master data options successfully.")


async def connect_to_mongo():
    logger.info("Connecting to MongoDB Atlas...")
    db_state.client = AsyncIOMotorClient(
        settings.MONGODB_URI,
        serverSelectionTimeoutMS=5000
    )
    db_state.db = db_state.client[settings.MONGODB_DB_NAME]
    # Ping database to verify connection
    await db_state.db.command("ping")
    logger.info(f"Connected to MongoDB Atlas: {settings.MONGODB_DB_NAME}")
    await init_db_indexes(db_state.db)


async def close_mongo_connection():
    logger.info("Closing MongoDB connection...")
    if db_state.client:
        db_state.client.close()
        logger.info("MongoDB connection closed.")


def get_database() -> AsyncIOMotorDatabase:
    if db_state.db is None:
        raise RuntimeError("Database connection has not been initialized.")
    return db_state.db


async def ping_database() -> bool:
    try:
        if db_state.db is not None:
            await db_state.db.command("ping")
            return True
        return False
    except Exception as e:
        logger.error(f"MongoDB ping failed: {e}")
        return False
