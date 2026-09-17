from fastapi import APIRouter

from app.api.v1 import (
    assessments,
    auth,
    catalog,
    health,
    insights,
    learning,
    product,
    profiles,
    twin,
)

v1 = APIRouter()
v1.include_router(health.router)
v1.include_router(auth.router)
v1.include_router(profiles.router)
v1.include_router(catalog.router)
v1.include_router(learning.router)
v1.include_router(assessments.router)
v1.include_router(twin.router)
v1.include_router(insights.router)
v1.include_router(product.router)
