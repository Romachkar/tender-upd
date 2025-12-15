from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import date


@dataclass
class DocumentMeta:
    path: str
    content: str
    doc_type: str  # "tz", "contract", "estimate", "instruction", "other"


@dataclass
class WorkItem:
    """Единица работ из ТЗ/сметы."""
    name: str
    category: str              # плитка / демонтаж / фасад / электрика / вентиляция / поставка / услуга / прочее
    unit: str                  # м2, м3, шт, пог.м, ч, объект и т.п.
    volume: float
    raw_row: Optional[str] = None  # исходная строка из документа


@dataclass
class TimeConditions:
    start_date: Optional[date]
    end_date: Optional[date]
    duration_days: Optional[int]
    key_milestones: List[str] = field(default_factory=list)
    penalties: List[str] = field(default_factory=list)
    other_terms: List[str] = field(default_factory=list)


@dataclass
class TenderObject:
    title: str
    description: str
    customer_name: Optional[str] = None
    customer_inn: Optional[str] = None
    customer_ogrn: Optional[str] = None
    region: Optional[str] = None
    address: Optional[str] = None


@dataclass
class PriceInfo:
    work_name: str
    unit: str
    volume: float
    price_min: float
    price_max: float
    currency: str
    sources: List[Dict[str, Any]]
    confidence: float
    freshness_days: Optional[int] = None


@dataclass
class PerformerInfo:
    work_name: str
    performers: List[Dict[str, Any]]  # здесь можно сразу класть то, что уже возвращает твой Search_engine


@dataclass
class LawChecks:
    customer_violations: List[str]   # найденные нарушения / риски
    participant_requirements: List[str]
    law_templates_used: List[str]    # какие шаблоны требований применили
    commentary: str                  # краткое резюме


@dataclass
class RiskAnalysis:
    general_risks: List[str]
    contract_risks: List[str]
    region_risks: List[str]
    probability_comment: str
    impact_comment: str
    overall_conclusion: str


@dataclass
class ScheduleAnalysis:
    summary: str
    critical_path: List[str]
    timeline_items: List[Dict[str, Any]]  # [{task, start, end, duration_days}]


@dataclass
class WorkBreakdown:
    """Фронт работ / WBS."""
    wbs_tree: Dict[str, Any]     # произвольная иерархия WBS
    commentary: str


@dataclass
class BudgetAnalysis:
    total_min: float
    total_max: float
    currency: str
    per_category: Dict[str, Dict[str, float]]  # {category: {min, max}}
    notes: str


@dataclass
class StrategyAnalysis:
    pros: List[str]
    cons: List[str]
    win_strategies: List[str]
    no_go_reasons: List[str]


@dataclass
class TenderAnalysisResult:
    tender: TenderObject
    documents: List[DocumentMeta]
    works: List[WorkItem]
    time_conditions: TimeConditions
    prices: List[PriceInfo]
    performers: List[PerformerInfo]
    law_checks: LawChecks
    risk_analysis: RiskAnalysis
    schedule_analysis: ScheduleAnalysis
    wbs: WorkBreakdown
    budget_analysis: BudgetAnalysis
    strategy_analysis: StrategyAnalysis
