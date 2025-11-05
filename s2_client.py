# paper_survey/s2_client.py
# -*- coding: utf-8 -*-
"""
Semantic Scholar Graph API（Bulk）检索客户端（仅 bulk 端点）
----------------------------------------------------------
本文件实现：
1) 将 SearchIntent 规范化为 S2 Bulk API 参数；
2) AND-of-OR 的查询串构造（any_groups 支持同义词/别名的“或”，组间“与”）；
3) 速率限制 + 指数退避重试；
4) 尽量使用服务端过滤（publicationDateOrYear / publicationTypes / openAccessPdf / venue / sort）；
5) 客户端兜底过滤（作者精确/包含匹配；venue 同义词规整；日期区间精确到日；最小有影响力引用数；publication_types 交集匹配）；
6) 返回 (collected, batch)：collected 为过滤后的结果，batch 为最后一页原始转换结果用于调试展示。

注意：
- 本实现**仅使用 bulk 端点**，不做回落到 /paper/search。
- 移除了 fields_of_study 的所有过滤逻辑（服务器端和客户端都不再使用）。
- 服务器端目前不支持 minInfluentialCitationCount，因此该项留给客户端过滤。
"""

import asyncio
import random
import logging
import httpx
import re
import calendar
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, date

from .config import S2_BASE, S2_API_KEY, S2_RPS
from .schemas import PaperMetadata, SearchIntent

logger = logging.getLogger("paper_survey.s2")

# ------------------------- 常量与端点 -------------------------
BULK_URL = f"{S2_BASE}/paper/search/bulk"  # 仅 bulk
FIELDS = (
    "paperId,title,url,abstract,authors,year,venue,externalIds,"
    "citationCount,influentialCitationCount,openAccessPdf,publicationTypes,"
    "publicationDate,fieldsOfStudy"
)

_HAS_KEY = bool(S2_API_KEY)
_INTERVAL = 1.0 / max(float(S2_RPS), 0.05)  # 全局 RPS 限速（最小 0.05 防除零）
_last = 0.0
_lock = asyncio.Lock()

# ------------------------- 会议同义词（用于客户端匹配规整） -------------------------
VENUE_SYNONYMS = {
    # 标准化到“无空格大写”后做集合对齐
    "NEURIPS": {"NEURIPS", "NIPS", "ADVANCES IN NEURAL INFORMATION PROCESSING SYSTEMS"},
    "ICLR": {"ICLR", "INTERNATIONAL CONFERENCE ON LEARNING REPRESENTATIONS"},
    "ICCV": {"ICCV", "INTERNATIONAL CONFERENCE ON COMPUTER VISION"},
    "CVPR": {"CVPR", "IEEE CONFERENCE ON COMPUTER VISION AND PATTERN RECOGNITION"},
    "EMNLP": {"EMNLP", "EMPIRICAL METHODS IN NATURAL LANGUAGE PROCESSING"},
    "ACL": {"ACL", "ASSOCIATION FOR COMPUTATIONAL LINGUISTICS"},
    "ICML": {"ICML", "INTERNATIONAL CONFERENCE ON MACHINE LEARNING"},
}

# =========================================================
# 1) 构造 query（AND-of-OR：any_groups）
# =========================================================
def _quote_if_needed(s: str) -> str:
    """多词短语自动加引号，保证作为整体匹配；已包引号则不重复。"""
    s = s.strip()
    if not s:
        return s
    if " " in s and not (s.startswith('"') and s.endswith('"')):
        return f'"{s}"'
    return s

# def _build_query(intent: SearchIntent) -> str:
#     """
#     将 any_groups（AND-of-OR）转为 S2 的 query 字符串：
#     - any_groups: List[List[str]]，每个子列表是一组“同义词/等价表达”，组内使用 OR，组间使用 AND。
#     - 为提高兼容性，这里用空格连接“组间 AND”，组内显式 "(a OR b)"。
#     - 若指定 author，则把作者名（短语）并入查询词，提升召回概率。
#     - 若指定 venues，则将 venue 字符串也并入查询词（真正过滤交给服务器参数 + 客户端核验）。
#     - 若最终为空，则返回 "*"。
#     """
#     parts: List[str] = []

#     # 组内 OR、组间 AND（以空格连接）
#     for group in (intent.any_groups or []):
#         toks = [_quote_if_needed(t) for t in group if t and str(t).strip()]
#         toks = list(dict.fromkeys(toks))  # 去重保持顺序
#         if not toks:
#             continue
#         if len(toks) == 1:
#             parts.append(toks[0])
#         else:
#             parts.append("(" + " OR ".join(toks) + ")")

#     # 作者作为“查询提示”，也加入（同时客户端再做精匹配）
#     if intent.author:
#         parts.append(_quote_if_needed(intent.author))

