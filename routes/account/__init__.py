"""Account routes — authentication, user profile, investigator dashboard."""
from .user_route import router as auth_router
from .investigator_route import router as investigator_router

__all__ = ["auth_router", "investigator_router"]
