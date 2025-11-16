# PaperFinder Agent

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**一个轻量级的多来源学术论文检索服务**

基于 FastAPI 构建，集成自然语言查询解析、多数据源聚合检索、智能去重过滤和结果排序，提供简洁高效的 RESTful API。

### 技术栈
- **框架**: FastAPI + Uvicorn
- **LLM 集成**: OpenAI / DeepSeek / 兼容 API
- **学术数据源**: Semantic Scholar, OpenAlex, Crossref, arXiv, PubMed, Europe PMC
- **异步任务**: 内置任务队列 + 进度追踪
- **语言**: Python 3.10+

## ✨ 功能概览

* **LLM 意图解析** → 将用户文本转为 `SearchIntent`
* **多来源检索**（始终包含 **S2**，可选 OpenAlex / Crossref / arXiv / PubMed / EuropePMC）
* **优先服务端过滤**（日期 / 期刊会议 / 文献类型 / OA 等）
* **统一客户端兜底过滤**（作者包含、场馆同义词规整、日期到日、最小影响力引用、类型交集）
* **跨来源去重**（键顺序：DOI → URL → 规范化标题+年份）
* **排序**（相关性 / 引用数 / 发表日期）
* **丰富统计**：逐来源抓取/去重/过滤计数、选用来源清单等
* **同步 + 异步接口**：支持即时响应和后台任务两种模式

---

## 🚀 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cat > .env << EOF
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=https://api.openai.com/v1
S2_API_KEY=your_s2_key
EOF

# 3. 启动服务
uvicorn main:app --reload --port 8000

# 4. 测试接口（新终端）
curl "http://localhost:8000/search?user_query=深度学习目标检测综述"

# 5. 查看 API 文档
open http://localhost:8000/docs
```

---

## 🗂 目录结构

```
easy_agent/
├─ main.py                        # FastAPI 接口 (/search, /tasks/*)
├─ search_multi.py                # 多来源聚合 + 去重 + 过滤（S2/OpenAlex/Crossref/arXiv/PubMed/EuropePMC）
├─ llm_parser.py                  # 自然语言 → SearchIntent
├─ ranking.py                     # 排序与截断
├─ schemas.py                     # Pydantic 模型：SearchIntent, PaperMetadata
├─ task_executor.py               # 异步任务执行引擎
├─ task_store.py                  # 任务状态存储与管理
├─ fill_author_citation_info.py   # （可选）首作者 h-index 填充（OpenAlex）
├─ test_search.py                 # 批量测试：产出 JSON/Markdown 报告
├─ test_fast_api.py               # FastAPI 接口测试
├─ logging_setup.py               # 日志配置
├─ config.py                      # 环境变量加载
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

```bash
# LLM 配置（必需）
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-4o-mini                              # 或 deepseek-chat
OPENAI_BASE_URL=https://api.openai.com/v1             # OpenAI 官方
# OPENAI_BASE_URL=https://api.deepseek.com/v1        # DeepSeek 替代方案

# Semantic Scholar 配置（推荐）
S2_API_KEY=your_s2_key                                # 可选但强烈推荐
S2_RPS=2                                              # 有 key: 2 req/s, 无 key: 0.5 req/s

# 服务配置
HOST=127.0.0.1                                        # 默认本地访问
PORT=8000                                             # 服务端口
LOG_LEVEL=INFO                                        # DEBUG|INFO|WARNING|ERROR
MAX_RESULTS_LIMIT=500                                 # 单次搜索最大结果数（1-1000）
```

> **说明**：
> - 无 `S2_API_KEY` 亦可运行，但速率限制更严格（0.5 req/s）
> - `OPENAI_BASE_URL` 支持任何 OpenAI 兼容 API（OpenAI/DeepSeek/本地模型）
> - `MAX_RESULTS_LIMIT` 超过 100 时建议使用异步接口 `/tasks/search`

---

## ▶️ 启动

### 开发模式（自动重载）
```bash
uvicorn main:app --reload --port 8000
```

### 生产模式（多进程）
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

### Windows PowerShell 快捷启动
```powershell
.\start.ps1
```

### 访问接口文档
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

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

### FastAPI 接口测试
```bash
# 确保服务已启动（另一个终端）
uvicorn main:app --reload

# 运行接口测试
python test_fast_api.py
```
测试同步接口 `/search` 和异步接口 `/tasks/*` 的基本功能。

---

## 🩺 常见问题

* **S2 400 "too many hits"**：查询过宽。请增加短语引号、限制日期/场馆，或加关键词组；代码也会跳过 `"*"` 这类无意义组合。
* **arXiv 时间过滤**：arXiv 不直接支持服务端按发表时间过滤，本项目在客户端做日期兜底。
* **不同来源引用数不一致**：正常现象，索引更新时间与统计口径不同。
* **首作者 h-index 为 null**：OpenAlex 可能无匹配或无统计。代码可按需回落为 `0`。
* **大量结果请求耗时长**：请求 200+ 篇论文可能需要 10-30 秒，建议使用异步接口 `/tasks/search`。

---

## ⚡ 性能说明

### 响应时间参考
| 结果数量 | 预计耗时 | 推荐接口 |
|---------|----------|----------|
| 10 篇   | ~1 秒    | 同步 `/search` |
| 50 篇   | ~2 秒    | 同步 `/search` |
| 100 篇  | ~4 秒    | 异步 `/tasks/search` |
| 200 篇  | ~8 秒    | 异步 `/tasks/search` |
| 500 篇  | ~20 秒   | 异步 `/tasks/search` |

### 使用建议
- **即时查询**：使用 `GET /search`，结果数 ≤ 50
- **批量检索**：使用 `POST /tasks/search`，支持进度查询
- **生产环境**：配置 `S2_API_KEY` 提升速率限制（0.5 → 2 req/s）
- **并发控制**：S2 有严格的速率限制，建议单实例部署
- **结果上限**：默认 500 篇，可通过 `MAX_RESULTS_LIMIT` 环境变量调整（1-1000）

---

## 📜 许可

MIT