#     # 场馆名也加一点“召回提示”
#     if intent.venues:
#         parts.extend([_quote_if_needed(v) for v in intent.venues if v and str(v).strip()])

#     q = " ".join([p for p in parts if p]) or "*"
#     return q
# s2_client.py

def _quote_if_needed(s: str) -> str:
    s = s.strip()
    if not s:
        return s
    # 多词短语加引号
    if " " in s and not (s.startswith('"') and s.endswith('"')):
        return f'"{s}"'
    return s

def _build_query(intent: SearchIntent) -> str:
    """
    将 any_groups（AND-of-OR）转换为“简单关键词串”：
    - 组内的同义词：全部平铺（用空格隔开），不加 OR/AND/括号
    - 组与组之间：同样直接空格连接（更“宽松”，更易召回）
    - 作者、场馆作为“召回提示”一并拼接（真正过滤交给参数与客户端兜底）
    - 最终留空则用 "*"
    """
    toks: List[str] = []

    # 平铺所有同义词
    for group in (intent.any_groups or []):
        for term in group:
            if term and str(term).strip():
                toks.append(_quote_if_needed(term))

    # 作者/场馆也加入（召回提示）
    if intent.author:
        toks.append(_quote_if_needed(intent.author))
    if intent.venues:
        toks.extend([_quote_if_needed(v) for v in intent.venues if v and str(v).strip()])

    # 去重但保序
    seen = set()
    flat = []
    for t in toks:
        if t and t not in seen:
            seen.add(t)
            flat.append(t)

    return " ".join(flat) or "*"

# =========================================================
# 2) 速率限制 + 指数退避重试
# =========================================================
async def _rate_limit():
    """统一 RPS 限流（请求间隔 >= _INTERVAL，并加少量抖动）。"""
    global _last
    async with _lock:
        now = asyncio.get_event_loop().time()
        wait = max(0.0, _INTERVAL - (now - _last)) + random.uniform(0, 0.05)
        if wait > 0:
            await asyncio.sleep(wait)
        _last = asyncio.get_event_loop().time()

async def _http_get(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """
    GET 请求（带限速与重试）。只返回 JSON dict；失败返回 {"total":0,"data":[]}
    - 仅用于 bulk 端点（本项目不回退到 /paper/search）
    """
    headers = {"Accept": "application/json"}
    if _HAS_KEY:
        headers["x-api-key"] = S2_API_KEY

    backoff = 0.5
    for attempt in range(6):
        await _rate_limit()
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                r = await client.get(url, params=params, headers=headers)

            if r.status_code == 200:
                return r.json()

            # 429/50x：指数退避
            if r.status_code in (429, 500, 502, 503, 504):
                logger.warning(f"[S2] {r.status_code}; retry in {backoff:.1f}s")
                await asyncio.sleep(backoff + random.uniform(0, 0.3))
                backoff = min(backoff * 2, 8.0)
                continue

            # 其它错误：记录并返回空
            logger.error(f"[S2] error {r.status_code}: {r.text[:200]}")
            return {"total": 0, "data": []}

        except (httpx.ReadTimeout, httpx.ConnectTimeout):
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 8.0)
        except Exception as e:
            if attempt >= 5:
                logger.error(f"[S2] fatal after retries: {e}")
                return {"total": 0, "data": []}
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 8.0)

    return {"total": 0, "data": []}

# =========================================================
# 3) 工具：作者/venue/日期/类型 过滤
# =========================================================
def _author_match(p: PaperMetadata, target: Optional[str]) -> bool:
    """
    作者过滤（客户端兜底）：
    - 未指定 target 时通过；
    - 大小写无关：完全相等或包含匹配。
    """
    if not target:
        return True
    t = target.strip().lower()
    for a in p.authors:
        al = a.lower()
        if al == t or t in al:
            return True
    return False

def _norm_token(s: str) -> str:
    """对比前规整：大写并去除非字母数字字符。"""
    return re.sub(r"[^A-Z0-9]+", "", (s or "").upper())

def _venue_match(p: PaperMetadata, venues: List[str]) -> bool:
    """
    会议/期刊过滤（客户端兜底）：
    - 未指定 venues 通过；
    - 论文缺 venue 则不通过（严格型）；
    - 规整化 + 同义词展开后比较（精确或包含）。
    """
    if not venues:
        return True
    if not p.journal:
        return False

    pj = _norm_token(p.journal)
    vset = set()
    for v in venues:
        vnorm = _norm_token(v)
        syns = VENUE_SYNONYMS.get(vnorm, {v})
        vset |= {_norm_token(x) for x in syns}
        vset.add(vnorm)

    if pj in vset:
        return True
    if any(v in pj or pj in v for v in vset):
        return True
    return False

