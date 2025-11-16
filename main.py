# paper_survey/main.py

from fastapi import FastAPI, Query, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
import config  # 确保 .env 文件被加载
from llm_parser import parse_user_intent
#from s2_client import search_papers
from search_multi import search_papers
from ranking import rank_papers
from schemas import PaperMetadata, SearchTaskRequest  # 和可选的 SearchResponse
import json, os
from logging_setup import setup_logging
import task_store
import task_executor
import logging

logger = logging.getLogger("paper_survey.main")


setup_logging(os.getenv("LOG_LEVEL", "DEBUG"))

app = FastAPI(title="PaperFinder Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

@app.on_event("startup")
async def startup_event():
    """Initialize background jobs on startup"""
    task_store.start_cleanup_background_job()
    logger.info("Application started successfully")

@app.get("/search")
async def search(user_query: str = Query(...)):
    import time
    start_time = time.time()

    try:
        # 1) 解析意图
        intent = await parse_user_intent(user_query)
        # logger.info(f"[INTENT] {intent.dict()}")

        # 1.5) 验证 max_results 限制
        if intent.max_results > config.MAX_RESULTS_LIMIT:
            raise HTTPException(
                status_code=400,
                detail=f"max_results ({intent.max_results}) exceeds limit of {config.MAX_RESULTS_LIMIT}"
            )

        # 2) 调 S2 + 过滤（拿回统计）
        papers, stats = await search_papers(intent)
      # papers,stats = await search_multi(intent, papers)
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

        # Performance logging for large requests
        elapsed = time.time() - start_time
        if intent.max_results > 100:
            logger.info(
                f"Large request completed: max_results={intent.max_results}, "
                f"final_count={len(papers_final)}, execution_time={elapsed:.1f}s, "
                f"sources={intent.enabled_sources}"
            )

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

# ============= Async Task API Endpoints =============

@app.post("/tasks/search", status_code=202)
async def create_search_task(request: SearchTaskRequest, background_tasks: BackgroundTasks):
    """
    Create an asynchronous search task.

    Returns task_id immediately while search executes in background.
    Use GET /tasks/{task_id} to poll for progress and results.
    """
    try:
        # Validate query length
        if not request.user_query or len(request.user_query.strip()) == 0:
            raise HTTPException(status_code=400, detail="user_query is required and cannot be empty")

        # Create task in store
        task_id = await task_store.create_task(request.user_query)

        # Start background execution
        background_tasks.add_task(task_executor.execute_search_task, task_id, request.user_query)

        task = await task_store.get_task(task_id)

        return {
            "task_id": task_id,
            "status": task.status.value,
            "created_at": task.created_at.isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to create search task")
        raise HTTPException(status_code=500, detail=f"Failed to create task: {str(e)}")


@app.get("/tasks/{task_id}")
async def get_task_status(task_id: str):
    """
    Get status and results of an async search task.

    Returns:
    - In progress: status, progress info
    - Completed: status, progress, full results
    - Failed: status, error message
    """
    task = await task_store.get_task(task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Build base response
    response = {
        "task_id": task.task_id,
        "status": task.status.value,
        "progress": task.progress.dict(),
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat()
    }

    # Add results if completed
    if task.status.value == "completed":
        response["results"] = task.results
        if task.completed_at:
            response["completed_at"] = task.completed_at.isoformat()

    # Add error if failed
    if task.status.value == "failed":
        if task.errors:
            response["error"] = task.errors[-1].get("message", "Unknown error")

    # Add source errors if any
    if task.errors:
        response["errors"] = task.errors

    return response
