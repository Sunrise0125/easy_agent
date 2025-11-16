import httpx
import asyncio


async def search_papers_async(query: str, poll_interval: float = 2.0):
    """调用异步论文检索 API，支持进度跟踪"""
    async with httpx.AsyncClient(timeout=120.0) as client:
        # 1. 创建任务
        create_response = await client.post(
            "http://localhost:8066/tasks/search",
            json={"user_query": query}
        )

        if create_response.status_code != 202:
            raise Exception(f"创建任务失败: {create_response.text}")

        task_data = create_response.json()
        task_id = task_data["task_id"]
        print(f"任务已创建: {task_id}")

        # 2. 轮询任务状态
        while True:
            await asyncio.sleep(poll_interval)

            status_response = await client.get(
                f"http://localhost:8066/tasks/{task_id}"
            )

            if status_response.status_code != 200:
                raise Exception(f"查询任务失败: {status_response.text}")

            data = status_response.json()
            status = data.get("status", "unknown")
            progress_data = data.get("progress", {}) or {}
            progress_percent = progress_data.get("overall_percent", 0.0)
            stage_desc = progress_data.get("stage_description", "")

            print(f"状态: {status} ({stage_desc}), 进度: {progress_percent}%")

            # 显示每个来源的进度
            sources = progress_data.get("sources") or {}
            for source, info in sources.items():
                fetched = info.get("fetched", 0)
                s_status = info.get("status", "unknown")
                print(f"  - {source}: {s_status}, 已获取 {fetched} 篇")

            # 任务完成或失败
            if status == "completed":
                print("\n✅ 任务完成！")
                # 这里返回整个 data，让外层用 data['results'] 访问
                return data

            elif status == "failed":
                error = data.get("error", "未知错误")
                raise Exception(f"任务失败: {error}")


async def main():
    query = "agentic image restoration相关的论文，按相关性排序，返回20篇"

    try:
        result = await search_papers_async(query, poll_interval=1.0)

        # 假设后端返回结构：{"status": "...", "progress": {...}, "results": [...]}
        papers = result.get("results", []).get("results", [])
        print(f"\n找到 {len(papers)} 篇论文")

        for i, paper in enumerate(papers, 1):
            title = paper.get("title", "无标题")
            authors = paper.get("authors") or []
            year = paper.get("year", "未知年份")
            citations = paper.get("citations", 0)
            pdf_url = paper.get("pdf_url")

            print(f"\n{i}. {title}")
            if authors:
                print(f"   作者: {', '.join(authors[:3])}")
            print(f"   年份: {year} | 引用: {citations}")
            print(f"   PDF: {pdf_url}")

    except Exception as e:
        print(f"❌ 错误: {e}")


if __name__ == "__main__":
    # 在普通 .py 脚本里这样运行异步 main
    asyncio.run(main())
