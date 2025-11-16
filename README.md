# PaperFinder Agent — 说明文档（中文）

一个轻量的多来源论文检索服务（基于 FastAPI）：把自然语言查询解析成结构化意图，向多家学术接口发起检索，统一清洗与去重，排序后返回简洁 JSON 结果。

## ✨ 功能概览

* **LLM 意图解析** → 将用户文本转为 `SearchIntent`
* **多来源检索**（始终包含 **S2**，可选 OpenAlex / Crossref / arXiv / PubMed / EuropePMC）
* **优先服务端过滤**（日期 / 期刊会议 / 文献类型 / OA 等）
* **统一客户端兜底过滤**（作者包含、场馆同义词规整、日期到日、最小影响力引用、类型交集）
* **跨来源去重**（键顺序：DOI → URL → 规范化标题+年份）
* **排序**（相关性 / 引用数 / 发表日期）
* **丰富统计**：逐来源抓取/去重/过滤计数、选用来源清单等

---

## 🗂 目录结构

```
paper_survey/
├─ main.py                # FastAPI 接口 (/search)
├─ search_multi.py        # 多来源聚合 + 去重 + 过滤
├─ s2_client.py           # 单来源适配器（S2/OpenAlex/Crossref/等）
├─ llm_parser.py          # 自然语言 → SearchIntent
├─ ranking.py             # 排序与截断
├─ schemas.py             # Pydantic 模型：SearchIntent, PaperMetadata
├─ author_hindex.py       # （可选）首作者 h-index 填充（OpenAlex）
├─ test_search.py         # 批量测试：产出 JSON/Markdown 报告
├─ logging_setup.py       # 日志配置
├─ config.py              # 环境变量加载
└─ requirements.txt
```

---

## ⚙️ 环境与依赖

* Python **3.10+**

安装依赖：

```bash
pip install -r requirements.txt
```

最小依赖示例（`requirements.txt`）：

```
fastapi
uvicorn[standard]
httpx
pydantic
python-dotenv
```

如使用可选模块：

```
openai            # 若 llm_parser 使用 OpenAI
scholarly         # 如需 Google Scholar（不推荐生产）
```

---

## 🔐 环境变量

在项目根目录创建 `.env`（或直接设置环境变量）：

```
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini            # 与 llm_parser.py 保持一致
S2_API_KEY=...                      # Semantic Scholar 可选但推荐
S2_BASE=https://api.semanticscholar.org/graph/v1
S2_RPS=2                            # S2 限速（req/s）
LOG_LEVEL=INFO
```

> 无 `S2_API_KEY` 亦可运行，但分页与速率限制更保守。

---

## ▶️ 启动

```bash
uvicorn main:app --reload --port 8000
```

打开：`http://localhost:8000/docs`

---

## 🔎 接口说明

### 同步接口（Synchronous API）

#### `GET /search?user_query=...`

**输入**：自然语言字符串。LLM 会产出类似结构：

```json
{
  "any_groups": [["reinforcement learning"], ["robot control"]],
  "enabled_sources": ["s2","openalex","arxiv"],   // LLM 选择；后端强制包含 s2
  "venues": ["ICLR","NeurIPS"],
  "author": null,
  "date_start": "2024-01-01",
  "date_end": "2024-12-31",
  "must_have_pdf": false,
  "publication_types": [],
  "min_influential_citations": null,
  "max_results": 10,
  "sort_by": "publicationDate"                   // 或 "citationCount" | "relevance"
}
```

**返回（节选）：**

```json
{
  "query": "强化学习与机器人控制，2024年，按时间排序",
  "normalized_intent": { ... },
  "api_params": {
    "endpoint": "graph/v1/paper/search/bulk",
    "query_combinations": 2,
    "queries": ["[s2] \"reinforcement learning\" \"robot control\"", "..."]
  },
  "counts": {
    "query_combinations": 2,
    "total_raw_fetched": 310,
    "total_raw_unique": 260,
    "final_unique_count": 120,
    "after_rank_cut": 10
  },
  "stats": {
    "selected_sources": ["s2","openalex","arxiv"],
    "per_page": 100,
    "total_pages": 5,
    "total_after_filter": 140,
    "per_source_after_filter": { "s2": 90, "openalex": 35, "arxiv": 15 },
    "total_after_filter_s2": 90,
    "total_after_filter_openalex": 35,
    "total_after_filter_crossref": 0,
    "total_after_filter_arxiv": 15,
    "total_after_filter_pubmed": 0,
    "total_after_filter_eupmc": 0,
    "individual_stats": [
      {"source":"s2","raw_fetched":200,"raw_unique":160,"after_filter":90,"pages":4},
      {"source":"openalex","raw_fetched":80,"raw_unique":70,"after_filter":35,"pages":1},
      {"source":"arxiv","raw_fetched":30,"raw_unique":30,"after_filter":15,"pages":1}
    ]
  },
  "results": [
    {
      "title": "...",
      "authors": ["Alice", "Bob"],
      "publication_date": "2024-05-07",
      "venue": "ICLR",
      "citations": 12,
      "influential_citations": 1,
      "url": "https://...",
      "has_pdf": true
    }
  ]
}
```

