# paper_survey/s2_client.py
# -*- coding: utf-8 -*-
"""
多来源文献检索客户端（S2 Bulk + OpenAlex + Crossref）
----------------------------------------------------------
本文件实现：
1) 将 SearchIntent 规范化为 S2 Bulk API 参数；
2) AND-of-OR 的查询串构造（any_groups 支持同义词/别名的“或”，组间“与”）；
3) S2：速率限制 + 指数退避重试 + 服务器过滤（publicationDateOrYear / publicationTypes / openAccessPdf / venue / sort）；
4) 各来源内部先页内去重（DOI>URL>标题+年），S2 支持跨页去重；
5) 聚合层“客户端兜底过滤”（作者/venue/日期/影响力/类型）在**三来源统一进行**；
6) 末端统一去重（跨来源）：DOI > URL > 标题+年；
7) 返回 (collected, stats)：
   - collected：最终汇总 + 去重后的结果集合
   - stats：计数/参数信息（含 per-source 汇总与 per_source_after_filter、total_after_filter_*）
"""

import asyncio
import random
import logging
import httpx
import re
import calendar
import itertools
import shlex
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, date
import xml.etree.ElementTree as ET

from config import S2_BASE, S2_API_KEY, S2_RPS,OPENALEX_URL, CROSSREF_URL, ARXIV_URL, PUBMED_EUTILS, EUPMC_URL
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
    "NEURIPS": {"NEURIPS", "NIPS", "ADVANCES IN NEURAL INFORMATION PROCESSING SYSTEMS"},
    "ICLR": {"ICLR", "INTERNATIONAL CONFERENCE ON LEARNING REPRESENTATIONS"},
    "ICCV": {"ICCV", "INTERNATIONAL CONFERENCE ON COMPUTER VISION"},
    "CVPR": {"CVPR", "IEEE CONFERENCE ON COMPUTER VISION AND PATTERN RECOGNITION"},
    "EMNLP": {"EMNLP", "EMPIRICAL METHODS IN NATURAL LANGUAGE PROCESSING"},
    "ACL": {"ACL", "ASSOCIATION FOR COMPUTATIONAL LINGUISTICS"},
    "ICML": {"ICML", "INTERNATIONAL CONFERENCE ON MACHINE LEARNING"},
}

# =========================================================
# 工具：查询组合
# =========================================================
def _quote_if_needed(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return s
    if " " in s and not (s.startswith('"') and s.endswith('"')):
        return f'"{s}"'
    return s

def _build_query_combinations(intent: SearchIntent) -> List[str]:
    groups = intent.any_groups or []
    filtered_groups: List[List[str]] = []
    for group in groups:
        clean_group = [_quote_if_needed(term) for term in group if term and str(term).strip()]
        if clean_group:
            filtered_groups.append(clean_group)
    if not filtered_groups:
        return ["*"]  # 
    combinations = list(itertools.product(*filtered_groups))
    queries: List[str] = []
    for combo in combinations:
        queries.append(" ".join(combo))
    logger.info(f"[MULTI] Generated {len(queries)} query combination(s)")
    return queries

# =========================================================
# 工具：S2 限流 + 重试 HTTP
# =========================================================
async def _rate_limit():
    global _last
    async with _lock:
        now = asyncio.get_event_loop().time()
        wait = max(0.0, _INTERVAL - (now - _last)) + random.uniform(0, 0.05)
        if wait > 0:
            await asyncio.sleep(wait)
        _last = asyncio.get_event_loop().time()

async def _http_get(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    headers = {"Accept": "application/json"}
    if _HAS_KEY:
        headers["x-api-key"] = S2_API_KEY
    backoff = 0.5
    for attempt in range(6):
        await _rate_limit()
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                r = await client.get(url, params=params, headers=headers)
            log_params = {kk: vv for kk, vv in params.items() if kk != "fields"}
            logger.debug("[S2] HTTP %s GET %s params=%s", r.status_code, url, log_params)
            if r.status_code == 200:
                j = r.json()
                if attempt > 0:
                    logger.info("[S2] recovered after %d retries", attempt)
                return j
            if r.status_code in (429, 500, 502, 503, 504):
                logger.warning("[S2] %s; retry in %.1fs (attempt %d)", r.status_code, backoff, attempt + 1)
                await asyncio.sleep(backoff + random.uniform(0, 0.3))
                backoff = min(backoff * 2, 8.0)
                continue
            logger.error("[S2] error %s: %s", r.status_code, r.text[:200])
            return {"total": 0, "data": []}
        except (httpx.ReadTimeout, httpx.ConnectTimeout) as e:
            logger.warning("[S2] timeout: %s; retry in %.1fs (attempt %d)", repr(e), backoff, attempt + 1)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 8.0)
            continue
        except Exception as e:
            logger.exception("[S2] unexpected error on attempt %d: %s", attempt + 1, repr(e))
            if attempt >= 5:
                return {"total": 0, "data": []}
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 8.0)
            continue
    return {"total": 0, "data": []}

# =========================================================
# 工具：字段规整 / 过滤 / 去重 Key
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

def _min_influential_match(p: PaperMetadata, mc: Optional[int]) -> bool:
    if mc is None:
        return True
    return (p.influential_citations or 0) >= mc

