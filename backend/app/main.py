from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.campaigns.api import router as campaigns_router
from app.core.config import settings
from app.serving.api import router as serving_router
from app.serving.auth_api import router as auth_router
from app.serving.events_api import router as events_router
from app.serving.onboarding_api import router as onboarding_router
from app.serving.performance_api import router as performance_router
from app.serving.users import router as users_router

app = FastAPI(title="Adaptive Ad Recommender", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_credentials=True,  # so the refresh-token cookie rides along on /auth/refresh
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(serving_router)
app.include_router(campaigns_router)
app.include_router(events_router)
app.include_router(onboarding_router)
app.include_router(performance_router)
app.include_router(users_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
