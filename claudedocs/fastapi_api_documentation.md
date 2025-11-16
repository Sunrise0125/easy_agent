# PaperFinder Agent - API 调用文档

**服务地址**: `http://localhost:8000` (默认)
**文档更新**: 2025-11-15

---

## 快速开始

### 1. 启动服务

```bash
# 配置环境变量 (.env 文件)
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat
S2_API_KEY=your_s2_api_key
MAX_RESULTS_LIMIT=500  # 最大论文数量限制（可选，默认 500）

# 启动服务
uvicorn main:app --reload --port 8000
```

### 2. 访问文档

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

---

## API 调用

### 同步接口（立即返回结果）

#### 接口地址

```
GET /search?user_query={查询内容}
```

#### 参数说明

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| user_query | string | ✅ | 自然语言查询，支持中英文 |

**适用场景**: 简单查询、快速获取结果

---

### 异步接口（后台执行，支持进度跟踪）

#### 1. 创建搜索任务

```
POST /tasks/search
Content-Type: application/json
```

**请求体**:
```json
{
  "user_query": "深度学习目标检测综述，最近三年"
}
```

**响应** (HTTP 202 Accepted):
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "created",
  "created_at": "2025-11-16T10:00:00Z"
}
```

#### 2. 查询任务状态

```
GET /tasks/{task_id}
```

**响应**（进行中）:
```json
{
  "task_id": "550e8400-...",
  "status": "searching",
  "progress": {
    "stage": "searching",
    "stage_description": "正在搜索 OpenAlex",
    "sources": {
      "s2": {
        "status": "completed",
        "fetched": 120,
        "total_estimated": null,
        "errors": null
      },
      "openalex": {
        "status": "in_progress",
        "fetched": 45,
        "total_estimated": 100,
        "errors": null
      },
      "arxiv": {
        "status": "pending",
        "errors": null
      }
    },
    "overall_percent": 58
  },
  "created_at": "2025-11-16T10:00:00Z",
  "updated_at": "2025-11-16T10:00:05Z"
}
```

**响应**（完成）:
```json
{
  "task_id": "550e8400-...",
  "status": "completed",
  "progress": {
    "stage": "completed",
    "stage_description": "搜索完成",
    "overall_percent": 100
  },
  "results": {
    "query": "深度学习目标检测综述，最近三年",
    "normalized_intent": { ... },
    "api_params": { ... },
    "counts": { ... },
    "results": [ ... ]
  },
  "errors": [],
  "created_at": "2025-11-16T10:00:00Z",
  "completed_at": "2025-11-16T10:00:12Z"
}
```

#### 任务状态说明

| 状态 | 说明 | 进度 | 中文描述示例 |
|------|------|------|-------------|
| `created` | 任务已创建，等待执行 | 0% | "任务已创建" |
| `parsing` | LLM 正在解析查询意图 | 25% | "正在解析查询意图" |
| `searching` | 多来源搜索进行中 | 25%-75% | "正在搜索 Semantic Scholar" |
| `ranking` | 结果排序中 | 75%-100% | "进入排序阶段" |
| `completed` | 搜索完成，结果可用 | 100% | "搜索完成" |
| `failed` | 任务失败（查看 error 字段） | 0% | "搜索失败" |

#### 来源状态说明

| 状态 | 说明 |
|------|------|
| `pending` | 等待开始 |
| `in_progress` | 检索中 |
| `completed` | 该来源完成 |
| `failed` | 该来源失败（不影响整体任务） |

#### 任务生命周期

- 任务完成或失败后，将在 **30 分钟后自动清理**
- 建议客户端获取结果后立即保存
- 任务 ID 采用 UUID4 格式，随机生成

**适用场景**: 复杂查询、需要实时进度反馈的前端应用

---

## 调用示例

### cURL - 同步接口

```bash
# 基础查询
curl "http://localhost:8000/search?user_query=深度学习目标检测综述，最近三年"

# 复杂查询
curl "http://localhost:8000/search?user_query=强化学习与机器人控制，ICLR或NeurIPS，2023到2025，按引用数排序"

# URL 编码查询
curl -G "http://localhost:8000/search" \
  --data-urlencode "user_query=Transformer架构的最新研究，要求有PDF"
```

### cURL - 异步接口

```bash
# 1. 创建任务
task_response=$(curl -X POST "http://localhost:8000/tasks/search" \
  -H "Content-Type: application/json" \
  -d '{"user_query": "深度学习目标检测综述，最近三年"}')

# 提取 task_id
task_id=$(echo $task_response | jq -r '.task_id')
echo "任务 ID: $task_id"

# 2. 轮询任务状态（每 2 秒一次）
while true; do
  status=$(curl -s "http://localhost:8000/tasks/$task_id")
  task_status=$(echo $status | jq -r '.status')
  progress=$(echo $status | jq -r '.progress.overall_percent')

  echo "状态: $task_status, 进度: $progress%"

  if [ "$task_status" = "completed" ] || [ "$task_status" = "failed" ]; then
    echo $status | jq '.'
    break
  fi

  sleep 2
done
```

### Python - 同步接口

```python
import httpx
import asyncio

async def search_papers_async(query: str):
    """真正的异步实现"""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "http://localhost:8000/search",
            params={"user_query": query},
            timeout=60.0,
        )
        return response.json()

