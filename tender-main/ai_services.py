#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import json
import logging
import re
import copy
from typing import Any, Dict, List, Optional

# ---------------------------
# Загрузка .env (если есть)
# ---------------------------
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ai_services")
from search_services import SearchService

# Попытка подключить MindSearch-провайдера LLM
try:
    from registry import ProviderRegistry  # type: ignore
except Exception:  # pragma: no cover
    ProviderRegistry = None  # type: ignore

# ---------------------------
# Логирование
# ---------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("ai_services")

MODEL_NAME = os.getenv("MODEL_NAME", "openai/gpt-oss-120b")
_llm_provider = None

# --------------------
# Базовая схема JSON
# --------------------
UNIFIED_TENDER_SCHEMA: Dict[str, Any] = {
    "tender_id": "",
    "title": "",
    "description": "",
    "document_type": "",
    "purchase_type": "",
    "customer": {
        "name": "",
        "inn": "",
        "kpp": "",
        "ogrn": "",
        "address": "",
        "contacts": "",
    },
    "object": {
        "name": "",
        "address": "",
        "category": "",
        "volume": "",
        "unit": "",
    },
    "technical": {
        "requirements": "",
        "conditions": "",
        "works": {
            "works_list": [
                {
                    "name": "",
                    "volume": "",
                    "unit": "",
                    "materials": "",
                    "equipment": "",
                    "location": "",
                    "notes": "",
                }
            ]
        },
    },
    "timeline": {
        "start_date": "",
        "end_date": "",
        "duration_days": "",
        "delivery_schedule": "",
    },
    "pricing": {
        "currency": "RUB",
        "price_estimate_min": "",
        "price_estimate_max": "",
        "sources": "",
        "calculation_comment": "",
    },
    "goods": {
        "items": [
            {
                "name": "",
                "description": "",
                "brand": "",
                "model": "",
                "certificates": "",
                "quantity": "",
                "unit": "",
                "requirements": "",
            }
        ]
    },
    "legal": {
        "contract_conditions": "",
        "penalties": "",
        "guarantees": "",
    },
    "analysis_meta": {
        "user_city": "",
        "fallback_used": False,
        "fallback_reason": "",
    },
}


# ---------------------------
# Провайдер LLM
# ---------------------------
def get_llm_provider(enable: bool = True) -> Optional[Any]:
    global _llm_provider

    if not enable:
        return None

    if _llm_provider is not None:
        return _llm_provider

    if ProviderRegistry is None:
        logger.warning("ProviderRegistry MindSearch не найден — LLM недоступен.")
        return None

    try:
        _llm_provider = ProviderRegistry.get_provider()
        logger.info("LLM-провайдер успешно инициализирован.")
    except Exception as e:  # pragma: no cover
        logger.warning("Не удалось инициализировать LLM-провайдера: %s", e)
        _llm_provider = None

    return _llm_provider


# ---------------------------
# Вспомогательные функции JSON
# ---------------------------
def _strip_code_fences(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9]*", "", s).strip()
    if s.endswith("```"):
        s = s[:-3].strip()
    return s


import json
import re

