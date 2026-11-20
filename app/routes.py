import hashlib
import secrets
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import ValidationError

from app.database import get_db
from app.models import AuditLog, Client, Firm, Transaction
from app.schemas import (
    BulkTransactionCreate,
    ReversalCreate,
    TransactionCreate,
)

router = APIRouter(prefix="/api", tags=["Ledger"])


def to_utc_naive(value: datetime) -> datetime:
    """Store all timestamps as UTC-naive values for SQLite consistency."""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return value.replace(tzinfo=None)


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def get_firm(
    x_firm_key: str | None = Header(default=None, alias="X-Firm-Key"),
    db: Session = Depends(get_db),
):
    if not x_firm_key:
        raise HTTPException(status_code=401, detail="X-Firm-Key header is required")

    firm = db.query(Firm).filter(
        Firm.api_key_hash == hash_api_key(x_firm_key)
    ).first()

    if not firm:
        raise HTTPException(status_code=401, detail="Invalid firm API key")

    return firm


@router.post("/firms")
def create_firm(name: str, db: Session = Depends(get_db)):
    api_key = secrets.token_urlsafe(32)
    firm = Firm(name=name, api_key_hash=hash_api_key(api_key))
    db.add(firm)

    try:
        db.commit()
        db.refresh(firm)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Firm already exists")

    # The plaintext key is returned only once; only its hash is stored.
    return {"id": firm.id, "name": firm.name, "api_key": api_key}


@router.post("/clients")
def create_client(
    client_ref: str,
    firm: Firm = Depends(get_firm),
    db: Session = Depends(get_db),
):
    client = Client(firm_id=firm.id, client_ref=client_ref)
    db.add(client)

    try:
        db.commit()
        db.refresh(client)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Client already exists")

    return {"id": client.id, "client_ref": client.client_ref}


def ensure_client(db: Session, firm_id: int, client_ref: str):
    client = db.query(Client).filter(
        Client.firm_id == firm_id,
        Client.client_ref == client_ref,
    ).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


def write_audit(
    db: Session, firm_id: int, transaction_ref: str,
    action: str, details: str,
):
    db.add(AuditLog(
        firm_id=firm_id,
        transaction_ref=transaction_ref,
        action=action,
        details=details,
    ))


@router.post("/transactions")
def create_transaction(
    data: TransactionCreate,
    firm: Firm = Depends(get_firm),
    db: Session = Depends(get_db),
):
    ensure_client(db, firm.id, data.client_ref)
    if data.transaction_type == "transfer":
        ensure_client(db, firm.id, data.counterparty_client_ref)
    transaction = Transaction(
        firm_id=firm.id,
        client_ref=data.client_ref,
        counterparty_client_ref=data.counterparty_client_ref,
        transaction_ref=data.transaction_ref,
        transaction_type=data.transaction_type,
        amount=data.amount,
        currency=data.currency,
        transaction_date=to_utc_naive(data.transaction_date),
    )
    db.add(transaction)

    try:
        db.flush()
        write_audit(
            db, firm.id, data.transaction_ref, "CREATED",
            f"Amount: {data.amount} {data.currency}",
        )
        db.commit()
        db.refresh(transaction)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Duplicate transaction")

    return {
        "message": "Transaction created",
        "transaction_id": transaction.id,
        "transaction_ref": transaction.transaction_ref,
    }


@router.post("/transactions/bulk")
def bulk_import(
    data: BulkTransactionCreate,
    firm: Firm = Depends(get_firm),
    db: Session = Depends(get_db),
):
    results = []

    for row_number, raw_item in enumerate(data.transactions, start=1):
        try:
            item = TransactionCreate.model_validate(raw_item)
        except ValidationError as exc:
            results.append({
                "row": row_number,
                "transaction_ref": raw_item.get("transaction_ref") if isinstance(raw_item, dict) else None,
                "status": "rejected",
                "reason": "; ".join(
                    ".".join(str(part) for part in error["loc"]) + ": " + error["msg"]
                    for error in exc.errors(include_url=False)
                ),
            })
            continue

        client = db.query(Client).filter(
            Client.firm_id == firm.id,
            Client.client_ref == item.client_ref,
        ).first()
        counterparty_exists = True
        if item.transaction_type == "transfer":
            counterparty_exists = db.query(Client.id).filter(
                Client.firm_id == firm.id,
                Client.client_ref == item.counterparty_client_ref,
            ).first() is not None
        if not client or not counterparty_exists:
            results.append({
                "row": row_number,
                "transaction_ref": item.transaction_ref,
                "status": "rejected",
                "reason": "Client or transfer counterparty not found",
            })
            continue

        transaction = Transaction(
            firm_id=firm.id,
            client_ref=item.client_ref,
            counterparty_client_ref=item.counterparty_client_ref,
            transaction_ref=item.transaction_ref,
            transaction_type=item.transaction_type,
            amount=item.amount,
            currency=item.currency,
            transaction_date=to_utc_naive(item.transaction_date),
        )

        try:
            db.add(transaction)
            db.flush()
            write_audit(
                db, firm.id, item.transaction_ref, "CREATED", "Bulk import"
            )
            db.commit()
            results.append({
                "row": row_number,
                "transaction_ref": item.transaction_ref,
                "status": "success",
            })
        except IntegrityError:
            db.rollback()
            results.append({
                "row": row_number,
                "transaction_ref": item.transaction_ref,
                "status": "rejected",
                "reason": "Duplicate transaction",
            })

    return {"total": len(data.transactions), "results": results}


