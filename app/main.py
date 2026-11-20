
from fastapi import FastAPI
from app.database import engine, Base
from app import models
from app.routes import router

# Create database tables
Base.metadata.create_all(bind=engine)

# Initialize FastAPI
app = FastAPI(
    title="Tenant Ledger API",
    description="Multi-tenant financial transaction management system",
    version="1.0.0"
)

# Register API routes
app.include_router(router)


@app.get("/")
def home():
    return {
        "message": "Tenant Ledger API is running",
        "status": "success"
    }


@app.get("/health")
def health_check():
    return {
        "status": "healthy"
    }