def _why_reject(p: PaperMetadata, intent: SearchIntent) -> Optional[str]:
    """客户端兜底过滤（统一用于三来源）。返回第一个触发的过滤原因；通过返回 None。"""
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

def _norm_title(t: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().lower())

def _unique_key(p: PaperMetadata) -> tuple:
    if p.doi:
        return ("doi", (p.doi or "").lower())
    if p.url:
        return ("url", (p.url or "").lower())
    return ("ty", _norm_title(p.title), int(p.year or 0))

def _short(txt: Optional[str], n: int = 120) -> str:
    s = (txt or "").replace("\n", " ").strip()
    return (s[: n - 1] + "…") if len(s) > n else s

def _clean_doi(doi: Optional[str]) -> Optional[str]:
    if not doi:
        return None
    d = doi.strip()
    d = re.sub(r"^https?://doi\.org/", "", d, flags=re.I)
    return d or None

def _truncate(s: Optional[str], n: int = 4000) -> Optional[str]:
    return s if not s else s[:n]

def _first_n(lst: List[str], n: int = 25) -> List[str]:
    return (lst or [])[:n]
# =========================================================
# S2 服务器参数
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
    s = (intent.sort_by or "").strip()
    if s == "citationCount":
        return {"sort": "citationCount:desc"}
    if s == "publicationDate":
        return {"sort": "publicationDate:desc"}
    return {}

# =========================================================
# JSON -> PaperMetadata（S2）
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
# 单来源检索：S2（仅服务器过滤 + 内部页/跨页去重；不做客户端兜底过滤）
# =========================================================
async def _search_s2_single_query(
    query: str,
    intent: SearchIntent,
    s2_seen_keys: set,
    per_page: int,
    max_pages: int
) -> Tuple[List[PaperMetadata], Dict[str, Any]]:
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
    collected_no_client_filter: List[PaperMetadata] = []

    raw_fetched = 0
    raw_unique = 0
    server_total: Optional[int] = None
    pages = 0

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

        page_new_objects: List[PaperMetadata] = []
        for it in items:
            p = _item_to_paper(it)
            k = _unique_key(p)
            if k in s2_seen_keys:
                continue
            s2_seen_keys.add(k)
            page_new_objects.append(p)

        raw_unique += len(page_new_objects)
        collected_no_client_filter.extend(page_new_objects)

        if server_total is not None and offset + len(items) >= server_total:
            logger.info("[S2] reached server_total end, stop paging")
            break

        if len(collected_no_client_filter) >= per_page:  # 足够一页即可提前停（避免成本）
            logger.info("[S2] collected enough for this query, stop early")
            break

        offset += len(items)

    stats = {
        "server_total": server_total,
        "raw_fetched": raw_fetched,
        "raw_unique": raw_unique,
        "after_filter": None,     # 在聚合层统一计算
        "query": "[s2] " + query,
        "pages": pages,
    }
    return collected_no_client_filter, stats

# =========================================================
# 单来源检索：OpenAlex（最小实现 + 页内去重）
# =========================================================
async def _search_openalex_single_query(
    query: str,
    intent: SearchIntent,
    oa_seen_keys: set,
    per_page: int
) -> Tuple[List[PaperMetadata], Dict[str, Any]]:
    ds, de = intent.date_start, intent.date_end
    flt = []
    if ds: flt.append(f"from_publication_date:{ds}")
    if de: flt.append(f"to_publication_date:{de}")
    if intent.must_have_pdf: flt.append("open_access.is_oa:true")

    params = {
        "search": None if query == "*" else query,
        "per-page": min(per_page, 200),
        "page": 1,
        "filter": ",".join(flt) if flt else None,
        "sort": "publication_date:desc" if (intent.sort_by or "") == "publicationDate" else None,
        "mailto": "test@example.com"
    }
    params = {k: v for k, v in params.items() if v is not None}

    raw_items: List[Dict[str, Any]] = []
    pages = 1
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(OPENALEX_URL, params=params)
            r.raise_for_status()
            j = r.json()
            raw_items = j.get("results", []) or []
    except Exception as e:
        logger.warning("[OpenAlex] error: %s", repr(e))
        return [], {
            "server_total": 0, "raw_fetched": 0, "raw_unique": 0, "after_filter": None,
            "query": "[openalex] " + query, "pages": 0
        }

    tmp: List[PaperMetadata] = []
    for it in raw_items:
        authors = []
        for a in (it.get("authorships") or []):
            au = a.get("author") or {}
            if au.get("display_name"):
                authors.append(au["display_name"])
        prim = it.get("primary_location") or {}
        venue = (it.get("host_venue") or {}).get("display_name")
        url = prim.get("landing_page_url") or it.get("id")
        pdf_url = prim.get("pdf_url")
        doi = it.get("doi")
        if doi and doi.startswith("https://doi.org/"):
            doi = doi.replace("https://doi.org/", "")
        pm = PaperMetadata(
            title=it.get("title") or "",
            authors=authors,
            abstract=None,
            year=it.get("publication_year"),
            doi=doi,
            journal=venue,
            url=url or pdf_url,
            citations=it.get("cited_by_count"),
            influential_citations=None,
            open_access=bool((it.get("open_access") or {}).get("is_oa")),
            publication_types=[it.get("type")] if it.get("type") else [],
            publication_date=it.get("publication_date"),
            fields_of_study=[c.get("display_name") for c in (it.get("concepts") or [])[:5]],
        )
        k = _unique_key(pm)
        if k in oa_seen_keys:
            continue
        oa_seen_keys.add(k)
        tmp.append(pm)

    stats = {
        "server_total": None,
        "raw_fetched": len(raw_items),
        "raw_unique": len(tmp),
        "after_filter": None,
        "query": "[openalex] " + query,
        "pages": pages,
    }
    return tmp, stats

