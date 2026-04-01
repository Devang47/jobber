import os
from dataclasses import dataclass, field
from dotenv import load_dotenv


@dataclass
class Config:
    discord_token: str
    server_ids: list[int]
    groq_api_key: str
    groq_model: str
    telegram_bot_token: str
    telegram_chat_id: int
    min_message_length: int
    prefilter_keywords: list[str]
    log_level: str
    reconnect_delay: int
    max_reconnect_attempts: int
    telegram_cooldown: int

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()

        discord_token = os.getenv("DISCORD_TOKEN")
        if not discord_token:
            raise ValueError("DISCORD_TOKEN is required in .env")

        server_ids_raw = os.getenv("DISCORD_SERVER_IDS", "")
        if not server_ids_raw.strip():
            raise ValueError("DISCORD_SERVER_IDS is required in .env")
        server_ids = [int(sid.strip()) for sid in server_ids_raw.split(",") if sid.strip()]

        groq_api_key = os.getenv("GROQ_API_KEY")
        if not groq_api_key:
            raise ValueError("GROQ_API_KEY is required in .env")

        telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not telegram_bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required in .env")

        telegram_chat_id_raw = os.getenv("TELEGRAM_CHAT_ID")
        if not telegram_chat_id_raw:
            raise ValueError("TELEGRAM_CHAT_ID is required in .env")
        telegram_chat_id = int(telegram_chat_id_raw)

        keywords_raw = os.getenv(
            "PREFILTER_KEYWORDS",
            "hiring,looking for,freelance,remote,contract,developer,designer,gig,project,budget,pay,rate,deadline,apply,opportunity,position,role",
        )

        return cls(
            discord_token=discord_token,
            server_ids=server_ids,
            groq_api_key=groq_api_key,
            groq_model=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
            telegram_bot_token=telegram_bot_token,
            telegram_chat_id=telegram_chat_id,
            min_message_length=int(os.getenv("MIN_MESSAGE_LENGTH", "50")),
            prefilter_keywords=[kw.strip() for kw in keywords_raw.split(",") if kw.strip()],
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            reconnect_delay=int(os.getenv("RECONNECT_DELAY_SECONDS", "5")),
            max_reconnect_attempts=int(os.getenv("MAX_RECONNECT_ATTEMPTS", "10")),
            telegram_cooldown=int(os.getenv("TELEGRAM_COOLDOWN_SECONDS", "30")),
        )
