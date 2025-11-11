# author_hindex.py
import asyncio
from typing import List, Optional, Tuple, Dict
import httpx
import logging
from schemas import PaperMetadata

logger = logging.getLogger("paper_survey.author_hindex")

OPENALEX_AUTHORS = "https://api.openalex.org/authors"
OPENALEX_MAILTO = "2649022496@qq.com"  # 按 OpenAlex 要求填写可联系邮箱
TIMEOUT = 10.0
CONCURRENCY = 8

# 简单缓存：作者名(小写) -> hindex 或 None
_hindex_cache: Dict[str, Optional[int]] = {}

def _norm(name: str) -> str:
    return (name or "").strip().lower()

async def _fetch_hindex(client: httpx.AsyncClient, author_name: str) -> Optional[int]:
    """查第一作者的 h-index（取搜索 Top-1），失败返回 None。带内存缓存。"""
    key = _norm(author_name)
    if not key:
        return None
    if key in _hindex_cache:
        return _hindex_cache[key]

    params = {
        "search": author_name,
        "per-page": 1,
        "mailto": OPENALEX_MAILTO,
    }
    try:
        r = await client.get(OPENALEX_AUTHORS, params=params, timeout=TIMEOUT, follow_redirects=True)
        r.raise_for_status()
        data = r.json() or {}
        results = data.get("results") or []
        if not results:
            _hindex_cache[key] = None
            return None
        summary = (results[0].get("summary_stats") or {})
        h = summary.get("h_index")
        # 有些作者可能没有统计，转成 int/None
        hindex = int(h) if isinstance(h, (int, float)) else None
        _hindex_cache[key] = hindex
        return hindex
    except Exception as e:
        logger.warning("[OpenAlex:first-author-h] %s -> %s", author_name, repr(e))
        _hindex_cache[key] = None
        return None

async def fill_first_author_hindex_async(papers: List[PaperMetadata]) -> List[PaperMetadata]:
    """为每篇论文写入 first_author_hindex。"""
    # 收集第一作者（去重）
    first_authors: List[str] = []
    seen = set()
    for p in papers:
        name = (p.authors[0] if p.authors else "").strip()
        k = _norm(name)
        if k and k not in seen:
            seen.add(k)
            first_authors.append(name)

    if not first_authors:
        return papers

    sem = asyncio.Semaphore(CONCURRENCY)

    async with httpx.AsyncClient(headers={"Accept": "application/json"}) as client:
        async def task(name: str) -> Tuple[str, Optional[int]]:
            async with sem:
                return name, await _fetch_hindex(client, name)

        results = await asyncio.gather(*[task(n) for n in first_authors], return_exceptions=False)

    # 映射：作者名(小写) -> hindex
    name2h = {_norm(n): h for n, h in results}

    # 写回
    for p in papers:
        n = _norm(p.authors[0] if p.authors else "")
        # 用 1 作为默认值兜底
        p.first_author_hindex = name2h.get(n, 1)
    return papers
