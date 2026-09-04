from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://hoodwise:hoodwise@localhost:5432/hoodwise"
    session_secret: str = "change-me-to-a-long-random-string"
    session_ttl_hours: int = 720

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"
    chat_model: str = "claude-sonnet-4-5"
    vision_model: str = "claude-sonnet-4-5"

    manual_dir: str = "FC&FK Service Manual"
    data_dir: str = "data"
    image_dir: str = "data/images"

    app_host: str = "0.0.0.0"
    app_port: int = 8000
    cors_origins: str = "http://localhost:3000"
    rate_limit_chat_per_min: int = 20
    rate_limit_vin_per_min: int = 30

    # Retailer sites for shop links: "eu" (German sites, ship to the Balkans), "uk", or "us".
    shop_region: str = "eu"

    # Optional global VIN APIs (NHTSA vPIC is always tried first; it is US-only).
    vincario_api_key: str = ""
    vincario_secret_key: str = ""
    carsxe_api_key: str = ""
    api_ninjas_key: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