def _pubtypes_match(p: PaperMetadata, want: List[str]) -> bool:
    """
    文献类型匹配（客户端兜底）：
    - want 为空则通过；
    - 两侧均转小写比较，判定是否有交集；
    - 若论文缺失 publication_types：
        * 若 want 仅包含研究类（JournalArticle/Conference），遵循旧逻辑“缺失也通过”；
        * 若 want 包含 Review，则严格要求具有 review 证据（缺失则不通过）。
    """
    if not want:
        return True

    want_set = {w.strip().lower() for w in want if w}
    # 研究类集合
    research_set = {"journalarticle", "conference"}

    # 论文侧
    types = [x.lower() for x in (p.publication_types or [])]

    if not types:
        # 缺类型：若仅要求研究类，则放行；若包含 Review，则不通过
        only_research = all(w in research_set for w in want_set)
        return only_research

    have_set = set(types)
    # 交集即通过
    return bool(have_set & want_set)

# ---- 日期解析（支持 YYYY / YYYY-MM / YYYY-MM-DD） ----
def _parse_date_any(s: Optional[str], end: bool = False) -> Optional[date]:
    """
    将 'YYYY' / 'YYYY-MM' / 'YYYY-MM-DD' 解析为 date。
    - 对 YYYY：start -> 1/1；end -> 12/31
    - 对 YYYY-MM：start -> 当月1日；end -> 当月最后一日
    - 对 YYYY-MM-DD：直接转换
    """
    if not s:
        return None
    ss = s.strip()
    try:
        if re.fullmatch(r"\d{4}$", ss):
            y = int(ss)
            return date(y, 12, 31) if end else date(y, 1, 1)
        if re.fullmatch(r"\d{4}-\d{2}$", ss):
            y, m = map(int, ss.split("-"))
            last = calendar.monthrange(y, m)[1]
            return date(y, m, last) if end else date(y, m, 1)
        # YYYY-MM-DD
        return datetime.fromisoformat(ss).date()
    except Exception:
        return None

def _date_match(p: PaperMetadata, ds: Optional[str], de: Optional[str]) -> bool:
    """
    精确到“日”的时间窗过滤（客户端兜底）：
    - 优先用 publication_date；
    - 无则用 year 的“中位日”（7/1）近似；
    - 若 intent 给了时间窗但论文没有日期/年份，则剔除。
    """
    if not (ds or de):
        return True

    # 先用 publication_date（YYYY-MM-DD）
    pd = None
    if p.publication_date:
        try:
            pd = datetime.fromisoformat(p.publication_date[:10]).date()
        except Exception:
            pd = None
    # 退化用 year 近似（年中位）
    if not pd and p.year:
        try:
            pd = date(int(p.year), 7, 1)
        except Exception:
            pd = None

    if not pd:
        return False

    start = _parse_date_any(ds, end=False)
    endd = _parse_date_any(de, end=True)
    if start and pd < start:
        return False
    if endd and pd > endd:
        return False
    return True

def _min_influential_match(p: PaperMetadata, mc: Optional[int]) -> bool:
    """最小“有影响力引用数”阈值（客户端过滤）。"""
    if mc is None:
        return True
    return (p.influential_citations or 0) >= mc

# =========================================================
# 4) 服务器参数（尽可能让服务器过滤/排序）
# =========================================================
def _date_param(intent: SearchIntent) -> Dict[str, Any]:
    """
    publicationDateOrYear: "<start>:<end>"（任一可缺省）
    - start/end 允许 YYYY / YYYY-MM / YYYY-MM-DD
    """
    ds = (intent.date_start or "").strip()
    de = (intent.date_end or "").strip()
    if ds or de:
        return {"publicationDateOrYear": f"{ds}:{de}"}
    return {}

def _pubtypes_param(intent: SearchIntent) -> Dict[str, Any]:
    """
    publicationTypes: 逗号分隔，允许 "JournalArticle","Conference","Review"
    - 若传入 ["Review"] -> "Review"
    - 若传入 ["JournalArticle","Conference"] -> "JournalArticle,Conference"
    - 其它组合按原样拼接
    """
    pts = [t for t in (intent.publication_types or []) if t]
    if not pts:
        return {}
    # 去重并保持次序
    norm = []
    seen = set()
    for t in pts:
        key = t.strip()
        if not key:
            continue
        if key not in seen:
            norm.append(key)
            seen.add(key)
    return {"publicationTypes": ",".join(norm)} if norm else {}

def _venues_param(intent: SearchIntent) -> Dict[str, Any]:
    """venue: 逗号分隔"""
    params: Dict[str, Any] = {}
    if intent.venues:
        params["venue"] = ",".join(intent.venues)
    return params  # ← 修正：原实现误返回 {}

