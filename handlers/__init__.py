from aiogram import Router

from .admin import router as admin_router
from .application import router as application_router
from .attendance import router as attendance_router
from .catalog import router as catalog_router
from .groups import router as groups_router
from .interview import router as interview_router
from .location import router as location_router
from .menu import router as menu_router
from .middlewares import AccessMiddleware
from .resume import router as resume_router
from .shift import router as shift_router
from .start import router as start_router

router = Router()

# Works out the sender's access level (owner / approved / unapproved) for every handler
router.message.outer_middleware(AccessMiddleware())
router.callback_query.outer_middleware(AccessMiddleware())

# menu_router goes first: a menu button pressed while the admin is typing something
# (for example a salary) must not be taken as that input
# Group chats only (bot added / removed, renamed, migrated): never sees private messages
router.include_router(groups_router)
router.include_router(menu_router)
# shift_router goes next: while the admin types a salary, no catch-all text
# handler of another router can take that message
router.include_router(shift_router)
# Same reason as shift_router: a typed name / location of the catalog wizard
# must not reach the employee handlers
router.include_router(catalog_router)
# Must come BEFORE the employee routers: they take /start and every other message of
# users who are not approved, so the employee handlers below only see approved users.
# resume_router (the candidate's form) goes first: application_router ends with the
# catch-all handlers for unapproved users. interview_router (the admin's interview
# wizard and the candidate's "another time" request) goes before both: its typed
# answers must not reach their catch-alls
router.include_router(interview_router)
router.include_router(resume_router)
router.include_router(application_router)
router.include_router(start_router)
router.include_router(location_router)
router.include_router(attendance_router)
router.include_router(admin_router)