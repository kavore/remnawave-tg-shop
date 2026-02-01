# Test referral script

Run inside the bot container (has DB access):

```
docker exec remnawave-tg-shop python3 /app/scripts/add_test_referrals.py --referrer-id <ID>
```

Examples:

```
docker exec remnawave-tg-shop python3 /app/scripts/add_test_referrals.py --referrer-id 8448246169 --count 3 --amount 300 --provider yookassa
```

Arguments:
- `--referrer-id` — Telegram user ID of the referrer (must exist in DB)
- `--count` — number of referred users to create (default: 3)
- `--amount` — payment amount for each referred user (default: 300)
- `--provider` — payment provider for test payments (default: yookassa)

Notes:
- Cash bonus is credited only if `REFERRAL_CASH_BONUS_PERCENT` > 0 and provider is in `yookassa/freekassa/severpay/platega`.
