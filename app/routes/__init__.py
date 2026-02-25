# Copyright © 2025 Novatrax Labs LLC. All Rights Reserved.

"""
Routes package — combined router.

All sub-routers (auth, products, orders, audit) are included here and
exported as a single ``router`` symbol.  ``app/main.py`` imports only
this symbol:

    from app.routes import router
    app.include_router(router)

There is no ``app/routes.py`` file alongside this package to avoid a
Python module/package naming collision.
"""

from fastapi import APIRouter

from app.routes.auth import router as auth_router
from app.routes.audit import router as audit_router
from app.routes.orders import router as orders_router
from app.routes.products import router as products_router

router = APIRouter(prefix="/api/v1")

router.include_router(auth_router)
router.include_router(products_router)
router.include_router(orders_router)
router.include_router(audit_router)
