# paper_survey/task_executor.py
"""
Background task execution for async search tasks.
Orchestrates the full search pipeline with progress tracking.
"""

import logging
from typing import Dict, Any

import config
import task_store
from llm_parser import parse_user_intent
from search_multi import search_papers
from ranking import rank_papers
from schemas import TaskStatus

logger = logging.getLogger("paper_survey.task_executor")


async def execute_search_task(task_id: str, user_query: str) -> None:
    """
    Execute the full search pipeline asynchronously for a task.

    This function runs in the background and updates task progress at each stage:
    - parsing: LLM intent parsing
    - searching: Multi-source paper search
    - ranking: Result sorting and truncation
    - completed: Final results ready
    - failed: Error occurred

    Args:
        task_id: UUID of the task
        user_query: User's search query
    """
    import time
    start_time = time.time()

    try:
        logger.info(f"Task {task_id} starting execution")

        # ========== Stage 1: Parsing ==========
        await task_store.update_task_status(task_id, TaskStatus.parsing)
        logger.info(f"Task {task_id}: Parsing user intent")

        try:
            intent = await parse_user_intent(user_query)

            # Validate max_results limit
            if intent.max_results > config.MAX_RESULTS_LIMIT:
                await task_store.fail_task(
                    task_id,
                    f"max_results ({intent.max_results}) exceeds limit of {config.MAX_RESULTS_LIMIT}"
                )
                return
        except Exception as e:
            logger.exception(f"Task {task_id}: Intent parsing failed")
            await task_store.fail_task(task_id, f"Failed to parse query: {str(e)}")
            return

        # ========== Stage 2: Searching ==========
        await task_store.update_task_status(task_id, TaskStatus.searching)
        logger.info(f"Task {task_id}: Starting multi-source search")

        # Define progress callback that updates task store
        async def progress_callback(
            source: str,
            status: str,
            fetched: int = 0,
            total: int | None = None,
            errors: list[str] | None = None
        ):
            """Update source progress in task store"""
            await task_store.update_source_progress(
                task_id=task_id,
                source=source,
                status=status,
                fetched=fetched,
                total=total,
                errors=errors
            )

        try:
            papers, stats = await search_papers(intent, progress_callback=progress_callback)
        except Exception as e:
            logger.exception(f"Task {task_id}: Search failed")
            await task_store.fail_task(task_id, f"Search failed: {str(e)}")
            return

        # ========== Stage 3: Ranking ==========
        await task_store.update_task_status(task_id, TaskStatus.ranking)
        logger.info(f"Task {task_id}: Ranking results")

        try:
            papers_sorted = rank_papers(papers, mode=intent.sort_by)
            papers_final = papers_sorted[:intent.max_results]
        except Exception as e:
            logger.exception(f"Task {task_id}: Ranking failed")
            await task_store.fail_task(task_id, f"Ranking failed: {str(e)}")
            return

        # ========== Build Response (same format as /search) ==========
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
                f"Task {task_id}: Large request completed: max_results={intent.max_results}, "
                f"final_count={len(papers_final)}, execution_time={elapsed:.1f}s, "
                f"sources={intent.enabled_sources}"
            )

        response = {
            "query": user_query,
            "normalized_intent": intent.dict(),
            "api_params": api_params,
            "counts": counts,
            "results": result,
        }

        # ========== Stage 4: Completed ==========
        await task_store.complete_task(task_id, response)
        logger.info(f"Task {task_id} completed successfully with {len(papers_final)} results")

    except Exception as e:
        # Catch any unexpected errors
        logger.exception(f"Task {task_id}: Unexpected error during execution")
        await task_store.fail_task(task_id, f"Unexpected error: {str(e)}")
