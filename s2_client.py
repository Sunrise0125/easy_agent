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
5) 客户端兜底过滤（作者精确/包含匹配；venue 同义词规整；日期区间精确到日；
   最小有影响力引用数；publication_types 交集匹配）；
6) 去重（基于 DOI > URL > 规范化标题+年份），并尊重 total/无新增终止，避免翻页重复；
7) 返回 (collected, batch, stats)：
   - collected：过滤+去重后的累计结果
   - batch：最后一页“去重后但未做客户端过滤”的原始结果（便于前端对比）
   - stats：计数/参数信息（server_total/raw_fetched/raw_unique/after_filter/query/per_page/pages/params_used）
"""

import asyncio
import random
import logging
import httpx
import re
import calendar
import itertools
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, date

from config import S2_BASE, S2_API_KEY, S2_RPS
from schemas import PaperMetadata, SearchIntent

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
    s = (s or "").strip()
    if not s:
        return s
    if " " in s and not (s.startswith('"') and s.endswith('"')):
        return f'"{s}"'
    return s

def _build_query_combinations(intent: SearchIntent) -> List[str]:
    """
    将 any_groups（AND-of-OR）展开为笛卡尔积组合查询列表：
    - 同一子数组内为同义词"或"关系，子数组之间为"且"关系
    - 生成所有可能的组合（从每个组选一个词）
    - 例如：[["A", "B"], ["C"]] -> ["A C", "B C"]
    - 留空则返回 ["*"]
    """
    groups = intent.any_groups or []
    
    # 过滤空组和空词
    filtered_groups: List[List[str]] = []
    for group in groups:
        clean_group = [_quote_if_needed(term) for term in group if term and str(term).strip()]
        if clean_group:
            filtered_groups.append(clean_group)
    
    if not filtered_groups:
        return ["*"]
    
    # 生成笛卡尔积
    combinations = list(itertools.product(*filtered_groups))
    
    # 构建查询字符串
    queries: List[str] = []
    for combo in combinations:
        q = " ".join(combo)
        queries.append(q)
    
    logger.info(f"[S2] Generated {len(queries)} query combination(s)")
    for i, q in enumerate(queries, 1):
        logger.info(f"[S2] Query {i}: '{q}'")
    
    return queries

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

            # 日志里去掉 fields，避免冗长；且用参数化日志避免花括号歧义
            log_params = {kk: vv for kk, vv in params.items() if kk != "fields"}
            logger.debug("[S2] HTTP %s GET %s params=%s", r.status_code, url, log_params)

            if r.status_code == 200:
                j = r.json()
                if attempt > 0:
                    logger.info("[S2] recovered after %d retries", attempt)
                return j

            # 可恢复错误：退避重试
            if r.status_code in (429, 500, 502, 503, 504):
                logger.warning("[S2] %s; retry in %.1fs (attempt %d)", r.status_code, backoff, attempt + 1)
                await asyncio.sleep(backoff + random.uniform(0, 0.3))
                backoff = min(backoff * 2, 8.0)
                continue

            # 其它错误：记录并返回空
            logger.error("[S2] error %s: %s", r.status_code, r.text[:200])
            return {"total": 0, "data": []}

        except (httpx.ReadTimeout, httpx.ConnectTimeout) as e:
            logger.warning("[S2] timeout: %s; retry in %.1fs (attempt %d)", repr(e), backoff, attempt + 1)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 8.0)
            continue

        except Exception as e:
            logger.exception("[S2] unexpected error on attempt %d: %s", attempt + 1, repr(e))
            if attempt >= 5:  # 最后一次也失败了
                return {"total": 0, "data": []}
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 8.0)
            continue

    # 所有重试均失败
    return {"total": 0, "data": []}

# =========================================================
# 3) 工具：作者/venue/日期/类型 过滤 + 去重键 + 调试辅助
# =========================================================
def _author_match(p: PaperMetadata, target: Optional[str]) -> bool:
    if not target:
        return True
    t = target.strip().lower()
    for a in p.authors:
        al = a.lower()
        if al == t or t in al:
            return True
    return False

def _norm_token(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", (s or "").upper())

def _venue_match(p: PaperMetadata, venues: List[str]) -> bool:
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
    if not want:
        return True
    want_set = {w.strip().lower() for w in want if w}
    research_set = {"journalarticle", "conference"}
    types = [x.lower() for x in (p.publication_types or [])]
    if not types:
        only_research = all(w in research_set for w in want_set)
        return only_research
    have_set = set(types)
    return bool(have_set & want_set)

def _parse_date_any(s: Optional[str], end: bool = False) -> Optional[date]:
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
        return datetime.fromisoformat(ss).date()
    except Exception:
        return None

def _date_match(p: PaperMetadata, ds: Optional[str], de: Optional[str]) -> bool:
    if not (ds or de):
        return True
    pd = None
    if p.publication_date:
        try:
            pd = datetime.fromisoformat(p.publication_date[:10]).date()
        except Exception:
            pd = None
    if not pd and p.year:
        try:
            pd = date(int(p.year), 7, 1)  # 年中位
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
    if mc is None:
        return True
    return (p.influential_citations or 0) >= mc

def _norm_title(t: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().lower())

def _unique_key(p: PaperMetadata) -> tuple:
    """去重主键优先级：DOI > URL > (规范化标题, 年份)"""
    if p.doi:
        return ("doi", (p.doi or "").lower())
    if p.url:
        return ("url", (p.url or "").lower())
    return ("ty", _norm_title(p.title), int(p.year or 0))

def _short(txt: Optional[str], n: int = 120) -> str:
    s = (txt or "").replace("\n", " ").strip()
    return (s[: n - 1] + "…") if len(s) > n else s

def _why_reject(p: PaperMetadata, intent: SearchIntent) -> Optional[str]:
    """返回第一个触发的过滤原因（仅用于 DEBUG 日志）。"""
    if not _author_match(p, intent.author):
        return "author_mismatch"
    if not _venue_match(p, intent.venues):
        return f"venue_mismatch(p.journal={p.journal})"
    if not _pubtypes_match(p, intent.publication_types):
        return f"pubtypes_mismatch(p.types={p.publication_types}, want={intent.publication_types})"
    if intent.must_have_pdf and not p.open_access:
        return "need_open_access_pdf"
    if not _date_match(p, intent.date_start, intent.date_end):
        return f"date_out_of_range(pub_date={p.publication_date}, year={p.year})"
    if not _min_influential_match(p, intent.min_influential_citations):
        return f"low_influential_citations({p.influential_citations})"
    return None

# =========================================================
# 4) 服务器参数（尽可能让服务器过滤/排序）
# =========================================================
def _date_param(intent: SearchIntent) -> Dict[str, Any]:
    ds = (intent.date_start or "").strip()
    de = (intent.date_end or "").strip()
    if ds or de:
        return {"publicationDateOrYear": f"{ds}:{de}"}
    return {}

def _pubtypes_param(intent: SearchIntent) -> Dict[str, Any]:
    pts = [t for t in (intent.publication_types or []) if t]
    if not pts:
        return {}
    norm: List[str] = []
    seen: set = set()
    for t in pts:
        key = t.strip()
        if key and key not in seen:
            seen.add(key)
            norm.append(key)
    return {"publicationTypes": ",".join(norm)} if norm else {}

def _venues_param(intent: SearchIntent) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    if intent.venues:
        params["venue"] = ",".join(intent.venues)
    return params

def _if_must_have_pdf_param(intent: SearchIntent) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    if intent.must_have_pdf:
        params["openAccessPdf"] = "true"
    return params

def _sort_param(intent: SearchIntent) -> Dict[str, Any]:
    """
    bulk sort: <field>:<order>
    field: paperId | publicationDate | citationCount
    order: asc | desc
    relevance 不要下发（默认相关性）
    """
    s = (intent.sort_by or "").strip()
    if s == "citationCount":
        return {"sort": "citationCount:desc"}
    if s == "publicationDate":
        return {"sort": "publicationDate:desc"}
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
        fields_of_study=fos,
    )

# =========================================================
# 6) 主流程：bulk 调用 + 服务端过滤 + 客户端兜底 + 去重 + 详尽日志
# =========================================================
async def _search_single_query(
    query: str,
    intent: SearchIntent,
    global_seen_keys: set,
    per_page: int,
    max_pages: int
) -> Tuple[List[PaperMetadata], Dict[str, Any]]:
    """
    执行单个查询并返回 (collected, stats)。
    使用 global_seen_keys 进行跨查询去重。
    """
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

    logger.info(f"[S2 PARAMS] { {k: v for k, v in server_params.items() if k != 'fields'} }")

    offset = 0
    collected: List[PaperMetadata] = []

    # 统计用
    raw_fetched = 0
    raw_unique = 0
    server_total: Optional[int] = None
    pages = 0
    no_new_page_in_a_row = 0

    while pages < max_pages:
        server_params["offset"] = offset
        data = await _http_get(BULK_URL, server_params)

        items = data.get("data") or []
        if server_total is None:
            server_total = data.get("total")

        logger.info(f"[S2] page={pages+1} offset={offset} received={len(items)} total={server_total}")

        if not items:
            break

        pages += 1
        raw_fetched += len(items)

        # --- 页内转对象 + 立刻跨页去重 ---
        page_new_objects: List[PaperMetadata] = []
        page_dup = 0
        for it in items:
            p = _item_to_paper(it)
            k = _unique_key(p)
            if k in global_seen_keys:
                page_dup += 1
                logger.debug(f"[S2] DUP skip key={k[:2]} title='{_short(p.title)}'")
                continue
            global_seen_keys.add(k)
            page_new_objects.append(p)

        page_new_count = len(page_new_objects)
        raw_unique += page_new_count

        logger.info(f"[S2] page_new_unique={page_new_count} page_dup={page_dup}")

        # --- 客户端兜底过滤 ---
        kept: List[PaperMetadata] = []
        dropped = 0
        for p in page_new_objects:
            reason = _why_reject(p, intent)
            if reason is None:
                kept.append(p)
            else:
                dropped += 1
                logger.debug(f"[S2] REJECT reason={reason} | title='{_short(p.title)}' | venue='{p.journal}' | date={p.publication_date} | infl={p.influential_citations} | types={p.publication_types} | open={p.open_access}")

        collected.extend(kept)
        logger.info(f"[S2] page_kept={len(kept)} page_dropped={dropped} collected_total={len(collected)}")

        # --- 终止条件 ---
        if server_total is not None and offset + len(items) >= server_total:
            logger.info("[S2] reached server_total end, stop paging")
            break

        if page_new_count == 0:
            no_new_page_in_a_row += 1
            logger.info(f"[S2] no new unique on this page (#{no_new_page_in_a_row}) -> stop")
            break
        else:
            no_new_page_in_a_row = 0

        if len(collected) >= intent.max_results * 3:
            logger.info("[S2] collected enough for this query, stop early")
            break

        offset += len(items)

    stats = {
        "server_total": server_total,
        "raw_fetched": raw_fetched,
        "raw_unique": raw_unique,
        "after_filter": len(collected),
        "query": query,
        "pages": pages,
    }

    return collected, stats


async def search_papers(intent: SearchIntent) -> Tuple[List[PaperMetadata], List[PaperMetadata], Dict[str, Any]]:
    """
    执行检索并返回 (collected, batch, stats)：
    - collected：经过"服务端过滤 + 客户端兜底过滤 + 去重"的结果集合
    - batch：最后一个查询的最后一页结果（已去重）
    - stats：计数与调试信息（包含所有查询组合的统计）
    """
    queries = _build_query_combinations(intent)

    per_page = min(max(intent.max_results * 3, 50), 100) if _HAS_KEY else max(intent.max_results * 2, 50)
    max_pages = 4 if _HAS_KEY else 2

    # 全局去重集（跨所有查询组合）
    global_seen_keys: set = set()
    all_collected: List[PaperMetadata] = []
    all_stats: List[Dict[str, Any]] = []
    last_batch: List[PaperMetadata] = []

    # 对每个查询组合执行搜索
    for i, query in enumerate(queries, 1):
        logger.info(f"[S2] ===== Executing query combination {i}/{len(queries)} =====")
        
        collected, single_stats = await _search_single_query(
            query, intent, global_seen_keys, per_page, max_pages
        )
        
        all_collected.extend(collected)
        all_stats.append(single_stats)
        
        # 保留最后一个查询的结果作为 batch（与原始接口保持一致）
        if i == len(queries):
            last_batch = collected
        
        logger.info(
            f"[S2] Query {i} summary: server_total={single_stats['server_total']} "
            f"raw_fetched={single_stats['raw_fetched']} raw_unique={single_stats['raw_unique']} "
            f"after_filter={single_stats['after_filter']} pages={single_stats['pages']}"
        )

    # 汇总统计
    total_raw_fetched = sum(s["raw_fetched"] for s in all_stats)
    total_raw_unique = sum(s["raw_unique"] for s in all_stats)
    total_pages = sum(s["pages"] for s in all_stats)
    
    combined_stats = {
        "query_combinations": len(queries),
        "queries": [s["query"] for s in all_stats],
        "total_raw_fetched": total_raw_fetched,
        "total_raw_unique": total_raw_unique,
        "final_unique_count": len(all_collected),
        "per_page": per_page,
        "total_pages": total_pages,
        "individual_stats": all_stats,
    }

    logger.info(
        f"[S2] ===== FINAL SUMMARY ===== "
        f"queries={len(queries)} total_raw_fetched={total_raw_fetched} "
        f"total_raw_unique={total_raw_unique} final_unique={len(all_collected)} "
        f"total_pages={total_pages}"
    )
    
    return all_collected, combined_stats
