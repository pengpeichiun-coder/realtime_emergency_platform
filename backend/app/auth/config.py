import os
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv


@dataclass(frozen=True)
class AuthSettings:
    secret: str
    access_minutes: int = 10
    refresh_days: int = 7
    environment: str = "production"

    def __post_init__(self):
        if len(self.secret.encode()) < 32:
            raise ValueError("JWT_SECRET must contain at least 32 bytes.")
        if not 1 <= self.access_minutes <= 60 or not 1 <= self.refresh_days <= 30:
            raise ValueError("Token lifetimes must be 1–60 minutes and 1–30 days.")
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("APP_ENV must be development, test or production.")

    @classmethod
    def load(cls):
        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
        return cls(
            os.environ.get("JWT_SECRET", ""),
            int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "10")),
            int(os.environ.get("REFRESH_TOKEN_EXPIRE_DAYS", "7")),
            os.environ.get("APP_ENV", "production"),
        )
