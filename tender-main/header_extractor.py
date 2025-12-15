"""
header_extractor.py

Локальный парсер шапки и работ для тендерных документов.

Задачи модуля:
- Выделить из сырого текста документа основные поля шапки:
  * название тендера
  * краткое описание
  * заказчик
  * адрес заказчика
  * контакты заказчика
  * объект закупки
  * адрес объекта

- Найти блок(и) с перечнем работ / спецификацией и извлечь из них
  кандидатов работ (название, объём, единица измерения – по возможности).

- Сформировать удобные текстовые подсказки / контексты для LLM,
  чтобы она доуточняла данные, но НЕ придумывала их с нуля.

Модуль пока автономный: его можно вызывать из ai_services позже.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple
import logging
import re
import itertools

logger = logging.getLogger("TenderAnalyzer")


# ---------------------------------------------------------------------------
#   ДАТАКЛАССЫ
# ---------------------------------------------------------------------------


@dataclass
class TenderHeader:
    """Структура шапки тендера, без лишнего мусора."""

    title: str = ""              # Название тендера / контракта
    description: str = ""        # Краткое описание / предмет закупки
    customer: str = ""           # Наименование заказчика
    customer_address: str = ""   # Адрес заказчика
    customer_contacts: str = ""  # Телефоны, e-mail и т.п.
    object_name: str = ""        # Объект закупки (что именно поставляется/строится)
    object_address: str = ""     # Адрес объекта (место выполнения работ / поставки)


@dataclass
class WorkCandidate:
    """
    Кандидат работы, вытащенный из раздела "Перечень работ / Спецификация".

    name        – текстовое название работ/услуг/товара.
    volume      – строковое представление объёма, если удалось вытащить (иначе "").
    unit        – единица измерения (шт, м2, м3 и т.п.), если удалось вытащить.
    source_line – исходная строка документа (для отладки / LLM).
    """

    name: str
    volume: str = ""
    unit: str = ""
    source_line: str = ""


# ---------------------------------------------------------------------------
#   ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ОЧИСТКИ ТЕКСТА
# ---------------------------------------------------------------------------


_RE_UNDERSCORES = re.compile(r"[_]{2,}")
_RE_MULTI_SPACE = re.compile(r"\s{2,}")
_RE_ONLY_PUNCT = re.compile(r"^[\s\W_]+$")  # строка только из мусорных символов
_RE_LATIN_V_NOISE = re.compile(r"\bV{2,}\b", re.IGNORECASE)  # V V V, VVV и пр.


def _normalize_line(line: str) -> str:
    """
    Нормализует одну строку:
    - убирает длинные цепочки подчёркиваний;
    - чистит "V V V" и подобный шум;
    - схлопывает повторные пробелы;
    - обрезает по краям.
    """
    if not line:
        return ""

    s = line.replace("\t", " ").strip()
    s = _RE_UNDERSCORES.sub(" ", s)
    s = _RE_LATIN_V_NOISE.sub(" ", s)
    s = _RE_MULTI_SPACE.sub(" ", s)
    return s.strip()


def _split_to_lines(text: str, max_lines: int = 300) -> List[str]:
    """
    Разбивает текст на строки и предварительно чистит.

    Ограничивает количество строк (верх документа), чтобы не тащить всё полотно.
    """
    if not text:
        return []

    raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    lines: List[str] = []

    for raw in itertools.islice(raw_lines, 0, max_lines):
        line = _normalize_line(raw)
        if not line:
            continue

        # отбрасываем совсем мусорные строки из одних символов
        if _RE_ONLY_PUNCT.match(line):
            continue

        lines.append(line)

    return lines


def _limit_len(s: str, max_len: int = 260) -> str:
    """Обрезает слишком длинные значения шапки."""
    s = s.strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 3].rstrip() + "..."

def _sanitize_header_value(s: str) -> str:
    """
    Нормализует значение для шапки:
    - убирает лишние кавычки и пробелы по краям;
    - схлопывает повторные пробелы;
    - обрезает по длине.
    Никаких фильтров по «бред/тест» здесь нет — только косметика.
    """
    if not s:
        return ""

    s = s.strip().strip("«»\"'")
    if not s:
        return ""

    s = _RE_MULTI_SPACE.sub(" ", s)
    return _limit_len(s)


def extract_header_from_text(text: str) -> TenderHeader:
    """
    Основная функция: парсинг шапки из сырого текста документа.

    Использует только эвристики и regex, без LLM.
    """
    lines = _split_to_lines(text, max_lines=350)
    logger.info("HeaderExtractor: получено %d строк для парсинга шапки", len(lines))

    if not lines:
        return TenderHeader()

    title, desc = _extract_title_and_description(lines)
    customer, customer_addr = _extract_customer(lines)
    contacts = _extract_contacts(lines)
    obj, obj_addr = _extract_object(lines)

    # Если объект не нашли – используем более "предметное" поле
    if not obj:
        if desc:
            obj = desc
        elif title:
            obj = title

    header = TenderHeader(
        title=title or "",
        description=desc or "",
        customer=customer or "",
        customer_address=customer_addr or "",
        customer_contacts=contacts or "",
        object_name=obj or "",
        object_address=obj_addr or "",
    )

    logger.info(
        "HeaderExtractor: шапка распознана: %s",
        {k: v for k, v in asdict(header).items() if v},
    )
    return header

# ---------------------------------------------------------------------------
#   ПАРСИНГ ШАПКИ
# ---------------------------------------------------------------------------
# Простые эвристики по ключевым словам
_RE_TITLE_LINE = re.compile(
    r"(?i)\b(проект\s+контракта|контракт|договор)\b.*"
)
_RE_CUSTOMER_LABEL = re.compile(r"(?i)\bзаказчик\b[:\-–]?\s*(.+)?")
_RE_CUSTOMER_ADDR_LABEL = re.compile(
    r"(?i)(адрес заказчика|место нахождения заказчика)\b[:\-–]?\s*(.+)?"
)
_RE_OBJECT_LABEL = re.compile(r"(?i)\bобъект\b[:\-–]?\s*(.+)?")
_RE_OBJECT_ADDR_LABEL = re.compile(
    r"(?i)(адрес объекта|место выполнения работ|место поставки товара)\b[:\-–]?\s*(.+)?"
)
_RE_PHONE_LINE = re.compile(r"(?i)\b(тел\.?|телефон|факс|e-mail|email|почта)\b")

# Адрес – более широкий, чем раньше
_RE_ADDRESS_GENERIC = re.compile(
    r"(?i)("
    r"\b\d{5,6}\b.*\b(г\.|город|респ\.|край|обл\.|ул\.|улица|пр-кт|проспект|пер\.|переулок|шоссе)\b"
    r"|"
    r"\b(г\.|город)\s+[^,\n]+(ул\.|улица|пр-кт|проспект|пер\.|переулок|шоссе)\s+[^,\n]+"
    r")"
)

# Маркеры названий организаций
_RE_ORG_HINT = re.compile(
    r"(?i)\b("
    r"ооо|оао|пао|зао|ао|ип|муп|гуп|"
    r"администраци[яи]|министерств[оа]|комитет|департамент|"
    r"муниципальн\w+\s+учреждени\w+|"
    r"государственн\w+\s+учреждени\w+|"
    r"федеральн\w+\s+государственн\w+\s+учреждени\w+"
    r")\b"
)


def _extract_title_and_description(lines: List[str]) -> Tuple[str, str]:
    """
    Пытается найти строку с названием тендера и кратким описанием.
    Возвращает (title, description).

    Требования:
    - title — максимально полный "официальный" заголовок;
    - description — более короткое, предметное описание, НЕ совпадающее дословно с title.
    """
    title = ""
    desc = ""

    # 1. Сначала ищем "проект контракта / контракт / договор"
    for idx, line in enumerate(lines[:80]):
        m = _RE_TITLE_LINE.search(line)
        if m:
            norm = _normalize_line(line)
            if len(norm) < 10:
                continue
            title = _limit_len(norm)

            m2 = re.search(r"на\s+(.+)", norm, flags=re.IGNORECASE)
            if m2:
                desc = _limit_len("на " + m2.group(1))
            elif idx + 1 < len(lines):
                next_line = _normalize_line(lines[idx + 1])
                if len(next_line) >= 25:
                    desc = _limit_len(next_line)
            break

    # 2. Если не нашли — ТЗ / извещение
    if not title:
        for idx, line in enumerate(lines[:80]):
            norm = _normalize_line(line)
            low = norm.lower()
            if "техническое задание" in low or "извещение о проведении" in low:
                if len(norm) < 15:
                    continue
                title = _limit_len(norm)

                m2 = re.search(r"на\s+(.+)", norm, flags=re.IGNORECASE)
                if m2:
                    desc = _limit_len("на " + m2.group(1))
                elif idx + 1 < len(lines):
                    next_line = _normalize_line(lines[idx + 1])
                    if len(next_line) >= 25:
                        desc = _limit_len(next_line)
                break

    # 3. Если ничего не нашли — первая осмысленная строка как title,
    #    следующая осмысленная — как desc.
    if not title:
        first_idx = None
        for idx, line in enumerate(lines[:60]):
            norm = _normalize_line(line)
            if len(norm) >= 25 and not _RE_ONLY_PUNCT.match(norm):
                title = _limit_len(norm)
                first_idx = idx
                break

        if first_idx is not None:
            for line in lines[first_idx + 1 : first_idx + 10]:
                norm = _normalize_line(line)
                if len(norm) >= 25 and norm != title:
                    desc = _limit_len(norm)
                    break

    # 4. Финальная обработка description.
    if title and (not desc or desc == title):
        # для описания убираем технические хвосты из title (Код ОКПД и т.п.)
        tmp = re.split(r"Код\s+ОКПД|ОКПД\s*2|ОКПД2", title, maxsplit=1, flags=re.IGNORECASE)[0]
        m2 = re.search(r"на\s+(.+)", tmp, flags=re.IGNORECASE)
        if m2:
            desc = _limit_len("на " + m2.group(1))
        else:
            desc = _limit_len(tmp)

    return _sanitize_header_value(title), _sanitize_header_value(desc)


def _looks_like_real_customer(candidate: str) -> bool:
    """
    Проверяет, похоже ли содержимое после слова «Заказчик»
    на реальное название организации, а не на текст обязательств.
    """
    s = (candidate or "").strip()
    if not s:
        return False

    s_low = s.lower()

    # 1. Если есть маркеры ОРГ (ООО, АО, администрация и т.п.) — сразу ок.
    if _RE_ORG_HINT.search(s_low):
        return True

    # 2. Явно "плохие" фразы — это не название юрлица.
    bad_phrases = [
        "в течение",            # "Заказчик в течение 2 (двух) рабочих дней сообщает ..."
        "рабочих дней",
        "поставщик обязан",
        "обязан произвести замену",
        "обязан уведомить",
        "вправе",
        "сообщает",
        "уведомить",
        "посредством почты",
        "посредством электронной почты",
        "в письменной форме",
    ]
    if any(bp in s_low for bp in bad_phrases):
        return False

    # 3. Слишком длинные простыни редко бывают названием организации.
    if len(s) > 200:
        return False

    return True



def _extract_customer(lines: List[str]) -> Tuple[str, str]:
    """
    Пытается вытащить название заказчика и его адрес.
    Возвращает (customer, customer_address).

    Приоритет:
    1) строка 'Заказчик: ...';
    2) конструкция 'именуемое в дальнейшем "Заказчик"';
    3) любое юрлицо (ООО/АО/Администрация...), даже без слова 'Заказчик';
    4) адрес по явной метке 'Адрес заказчика';
    5) любой ярко выраженный адрес в верхней части документа.
    """
    customer = ""
    addr = ""

    # 1. 'Заказчик: ...'
    for line in lines[:120]:
        m = _RE_CUSTOMER_LABEL.search(line)
        if m and m.group(1):
            candidate = m.group(1).strip()
            if _looks_like_real_customer(candidate):
                customer = _limit_len(candidate)
                break

    # 2. 'именуемое в дальнейшем "Заказчик"'
    if not customer:
        for i, line in enumerate(lines[:120]):
            low = line.lower()
            if "именуем" in low and "заказчик" in low:
                if i > 0:
                    prev = lines[i - 1].strip()
                    if prev:
                        customer = _limit_len(prev)
                        break
                customer = _limit_len(line)
                break

    # 3. fallback — ищем хоть какое-то юрлицо
    if not customer:
        org_candidate = _find_org_like_in_lines(lines)
        if org_candidate:
            customer = org_candidate

    # 4. Адрес заказчика по явной метке
    for line in lines[:160]:
        m = _RE_CUSTOMER_ADDR_LABEL.search(line)
        if m:
            if m.group(2):
                addr = _limit_len(m.group(2))
            else:
                idx = lines.index(line)
                for extra in lines[idx + 1 :]:
                    if len(extra.strip()) < 10:
                        continue
                    addr = _limit_len(extra)
                    break
            break

    # 5. Общий адрес, если метки нет
    if not addr:
        for line in lines[:160]:
            if _RE_ADDRESS_GENERIC.search(line):
                addr = _limit_len(line)
                break

    return _sanitize_header_value(customer), _sanitize_header_value(addr)



def _find_org_like_in_lines(lines: List[str], max_lines: int = 160) -> str:
    """
    Пытается найти строку, похожую на название организации,
    даже если нет явной метки 'Заказчик:'.
    """
    for line in lines[:max_lines]:
        raw = line.strip()
        if len(raw) < 6 or len(raw) > 220:
            continue
        if _RE_ONLY_PUNCT.match(raw):
            continue
        if _RE_PHONE_LINE.search(raw):
            continue

        low = raw.lower()
        if _RE_ORG_HINT.search(low):
            return _limit_len(raw)
    return ""


def _extract_contacts(lines: List[str]) -> str:
    """
    Ищет строку с телефонами / email / указанием контактного лица
    в верхней части документа.

    Важно: если ничего подобного нет, возвращает пустую строку,
    чтобы в таблицу не попадали заголовки таблиц и прочий мусор.
    """
    # сначала пытаемся найти строку, где есть и email/телефон, и, возможно, ФИО
    for line in lines[:250]:
        norm = _normalize_line(line)
        low = norm.lower()
        if _RE_PHONE_LINE.search(low) or "контактное лицо" in low or "ответственный" in low:
            # отбрасываем совсем короткие и странные строки
            if len(norm) < 8:
                continue
            return _limit_len(norm)

    return ""


def _extract_object(lines: List[str]) -> Tuple[str, str]:
    """
    Пытается вытащить объект закупки и адрес объекта.
    Возвращает (object_name, object_address).
    """
    obj = ""
    obj_addr = ""

    # 1. Явная строка "Объект: ..."
    for line in lines[:180]:
        m = _RE_OBJECT_LABEL.search(line)
        if m and m.group(1):
            obj = _limit_len(m.group(1).strip())
            break

    # 2. Если объекта нет — пробуем вытащить фразы "Объект закупки / Предмет контракта"
    if not obj:
        for line in lines[:200]:
            low = line.lower()
            if "объект закупки" in low or "предмет контракта" in low:
                norm = _normalize_line(line)
                m = re.search(r":\s*(.+)", norm)
                obj = _limit_len(m.group(1) if m else norm)
                break

    # 3. Если всё равно нет — возьмём фразу "на ..." из заголовка/описания позже (в extract_header_from_text)

    # 4. Адрес объекта по явной метке
    for line in lines[:220]:
        m = _RE_OBJECT_ADDR_LABEL.search(line)
        if m:
            if m.group(2):
                obj_addr = _limit_len(m.group(2))
            else:
                idx = lines.index(line)
                for extra in lines[idx + 1 :]:
                    if len(extra.strip()) < 10:
                        continue
                    obj_addr = _limit_len(extra)
                    break
            break

    # 5. Общий адрес, если метки нет
    if not obj_addr:
        for line in lines[:220]:
            if _RE_ADDRESS_GENERIC.search(line):
                obj_addr = _limit_len(line)
                break

    return _sanitize_header_value(obj), _sanitize_header_value(obj_addr)

# ---------------------------------------------------------------------------
#   ПОИСК И ПАРСИНГ РАЗДЕЛА "ПЕРЕЧЕНЬ РАБОТ / СПЕЦИФИКАЦИЯ"
# ---------------------------------------------------------------------------


_WORKS_SECTION_RE = re.compile(
    r"(?i)(перечень работ|спецификац[ияи]|ведомость объем[ао]в работ|локальная смета)"
)

# Строки-шапки таблиц, которые лучше пропустить как служебные.
_WORKS_HEADER_HINTS = (
    "наименование работ",
    "вид работ",
    "ед.",
    "объем",
    "кол-во",
    "количество",
)


def _find_works_section_indices(lines: List[str]) -> Optional[Tuple[int, int]]:
    """
    Ищет диапазон строк, относящихся к перечню работ.

    Возвращает (start_idx, end_idx) или None.
    """
    start_idx = None
    for i, line in enumerate(lines):
        if _WORKS_SECTION_RE.search(line):
            start_idx = i
            break

    if start_idx is None:
        return None

    # Вперёд от заголовка – пока встречаются непустые строки.
    # Ограничим длину секции, чтобы не захватить весь документ.
    end_idx = min(len(lines), start_idx + 200)
    # Дополнительный "тормоз": если после 5+ подряд пустых строк – считаем, что секция закончилась.
    empty_streak = 0
    for j in range(start_idx + 1, end_idx):
        if not lines[j].strip():
            empty_streak += 1
            if empty_streak >= 5:
                end_idx = j
                break
        else:
            empty_streak = 0

    return start_idx, end_idx


_num_re = re.compile(r"(?:(\d+(?:[.,]\d+)*)\s*(шт|м2|м3|м|тн|тонн[аы]?|кг|компл\.?|ед\.?))", re.IGNORECASE)


def _parse_work_line(line: str) -> WorkCandidate:
    """
    Пробует вытащить из строки:
    - название работы,
    - объём + единицу измерения (если распознались).

    Логика:
    - если есть " | " — считаем, что это строка таблицы; первая ячейка — название,
      где-то дальше – числа и единицы.
    - иначе – берём строку как название и ищем число+единицу.
    """
    original = line
    # Режем служебные номера вида "1.", "2)" в начале
    line = line.strip()
    line = re.sub(r"^\s*\d+[\.\)]\s*", "", line)

    cells = [c.strip() for c in line.split("|")] if "|" in line else [line]
    cells = [c for c in cells if c]

    name = ""
    volume = ""
    unit = ""

    if not cells:
        return WorkCandidate(name="", source_line=original)

    # первая ненулевая ячейка — кандидат названия
    name = cells[0]

    # поиск объёма+единицы по всем ячейкам
    for c in cells[1:]:
        m = _num_re.search(c)
        if m:
            volume = m.group(1).replace(",", ".")
            unit = m.group(2)
            break

    # если объём не нашли, пробуем по всей строке
    if not volume:
        m = _num_re.search(line)
        if m:
            volume = m.group(1).replace(",", ".")
            unit = m.group(2)

    return WorkCandidate(
        name=_limit_len(name),
        volume=volume,
        unit=unit,
        source_line=original,
    )


def extract_work_candidates(text: str) -> List[WorkCandidate]:
    """
    Основная функция: ищет блок с перечнем работ и возвращает список WorkCandidate.

    Если раздел не найден – возвращает пустой список.
    """
    lines = _split_to_lines(text, max_lines=2000)  # для работ нужно больше строк
    if not lines:
        return []

    idx_range = _find_works_section_indices(lines)
    if not idx_range:
        logger.info("HeaderExtractor: раздел 'Перечень работ' не найден.")
        return []

    start_idx, end_idx = idx_range
    section_lines = lines[start_idx:end_idx]

    logger.info(
        "HeaderExtractor: найден раздел работ, строки %d–%d (всего %d)",
        start_idx,
        end_idx,
        len(section_lines),
    )

    candidates: List[WorkCandidate] = []
    for line in section_lines:
        l = line.lower()
        # пропускаем строку-заголовок таблицы
        if any(hint in l for hint in _WORKS_HEADER_HINTS):
            continue
        # пропускаем совсем короткий мусор
        if len(line) < 5:
            continue
        wc = _parse_work_line(line)
        if wc.name:
            candidates.append(wc)

    logger.info("HeaderExtractor: найдено %d кандидатов работ", len(candidates))
    return candidates


# ---------------------------------------------------------------------------
#   ПОДСКАЗКИ / КОНТЕКСТ ДЛЯ LLM
# ---------------------------------------------------------------------------


HEADER_SYSTEM_PROMPT = (
    "Ты — помощник по анализу российских тендерных документов. "
    "Твоя задача — аккуратно извлекать информацию из текста договора / контракта, "
    "не добавляя того, чего нет явно в документе. Если данные не указаны, "
    "нужно честно оставлять поле пустым."
)

WORKS_SYSTEM_PROMPT = (
    "Ты — помощник по анализу перечня работ в российских тендерных документах. "
    "Твоя задача — выделять отдельные виды работ, их объём и единицу измерения. "
    "Не группируй разные работы в одну строку. Не выдумывай значения, которых нет в тексте."
)


def build_header_llm_prompt(raw_header_fragment: str, prefilled: TenderHeader) -> str:
    """
    Формирует текст запроса к LLM для уточнения шапки.

    На вход можно дать:
    - небольшой фрагмент исходного текста (первые 100–150 строк),
    - уже предварительно распознанную шапку (prefilled),
      чтобы модель не придумывала новые значения, а только поправила / дополнила.
    """
    lines = [
        "Ниже приведён фрагмент тендерной документации.",
        "Извлеки из него следующие поля шапки (если они явно указаны):",
        "1) Название тендера",
        "2) Краткое описание / предмет закупки",
        "3) Заказчик",
        "4) Адрес заказчика",
        "5) Контакты заказчика (телефон, e-mail и т.п.)",
        "6) Объект закупки (что именно поставляется / выполняется)",
        "7) Адрес объекта (место выполнения работ / поставки)",
        "",
        "Если каких-то данных нет в тексте, верни для них пустую строку.",
        "Используй формат JSON с ключами:",
        '  {"title": "...", "description": "...", "customer": "...",',
        '   "customer_address": "...", "customer_contacts": "...",',
        '   "object_name": "...", "object_address": "..."}',
        "",
    ]

    # Добавим уже найденные эвристикой значения как подсказку.
    pre = {k: v for k, v in asdict(prefilled).items() if v}
    if pre:
        lines.append("Для ориентира уже найдены такие значения (их можно аккуратно скорректировать):")
        for k, v in pre.items():
            lines.append(f"- {k}: {v}")
        lines.append("")

    lines.append("Фрагмент документа:")
    lines.append(raw_header_fragment)

    return "\n".join(lines)


def build_works_llm_prompt(raw_works_fragment: str, candidates: List[WorkCandidate]) -> str:
    """
    Формирует текстовый prompt для LLM по разделу 'Перечень работ'.

    На вход:
    - raw_works_fragment — текстовый блок, где находится спецификация;
    - candidates — уже выделенные строками кандидаты работ (для ориентира).
    """
    lines = [
        "Ниже приведён фрагмент тендерной документации с перечнем работ / спецификацией.",
        "Выдели из него отдельные виды работ с объёмами и единицами измерения.",
        "",
        "Верни результат в формате JSON-массива, например:",
        '[{"name": "Поставка свай винтовых", "volume": 10, "unit": "шт"}, ...]',
        "",
        "Требования:",
        "- не объединяй разные виды работ в одну строку;",
        "- не выдумывай объёмы и единицы измерения — только то, что явно указано;",
        "- если объём/единица не указаны, верни null для соответствующего поля.",
        "",
    ]

    if candidates:
        lines.append("Уже автоматически выделены такие кандидаты работ (их можно уточнить):")
        for c in candidates[:15]:
            lines.append(f'- "{c.name}" (объём: {c.volume or "?"} {c.unit or ""})')
        if len(candidates) > 15:
            lines.append(f"... и ещё {len(candidates) - 15} строк(и).")
        lines.append("")

    lines.append("Фрагмент документа:")
    lines.append(raw_works_fragment)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
#   РЕЖИМ ОТЛАДКИ
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    """
    Небольшой ручной тест:
    python header_extractor.py path_to_txt_or_doc_converted.txt
    (Ожидается, что текст уже прочитан read_services и передан сюда.)
    """
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if len(sys.argv) < 2:
        print("Укажите путь к текстовому файлу с тендером.")
        sys.exit(0)

    path = sys.argv[1]
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        txt = f.read()

    header = extract_header_from_text(txt)
    works = extract_work_candidates(txt)

    print("=== HEADER ===")
    for k, v in asdict(header).items():
        print(f"{k}: {v}")

    print("\n=== WORK CANDIDATES (первые 20) ===")
    for w in works[:20]:
        print(f"- {w.name} | {w.volume} {w.unit}")
