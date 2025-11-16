# paper_survey/task_store.py
"""
In-memory task storage and management for async search tasks.
Provides thread-safe operations for creating, updating, and retrieving task states.
"""

import asyncio
import uuid
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Any

from schemas import TaskState, TaskStatus, TaskProgress, SourceProgress, SourceStatus

logger = logging.getLogger("paper_survey.task_store")

# Global in-memory storage
_tasks: Dict[str, TaskState] = {}
_tasks_lock = asyncio.Lock()


def _calculate_overall_percent(stage: str, sources: Dict[str, SourceProgress]) -> int:
    """
    Calculate overall progress percentage (0-100) based on fixed milestones.

    进度分配（4个主要阶段，平均分配）：
    - 创建任务: 0%
    - 解析意图: 25% (第1阶段完成)
    - 搜索文献: 25% → 75% (第2阶段，根据完成的来源数动态计算)
    - 排序结果: 75% → 100% (第3阶段)

    Args:
        stage: Current execution stage
        sources: Dictionary of source progress states

    Returns:
        Integer percentage from 0-100
    """
    if stage == "created":
        return 0
    elif stage == "parsing":
        return 25  # 第1阶段完成
    elif stage == "searching":
        if not sources:
            return 25  # 搜索刚开始，还没有来源信息

        # 第2阶段：根据完成的来源数动态计算 (25%-75% 范围)
        total_sources = len(sources)
        completed = sum(1 for s in sources.values() if s.status == SourceStatus.completed)

        # 每个来源完成增加 50% / N 的进度
        return 25 + int((completed / total_sources) * 50)
    elif stage == "ranking":
        return 75  # 第3阶段开始
    elif stage == "completed":
        return 100  # 第4阶段完成
    elif stage == "failed":
        return 0
    else:
        return 0


def _generate_stage_description(stage: str, sources: Dict[str, SourceProgress]) -> str:
    """
    Generate Chinese description for current stage.

    Args:
        stage: Current execution stage
        sources: Dictionary of source progress states

    Returns:
        Chinese description string
    """
    # 来源名称映射（英文 → 中文）
    SOURCE_NAMES = {
        "s2": "Semantic Scholar",
        "openalex": "OpenAlex",
        "arxiv": "arXiv",
        "crossref": "Crossref",
        "pubmed": "PubMed",
        "eupmc": "Europe PMC"
    }

    if stage == "created":
        return "任务已创建"
    elif stage == "parsing":
        return "正在解析查询意图"
    elif stage == "searching":
        if not sources:
            return "准备开始搜索"

        # 查找当前正在进行的来源
        in_progress_sources = [
            SOURCE_NAMES.get(name, name)
            for name, prog in sources.items()
            if prog.status == SourceStatus.in_progress
        ]

        if in_progress_sources:
            # 显示第一个正在进行的来源
            return f"正在搜索 {in_progress_sources[0]}"
        else:
            # 都完成或都待处理
            completed = sum(1 for s in sources.values() if s.status == SourceStatus.completed)
            total = len(sources)
            if completed == total:
                return "文献检索完成"
            else:
                return "正在搜索多个来源"
    elif stage == "ranking":
        return "进入排序阶段"
    elif stage == "completed":
        return "搜索完成"
    elif stage == "failed":
        return "搜索失败"
    else:
        return "处理中"


async def create_task(query: str) -> str:
    """
    Create a new task and store it in memory.

    Args:
        query: User's search query

    Returns:
        Generated task_id (UUID4 string)
    """
    task_id = str(uuid.uuid4())
    now = datetime.utcnow()

    task_state = TaskState(
        task_id=task_id,
        status=TaskStatus.created,
        progress=TaskProgress(
            stage="created",
            stage_description="任务已创建",
            sources={},
            overall_percent=0
        ),
        query=query,
        results=None,
        errors=[],
        created_at=now,
        updated_at=now,
        completed_at=None,
        expires_at=None
    )

    async with _tasks_lock:
        _tasks[task_id] = task_state

    logger.info(f"Created task {task_id} for query: {query[:50]}...")
    return task_id


async def get_task(task_id: str) -> Optional[TaskState]:
    """
    Retrieve task state by ID.

    Args:
        task_id: UUID of the task

    Returns:
        TaskState if found, None otherwise
    """
    async with _tasks_lock:
        return _tasks.get(task_id)


