from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    def chat(self, prompt: str, timeout_s: int = 60) -> str:
        raise NotImplementedError

    def summarize(self, prompt: str, timeout_s: int = 60) -> str:
        return self.chat(prompt, timeout_s=timeout_s)
