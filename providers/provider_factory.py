from __future__ import annotations

import os

from core.llm_provider import LLMProvider
from providers.ollama_provider import OllamaProvider


def get_provider(provider_name: str | None = None, model: str = "") -> LLMProvider:
    provider = str(provider_name or os.getenv("TREND_LLM_PROVIDER", "ollama")).strip().lower() or "ollama"
    if provider in {"ollama", "local"}:
        return OllamaProvider(model=model)
    raise ValueError(f"Unsupported LLM provider: {provider}")
