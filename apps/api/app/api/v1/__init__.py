from fastapi import APIRouter

from apps.api.app.api.v1.auth import router as auth_router
from apps.api.app.api.v1.resources import router as resources_router
from apps.api.app.api.v1.runs import router as runs_router

router = APIRouter(prefix="/v1")
router.include_router(auth_router)
router.include_router(resources_router)
router.include_router(runs_router)
