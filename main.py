# paper_survey/main.py

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
import config  # 确保 .env 文件被加载
from llm_parser import parse_user_intent
from s2_client import search_papers
from ranking import rank_papers
from schemas import PaperMetadata  # 和可选的 SearchResponse
import json, os
from logging_setup import setup_logging


setup_logging(os.getenv("LOG_LEVEL", "DEBUG"))

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
        # logger.info(f"[INTENT] {intent.dict()}")

        # 2) 调 S2 + 过滤（拿回统计）
        papers, batch, stats = await search_papers(intent)

        # 3) 排序 + 截断
        papers_sorted = rank_papers(papers, mode=intent.sort_by)
        papers_final = papers_sorted[: intent.max_results]

        # 4) 组织返回
        api_params = {
            "endpoint": "graph/v1/paper/search/bulk",
            "query_combinations": stats.get("query_combinations"),
            "queries": stats.get("queries"),
        }

        counts = {
            "query_combinations": stats.get("query_combinations"),
            "total_raw_fetched": stats.get("total_raw_fetched"),
            "total_raw_unique": stats.get("total_raw_unique"),
            "final_unique_count": stats.get("final_unique_count"),
            "after_rank_cut": len(papers_final),
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
        # logger.exception("search failed")
        return {
            "query": user_query,
            "error": str(e),
            "results": [],
            "counts": {
                "query_combinations": 0,
                "total_raw_fetched": 0,
                "total_raw_unique": 0,
                "final_unique_count": 0,
                "after_rank_cut": 0
            },
        }
