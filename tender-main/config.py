#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Простой чекер конфигурации проекта Tender Analyzer.

- Подгружает .env из корня проекта
- Проверяет наличие ключевых переменных
- Печатает понятный статус в консоль
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


# -------------------------------------------------------------------
# 1. Находим и подгружаем .env из папки, где лежит этот файл
# -------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

if load_dotenv is not None:
    if ENV_PATH.exists():
        load_dotenv(dotenv_path=ENV_PATH)
    else:
        print(f"⚠ Файл .env не найден по пути: {ENV_PATH}")
else:
    print("⚠ Модуль python-dotenv не установлен. Установи его: pip install python-dotenv")

# -------------------------------------------------------------------
# 2. Читаем переменные окружения
# -------------------------------------------------------------------
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME", "openai/gpt-oss-120b")
MINDSEARCH_LLM_PROVIDER = os.getenv("MINDSEARCH_LLM_PROVIDER", "openrouter")


def print_status() -> None:
    """Печатаем человеко-читаемый статус конфигурации."""
    print("=== Проверка конфигурации LLM / OpenRouter ===")

    if not OPENROUTER_API_KEY:
        print("❌ OPENROUTER_API_KEY не найден. LLM для цен и анализа будет недоступен.")
    else:
        masked = OPENROUTER_API_KEY[:8] + "..." + OPENROUTER_API_KEY[-4:]
        print(f"✅ OPENROUTER_API_KEY найден: {masked}")
        print(f"   Длина ключа: {len(OPENROUTER_API_KEY)} символов")

    print(f"Модель LLM (MODEL_NAME): {MODEL_NAME}")
    print(f"Провайдер (MINDSEARCH_LLM_PROVIDER): {MINDSEARCH_LLM_PROVIDER}")
    print("")
    print("Если здесь всё зелёное, то проблемы надо искать не в .env, а в логике кода.")


if __name__ == "__main__":
    print_status()
