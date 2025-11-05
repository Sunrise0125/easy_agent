# paper_survey/main.py

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from .llm_parser import parse_user_intent
from .s2_client import search_papers
from .ranking import rank_papers
from .schemas import PaperMetadata  # 和可选的 SearchResponse
import json
import logging

logger = logging.getLogger("paper_survey.app")

app = FastAPI(title="PaperFinder Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

@app.get("/search")
async def search(user_query: str = Query(...)):
    try:
        # 1) 解析意图
        intent = await parse_user_intent(user_query)
        logger.info(f"[INTENT] {intent.dict()}")

        # 2) 调 S2 + 过滤（拿回统计）
        papers, stats = await search_papers(intent)

        # 3) 排序 + 截断
        papers_sorted = rank_papers(papers, mode=intent.sort_by)
        papers_final = papers_sorted[: intent.max_results]

        # 4) 组织返回
        api_params = {
            "endpoint": "graph/v1/paper/search/bulk",
            "s2_query_built": stats.get("query"),
            "server_params": stats.get("params_used"),
        }

        counts = {
            "server_total": stats.get("server_total"),            # 服务器报告的 total（若有）
            "raw_fetched": stats.get("raw_fetched"),              # 你实际抓到的条数（过滤前）
            "after_filter": stats.get("after_filter"),            # 客户端过滤后
            "after_rank_cut": len(papers_final),                  # 排序+截断后的条数（返回给前端）
        }

        result = [p.dict() for p in papers_final]
       

        return {
            "query": user_query,
            "normalized_intent": intent.dict(),
            "api_params": api_params,
            "counts": counts,
            "results": result,
            
        }
    except Exception as e:
        logger.exception("search failed")
        return {
            "query": user_query,
            "error": str(e),
            "results": [],
            "counts": {"server_total": None, "raw_fetched": 0, "after_filter": 0, "after_rank_cut": 0},
        }
