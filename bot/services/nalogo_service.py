import asyncio
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

from nalogo import Client
from nalogo.dto.income import (
    AtomDateTime,
    IncomeClient,
    IncomeRequest,
    IncomeServiceItem,
    PaymentType,
)


class NalogoService:
    def __init__(self, inn: Optional[str], password: Optional[str]) -> None:
        self.inn = inn.strip() if inn else None
        self.password = password
        self.configured = bool(self.inn and self.password)
        self._client = Client() if self.configured else None
        self._auth_lock = asyncio.Lock()

        if not self.configured:
            logging.warning("Nalogo credentials are missing. Receipt sending disabled.")

    async def _ensure_authenticated(self) -> bool:
        if not self._client:
            return False

        async with self._auth_lock:
            token_data = await self._client.auth_provider.get_token()
            if token_data:
                return True

            try:
                token_json = await self._client.create_new_access_token(
                    self.inn,
                    self.password,
                )
                await self._client.authenticate(token_json)
                logging.info("Nalogo authentication succeeded.")
                return True
            except Exception:
                logging.exception("Nalogo authentication failed.")
                return False

    async def create_income_receipt(
        self,
        *,
        item_name: str,
        amount: float,
        quantity: float = 1.0,
        client: Optional[IncomeClient] = None,
        operation_time: Optional[datetime] = None,
    ) -> Optional[str]:
        if not self.configured:
            return None
        if not await self._ensure_authenticated():
            return None

        try:
            # API expects quantity as integer when it's a whole number
            qty = int(quantity) if float(quantity).is_integer() else Decimal(str(quantity))
            service_item = IncomeServiceItem(
                name=item_name,
                amount=Decimal(str(amount)),
                quantity=qty,
            )
            total_amount = service_item.get_total_amount()
            request = IncomeRequest(
                operation_time=(
                    AtomDateTime.from_datetime(operation_time)
                    if operation_time
                    else AtomDateTime.now()
                ),
                request_time=AtomDateTime.now(),
                services=[service_item],
                total_amount=str(total_amount),
                client=client or IncomeClient(),
                payment_type=PaymentType.ACCOUNT,
                ignore_max_total_income_restriction=False,
            )
            response = await self._client.http_client.post(
                "/income",
                json_data=request.model_dump(),
            )
            payload = response.json()
            receipt_uuid = (
                payload.get("approvedReceiptUuid")
                or payload.get("receiptUuid")
                or payload.get("receipt_uuid")
            )
            if receipt_uuid:
                logging.info("Nalogo receipt created: %s", receipt_uuid)
            else:
                logging.info("Nalogo receipt created without a UUID in response.")
            return receipt_uuid
        except Exception:
            logging.exception("Failed to create Nalogo receipt.")
            return None

    async def close(self) -> None:
        return None
