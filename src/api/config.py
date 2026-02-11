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
    lite_telegram_enable_charts: bool = Field(
        default=True, alias="LITE_TELEGRAM_ENABLE_CHARTS"
    )
    lite_telegram_enable_map_buttons: bool = Field(
        default=True, alias="LITE_TELEGRAM_ENABLE_MAP_BUTTONS"
    )
    lite_telegram_enable_maps: bool = Field(
        default=True, alias="LITE_TELEGRAM_ENABLE_MAPS"
    )
    lite_telegram_interaction_ttl_seconds: int = Field(
        default=3600, alias="LITE_TELEGRAM_INTERACTION_TTL_SECONDS"
    )
    lite_telegram_interaction_cache_size: int = Field(
        default=100000, alias="LITE_TELEGRAM_INTERACTION_CACHE_SIZE"
    )
    lite_chart_caption_max_chars: int = Field(
        default=80, alias="LITE_CHART_CAPTION_MAX_CHARS"
    )
    lite_map_caption_max_chars: int = Field(
        default=80, alias="LITE_MAP_CAPTION_MAX_CHARS"
    )
    lite_chart_render_dpi: int = Field(
        default=180, alias="LITE_CHART_RENDER_DPI"
    )
    lite_chart_width_px: int = Field(default=1200, alias="LITE_CHART_WIDTH_PX")
    lite_chart_height_px: int = Field(
        default=750, alias="LITE_CHART_HEIGHT_PX"
    )

    # Map rendering settings
    mapbox_access_token: str = Field(default="", alias="MAPBOX_ACCESS_TOKEN")
    mapbox_api_token: str = Field(default="", alias="MAPBOX_API_TOKEN")
    mapbox_style_id: str = Field(
        default="mapbox/dark-v11", alias="MAPBOX_STYLE_ID"
    )
    mapbox_static_timeout_seconds: float = Field(
        default=2.5, alias="MAPBOX_STATIC_TIMEOUT_SECONDS"
    )
    mapbox_static_scale: int = Field(default=2, alias="MAPBOX_STATIC_SCALE")
    map_overlay_tile_timeout_seconds: float = Field(
        default=4.0, alias="MAP_OVERLAY_TILE_TIMEOUT_SECONDS"
    )
    map_overlay_tile_retries: int = Field(
        default=1, alias="MAP_OVERLAY_TILE_RETRIES"
    )
    map_overlay_max_concurrency: int = Field(
        default=8, alias="MAP_OVERLAY_MAX_CONCURRENCY"
    )

    @property
    def domains_allowlist(self) -> list[str]:
        if not self.domains_allowlist_str.strip():
            return []
        return [
            domain.strip() for domain in self.domains_allowlist_str.split(",")
        ]

    @property
    def resolved_mapbox_token(self) -> str:
        return self.mapbox_access_token or self.mapbox_api_token

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
