#!/usr/bin/env python3
import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import Settings
from db.database_setup import init_db_connection
from db.dal import user_dal, payment_dal


ALLOWED_CASH_PROVIDERS = {"yookassa", "freekassa", "severpay", "platega"}


def _build_user_id(seed: int, idx: int) -> int:
    # Telegram-like positive bigint
    return int(f"9{seed:09d}{idx}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Add test referral friends and payments.")
    parser.add_argument("--referrer-id", type=int, required=True, help="User ID of the referrer")
    parser.add_argument("--count", type=int, default=3, help="Number of referred users to create")
    parser.add_argument("--amount", type=float, default=300.0, help="Payment amount for each referred user")
    parser.add_argument(
        "--provider",
        type=str,
        default="yookassa",
        help="Payment provider for test payments",
    )
    args = parser.parse_args()

    settings = Settings()
    session_factory = init_db_connection(settings)

    provider = args.provider.lower()

    async with session_factory() as session:
        referrer = await user_dal.get_user_by_id(session, args.referrer_id)
        if not referrer:
            raise RuntimeError(f"Referrer {args.referrer_id} not found in DB.")

        now = datetime.now(timezone.utc)
        seed = int(now.timestamp()) % 1000000000
        created_users = []

        for i in range(args.count):
            new_user_id = _build_user_id(seed, i)
            user_payload = {
                "user_id": new_user_id,
                "username": f"test_ref_{new_user_id}",
                "first_name": f"TestRef{i+1}",
                "language_code": "ru",
                "registration_date": now,
                "referred_by_id": args.referrer_id,
            }
            user_model, _created = await user_dal.create_user(session, user_payload)
            created_users.append(user_model)

            payment_payload = {
                "user_id": new_user_id,
                "amount": float(args.amount),
                "currency": "RUB",
                "status": "succeeded",
                "description": "test referral payment",
                "subscription_duration_months": 1,
                "provider": provider,
                "provider_payment_id": f"test:{provider}:{new_user_id}",
            }
            await payment_dal.create_payment_record(session, payment_payload)

        # Apply cash bonus for referrer if provider supports it
        bonus_percent = float(getattr(settings, "REFERRAL_CASH_BONUS_PERCENT", 0.0) or 0.0)
        if bonus_percent > 0 and provider in ALLOWED_CASH_PROVIDERS:
            bonus_total = round(args.amount * bonus_percent / 100.0, 2) * args.count
            await user_dal.adjust_referral_balance(session, args.referrer_id, bonus_total)
            logging.info(
                "Referral balance increased by %.2f (percent %.2f%%, %d payments)",
                bonus_total,
                bonus_percent,
                args.count,
            )

        await session.commit()

    print(f"Created {len(created_users)} referred users and payments for referrer {args.referrer_id}.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
