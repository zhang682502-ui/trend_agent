from __future__ import annotations

from providers.provider_factory import get_provider


def main() -> None:
    provider = get_provider("ollama", "qwen2:1.5b")
    reply = provider.chat("Reply with one short Chinese sentence saying hello.", timeout_s=60)
    print(reply)


if __name__ == "__main__":
    main()
