from aiogram import Router

from . import common
from . import broadcast
from .promo import promo_router_aggregate
from . import user_management
from . import statistics
from . import sync_admin
from . import logs_admin
from . import payments
from . import ads
from . import referral_withdrawals

admin_router_aggregate = Router(name="admin_features_router")

admin_router_aggregate.include_router(common.router)
admin_router_aggregate.include_router(broadcast.router)
admin_router_aggregate.include_router(promo_router_aggregate)
admin_router_aggregate.include_router(user_management.router)
admin_router_aggregate.include_router(statistics.router)
admin_router_aggregate.include_router(sync_admin.router)
admin_router_aggregate.include_router(logs_admin.router)
admin_router_aggregate.include_router(payments.router)
admin_router_aggregate.include_router(ads.router)
admin_router_aggregate.include_router(referral_withdrawals.router)

__all__ = ("admin_router_aggregate", )
