# Tenant Ledger API
FastAPI + SQLAlchemy multi-tenant financial ledger with SQLite and per-firm API keys.
Setup: `pip install -r requirements.txt` | Run: `uvicorn app.main:app --reload`
Tests: `pytest -v` | Interactive API: `http://127.0.0.1:8000/docs`
Create a firm first; securely save its one-time API key and send it as `X-Firm-Key`.