@router.get("/transactions")
def list_transactions(
    firm: Firm = Depends(get_firm),
    db: Session = Depends(get_db),
):
    return db.query(Transaction).filter(
        Transaction.firm_id == firm.id
    ).order_by(Transaction.transaction_date, Transaction.id).all()


def calculate_balances(transactions, client_ref: str):
    balances = {}
    for transaction in transactions:
        currency = transaction.currency
        balances.setdefault(currency, Decimal("0.00"))

        if transaction.transaction_type == "deposit":
            if transaction.client_ref == client_ref:
                balances[currency] += transaction.amount
        elif transaction.transaction_type in ("withdrawal", "fee"):
            if transaction.client_ref == client_ref:
                balances[currency] -= transaction.amount
        elif transaction.transaction_type == "transfer":
            if transaction.client_ref == client_ref:
                balances[currency] -= transaction.amount
            if transaction.counterparty_client_ref == client_ref:
                balances[currency] += transaction.amount

    return balances


@router.get("/clients/{client_ref}/balance")
def client_balance(
    client_ref: str,
    firm: Firm = Depends(get_firm),
    db: Session = Depends(get_db),
):
    ensure_client(db, firm.id, client_ref)
    transactions = db.query(Transaction).filter(
        Transaction.firm_id == firm.id,
        or_(
            Transaction.client_ref == client_ref,
            Transaction.counterparty_client_ref == client_ref,
        ),
    ).all()
    return {"client_ref": client_ref, "balances": calculate_balances(transactions, client_ref)}


@router.get("/clients/{client_ref}/balance/history")
def historical_balance(
    client_ref: str,
    as_of: datetime,
    firm: Firm = Depends(get_firm),
    db: Session = Depends(get_db),
):
    ensure_client(db, firm.id, client_ref)
    # SQLite stores timestamps without timezone; normalize the query to UTC-naive.
    if as_of.tzinfo is not None:
        as_of = as_of.astimezone(timezone.utc).replace(tzinfo=None)

    transactions = db.query(Transaction).filter(
        Transaction.firm_id == firm.id,
        or_(
            Transaction.client_ref == client_ref,
            Transaction.counterparty_client_ref == client_ref,
        ),
        Transaction.transaction_date <= as_of,
    ).all()
    return {
        "client_ref": client_ref,
        "as_of": as_of,
        "balances": calculate_balances(transactions, client_ref),
    }


@router.post("/transactions/{transaction_id}/reverse")
def reverse_transaction(
    transaction_id: int,
    data: ReversalCreate,
    firm: Firm = Depends(get_firm),
    db: Session = Depends(get_db),
):
    original = db.query(Transaction).filter(
        Transaction.id == transaction_id,
        Transaction.firm_id == firm.id,
    ).first()
    if not original:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if original.reversal_of_id is not None:
        raise HTTPException(status_code=400, detail="A reversal cannot be reversed")
    if db.query(Transaction).filter(
        Transaction.reversal_of_id == original.id
    ).first():
        raise HTTPException(status_code=409, detail="Transaction already reversed")

    opposite_types = {
        "deposit": "withdrawal",
        "withdrawal": "deposit",
        "fee": "deposit",
        "transfer": "transfer",
    }
    reversal = Transaction(
        firm_id=firm.id,
        client_ref=(
            original.counterparty_client_ref
            if original.transaction_type == "transfer"
            else original.client_ref
        ),
        counterparty_client_ref=(
            original.client_ref if original.transaction_type == "transfer" else None
        ),
        transaction_ref=data.reversal_ref,
        transaction_type=opposite_types[original.transaction_type],
        amount=original.amount,
        currency=original.currency,
        transaction_date=datetime.now(timezone.utc).replace(tzinfo=None),
        reversal_of_id=original.id,
    )
    db.add(reversal)

    try:
        db.flush()
        write_audit(
            db, firm.id, data.reversal_ref, "REVERSED",
            f"Reversal of transaction {original.transaction_ref}",
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Duplicate reversal reference or transaction already reversed",
        )

    return {
        "message": "Transaction reversed successfully",
        "original_transaction": original.transaction_ref,
        "reversal_transaction": reversal.transaction_ref,
    }


@router.get("/audit-logs")
def list_audit_logs(
    firm: Firm = Depends(get_firm),
    db: Session = Depends(get_db),
):
    return db.query(AuditLog).filter(
        AuditLog.firm_id == firm.id
    ).order_by(AuditLog.id).all()