async def update_task_status(task_id: str, status: TaskStatus) -> None:
    """
    Update task status and stage.

    Args:
        task_id: UUID of the task
        status: New TaskStatus value
    """
    async with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            logger.warning(f"Task {task_id} not found for status update")
            return

        task.status = status
        task.progress.stage = status.value
        task.updated_at = datetime.utcnow()
        task.progress.overall_percent = _calculate_overall_percent(
            task.progress.stage,
            task.progress.sources
        )
        task.progress.stage_description = _generate_stage_description(
            task.progress.stage,
            task.progress.sources
        )

    logger.info(f"Task {task_id} status updated: {status.value}")


async def update_source_progress(
    task_id: str,
    source: str,
    status: str,
    fetched: int = 0,
    total: Optional[int] = None,
    errors: Optional[List[str]] = None
) -> None:
    """
    Update progress for a specific search source.

    Args:
        task_id: UUID of the task
        source: Source name (e.g., "s2", "openalex")
        status: Source status ("pending", "in_progress", "completed", "failed")
        fetched: Number of papers fetched so far
        total: Estimated total (if known)
        errors: List of error messages (if any)
    """
    async with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            logger.warning(f"Task {task_id} not found for source progress update")
            return

        # Initialize source if not exists
        if source not in task.progress.sources:
            task.progress.sources[source] = SourceProgress()

        # Update source progress
        source_progress = task.progress.sources[source]
        source_progress.status = SourceStatus(status)
        source_progress.fetched = fetched
        source_progress.total_estimated = total
        if errors:
            source_progress.errors = errors

        # Update overall progress and description
        task.updated_at = datetime.utcnow()
        task.progress.overall_percent = _calculate_overall_percent(
            task.progress.stage,
            task.progress.sources
        )
        task.progress.stage_description = _generate_stage_description(
            task.progress.stage,
            task.progress.sources
        )

    logger.debug(f"Task {task_id} source {source}: {status}, fetched={fetched}")


async def complete_task(task_id: str, results: Dict[str, Any]) -> None:
    """
    Mark task as completed and store results.

    Args:
        task_id: UUID of the task
        results: Complete search results (same format as /search endpoint)
    """
    async with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            logger.warning(f"Task {task_id} not found for completion")
            return

        now = datetime.utcnow()
        task.status = TaskStatus.completed
        task.progress.stage = "completed"
        task.progress.overall_percent = 100
        task.results = results
        task.updated_at = now
        task.completed_at = now
        task.expires_at = now + timedelta(minutes=30)  # TTL: 30 minutes

    logger.info(f"Task {task_id} completed successfully")


async def fail_task(task_id: str, error: str) -> None:
    """
    Mark task as failed and store error message.

    Args:
        task_id: UUID of the task
        error: Error message
    """
    async with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            logger.warning(f"Task {task_id} not found for failure update")
            return

        now = datetime.utcnow()
        task.status = TaskStatus.failed
        task.progress.stage = "failed"
        task.errors.append({
            "message": error,
            "timestamp": now.isoformat()
        })
        task.updated_at = now
        task.completed_at = now
        task.expires_at = now + timedelta(minutes=30)  # TTL: 30 minutes

    logger.error(f"Task {task_id} failed: {error}")


async def cleanup_expired_tasks() -> int:
    """
    Remove expired tasks from memory (TTL-based cleanup).
    Only removes tasks with status 'completed' or 'failed' and expired.

    Returns:
        Number of tasks removed
    """
    now = datetime.utcnow()
    removed_count = 0

    async with _tasks_lock:
        expired_ids = [
            task_id for task_id, task in _tasks.items()
            if task.expires_at is not None and task.expires_at < now
        ]

        for task_id in expired_ids:
            del _tasks[task_id]
            removed_count += 1

    if removed_count > 0:
        logger.info(f"Cleaned up {removed_count} expired tasks")

    return removed_count


async def _cleanup_background_job():
    """
    Background job that runs cleanup every 5 minutes.
    """
    while True:
        await asyncio.sleep(300)  # 5 minutes
        try:
            await cleanup_expired_tasks()
        except Exception as e:
            logger.exception(f"Error in cleanup background job: {e}")


def start_cleanup_background_job():
    """
    Start the background task cleanup job.
    Should be called once during application startup.
    """
    asyncio.create_task(_cleanup_background_job())
    logger.info("Started task cleanup background job (runs every 5 minutes)")