def _if_must_have_pdf_param(intent: SearchIntent) -> Dict[str, Any]:
    """openAccessPdf: true/false（这里只在 True 时下发，False=不加）"""
    params: Dict[str, Any] = {}
    if intent.must_have_pdf:
        params["openAccessPdf"] = "true"
    return params

def _sort_param(intent: SearchIntent) -> Dict[str, Any]:
    """
    bulk 支持 sort: relevance | citationCount | publicationDate
    - 服务器默认 relevance；若用户选 relevance 可不下发 sort。
    """
    s = (intent.sort_by or "").strip()
    if s in ("citationCount", "publicationDate"):
        return {"sort": s}
    # relevance 或其它 -> 让服务器默认
    return {}

# =========================================================
# 5) JSON -> PaperMetadata
# =========================================================
def _item_to_paper(item: Dict[str, Any]) -> PaperMetadata:
    authors = [a.get("name", "") for a in item.get("authors", []) if a.get("name")]
    open_pdf = bool(item.get("openAccessPdf"))
    pub_types = item.get("publicationTypes") or []
    if isinstance(pub_types, str):
        pub_types = [pub_types]
    fos = item.get("fieldsOfStudy") or []
    if isinstance(fos, str):
        fos = [fos]

    return PaperMetadata(
        title=item.get("title", "") or "",
        authors=authors,
        abstract=item.get("abstract"),
        year=item.get("year"),
        doi=(item.get("externalIds") or {}).get("DOI"),
        journal=item.get("venue"),
        url=item.get("url"),
        citations=item.get("citationCount"),
        influential_citations=item.get("influentialCitationCount"),
        open_access=open_pdf,
        publication_types=pub_types,
        publication_date=item.get("publicationDate"),
        fields_of_study=fos,  # 虽然不再用于过滤，但仍保留给上层展示/调试
    )

# =========================================================
# 6) 主流程：bulk 调用 + 服务端过滤 + 客户端兜底
# =========================================================
async def search_papers(intent: SearchIntent) -> Tuple[List[PaperMetadata], List[PaperMetadata], Dict[str, Any]]:
    """
    执行检索并返回 (collected, batch, stats)：
    - collected：经过“服务端过滤 + 客户端兜底过滤”的结果集合
    - batch：最后一页的原始转换结果（便于调试/展示）
    - stats：计数与调试信息（server_total/raw_fetched/after_filter/query/per_page/pages）
    """
    query = _build_query(intent)

    per_page = min(max(intent.max_results * 3, 50), 100) if _HAS_KEY else max(intent.max_results * 2, 50)
    max_pages = 4 if _HAS_KEY else 2

    server_params: Dict[str, Any] = {
        "query": query,
        "fields": FIELDS,
        "limit": per_page,
    }
    server_params.update(_date_param(intent))
    server_params.update(_venues_param(intent))
    server_params.update(_if_must_have_pdf_param(intent))
    server_params.update(_pubtypes_param(intent))
    server_params.update(_sort_param(intent))

    logger.debug(f"[S2] bulk params(base)={server_params}")

    offset = 0
    collected: List[PaperMetadata] = []
    batch: List[PaperMetadata] = []

    # 统计用
    raw_fetched = 0
    server_total: Optional[int] = None
    pages = 0

    for _ in range(max_pages):
        server_params["offset"] = offset
        data = await _http_get(BULK_URL, server_params)

        items = data.get("data") or []
        # 统计：服务端 total（通常出现在第一页）
        if server_total is None:
            server_total = data.get("total")

        if not items:
            break

        pages += 1
        raw_fetched += len(items)
        batch = [_item_to_paper(it) for it in items]

        # ---- 客户端兜底过滤 ----
        filtered: List[PaperMetadata] = []
        for p in batch:
            if not _author_match(p, intent.author):
                continue
            if not _venue_match(p, intent.venues):
                continue
            if not _pubtypes_match(p, intent.publication_types[0] if intent.publication_types else None):
                continue
            if intent.must_have_pdf and not p.open_access:
                continue
            if not _date_match(p, intent.date_start, intent.date_end):
                continue
            if not _min_influential_match(p, intent.min_influential_citations):
                continue
            filtered.append(p)

        collected.extend(filtered)

        if len(collected) >= intent.max_results * 3:
            break

        offset += per_page

    stats = {
        "server_total": server_total,
        "raw_fetched": raw_fetched,
        "after_filter": len(collected),
        "query": query,
        "per_page": per_page,
        "pages": pages,
        "params_used": {k: v for k, v in server_params.items() if k != "fields"},
    }

    logger.info(
        f"[S2] server_total={server_total} raw_fetched={raw_fetched} "
        f"after_filter={len(collected)} pages={pages}"
    )
    return collected, stats