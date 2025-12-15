import os
import logging
from typing import List, Dict
from tender_core.models import DocumentMeta
from ai_services import analyze_strategy, analyze_budget, analyze_schedule, build_wbs
from parser_pipeline import process_document
from generate_report import generate_pdf_report
from read_services import read_doc

# Настройки логирования
logger = logging.getLogger(__name__)


def main_pipeline(doc_paths: List[str], use_llm: bool = True, generate_report_flag: bool = True) -> Dict:
    """
    Главный пайплайн для обработки тендера.
    Выполняет:
    1. Чтение документов.
    2. Классификацию и анализ через LLM (или стандартный анализ).
    3. Генерацию отчёта.
    """
    # Шаг 1: Загрузка и классификация документов
    logger.info("Запуск обработки тендера. Чтение документов...")
    docs = load_documents(doc_paths)

    logger.info(f"Загружено {len(docs)} документов.")

    # Шаг 2: Обработка документов
    results = {}
    for doc in docs:
        logger.info(f"Обрабатываем документ: {doc.path}")
        result = process_document(doc, use_llm)
        results[doc.path] = result

    # Шаг 3: Анализ рисков и бюджетирования
    logger.info("Запуск анализа рисков и бюджета...")
    strategy_results = {}
    for doc in docs:
        if doc.doc_type == "contract":
            strategy_results[doc.path] = analyze_strategy(doc, results[doc.path].get("budget", {}),
                                                          results[doc.path].get("risks", {}))
            strategy_results[doc.path]["wbs"] = build_wbs(results[doc.path].get("works", []))

    # Шаг 4: Генерация отчёта (по желанию)
    if generate_report_flag:
        logger.info("Генерация PDF-отчёта...")
        generate_pdf_report(results, strategy_results)

    return results


def load_documents(doc_paths: List[str]) -> List[DocumentMeta]:
    """
    Загружает документы из списка путей, и классифицирует их.
    """
    docs = []
    for path in doc_paths:
        try:
            # Используем функцию загрузки и классификации, которая была переписана
            doc_meta = DocumentMeta(path=path, content=read_and_classify(path))
            docs.append(doc_meta)
        except Exception as e:
            logger.error(f"Ошибка при загрузке документа {path}: {e}")

    return docs


def read_and_classify(path: str) -> str:
    """
    Чтение документа и классификация его типа (ТЗ, контракт, смета и т.д.)
    """
    try:
        content = _read_any(path)
        return _classify_document_based_on_rules(content)
    except Exception as e:
        logger.error(f"Ошибка при чтении документа {path}: {e}")
        return "unknown"


def _read_any(path: str) -> str:
    """
    Прочитает документ и вернёт его текстовое содержимое.
    """
    ext = os.path.splitext(path)[1].lower()

    try:
        if ext == ".docx":
            content = read_docx(path)
        elif ext == ".doc":
            content = read_doc(path)
        elif ext == ".pdf":
            content = read_pdf(path)
        elif ext == ".txt":
            with open(path, "r", encoding="utf-8", errors="ignore") as file:
                content = file.read()
        else:
            content = ""
    except Exception as e:
        logger.error(f"Ошибка при чтении файла {path}: {e}")
        content = ""

    return content


def _classify_document_based_on_rules(content: str) -> str:
    """
    Простой эвристический алгоритм классификации.
    """
    if "техническое задание" in content.lower():
        return "technical_specification"
    if "проект контракта" in content.lower():
        return "contract_project"
    if "смета" in content.lower():
        return "estimate"
    return "other"