def search_papers(query: str):
    """同步封装，外部直接调用这个"""
    return asyncio.run(search_papers_async(query))

if __name__ == "__main__":
    result = search_papers("深度学习目标检测综述，CVPR会议，最近三年")

    print(f"找到 {len(result['results'])} 篇论文")
    for paper in result["results"]:
        print(f"标题: {paper['title']}")
        print(f"作者: {', '.join(paper['authors'][:3])}")
        print(f"年份: {paper['year']}")
        print(f"引用: {paper['citations']}")
        print(f"链接: {paper['url']}\n")

```

### Python - 异步接口（带进度跟踪）

```python
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

```

### Python - 批量并发查询（异步接口）

```python
import asyncio
import httpx

async def create_task(client: httpx.AsyncClient, query: str) -> str:
    """创建搜索任务"""
    response = await client.post(
        "http://localhost:8000/tasks/search",
        json={"user_query": query}
    )
    return response.json()["task_id"]

async def wait_for_task(client: httpx.AsyncClient, task_id: str) -> dict:
    """等待任务完成"""
    while True:
        response = await client.get(f"http://localhost:8000/tasks/{task_id}")
        data = response.json()

        if data["status"] in ["completed", "failed"]:
            return data

        await asyncio.sleep(2)

async def batch_search_async(queries: list[str]):
    """批量并发查询"""
    async with httpx.AsyncClient(timeout=120.0) as client:
        # 1. 并发创建所有任务
        print("创建任务...")
        task_ids = await asyncio.gather(*[
            create_task(client, query) for query in queries
        ])

        print(f"已创建 {len(task_ids)} 个任务\n")

        # 2. 并发等待所有任务完成
        print("等待任务完成...")
        results = await asyncio.gather(*[
            wait_for_task(client, task_id) for task_id in task_ids
        ])

        return results

# 使用示例
queries = [
    "深度学习目标检测综述，CVPR，2020-2023",
    "强化学习机器人控制，ICLR，2024",
    "Transformer架构最新研究，NeurIPS，2023-2024",
]

results = await batch_search_async(queries)

for i, result in enumerate(results, 1):
    if result["status"] == "completed":
        count = len(result["results"]["results"])
        print(f"✅ 查询 {i}: 找到 {count} 篇论文")
    else:
        print(f"❌ 查询 {i}: 失败")
```

### JavaScript - 同步接口

```javascript
async function searchPapers(query) {
  const url = new URL('http://localhost:8000/search');
  url.searchParams.append('user_query', query);

  const response = await fetch(url);
  const data = await response.json();
  return data;
}

// 使用示例
const result = await searchPapers('图神经网络在推荐系统中的应用');

console.log(`找到 ${result.results.length} 篇论文`);
result.results.forEach(paper => {
  console.log(`标题: ${paper.title}`);
  console.log(`年份: ${paper.year} | 引用: ${paper.citations}\n`);
});
```

### JavaScript - 异步接口（React 示例）

```javascript
import { useState, useEffect } from 'react';

