import json
import logging
import os
from typing import Dict, Optional, Tuple
from dataclasses import dataclass


from registry import ProviderRegistry
import re

logger = logging.getLogger(__name__)
# Максимально разумная цена за 1 единицу работы (руб.)
# Всё, что выше, считаем неадекватным и игнорируем.
MAX_REASONABLE_UNIT_PRICE = 10_000_000  # 10 млн руб.

@dataclass
class PriceInfo:
    """
    Унифицированный объект с результатом поиска цены,
    который потом использует ai_services / generate_report.
    """
    ok: bool
    source: str = ""                 # откуда взяли цену: 'llm', 'cache', 'none' и т.п.
    price_min: Optional[float] = None
    price_max: Optional[float] = None
    unit: str = "шт"
    currency: str = "RUB"
    confidence: float = 0.0          # субъективная уверенность (0..1)
    comment: str = ""                # пояснение для отчёта / логов

from typing import List

@dataclass
class PerformerInfo:
    """
    Описание найденного исполнителя (компании) для вида работ.
    Это то, что потом кладём в performers_by_task.
    """
    name: str
    site: str = ""
    phone: str = ""
    email: str = ""
    address: str = ""
    rating: float | None = None

class SearchService:
    """
    Сервис поиска цен.

    Реализация сейчас такая:
    1) Кэш по (task, city), чтобы не дёргать LLM по кругу.
    2) Поиск цены через OpenRouter (LLM), аккуратный промпт,
       ответ строго в JSON, который парсим.
    3) Без лишних логов с кучей ссылок.
    """

    def __init__(self) -> None:
        self._cache: Dict[Tuple[str, str], PriceInfo] = {}

        # Инициализация провайдера
        self._provider = ProviderRegistry.get_provider()

        # Получение API ключа
        self._openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "").strip()
        if self._openrouter_api_key:
            logger.info("SearchService: OPENROUTER_API_KEY найден, LLM-поиск цен включён.")
        else:
            logger.warning(
                "SearchService: OPENROUTER_API_KEY не найден, LLM-поиск цен будет отключён."
            )
        # Ключ для внешнего сервиса по поиску исполнителей (если используем)
        # Даже если переменная окружения не задана, поле ДОЛЖНО существовать,
        # чтобы не было AttributeError.
        self._places_api_key: str = os.getenv("PLACES_API_KEY", "").strip()
        if not self._places_api_key:
            logger.warning(
                "SearchService: PLACES_API_KEY не настроен, поиск исполнителей будет отключён."
            )

        # Чуть приглушим самые шумные HTTP-логгеры (если они есть)
        for noisy in ("httpx", "urllib3"):
            try:
                logging.getLogger(noisy).setLevel(logging.WARNING)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # ВНУТРЕННИЙ ВЫЗОВ ВНЕШНЕГО API ПО ОРГАНИЗАЦИЯМ
    # ------------------------------------------------------------------
    def _call_places_api(self, query: str, city: str, limit: int = 5) -> list[dict]:
        """
        Реальный вызов API Яндекс Поиска по организациям.

        Возвращает список "features" из ответа, каждый элемент — dict.
        При любых ошибках (403, сетевые и т.п.) возвращает [] без трейсбека.
        """
        if not self._places_api_key:
            return []

        import requests

        url = "https://search-maps.yandex.ru/v1/"
        # Ищем компании по тексту "вид работ + город"
        text = f"{query} {city}".strip()
        params = {
            "apikey": self._places_api_key,
            "text": text,
            "lang": "ru_RU",
            "type": "biz",      # бизнес-объекты
            "results": limit,   # максимум организаций
        }

        try:
            resp = requests.get(url, params=params, timeout=10)
        except Exception as e:
            # Любая сетевая ошибка — просто предупреждение и пустой список
            logger.warning(
                "SearchService: не удалось обратиться к Яндекс API по организациям "
                "для запроса '%s' (%s): %s",
                text,
                url,
                e,
            )
            return []

        # Специально обрабатываем 403 как штатную ситуацию (нет доступа/лимиты)
        if resp.status_code == 403:
            logger.warning(
                "SearchService: Яндекс API по организациям вернул 403 Forbidden для запроса "
                "'%s' (%s). Продолжаем без Яндекс-организаций.",
                text,
                resp.url,
            )
            return []

        # Остальные коды ошибок — тоже без трейсбека
        if not resp.ok:
            logger.warning(
                "SearchService: Яндекс API по организациям вернул статус %s для запроса "
                "'%s' (%s). Продолжаем без Яндекс-организаций.",
                resp.status_code,
                text,
                resp.url,
            )
            return []

        try:
            data = resp.json()
        except Exception as e:
            logger.warning(
                "SearchService: не удалось распарсить JSON от Яндекс API по организациям "
                "для запроса '%s' (%s): %s",
                text,
                resp.url,
                e,
            )
            return []

        # для поиска организаций Яндекс возвращает FeatureCollection с полем "features"
        items = data.get("features", [])
        if not isinstance(items, list):
            return []
        return items


    def _search_avito_performers(self, task: str, city: str, limit: int = 5) -> list[PerformerInfo]:
        """
        Fallback-поиск исполнителей через Avito.

        Логика:
        - формирует поисковый запрос "вид работ + город"
        - открывает страницу поиска Avito
        - парсит первые N объявлений
        - возвращает их как PerformerInfo с прямыми ссылками на объявления.

        При ошибках (включая 429 Too Many Requests) возвращает пустой список,
        чтобы не ронять весь анализ.
        """
        import requests
        from bs4 import BeautifulSoup  # пакет beautifulsoup4

        query_str = f"{task} {city}".strip()
        params = {"q": query_str}
        url = "https://www.avito.ru/rossiya"

        headers = {
            # Без User-Agent Avito чаще режет запросы
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            )
        }

        try:
            resp = requests.get(url, params=params, headers=headers, timeout=10)
            try:
                resp.raise_for_status()
            except requests.HTTPError as e:
                # Если Avito нас ограничивает по частоте — просто логируем и выходим
                if resp.status_code == 429:
                    logger.warning(
                        "SearchService: Avito вернул 429 Too Many Requests для запроса '%s' (%s). "
                        "Возвращаем пустой список исполнителей.",
                        query_str,
                        url,
                    )
                    return []
                # Для других статусов пробрасываем дальше, пусть обработается выше
                raise

        except Exception:
            # Любая сетевая ошибка — аккуратно залогировать и вернуть пустой список
            logger.exception(
                "SearchService: ошибка сети при обращении к Avito для запроса '%s'.", query_str
            )
            return []

        soup = BeautifulSoup(resp.text, "html.parser")

        # Типичная разметка Avito: ссылки объявлений имеют data-marker="item-title"
        links = soup.select('a[data-marker="item-title"]')
        if not links:
            # fallback-селектор на случай, если Avito что-то поменяет
            links = soup.select('a[itemprop="url"]')

        performers: list[PerformerInfo] = []

        for a in links[: (limit or 5)]:
            title = (a.get_text(strip=True) or "").strip()
            href = a.get("href") or ""
            if not href:
                continue

            # Делаем абсолютный URL
            if href.startswith("http"):
                full_url = href
            else:
                full_url = "https://www.avito.ru" + href

            if not title:
                title = "Объявление Avito"

            performers.append(
                PerformerInfo(
                    name=title,
                    site=full_url,   # прямая ссылка на объявление
                    phone="",        # телефон без авторизации не вытащим
                    email="",
                    address="Avito",
                    rating=None,
                )
            )

        return performers



    def _parse_places_items(self, items: list[dict]) -> list[PerformerInfo]:
        """
        Превращаем ответ Яндекс search-maps (features) в список PerformerInfo.
        Формат берём из CompanyMetaData.
        """
        performers: list[PerformerInfo] = []

        for it in items or []:
            try:
                if not isinstance(it, dict):
                    continue

                props = it.get("properties", {}) or {}
                company = props.get("CompanyMetaData", {}) or {}

                name = (company.get("name") or "").strip()
                if not name:
                    continue

                address = (company.get("address") or props.get("description") or "").strip()

                site = (company.get("url") or "").strip()

                # телефоны
                phone = ""
                phones = company.get("Phones") or []
                if isinstance(phones, list) and phones:
                    ph0 = phones[0]
                    if isinstance(ph0, dict):
                        phone = (ph0.get("formatted") or ph0.get("number") or "").strip()
                    elif isinstance(ph0, str):
                        phone = ph0.strip()

                # рейтинг (если будет в ответе)
                rating = None
                rating_val = company.get("rating") or company.get("Reviews", {}).get("rating")
                if rating_val is not None:
                    try:
                        rating = float(rating_val)
                    except (TypeError, ValueError):
                        rating = None

                performers.append(
                    PerformerInfo(
                        name=name,
                        site=site,
                        phone=phone,
                        email="",          # в Яндекс-ответе обычно нет
                        address=address,
                        rating=rating,
                    )
                )
            except Exception:
                logger.exception(
                    "SearchService: не удалось распарсить элемент ответа Яндекс API."
                )
                continue

        return performers


    # ------------------------------------------------------------------
    # Публичный метод, который зовёт ai_services.py
    # ------------------------------------------------------------------
    def search_prices(self, task: str, city: str = "Россия") -> PriceInfo:
        """
        Основной вход: поиск цены для вида работ в конкретном городе.

        :param task: строка с описанием работ (например 'Бурение свай диаметром 300 мм')
        :param city: город / регион (например 'Казань')
        :return: PriceInfo
        """
        task = (task or "").strip()
        city = (city or "").strip() or "Россия"

        if not task:
            return PriceInfo(
                ok=False,
                source="search_service",
                comment="Не задано описание вида работ для поиска цены.",
            )

        cache_key = (task.lower(), city.lower())
        if cache_key in self._cache:
            logger.info(
                "SearchService: используем кэш цен для '%s' (%s)", task, city
            )
            cached = self._cache[cache_key]
            # Явно пометим, что это из кэша
            return PriceInfo(
                ok=cached.ok,
                source="cache",
                price_min=cached.price_min,
                price_max=cached.price_max,
                unit=cached.unit,
                currency=cached.currency,
                confidence=cached.confidence,
                comment=cached.comment,
            )

        logger.info(
            "SearchService: LLM-поиск цены для '%s' в городе '%s'", task, city
        )

        # Если ключа нет — даже не пытаемся
        if not self._openrouter_api_key:
            info = PriceInfo(
                ok=False,
                source="none",
                comment="OPENROUTER_API_KEY не настроен, поиск цен недоступен.",
            )
            self._cache[cache_key] = info
            return info

        try:
            raw_content = self._ask_llm_price(task, city)
            info = self._parse_llm_price(raw_content)
        except Exception as e:
            logger.exception("SearchService: ошибка при LLM-оценке цены: %s", e)
            info = PriceInfo(
                ok=False,
                source="llm_error",
                comment=f"Ошибка при запросе LLM: {e}",
            )

        self._cache[cache_key] = info
        return info

    # ------------------------------------------------------------------
    # Внутренний вызов OpenRouter
    # ------------------------------------------------------------------
    def _ask_llm_price(self, task: str, city: str) -> str:
        """
        Запрашивает у LLM ориентировочный диапазон цен для вида работ.
        """
        system_prompt = (
            "Ты выступаешь как опытный российский сметчик и аналитик рынка стройматериалов. "
            "Твоя задача — оценить ориентировочную текущую (2024–2025 гг.) рыночную цену "
            "за единицу работы или услуги в рублях для указанных работ в указанном городе России. "
            "Нужно дать реалистичный диапазон цен (price_min и price_max), "
            "который отражает разброс цен по рынку: не слишком узкий и не экстремально широкий. "
            "Ориентируйся на массовый сегмент и типичных подрядчиков, а не на премиум- или демпинговые цены. "
            "Цена должна быть за 1 условную единицу измерения (unit), "
            "например 'шт', 'м', 'м²', 'п.м.' и т.п.\n\n"
            "Ответь СТРОГО в формате JSON БЕЗ дополнительных комментариев, текста, "
            "объяснений до или после JSON.\n\n"
            "Формат JSON:\n"
            "{\n"
            '  "price_min": <минимальная_цена_за_единицу>,\n'
            '  "price_max": <максимальная_цена_за_единицу>,\n'
            '  "unit": "<единица_измерения>",\n'
            '  "currency": "RUB",\n'
            '  "confidence": <число_от_0_до_1>,\n'
            '  "comment": "<краткий комментарий или уточнение>"\n'
            "}\n\n"
            "Если данных почти нет, всё равно постарайся дать аккуратную оценку с пониженной confidence. "
            "Если цены в источниках очень разные, лучше дай достаточно широкий диапазон, "
            "например примерно от 0.6× до 1.6× средней типичной цены."
        )

        user_prompt = (
            f"Вид работ: {task}\n"
            f"Город / регион: {city}\n\n"
            "Нужно оценить ориентировочную рыночную цену за 1 единицу работы "
            "в рублях на основании типичных российских прайсов, коммерческих предложений "
            "и открытых источников. Укажи разумный диапазон цен (price_min и price_max) "
            "для массового рынка. Не завышай и не занижай диапазон искусственно."
        )

        model_name = os.getenv("PRICE_LLM_MODEL", "deepseek/deepseek-r1")

        provider = ProviderRegistry.get_provider()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        try:
            resp = provider.generate(
                messages=messages,
                model=model_name,
                max_tokens=512,
                temperature=0.1,
            )
            content = resp["choices"][0]["message"]["content"]
            return content
        except Exception as e:
            logger.error("Ошибка при запросе LLM: %s", e)
            return ""

    # ------------------------------------------------------------------
    # Публичный метод: поиск исполнителей для вида работ
    # ------------------------------------------------------------------
    def search_performers(self, task: str, city: str = "Россия", limit: int = 5) -> list[PerformerInfo]:
        """
        Ищет потенциальных исполнителей (компании/продавцов) под заданный вид работ
        в конкретном городе.

        Стратегия:
        1) Пытаемся через Яндекс Search Maps (если есть PLACES_API_KEY).
        2) Если Яндекс недоступен / вернул ошибку / пусто — делаем fallback на Avito:
           парсим топ-объявления и возвращаем их как PerformerInfo.
        """
        task = (task or "").strip()
        city = (city or "").strip() or "Россия"

        if not task:
            return []

        performers: list[PerformerInfo] = []

        # --- 1. Пытаемся через Яндекс, если ключ есть ---
        if self._places_api_key:
            query = task
            logger.info("SearchService: поиск исполнителей (Яндекс) для '%s' в городе '%s'", task, city)
            try:
                items = self._call_places_api(query, city, limit=limit)
                performers = self._parse_places_items(items)
            except Exception:
                # Не даём ошибке Яндекса завалить Avito fallback
                logger.exception("SearchService: ошибка на этапе поиска исполнителей через Яндекс.")

        # --- 2. Fallback на Avito, если по Яндексу ничего не нашли ---
        if not performers:
            logger.info("SearchService: fallback на Avito для '%s' в городе '%s'", task, city)
            try:
                performers = self._search_avito_performers(task, city, limit=limit)
            except Exception:
                logger.exception("SearchService: ошибка при поиске исполнителей через Avito.")
                performers = []

        return performers

    # ------------------------------------------------------------------
    # Разбор ответа LLM в PriceInfo
    def _parse_llm_price(self, content: str) -> PriceInfo:
        """
        Разбор ответа LLM в максимально живучем стиле.

        Приоритет:
        1) Пробуем распарсить честный JSON / почти JSON.
        2) Если JSON развалился или в нём нет диапазона цен —
           вытаскиваем все числа из текста и делаем из них диапазон.
        Важно: если нашли хоть какие-то адекватные числа — не возвращаем 0 RUB.
        """
        if not content or not content.strip():
            return PriceInfo(
                ok=False,
                source="llm",
                comment="Пустой ответ LLM при оценке цены.",
            )

        text = content.strip()

        # убираем ```json ... ``` оболочку, если есть
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"```$", "", text, flags=re.IGNORECASE).strip()

        # выдергиваем JSON-подобный фрагмент { ... }
        start = text.find("{")
        end = text.rfind("}")
        json_str = text[start: end + 1] if 0 <= start < end else ""

        price_min: Optional[float] = None
        price_max: Optional[float] = None
        unit = ""
        currency = "RUB"
        confidence: float = 0.0
        comment = ""

        # ---------- 1. Пытаемся распарсить JSON ----------
        if json_str:

            def _try_parse_obj(s: str):
                # сначала пробуем как есть
                try:
                    return json.loads(s)
                except Exception:
                    cleaned = s.strip()
                    # уберём запятые перед закрывающими скобками
                    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
                    # одинарные кавычки → двойные
                    cleaned = cleaned.replace("'", '"')
                    return json.loads(cleaned)

            variants = []
            base = json_str.strip()
            if base:
                variants.append(base)
                variants.append(re.sub(r",\s*([}\]])", r"\1", base))
                variants.append(base.replace("'", '"'))

            obj = None
            last_err: Optional[Exception] = None
            for v in variants:
                if not v:
                    continue
                try:
                    cand = _try_parse_obj(v)
                    if isinstance(cand, dict):
                        obj = cand
                        break
                except Exception as e:
                    last_err = e
                    continue

            def _get_num(d: dict, *keys) -> Optional[float]:
                for k in keys:
                    if k in d and d[k] is not None:
                        try:
                            return float(str(d[k]).replace(" ", "").replace(",", "."))
                        except Exception:
                            continue
                return None

            if isinstance(obj, dict):
                price_min = _get_num(obj, "price_min", "min")
                price_max = _get_num(obj, "price_max", "max")

                if price_min is None and price_max is not None:
                    price_min = price_max
                if price_max is None and price_min is not None:
                    price_max = price_min

                unit = (obj.get("unit") or unit).strip() or unit
                currency = (obj.get("currency") or currency).strip() or currency
                try:
                    confidence = float(obj.get("confidence", confidence))
                except (TypeError, ValueError):
                    pass
                comment = (obj.get("comment") or comment).strip()

        ok = (
                price_min is not None
                and price_max is not None
                and price_min >= 0
                and price_max >= price_min
                and price_max <= MAX_REASONABLE_UNIT_PRICE
        )

        # Если JSON-ответ формально корректен, но цены запредельные —
        # принудительно считаем его невалидным и отдаём на эвристику.
        if ok and (
                price_min > MAX_REASONABLE_UNIT_PRICE
                or price_max > MAX_REASONABLE_UNIT_PRICE
        ):
            logger.warning(
                "SearchService: цена из JSON LLM неадекватно большая: %.2f–%.2f, игнорируем.",
                price_min,
                price_max,
            )
            ok = False

        # ---------- 2. Эвристика, если JSON не дал диапазон ----------
        if not ok:
            nums_raw = re.findall(r"\d[\d\s]{0,8}(?:[.,]\d+)?", text)
            values = []
            for n in nums_raw:
                try:
                    v = float(n.replace(" ", "").replace(",", "."))
                except ValueError:
                    continue

                # отсекаем совсем мелкие (чаще confidence, проценты и т.п.)
                if v < 10:
                    continue

                # выкидываем "годы" типа 2019–2035
                if 1900 <= v <= 2100:
                    continue

                # и совсем безумные цены выше нашего потолка
                if v > MAX_REASONABLE_UNIT_PRICE:
                    continue

                values.append(v)

            values.sort()
            if values:
                if len(values) == 1:
                    price_min = price_max = values[0]
                else:
                    price_min, price_max = values[0], values[1]

                unit = unit or "шт"
                currency = currency or "RUB"
                # эвристика менее надёжна — confidence не выше 0.5
                confidence = min(confidence or 0.5, 0.5)
                extra = "Диапазон цен восстановлен эвристикой из текста ответа LLM."
                comment = f"{comment} {extra}".strip()
                ok = True

                logger.warning(
                    "SearchService: JSON цены не распарсился, "
                    "но найдены числа %s -> %.2f–%.2f",
                    values,
                    price_min,
                    price_max,
                )

        # ---------- 2.5. Если диапазон слишком узкий — немного расширяем ----------
        if ok and price_min is not None and price_max is not None and price_min > 0:
            original_min = price_min
            original_max = price_max
            ratio = price_max / max(price_min, 1e-6)
            # если max меньше чем в 1.5 раза больше min — считаем диапазон слишком узким
            if ratio < 1.5:
                mid = (price_min + price_max) / 2.0
                # обеспечиваем хотя бы ±30 % от средней цены
                min_spread = 0.3 * mid
                current_spread = price_max - price_min
                spread = max(current_spread, min_spread)
                price_min = max(10.0, mid - spread)
                price_max = min(MAX_REASONABLE_UNIT_PRICE, mid + spread)
                if not comment:
                    comment = "Диапазон цен немного расширен для более реалистичного разброса."
                else:
                    comment = f"{comment} Диапазон цен немного расширен для более реалистичного разброса."
                logger.info(
                    "SearchService: диапазон %.2f–%.2f расширен до %.2f–%.2f",
                    original_min,
                    original_max,
                    price_min,
                    price_max,
                )

        # ---------- 3. Логирование и возврат ----------
        if ok:
            logger.info(
                "SearchService: LLM-оценка цены: %.2f–%.2f %s (%s, confidence=%.2f)",
                price_min,
                price_max,
                unit,
                currency,
                confidence,
            )
        else:
            if not comment:
                comment = "LLM не смог вернуть корректный диапазон цен."
            logger.info(
                "SearchService: не удалось извлечь цену из ответа LLM. comment=%s",
                comment,
            )

        return PriceInfo(
            ok=ok,
            source="llm" if ok else "llm_error",
            price_min=price_min,
            price_max=price_max,
            unit=unit,
            currency=currency,
            confidence=confidence,
            comment=comment,
        )