---

### 异步接口（Asynchronous Task API）

异步接口允许创建后台搜索任务并轮询进度，适用于需要实时反馈的前端应用。

#### `POST /tasks/search`

创建异步搜索任务，立即返回 `task_id`，搜索在后台执行。

**请求：**
```bash
curl -X POST "http://localhost:8000/tasks/search" \
  -H "Content-Type: application/json" \
  -d '{"user_query": "深度学习目标检测综述"}'
```

**响应（HTTP 202）：**
```json
{
  "task_id": "550e8400-e29b-41d4-a716-446655440000",
  "status": "created",
  "created_at": "2025-11-15T14:30:00Z"
}
```

#### `GET /tasks/{task_id}`

查询任务状态和结果。建议每 1-2 秒轮询一次。

**请求：**
```bash
curl "http://localhost:8000/tasks/550e8400-e29b-41d4-a716-446655440000"
```

**响应（进行中，HTTP 200）：**
```json
{
  "task_id": "550e8400-...",
  "status": "searching",
  "progress": {
    "stage": "searching",
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
    "overall_percent": 45
  },
  "created_at": "2025-11-15T14:30:00Z",
  "updated_at": "2025-11-15T14:30:05Z"
}
```

**响应（完成，HTTP 200）：**
```json
{
  "task_id": "550e8400-...",
  "status": "completed",
  "progress": {
    "stage": "completed",
    "overall_percent": 100
  },
  "results": {
    "query": "深度学习目标检测综述",
    "normalized_intent": { ... },
    "api_params": { ... },
    "counts": { ... },
    "results": [ ... ]
  },
  "errors": [],
  "created_at": "2025-11-15T14:30:00Z",
  "completed_at": "2025-11-15T14:30:12Z"
}
```

**任务状态说明：**
- `created`: 任务已创建，等待执行
- `parsing`: LLM 正在解析查询意图
- `searching`: 多来源搜索进行中
- `ranking`: 结果排序中
- `completed`: 搜索完成，结果可用
- `failed`: 任务失败（查看 `error` 字段）

**来源状态说明：**
- `pending`: 等待开始
- `in_progress`: 检索中
- `completed`: 该来源完成
- `failed`: 该来源失败（不影响整体任务）

**任务生命周期：**
- 任务完成或失败后，将在 **30 分钟后自动清理**
- 建议客户端获取结果后立即保存
- 任务 ID 采用 UUID4 格式，随机生成

**错误处理：**
```json
{
  "task_id": "550e8400-...",
  "status": "completed",
  "results": { ... },
  "errors": [
    {
      "source": "arxiv",
      "message": "Connection timeout",
      "timestamp": "2025-11-15T14:30:08Z"
    }
  ]
}
```

---

## 🧠 来源选择逻辑

* LLM 输出 `enabled_sources`（1–3 个，**必须包含 `s2`**）
* 支持来源：`s2`, `openalex`, `crossref`, `arxiv`, `pubmed`, `eupmc`
* 后端会强制并保留 `s2`，并按选择项实际检索

---

## 🧹 过滤与去重

* **服务端过滤**：S2 / OpenAlex / Crossref / EuropePMC 支持部分参数
* **客户端兜底**（三/多来源统一标准）：

  * 作者子串匹配
  * 场馆同义词规整（NeurIPS/NIPS/全称等）
  * 日期范围（精确到日）
  * 最小影响力引用数
  * 文献类型交集
* **去重优先级**：DOI → URL → 规范化(标题)+年份（跨来源统一）

---

## 🧮 排序

`ranking.py` 支持：

* `"relevance"`（默认）
* `"citationCount"`
* `"publicationDate"`

---

## 🧪 测试

### 批量功能测试
```bash
python test_search.py
```
输出 `test_results/` 下的 JSON 与 Markdown 报告：包含 LLM 解析、逐来源统计、Top 结果等。

### 异步 API 集成测试
```bash
python test_async_api.py
```
测试任务创建、轮询、并发等场景。

### 向后兼容性测试
```bash
python test_backward_compat.py
```
验证同步 `/search` 端点保持不变。

---

## 🩺 常见问题

* **S2 400 “too many hits”**：查询过宽。请增加短语引号、限制日期/场馆，或加关键词组；代码也会跳过 `"*"` 这类无意义组合。
* **arXiv 时间过滤**：arXiv 不直接支持服务端按发表时间过滤，本项目在客户端做日期兜底。
* **不同来源引用数不一致**：正常现象，索引更新时间与统计口径不同。
* **首作者 h-index 为 null**：OpenAlex 可能无匹配或无统计。代码可按需回落为 `0`。

---

## 📜 许可

MIT（或按你项目需要替换）。
