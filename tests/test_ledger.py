
import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import Base, get_db


# Separate in-memory database for tests
TEST_ENGINE = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)

TestingSessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=TEST_ENGINE
)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def client():
    Base.metadata.create_all(bind=TEST_ENGINE)
    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=TEST_ENGINE)


def setup_firm_and_client(client, firm_name="Test Firm"):
    firm_response = client.post(
        "/api/firms",
        params={"name": firm_name}
    )

    firm_id = firm_response.json()["id"]

    headers = {"X-Firm-ID": str(firm_id)}

    client.post(
        "/api/clients",
        params={"client_ref": "CLIENT001"},
        headers=headers
    )

    return headers


def transaction_data(ref="TXN001", amount="1000.00"):
    return {
        "client_ref": "CLIENT001",
        "transaction_ref": ref,
        "transaction_type": "deposit",
        "amount": amount,
        "currency": "INR",
        "transaction_date": "2026-11-20T10:00:00"
    }


def test_create_firm(client):
    response = client.post(
        "/api/firms",
        params={"name": "Northbridge"}
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Northbridge"


def test_create_transaction(client):
    headers = setup_firm_and_client(client)

    response = client.post(
        "/api/transactions",
        json=transaction_data(),
        headers=headers
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Transaction created"


def test_duplicate_transaction_rejected(client):
    headers = setup_firm_and_client(client)

    client.post(
        "/api/transactions",
        json=transaction_data(),
        headers=headers
    )

    response = client.post(
        "/api/transactions",
        json=transaction_data(),
        headers=headers
    )

    assert response.status_code == 409


def test_negative_amount_rejected(client):
    headers = setup_firm_and_client(client)

    response = client.post(
        "/api/transactions",
        json=transaction_data(amount="-500"),
        headers=headers
    )

    assert response.status_code == 422


def test_invalid_currency_rejected(client):
    headers = setup_firm_and_client(client)

    data = transaction_data()
    data["currency"] = "XYZ"

    response = client.post(
        "/api/transactions",
        json=data,
        headers=headers
    )

    assert response.status_code == 422


def test_tenant_isolation(client):
    firm_a_headers = setup_firm_and_client(
        client, "Firm A"
    )

    client.post(
        "/api/transactions",
        json=transaction_data(),
        headers=firm_a_headers
    )

    firm_b_headers = setup_firm_and_client(
        client, "Firm B"
    )

    response = client.get(
        "/api/transactions",
        headers=firm_b_headers
    )

    assert response.status_code == 200
    assert response.json() == []


def test_client_balance(client):
    headers = setup_firm_and_client(client)

    client.post(
        "/api/transactions",
        json=transaction_data(amount="1000"),
        headers=headers
    )

    withdrawal = transaction_data(
        ref="TXN002",
        amount="200"
    )
    withdrawal["transaction_type"] = "withdrawal"

    client.post(
        "/api/transactions",
        json=withdrawal,
        headers=headers
    )

    response = client.get(
        "/api/clients/CLIENT001/balance",
        headers=headers
    )

    assert response.status_code == 200
    assert float(response.json()["balances"]["INR"]) == 800