# =========================================================
# 单来源检索：Crossref（最小实现 + 页内去重）
# =========================================================
async def _search_crossref_single_query(
    query: str,
    intent: SearchIntent,
    cr_seen_keys: set,
    per_page: int
) -> Tuple[List[PaperMetadata], Dict[str, Any]]:
    params = {
        "query": None if query == "*" else query,
        "rows": min(per_page, 100),
        "select": "title,author,issued,DOI,URL,container-title,type,is-referenced-by-count",
        "sort": "issued" if (intent.sort_by or "") == "publicationDate" else None,
        "order": "desc" if (intent.sort_by or "") == "publicationDate" else None,
    }
    flt = []
    if intent.date_start: flt.append(f"from-pub-date:{intent.date_start}")
    if intent.date_end:   flt.append(f"until-pub-date:{intent.date_end}")
    if flt:
        params["filter"] = ",".join(flt)
    params = {k: v for k, v in params.items() if v is not None}

    raw_items: List[Dict[str, Any]] = []
    pages = 1
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(CROSSREF_URL, params=params, headers={"Accept": "application/json"})
            r.raise_for_status()
            j = r.json() or {}
            msg = j.get("message") or {}
            raw_items = msg.get("items") or []
    except Exception as e:
        logger.warning("[Crossref] error: %s", repr(e))
        return [], {
            "server_total": 0, "raw_fetched": 0, "raw_unique": 0, "after_filter": None,
            "query": "[crossref] " + query, "pages": 0
        }

    tmp: List[PaperMetadata] = []
    for it in raw_items:
        # 作者
        authors = []
        for a in (it.get("author") or []):
            nm = " ".join([a.get("given") or "", a.get("family") or ""]).strip()
            if nm:
                authors.append(nm)
        # 日期
        issued = it.get("issued", {}).get("date-parts") or []
        pub_year, pub_date = None, None
        if issued and isinstance(issued[0], list):
            parts = issued[0]
            if len(parts) >= 1: pub_year = parts[0]
            if len(parts) >= 2: pub_date = f"{parts[0]:04d}-{parts[1]:02d}-01"
            if len(parts) >= 3: pub_date = f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"

        pm = PaperMetadata(
            title=(it.get("title") or [""])[0] if isinstance(it.get("title"), list) else (it.get("title") or ""),
            authors=authors,
            abstract=None,
            year=int(pub_year) if isinstance(pub_year, int) else None,
            doi=it.get("DOI"),
            journal=(it.get("container-title") or [""])[0]
                    if isinstance(it.get("container-title"), list) else it.get("container-title"),
            url=it.get("URL"),
            citations=int(it.get("is-referenced-by-count") or 0),
            influential_citations=None,
            open_access=False,  # ← 关键修复：不要传 None
            publication_types=[it.get("type")] if it.get("type") else [],
            publication_date=pub_date,
            fields_of_study=[],
        )

        k = _unique_key(pm)
        if k in cr_seen_keys:
            continue
        cr_seen_keys.add(k)
        tmp.append(pm)

    stats = {
        "server_total": None,
        "raw_fetched": len(raw_items),
        "raw_unique": len(tmp),
        "after_filter": None,
        "query": "[crossref] " + query,
        "pages": pages,
    }
    return tmp, stats

# =========================================================
# 单来源检索：Arxiv（最小实现 + 页内去重）
# =========================================================
def _split_terms_respecting_quotes(q: str) -> List[str]:
    """
    把查询串按空白切分，但保留双引号中的短语，返回原样（含双引号或不含）。
    例: ' "reinforcement learning" robot control ' ->
        ['"reinforcement learning"', 'robot', 'control']
    """
    q = (q or "").strip()
    if not q:
        return []
    try:
        return shlex.split(q)  # 会去掉引号
    except Exception:
        # 兜底：按连续空白切分
        return re.findall(r'"[^"]+"|\S+', q)
def _arxiv_query_string(q: str) -> str:
    """
    把用户组合后的单个 query（可能含带引号短语）映射到 arXiv 的 all: 语法：
    - 短语 -> all:"短语"
    - 单词 -> all:word
    - 组间用 AND
    """
    q = (q or "").strip()

    tokens = _split_terms_respecting_quotes(q)
    if not tokens:
        return 'all:"machine learning"'

    mapped = []
    for t in tokens:
        if t.startswith('"') and t.endswith('"') and len(t) >= 2:
            # 已是短语，去掉两端引号再加回标准形式
            phrase = t[1:-1]
            mapped.append(f'all:"{phrase}"')
        else:
            mapped.append(f"all:{t}")
    # arXiv AND 用空格或显式 AND 都可；交给 httpx URL 编码即可
    return " AND ".join(mapped)