function PaperSearchWithProgress({ query }) {
  const [taskId, setTaskId] = useState(null);
  const [status, setStatus] = useState('idle');
  const [progress, setProgress] = useState(0);
  const [stageDescription, setStageDescription] = useState('');
  const [results, setResults] = useState(null);
  const [error, setError] = useState(null);

  // 创建搜索任务
  const startSearch = async () => {
    try {
      setStatus('creating');

      const response = await fetch('http://localhost:8000/tasks/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_query: query })
      });

      const data = await response.json();
      setTaskId(data.task_id);
      setStatus('polling');

    } catch (err) {
      setError(err.message);
      setStatus('error');
    }
  };

  // 轮询任务状态
  useEffect(() => {
    if (!taskId || status !== 'polling') return;

    const pollInterval = setInterval(async () => {
      try {
        const response = await fetch(
          `http://localhost:8000/tasks/${taskId}`
        );
        const data = await response.json();

        setProgress(data.progress.overall_percent);
        setStageDescription(data.progress.stage_description || '');

        if (data.status === 'completed') {
          setResults(data.results);
          setStatus('completed');
          clearInterval(pollInterval);
        } else if (data.status === 'failed') {
          setError(data.error);
          setStatus('error');
          clearInterval(pollInterval);
        }

      } catch (err) {
        setError(err.message);
        setStatus('error');
        clearInterval(pollInterval);
      }
    }, 2000); // 每 2 秒轮询一次

    return () => clearInterval(pollInterval);
  }, [taskId, status]);

  return (
    <div>
      {status === 'idle' && (
        <button onClick={startSearch}>开始搜索</button>
      )}

      {status === 'polling' && (
        <div>
          <p>{stageDescription} - {progress}%</p>
          <progress value={progress} max={100} />
        </div>
      )}

      {status === 'completed' && (
        <div>
          <h3>找到 {results.results.length} 篇论文</h3>
          {results.results.map((paper, i) => (
            <div key={i}>
              <h4>{paper.title}</h4>
              <p>年份: {paper.year} | 引用: {paper.citations}</p>
            </div>
          ))}
        </div>
      )}

      {status === 'error' && (
        <p style={{color: 'red'}}>错误: {error}</p>
      )}
    </div>
  );
}
```

---

## 查询语法

系统支持自然语言查询，LLM 会自动解析以下信息：

### 支持的查询要素

| 要素 | 示例 | 说明 |
|------|------|------|
| **主题关键词** | "深度学习"、"目标检测" | 自动提取并组合 |
| **时间范围** | "2020到2023"、"最近三年" | 自动转换为日期 |
| **会议/期刊** | "CVPR"、"NeurIPS或ICLR" | 支持多个场馆 |
| **文献类型** | "综述"、"Review" | 自动识别类型 |
| **PDF要求** | "要求有PDF"、"必须开源" | 过滤条件 |
| **排序方式** | "按引用数排序"、"按时间排序" | 自动识别 |
| **引用数限制** | "引用数大于50" | 过滤低引用论文 |

### 查询示例

```
✅ "深度学习目标检测综述，CVPR会议，2020到2023年"
✅ "强化学习与机器人控制，ICLR或NeurIPS，按引用数排序"
✅ "Transformer架构的最新研究，要求有PDF，最近两年"
✅ "多模态学习在医学图像中的应用，综述类文章"
✅ "图神经网络推荐系统，引用数大于100，2023-2024"
✅ "材料科学中石墨烯的储能应用综述，最近五年"
```

---

## 返回结果

### 响应结构

```json
{
  "query": "用户输入的查询",
  "normalized_intent": { },
  "api_params": { },
  "counts": { },
  "results": [ ]
}
```

### 完整响应示例

```json
{
  "task_id": "59eecf97-09f5-4840-9727-196d7a2deaa6",
  "status": "completed",
  "progress": {
    "stage": "completed",
    "stage_description": "进入排序阶段",
    "sources": {
      "s2": {
        "status": "completed",
        "fetched": 1,
        "total_estimated": null,
        "errors": null
      },
      "openalex": {
        "status": "completed",
        "fetched": 1,
        "total_estimated": null,
        "errors": null
      },
      "arxiv": {
        "status": "completed",
        "fetched": 50,
        "total_estimated": null,
        "errors": null
      }
    },
    "overall_percent": 100
  },
  "created_at": "2025-11-16T07:05:42.155394",
  "updated_at": "2025-11-16T07:05:46.292991",
  "results": {
    "query": "agentic image restoration相关的论文，按相关性排序",
    "normalized_intent": {
      "any_groups": [
        [
          "agentic image restoration"
        ]
      ],
      "enabled_sources": [
        "s2",
        "openalex",
        "arxiv"
      ],
      "venues": [],
      "author": null,
      "date_start": null,
      "date_end": null,
      "must_have_pdf": false,
      "publication_types": [],
      "open_access": null,
      "min_influential_citations": null,
      "min_citations": null,
      "max_results": 10,
      "sort_by": "relevance",
      "language": null
    },
    "api_params": {
      "endpoint": "graph/v1/paper/search/bulk",
      "query_combinations": 1,
      "queries": [
        "[s2] \"agentic image restoration\"",
        "[openalex] \"agentic image restoration\"",
        "[arxiv] \"agentic image restoration\""
      ]
    },
    "counts": {
      "query_combinations": 1,
      "total_raw_fetched": 52,
      "total_raw_unique": 52,
      "final_unique_count": 51,
      "after_rank_cut": 10
    },
    "results": [
      {
        "title": "MF-LPR$^2$: Multi-Frame License Plate Image Restoration and Recognition using Optical Flow",
        "authors": [
          "Kihyun Na",
          "Junseok Oh",
          "Youngkwan Cho",
          "Bumjin Kim",
          "Sungmin Cho",
          "Jinyoung Choi",
          "Injung Kim"
        ],
        "first_author_hindex": null,
        "abstract": "License plate recognition (LPR) is important for traffic law enforcement, crime investigation, and surveillance. However, license plate areas in dash cam images often suffer from low resolution, motion blur, and glare, which make accurate recognition challenging. Existing generative models that rely on pretrained priors cannot reliably restore such poor-quality images, frequently introducing severe artifacts and distortions. To address this issue, we propose a novel multi-frame license plate restoration and recognition framework, MF-LPR$^2$, which addresses ambiguities in poor-quality images by aligning and aggregating neighboring frames instead of relying on pretrained knowledge. To achieve accurate frame alignment, we employ a state-of-the-art optical flow estimator in conjunction with carefully designed algorithms that detect and correct erroneous optical flow estimations by leveraging the spatio-temporal consistency inherent in license plate image sequences. Our approach enhances both image quality and recognition accuracy while preserving the evidential content of the input images. In addition, we constructed a novel Realistic LPR (RLPR) dataset to evaluate MF-LPR$^2$. The RLPR dataset contains 200 pairs of low-quality license plate image sequences and high-quality pseudo ground-truth images, reflecting the complexities of real-world scenarios. In experiments, MF-LPR$^2$ outperformed eight recent restoration models in terms of PSNR, SSIM, and LPIPS by significant margins. In recognition, MF-LPR$^2$ achieved an accuracy of 86.44%, outperforming both the best single-frame LPR (14.04%) and the multi-frame LPR (82.55%) among the eleven baseline models. The results of ablation studies confirm that our filtering and refinement algorithms significantly contribute to these improvements.",
        "year": 2025,
        "doi": "10.1016/j.cviu.2025.104361",
        "journal": "arXiv",
        "url": "http://arxiv.org/abs/2508.14797v1",
        "pdf_url": "http://arxiv.org/pdf/2508.14797v1.pdf",
        "citations": null,
        "influential_citations": null,
        "open_access": true,
        "publication_types": [
          "preprint"
        ],
        "publication_date": "2025-08-19",
        "fields_of_study": []
      }
    ]
  },
  "completed_at": "2025-11-16T07:05:46.292991"
}
```

### 关键字段说明

#### 1. `normalized_intent` - LLM 解析的查询意图

| 字段 | 说明 | 示例 |
|------|------|------|
| `any_groups` | 关键词组合（AND-of-OR） | `[["deep learning"], ["object detection"]]` |
| `enabled_sources` | 使用的数据源 | `["s2", "openalex", "arxiv"]` |
| `venues` | 目标会议/期刊 | `["CVPR", "ICCV"]` |
| `date_start` / `date_end` | 时间范围 | `"2022-01-01"` ~ `"2025-11-15"` |
| `publication_types` | 文献类型 | `["Review", "Conference"]` |
| `sort_by` | 排序方式 | `"publicationDate"` / `"citationCount"` / `"relevance"` |
| `max_results` | 最大返回数 | `10` |

#### 2. `counts` - 检索统计

| 字段 | 说明 |
|------|------|
| `query_combinations` | 生成的查询组合数 |
| `total_raw_fetched` | 从所有数据源抓取的原始论文数 |
| `total_raw_unique` | 初步去重后的论文数 |
| `final_unique_count` | 过滤后的唯一论文数 |
| `after_rank_cut` | 最终返回的论文数 |

#### 3. `results` - 论文列表

| 字段 | 类型 | 说明 |
|------|------|------|
| `title` | string | 论文标题 |
| `authors` | string[] | 作者列表 |
| `first_author_hindex` | int | 第一作者 H-index (可能为 null) |
| `abstract` | string | 摘要 |
| `year` | int | 发表年份 |
| `journal` | string | 期刊/会议名称 |
| `url` | string | 论文主页链接 |
| `pdf_url` | string | PDF 直接下载链接 (可能为 null) |
| `citations` | int | 引用数 |
| `influential_citations` | int | 影响力引用数 |
| `open_access` | boolean | 是否有开放 PDF |
| `publication_date` | string | 发表日期 (YYYY-MM-DD) |
| `fields_of_study` | string[] | 研究领域 |

---

## 实际效果演示

### 示例 1: 计算机视觉综述

**查询**:
```
深度学习目标检测综述，CVPR会议，最近三年
```

**效果**:
- 自动识别关键词: "deep learning", "object detection", "survey"
- 限定会议: CVPR
- 时间范围: 2022-01-01 ~ 2025-11-15
- 文献类型: Review
- 数据源: S2, OpenAlex, arXiv
- 返回: 10 篇相关综述论文

**典型返回**:
```json
{
  "results": [
    {
      "title": "Deep Learning for Object Detection: A Comprehensive Survey",
      "year": 2024,
      "journal": "CVPR",
      "citations": 245,
      "open_access": true
    }
  ]
}
```

---

### 示例 2: 强化学习顶会论文

**查询**:
```
强化学习与机器人控制，ICLR或NeurIPS，2024年，按引用数排序
```

**效果**:
- 关键词: "reinforcement learning", "robot control"
- 会议: ICLR, NeurIPS
- 时间: 2024-01-01 ~ 2024-12-31
- 排序: 引用数降序
- 返回: 10 篇高引用论文

**典型返回**:
```json
{
  "results": [
    {
      "title": "Hierarchical Reinforcement Learning for Robot Control",
      "year": 2024,
      "journal": "ICLR",
      "citations": 89,
      "influential_citations": 12
    }
  ]
}
```

---

### 示例 3: 生物医学开放论文

**查询**:
```
蛋白质结构预测的最新进展，要求有PDF，最近两年
```

**效果**:
- 关键词: "protein structure prediction"
- 时间: 2023-01-01 ~ 2025-11-15
- 过滤: 必须有开放 PDF
- 数据源: S2, PubMed, Europe PMC
- 返回: 10 篇开放获取论文

**典型返回**:
```json
{
  "results": [
    {
      "title": "AlphaFold 3: Advances in Protein Structure Prediction",
      "year": 2024,
      "journal": "Nature",
      "citations": 1250,
      "open_access": true,
      "url": "https://..."
    }
  ]
}
```

---

### 示例 4: 跨学科材料研究

**查询**:
```
材料科学中石墨烯的储能应用综述，最近五年
```

**效果**:
- 关键词: "graphene", "energy storage", "materials science", "survey"
- 时间: 2020-01-01 ~ 2025-11-15
- 文献类型: Review
- 数据源: S2, OpenAlex
- 返回: 10 篇综述论文

**典型返回**:
```json
{
  "results": [
    {
      "title": "Graphene-based Materials for Energy Storage: A Review",
      "year": 2023,
      "journal": "Advanced Materials",
      "citations": 342,
      "fields_of_study": ["Materials Science", "Chemistry"]
    }
  ]
}
```

---

## 数据源说明

系统自动从多个学术数据库检索，并智能去重：

| 数据源 | 覆盖领域 | 特点 |
|--------|----------|------|
| **Semantic Scholar** | 全学科 | 必选，最全面 |
| **OpenAlex** | 全学科 | 开放数据，更新快 |
| **arXiv** | 物理/计算机/数学 | 预印本，最新研究 |
| **PubMed** | 生物医学 | 权威医学文献 |
| **Europe PMC** | 生命科学 | 欧洲生命科学数据 |
| **Crossref** | 全学科 | DOI 元数据 |

LLM 会根据查询主题自动选择合适的数据源组合。

---

## 错误处理

### 错误响应格式

```json
{
  "query": "用户查询",
  "error": "错误信息",
  "results": [],
  "counts": {
    "query_combinations": 0,
    "total_raw_fetched": 0,
    "total_raw_unique": 0,
    "final_unique_count": 0,
    "after_rank_cut": 0
  }
}
```

### 常见错误

**1. 查询过宽**
```json
{
  "error": "S2 API error: 400 too many hits"
}
```
**解决**: 添加更具体的关键词、限制时间范围或指定会议

**2. LLM 解析失败**
```json
{
  "error": "LLM parsing failed: Invalid JSON response"
}
```
**解决**: 检查 API Key 或简化查询语句

**3. 无结果**
```json
{
  "results": [],
  "counts": { "final_unique_count": 0 }
}
```
**解决**: 放宽过滤条件或扩大时间范围

---

## 批量调用示例

### Python 批量查询

```python
import asyncio
import httpx

