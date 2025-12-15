import requests
import json
import os
from typing import List, Dict, Any

class OpenRouterProvider:
    """
    Провайдер для OpenRouter, отправляющий запросы на API для генерации текста.
    """

    def __init__(self, api_key: str = None, api_base: str = "https://openrouter.ai/api/v1", timeout: int = 60):
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

    def _build_url(self, path: str) -> str:
        """Строит полный URL для API-запроса."""
        base = self.api_base.rstrip("/")
        return f"{base}/{path.lstrip('/')}"

    def _headers(self) -> Dict[str, str]:
        """Генерация стандартных заголовков для API-запросов."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def generate(
        self,
        messages: List[Dict[str, str]],
        model: str = "openai/gpt-4",
        temperature: float = 0.7,
        max_tokens: int = 2048
    ) -> Dict[str, Any]:
        """
        Отправляет запрос на API OpenRouter и возвращает ответ сгенерированного текста.
        :param messages: Список сообщений для генерации.
        :param model: Модель, которая будет использоваться.
        :param temperature: Температура генерации.
        :param max_tokens: Максимальное количество токенов в ответе.
        :return: JSON-ответ с сгенерированным текстом.
        """
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        url = self._build_url("/chat/completions")

        try:
            # Отправка POST-запроса
            resp = requests.post(
                url,
                headers=self._headers(),
                json=payload,
                timeout=self.timeout
            )

            resp.raise_for_status()  # Ошибка, если ответ не 2xx

            # Преобразование ответа в JSON
            data = resp.json()

            return data
        except requests.exceptions.RequestException as e:
            print(f"Ошибка при запросе: {e}")
            return {}

        except json.JSONDecodeError as e:
            print(f"Ошибка при парсинге JSON: {e}")
            return {}

    def stream(
        self,
        messages: List[Dict[str, str]],
        model: str = "openai/gpt-4",
        temperature: float = 0.7,
        max_tokens: int = 2048
    ) -> str:
        """
        Потоковая генерация текста (для случаев, когда необходим ответ по частям).
        :param messages: Список сообщений для генерации.
        :param model: Модель, которая будет использоваться.
        :param temperature: Температура генерации.
        :param max_tokens: Максимальное количество токенов в ответе.
        :return: Часть сгенерированного текста.
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
            # Потоковый запрос
            with requests.post(
                url,
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
                stream=True
            ) as resp:

                resp.raise_for_status()

                # Чтение потока
                for raw_line in resp.iter_lines(decode_unicode=True):
                    if not raw_line:
                        continue
                    line = raw_line.strip()

                    # СSS и SSE формат, например: "data: {...}"
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
