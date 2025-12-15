import logging
import logging
from openrouter_provider import OpenRouterProvider  # или from openrouter import ...

# Импортируем локального провайдера OpenRouter из файла openrouter_provider.py
from openrouter_provider import OpenRouterProvider


class ProviderRegistry:
    """
    Простой реестр LLM-провайдера для всего приложения.
    Сейчас используем только OpenRouterProvider.
    """

    _provider: OpenRouterProvider | None = None

    @classmethod
    def init(cls) -> None:
        """Явная инициализация провайдера."""
        if cls._provider is not None:
            return

        try:
            cls._provider = OpenRouterProvider(timeout=30)  # вместо дефолтных 60
            logging.getLogger(__name__).info(
                "ProviderRegistry: OpenRouterProvider успешно инициализирован."
            )
        except Exception as e:
            logging.getLogger(__name__).warning(
                "ProviderRegistry: не удалось инициализировать OpenRouterProvider: %s",
                e,
            )
            cls._provider = None

    @classmethod
    def get_provider(cls) -> OpenRouterProvider:
        """
        Возвращает готовый провайдер LLM.
        Если инициализация не удалась — выбрасывает исключение.
        """
        if cls._provider is None:
            cls.init()

        if cls._provider is None:
            raise RuntimeError(
                "LLM-провайдер не инициализирован. Проверь OPENROUTER_API_KEY и настройки."
            )

        return cls._provider
