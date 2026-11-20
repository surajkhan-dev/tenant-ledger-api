from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app

TEST_ENGINE = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)


@event.listens_for(TEST_ENGINE, "connect")
def enable_test_foreign_keys(connection, _record):
    cursor = connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()
TestingSessionLocal = sessionmaker(
    autocommit=False, autoflush=False, bind=TEST_ENGINE
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
    firm_response = client.post("/api/firms", params={"name": firm_name})
    assert firm_response.status_code == 200
    payload = firm_response.json()
    headers = {"X-Firm-Key": payload["api_key"]}
    client_response = client.post(
        "/api/clients",
        params={"client_ref": "CLIENT001"},
        headers=headers,
    )
    assert client_response.status_code == 200
    return headers


def transaction_data(ref="TXN001", amount="1000.00", date="2026-11-20T10:00:00"):
    return {
        "client_ref": "CLIENT001",
        "transaction_ref": ref,
        "transaction_type": "deposit",
        "amount": amount,
        "currency": "INR",
        "transaction_date": date,
    }


def create_transaction(client, headers, data=None):
    return client.post(
        "/api/transactions",
        json=data or transaction_data(),
        headers=headers,
    )


def test_create_firm_returns_one_time_api_key(client):
    response = client.post("/api/firms", params={"name": "Northbridge"})
    assert response.status_code == 200
    assert response.json()["name"] == "Northbridge"
    assert len(response.json()["api_key"]) > 30


def test_firm_api_key_is_required(client):
    response = client.get("/api/transactions")
    assert response.status_code == 401

    response = client.get(
        "/api/transactions", headers={"X-Firm-Key": "not-a-valid-key"}
    )
    assert response.status_code == 401


def test_create_transaction(client):
    headers = setup_firm_and_client(client)
    response = create_transaction(client, headers)
    assert response.status_code == 200
    assert response.json()["message"] == "Transaction created"


def test_duplicate_transaction_rejected(client):
    headers = setup_firm_and_client(client)
    assert create_transaction(client, headers).status_code == 200
    assert create_transaction(client, headers).status_code == 409


def test_negative_amount_rejected(client):
    headers = setup_firm_and_client(client)
    response = create_transaction(
        client, headers, transaction_data(amount="-500")
    )
    assert response.status_code == 422


def test_invalid_currency_rejected(client):
    headers = setup_firm_and_client(client)
    data = transaction_data()
    data["currency"] = "XYZ"
    response = create_transaction(client, headers, data)
    assert response.status_code == 422


def test_tenant_isolation(client):
    firm_a_headers = setup_firm_and_client(client, "Firm A")
    assert create_transaction(client, firm_a_headers).status_code == 200

    firm_b_headers = setup_firm_and_client(client, "Firm B")
    response = client.get("/api/transactions", headers=firm_b_headers)
    assert response.status_code == 200
    assert response.json() == []

    # Firm B cannot authenticate by guessing Firm A's numeric database ID.
    wrong_headers = {"X-Firm-Key": "1"}
    assert client.get("/api/transactions", headers=wrong_headers).status_code == 401


def test_client_balance_uses_decimal_arithmetic(client):
    headers = setup_firm_and_client(client)
    create_transaction(client, headers)

    withdrawal = transaction_data(ref="TXN002", amount="200")
    withdrawal["transaction_type"] = "withdrawal"
    assert create_transaction(client, headers, withdrawal).status_code == 200

    response = client.get(
        "/api/clients/CLIENT001/balance", headers=headers
    )
    assert response.status_code == 200
    assert Decimal(str(response.json()["balances"]["INR"])) == Decimal("800.00")


def test_historical_balance_excludes_later_transactions(client):
    headers = setup_firm_and_client(client)
    create_transaction(
        client, headers,
        transaction_data(ref="TXN-OLD", amount="100", date="2026-10-01T10:00:00"),
    )
    create_transaction(
        client, headers,
        transaction_data(ref="TXN-NEW", amount="50", date="2026-11-10T10:00:00"),
    )

    response = client.get(
        "/api/clients/CLIENT001/balance/history",
        params={"as_of": "2026-10-31T23:59:59"},
        headers=headers,
    )
    assert response.status_code == 200
    assert Decimal(str(response.json()["balances"]["INR"])) == Decimal("100.00")


def test_bulk_import_allows_partial_success(client):
    headers = setup_firm_and_client(client)
    valid = transaction_data(ref="BULK-OK", amount="25")
    invalid_client = transaction_data(ref="BULK-BAD", amount="30")
    invalid_client["client_ref"] = "MISSING"

    response = client.post(
        "/api/transactions/bulk",
        headers=headers,
        json={"transactions": [valid, invalid_client]},
    )
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert response.json()["results"][0]["status"] == "success"
    assert response.json()["results"][1]["status"] == "rejected"



def test_bulk_import_rejects_invalid_row_without_blocking_valid_row(client):
    headers = setup_firm_and_client(client)
    valid = transaction_data(ref="BULK-VALID", amount="75")
    invalid = transaction_data(ref="BULK-INVALID", amount="-10")

    response = client.post(
        "/api/transactions/bulk",
        headers=headers,
        json={"transactions": [valid, invalid]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["results"][0]["status"] == "success"
    assert body["results"][1]["status"] == "rejected"
    assert body["results"][1]["row"] == 2

    ledger = client.get("/api/transactions", headers=headers)
    assert len(ledger.json()) == 1


def test_reverse_transaction_preserves_original_and_restores_balance(client):
    headers = setup_firm_and_client(client)
    created = create_transaction(client, headers)
    original_id = created.json()["transaction_id"]

    reversal = client.post(
        f"/api/transactions/{original_id}/reverse",
        headers=headers,
        json={"reversal_ref": "TXN-REVERSAL"},
    )
    assert reversal.status_code == 200

    balance = client.get(
        "/api/clients/CLIENT001/balance", headers=headers
    )
    assert Decimal(str(balance.json()["balances"]["INR"])) == Decimal("0.00")

    transactions = client.get("/api/transactions", headers=headers).json()
    assert len(transactions) == 2
    assert transactions[0]["transaction_ref"] == "TXN001"
    assert transactions[1]["reversal_of_id"] == original_id


def test_duplicate_reversal_is_rejected(client):
    headers = setup_firm_and_client(client)
    original_id = create_transaction(client, headers).json()["transaction_id"]

    first = client.post(
        f"/api/transactions/{original_id}/reverse",
        headers=headers,
        json={"reversal_ref": "REV-1"},
    )
    second = client.post(
        f"/api/transactions/{original_id}/reverse",
        headers=headers,
        json={"reversal_ref": "REV-2"},
    )
    assert first.status_code == 200
    assert second.status_code == 409


def test_audit_log_records_transaction_and_reversal(client):
    headers = setup_firm_and_client(client)
    original_id = create_transaction(client, headers).json()["transaction_id"]
    client.post(
        f"/api/transactions/{original_id}/reverse",
        headers=headers,
        json={"reversal_ref": "AUDIT-REV"},
    )

    response = client.get("/api/audit-logs", headers=headers)
    assert response.status_code == 200
    actions = [entry["action"] for entry in response.json()]
    assert actions == ["CREATED", "REVERSED"]



def test_audit_logs_are_isolated_between_firms(client):
    firm_a_headers = setup_firm_and_client(client, "Audit Firm A")
    assert create_transaction(client, firm_a_headers).status_code == 200

    firm_b_headers = setup_firm_and_client(client, "Audit Firm B")
    response = client.get("/api/audit-logs", headers=firm_b_headers)
    assert response.status_code == 200
    assert response.json() == []


def test_historical_balance_includes_incoming_transfer(client):
    headers = setup_firm_and_client(client, "Historical Transfer Firm")
    client.post(
        "/api/clients",
        params={"client_ref": "CLIENT002"},
        headers=headers,
    )
    transfer = transaction_data(
        ref="HIST-TRANSFER",
        amount="90.25",
        date="2026-10-10T12:00:00",
    )
    transfer["transaction_type"] = "transfer"
    transfer["counterparty_client_ref"] = "CLIENT002"

    created = create_transaction(client, headers, transfer)
    assert created.status_code == 200

    response = client.get(
        "/api/clients/CLIENT002/balance/history",
        params={"as_of": "2026-10-31T23:59:59"},
        headers=headers,
    )
    assert response.status_code == 200
    assert Decimal(str(response.json()["balances"]["INR"])) == Decimal("90.25")


def test_same_transaction_reference_can_exist_in_different_firms(client):
    firm_a_headers = setup_firm_and_client(client, "Reference Firm A")
    firm_b_headers = setup_firm_and_client(client, "Reference Firm B")

    assert create_transaction(client, firm_a_headers).status_code == 200
    assert create_transaction(client, firm_b_headers).status_code == 200


def test_transfer_debits_source_and_credits_destination(client):
    headers = setup_firm_and_client(client, "Transfer Firm")
    destination = client.post(
        "/api/clients",
        params={"client_ref": "CLIENT002"},
        headers=headers,
    )
    assert destination.status_code == 200

    transfer = transaction_data(ref="TRANSFER-1", amount="125.50")
    transfer["transaction_type"] = "transfer"
    transfer["counterparty_client_ref"] = "CLIENT002"
    created = create_transaction(client, headers, transfer)
    assert created.status_code == 200
    transaction_id = created.json()["transaction_id"]

    source_balance = client.get(
        "/api/clients/CLIENT001/balance", headers=headers
    ).json()["balances"]["INR"]
    destination_balance = client.get(
        "/api/clients/CLIENT002/balance", headers=headers
    ).json()["balances"]["INR"]
    assert Decimal(str(source_balance)) == Decimal("-125.50")
    assert Decimal(str(destination_balance)) == Decimal("125.50")

    reversal = client.post(
        f"/api/transactions/{transaction_id}/reverse",
        headers=headers,
        json={"reversal_ref": "TRANSFER-REV"},
    )
    assert reversal.status_code == 200

    source_after = client.get(
        "/api/clients/CLIENT001/balance", headers=headers
    ).json()["balances"]["INR"]
    destination_after = client.get(
        "/api/clients/CLIENT002/balance", headers=headers
    ).json()["balances"]["INR"]
    assert Decimal(str(source_after)) == Decimal("0.00")
    assert Decimal(str(destination_after)) == Decimal("0.00")


def test_transfer_requires_counterparty_in_same_firm(client):
    headers = setup_firm_and_client(client, "Transfer Source Firm")
    transfer = transaction_data(ref="TRANSFER-MISSING")
    transfer["transaction_type"] = "transfer"
    transfer["counterparty_client_ref"] = "NOT-OWNED"

    response = create_transaction(client, headers, transfer)
    assert response.status_code == 404


def test_transfer_requires_counterparty_field(client):
    headers = setup_firm_and_client(client, "Transfer Validation Firm")
    transfer = transaction_data(ref="TRANSFER-NO-DEST")
    transfer["transaction_type"] = "transfer"

    response = create_transaction(client, headers, transfer)
    assert response.status_code == 422