def parse_json_from_text(text: str):
    if not text:
        return None

    t = text.strip()

    # 1) убрать ```json ... ``` или любые ``` ... ```
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*```$", "", t)

    # 2) попытка: найти первый JSON-объект по балансу фигурных скобок
    start = t.find("{")
    if start != -1:
        depth = 0
        for i in range(start, len(t)):
            ch = t[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = t[start:i+1].strip()
                    try:
                        return json.loads(candidate)
                    except Exception:
                        break  # упало, попробуем regex ниже

    # 3) запасной вариант: regex “самый внешний объект”
    m = re.search(r"\{.*\}", t, flags=re.DOTALL)
    if m:
        candidate = m.group(0)
        try:
            return json.loads(candidate)
        except Exception:
            return None

    return None


    def _cleanup_json_string(s: str) -> str:
        s = s.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
        # убираем запятые перед закрывающими скобками: , } или , ]
        s = re.sub(r",\s*(\})", r"\1", s)
        s = re.sub(r",\s*(\])", r"\1", s)
        return s

    # Первая попытка — как есть
    try:
        return json.loads(candidate)
    except Exception:
        pass

    # Вторая попытка — подчищенный JSON + двойные кавычки
    candidate2 = _cleanup_json_string(candidate)
    candidate2 = candidate2.replace("'", '"')
    try:
        return json.loads(candidate2)
    except Exception:
        logger.warning("parse_json_from_text: не смог распарсить JSON")
        return None


def _deep_copy_schema() -> Dict[str, Any]:
    """Безопасный deepcopy схемы, чтобы не ломать шаблон."""
    return copy.deepcopy(UNIFIED_TENDER_SCHEMA)


# ---------------------------
# Разбиение текста на чанки
# ---------------------------
def _split_text(text: str, chunk_size: int = 20000, overlap: int = 500) -> List[str]:
    """
    Режем текст на крупные чанки, чтобы уменьшить количество LLM-запросов.
    """
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks: List[str] = []
    n = len(text)
    start = 0

    while start < n:
        end = min(start + chunk_size, n)
        chunk = text[start:end]
        chunks.append(chunk)
        if end == n:
            break
        start = end - overlap

    return chunks


# ---------------------------
# Fallback-парсер без LLM
# ---------------------------
def _fallback_parse(text: str) -> Dict[str, Any]:
    """
    Универсальный разбор ТЗ без LLM.

    Цель: вытащить максимум здравой информации:
    - заголовок;
    - краткое описание;
    - заказчик (ИНН/КПП/ОГРН, контакты);
    - объект закупки и адрес;
    - перечень работ/товаров с объёмами и единицами, если видны.
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    raw = text or ""

    # ----- 1) Заголовок -----
    title = ""
    for ln in lines[:20]:
        low = ln.lower()
        if any(
            w in low
            for w in [
                "поставка",
                "оказание услуг",
                "оказание услуги",
                "выполнение работ",
                "строитель",
                "ремонт",
            ]
        ):
            title = ln
            break
    if not title and lines:
        title = lines[0]

    title = title.strip().rstrip(" .")

    # ----- 2) Краткое описание -----
    description = ""
    if raw:
        clean = re.sub(r"\s+", " ", " ".join(lines[:50]))
        sentences = re.split(r"[.!?]+\s", clean)
        if len(sentences) >= 3:
            description = ". ".join(sentences[:3])
        else:
            description = clean[:600]

    # ----- 3) Заказчик, ИНН/КПП/ОГРН, контакты -----
    customer_name = ""
    customer_inn = ""
    customer_kpp = ""
    customer_ogrn = ""
    customer_address = ""
    customer_contacts = ""

    for ln in lines:
        low = ln.lower()

        if not customer_name and low.startswith("заказчик"):
            customer_name = ln.split(":", 1)[-1].strip()

        if "инн" in low and not customer_inn:
            nums = re.findall(r"\b\d{10,12}\b", ln)
            if nums:
                customer_inn = nums[0]

        if "кпп" in low and not customer_kpp:
            nums = re.findall(r"\b\d{9}\b", ln)
            if nums:
                customer_kpp = nums[0]

        if "огрн" in low and not customer_ogrn:
            nums = re.findall(r"\b\d{13}\b", ln)
            if nums:
                customer_ogrn = nums[0]

        if "тел" in low or "email" in low or "почт" in low:
            customer_contacts = ln

    # ----- 4) Объект закупки / адрес -----
    object_name = ""
    object_address = ""

    for ln in lines:
        low = ln.lower()
        if low.startswith("объект"):
            object_name = ln.split(":", 1)[-1].strip()
        if "место постав" in low or "адрес" in low:
            parts = ln.split(":", 1)
            if len(parts) == 2:
                object_address = parts[1].strip()

    if not object_name:
        object_name = title

    # ----- 5) Перечень работ (с объёмами и единицами) -----
    works: List[Dict[str, Any]] = []
    pattern = re.compile(
        r"(.+?)\s+([\d\s\.,]+)\s*(шт|штук|м2|м3|м³|м|тонн|т|кг|литр|л|ед|упак|компл|пог\.м|п\.м\.)",
        re.IGNORECASE,
    )

    for ln in lines:
        m = pattern.search(ln)
        if not m:
            continue

        name = m.group(1).strip()
        volume = m.group(2).strip()
        unit = m.group(3)

        works.append(
            {
                "name": name,
                "volume": volume,
                "unit": unit,
                "materials": "",
                "equipment": "",
                "location": "",
                "notes": "",
            }
        )

    # ----- 6) Перечень товаров (если есть таблица с товарами) -----
    goods_items: List[Dict[str, Any]] = []
    goods_pattern = re.compile(
        r"(.+?)\s+([\d\s\.,]+)\s*(шт|штук|м2|м3|м³|м|тонн|т|кг|литр|л|ед|упак|компл)",
        re.IGNORECASE,
    )

    for ln in lines:
        m = goods_pattern.search(ln)
        if not m:
            continue

        name = m.group(1).strip()
        qty = m.group(2).strip()
        unit = m.group(3)

        goods_items.append(
            {
                "name": name,
                "description": "",
                "brand": "",
                "model": "",
                "certificates": "",
                "quantity": qty,
                "unit": unit,
                "requirements": "",
            }
        )

    # ----- 6.1) Специальный случай: количество из "Количество, шт" -----
    qty = ""
    unit_hint = ""
    for idx, ln in enumerate(lines):
        low = ln.lower()
        if "количество" in low and ("шт" in low or "штук" in low or "ед" in low or "компл" in low):
            if "шт" in low or "штук" in low:
                unit_hint = "шт"
            nums = re.findall(r"\b\d{1,6}\b", ln)
            if nums:
                qty = nums[-1]
                break

        if "количество, шт" in low:
            for j in range(idx + 1, min(len(lines), idx + 20)):
                row = lines[j]
                nums = re.findall(r"\b\d{1,6}\b", row)
                if nums:
                    qty = nums[-1]
                    unit_hint = "шт"
                    break
            break

    if qty:
        target_goods = None
        for g in goods_items:
            if not isinstance(g, dict):
                continue
            nm = (g.get("name") or "").lower()
            if any(w in nm for w in ("сваи", "свая", "свай")):
                target_goods = g
                break
        if target_goods is None and goods_items:
            target_goods = goods_items[-1]
        if target_goods is not None:
            target_goods["quantity"] = qty
            if not target_goods.get("unit"):
                target_goods["unit"] = unit_hint or target_goods.get("unit") or "шт"

    # ----- 7) Сборка результата -----
    result = _deep_copy_schema()
    result["title"] = title
    result["description"] = description
    result["customer"]["name"] = customer_name
    result["customer"]["inn"] = customer_inn
    result["customer"]["kpp"] = customer_kpp
    result["customer"]["ogrn"] = customer_ogrn
    result["customer"]["address"] = customer_address
    result["customer"]["contacts"] = customer_contacts

    result["object"]["name"] = object_name
    result["object"]["address"] = object_address

    result["technical"]["works"]["works_list"] = works
    result["goods"]["items"] = goods_items

    result["analysis_meta"]["fallback_used"] = True
    result["analysis_meta"]["fallback_reason"] = "base_fallback"

    return result


# ---------------------------
# Нормализация к схеме
# ---------------------------
def _normalize_to_schema(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Приводим произвольный словарь к UNIFIED_TENDER_SCHEMA:
    - все отсутствующие поля заполняем значениями по умолчанию;
    - дополнительные поля (например, market_analysis, performers_by_task)
      сохраняем как есть и не выкидываем.
    """

    def _fill(template: Any, d: Any) -> Any:
        if isinstance(template, dict):
            out: Dict[str, Any] = {}
            d = d if isinstance(d, dict) else {}

            # 1) заполняем всё, что есть в шаблоне
            for k, v in template.items():
                out[k] = _fill(v, d.get(k))

            # 2) аккуратно добавляем «лишние» ключи из исходного словаря
            for k, v in d.items():
                if k not in template:
                    out[k] = v

            return out

        elif isinstance(template, list):
            if isinstance(d, list):
                return d
            return copy.deepcopy(template)

        else:
            return d if d not in (None, "") else template

    return _fill(UNIFIED_TENDER_SCHEMA, data)


# ---------------------------
# Красивый заголовок
# ---------------------------
def _beautify_title(title: str) -> str:
    """Делаем человеческое название тендера."""
    t = (title or "").strip()
    if not t:
        return ""

    low = t.lower()

    # "Техническое задание\nна поставку ..." -> "Поставка ..."
    if low.startswith("техническое задание"):
        m = re.search(r"на\s+(.+)", t, flags=re.IGNORECASE)
        if m:
            t = m.group(1).strip()
            low = t.lower()

    # "на поставку свай винтовых" -> "Поставка свай винтовых"
    if low.startswith("на "):
        rest = t[3:].strip()
        rlow = rest.lower()
        if rlow.startswith("поставк"):
            t = "Поставка " + rest.split(" ", 1)[1]
        elif rlow.startswith("оказан"):
            t = "Оказание " + rest.split(" ", 1)[1]
        elif rlow.startswith("выполн"):
            t = "Выполнение " + rest.split(" ", 1)[1]
        else:
            t = rest[:1].upper() + rest[1:]

    # сносим ведущую нумерацию "2.2." и т.п.
    t = re.sub(r"^[\d\.\)\s]+", "", t).strip()

    return t[:200]


def _postprocess_fields(
    tender_data: Dict[str, Any],
    fallback_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Старый рабочий постпроцессинг:
    - нормализуем title (убираем ОКПД, хвосты и т.п.)
    - аккуратно заполняем object.name
    - контакты берём только если действительно похожи на контакты
    - обрезаем слишком длинные поля
    """
    td = tender_data

    # --- title ---
    model_title = (td.get("title") or "").strip()
    fb_title = (fallback_data.get("title") or "").strip()
    if model_title:
        td["title"] = _beautify_title(model_title)
    elif fb_title:
        td["title"] = _beautify_title(fb_title)
    else:
        td["title"] = ""

    # --- object.name ---
    obj = td.get("object") or {}
    model_obj_name = (obj.get("name") or "").strip()
    fb_obj = fallback_data.get("object") or {}
    fb_obj_name = (fb_obj.get("name") or "").strip()

    if model_obj_name:
        obj["name"] = _beautify_title(model_obj_name)
    elif fb_obj_name:
        obj["name"] = _beautify_title(fb_obj_name)
    else:
        obj["name"] = ""

    td["object"] = obj

    # --- customer: дозаполнение из fallback, если LLM поле не заполнила ---
    cust = td.get("customer") or {}
    fb_cust = fallback_data.get("customer") or {}

    model_cust_name = (cust.get("name") or "").strip()
    fb_cust_name = (fb_cust.get("name") or "").strip()

    model_cust_addr = (cust.get("address") or "").strip()
    fb_cust_addr = (fb_cust.get("address") or "").strip()

    if not model_cust_name and fb_cust_name:
        cust["name"] = fb_cust_name

    if not model_cust_addr and fb_cust_addr:
        cust["address"] = fb_cust_addr

    td["customer"] = cust

    # --- contacts: берём LLM-версию только если она реально похожа на контакты ---
    fb_contacts = (fallback_data.get("customer", {}).get("contacts") or "").strip()
    llm_contacts = (td.get("customer", {}).get("contacts") or "").strip()
    td.setdefault("customer", {})
    # --- customer: если LLM не заполнил имя/адрес, аккуратно добираем из fallback ---
    cust = td.get("customer") or {}
    fb_cust = fallback_data.get("customer") or {}

    model_cust_name = (cust.get("name") or "").strip()
    fb_cust_name = (fb_cust.get("name") or "").strip()

    model_cust_addr = (cust.get("address") or "").strip()
    fb_cust_addr = (fb_cust.get("address") or "").strip()

    if not model_cust_name and fb_cust_name:
        cust["name"] = fb_cust_name

    if not model_cust_addr and fb_cust_addr:
        cust["address"] = fb_cust_addr

    td["customer"] = cust

    # --- contacts: берём LLM-версию только если она реально похожа на контакты ---
    fb_contacts = (fallback_data.get("customer", {}).get("contacts") or "").strip()
    llm_contacts = (td.get("customer", {}).get("contacts") or "").strip()
    td.setdefault("customer", {})

    def _looks_like_contacts(s: str) -> bool:
        if not s:
            return False
        low = s.lower()
        if any(tok in low for tok in ("тел", "phone", "факс", "e-mail", "email")):
            return True
        if "@" in low:
            return True
        if re.search(r"\+7\d{10}", low) or re.search(r"\b8\d{10}\b", low):
            return True
        return False

    if _looks_like_contacts(llm_contacts):
        td["customer"]["contacts"] = llm_contacts
    elif _looks_like_contacts(fb_contacts):
        td["customer"]["contacts"] = fb_contacts
    else:
        td["customer"]["contacts"] = ""

    # остальное – просто обрезаем до разумной длины
    def _trim(v: str, max_len: int = 260) -> str:
        v = (v or "").strip()
        return v if len(v) <= max_len else v[: max_len - 3].rstrip() + "..."

    td["description"] = _trim(td.get("description", ""))
    td["customer"]["name"] = _trim(td.get("customer", {}).get("name", ""))
    td["customer"]["address"] = _trim(td.get("customer", {}).get("address", ""))
    td["object"]["address"] = _trim(td.get("object", {}).get("address", ""))

    return td


# ---------------------------
# LLM: построение сообщений
# ---------------------------
def _build_llm_messages(chunk_text: str, user_city: Optional[str]) -> List[Dict[str, str]]:
    schema_text = json.dumps(UNIFIED_TENDER_SCHEMA, ensure_ascii=False, indent=2)

    system_msg = (
        "Ты — эксперт по анализу тендерной документации (44-ФЗ, 223-ФЗ и др.).\n"
        "Твоя задача — извлечь структурированные данные из фрагмента тендерных документов "
        "и вернуть СТРОГО ОДИН JSON-объект, строго соответствующий заданной схеме.\n\n"
        "Документы могут быть любыми: техническое задание, проект контракта, извещение, "
        "сведения о закупке, требования к заявке, НМЦК, переписка и т.п.\n\n"
        "Особенно внимательно заполняй поля верхней таблицы отчёта:\n"
        " • 'title' — краткое название закупки (например, 'Поставка свай винтовых'). "
        "Не включай коды ОКПД, длинные юридические формулировки и лишние подробности.\n"
        " • 'description' — 1–3 предложения, объясняющих суть закупки простым языком.\n"
        " • 'customer.name' — официальное наименование заказчика. Ищи его в преамбуле "
        "контрактов, в извещении, в реквизитах и т.п.\n"
        " • 'customer.address' — официальный адрес заказчика (индекс, город, улица и т.п.). "
        "Допустимо брать его из раздела о месте поставки, если он явно относится к заказчику.\n"
        " • 'customer.contacts' — контактные данные (ФИО контактного лица, телефон, e-mail). "
        "Никогда не подставляй сюда описание предмета закупки или характеристики товара.\n"
        " • 'object.name' и 'object.address' — объект закупки и адрес объекта/поставки.\n\n"
        "Обязательные правила:\n"
        "1) Верни ТОЛЬКО JSON, без пояснений, комментариев и без ```.\n"
        "2) JSON должен быть корректным и парситься через json.loads без ошибок.\n"
        "3) Если каких-то данных нет в тексте — ставь пустые строки или null, не выдумывай значения.\n"
        "4) Строго соблюдай структуру и типы полей, как в схеме ниже.\n"
        "5) Все суммы указывай в числовом формате без пробелов, для строк валют используй коды "
        "(например, 'RUB').\n\n"
        f"Вот JSON-схема, которой НУЖНО строго следовать:\n{schema_text}"
    )

    user_msg = (
        f"Город/регион закупки (если известен пользователю): {user_city or 'не указан'}.\n\n"
        "Проанализируй приведённый ниже фрагмент тендерной документации и заполни все поля "
        "схемы, которые можно надёжно извлечь из текста. Если данных недостаточно — оставь "
        "соответствующие поля пустыми.\n\n"
        f"Фрагмент тендерной документации:\n{chunk_text}\n\n"
        "Верни ОДИН JSON-объект строго по указанной схеме. Никакого текста до или после JSON."
    )

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def _call_llm_chunk(
    provider: Any,
    chunk_text: str,
    user_city: Optional[str],
    chunk_index: int,
    total_chunks: int,
) -> Optional[Dict[str, Any]]:
    logger.info("LLM call chunk %s/%s", chunk_index, total_chunks)
    messages = _build_llm_messages(chunk_text, user_city)
    try:
        raw = provider.generate(messages=messages, model=MODEL_NAME)
        content = raw["choices"][0]["message"]["content"]
        parsed = parse_json_from_text(content)
        if isinstance(parsed, dict):
            return parsed
        logger.warning("LLM не вернул dict для чанка %s", chunk_index)
        return None
    except Exception as e:  # pragma: no cover
        logger.warning("Ошибка LLM на чанке %s: %s", chunk_index, e)
        return None


