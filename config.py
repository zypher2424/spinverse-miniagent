"""本版本独立配置；不读取历史版本密钥，Session 只保存非敏感配置指纹。"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class Config:
    base_url: str
    model: str
    api_key: str = field(repr=False)

    @classmethod
    def load(cls, model=None):
        load_dotenv(Path(__file__).with_name(".env"), override=False)
        config = cls(os.getenv("OPENAI_BASE_URL", "").strip(),
                     model or os.getenv("MODEL_NAME", "").strip(),
                     os.getenv("OPENAI_API_KEY", "").strip())
        if not all((config.base_url, config.model, config.api_key)):
            raise ValueError("请在项目根目录 .env 配置 OPENAI_BASE_URL、OPENAI_API_KEY、MODEL_NAME，或使用 --demo。")
        if not config.base_url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise ValueError("远程 API 必须使用 HTTPS")
        return config
