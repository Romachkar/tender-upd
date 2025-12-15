import os
from typing import List

from registry import ProviderRegistry
from tender_core.models import DocumentMeta
from read_services import (
    read_docx,
    read_doc,
    read_xlsx,
    read_xls,
    read_csv,
    read_pdf,
    read_pptx,
    read_html,
    read_xml,
)
import subprocess
import tempfile
from pathlib import Path

_llm = ProviderRegistry.get_provider()

def convert_doc_to_docx(path):
    out_dir = tempfile.mkdtemp()
    subprocess.run([
        "soffice", "--headless", "--convert-to", "docx",
        "--outdir", out_dir, path
    ], check=True)
    new_file = os.path.join(out_dir, basename(path).replace(".doc", ".docx"))
    return new_file

def _looks_like_binary_garbage(text: str) -> bool:
    """
    Простейший детектор "кракозябр":
    если доля непечатаемых символов и совсем странных кодов слишком большая — считаем мусором.
    """
    if not text:
        return False
    bad = 0
    total = 0
    for ch in text:
        total += 1
        # Оставляем буквы/цифры/знаки препинания/пробелы/переносы строк
        if ch.isprintable() or ch.isspace():
            continue
        bad += 1
    # если больше 10% символов — мусор, считаем текст невалидным
    return (bad / max(total, 1)) > 0.10


def _clean_text(text: str) -> str:
    """Мягкая очистка текста от мусора и скрытых символов."""
    if not text:
        return ""
    cleaned = "".join(ch for ch in text if ch.isprintable() or ch.isspace())
    return cleaned.strip()

