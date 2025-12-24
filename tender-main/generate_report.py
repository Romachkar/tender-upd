import logging
import os
import json
import re
import html
from urllib.parse import quote_plus

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import (
    Table,
    TableStyle,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.fonts import addMapping

print("=== USING CORRECT generate_report.py ===")

logger = logging.getLogger(__name__)


def make_multiline_paragraph(lines_html, style):
    """Делает Paragraph с переносами строк (<br/>) из списка HTML-строк."""
    if not lines_html:
        return Paragraph("", style)
    text = "<br/>".join(lines_html)
    return Paragraph(text, style)


def sanitize_text_for_paragraph(text):
    """Очищает текст для безопасного использования в Paragraph/таблицах."""
    if text is None:
        return ""

    try:
        text = str(text)
    except Exception:
        text = ""

    cleaned_chars = []
    for ch in text:
        code = ord(ch)

        # переводы строк/табуляции → пробел
        if ch in ("\n", "\r", "\t"):
            cleaned_chars.append(" ")
            continue

        # обычный пробел
        if ch == " ":
            cleaned_chars.append(" ")
            continue

        # латиница + цифры
        if ("0" <= ch <= "9") or ("A" <= ch <= "Z") or ("a" <= ch <= "z"):
            cleaned_chars.append(ch)
            continue

        # базовая пунктуация и служебные символы
        if ch in ",.;:!?\"'()[]{}-_/№%«»":
            cleaned_chars.append(ch)
            continue

        # кириллица
        if 0x0400 <= code <= 0x04FF:
            cleaned_chars.append(ch)
            continue

        # всё остальное — шум → пробел
        cleaned_chars.append(" ")

    text = "".join(cleaned_chars)
    text = re.sub(r"\s+", " ", text).strip()
    text = html.escape(text)

    MAX_LEN = 1500
    if len(text) > MAX_LEN:
        text = text[:MAX_LEN] + "..."

    return text


def setup_fonts():
    """Настройка шрифтов для поддержки русского языка."""
    try:
        import platform

        if platform.system() == "Windows":
            font_paths = [
                "C:/Windows/Fonts/arial.ttf",
                "C:/Windows/Fonts/calibri.ttf",
                "C:/Windows/Fonts/tahoma.ttf",
            ]
            for font_path in font_paths:
                if os.path.exists(font_path):
                    pdfmetrics.registerFont(TTFont("RussianFont", font_path))
                    addMapping("RussianFont", 0, 0, "RussianFont")
                    return "RussianFont"
    except Exception as e:
        logger.warning("Не удалось настроить русские шрифты: %s", e)

    # fallback
    return "Helvetica"


def _ensure_dict(value):
    return value if isinstance(value, dict) else {}


def _safe_number(val, default=0.0):
    try:
        if val is None:
            return default
        if isinstance(val, (int, float)):
            return float(val)
        s = str(val)
        # убираем все виды пробелов (включая NBSP/узкий)
        s = re.sub(r"[\s\u00A0\u202F]+", "", s)
        s = s.replace(",", ".")
        # вычищаем валюты/буквы, если вдруг прилетели
        s = re.sub(r"[^\d.\-]+", "", s)
        if not s:
            return default
        return float(s)
    except Exception:
        return default


def _extract_performers_by_task(tender_data):
    """Достаём performers_by_task, если есть."""
    performers = tender_data.get("performers_by_task")
    if not isinstance(performers, dict):
        ma = _ensure_dict(tender_data.get("market_analysis", {}))
        performers = ma.get("performers_by_task", {})
    if not isinstance(performers, dict):
        performers = {}
    return performers


def extract_works(tender_data):
    """Универсальное извлечение работ из разных блоков JSON."""
    works = []

    # 1. Товары → работы
    goods = _ensure_dict(tender_data.get("goods", {})).get("items", [])
    if isinstance(goods, list):
        for g in goods:
            if not isinstance(g, dict):
                continue
            name = (g.get("name") or "").strip()
            qty = (g.get("quantity") or "").strip()
            unit = (g.get("unit") or "").strip()
            if name and qty:
                works.append({"name": name, "volume": qty, "unit": unit or "шт"})

    # 2. Работы из расчёта бюджета
    ma = _ensure_dict(tender_data.get("market_analysis", {}))
    calc = _ensure_dict(ma.get("minimum_sum_calculation", {}))
    wb = calc.get("works_breakdown", [])
    if isinstance(wb, list):
        for r in wb:
            if not isinstance(r, dict):
                continue
            name = (r.get("work_name") or r.get("name") or "").strip()
            if not name:
                continue
            volume = r.get("volume", "") or r.get("qty", "") or r.get("quantity", "")
            unit = r.get("unit", "") or r.get("unit_short", "")
            works.append({"name": name, "volume": volume, "unit": unit})

    # 3. То, что LLM положила в technical.works
    technical = _ensure_dict(tender_data.get("technical", {}))
    works_raw = technical.get("works", [])
    if isinstance(works_raw, list):
        src_list = works_raw
    elif isinstance(works_raw, dict):
        src_list = works_raw.get("works_list") or works_raw.get("items") or []
    else:
        src_list = []

    if isinstance(src_list, list):
        for w in src_list:
            if not isinstance(w, dict):
                continue
            name = (w.get("name") or "").strip()
            if not name:
                continue
            works.append(
                {"name": name, "volume": w.get("volume", ""), "unit": w.get("unit", "")}
            )

    # Убираем дубли: агрегируем по (name, unit), объёмы суммируем, если это числа
    aggregated = {}
    for w in works:
        name = (w.get("name") or "").strip()
        unit = (w.get("unit") or "").strip()
        volume_raw = str(w.get("volume", "")).strip()
        key = (name, unit)

        if key not in aggregated:
            aggregated[key] = {"name": name, "volume": volume_raw, "unit": unit}
        else:
            existing = aggregated[key]
            old_vol = str(existing.get("volume", "")).strip()
            if volume_raw:
                try:
                    v_new = float(volume_raw.replace(" ", "").replace(",", "."))
                    v_old = float(old_vol.replace(" ", "").replace(",", ".")) if old_vol else 0.0
                    total = v_new + v_old
                    if abs(total - int(total)) < 1e-6:
                        existing["volume"] = str(int(total))
                    else:
                        existing["volume"] = f"{total:.2f}".rstrip("0").rstrip(".")
                except Exception:
                    # если числа не парсятся — оставляем первое ненулевое значение
                    if not old_vol:
                        existing["volume"] = volume_raw

    return list(aggregated.values())


def _build_performers_lines(work_name, performers_data, search_city):
    """Возвращает список HTML-строк для ячейки 'Исполнители...'."""
    work_name = (work_name or "").strip()
    if not work_name:
        return []

    lines = []

    # 1. Реальные исполнители (Яндекс или Avito fallback)
    if isinstance(performers_data, dict) and performers_data.get(work_name):
        perfs = performers_data.get(work_name) or []
        if isinstance(perfs, list):
            for p in perfs[:5]:
                if not isinstance(p, dict):
                    continue

                nm = (p.get("name") or "").strip()
                contacts = p.get("contacts") or {}
                phone = ""
                email = ""
                if isinstance(contacts, dict):
                    phone = (contacts.get("phone") or "").strip()
                    email = (contacts.get("email") or "").strip()
                elif isinstance(contacts, str):
                    phone = contacts.strip()

                link = (p.get("profile_url") or p.get("site") or "").strip()

                price_str = ""
                raw_prices = p.get("prices") or []
                if isinstance(raw_prices, list) and raw_prices:
                    pr = raw_prices[0]
                    if isinstance(pr, dict):
                        vmin = pr.get("value_min")
                        vmax = pr.get("value_max")
                        v = pr.get("value")
                        unit_price = (pr.get("unit") or "").strip()
                        if vmin is not None and vmax is not None:
                            price_str = (
                                f"{_safe_number(vmin):,.0f}-"
                                f"{_safe_number(vmax):,.0f} {unit_price}"
                            ).strip()
                        elif v is not None:
                            price_str = f"{_safe_number(v):,.0f} {unit_price}".strip()

                parts = []
                if nm:
                    parts.append(sanitize_text_for_paragraph(nm))
                if phone:
                    parts.append("тел: " + sanitize_text_for_paragraph(phone))
                if email:
                    parts.append("email: " + sanitize_text_for_paragraph(email))
                if link:
                    href = html.escape(link, quote=True)
                    parts.append(f"<link href='{href}'>ссылка</link>")
                if price_str:
                    parts.append("цена: " + sanitize_text_for_paragraph(price_str))

                if parts:
                    lines.append("; ".join(parts))

    # 2. Fallback — общие ссылки на поиск, если реальных исполнителей нет
    if not lines:
        city_part = (" " + search_city) if search_city else ""
        query = quote_plus(f"{work_name}{city_part}")
        avito_url = f"https://www.avito.ru/rossiya?q={query}"
        yandex_url = f"https://yandex.ru/search/?text={query}"
        google_url = f"https://www.google.com/search?q={query}"

        avito_href = html.escape(avito_url, quote=True)
        ya_href = html.escape(yandex_url, quote=True)
        g_href = html.escape(google_url, quote=True)

        lines.append(
            "Поиск исполнителей: "
            f"<link href='{avito_href}'>Avito</link>; "
            f"<link href='{ya_href}'>Yandex</link>; "
            f"<link href='{g_href}'>Google</link>"
        )

    return lines


def generate_pdf_report(tender_json_path: str, output_path: str = "tender_report.pdf"):
    # ---------- загрузка JSON ----------
    with open(tender_json_path, "r", encoding="utf-8") as f:
        tender_data = json.load(f)

    if isinstance(tender_data, str):
        try:
            obj = json.loads(tender_data)
            if isinstance(obj, dict):
                tender_data = obj
            else:
                tender_data = {"title": str(obj)[:200], "description": str(obj)}
        except Exception:
            tender_data = {"title": tender_data[:200], "description": tender_data}

    if not isinstance(tender_data, dict):
        tender_data = {
            "title": "Отчёт по тендеру",
            "description": sanitize_text_for_paragraph(str(tender_data)),
        }

    # ---------- шрифты и стили ----------
    font_name = setup_fonts()
    styles = getSampleStyleSheet()
    styles["Title"].fontName = font_name
    styles["Heading2"].fontName = font_name
    styles["Heading3"].fontName = font_name
    styles["Normal"].fontName = font_name

    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=20,
        rightMargin=20,
        topMargin=30,
        bottomMargin=30,
    )
    story = []

    performers_data = _extract_performers_by_task(tender_data)

    # ---------- заголовок ----------
    story.append(Paragraph("<b>Отчёт по тендеру</b>", styles["Title"]))
    story.append(Spacer(1, 16))

    # ---------- сводная таблица ----------
    customer_raw = tender_data.get("customer", {})
    if isinstance(customer_raw, dict):
        customer_name = customer_raw.get("name", "")
        customer_address = customer_raw.get("address", "")
        customer_contacts = customer_raw.get("contacts", "")
    else:
        customer_name = customer_raw
        customer_address = ""
        customer_contacts = ""

    object_raw = tender_data.get("object", {})
    if isinstance(object_raw, dict):
        object_name = object_raw.get("name", "")
        object_address = object_raw.get("address", "")
    else:
        object_name = object_raw
        object_address = ""

    raw_title = (tender_data.get("title") or "").strip()
    description = (tender_data.get("description") or "").strip()

    def _shorten(txt: str, limit: int = 180) -> str:
        txt = (txt or "").strip()
        if len(txt) <= limit:
            return txt
        cut = txt.rfind(" ", 0, limit)
        if cut == -1:
            cut = limit
        return txt[:cut].rstrip() + "…"

    source_for_title = description or raw_title
    cleaned_title = source_for_title or ""
    if source_for_title:
        m = re.search(
            r"Техническое\s+задание\s+на\s+(.+?)(?:Код\s+ОКПД|ОКПД|1\.\s*Основные|$)",
            source_for_title,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if m:
            phrase = m.group(1).strip().rstrip(" .;,")
            cleaned_title = f"Техническое задание на {phrase}"

    cleaned_title = _shorten(cleaned_title, limit=180)
    cleaned_description = _shorten(description, limit=260)

    obj_name = (object_name or "").strip()
    if not obj_name or "сертификат" in obj_name.lower():
        m = re.search(
            r"Техническое\s+задание\s+на\s+(.+?)(?:Код\s+ОКПД|ОКПД|1\.\s*Основные|$)",
            description,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if m:
            phrase = m.group(1).strip().rstrip(" .;,")
            low = phrase.lower()
            if low.startswith("поставка "):
                obj_name = phrase[0].upper() + phrase[1:]
            elif low.startswith("поставку "):
                obj_name = "Поставка " + phrase[len("поставку "):]
            else:
                obj_name = "Поставка " + phrase
        else:
            obj_name = cleaned_title

    obj_name = _shorten(obj_name, limit=200)

    cust_address_clean = (customer_address or "").strip()
    if not cust_address_clean:
        cust_address_clean = (object_address or "").strip()

    contacts_raw = (customer_contacts or "").strip()
    contacts_low = contacts_raw.lower()
    if not contacts_raw:
        contacts_clean = ""
    elif any(x in contacts_low for x in ("тел", "тел.", "phone", "@", "e-mail", "email")):
        contacts_clean = _shorten(contacts_raw, limit=200)
    else:
        contacts_clean = "Контактные данные явно не указаны в тексте ТЗ."

    summary_fields = [
        ["Название тендера", cleaned_title],
        ["Описание", cleaned_description],
        ["Заказчик", customer_name],
        ["Адрес заказчика", cust_address_clean],
        ["Контакты заказчика", contacts_clean],
        ["Объект", obj_name],
        ["Адрес объекта", object_address],
    ]

    summary_rows = []
    for label, value in summary_fields:
        label_p = Paragraph(f"<b>{sanitize_text_for_paragraph(label)}</b>", styles["Normal"])
        value_p = Paragraph(sanitize_text_for_paragraph(value), styles["Normal"])
        summary_rows.append([label_p, value_p])

    tbl_summary = Table(summary_rows, colWidths=[50 * mm, 140 * mm], hAlign="LEFT")
    tbl_summary.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 1, colors.black),
                ("BACKGROUND", (0, 0), (0, -1), colors.lightgrey),
                ("FONT", (0, 0), (-1, -1), font_name, 11),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.black),
            ]
        )
    )
    story.append(tbl_summary)
    story.append(Spacer(1, 18))

    # ---------- 3. ПЕРЕЧЕНЬ РАБОТ ----------
    story.append(Paragraph("<b>Перечень работ</b>", styles["Heading2"]))
    story.append(Spacer(1, 6))

    works = extract_works(tender_data)

    search_city = (
        _ensure_dict(tender_data.get("market_analysis", {})).get("city")
        or _ensure_dict(tender_data.get("pricing", {})).get("city")
        or ""
    )

    works_table_data = [
        [
            Paragraph("Вид работ", styles["Normal"]),
            Paragraph("Объем", styles["Normal"]),
            Paragraph("Ед. изм", styles["Normal"]),
            Paragraph(
                "Исполнители (компания, контакты, цена / ссылки для поиска)",
                styles["Normal"],
            ),
        ]
    ]

    if not works:
        works_table_data.append(
            [
                Paragraph("Нет данных по видам работ", styles["Normal"]),
                Paragraph("", styles["Normal"]),
                Paragraph("", styles["Normal"]),
                Paragraph("", styles["Normal"]),
            ]
        )
    else:
        for w in works:
            if not isinstance(w, dict):
                continue

            work_name = (w.get("name") or "").strip()
            volume_val = w.get("volume", "")
            unit_val = w.get("unit", "")

            perf_lines = _build_performers_lines(work_name, performers_data, search_city)
            perf_par = make_multiline_paragraph(perf_lines, styles["Normal"])

            works_table_data.append(
                [
                    Paragraph(sanitize_text_for_paragraph(work_name), styles["Normal"]),
                    Paragraph(sanitize_text_for_paragraph(volume_val), styles["Normal"]),
                    Paragraph(sanitize_text_for_paragraph(unit_val), styles["Normal"]),
                    perf_par,
                ]
            )

    tbl_works = Table(
        works_table_data,
        colWidths=[70 * mm, 20 * mm, 20 * mm, 80 * mm],
        repeatRows=1,
    )
    tbl_works.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.3, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONT", (0, 0), (-1, -1), font_name, 10),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(tbl_works)
    story.append(Spacer(1, 18))

    # ---------- 4. ИСПОЛНИТЕЛИ ПО ЗАДАЧАМ ----------
    if performers_data:
        story.append(Paragraph("<b>Исполнители по задачам</b>", styles["Heading2"]))
        story.append(Spacer(1, 8))

        for work_name, performers in performers_data.items():
            story.append(
                Paragraph(
                    f"<b>Исполнители по задаче: {sanitize_text_for_paragraph(work_name)}</b>",
                    styles["Heading3"],
                )
            )

            perf_table_data = [
                [
                    "Имя",
                    "Тип",
                    "Ссылка",
                    "Рейтинг",
                    "Кратко отзывы",
                    "Цены",
                    "Телефон",
                    "Email",
                ]
            ]

            if not isinstance(performers, list):
                performers = []

            for p in performers:
                if not isinstance(p, dict):
                    continue

                raw_reviews = p.get("reviews", [])
                if isinstance(raw_reviews, dict):
                    avg_rating = raw_reviews.get("average_rating", "")
                    review_list = (
                        raw_reviews.get("reviews", [])
                        if isinstance(raw_reviews.get("reviews"), list)
                        else []
                    )
                elif isinstance(raw_reviews, list):
                    avg_rating = ""
                    review_list = raw_reviews
                else:
                    avg_rating = ""
                    review_list = []
                review_text = "; ".join(str(r) for r in review_list[:3])

                raw_prices = p.get("prices", [])
                price_items = []
                if isinstance(raw_prices, list):
                    for pr in raw_prices[:5]:
                        if isinstance(pr, dict):
                            vmin = pr.get("value_min")
                            vmax = pr.get("value_max")
                            v = pr.get("value")
                            unit = pr.get("unit", "")
                            if vmin is not None and vmax is not None:
                                price_items.append(
                                    f"{_safe_number(vmin):,.0f}-{_safe_number(vmax):,.0f} {unit}".strip()
                                )
                            elif v is not None:
                                price_items.append(
                                    f"{_safe_number(v):,.0f} {unit}".strip()
                                )
                        else:
                            price_items.append(str(pr))
                elif isinstance(raw_prices, str):
                    price_items.append(raw_prices)
                prices = "; ".join(price_items)

                contacts = p.get("contacts", {})
                if isinstance(contacts, dict):
                    phone = contacts.get("phone", "")
                    email = contacts.get("email", "")
                elif isinstance(contacts, str):
                    phone = contacts
                    email = ""
                else:
                    phone = ""
                    email = ""

                name_val = (p.get("name") or "").strip()
                type_val = (p.get("type") or "").strip()
                link_val = (p.get("profile_url") or p.get("site") or "").strip()

                # ссылка как кликабельный текст
                if link_val:
                    link_href = html.escape(link_val, quote=True)
                    link_cell = Paragraph(f"<link href='{link_href}'>ссылка</link>", styles["Normal"])
                else:
                    link_cell = Paragraph("", styles["Normal"])

                has_contacts = bool(phone or email)
                has_meta = any(
                    [name_val, type_val, link_val, avg_rating, review_text, has_contacts]
                )

                if not has_meta and prices:
                    perf_table_data.append(
                        [
                            sanitize_text_for_paragraph("Диапазон цен по рынку"),
                            "",
                            "",
                            "",
                            "",
                            sanitize_text_for_paragraph(prices),
                            "",
                            "",
                        ]
                    )
                else:
                    perf_table_data.append(
                        [
                            sanitize_text_for_paragraph(name_val),
                            sanitize_text_for_paragraph(type_val),
                            link_cell,
                            sanitize_text_for_paragraph(avg_rating),
                            sanitize_text_for_paragraph(review_text),
                            sanitize_text_for_paragraph(prices),
                            sanitize_text_for_paragraph(phone),
                            sanitize_text_for_paragraph(email),
                        ]
                    )

            tbl_perf = Table(perf_table_data, repeatRows=1)
            tbl_perf.setStyle(
                TableStyle(
                    [
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                        ("FONT", (0, 0), (-1, -1), font_name, 9),
                    ]
                )
            )
            story.append(tbl_perf)
            story.append(Spacer(1, 10))

    # ---------- 5. РАСЧЁТ БЮДЖЕТА ----------
    market_analysis = _ensure_dict(tender_data.get("market_analysis", {}))
    pricing = _ensure_dict(tender_data.get("pricing", {}))

    min_sum_calc = _ensure_dict(
        market_analysis.get("minimum_sum_calculation")
        or pricing.get("minimum_sum_calculation")
        or {}
    )

    works_for_budget = tender_data.get("technical", {}).get("works", {})
    if isinstance(works_for_budget, dict):
        works_for_budget = (
            works_for_budget.get("works_list") or works_for_budget.get("items") or []
        )
    if not isinstance(works_for_budget, list):
        works_for_budget = []

    if min_sum_calc:
        story.append(
            Paragraph(
                "<b>Расчёт бюджета на основе ограниченных данных</b>",
                styles["Heading2"],
            )
        )
        story.append(Spacer(1, 8))

        search_city2 = (
            market_analysis.get("city")
            or pricing.get("city")
            or ""
        )
        search_engine = (
            market_analysis.get("search_engine")
            or pricing.get("search_engine")
            or "Tender Search Engine"
        )
        conf_val = _safe_number(min_sum_calc.get("confidence", 0), 0.0)

        if search_city2:
            story.append(
                Paragraph(
                    f"Город: {sanitize_text_for_paragraph(search_city2)}",
                    styles["Normal"],
                )
            )
        if search_engine:
            story.append(
                Paragraph(
                    f"Источник данных: {sanitize_text_for_paragraph(search_engine)}",
                    styles["Normal"],
                )
            )
        story.append(
            Paragraph(f"Уверенность оценки: {conf_val:.0%}", styles["Normal"])
        )
        story.append(Spacer(1, 12))

        budget_table = [
            [
                "Вид работ",
                "Объем",
                "Ед.",
                "Мин",
                "Макс",
                "Медиана",
                "Q1/Q3",
                "Свежесть, дни",
                "Подытог мин",
                "Подытог макс",
                "Уверен.",
            ]
        ]

        works_breakdown = min_sum_calc.get("works_breakdown", [])
        if not isinstance(works_breakdown, list):
            works_breakdown = []

        has_real_prices = False

        for item in works_breakdown:
            if not isinstance(item, dict):
                continue

            status = item.get("status", "unknown")
            work_name = item.get("work_name", "")
            unit = item.get("unit", "")
            volume = _safe_number(item.get("volume", 0), 0.0)

            if status == "calculated":
                price_min = _safe_number(item.get("price_min", 0), 0.0)
                price_max = _safe_number(item.get("price_max", 0), 0.0)
                if price_min > 0 or price_max > 0:
                    has_real_prices = True

                q = item.get("quartiles", {}) or {}
                q1 = q.get("q1", "")
                med = q.get("median", "")
                q3 = q.get("q3", "")
                freshness = item.get("freshness_days")
                freshness_str = str(freshness) if freshness is not None else ""

                budget_table.append(
                    [
                        sanitize_text_for_paragraph(work_name),
                        f"{volume:.1f}",
                        sanitize_text_for_paragraph(unit),
                        f"{price_min:,.0f}" if price_min > 0 else "",
                        f"{price_max:,.0f}" if price_max > 0 else "",
                        f"{_safe_number(med, 0):,.0f}" if med != "" else "",
                        (
                            f"{_safe_number(q1, 0):,.0f}/"
                            f"{_safe_number(q3, 0):,.0f}"
                            if q1 != "" and q3 != ""
                            else ""
                        ),
                        freshness_str,
                        f"{_safe_number(item.get('subtotal_min', 0)):,.0f}",
                        f"{_safe_number(item.get('subtotal_max', 0)):,.0f}",
                        f"{_safe_number(item.get('confidence', 0), 0.0):.0%}",
                    ]
                )
            else:
                budget_table.append(
                    [
                        sanitize_text_for_paragraph(work_name),
                        f"{volume}",
                        sanitize_text_for_paragraph(unit),
                        "НЕТ ДАННЫХ",
                        "НЕТ ДАННЫХ",
                        "-",
                        "-",
                        "-",
                        "-",
                        "-",
                        "-",
                    ]
                )

        tbl_budget = Table(budget_table, repeatRows=1)
        tbl_budget.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.darkblue),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONT", (0, 0), (-1, -1), font_name, 9),
                    ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ]
            )
        )
        story.append(tbl_budget)
        story.append(Spacer(1, 16))

        total_min = _safe_number(min_sum_calc.get("total_min", 0), 0.0)
        total_max = _safe_number(min_sum_calc.get("total_max", 0), 0.0)
        currency = sanitize_text_for_paragraph(min_sum_calc.get("currency", "RUB"))

        if has_real_prices and (total_min > 0 or total_max > 0):
            story.append(
                Paragraph(
                    f"<b><font size=14 color='green'>МИНИМАЛЬНАЯ СУММА: {total_min:,.0f} {currency}</font></b>",
                    styles["Heading2"],
                )
            )
            story.append(
                Paragraph(
                    f"<b><font size=14 color='orange'>МАКСИМАЛЬНАЯ СУММА: {total_max:,.0f} {currency}</font></b>",
                    styles["Heading2"],
                )
            )
        else:
            story.append(
                Paragraph(
                    "<b><font size=12 color='red'>Не удалось надёжно рассчитать бюджет по доступным данным.</font></b>",
                    styles["Heading2"],
                )
            )

        story.append(Spacer(1, 12))

        warnings = min_sum_calc.get("warnings", [])
        if isinstance(warnings, list) and warnings:
            story.append(Paragraph("<b>Предупреждения:</b>", styles["Heading3"]))
            for w in warnings:
                story.append(
                    Paragraph(
                        f"• {sanitize_text_for_paragraph(w)}",
                        styles["Normal"],
                    )
                )
            story.append(Spacer(1, 12))
    else:
        story.append(
            Paragraph(
                "<b>Расчёт бюджета на основе ограниченных данных</b>",
                styles["Heading2"],
            )
        )
        story.append(Spacer(1, 6))

        budget_table = [["Вид работ", "Объем", "Ед.", "Комментарий"]]
        for w in works_for_budget:
            if not isinstance(w, dict):
                continue
            budget_table.append(
                [
                    sanitize_text_for_paragraph(w.get("name", "")),
                    sanitize_text_for_paragraph(w.get("volume", "")),
                    sanitize_text_for_paragraph(w.get("unit", "")),
                    "Недостаточно данных для расчета цен",
                ]
            )
        if len(budget_table) == 1:
            budget_table.append(
                ["—", "—", "—", "Недостаточно данных для расчета цен"]
            )

        tbl_budget = Table(budget_table, repeatRows=1)
        tbl_budget.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                    ("FONT", (0, 0), (-1, -1), font_name, 10),
                ]
            )
        )
        story.append(tbl_budget)
        story.append(Spacer(1, 12))

    # ---------- сборка PDF ----------
    try:
        doc.build(story)
    except Exception as e:
        raise Exception(
            f"Ошибка при создании PDF: {e}. Проверьте корректность данных в JSON файле."
        )


if __name__ == "__main__":
    generate_pdf_report("temp/aggregated_tender.json", "tender_report.pdf")
    print("PDF-отчёт успешно сформирован.")
