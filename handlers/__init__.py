from aiogram import Router

from .start import router as start_router
from .location import router as location_router
from .attendance import router as attendance_router
from .admin import router as admin_router

router = Router()
router.include_router(start_router)
router.include_router(location_router)
router.include_router(attendance_router)
router.include_router(admin_router)