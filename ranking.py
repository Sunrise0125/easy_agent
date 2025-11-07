# paper_survey/ranking.py
import math
from typing import List, Optional, Tuple
from datetime import datetime, date, timezone, timedelta

# 优先使用 Asia/Shanghai；失败则回退到固定 UTC+8，避免 ZoneInfoNotFoundError
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
    LOCAL_TZ = ZoneInfo("Asia/Shanghai")
except Exception:
    LOCAL_TZ = timezone(timedelta(hours=8))

from schemas import PaperMetadata, TOP_VENUES


# ---------- 场馆打分 ----------
def _venue_score(v: Optional[str]) -> float:
    if not v:
        return 0.3
    return 1.0 if v.upper() in TOP_VENUES else 0.5

# ---------- 将论文“发布日期”转为 date ----------
def _pub_date_as_date(p: PaperMetadata) -> Optional[date]:
    """
    - 优先使用 p.publication_date（ISO8601，取前 10 位 YYYY-MM-DD）
    - 若无，则用 p.year 的中位日 7/1 作为近似（避免 1/1 过旧或 12/31 过新偏差）
    """
    if p.publication_date:
        try:
            return datetime.fromisoformat(p.publication_date[:10]).date()
        except Exception:
            pass
    if p.year:
        try:
            return date(int(p.year), 7, 1)
        except Exception:
            return None
    return None

# ---------- 距今天数（越小越新） ----------
def _recency_days(p: PaperMetadata) -> Optional[int]:
    d = _pub_date_as_date(p)
    if not d:
        return None
    today = datetime.now(LOCAL_TZ).date()
    return max(0, (today - d).days)

# ---------- “天”粒度新鲜度分（半衰 一 年） ----------
def _recency_score_by_day(p: PaperMetadata) -> float:
    days = _recency_days(p)
    if days is None:
        return 0.0
    half_life_days = 365  # ≈1 年半衰
    return math.pow(2.0, - days / half_life_days)

# ---------- 重要性综合分（只看“有影响力引用数”） ----------
def importance(p: PaperMetadata) -> float:
    infl = p.influential_citations or 0
    rec  = _recency_score_by_day(p)
    ven  = _venue_score(p.journal)
    # 你前面已经决定只考虑“有影响力引用数”，这里沿用该设定
    return 0.4 * rec + 0.3 * ven + 0.3 * math.log1p(infl)

# ---------- “按日期新→旧”的排序 key（无缓存，现场计算） ----------
def _key_freshness(p: PaperMetadata) -> Tuple[int, int, int, float]:
    """
    返回一个可比较的 tuple，按优先级排序：
    1) 有无日期：有(0) 优先于 无(1)
    2) 距今天数：越小越新（升序）
    3) 有影响力引用数：越多越好（降序 -> 用 - 值）
    4) 场馆得分：越高越好（降序 -> 用 - 值）
    """
    rd = _recency_days(p)
    has_date_flag = 0 if rd is not None else 1
    rd_key = rd if rd is not None else 10**9
    infl = -(p.influential_citations or 0)
    ven  = -_venue_score(p.journal)
    return (has_date_flag, rd_key, infl, ven)

def rank_papers(papers: List[PaperMetadata], mode: str = "relevance") -> List[PaperMetadata]:
    """
    支持三种模式：
    - "influentialCitationCount" / "influentialcitations" / "citationcount"
        => 按“有影响力引用数”降序
    - "publicationDate" / "freshness" / "date" / "newest"
        => 按“日”粒度新鲜度：有日期优先、越新越靠前，并用影响力引用与场馆打破并列
    - "importance" / "relevance"（默认）
        => 综合分 importance（新鲜度 + 场馆 + log1p(影响力引用)）
    """
    key = (mode or "relevance").lower()

    if key in ("influentialcitationcount", "influentialcitations", "citationcount"):
        return sorted(papers, key=lambda p: (p.influential_citations or 0), reverse=True)

    if key in ("publicationdate", "freshness", "date", "newest"):
        return sorted(papers, key=_key_freshness)

    if key in ("importance", "relevance"):
        return sorted(papers, key=importance, reverse=True)

    # 未知模式：原样返回
    return papers