async def batch_search(queries: list[str]):
    """批量查询论文"""
    async with httpx.AsyncClient(timeout=60.0) as client:
        tasks = [
            client.get("http://localhost:8000/search", params={"user_query": q})
            for q in queries
        ]
        responses = await asyncio.gather(*tasks)
        return [r.json() for r in responses]

# 使用示例
queries = [
    "深度学习目标检测综述，CVPR，2020-2023",
    "强化学习机器人控制，ICLR，2024",
    "Transformer架构最新研究，NeurIPS，2023-2024",
]

results = await batch_search(queries)

for i, result in enumerate(results, 1):
    print(f"\n查询 {i}: {result['query']}")
    print(f"找到 {len(result['results'])} 篇论文")
    if result['results']:
        top_paper = result['results'][0]
        print(f"Top 1: {top_paper['title']}")
```

### 带延迟的批量查询（避免速率限制）

```python
async def batch_search_with_delay(queries: list[str], delay: float = 2.0):
    """批量查询，添加延迟避免速率限制"""
    results = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        for query in queries:
            response = await client.get(
                "http://localhost:8000/search",
                params={"user_query": query}
            )
            results.append(response.json())
            await asyncio.sleep(delay)  # 每次查询间隔 2 秒
    return results
```

---

## 常见问题

### Q1: 同步接口 vs 异步接口，如何选择？

**使用同步接口** (`GET /search`) 的情况：
- ✅ 简单快速查询（预计 <5 秒）
- ✅ 命令行工具、脚本
- ✅ 不需要显示进度的场景

**使用异步接口** (`POST /tasks/search`) 的情况：
- ✅ 复杂查询（多来源、多关键词）
- ✅ 需要显示实时进度的前端界面
- ✅ 避免 HTTP 超时问题
- ✅ 需要并发处理多个查询

### Q2: 如何获取更多结果？

**方法 1: 在查询中指定数量**（推荐）
```
"深度学习目标检测，返回50篇论文"
"强化学习综述，需要100篇"
"Transformer架构研究，最多200篇"
```

LLM 会自动识别数量要求并设置 `max_results`。

**方法 2: 配置服务器限制**

默认最大限制为 **500 篇论文**，可通过环境变量调整：
```bash
# .env 文件
MAX_RESULTS_LIMIT=500  # 最大限制（1-1000）
```

**注意事项**:
- 默认返回 10 篇（未指定时）
- 推荐使用异步接口 (`/tasks/search`) 请求 100+ 篇论文
- 超过配置限制会返回 HTTP 400 错误
- 更大的结果集需要更长的响应时间（200篇约 4-10s，500篇约 10-25s）

### Q3: 异步接口的轮询频率建议？

**推荐轮询间隔**:
- 标准查询: 每 **1-2 秒** 轮询一次
- 复杂查询: 每 **2-3 秒** 轮询一次
- 避免: <1 秒的高频轮询（浪费资源）

**最佳实践**:
```javascript
// ✅ 好的做法：指数退避
let interval = 1000;
const poll = async () => {
  const data = await fetchTaskStatus(taskId);
  if (data.status === 'completed') return data;

  interval = Math.min(interval * 1.2, 5000); // 最多 5 秒
  await sleep(interval);
  return poll();
};

