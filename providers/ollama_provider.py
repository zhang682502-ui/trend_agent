from __future__ import annotations

from core.llm_provider import LLMProvider
from tools.ollama_cli import run_ollama


class OllamaProvider(LLMProvider):
    def __init__(self, model: str) -> None:
        self.model = str(model or "").strip()
        if not self.model:
            raise ValueError("model is required")

    def chat(self, prompt: str, timeout_s: int = 60) -> str:
        return run_ollama(model=self.model, prompt=prompt, timeout_s=timeout_s).strip()

    def summarize(self, prompt: str, timeout_s: int = 60) -> str:
        return self.chat(prompt, timeout_s=timeout_s)
