
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func

from app.database import get_db
from app.models import Firm, Client, Transaction, AuditLog
from app.schemas import TransactionCreate, BulkTransactionCreate


router = APIRouter(prefix="/api", tags=["Ledger"])


def get_firm(
    x_firm_id: int = Header(...),
    db: Session = Depends(get_db)
):
    firm = db.query(Firm).filter(Firm.id == x_firm_id).first()

    if not firm:
        raise HTTPException(status_code=404, detail="Firm not found")

    return firm


@router.post("/firms")
def create_firm(name: str, db: Session = Depends(get_db)):
    firm = Firm(name=name)
    db.add(firm)

    try:
        db.commit()
        db.refresh(firm)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Firm already exists")

    return {"id": firm.id, "name": firm.name}


@router.post("/clients")
def create_client(
    client_ref: str,
    firm: Firm = Depends(get_firm),
    db: Session = Depends(get_db)
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


@router.post("/transactions")
def create_transaction(
    data: TransactionCreate,
    firm: Firm = Depends(get_firm),
    db: Session = Depends(get_db)
):
    client = db.query(Client).filter(
        Client.firm_id == firm.id,
        Client.client_ref == data.client_ref
    ).first()

    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    transaction = Transaction(
        firm_id=firm.id,
        client_ref=data.client_ref,
        transaction_ref=data.transaction_ref,
        transaction_type=data.transaction_type,
        amount=data.amount,
        currency=data.currency,
        transaction_date=data.transaction_date
    )

    db.add(transaction)

    try:
        db.flush()

        db.add(AuditLog(
            firm_id=firm.id,
            transaction_ref=data.transaction_ref,
            action="CREATED",
            details=f"Amount: {data.amount} {data.currency}"
        ))

        db.commit()
        db.refresh(transaction)

    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Duplicate transaction")

    return {
        "message": "Transaction created",
        "transaction_id": transaction.id,
        "transaction_ref": transaction.transaction_ref
    }


@router.post("/transactions/bulk")
def bulk_import(
    data: BulkTransactionCreate,
    firm: Firm = Depends(get_firm),
    db: Session = Depends(get_db)
):
    results = []

    for item in data.transactions:
        client = db.query(Client).filter(
            Client.firm_id == firm.id,
            Client.client_ref == item.client_ref
        ).first()

        if not client:
            results.append({
                "transaction_ref": item.transaction_ref,
                "status": "rejected",
                "reason": "Client not found"
            })
            continue

        transaction = Transaction(
            firm_id=firm.id,
            client_ref=item.client_ref,
            transaction_ref=item.transaction_ref,
            transaction_type=item.transaction_type,
            amount=item.amount,
            currency=item.currency,
            transaction_date=item.transaction_date
        )

        try:
            db.add(transaction)
            db.flush()

            db.add(AuditLog(
                firm_id=firm.id,
                transaction_ref=item.transaction_ref,
                action="CREATED",
                details="Bulk import"
            ))

            db.commit()

            results.append({
                "transaction_ref": item.transaction_ref,
                "status": "success"
            })

        except IntegrityError:
            db.rollback()

            results.append({
                "transaction_ref": item.transaction_ref,
                "status": "rejected",
                "reason": "Duplicate transaction"
            })

    return {
        "total": len(data.transactions),
        "results": results
    }


@router.get("/transactions")
def list_transactions(
    firm: Firm = Depends(get_firm),
    db: Session = Depends(get_db)
):
    transactions = db.query(Transaction).filter(
        Transaction.firm_id == firm.id
    ).all()

    return transactions


@router.get("/clients/{client_ref}/balance")
def client_balance(
    client_ref: str,
    firm: Firm = Depends(get_firm),
    db: Session = Depends(get_db)
):
    client = db.query(Client).filter(
        Client.firm_id == firm.id,
        Client.client_ref == client_ref
    ).first()

    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    transactions = db.query(Transaction).filter(
        Transaction.firm_id == firm.id,
        Transaction.client_ref == client_ref
    ).all()

    balances = {}

    for transaction in transactions:
        currency = transaction.currency

        if currency not in balances:
            balances[currency] = 0

        if transaction.transaction_type in ("deposit", "transfer"):
            balances[currency] += transaction.amount

        elif transaction.transaction_type in ("withdrawal", "fee"):
            balances[currency] -= transaction.amount

    return {
        "client_ref": client_ref,
        "balances": balances
    }