// ❌ 不好的做法：固定高频轮询
setInterval(() => fetchTaskStatus(taskId), 500); // 太频繁！
```

### Q4: 任务会自动清理吗？

是的。任务完成或失败后，会在 **30 分钟后自动清理**。

- ✅ 节省服务器内存
- ⚠️ 获取结果后应立即保存
- 📝 任务 ID 无法恢复已清理的任务

### Q5: 如果任务失败了怎么办？

检查返回的 `error` 字段：
```json
{
  "status": "failed",
  "error": "Failed to parse query: invalid syntax"
}
```

**常见失败原因**:
- LLM API 配置错误（检查 API Key）
- 查询过于复杂或模糊
- 网络连接问题

**解决方案**:
1. 简化查询语句
2. 检查环境变量配置
3. 查看服务器日志

### Q6: 支持哪些会议/期刊？

支持所有主流会议/期刊，包括但不限于：
- **AI/ML**: NeurIPS, ICLR, ICML, AAAI, IJCAI
- **CV**: CVPR, ICCV, ECCV
- **NLP**: ACL, EMNLP, NAACL
- **Data**: KDD, WWW, SIGIR

### Q7: 如何获取论文全文？

API 只返回元数据。获取全文：
- 开放获取论文 (`open_access: true`): 访问 `url` 字段
- 非开放论文: 通过 DOI 或机构订阅获取
- arXiv 论文: 将 URL 中的 `/abs/` 改为 `/pdf/`

### Q8: 中文查询效果如何？

完全支持中文查询，LLM 会自动：
- 提取中文关键词
- 翻译为英文进行检索
- 识别中文表述的时间、会议等

### Q9: 返回结果是实时的吗？

返回结果基于各数据源的最新索引，通常：
- arXiv: 每日更新
- Semantic Scholar: 每周更新
- PubMed: 每日更新
- OpenAlex: 持续更新

---

## 性能优化建议

### 1. 批量查询时使用异步接口

```python
# ❌ 不好：串行同步查询（慢）
for query in queries:
    result = await search_sync(query)  # 每个等 10 秒

