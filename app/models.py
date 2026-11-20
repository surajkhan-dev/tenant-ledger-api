from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from app.database import Base


class Firm(Base):
    __tablename__ = "firms"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    api_key_hash = Column(String(64), nullable=False, unique=True)


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)
    firm_id = Column(Integer, ForeignKey("firms.id"), nullable=False)
    client_ref = Column(String(100), nullable=False)

    __table_args__ = (
        UniqueConstraint("firm_id", "client_ref", name="uq_firm_client_ref"),
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    firm_id = Column(Integer, ForeignKey("firms.id"), nullable=False)
    # For transfers, client_ref is the source and counterparty_client_ref is
    # the destination. A transfer is represented by one immutable ledger row.
    client_ref = Column(String(100), nullable=False)
    counterparty_client_ref = Column(String(100), nullable=True)
    transaction_ref = Column(String(100), nullable=False)
    transaction_type = Column(String(20), nullable=False)
    amount = Column(Numeric(18, 2), nullable=False)
    currency = Column(String(3), nullable=False)
    transaction_date = Column(DateTime, nullable=False)
    reversal_of_id = Column(
        Integer, ForeignKey("transactions.id"), nullable=True, unique=True
    )
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "firm_id", "transaction_ref", name="uq_firm_transaction_ref"
        ),
    )


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    firm_id = Column(Integer, ForeignKey("firms.id"), nullable=False)
    transaction_ref = Column(String(100), nullable=False)
    action = Column(String(50), nullable=False)
    details = Column(String(500))
    created_at = Column(DateTime, server_default=func.now())
