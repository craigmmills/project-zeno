from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

# Load .env first, then override with .env.local
load_dotenv(".env")
load_dotenv(".env.local", override=True)


class _APISettings(BaseSettings):
    """API-specific settings for quotas, authentication, and access control."""

    domains_allowlist_str: str = Field(default="", alias="DOMAINS_ALLOWLIST")

    # Quota settings
    daily_quota_warning_threshold: int = 5
    admin_user_daily_quota: int = 100
    regular_user_daily_quota: int = 25
    pro_user_daily_quota: int = 50
    machine_user_daily_quota: int = 99999
    anonymous_user_daily_quota: int = 10
    ip_address_daily_quota: int = 50
    enable_quota_checking: bool = True

    # Authentication and access control
    nextjs_api_key: str = Field(..., alias="NEXTJS_API_KEY")
    max_user_signups: int = Field(default=-1, alias="MAX_USER_SIGNUPS")
    allow_public_signups: bool = Field(
        default=False, alias="ALLOW_PUBLIC_SIGNUPS"
    )
    allow_anonymous_chat: bool = Field(
        default=False, alias="ALLOW_ANONYMOUS_CHAT"
    )

    # Lite / Telegram settings
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    telegram_webhook_secret: str = Field(
        default="", alias="TELEGRAM_WEBHOOK_SECRET"
    )
    lite_max_response_words: int = Field(
        default=300, alias="LITE_MAX_RESPONSE_WORDS"
    )
    lite_telegram_message_char_limit: int = Field(
        default=4096, alias="LITE_TELEGRAM_MESSAGE_CHAR_LIMIT"
    )
    lite_enable_model_rewrite: bool = Field(
        default=True, alias="LITE_ENABLE_MODEL_REWRITE"
    )

    @property
    def domains_allowlist(self) -> list[str]:
        if not self.domains_allowlist_str.strip():
            return []
        return [
            domain.strip() for domain in self.domains_allowlist_str.split(",")
        ]

    @field_validator("nextjs_api_key")
    def validate_nextjs_api_key(cls, value):
        if not value or value.strip() == "":
            raise ValueError(
                "NEXTJS_API_KEY must be set to a non-empty string"
            )
        return value

    model_config = {
        "env_file": (".env", ".env.local"),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


# Create a singleton instance
APISettings = _APISettings()