# ✅ 好：并发异步查询（快）
task_ids = await asyncio.gather(*[
    create_task(query) for query in queries
])
results = await asyncio.gather(*[
    wait_for_task(tid) for tid in task_ids
])
```

### 2. 合理设置超时

```python
# 同步接口
timeout = 60.0  # 复杂查询可能需要 30-60 秒

# 异步接口
timeout = 120.0  # 轮询超时可以更长
```

### 3. 缓存结果

对于相同查询，建议在客户端缓存结果：

```python
import hashlib
from functools import lru_cache

@lru_cache(maxsize=100)
def get_cached_results(query_hash: str):
    # 缓存最近 100 个查询结果
    pass
```

### 4. 错误重试

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10)
)
async def search_with_retry(query: str):
    return await search_papers_async(query)
```

---

## 进度跟踪系统详解

### 进度计算方式

异步任务的进度基于**固定阶段平均分配**原则，确保进度增长的一致性和可预测性。

#### 阶段分配（4个主要阶段）

| 阶段 | 进度范围 | 说明 |
|------|---------|------|
| **创建任务** | 0% | 任务初始化完成 |
| **解析意图** | 0% → 25% | LLM 解析查询，提取关键词和过滤条件 |
| **搜索文献** | 25% → 75% | 多来源并发检索（动态计算） |
| **排序结果** | 75% → 100% | 去重、过滤、排序 |

#### 搜索阶段动态计算

在搜索阶段（占总进度的 50%），进度根据完成的来源数动态计算：

```
进度 = 25% + 50% × (已完成来源数 / 总来源数)
```

**示例**（3个来源：s2, openalex, arxiv）：

```
阶段              进度计算                          进度值
─────────────────────────────────────────────────────────
任务创建          固定                              0%
解析完成          固定                              25%
S2 完成           25% + 50% × (1/3)                41%
OpenAlex 完成     25% + 50% × (2/3)                58%
arXiv 完成        25% + 50% × (3/3)                75%
排序完成          固定                              100%
```

### 中文描述系统

每个阶段和来源状态都有对应的**友好中文描述**，便于前端直接展示：

#### 主阶段描述

| 阶段 | 描述 |
|------|------|
| `created` | "任务已创建" |
| `parsing` | "正在解析查询意图" |
| `searching` | "正在搜索 {来源名称}" (动态) |
| `ranking` | "进入排序阶段" |
| `completed` | "搜索完成" |
| `failed` | "搜索失败" |

#### 来源名称映射

| 英文标识 | 中文名称 |
|---------|---------|
| `s2` | Semantic Scholar |
| `openalex` | OpenAlex |
| `arxiv` | arXiv |
| `crossref` | Crossref |
| `pubmed` | PubMed |
| `eupmc` | Europe PMC |

#### 搜索阶段描述逻辑

在搜索阶段，`stage_description` 会**动态显示当前正在搜索的来源**：

```json
// S2 正在搜索时
{"stage_description": "正在搜索 Semantic Scholar"}

// OpenAlex 正在搜索时
{"stage_description": "正在搜索 OpenAlex"}

// 所有来源完成时
{"stage_description": "文献检索完成"}
```

### 前端集成示例

#### 显示进度条 + 中文描述

```javascript
// React 示例
<div className="search-progress">
  <h4>{progress.stage_description}</h4>
  <ProgressBar value={progress.overall_percent} max={100} />
  <p>{progress.overall_percent}% 完成</p>
</div>
```

**效果**：
```
正在搜索 OpenAlex
[████████████░░░░░░░░] 58% 完成
```

#### 显示详细来源进度