def _convert_doc_to_docx_with_libreoffice(path: str) -> str:
    """
    Пытаемся конвертировать .doc в .docx через LibreOffice (soffice).
    В случае любой ошибки возвращаем исходный путь — тогда задействуется старый read_doc.
    """
    try:
        from shutil import which

        # Если LibreOffice не установлен — тихо выходим
        if which("soffice") is None:
            return path

        tmp_dir = tempfile.mkdtemp(prefix="tender_docx_")

        subprocess.run(
            [
                "soffice",
                "--headless",
                "--convert-to",
                "docx",
                "--outdir",
                tmp_dir,
                path,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        src_name = os.path.basename(path)
        base, _ = os.path.splitext(src_name)
        candidate = os.path.join(tmp_dir, base + ".docx")
        if os.path.exists(candidate):
            return candidate
    except Exception as e:
        print(f"⚠️ Не удалось конвертировать {path} через LibreOffice: {e}")

    # В любом спорном случае продолжаем работать со старым .doc
    return path


def _read_any(path: str) -> str:
    """
    Унифицированное чтение документа по расширению.
    НЕ бросает исключений наружу — в случае проблем возвращает пустую строку.
    """
    ext = os.path.splitext(path)[1].lower()

    try:
        if ext == ".docx":
            content = read_docx(path)
        elif ext == ".doc":
            # Сначала пробуем сконвертировать в .docx через LibreOffice
            converted = _convert_doc_to_docx_with_libreoffice(path)
            if converted.lower().endswith(".docx") and os.path.exists(converted):
                content = read_docx(converted)
            else:
                # Запасной вариант — старый read_doc (textract / win32com)
                content = read_doc(path)
        elif ext in (".xlsx",):
            content = read_xlsx(path)
        elif ext in (".xlsx",):
            content = read_xlsx(path)
        elif ext in (".xls",):
            content = read_xls(path)
        elif ext == ".csv":
            content = read_csv(path)
        elif ext == ".pdf":
            content = read_pdf(path)
        elif ext in (".pptx",):
            content = read_pptx(path)
        elif ext in (".html", ".htm"):
            content = read_html(path)
        elif ext == ".xml":
            content = read_xml(path)
        else:
            # простой текстовый файл
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
    except Exception as e:  # на всякий случай не даём свалиться всему пайплайну
        print(f"❗ Ошибка при чтении файла {path}: {e}")
        return ""

    # лёгкая очистка + защита от бинарного мусора
    content = _clean_text(content)
    if _looks_like_binary_garbage(content):
        print(f"⚠️ Похоже, файл {path} содержит бинарные данные и не может быть корректно прочитан.")
        return ""

    return content


def _classify_doc_rule_based(text: str) -> str:
    """
    Простая эвристическая классификация документов:
    tz, contract, estimate, instruction, other.
    """
    if not text:
        return "other"

    t = text.lower()

    # Техническое задание
    if any(kw in t for kw in ["техническое задание", "тз ", " тз:", "т.з."]):
        return "tz"

    # Проект контракта / договора
    if any(kw in t for kw in ["проект контракта", "проект договора", "настоящий контракт", "настоящий договор"]):
        return "contract"

    # Смета / локальная смета / ведомость объёмов работ
    if any(kw in t for kw in ["смета", "локальная смета", "ведомость объемов", "ведомость объёмов", "калькуляция"]):
        return "estimate"

    # Инструкции / регламенты
    if any(kw in t for kw in ["инструкция", "руководство по эксплуатации", "регламент", "памятка"]):
        return "instruction"

    return "other"


def _classify_doc_llm(text: str) -> str:
    """
    LLM-классификация типа документа.
    Даже если LLM недоступен или ответ кривой — никогда не кидает исключения,
    а просто возвращает 'other' или результат rule-based.
    """
    # если текста нет — дальше не идём
    if not text or not text.strip():
        return "other"

    # на всякий случай режем очень длинный документ
    snippet = text[:4000]

    system_msg = {
        "role": "system",
        "content": (
            "Ты помощник для тендерной аналитики. "
            "По краткому фрагменту текста определи тип документа госзакупки. "
            "Допустимые ответы (одно слово, латиницей): "
            "tz, contract, estimate, instruction, other."
        ),
    }
    user_msg = {
        "role": "user",
        "content": (
            "Вот фрагмент документа:\\n\\n"
            f"{snippet}\\n\\n"
            "Ответь одним словом: tz, contract, estimate, instruction или other."
        ),
    }

    try:
        # BaseProvider.generate обычно возвращает JSON от API (а не голую строку),
        # поэтому аккуратно достаём текст из первого choice.
        resp = _llm.generate(messages=[system_msg, user_msg])
        # ожидаем структуру, похожую на OpenAI / OpenRouter
        if isinstance(resp, dict):
            choices = resp.get("choices") or []
            if choices:
                msg = choices[0].get("message") or {}
                content = (msg.get("content") or "").strip().lower()
            else:
                content = ""
        else:
            # на всякий случай, если провайдер вернул уже строку
            content = str(resp).strip().lower()

        allowed = {"tz", "contract", "estimate", "instruction", "other"}
        for token in allowed:
            if token in content:
                return token

        # если LLM ничего внятного не сказал — fallback к rule-based
        return _classify_doc_rule_based(text)

    except Exception as e:
        print(f"⚠️ Ошибка LLM при классификации документа: {e}")
        return _classify_doc_rule_based(text)


def load_and_classify_documents(paths: List[str], use_llm: bool = False) -> List[DocumentMeta]:
    """
    Загружает список файлов, читает их содержимое и присваивает каждому тип:
    tz / contract / estimate / instruction / other.

    Возвращает список DocumentMeta, который дальше идёт в основной пайплайн.
    """
    docs: List[DocumentMeta] = []

    for p in paths:
        content = _read_any(p)
        # если файл вообще не прочитался — пропускаем его, чтобы не ломать логику
        if not content:
            print(f"⚠️ Файл {p} не был прочитан или содержит только мусор — пропускаю.")
            continue

        doc_type = _classify_doc_rule_based(content)
        if use_llm:
            doc_type = _classify_doc_llm(content)

        docs.append(DocumentMeta(path=p, content=content, doc_type=doc_type))

    return docs
