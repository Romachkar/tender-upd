from typing import List, Dict
from tender_core.models import TenderObject, WorkItem, TimeConditions
from registry import ProviderRegistry

_llm = ProviderRegistry.get_provider()

def analyze_risks(tender_obj: TenderObject, works: List[WorkItem], time_conditions: TimeConditions, region: str) -> Dict:
    """
    Анализ рисков для тендера, включая возможные угрозы и рекомендации.
    """
    if not tender_obj or not works:
        return {"risks": [], "recommendations": []}

    # Примерный запрос на анализ рисков
    text = f"Оцените риски для тендера {tender_obj.title} в регионе {region}. Примените анализ рисков по следующим работам: {', '.join([w.name for w in works])}"

    try:
        messages = [
            {"role": "system", "content": "Ты — эксперт по анализу рисков на тендерах."},
            {"role": "user", "content": text}
        ]
        resp = _llm.generate(messages=messages, model="openai/gpt-oss-120b", temperature=0.3)
        return {"risks": resp["choices"][0]["message"]["content"].splitlines(), "recommendations": []}
    except Exception as e:
        print(f"Ошибка при анализе рисков: {e}")
        return {"risks": ["Не удалось провести анализ рисков."], "recommendations": []}


def analyze_schedule(works: List[WorkItem], time_conditions: TimeConditions) -> Dict:
    """
    Анализ сроков выполнения работ, включая задержки, сроки, и рекомендации.
    """
    if not works or not time_conditions:
        return {"schedule": [], "recommendations": []}

    text = f"Проанализируйте временные условия для тендера с работами: {', '.join([w.name for w in works])}. Учитывайте текущие сроки и временные ограничения."

    try:
        messages = [
            {"role": "system", "content": "Ты — эксперт по анализу сроков на тендерах."},
            {"role": "user", "content": text}
        ]
        resp = _llm.generate(messages=messages, model="openai/gpt-oss-120b", temperature=0.3)
        return {"schedule": resp["choices"][0]["message"]["content"].splitlines(), "recommendations": []}
    except Exception as e:
        print(f"Ошибка при анализе сроков: {e}")
        return {"schedule": ["Не удалось провести анализ сроков."], "recommendations": []}


def build_wbs(works: List[WorkItem]) -> Dict:
    """
    Строит иерархию работ (Work Breakdown Structure).
    """
    if not works:
        return {"wbs": []}

    # Простой пример — строим иерархию работ
    wbs = {}
    for work in works:
        wbs[work.name] = {"tasks": [{"name": work.name, "volume": work.volume, "unit": work.unit}]}

    return {"wbs": wbs}


def analyze_budget(works: List[WorkItem], prices: Dict) -> Dict:
    """
    Анализ бюджета на основе объёмов работ и цен.
    """
    if not works or not prices:
        return {"budget": [], "recommendations": []}

    budget_analysis = []
    for work in works:
        price = prices.get(work.name)
        if price:
            budget_analysis.append({"work_name": work.name, "budget": work.volume * price})
        else:
            budget_analysis.append({"work_name": work.name, "budget": "Нет данных"})

    return {"budget": budget_analysis, "recommendations": []}


def analyze_strategy(tender_obj: TenderObject, budget: Dict, risks: Dict) -> Dict:
    """
    Анализирует стратегию участия в тендере, оценивая риски, бюджет и другие параметры.
    """
    if not tender_obj or not budget or not risks:
        return {"strategy": [], "recommendations": []}

    strategy = []
    total_budget = sum([b["budget"] for b in budget["budget"] if isinstance(b["budget"], (int, float))])
    if total_budget < 1000000:
        strategy.append("Рекомендуется участвовать в тендере, так как бюджет ниже средней рыночной стоимости.")
    else:
        strategy.append("Тендер может быть высокорисковым, требуется дополнительная проверка.")

    return {"strategy": strategy, "recommendations": []}