```javascript
// 显示每个来源的状态
{Object.entries(progress.sources).map(([source, info]) => (
  <div key={source} className="source-item">
    <span className="source-name">{SOURCE_NAMES[source]}</span>
    <span className={`status-${info.status}`}>
      {STATUS_LABELS[info.status]}
    </span>
    {info.fetched && <span>({info.fetched} 篇)</span>}
  </div>
))}
```

**效果**：
```
✅ Semantic Scholar  已完成 (120 篇)
⏳ OpenAlex         检索中 (45 篇)
⏸️  arXiv            等待中
```

### 轮询最佳实践

#### 推荐轮询策略

```javascript
// ✅ 好的做法：自适应轮询间隔
const pollWithBackoff = async (taskId) => {
  let interval = 1000;  // 初始 1 秒
  let previousPercent = 0;

  while (true) {
    const data = await fetchTaskStatus(taskId);

    // 检查是否完成
    if (data.status === 'completed' || data.status === 'failed') {
      return data;
    }

    // 如果进度在变化，保持快速轮询
    if (data.progress.overall_percent > previousPercent) {
      interval = 1000;  // 重置为 1 秒
    } else {
      // 进度停滞，逐渐增加间隔
      interval = Math.min(interval * 1.2, 5000);  // 最多 5 秒
    }

    previousPercent = data.progress.overall_percent;
    await sleep(interval);
  }
};
```

#### 避免的做法

```javascript
// ❌ 不好的做法：固定高频轮询
setInterval(() => fetchTaskStatus(taskId), 500);  // 太频繁！

// ❌ 不好的做法：忽略进度信息
while (true) {
  await fetchTaskStatus(taskId);
  await sleep(10000);  // 间隔太长，体验差
}
```

### 进度异常处理

#### 进度卡住

如果进度长时间（>30秒）停留在某个百分比：

```javascript
let stuckTimer = 0;
const MAX_STUCK_TIME = 30000;  // 30 秒

if (currentPercent === previousPercent) {
  stuckTimer += pollInterval;
  if (stuckTimer > MAX_STUCK_TIME) {
    console.warn('任务可能卡住，建议刷新或重新创建');
    // 可选：自动重试或提示用户
  }
} else {
  stuckTimer = 0;  // 重置计时器
}
```

#### 来源失败处理

单个来源失败不影响整体任务：

```javascript
// 检查是否有来源失败
const failedSources = Object.entries(progress.sources)
  .filter(([_, info]) => info.status === 'failed')
  .map(([source, info]) => ({
    source,
    error: info.errors?.[0] || '未知错误'
  }));

if (failedSources.length > 0) {
  console.warn('部分来源失败:', failedSources);
  // 显示警告但继续等待其他来源
}
```

---

## PDF 下载链接支持

### 功能说明

从 v1.3 开始，所有搜索结果的 `PaperMetadata` 对象都包含 `pdf_url` 字段，提供论文的直接 PDF 下载链接。

### 字段定义

```json
{
  "url": "https://arxiv.org/abs/2404.12345",        // 论文主页
  "pdf_url": "https://arxiv.org/pdf/2404.12345.pdf", // PDF 直接下载（可能为 null）
  "open_access": true                                 // 是否开放获取
}
```

**关键区别**:
- `url`: 论文的主页/详情页链接（通常是 HTML 页面）
- `pdf_url`: PDF 文件的直接下载链接（如果可用）
- `open_access`: 布尔值，表示是否有可用的 PDF

### 各数据源 PDF URL 支持情况

| 数据源 | PDF URL 支持 | 提取方式 | 覆盖率 |
|--------|--------------|----------|--------|
| **Semantic Scholar** | ✅ 完全支持 | 从 `openAccessPdf.url` 提取 | ~60% (开放获取论文) |
| **OpenAlex** | ✅ 完全支持 | 从 `primary_location.pdf_url` 提取 | ~40% |
| **arXiv** | ✅ 完全支持 | URL 模式转换 (`/abs/` → `/pdf/` + `.pdf`) | 100% (所有 arXiv 论文) |
| **Crossref** | ❌ 不支持 | 默认 `null` | 0% |
| **PubMed** | ❌ 不支持 | 默认 `null` | 0% |
| **Europe PMC** | ❌ 不支持 | 默认 `null` | 0% |

### 使用示例

#### Python 批量下载

```python
import requests

results = search_response["results"]

for paper in results:
    if paper["pdf_url"]:
        try:
            response = requests.get(paper["pdf_url"], timeout=30)
            if response.status_code == 200:
                filename = f"{paper['year']}_{paper['title'][:50]}.pdf"
                with open(filename, 'wb') as f:
                    f.write(response.content)
                print(f"✓ Downloaded: {filename}")
            else:
                print(f"✗ Failed (HTTP {response.status_code}): {paper['title']}")
        except Exception as e:
            print(f"✗ Error downloading {paper['title']}: {e}")
    else:
        print(f"⊘ No PDF available: {paper['title']}")
```

#### JavaScript 前端显示