async def _search_arxiv_single_query(
    query: str,
    intent: SearchIntent,
    ax_seen_keys: set,
    per_page: int
) -> Tuple[List[PaperMetadata], Dict[str, Any]]:
    # arXiv 仅支持页码/排序有限，日期过滤不直接支持（可在客户端兜底）
    params = {
        "search_query": _arxiv_query_string(query),
        "start": 0,
        "max_results": min(per_page, 100),
        "sortBy": "submittedDate" if (intent.sort_by or "") == "publicationDate" else "relevance",
        "sortOrder": "descending",
    }
    #print(f"[arXiv PARAMS] {params['search_query']}")

    raw_xml = ""
    try:
        async with httpx.AsyncClient(timeout=20.0,follow_redirects=True) as client:
            r = await client.get(ARXIV_URL, params=params, headers={"Accept": "application/atom+xml"})
            r.raise_for_status()
            raw_xml = r.text
    except Exception as e:
        logger.warning("[arXiv] error: %s", repr(e))
        return [], {
            "server_total": 0, "raw_fetched": 0, "raw_unique": 0, "after_filter": None,
            "query": "[arxiv] " + query, "pages": 1
        }

    items: List[PaperMetadata] = []
    try:
        # 解析 Atom
        ns = {
            "atom": "http://www.w3.org/2005/Atom",
            "arxiv": "http://arxiv.org/schemas/atom",
        }
        root = ET.fromstring(raw_xml)
        entries = root.findall("atom:entry", ns)
        for e in entries:
            title = (e.findtext("atom:title", default="", namespaces=ns) or "").strip()
            summary = (e.findtext("atom:summary", default="", namespaces=ns) or "").strip()
            id_url = (e.findtext("atom:id", default="", namespaces=ns) or "").strip()
            published = (e.findtext("atom:published", default="", namespaces=ns) or "").strip()  # 2024-03-01T...
            pub_date = published[:10] if len(published) >= 10 else None
            pub_year = None
            if pub_date and re.match(r"^\d{4}-\d{2}-\d{2}$", pub_date):
                pub_year = int(pub_date[:4])

            authors = []
            for au in e.findall("atom:author", ns):
                nm = (au.findtext("atom:name", default="", namespaces=ns) or "").strip()
                if nm:
                    authors.append(nm)

            doi = None
            doi_el = e.find("arxiv:doi", ns)
            if doi_el is not None and (doi_el.text or "").strip():
                doi = _clean_doi(doi_el.text.strip())

            pm = PaperMetadata(
                title=title,
                authors=_first_n(authors),
                abstract=_truncate(summary, 4000),
                year=pub_year,
                doi=doi,
                journal="arXiv",
                url=id_url,
                citations=None,  # arXiv 不提供引用
                influential_citations=None,
                open_access=True,
                publication_types=["preprint"],
                publication_date=pub_date,
                fields_of_study=[],
            )
            k = _unique_key(pm)
            if k in ax_seen_keys:
                continue
            ax_seen_keys.add(k)
            items.append(pm)
    except Exception as e:
        logger.warning("[arXiv] parse error: %s", repr(e))
        # 解析失败当作 0
        return [], {
            "server_total": None, "raw_fetched": 0, "raw_unique": 0, "after_filter": None,
            "query": "[arxiv] " + query, "pages": 1
        }

    stats = {
        "server_total": None,
        "raw_fetched": len(items),
        "raw_unique": len(items),
        "after_filter": None,
        "query": "[arxiv] " + query,
        "pages": 1,
    }
    return items, stats


