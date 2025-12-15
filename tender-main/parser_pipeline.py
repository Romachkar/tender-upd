from typing import List, Dict, Any
from ai_services import analyze_tender_with_llm
from tender_core.models import DocumentMeta
from read_services import read_pdf, read_docx, read_doc
from registry import ProviderRegistry

# Инициализация провайдера LLM
_llm = ProviderRegistry.get_provider()


def process_document(doc_meta: DocumentMeta, use_llm: bool = True) -> Dict[str, Any]:
    """
    Обработка документа, классификация и анализ содержимого с использованием LLM
    """
    try:
        # Разделение текста документа на чанки для последующего анализа
        text = doc_meta.content
        chunk_size = 2000  # максимальный размер чанка
        overlap = 200  # количество перекрытий чанков
        chunks = _split_text(text, chunk_size, overlap)

        # Если LLM включен, используем его для анализа чанков
        if use_llm:
            logger.info(f"Запуск анализа для документа {doc_meta.path}. Чанков: {len(chunks)}.")
            analysis_results = {}
            for idx, chunk in enumerate(chunks, start=1):
                try:
                    chunk_results = analyze_tender_with_llm(chunk, doc_meta.city)
                    analysis_results.update(chunk_results)
                except Exception as e:
                    logger.error(f"Ошибка при анализе чанка {idx}: {e}")
            return analysis_results
        else:
            # Если LLM не используется, делаем обычную классификацию документа
            return {
                "classification": _classify_document_based_on_rules(doc_meta.content),
            }
    except Exception as e:
        logger.error(f"Ошибка при обработке документа {doc_meta.path}: {e}")
        return {}


def _split_text(text: str, chunk_size: int = 2000, overlap: int = 200) -> List[str]:
    """
    Разделяет текст на чанки с наложением (overlap), чтобы LLM не обрабатывал слишком большие объемы.
    """
    text = text.strip()
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunk = text[start:end]
        chunks.append(chunk)
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return chunks


def _classify_document_based_on_rules(content: str) -> str:
    """
    Простейшая эвристическая классификация документа на основе его содержания.
    """
    if not content:
        return "unknown"

    content_lower = content.lower()

    # Пример классификации:
    if "техническое задание" in content_lower:
        return "technical_specification"
    if "проект контракта" in content_lower:
        return "contract_project"
    if "смета" in content_lower:
        return "estimate"
    if "поставка" in content_lower:
        return "supply"

    return "other"

