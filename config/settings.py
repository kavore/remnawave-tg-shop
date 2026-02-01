import logging
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, ValidationError, computed_field, field_validator
from typing import Optional, List, Dict, Any


class Settings(BaseSettings):
    BOT_TOKEN: str
    ADMIN_IDS_STR: str = Field(
        default="",
        alias="ADMIN_IDS",
        description="Comma-separated list of admin Telegram User IDs")

    POSTGRES_USER: str = Field(default="user")
    POSTGRES_PASSWORD: str = Field(default="password")
    POSTGRES_HOST: str = Field(default="localhost")
    POSTGRES_PORT: int = Field(default=5432)
    POSTGRES_DB: str = Field(default="vpn_shop_db")

    DEFAULT_LANGUAGE: str = Field(default="ru")
    DEFAULT_CURRENCY_SYMBOL: str = Field(default="RUB")

    SUPPORT_LINK: Optional[str] = Field(default=None)
    SERVER_STATUS_URL: Optional[str] = Field(default=None)
    TERMS_OF_SERVICE_URL: Optional[str] = Field(default=None)
    REQUIRED_CHANNEL_ID: Optional[int] = Field(
        default=None,
        description="Telegram channel ID the user must join to access the bot")
    REQUIRED_CHANNEL_LINK: Optional[str] = Field(
        default=None,
        description="Public username or invite link to the required channel for join button")

    YOOKASSA_SHOP_ID: Optional[str] = None
    YOOKASSA_SECRET_KEY: Optional[str] = None
    YOOKASSA_RETURN_URL: Optional[str] = None

    YOOKASSA_DEFAULT_RECEIPT_EMAIL: Optional[str] = Field(default=None)
    YOOKASSA_VAT_CODE: int = Field(default=1)
    # Deprecated: explicit receipt fields are now derived from YOOKASSA_AUTOPAYMENTS_ENABLED
    YOOKASSA_PAYMENT_MODE: str = Field(default="full_prepayment")
    YOOKASSA_PAYMENT_SUBJECT: str = Field(default="service")
    # Single toggle to enable recurring payments (saving cards, managing payment methods, auto-renew)
    YOOKASSA_AUTOPAYMENTS_ENABLED: bool = Field(default=False)
    YOOKASSA_AUTOPAYMENTS_REQUIRE_CARD_BINDING: bool = Field(
        default=True,
        description="When true, new YooKassa payments in autopay mode force card binding without a user checkbox."
    )

    LKNPD_INN: Optional[str] = Field(
        default=None,
        alias="NALOGO_INN",
        description="INN for lknpd.nalog.ru (self-employed) authentication"
    )
    LKNPD_PASSWORD: Optional[str] = Field(
        default=None,
        alias="NALOGO_PASSWORD",
        description="Password for lknpd.nalog.ru (self-employed) authentication"
    )
    LKNPD_API_URL: str = Field(
        default="https://lknpd.nalog.ru/api",
        alias="NALOGO_API_URL",
        description="Base URL for LKNPD API (can be overridden for proxies)"
    )
    LKNPD_RECEIPT_NAME_SUBSCRIPTION: str = Field(
        default="subscription {months} months",
        alias="NALOGO_RECEIPT_NAME_SUBSCRIPTION",
        description="Receipt item name for time-based subscriptions. Use {months} placeholder for duration."
    )
    LKNPD_RECEIPT_NAME_TRAFFIC: str = Field(
        default="traffic package {gb} GB",
        alias="NALOGO_RECEIPT_NAME_TRAFFIC",
        description="Receipt item name for traffic packages. Use {gb} placeholder for traffic amount."
    )

    WEBHOOK_BASE_URL: Optional[str] = None

    CRYPTOPAY_TOKEN: Optional[str] = None
    CRYPTOPAY_NETWORK: str = Field(default="mainnet")
    CRYPTOPAY_CURRENCY_TYPE: str = Field(default="fiat")
    CRYPTOPAY_ASSET: str = Field(default="RUB")
    CRYPTOPAY_ENABLED: bool = Field(default=True)
    PLATEGA_ENABLED: bool = Field(default=False)
    PLATEGA_BASE_URL: str = Field(default="https://app.platega.io")
    PLATEGA_MERCHANT_ID: Optional[str] = None
    PLATEGA_SECRET: Optional[str] = None
    PLATEGA_PAYMENT_METHOD: int = Field(
        default=2, description="Platega payment method ID (e.g., 2 for SBP QR)"
    )
    PLATEGA_RETURN_URL: Optional[str] = Field(default=None)
    PLATEGA_FAILED_URL: Optional[str] = Field(default=None)

    FREEKASSA_ENABLED: bool = Field(default=False)
    FREEKASSA_MERCHANT_ID: Optional[str] = None
    FREEKASSA_FIRST_SECRET: Optional[str] = None
    FREEKASSA_SECOND_SECRET: Optional[str] = None
    FREEKASSA_PAYMENT_URL: str = Field(default="https://pay.freekassa.ru/")
    FREEKASSA_API_KEY: Optional[str] = None
    FREEKASSA_PAYMENT_IP: Optional[str] = None
    FREEKASSA_PAYMENT_METHOD_ID: Optional[int] = None

    SEVERPAY_ENABLED: bool = Field(default=False)
    SEVERPAY_MID: Optional[int] = None
    SEVERPAY_TOKEN: Optional[str] = None
    SEVERPAY_RETURN_URL: Optional[str] = None
    SEVERPAY_BASE_URL: str = Field(default="https://severpay.io/api/merchant")
    SEVERPAY_LIFETIME_MINUTES: Optional[int] = Field(
        default=None,
        description="Lifetime of the payment link in minutes (30-4320, defaults to provider value)",
    )

    YOOKASSA_ENABLED: bool = Field(default=True)
    STARS_ENABLED: bool = Field(default=True)
    PAYMENT_METHODS_ORDER: Optional[str] = Field(
        default=None,
        description="Comma-separated list of payment methods to show (e.g., severpay,freekassa,yookassa,platega,stars,cryptopay)",
    )

    MONTH_1_ENABLED: bool = Field(default=True, alias="1_MONTH_ENABLED")
    MONTH_3_ENABLED: bool = Field(default=True, alias="3_MONTHS_ENABLED")
    MONTH_6_ENABLED: bool = Field(default=True, alias="6_MONTHS_ENABLED")
    MONTH_12_ENABLED: bool = Field(default=True, alias="12_MONTHS_ENABLED")

    RUB_PRICE_1_MONTH: Optional[int] = Field(default=None)
    RUB_PRICE_3_MONTHS: Optional[int] = Field(default=None)
    RUB_PRICE_6_MONTHS: Optional[int] = Field(default=None)
    RUB_PRICE_12_MONTHS: Optional[int] = Field(default=None)

    STARS_PRICE_1_MONTH: Optional[int] = Field(default=None)
    STARS_PRICE_3_MONTHS: Optional[int] = Field(default=None)
    STARS_PRICE_6_MONTHS: Optional[int] = Field(default=None)
    STARS_PRICE_12_MONTHS: Optional[int] = Field(default=None)
    PANEL_WEBHOOK_SECRET: Optional[str] = Field(default=None)

    TRAFFIC_PACKAGES: Optional[str] = Field(
        default=None,
        description="Comma-separated list of traffic packages in the format '<GB>:<price>', e.g. '10:199,50:799'",
    )
    STARS_TRAFFIC_PACKAGES: Optional[str] = Field(
        default=None,
        description="Comma-separated list of traffic packages priced in Stars, e.g. '5:500,20:1500'",
    )

    SUBSCRIPTION_NOTIFICATIONS_ENABLED: bool = Field(default=True)
    SUBSCRIPTION_NOTIFY_ON_EXPIRE: bool = Field(default=True)
    SUBSCRIPTION_NOTIFY_AFTER_EXPIRE: bool = Field(default=True)
    SUBSCRIPTION_NOTIFY_DAYS_BEFORE: int = Field(default=3)

    REFERRAL_BONUS_DAYS_INVITER_1_MONTH: Optional[int] = Field(
        default=3, alias="REFERRAL_BONUS_DAYS_1_MONTH")
    REFERRAL_BONUS_DAYS_INVITER_3_MONTHS: Optional[int] = Field(
        default=7, alias="REFERRAL_BONUS_DAYS_3_MONTHS")
    REFERRAL_BONUS_DAYS_INVITER_6_MONTHS: Optional[int] = Field(
        default=15, alias="REFERRAL_BONUS_DAYS_6_MONTHS")
    REFERRAL_BONUS_DAYS_INVITER_12_MONTHS: Optional[int] = Field(
        default=30, alias="REFERRAL_BONUS_DAYS_12_MONTHS")

    REFERRAL_BONUS_DAYS_REFEREE_1_MONTH: Optional[int] = Field(
        default=1, alias="REFEREE_BONUS_DAYS_1_MONTH")
    REFERRAL_BONUS_DAYS_REFEREE_3_MONTHS: Optional[int] = Field(
        default=3, alias="REFEREE_BONUS_DAYS_3_MONTHS")
    REFERRAL_BONUS_DAYS_REFEREE_6_MONTHS: Optional[int] = Field(
        default=7, alias="REFEREE_BONUS_DAYS_6_MONTHS")
    REFERRAL_BONUS_DAYS_REFEREE_12_MONTHS: Optional[int] = Field(
        default=15, alias="REFEREE_BONUS_DAYS_12_MONTHS")

    # Referral program configuration
    REFERRAL_ONE_BONUS_PER_REFEREE: bool = Field(
        default=True,
        description="When true, referral bonuses (for inviter and referee) are applied only once per invited user - on their first successful payment."
    )
    REFERRAL_CASH_BONUS_PERCENT: float = Field(
        default=0.0,
        description="Cash bonus percent credited to inviter balance from successful referred payments."
    )
    REFERRAL_WITHDRAW_MIN_RUB: float = Field(
        default=1000.0,
        description="Minimum referral balance amount to request withdrawal (RUB)."
    )
    LEGACY_REFS: bool = Field(
        default=True,
        description="Allow legacy referral links like ref_<telegram_id> to continue working. Defaults to True when unset."
    )

    PANEL_API_URL: Optional[str] = None
    PANEL_API_KEY: Optional[str] = None
    USER_TRAFFIC_LIMIT_GB: Optional[float] = Field(default=0.0)
    USER_TRAFFIC_STRATEGY: str = Field(default="NO_RESET")
    USER_SQUAD_UUIDS: Optional[str] = Field(
        default=None,
        description=
        "Comma-separated UUIDs of internal squads to assign to new panel users")
    USER_EXTERNAL_SQUAD_UUID: Optional[str] = Field(
        default=None,
        description=
        "UUID of the external squad to assign to new panel users (optional)")

    TRIAL_ENABLED: bool = Field(default=True)
    TRIAL_DURATION_DAYS: int = Field(default=3)
    TRIAL_TRAFFIC_LIMIT_GB: Optional[float] = Field(default=5.0)

    CRYPT4_ENABLED: bool = Field(default=False, description="Enable happ crypt4 encryption for subscription URLs")
    CRYPT4_REDIRECT_URL: Optional[str] = Field(default=None, description="Base redirect URL used for the connect button when crypt4 is enabled")

    WEB_SERVER_HOST: str = Field(default="0.0.0.0")
    WEB_SERVER_PORT: int = Field(default=8080)
    LOGS_PAGE_SIZE: int = Field(default=10)

    SUBSCRIPTION_MINI_APP_URL: Optional[str] = Field(default=None)

    START_COMMAND_DESCRIPTION: Optional[str] = Field(default=None)
    DISABLE_WELCOME_MESSAGE: bool = Field(default=False, description="Disable welcome message on /start command")

    MY_DEVICES_SECTION_ENABLED: bool = Field(
        default=False,
        description="Enable the My Devices section in the subscription menu"
    )
    USER_HWID_DEVICE_LIMIT: Optional[int] = Field(
        default=None,
        description="Default hardware device limit for panel users (0 = unlimited)"
    )
    
    # Inline mode thumbnail URLs
    INLINE_REFERRAL_THUMBNAIL_URL: str = Field(default="https://cdn-icons-png.flaticon.com/512/1077/1077114.png")
    INLINE_USER_STATS_THUMBNAIL_URL: str = Field(default="https://cdn-icons-png.flaticon.com/512/681/681494.png")
    INLINE_FINANCIAL_STATS_THUMBNAIL_URL: str = Field(default="https://cdn-icons-png.flaticon.com/512/2769/2769339.png")
    INLINE_SYSTEM_STATS_THUMBNAIL_URL: str = Field(default="https://cdn-icons-png.flaticon.com/512/2920/2920277.png")

    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @computed_field
    @property
    def ADMIN_IDS(self) -> List[int]:
        if self.ADMIN_IDS_STR:
            try:
                return [
                    int(admin_id.strip())
                    for admin_id in self.ADMIN_IDS_STR.split(',')
                    if admin_id.strip().isdigit()
                ]
            except ValueError:
                logging.error(
                    f"Invalid ADMIN_IDS_STR format: '{self.ADMIN_IDS_STR}'. Expected comma-separated integers."
                )
                return []
        return []

    @computed_field
    @property
    def PRIMARY_ADMIN_ID(self) -> Optional[int]:
        ids = self.ADMIN_IDS
        return ids[0] if ids else None

    @computed_field
    @property
    def trial_traffic_limit_bytes(self) -> int:
        if self.TRIAL_TRAFFIC_LIMIT_GB is None or self.TRIAL_TRAFFIC_LIMIT_GB <= 0:
            return 0
        return int(self.TRIAL_TRAFFIC_LIMIT_GB * (1024**3))

    @computed_field
    @property
    def user_traffic_limit_bytes(self) -> int:
        if self.USER_TRAFFIC_LIMIT_GB is None or self.USER_TRAFFIC_LIMIT_GB <= 0:
            return 0
        return int(self.USER_TRAFFIC_LIMIT_GB * (1024**3))

    @computed_field
    @property
    def parsed_user_squad_uuids(self) -> Optional[List[str]]:
        if self.USER_SQUAD_UUIDS:
            return [
                uuid.strip()
                for uuid in self.USER_SQUAD_UUIDS.split(',')
                if uuid.strip()
            ]
        return None

    @computed_field
    @property
    def parsed_user_external_squad_uuid(self) -> Optional[str]:
        if self.USER_EXTERNAL_SQUAD_UUID:
            cleaned = self.USER_EXTERNAL_SQUAD_UUID.strip()
            if cleaned:
                return cleaned
        return None

    @computed_field
    @property
    def yookassa_webhook_path(self) -> str:

        return "/webhook/yookassa"

    @computed_field
    @property
    def yookassa_full_webhook_url(self) -> Optional[str]:
        base = self.WEBHOOK_BASE_URL
        if base:
            return f"{base.rstrip('/')}{self.yookassa_webhook_path}"
        return None

    @computed_field
    @property
    def panel_webhook_path(self) -> str:
        return "/webhook/panel"

    @computed_field
    @property
    def panel_full_webhook_url(self) -> Optional[str]:
        base = self.WEBHOOK_BASE_URL
        if base:
            return f"{base.rstrip('/')}{self.panel_webhook_path}"
        return None

    @computed_field
    @property
    def cryptopay_webhook_path(self) -> str:
        return "/webhook/cryptopay"

    @computed_field
    @property
    def cryptopay_full_webhook_url(self) -> Optional[str]:
        base = self.WEBHOOK_BASE_URL
        if base:
            return f"{base.rstrip('/')}{self.cryptopay_webhook_path}"
        return None

    @computed_field
    @property
    def freekassa_webhook_path(self) -> str:
        return "/webhook/freekassa"

    @computed_field
    @property
    def freekassa_full_webhook_url(self) -> Optional[str]:
        base = self.WEBHOOK_BASE_URL
        if base:
            return f"{base.rstrip('/')}{self.freekassa_webhook_path}"
        return None

    @computed_field
    @property
    def severpay_webhook_path(self) -> str:
        return "/webhook/severpay"

    @computed_field
    @property
    def severpay_full_webhook_url(self) -> Optional[str]:
        base = self.WEBHOOK_BASE_URL
        if base:
            return f"{base.rstrip('/')}{self.severpay_webhook_path}"
        return None

    @computed_field
    @property
    def platega_webhook_path(self) -> str:
        return "/webhook/platega"

    @computed_field
    @property
    def platega_full_webhook_url(self) -> Optional[str]:
        base = self.WEBHOOK_BASE_URL
        if base:
            return f"{base.rstrip('/')}{self.platega_webhook_path}"
        return None

    # Computed YooKassa receipt fields based on recurring toggle
    @computed_field
    @property
    def yk_receipt_payment_mode(self) -> str:
        # If autopayments are enabled, use service; otherwise full prepayment
        return "service" if self.YOOKASSA_AUTOPAYMENTS_ENABLED else "full_prepayment"

    @computed_field
    @property
    def yk_receipt_payment_subject(self) -> str:
        # If autopayments are enabled, use full_payment; otherwise payment
        return "full_payment" if self.YOOKASSA_AUTOPAYMENTS_ENABLED else "payment"

    @computed_field
    @property
    def subscription_options(self) -> Dict[int, float]:
        options: Dict[int, float] = {}

        if self.MONTH_1_ENABLED and self.RUB_PRICE_1_MONTH is not None:
            options[1] = float(self.RUB_PRICE_1_MONTH)
        if self.MONTH_3_ENABLED and self.RUB_PRICE_3_MONTHS is not None:
            options[3] = float(self.RUB_PRICE_3_MONTHS)
        if self.MONTH_6_ENABLED and self.RUB_PRICE_6_MONTHS is not None:
            options[6] = float(self.RUB_PRICE_6_MONTHS)
        if self.MONTH_12_ENABLED and self.RUB_PRICE_12_MONTHS is not None:
            options[12] = float(self.RUB_PRICE_12_MONTHS)
        return options

    @computed_field
    @property
    def stars_subscription_options(self) -> Dict[int, int]:
        options: Dict[int, int] = {}
        if self.STARS_ENABLED and self.MONTH_1_ENABLED and self.STARS_PRICE_1_MONTH is not None:
            options[1] = self.STARS_PRICE_1_MONTH
        if self.STARS_ENABLED and self.MONTH_3_ENABLED and self.STARS_PRICE_3_MONTHS is not None:
            options[3] = self.STARS_PRICE_3_MONTHS
        if self.STARS_ENABLED and self.MONTH_6_ENABLED and self.STARS_PRICE_6_MONTHS is not None:
            options[6] = self.STARS_PRICE_6_MONTHS
        if self.STARS_ENABLED and self.MONTH_12_ENABLED and self.STARS_PRICE_12_MONTHS is not None:
            options[12] = self.STARS_PRICE_12_MONTHS
        return options

    @computed_field
    @property
    def traffic_packages(self) -> Dict[float, float]:
        """
        Mapping of traffic size in GB to price in the default currency.
        """
        packages: Dict[float, float] = {}
        raw = (self.TRAFFIC_PACKAGES or "").strip()
        if not raw:
            return packages
        for part in raw.split(","):
            chunk = part.strip()
            if not chunk or ":" not in chunk:
                continue
            size_str, price_str = chunk.split(":", 1)
            try:
                size_gb = float(size_str.strip())
                price_val = float(price_str.strip())
                if size_gb > 0 and price_val >= 0:
                    packages[size_gb] = price_val
            except ValueError:
                logging.warning("Invalid TRAFFIC_PACKAGES entry skipped: %s", chunk)
                continue
        return packages

    @computed_field
    @property
    def stars_traffic_packages(self) -> Dict[float, int]:
        """
        Mapping of traffic size in GB to price in Telegram Stars.
        """
        packages: Dict[float, int] = {}
        raw = (self.STARS_TRAFFIC_PACKAGES or "").strip()
        if not raw:
            return packages
        for part in raw.split(","):
            chunk = part.strip()
            if not chunk or ":" not in chunk:
                continue
            size_str, price_str = chunk.split(":", 1)
            try:
                size_gb = float(size_str.strip())
                price_val = int(float(price_str.strip()))
                if size_gb > 0 and price_val >= 0:
                    packages[size_gb] = price_val
            except ValueError:
                logging.warning("Invalid STARS_TRAFFIC_PACKAGES entry skipped: %s", chunk)
                continue
        return packages

    @computed_field
    @property
    def traffic_sale_mode(self) -> bool:
        """When true, the bot sells traffic packages instead of time-based subscriptions."""
        return bool(self.traffic_packages or self.stars_traffic_packages)

    @computed_field
    @property
    def referral_bonus_inviter(self) -> Dict[int, int]:
        bonuses: Dict[int, int] = {}
        if self.REFERRAL_BONUS_DAYS_INVITER_1_MONTH is not None:
            bonuses[1] = self.REFERRAL_BONUS_DAYS_INVITER_1_MONTH
        if self.REFERRAL_BONUS_DAYS_INVITER_3_MONTHS is not None:
            bonuses[3] = self.REFERRAL_BONUS_DAYS_INVITER_3_MONTHS
        if self.REFERRAL_BONUS_DAYS_INVITER_6_MONTHS is not None:
            bonuses[6] = self.REFERRAL_BONUS_DAYS_INVITER_6_MONTHS
        if self.REFERRAL_BONUS_DAYS_INVITER_12_MONTHS is not None:
            bonuses[12] = self.REFERRAL_BONUS_DAYS_INVITER_12_MONTHS
        return bonuses

    @computed_field
    @property
    def referral_bonus_referee(self) -> Dict[int, int]:
        bonuses: Dict[int, int] = {}
        if self.REFERRAL_BONUS_DAYS_REFEREE_1_MONTH is not None:
            bonuses[1] = self.REFERRAL_BONUS_DAYS_REFEREE_1_MONTH
        if self.REFERRAL_BONUS_DAYS_REFEREE_3_MONTHS is not None:
            bonuses[3] = self.REFERRAL_BONUS_DAYS_REFEREE_3_MONTHS
        if self.REFERRAL_BONUS_DAYS_REFEREE_6_MONTHS is not None:
            bonuses[6] = self.REFERRAL_BONUS_DAYS_REFEREE_6_MONTHS
        if self.REFERRAL_BONUS_DAYS_REFEREE_12_MONTHS is not None:
            bonuses[12] = self.REFERRAL_BONUS_DAYS_REFEREE_12_MONTHS
        return bonuses

    @computed_field
    @property
    def yookassa_autopayments_active(self) -> bool:
        """Autopay features are available only when YooKassa itself is enabled."""
        return bool(self.YOOKASSA_ENABLED and self.YOOKASSA_AUTOPAYMENTS_ENABLED)

    @computed_field
    @property
    def payment_methods_order(self) -> List[str]:
        """
        Ordered list of payment providers to show in the subscription payment keyboard.
        """
        default_order = [
            "freekassa",
            "platega",
            "severpay",
            "yookassa",
            "stars",
            "cryptopay",
        ]
        if not self.PAYMENT_METHODS_ORDER:
            return default_order
        methods = []
        for item in self.PAYMENT_METHODS_ORDER.split(","):
            slug = item.strip().lower()
            if slug:
                methods.append(slug)
        return methods or default_order
    
    # Logging Configuration
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Global log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)",
    )
    LOG_CHAT_ID: Optional[int] = Field(default=None, description="Telegram chat/group ID for sending notifications")
    LOG_THREAD_ID: Optional[int] = Field(default=None, description="Thread ID for supergroup messages (optional)")
    
    @field_validator('LOG_LEVEL', mode='before')
    @classmethod
    def normalize_log_level(cls, v):
        if isinstance(v, str):
            v = v.strip().upper()
        if not v:
            return "INFO"
        return v

    @field_validator('LOG_CHAT_ID', 'LOG_THREAD_ID', mode='before')
    @classmethod
    def validate_optional_int_fields(cls, v):
        """Convert empty strings to None for optional integer fields"""
        if isinstance(v, str) and v.strip() == '':
            return None
        return v

    @field_validator(
        'REQUIRED_CHANNEL_LINK',
        'PLATEGA_RETURN_URL',
        'PLATEGA_FAILED_URL',
        'SEVERPAY_RETURN_URL',
        'CRYPT4_REDIRECT_URL',
        mode='before',
    )
    @classmethod
    def sanitize_optional_link(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v
    
    @field_validator('USER_HWID_DEVICE_LIMIT', 'SEVERPAY_MID', 'SEVERPAY_LIFETIME_MINUTES', mode='before')
    @classmethod
    def validate_optional_int(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return None
        return v
    
    # Notification types
    LOG_NEW_USERS: bool = Field(default=True, description="Send notifications for new user registrations")
    LOG_PAYMENTS: bool = Field(default=True, description="Send notifications for successful payments")
    LOG_PROMO_ACTIVATIONS: bool = Field(default=True, description="Send notifications for promo code activations")
    LOG_TRIAL_ACTIVATIONS: bool = Field(default=True, description="Send notifications for trial activations")
    LOG_SUSPICIOUS_ACTIVITY: bool = Field(default=True, description="Send notifications for suspicious promo attempts")

    model_config = SettingsConfigDict(env_file='.env',
                                      env_file_encoding='utf-8',
                                      extra='ignore',
                                      populate_by_name=True)


_settings_instance: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings_instance
    if _settings_instance is None:
        try:
            _settings_instance = Settings()
            if not _settings_instance.ADMIN_IDS:
                logging.warning(
                    "CRITICAL: ADMIN_IDS not set or contains no valid integer IDs in .env. "
                    "Admin functionality will be restricted.")

            if not _settings_instance.PANEL_API_URL:
                logging.warning(
                    "CRITICAL: PANEL_API_URL is not set. Panel integration will not work."
                )
            if not _settings_instance.YOOKASSA_SHOP_ID or not _settings_instance.YOOKASSA_SECRET_KEY:
                logging.warning(
                    "CRITICAL: YooKassa credentials (SHOP_ID or SECRET_KEY) are not set. Payments will not work."
                )
            if (
                _settings_instance.LKNPD_INN
                or _settings_instance.LKNPD_PASSWORD
            ) and not (
                _settings_instance.LKNPD_INN
                and _settings_instance.LKNPD_PASSWORD
            ):
                logging.warning(
                    "WARNING: LKNPD credentials are incomplete. Receipt sending will be disabled."
                )
            if _settings_instance.FREEKASSA_ENABLED:
                if (
                    not _settings_instance.FREEKASSA_MERCHANT_ID
                    or not _settings_instance.FREEKASSA_API_KEY
                ):
                    logging.warning(
                        "CRITICAL: FreeKassa is enabled but SHOP_ID or API key is missing. FreeKassa payments will not work."
                    )
                if not _settings_instance.FREEKASSA_SECOND_SECRET:
                    logging.warning(
                        "WARNING: FreeKassa second secret is not set. Incoming payment notifications cannot be verified."
                    )
                if not _settings_instance.subscription_options:
                    logging.warning(
                        "CRITICAL: FreeKassa is enabled but no subscription prices are configured (RUB_PRICE_*). Users will not see payment buttons."
                    )

            if _settings_instance.PLATEGA_ENABLED:
                if (
                    not _settings_instance.PLATEGA_MERCHANT_ID
                    or not _settings_instance.PLATEGA_SECRET
                ):
                    logging.warning(
                        "CRITICAL: Platega is enabled but merchant credentials (PLATEGA_MERCHANT_ID/PLATEGA_SECRET) are missing. Platega payments will not work."
                    )
            if _settings_instance.SEVERPAY_ENABLED:
                if not _settings_instance.SEVERPAY_MID or not _settings_instance.SEVERPAY_TOKEN:
                    logging.warning(
                        "CRITICAL: SeverPay is enabled but MID or TOKEN is missing. SeverPay payments will not work."
                    )

        except ValidationError as e:
            logging.critical(
                f"Pydantic validation error while loading settings: {e}")

            raise SystemExit(
                f"CRITICAL SETTINGS ERROR: {e}. Please check your .env file and Settings model."
            )
    return _settings_instance