#  ==========================================================
# 单来源检索：PubMed（最小实现 + 页内去重）
#  ==========================================================
async def _search_pubmed_single_query(
    query: str,
    intent: SearchIntent,
    pm_seen_keys: set,
    per_page: int
) -> Tuple[List[PaperMetadata], Dict[str, Any]]:
    # term 组合（简单 AND）
    term = (query or "").strip()
    if term == "*" or not term:
        term = "machine learning"
    # 日期过滤：PubMed 支持 [PDAT] 过滤
    date_filter = ""
    if intent.date_start and intent.date_end:
        date_filter = f' AND ("{intent.date_start}"[Date - Publication] : "{intent.date_end}"[Date - Publication])'
    elif intent.date_start and not intent.date_end:
        date_filter = f' AND ("{intent.date_start}"[Date - Publication] : "3000"[Date - Publication])'
    elif intent.date_end and not intent.date_start:
        date_filter = f' AND ("1800"[Date - Publication] : "{intent.date_end}"[Date - Publication])'
    term = term + date_filter

    # 1) ESearch
    ids: List[str] = []
    try:
        params = {
            "db": "pubmed",
            "term": term,
            "retmax": min(per_page, 200),
            "retmode": "json",
            "sort": "pub_date" if (intent.sort_by or "") == "publicationDate" else None,
        }
        params = {k: v for k, v in params.items() if v is not None}
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(f"{PUBMED_EUTILS}/esearch.fcgi", params=params)
            r.raise_for_status()
            j = r.json()
            ids = (j.get("esearchresult") or {}).get("idlist", []) or []
    except Exception as e:
        logger.warning("[PubMed] esearch error: %s", repr(e))
        return [], {"server_total": 0, "raw_fetched": 0, "raw_unique": 0, "after_filter": None,
                    "query": "[pubmed] " + query, "pages": 1}

    if not ids:
        return [], {"server_total": 0, "raw_fetched": 0, "raw_unique": 0, "after_filter": None,
                    "query": "[pubmed] " + query, "pages": 1}

    # 2) ESummary
    items: List[PaperMetadata] = []
    try:
        params2 = {
            "db": "pubmed",
            "id": ",".join(ids),
            "retmode": "json",
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(f"{PUBMED_EUTILS}/esummary.fcgi", params=params2)
            r.raise_for_status()
            j = r.json()
            result = j.get("result") or {}
            for pid in ids:
                v = result.get(pid) or {}
                title = (v.get("title") or "").strip()
                authors = []
                for au in (v.get("authors") or []):
                    nm = (au.get("name") or "").strip()
                    if nm:
                        authors.append(nm)
                pubdate = (v.get("pubdate") or "").strip()  # e.g., "2024 Jan 05"
                # 尝试标准化日期（尽可能 YYYY-MM-DD）
                pub_year = None
                pub_date = None
                m = re.search(r"(\d{4})", pubdate)
                if m:
                    pub_year = int(m.group(1))
                # 简单猜测月日
                try:
                    dt = date.fromisoformat(pubdate)
                    pub_date = dt.isoformat()
                except Exception:
                    # 粗略兜底：只要有年份
                    if pub_year:
                        pub_date = f"{pub_year:04d}-01-01"

                venue = (v.get("fulljournalname") or v.get("source") or "").strip()
                # DOI
                doi = None
                for eid in (v.get("articleids") or []):
                    if (eid.get("idtype") or "").lower() == "doi":
                        doi = _clean_doi(eid.get("value"))
                        break

                pm = PaperMetadata(
                    title=title,
                    authors=_first_n(authors),
                    abstract=None,  # 需要 EFetch 才能拿摘要，这里先省略
                    year=pub_year,
                    doi=doi,
                    journal=venue,
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
                    citations=None,  # PubMed 不提供引用
                    influential_citations=None,
                    open_access=False,  # PubMed 本身不是 OA 仓库
                    publication_types=_first_n(v.get("pubtype") or ["journal-article"], 5),
                    publication_date=pub_date,
                    fields_of_study=["Biomedicine"],
                )
                k = _unique_key(pm)
                if k in pm_seen_keys:
                    continue
                pm_seen_keys.add(k)
                items.append(pm)
    except Exception as e:
        logger.warning("[PubMed] esummary error: %s", repr(e))
        return [], {"server_total": 0, "raw_fetched": 0, "raw_unique": 0, "after_filter": None,
                    "query": "[pubmed] " + query, "pages": 1}

    stats = {
        "server_total": None,
        "raw_fetched": len(items),
        "raw_unique": len(items),
        "after_filter": None,
        "query": "[pubmed] " + query,
        "pages": 1,
    }
    return items, stats

#  ==========================================================
# 单来源检索：Europe PMC（最小实现 + 页内去重）
#  ==========================================================

async def _search_eupmc_single_query(
    query: str,
    intent: SearchIntent,
    ep_seen_keys: set,
    per_page: int
) -> Tuple[List[PaperMetadata], Dict[str, Any]]:
    # 组合 query：关键词 AND 日期
    q = (query or "").strip()
    if not q or q == "*":
        q = "machine learning"
    date_q = ""
    if intent.date_start and intent.date_end:
        date_q = f' AND FIRST_PDATE:[{intent.date_start} TO {intent.date_end}]'
    elif intent.date_start and not intent.date_end:
        date_q = f' AND FIRST_PDATE:[{intent.date_start} TO 3000-12-31]'
    elif intent.date_end and not intent.date_start:
        date_q = f' AND FIRST_PDATE:[1800-01-01 TO {intent.date_end}]'
    full_q = q + date_q

    params = {
        "query": full_q,
        "format": "json",
        "pageSize": min(per_page, 1000),
        "resultType": "core",
        # Europe PMC 的 sort 用法与参数较多，这里让服务端按默认相关性；时间排序交给上层 rank
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(EUPMC_URL, params=params)
            r.raise_for_status()
            j = r.json() or {}
    except Exception as e:
        logger.warning("[EuropePMC] error: %s", repr(e))
        return [], {"server_total": 0, "raw_fetched": 0, "raw_unique": 0, "after_filter": None,
                    "query": "[eupmc] " + query, "pages": 1}

    recs = (j.get("resultList") or {}).get("result") or []
    items: List[PaperMetadata] = []
    for it in recs:
        title = (it.get("title") or "").strip()
        venue = (it.get("journalInfo") or {}).get("journal", {}).get("title") or it.get("source")
        pub_date = it.get("firstPublicationDate") or it.get("pubYear")
        year = None
        if it.get("pubYear"):
            try:
                year = int(it["pubYear"])
            except Exception:
                year = None
        elif pub_date and isinstance(pub_date, str) and re.match(r"^\d{4}", pub_date):
            try:
                year = int(pub_date[:4])
            except Exception:
                year = None

        authors = []
        for au in (it.get("authorList") or {}).get("author", []) or []:
            nm = " ".join([au.get("firstName") or "", au.get("lastName") or ""]).strip()
            if not nm:
                nm = au.get("fullName") or ""
            if nm:
                authors.append(nm)

        doi = _clean_doi(it.get("doi"))
        url = it.get("fullTextUrlList", {})
        ft_urls = url.get("fullTextUrl", []) if isinstance(url, dict) else []
        landing = it.get("pmcid") and f"https://europepmc.org/abstract/MED/{it.get('pmid')}"  # 兜底
        best_url = None
        for u in ft_urls:
            if u.get("url"):
                best_url = u["url"]
                break
        if not best_url:
            best_url = it.get("id") or landing

        pm = PaperMetadata(
            title=title,
            authors=_first_n(authors),
            abstract=None,  # EuropePMC 支持 fetch 摘要，这里先省略
            year=year,
            doi=doi,
            journal=venue,
            url=best_url,
            citations=int(it.get("citedByCount") or 0),
            influential_citations=None,
            open_access=bool(it.get("isOpenAccess")),
            publication_types=[it.get("pubType")] if it.get("pubType") else [],
            publication_date=pub_date if (pub_date and len(str(pub_date)) >= 4) else None,
            fields_of_study=["Biomedicine"],
        )
        k = _unique_key(pm)
        if k in ep_seen_keys:
            continue
        ep_seen_keys.add(k)
        items.append(pm)

    stats = {
        "server_total": None,
        "raw_fetched": len(items),
        "raw_unique": len(items),
        "after_filter": None,
        "query": "[eupmc] " + query,
        "pages": 1,
    }
    return items, stats


# # =========================================================
# # 主入口：多来源检索聚合
# # =========================================================
# async def search_papers(intent: SearchIntent) -> Tuple[List[PaperMetadata], Dict[str, Any]]:
#     """
#     新逻辑：
#     for query in queries:
#         s2_collected = _search_s2_single_query(... 仅服务器过滤、内部去重)
#         oa_collected = _search_openalex_single_query(...)
#       + cr_collected = _search_crossref_single_query(...)
#       -> 在聚合层“统一做客户端兜底过滤 + 统计每种来源”
#     最后：三来源汇总 + 跨来源末端去重，返回 (collected_final, combined_stats)
#     """
#     queries = _build_query_combinations(intent)

#     # 计算 per_page / max_pages（S2 可翻页，其他来源本实现只取第一页）
#     per_page = min(max(intent.max_results * 3, 50), 100) if _HAS_KEY else max(intent.max_results * 2, 50)
#     max_pages = 4 if _HAS_KEY else 2

#     # 各来源“内部去重”用的 seen sets（仅作用于该来源内，跨查询共享）
#     s2_seen_keys: set = set()
#     oa_seen_keys: set = set()
#     cr_seen_keys: set = set()

#     # 各来源在“客户端兜底过滤后”的累计结果
#     s2_all_collected: List[PaperMetadata] = []
#     oa_all_collected: List[PaperMetadata] = []
#     cr_all_collected: List[PaperMetadata] = []

#     # 汇总统计器（逐来源）
#     per_source_stats: Dict[str, Dict[str, int]] = {
#         "s2": {"raw_fetched": 0, "raw_unique": 0, "after_filter": 0, "pages": 0},
#         "openalex": {"raw_fetched": 0, "raw_unique": 0, "after_filter": 0, "pages": 0},
#         "crossref": {"raw_fetched": 0, "raw_unique": 0, "after_filter": 0, "pages": 0},
#     }

#     # 记录每个来源的“查询文本”（便于调试）
#     per_source_queries: List[str] = []

#     for i, query in enumerate(queries, 1):
#         logger.info(f"[MULTI] ===== Executing query {i}/{len(queries)}: {query}")

#         # 1) S2：仅服务器过滤 + 内部去重（不做客户端兜底）
#         s2_collected_raw, s2_stats = await _search_s2_single_query(
#             query, intent, s2_seen_keys, per_page, max_pages
#         )
#         per_source_stats["s2"]["raw_fetched"] += int(s2_stats.get("raw_fetched") or 0)
#         per_source_stats["s2"]["raw_unique"]  += int(s2_stats.get("raw_unique")  or 0)
#         per_source_stats["s2"]["pages"]       += int(s2_stats.get("pages")       or 0)
#         per_source_queries.append(s2_stats.get("query") or f"[s2] {query}")

#         # 2) OpenAlex：内部去重
#         oa_collected_raw, oa_stats = await _search_openalex_single_query(
#             query, intent, oa_seen_keys, per_page
#         )
#         per_source_stats["openalex"]["raw_fetched"] += int(oa_stats.get("raw_fetched") or 0)
#         per_source_stats["openalex"]["raw_unique"]  += int(oa_stats.get("raw_unique")  or 0)
#         per_source_stats["openalex"]["pages"]       += int(oa_stats.get("pages")       or 0)
#         per_source_queries.append(oa_stats.get("query") or f"[openalex] {query}")

#         # 3) Crossref：内部去重
#         cr_collected_raw, cr_stats = await _search_crossref_single_query(
#             query, intent, cr_seen_keys, per_page
#         )
#         per_source_stats["crossref"]["raw_fetched"] += int(cr_stats.get("raw_fetched") or 0)
#         per_source_stats["crossref"]["raw_unique"]  += int(cr_stats.get("raw_unique")  or 0)
#         per_source_stats["crossref"]["pages"]       += int(cr_stats.get("pages")       or 0)
#         per_source_queries.append(cr_stats.get("query") or f"[crossref] {query}")

#         # 4) 聚合层：统一做“客户端兜底过滤”（三来源同标准）
#         def _apply_client_filter(items: List[PaperMetadata]) -> List[PaperMetadata]:
#             kept = []
#             for p in items:
#                 reason = _why_reject(p, intent)
#                 if reason is None:
#                     kept.append(p)
#             return kept

#         s2_kept = _apply_client_filter(s2_collected_raw)
#         oa_kept = _apply_client_filter(oa_collected_raw)
#         cr_kept = _apply_client_filter(cr_collected_raw)

#         # 5) 统计 after_filter（来源内部）
#         per_source_stats["s2"]["after_filter"]       += len(s2_kept)
#         per_source_stats["openalex"]["after_filter"] += len(oa_kept)
#         per_source_stats["crossref"]["after_filter"] += len(cr_kept)

#         # 6) 累计各来源“客户端过滤后”的集合（还未跨来源末端去重）
#         s2_all_collected.extend(s2_kept)
#         oa_all_collected.extend(oa_kept)
#         cr_all_collected.extend(cr_kept)

#     # ===== 最终：三来源合并 + 跨来源末端去重 =====
#     merged_all: List[PaperMetadata] = s2_all_collected + oa_all_collected + cr_all_collected

#     global_final_seen: set = set()
#     collected_final: List[PaperMetadata] = []
#     for p in merged_all:
#         k = _unique_key(p)
#         if k in global_final_seen:
#             continue
#         global_final_seen.add(k)
#         collected_final.append(p)

#     # ===== 组合统计 =====
#     individual_stats = [
#         {"source": "s2", **per_source_stats["s2"]},
#         {"source": "openalex", **per_source_stats["openalex"]},
#         {"source": "crossref", **per_source_stats["crossref"]},
#     ]
#     total_raw_fetched = sum(s.get("raw_fetched", 0) for s in per_source_stats.values())
#     total_raw_unique  = sum(s.get("raw_unique",  0) for s in per_source_stats.values())
#     total_after_filter = sum(s.get("after_filter", 0) for s in per_source_stats.values())
#     total_pages       = sum(s.get("pages",       0) for s in per_source_stats.values())

#     per_source_after_filter = {k: v.get("after_filter", 0) for k, v in per_source_stats.items()}

#     combined_stats = {
#         "query_combinations": len(queries),
#         "queries": per_source_queries,  # 含各来源标注的查询文本
#         "per_page": per_page,
#         "total_pages": total_pages,

#         # 原汇总指标
#         "total_raw_fetched": total_raw_fetched,
#         "total_raw_unique": total_raw_unique,
#         "total_after_filter": total_after_filter,
#         "final_unique_count": len(collected_final),

#         # 逐来源指标（你提的需求）
#         "per_source_after_filter": per_source_after_filter,
#         "total_after_filter_s2":       per_source_after_filter.get("s2", 0),
#         "total_after_filter_openalex": per_source_after_filter.get("openalex", 0),
#         "total_after_filter_crossref": per_source_after_filter.get("crossref", 0),

#         # 细项
#         "individual_stats": individual_stats,
#     }

#     logger.info(
#         "[MULTI] FINAL merged: fetched=%d unique=%d after_filter=%d final=%d",
#         total_raw_fetched, total_raw_unique, total_after_filter, len(collected_final)
#     )
#     return collected_final, combined_stats


# =========================================================
# 主入口：多来源检索聚合（动态来源，s2 必选）
# =========================================================
async def search_papers(intent: SearchIntent) -> Tuple[List[PaperMetadata], Dict[str, Any]]:
    """
    - 根据 intent.enabled_sources 动态选择来源（1~3 个；强制包含 s2；默认 ["s2"]）
    - 仅对“被选中”的来源发起检索
    - 来源内：仅服务器过滤 + 内部去重
    - 聚合层：统一兜底过滤 + 逐来源统计
    - 末端：跨来源最终去重
    返回：(collected_final, combined_stats)
    """
    # ---------- 允许的来源 & 选择 ----------
    # 你可以把这个集合与 schemas.ALOWED_SOURCES 保持一致
    ALLOWED_SOURCES = ["s2", "openalex", "crossref", "arxiv", "pubmed", "eupmc"]

    def _normalize_sources(srcs: Optional[List[str]]) -> List[str]:
        # 兜底
        if not srcs:
            srcs = ["s2"]
        # 规范化 + 去重，保顺序
        seen = set()
        norm = []
        for x in srcs:
            if not isinstance(x, str):
                continue
            k = x.strip().lower()
            if k in ALLOWED_SOURCES and k not in seen:
                seen.add(k)
                norm.append(k)
        # 强制包含 s2
        if "s2" not in seen:
            norm = ["s2"] + norm
            seen.add("s2")
        # 限制 1~3 个（始终包含 s2）
        if len(norm) > 3:
            others = [x for x in norm if x != "s2"]
            norm = ["s2"] + others[:2]
        return norm or ["s2"]

    selected_sources = _normalize_sources(getattr(intent, "enabled_sources", None))

    # ---------- 查询组合、分页 ----------
    queries = _build_query_combinations(intent)
    per_page = min(max(intent.max_results * 3, 50), 100) if _HAS_KEY else max(intent.max_results * 2, 50)
    max_pages = 4 if _HAS_KEY else 2

    # ---------- provider 映射（适配不同签名） ----------
    # 注意：未实现的来源先占位 None，会被自动跳过
    providers: Dict[str, Any] = {
        "s2":       lambda q, seen: _search_s2_single_query(q, intent, seen, per_page, max_pages),
        "openalex": lambda q, seen: _search_openalex_single_query(q, intent, seen, per_page),
        "crossref": lambda q, seen: _search_crossref_single_query(q, intent, seen, per_page),
        "arxiv":    lambda q, seen: _search_arxiv_single_query(q, intent, seen, per_page),
        "pubmed":   lambda q, seen: _search_pubmed_single_query(q, intent, seen, per_page),
        "eupmc":    lambda q, seen: _search_eupmc_single_query(q, intent, seen, per_page),
    }

    # seen/结果/统计
    seen_map: Dict[str, set] = {src: set() for src in selected_sources}
    collected_map: Dict[str, List[PaperMetadata]] = {src: [] for src in selected_sources}
    per_source_stats: Dict[str, Dict[str, int]] = {
        src: {"raw_fetched": 0, "raw_unique": 0, "after_filter": 0, "pages": 0}
        for src in selected_sources
    }
    per_source_queries: List[str] = []

    # 统一兜底过滤
    def _apply_client_filter(items: List[PaperMetadata]) -> List[PaperMetadata]:
        kept = []
        for p in items:
            if _why_reject(p, intent) is None:
                kept.append(p)
        return kept

    # ---------- 检索执行（按 query × selected_sources） ----------
    for i, query in enumerate(queries, 1):
        if( not query or query.strip() == "" or query.strip() == "*" ):
            continue
        logger.info(f"[MULTI] ===== Executing query {i}/{len(queries)}: {query}")
        for src in selected_sources:
            search_fn = providers.get(src)
            if search_fn is None:
                # 未实现的来源，记录 0 并跳过
                logger.info(f"[{src}] provider not implemented, skip this source")
                per_source_queries.append(f"[{src}] {query}")
                continue
            try:
                raw_items, s = await search_fn(query, seen_map[src])
            except Exception as e:
                logger.warning(f"[{src}] error: {repr(e)}")
                raw_items, s = [], {"raw_fetched": 0, "raw_unique": 0, "pages": 0, "query": f"[{src}] {query}"}

            st = per_source_stats[src]
            st["raw_fetched"] += int(s.get("raw_fetched") or 0)
            st["raw_unique"]  += int(s.get("raw_unique")  or 0)
            st["pages"]       += int(s.get("pages")       or 0)
            per_source_queries.append(s.get("query") or f"[{src}] {query}")

            kept = _apply_client_filter(raw_items)
            st["after_filter"] += len(kept)
            collected_map[src].extend(kept)

    # ---------- 合并 + 跨来源末端去重 ----------
    merged_all: List[PaperMetadata] = []
    for src in selected_sources:
        merged_all.extend(collected_map.get(src, []))

    global_final_seen: set = set()
    collected_final: List[PaperMetadata] = []
    for p in merged_all:
        k = _unique_key(p)
        if k in global_final_seen:
            continue
        global_final_seen.add(k)
        collected_final.append(p)

    # ---------- 组合统计（动态来源） ----------
    individual_stats = [{"source": src, **per_source_stats[src]} for src in selected_sources]
    total_raw_fetched  = sum(s.get("raw_fetched",  0) for s in per_source_stats.values())
    total_raw_unique   = sum(s.get("raw_unique",   0) for s in per_source_stats.values())
    total_after_filter = sum(s.get("after_filter", 0) for s in per_source_stats.values())
    total_pages        = sum(s.get("pages",        0) for s in per_source_stats.values())

    per_source_after_filter = {src: per_source_stats[src].get("after_filter", 0) for src in selected_sources}

    # —— 动态展开：为所有 ALLOWED_SOURCES 输出 total_after_filter_<src>（未选中则 0）
    expanded_totals = {
        f"total_after_filter_{src}": per_source_after_filter.get(src, 0) for src in ALLOWED_SOURCES
    }

    # —— 向后兼容三项：未选中也返回 0，避免前端报 KeyError
    def _af(src: str) -> int:
        return per_source_after_filter.get(src, 0)

    combined_stats = {
        "selected_sources": selected_sources,
        "query_combinations": len(queries),
        "queries": per_source_queries,
        "per_page": per_page,
        "total_pages": total_pages,

        "total_raw_fetched": total_raw_fetched,
        "total_raw_unique": total_raw_unique,
        "total_after_filter": total_after_filter,
        "final_unique_count": len(collected_final),

        "per_source_after_filter": per_source_after_filter,
        # 旧字段（保兼容）
        "total_after_filter_s2":       _af("s2"),
        "total_after_filter_openalex": _af("openalex"),
        "total_after_filter_crossref": _af("crossref"),

        # 新增：动态展开（六个来源全覆盖；未选为 0）
        **expanded_totals,

        "individual_stats": individual_stats,
    }

    logger.info(
        "[MULTI] FINAL merged: sources=%s fetched=%d unique=%d after_filter=%d final=%d",
        ",".join(selected_sources), total_raw_fetched, total_raw_unique, total_after_filter, len(collected_final)
    )
    return collected_final, combined_stats
