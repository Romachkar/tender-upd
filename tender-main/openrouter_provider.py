import requests
import json
import os
from typing import List, Dict, Any


class OpenRouterProvider:
    """
    Провайдер для OpenRouter, отправляющий запросы на API для генерации текста.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str = "https://openrouter.ai/api/v1",
        timeout: int = 60,
    ):
        """
        Инициализация провайдера с заданным API ключом и базовым URL.
        :param api_key: Ключ API OpenRouter.
        :param api_base: Базовый URL для запросов.
        :param timeout: Время ожидания ответа от API.
        """
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        self.api_base = api_base
        self.timeout = timeout

        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY не установлен")

    # ---------- Вспомогательные методы ----------

    def _build_url(self, endpoint: str) -> str:
        """
        Собирает полный URL для запроса.
        :param endpoint: Путь к эндпоинту, например "/chat/completions".
        :return: Полный URL.
        """
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        return self.api_base.rstrip("/") + endpoint

    def _headers(self) -> Dict[str, str]:
        """
        Формирует заголовки для запросов к OpenRouter.
        """
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    # ---------- Простая генерация текста ----------
    # ---------- Совместимость с интерфейсом MindSearch: метод generate ----------

    def generate(
        self,
        messages: List[Dict[str, Any]],
        model: str = "gpt-4.1",
        max_tokens: int = 1024,
        temperature: float = 0.7,
        extra_headers: Dict[str, str] | None = None,
        **_: Any,
    ) -> Dict[str, Any]:
        """
        Унифицированный метод generate(...) под ожидаемый интерфейс MindSearch.

        Используется в:
          - ai_services.py (анализ чанков и чат с агентом),
          - search_services.py (LLM-поиск цены).

        Возвращает полный JSON-ответ OpenRouter в формате /chat/completions.
        """
        url = self._build_url("/chat/completions")

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        headers = self._headers()
        if extra_headers:
            headers.update(extra_headers)

        resp = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def generate_text(
        self,
        prompt: str,
        model: str = "gpt-4.1",
        max_tokens: int = 512,
        temperature: float = 0.7,
        extra_headers: Dict[str, str] | None = None,
    ) -> str:
        """
        Отправляет запрос к OpenRouter для генерации текста.

        :param prompt: Текст запроса.
        :param model: Имя модели, например "gpt-4.1" или "gpt-4o-mini".
        :param max_tokens: Максимальное количество токенов в ответе.
        :param temperature: Температура для контроля креативности.
        :param extra_headers: Дополнительные заголовки, если нужно.
        :return: Сгенерированный текст.
        """
        url = self._build_url("/chat/completions")

        messages = [{"role": "user", "content": prompt}]

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        headers = self._headers()
        if extra_headers:
            headers.update(extra_headers)

        try:
            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )

            resp.raise_for_status()
            data = resp.json()

            # Извлекаем текст из первого варианта ответа
            choices = data.get("choices", [])
            if not choices:
                return ""

            message = choices[0].get("message", {})
            content = message.get("content", "")

            if isinstance(content, list):
                # Иногда контент приходит списком частей
                text_parts = []
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        text_parts.append(part["text"])
                    elif isinstance(part, str):
                        text_parts.append(part)
                return "".join(text_parts)

            return content if isinstance(content, str) else ""

        except requests.exceptions.RequestException as e:
            print(f"Ошибка при запросе к OpenRouter: {e}")
            return ""
        except json.JSONDecodeError as e:
            print(f"Ошибка парсинга JSON-ответа: {e}")
            return ""

    # ---------- Чат-формат (messages) ----------

    def chat(
        self,
        messages: List[Dict[str, Any]],
        model: str = "gpt-4.1",
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> Dict[str, Any]:
        """
        Отправляет запрос к чат-модели OpenRouter.

        :param messages: Список сообщений вида [{"role": "user", "content": "..."}, ...].
        :param model: Имя модели OpenRouter.
        :param max_tokens: Максимальное количество токенов.
        :param temperature: Температура.
        :return: Полный JSON-ответ от OpenRouter.
        """
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        url = self._build_url("/chat/completions")

        try:
            resp = requests.post(
                url,
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )

            resp.raise_for_status()
            data = resp.json()
            return data
        except requests.exceptions.RequestException as e:
            print(f"Ошибка при запросе: {e}")
            return {}
        except json.JSONDecodeError as e:
            print(f"Ошибка при парсинге JSON: {e}")
            return {}

    # ---------- Стриминг (потоковый вывод) ----------

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        model: str = "gpt-4.1",
        max_tokens: int = 512,
        temperature: float = 0.7,
    ):
        """
        Потоковый запрос к OpenRouter (stream=True).
        Генератор, который по мере прихода данных отдаёт части текста.

        :param messages: Сообщения чата.
        :param model: Имя модели.
        :param max_tokens: Лимит токенов.
        :param temperature: Температура.
        :yield: Части текста по мере генерации.
        """
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        url = self._build_url("/chat/completions")

        try:
            with requests.post(
                url,
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
                stream=True,
            ) as resp:
                resp.raise_for_status()

                for raw_line in resp.iter_lines(decode_unicode=True):
                    if not raw_line:
                        continue

                    line = raw_line.strip()

                    # SSE формат: "data: {...}"
                    if line.startswith("data:"):
                        line = line[len("data:") :].strip()

                    if line == "[DONE]" or not line:
                        continue

                    try:
                        data = json.loads(line)
                        for choice in data.get("choices", []):
                            delta = choice.get("delta") or choice.get("message") or {}
                            text = delta.get("content") or delta.get("text")
                            if text:
                                yield text
                    except json.JSONDecodeError:
                        # Если данные не в формате JSON, возвращаем как текст
                        yield line
        except requests.exceptions.RequestException as e:
            print(f"Ошибка при запросе: {e}")
            yield ""
