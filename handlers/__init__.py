from aiogram import Router

from .menu import router as menu_router
from .shift import router as shift_router
from .application import router as application_router
from .start import router as start_router
from .location import router as location_router
from .attendance import router as attendance_router
from .admin import router as admin_router
from .middlewares import AccessMiddleware

router = Router()

# Works out the sender's access level (owner / approved / unapproved) for every handler
router.message.outer_middleware(AccessMiddleware())
router.callback_query.outer_middleware(AccessMiddleware())

# menu_router goes first: a menu button pressed while the admin is typing something
# (for example a salary) must not be taken as that input
router.include_router(menu_router)
# shift_router goes next: while the admin types a salary, no catch-all text
# handler of another router can take that message
router.include_router(shift_router)
# Must come BEFORE the employee routers: it takes /start and every other message of
# users who are not approved, so the employee handlers below only see approved users
router.include_router(application_router)
router.include_router(start_router)
router.include_router(location_router)
router.include_router(attendance_router)
router.include_router(admin_router)