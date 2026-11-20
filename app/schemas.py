
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TransactionCreate(BaseModel):
    client_ref: str = Field(min_length=1, max_length=100)
    transaction_ref: str = Field(min_length=1, max_length=100)

    transaction_type: Literal[
        "deposit", "withdrawal", "fee", "transfer"
    ]

    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)

    currency: str = Field(min_length=3, max_length=3)

    transaction_date: datetime

    @field_validator("currency")
    @classmethod
    def validate_currency(cls, value):
        allowed = {"USD", "EUR", "GBP", "INR"}
        value = value.upper()

        if value not in allowed:
            raise ValueError("Unsupported currency")

        return value

    @field_validator("transaction_date")
    @classmethod
    def validate_transaction_date(cls, value):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)

        if value > datetime.now(timezone.utc):
            raise ValueError("Future dates are not allowed")

        return value


class BulkTransactionCreate(BaseModel):
    # Validate each row independently in the endpoint so one bad row
    # does not reject the entire import request.
    transactions: list[dict] = Field(min_length=1, max_length=1000)


class TransactionResponse(BaseModel):
    id: int
    firm_id: int
    client_ref: str
    transaction_ref: str
    transaction_type: str
    amount: Decimal
    currency: str
    transaction_date: datetime

    model_config = {"from_attributes": True}


class ReversalCreate(BaseModel):
    reversal_ref: str = Field(min_length=1, max_length=100)