```javascript
function renderPaperCard(paper) {
  const downloadButton = paper.pdf_url
    ? `<a href="${paper.pdf_url}" 
          class="btn-download" 
          target="_blank"
          rel="noopener noreferrer">
          📥 下载 PDF
       </a>`
    : `<span class="no-pdf">无 PDF</span>`;

  return `
    <div class="paper-card">
      <h3>${paper.title}</h3>
      <p class="authors">${paper.authors.join(', ')}</p>
      <div class="actions">
        <a href="${paper.url}" target="_blank">🔗 查看详情</a>
        ${downloadButton}
      </div>
    </div>
  `;
}
```

### 注意事项

#### 1. PDF URL 有效性

- **不进行验证**: 系统信任数据源提供的 URL，不发送 HEAD 请求验证
- **可能失效**: PDF URL 可能因权限、重定向、404 等原因无法访问
- **建议**: 客户端应处理下载失败情况（见上方示例）

#### 2. `pdf_url` 与 `open_access` 的关系

```
pdf_url 存在 ⇒ open_access = true  (通常成立)
open_access = true ⇏ pdf_url 存在  (某些数据源不提供 URL)
```

**示例**:
- Crossref 可能标记 `open_access=true`，但 `pdf_url` 仍为 `null`（因为 Crossref 不提供 PDF 链接）
- arXiv 所有论文都是 `open_access=true` 且 `pdf_url` 存在

#### 3. 后备策略

当 `pdf_url` 为 `null` 时的推荐处理：

```python
def get_pdf_link(paper):
    # 优先使用 pdf_url
    if paper["pdf_url"]:
        return paper["pdf_url"]
    
    # 后备：访问主页链接
    if paper["url"]:
        return paper["url"]
    
    # 最终后备：通过 DOI 访问
    if paper["doi"]:
        return f"https://doi.org/{paper['doi']}"
    
    return None
```

### 完整示例响应

```json
{
  "results": [
    {
      "title": "Attention Is All You Need",
      "authors": ["Vaswani, Ashish", "Shazeer, Noam"],
      "year": 2017,
      "journal": "NeurIPS",
      "url": "https://arxiv.org/abs/1706.03762",
      "pdf_url": "https://arxiv.org/pdf/1706.03762.pdf",
      "open_access": true,
      "citations": 95432
    },
    {
      "title": "Deep Residual Learning for Image Recognition",
      "authors": ["He, Kaiming", "Zhang, Xiangyu"],
      "year": 2016,
      "journal": "CVPR",
      "url": "https://openaccess.thecvf.com/content_cvpr_2016/...",
      "pdf_url": "https://arxiv.org/pdf/1512.03385.pdf",
      "open_access": true,
      "citations": 125789
    },
    {
      "title": "Some Paywalled Conference Paper",
      "authors": ["Smith, John", "Doe, Jane"],
      "year": 2024,
      "journal": "ACM CHI",
      "url": "https://dl.acm.org/doi/10.1145/...",
      "pdf_url": null,
      "open_access": false,
      "citations": 12
    }
  ]
}
```

---

## API 版本历史

### v1.4 (2025-11-16)
- ✨ **增加结果数量上限**：支持一次返回最多 500 篇论文（可配置）
- ✨ **新增配置**: `MAX_RESULTS_LIMIT` 环境变量（默认 500，范围 1-1000）
- ✨ **动态分页优化**：移除硬编码的 100 篇限制，基于 `max_results` 动态计算
- ✨ **性能日志**：自动记录 >100 篇结果的请求执行时间
- 🔧 **LLM 提示更新**：推荐 10/20/50 篇，支持最大 200（同步）或 500（异步）
- ⚡ **响应时间优化**：更高效的分页策略，减少不必要的 API 调用
- ✅ 100% 向后兼容 v1.3（默认 `max_results=10` 不变）

### v1.3 (2025-11-16)
- ✨ **新增 PDF URL 字段** (`pdf_url` 字段)
- ✨ **Semantic Scholar**: 从 `openAccessPdf.url` 提取 PDF 链接 (~60% 覆盖率)
- ✨ **OpenAlex**: 从 `primary_location.pdf_url` 提取 PDF 链接 (~40% 覆盖率)
- ✨ **arXiv**: 自动生成 PDF URL (100% 覆盖率)
- ✅ 100% 向后兼容 v1.2（新字段为可选）

### v1.2 (2025-11-16)
- ✨ **新增中文进度描述** (`stage_description` 字段)
- ✨ **优化进度计算**：改为基于固定阶段的平均分配（0% → 25% → 75% → 100%）
- ✨ **动态来源描述**：实时显示"正在搜索 {来源名称}"
- 📊 **更直观的进度**：每个阶段权重清晰，用户体验更友好
- ✅ 100% 向后兼容 v1.1

### v1.1 (2025-11-16)
- ✨ 新增异步任务接口 (`POST /tasks/search`, `GET /tasks/{task_id}`)
- ✨ 实时进度跟踪（阶段 + 每个来源的详细进度）
- ✨ 自动 TTL 清理（30 分钟）
- ✅ 100% 向后兼容同步接口

### v1.0 (2025-11-15)
- 🎉 初始版本
- ✅ 同步搜索接口 (`GET /search`)
- ✅ 多来源检索（S2, OpenAlex, arXiv, PubMed, Europe PMC, Crossref）
- ✅ LLM 意图解析
- ✅ 智能去重与排序

---

**文档更新时间**: 2025-11-16
**API 版本**: v1.4
**维护者**: PaperFinder Team
