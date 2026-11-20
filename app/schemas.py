from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class TransactionCreate(BaseModel):
    client_ref: str = Field(min_length=1, max_length=100)
    counterparty_client_ref: str | None = Field(default=None, min_length=1, max_length=100)
    transaction_ref: str = Field(min_length=1, max_length=100)
    transaction_type: Literal["deposit", "withdrawal", "fee", "transfer"]
    amount: Decimal = Field(gt=0, max_digits=18, decimal_places=2)
    currency: str = Field(min_length=3, max_length=3)
    transaction_date: datetime

    @field_validator("client_ref", "transaction_ref", "counterparty_client_ref")
    @classmethod
    def normalize_references(cls, value):
        if value is None:
            return value
        value = value.strip()
        if not value:
            raise ValueError("Reference cannot be blank")
        return value

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

    @model_validator(mode="after")
    def validate_transfer_accounts(self):
        if self.transaction_type == "transfer":
            if not self.counterparty_client_ref:
                raise ValueError("counterparty_client_ref is required for transfers")
            if self.counterparty_client_ref == self.client_ref:
                raise ValueError("A client cannot transfer to itself")
        elif self.counterparty_client_ref is not None:
            raise ValueError("counterparty_client_ref is only valid for transfers")
        return self


class BulkTransactionCreate(BaseModel):
    # Rows are validated individually to preserve partial-success imports.
    transactions: list[Any] = Field(min_length=1, max_length=1000)


class TransactionResponse(BaseModel):
    id: int
    firm_id: int
    client_ref: str
    counterparty_client_ref: str | None
    transaction_ref: str
    transaction_type: str
    amount: Decimal
    currency: str
    transaction_date: datetime
    reversal_of_id: int | None

    model_config = {"from_attributes": True}


class ReversalCreate(BaseModel):
    reversal_ref: str = Field(min_length=1, max_length=100)

    @field_validator("reversal_ref")
    @classmethod
    def normalize_reversal_ref(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Reversal reference cannot be blank")
        return value