# ---------------------------
# Слияние нескольких словарей
# ---------------------------
def _merge_dicts(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    """
    Рекурсивное слияние словарей:
    - словари мёрджим по ключам;
    - списки конкатенируем;
    - скаляры из extra перекрывают base, если не пустые.
    """
    if not isinstance(extra, dict):
        return base

    for k, v in extra.items():
        if isinstance(v, dict):
            node = base.get(k) or {}
            if not isinstance(node, dict):
                node = {}
            base[k] = _merge_dicts(node, v)
        elif isinstance(v, list):
            base_list = base.get(k)
            if isinstance(base_list, list):
                base[k] = base_list + v
            else:
                base[k] = v
        else:
            if v not in ("", None, []):
                base[k] = v

    return base


def _aggregate_tender_dicts(dicts: List[Dict[str, Any]]) -> Dict[str, Any]:
    dicts = [d for d in dicts if isinstance(d, dict) and d]
    if not dicts:
        return _deep_copy_schema()
    agg: Dict[str, Any] = _deep_copy_schema()
    for d in dicts:
        agg = _merge_dicts(agg, d)
    return _normalize_to_schema(agg)


# ---------------------------
# РАСЧЁТ market_analysis
# ---------------------------
def _extract_works_for_pricing(tender_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Универсальный сбор работ для ценового анализа.
    Ищем работы/товары во всех возможных местах, которые может выдать LLM или парсеры:
      - tender_data["goods"]["items"]
      - tender_data["technical"]["works"]["works_list"]
      - tender_data["technical"]["works"]["items"]
      - tender_data["technical"]["items"]
      - tender_data["works"]
    Каждая найденная запись преобразуется в формат:
      { "name": ..., "volume": ..., "unit": ... }
    """
    results: List[Dict[str, Any]] = []

    def add_entry(name, volume, unit):
        """Внутренняя функция для безопасного добавления записи."""
        if not name:
            return
        name = name.strip()
        if len(name) < 3:
            return
        try:
            volume_num = float(str(volume).replace(" ", "").replace(",", "."))
        except Exception:
            volume_num = 1.0
        unit_clean = (unit or "").strip() or "шт"
        results.append(
            {
                "name": name,
                "volume": volume_num,
                "unit": unit_clean,
            }
        )

    # ---------- 1. goods.items ----------
    goods = tender_data.get("goods") or {}
    items = goods.get("items") or []
    if isinstance(items, list):
        for it in items:
            if not isinstance(it, dict):
                continue
            add_entry(
                it.get("name"),
                it.get("quantity") or it.get("volume") or 1,
                it.get("unit"),
            )

    # ---------- 2. technical.works.works_list ----------
    tech = tender_data.get("technical") or {}
    works_block = tech.get("works") or {}
    wl = works_block.get("works_list") or []
    if isinstance(wl, list):
        for w in wl:
            if not isinstance(w, dict):
                continue
            add_entry(
                w.get("name"),
                w.get("volume") or w.get("quantity") or 1,
                w.get("unit"),
            )

    # ---------- 3. technical.works.items ----------
    works_items = works_block.get("items") or []
    if isinstance(works_items, list):
        for w in works_items:
            if not isinstance(w, dict):
                continue
            add_entry(
                w.get("name"),
                w.get("volume") or w.get("quantity") or 1,
                w.get("unit"),
            )

    # ---------- 4. technical.items ----------
    tech_items = tech.get("items") or []
    if isinstance(tech_items, list):
        for it in tech_items:
            if not isinstance(it, dict):
                continue
            add_entry(
                it.get("name"),
                it.get("volume") or it.get("quantity") or 1,
                it.get("unit"),
            )

    # ---------- 5. tender_data["works"] (дополнительный fallback) ----------
    fallback_works = tender_data.get("works") or []
    if isinstance(fallback_works, list):
        for w in fallback_works:
            if not isinstance(w, dict):
                continue
            add_entry(
                w.get("name"),
                w.get("volume") or w.get("quantity") or 1,
                w.get("unit"),
            )

    return results



def _apply_market_analysis(tender: Dict[str, Any], city: Optional[str]) -> None:
    """
    Старая рабочая логика:
    - берём список работ из _extract_works_for_pricing
    - для каждой работы спрашиваем SearchService
    - считаем subtotal и общий итог
    - складываем всё в tender["market_analysis"]["minimum_sum_calculation"]
    """
    works = _extract_works_for_pricing(tender)
    if not works:
        return

    service = SearchService()
    rows: List[Dict[str, Any]] = []
    total_min = 0.0
    total_max = 0.0

    for w in works:
        name = (w.get("name") or "").strip()
        if not name:
            continue

        volume_raw = w.get("volume") or 1
        try:
            volume = float(str(volume_raw).replace(" ", "").replace(",", "."))
        except Exception:
            volume = 1.0

        unit = (w.get("unit") or "").strip() or "шт"

        try:
            price_info = service.search_prices(task=name, city=city or "Россия")
        except Exception as e:  # pragma: no cover
            logger.warning("Ошибка поиска цен для '%s': %s", name, e)
            price_info = None

        if price_info and getattr(price_info, "ok", False) and price_info.price_min is not None:
            pmn = float(price_info.price_min)
            pmx = float(price_info.price_max or price_info.price_min)
            subtotal_min = pmn * volume
            subtotal_max = pmx * volume
            total_min += subtotal_min
            total_max += subtotal_max

            row = {
                "status": "calculated",
                "work_name": name,
                "volume": volume,
                "unit": getattr(price_info, "unit", None) or unit,
                "price_min": pmn,
                "price_max": pmx,
                "subtotal_min": subtotal_min,
                "subtotal_max": subtotal_max,
                "currency": getattr(price_info, "currency", None) or "RUB",
                "confidence": 0.5,
            }
        else:
            row = {
                "status": "no_data",
                "work_name": name,
                "volume": volume,
                "unit": unit,
                "comment": getattr(price_info, "comment", None)
                or "Недостаточно данных для расчёта цен",
            }

        rows.append(row)

    # пишем обратно в tender
    ma = tender.setdefault("market_analysis", {})
    calc = ma.setdefault("minimum_sum_calculation", {})
    calc["total_min"] = round(total_min, 2) if total_min else ""
    calc["total_max"] = round(total_max, 2) if total_max else ""
    calc["currency"] = "RUB"
    calc["confidence"] = "0.5"
    calc["works_breakdown"] = rows
    calc["works_breakdown"] = rows

    # --- ищем исполнителей по каждой задаче через SearchService ---
    performers_by_task: Dict[str, List[Dict[str, Any]]] = {}

    for r in rows:
        if not isinstance(r, dict):
            continue

        work_name = (r.get("work_name") or "").strip()
        if not work_name:
            continue

        unit = (r.get("unit") or "").strip()
        price_min = r.get("price_min")
        price_max = r.get("price_max")
        currency = (r.get("currency") or "RUB").strip() or "RUB"

        # обращаемся к поиску исполнителей в том же городе
        performers = service.search_performers(work_name, city=city or "Россия", limit=5)

        performer_entries: List[Dict[str, Any]] = []

        for perf in performers:
            entry: Dict[str, Any] = {
                "name": perf.name,
                "type": "поставщик",  # при желании можно варьировать
                "profile_url": perf.site,
                "reviews": {
                    "average_rating": perf.rating if perf.rating is not None else "",
                    "reviews": [],
                },
                "prices": [
                    {
                        "value_min": price_min,
                        "value_max": price_max,
                        "unit": unit,
                        "currency": currency,
                        "source": "places_api",
                    }
                ],
                "contacts": {
                    "phone": perf.phone,
                    "email": perf.email,
                },
            }
            performer_entries.append(entry)

        if performer_entries:
            performers_by_task[work_name] = performer_entries

    if performers_by_task:
        ma["performers_by_task"] = performers_by_task

    ma["city"] = city or ""
    ma["search_engine"] = "Tender Search Engine"


# ---------------------------
# ОСНОВНАЯ ФУНКЦИЯ АНАЛИЗА ТЕКСТА
# ---------------------------
def analyze_text(
    text: str,
    user_city: Optional[str] = None,
    use_llm: bool = False,
) -> str:
    """
    Универсальный анализ текста тендерной документации.

    Главное правило: если LLM доступен и смог вернуть валидный JSON,
    ИМЕННО ЕГО результат используется как основа для всех полей (включая шапку).
    Fallback-парсер используется только как запасной источник данных.
    """
    if not text:
        tender_data = _deep_copy_schema()
        return json.dumps(tender_data, ensure_ascii=False, indent=2)

    # 1) Базовый разбор без ИИ (fallback-парсер) — как резерв
    fallback_data = _fallback_parse(text)

    # --- ограничиваем использование LLM для очень длинных текстов ---
    MAX_LLM_TEXT = 60_000  # символов
    if use_llm and len(text or "") > MAX_LLM_TEXT:
        logger.info(
            "Текст длиной %d символов превышает лимит %d, отключаем LLM для ускорения.",
            len(text or ""),
            MAX_LLM_TEXT,
        )
        use_llm = False

    # 2) Если LLM отключен — работаем только на fallback
    if not use_llm:
        tender_data = _normalize_to_schema(fallback_data)
        tender_data = _postprocess_fields(tender_data, fallback_data)
        tender_data.setdefault("analysis_meta", {})
        tender_data["analysis_meta"]["user_city"] = user_city or ""
        _apply_market_analysis(tender_data, user_city)
        return json.dumps(tender_data, ensure_ascii=False, indent=2)

    # 3) Подключаем LLM
    provider = get_llm_provider(enable=True)
    if provider is None:
        logger.warning("LLM недоступен, остаёмся на fallback-анализе.")
        tender_data = _normalize_to_schema(fallback_data)
        tender_data = _postprocess_fields(tender_data, fallback_data)
        tender_data.setdefault("analysis_meta", {})
        tender_data["analysis_meta"]["user_city"] = user_city or ""
        _apply_market_analysis(tender_data, user_city)
        return json.dumps(tender_data, ensure_ascii=False, indent=2)

    # 4) Готовим текст для LLM
    LLM_TEXT_LIMIT = 60_000
    llm_text = (text or "")[:LLM_TEXT_LIMIT]

    chunks = _split_text(llm_text)
    total_chunks = len(chunks)
    logger.info(
        "Запуск анализа. Чанков: %d. use_llm=%s, len(text)=%d, len(llm_text)=%d",
        total_chunks,
        use_llm,
        len(text or ""),
        len(llm_text),
    )

    partial_results: List[Dict[str, Any]] = []
    for idx, chunk_text in enumerate(chunks, start=1):
        dct = _call_llm_chunk(
            provider=provider,
            chunk_text=chunk_text,
            user_city=user_city,
            chunk_index=idx,
            total_chunks=total_chunks,
        )
        if dct:
            partial_results.append(dct)

    # 5) Если LLM смог вернуть хотя бы один JSON — используем его как ОСНОВУ
    if partial_results:
        llm_agg = _aggregate_tender_dicts(partial_results)
        tender_data = _normalize_to_schema(llm_agg)
    else:
        logger.warning("LLM не вернул ни одного валидного JSON, используем только fallback.")
        tender_data = _normalize_to_schema(fallback_data)

    # 6) Финальная зачистка полей (LLM-first, fallback-only-if-empty)
    tender_data = _postprocess_fields(tender_data, fallback_data)
    tender_data.setdefault("analysis_meta", {})
    tender_data["analysis_meta"]["user_city"] = user_city or ""

    # 7) Расчёт цен (market_analysis)
    _apply_market_analysis(tender_data, user_city)

    return json.dumps(tender_data, ensure_ascii=False, indent=2)


# ---------------------------
# ОБЪЕДИНЕНИЕ НЕСКОЛЬКИХ JSON
# ---------------------------
def summarize_jsons(jsons: List[str]) -> str:
    """
    Объединяет несколько JSON-строк в один JSON по унифицированной схеме.
    Используется в app.py после анализа каждого файла.
    """
    tender_dicts: List[Dict[str, Any]] = []
    for s in jsons:
        if not s:
            continue
        try:
            obj = json.loads(s)
        except Exception:
            obj = parse_json_from_text(s)

        if isinstance(obj, dict):
            tender_dicts.append(obj)
        elif isinstance(obj, list):
            tender_dicts.extend([x for x in obj if isinstance(x, dict)])

    agg = _aggregate_tender_dicts(tender_dicts)
    return json.dumps(agg, ensure_ascii=False, indent=2)


# ---------------------------
# ЧАТ С МОДЕЛЬЮ ПО aggregated_tender.json
# ---------------------------
def chat_with_model(user_message: str) -> str:
    """
    Чат по уже сохранённому temp/aggregated_tender.json.
    Если LLM недоступен — возвращаем понятное сообщение.
    """
    from pathlib import Path

    json_path = Path("temp") / "aggregated_tender.json"
    if not json_path.exists():
        return (
            "Файл temp/aggregated_tender.json не найден. "
            "Сначала запустите анализ тендерной документации и сформируйте отчёт."
        )

    try:
        with json_path.open("r", encoding="utf-8") as f:
            tender_data = json.load(f)
    except Exception as e:
        logger.exception("Не удалось прочитать aggregated_tender.json: %s", e)
        return (
            "Не удалось прочитать файл aggregated_tender.json. "
            f"Подробности в логах: {e}"
        )

    provider = get_llm_provider(enable=True)
    if not provider:
        return (
            "LLM сейчас недоступен (скорее всего, не задан OPENROUTER_API_KEY "
            "или MindSearch не сконфигурирован). "
            "Отчёт по тендеру при этом сформирован, но задать уточняющие вопросы к ИИ сейчас нельзя."
        )

    system_msg = (
        "Ты — AI-консультант по тендерам. "
        "Отвечай по-русски, кратко и по делу, опираясь только на переданный JSON "
        "с результатами анализа тендера. Если данных не хватает — честно говори об этом."
    )

    context_msg = (
        "Ниже приведён JSON с результатами анализа тендера. "
        "Используй его для ответа на мой вопрос.\n\n"
        f"{json.dumps(tender_data, ensure_ascii=False, indent=2)}"
    )

    try:
        resp = provider.generate(
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": context_msg},
                {"role": "user", "content": user_message},
            ],
            model=MODEL_NAME,
        )
        answer = resp["choices"][0]["message"]["content"]
        return answer
    except Exception as e:  # pragma: no cover
        logger.exception("Ошибка в chat_with_model: %s", e)
        return (
            "Не удалось обратиться к модели (скорее всего, ошибка OpenRouter/MindSearch: "
            f"{e}). Отчёт по тендеру при этом сформирован в упрощённом режиме."
        )
