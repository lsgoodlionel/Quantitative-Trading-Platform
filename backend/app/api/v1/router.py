from fastapi import APIRouter

from app.api.v1.endpoints import auth, bars, strategies, backtests, orders, positions, risk, stream

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(bars.router, prefix="/bars", tags=["Market Data"])
api_router.include_router(strategies.router, prefix="/strategies", tags=["Strategies"])
api_router.include_router(backtests.router, prefix="/backtests", tags=["Backtests"])
api_router.include_router(orders.router, prefix="/orders", tags=["Orders"])
api_router.include_router(positions.router, prefix="/positions", tags=["Positions"])
api_router.include_router(risk.router, prefix="/risk", tags=["Risk"])
api_router.include_router(stream.router, prefix="/stream", tags=["Real-time Stream"])
