from fastapi import APIRouter
from backend.app.api.v1.endpoints import (
    health,
    auth,
    users,
    societies,
    master_data,
    properties,
    customers,
    enquiries,
    search,
    visits,
    followups,
    tasks,
    notifications,
    audit,
    archives,
    reports,
    excel,
    whatsapp,
)

api_v1_router = APIRouter()
api_v1_router.include_router(health.router)
api_v1_router.include_router(auth.router)
api_v1_router.include_router(users.router)
api_v1_router.include_router(societies.router)
api_v1_router.include_router(master_data.router)
api_v1_router.include_router(properties.router, prefix="/properties", tags=["properties"])
api_v1_router.include_router(customers.router, prefix="/customers", tags=["customers"])
api_v1_router.include_router(enquiries.router, prefix="/enquiries", tags=["enquiries"])
api_v1_router.include_router(search.router, prefix="/search", tags=["search"])
api_v1_router.include_router(visits.router, prefix="/visits", tags=["visits"])
api_v1_router.include_router(followups.router, prefix="/followups", tags=["followups"])
api_v1_router.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
api_v1_router.include_router(notifications.router, prefix="/notifications", tags=["notifications"])
api_v1_router.include_router(audit.router)
api_v1_router.include_router(archives.router, prefix="/archives", tags=["archives"])
api_v1_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_v1_router.include_router(excel.router, prefix="/excel", tags=["excel"])
api_v1_router.include_router(whatsapp.router, prefix="/whatsapp", tags=["whatsapp"])


