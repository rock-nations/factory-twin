from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,  # `KEY=` in .env means "unset" -> use the default
    )

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "change-me"
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883

    openai_api_key: str = ""
    openai_orchestrator_model: str = ""
    openai_cypher_model: str = ""
    openai_answer_model: str = ""
    openai_cheap_model: str = ""
    openai_embedding_model: str = "text-embedding-3-small"
    openai_embedding_dimensions: int = 1536
    # "low"/"medium"/"high"/"none" for reasoning models; empty = do not send the parameter
    openai_reasoning_effort: str = ""
    price_input_per_m: float = 0.0
    price_output_per_m: float = 0.0

    rag_min_score: float = 0.68  # calibrated, see README
    sim_speed: float = 0.5

    # run the MQTT consumer + alarm engine inside the API process
    enable_streaming: bool = True

    cors_origins: list[str] = ["http://localhost:4200"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
