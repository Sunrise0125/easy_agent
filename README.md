下面是一份**详细的 `README.md`**，从目标→架构→每个模块与函数→端到端流程→调试与扩展，逐项讲清楚。你可以把它直接保存为 `paper_survey/readme.md` 使用。

---

# PaperFinder (S2 Graph v1 /paper/search)

> 前端传自然语言 → FastAPI → LLM 解析（受控 JSON）→ 参数规范化 → 调用 **Semantic Scholar `/graph/v1/paper/search`** → **客户端过滤**（FoS/venue/年限/作者/开源PDF/类型/最小引用）→ **综合排序** → 返回“少而精”的论文卡片（同时回传可复现的 API 访问参数）。

---

## 目录

* [1. 项目目标](#1-项目目标)
* [2. 路由与整体流程](#2-路由与整体流程)
* [3. 代码结构](#3-代码结构)
* [4. 安装与启动](#4-安装与启动)
* [5. 接口契约](#5-接口契约)
* [6. 模块详解（函数级）](#6-模块详解函数级)

  * [6.1 `main.py`](#61-mainpy)
  * [6.2 `llm_parser.py`](#62-llm_parserpy)
  * [6.3 `normalizer.py`](#63-normalizerpy)
  * [6.4 `s2_client.py`](#64-s2_clientpy)
  * [6.5 `ranking.py`](#65-rankingpy)
  * [6.6 `schemas.py`](#66-schemaspy)
  * [6.7 `top_venues.py`](#67-top_venuespy)
  * [6.8 `config.py`](#68-configpy)
  * [6.9 `logging_setup.py`](#69-logging_setuppy)
* [7. 排序策略](#7-排序策略)
* [8. 调试与常见问题](#8-调试与常见问题)
* [9. 性能与限流](#9-性能与限流)
* [10. 扩展建议](#10-扩展建议)

---

## 1. 项目目标

* 把**自然语言**检索意图转换为**稳定、可控**的学术检索：

  * **领域（Fields of Study）**严格映射到 Semantic Scholar **官方集合**；
  * **期刊/会议（venues）**限制在**白名单**（尤其是顶会/顶刊）；
  * 支持**年限**（近 X 年 / 区间 / 某年之后）、**开源 PDF**、**作者**、**论文类型**（研究/综述）等约束；
* 使用 **`/graph/v1/paper/search`**：

  * 该端点**不支持服务端过滤**，因此“FoS/venue/年限/开源PDF/类型/最小引用”等**在客户端过滤**；
* 返回**少而精**的 Top-N 论文，并附带**可复现的 API 请求参数**（便于排查与复现）。

---

## 2. 路由与整体流程

**`GET /search?user_query=...`**

```
┌─────────────┐        ┌─────────────┐        ┌──────────────────────┐
│   Frontend  │ ─────> │   FastAPI   │ ─────> │  LLM 解析 (JSON-only) │
└─────────────┘        └─────────────┘        └─────────┬────────────┘
                                                         │  受控集合（FoS/venues）
                                                         ▼
                                                规范化与兜底（中文年限等）
                                                         │
                                                         ▼
                                         /paper/search（分页、只按 query 召回）
                                                         │
                                                         ▼
                                     客户端过滤（FoS/venue/year/pdf/type/author/cites）
                                                         │
                                                         ▼
                                              重要性排序（综合打分）
                                                         │
                                                         ▼
                                  返回结果 + 本次 API 参数（便于复现/调试）
```

---

## 3. 代码结构

```
paper_survey/
├─ main.py                  # FastAPI 应用（/search、/health）
├─ config.py                # 环境变量读取（OpenAI, S2, RPS）
├─ schemas.py               # Pydantic 模型（SearchIntent, PaperMetadata, SearchResponse）
├─ normalizer.py            # 规则解析与规范化（FoS/年限等）
├─ llm_parser.py            # LLM 解析（JSON-only，受控集合）
├─ s2_client.py             # S2 /paper/search 客户端（分页/限流/重试/过滤）
├─ ranking.py               # 重要性排序（影响力+新近性+顶会+开源）
├─ top_venues.py            # 期刊/会议白名单（顶会/顶刊）
├─ logging_setup.py         # 控制台日志格式
├─ requirements.txt         # 依赖
├─ .env.example             # 环境变量示例
├─ start.ps1                # Windows 启动脚本（可选）
└─ start.sh                 # Linux/macOS 启动脚本（可选）
```

---

## 4. 安装与启动

1. 安装依赖：

```bash
pip install -r paper_survey/requirements.txt
```

2. 复制 `.env.example` → `.env`，按需填写：

```dotenv
OPENAI_API_KEY=your_llm_key_here
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat

S2_API_KEY=    # 可留空（匿名访问）
S2_RPS=0.5     # 无 Key 建议 <=0.5；有 Key 可 1.0

HOST=127.0.0.1
PORT=8000
```

3. 启动服务：

```bash
# Windows
pwsh .\paper_survey\start.ps1 -EnvFile "paper_survey\.env"
# 或
python -m uvicorn paper_survey.main:app --host 127.0.0.1 --port 8000 --reload --env-file ".\paper_survey\.env"
```

4. 测试：

```bash
curl --get "http://127.0.0.1:8000/search" \
  --data-urlencode "user_query=近三年 代码生成 大模型 ICLR 或 NeurIPS 需要开源PDF 综述不要 只看研究论文"
```

或浏览器打开：`http://127.0.0.1:8000/docs`

---

## 5. 接口契约

### 5.1 `GET /search`

**Query:**

* `user_query` (string) 自然语言检索语句

**Response:**

```json
{
  "query": "原始自然语言",
  "normalized_intent": {
    "main_topics": ["code generation","large language models"],
    "sub_topics": [],
    "fields_of_study": ["Computer Science"],
    "venues": ["ICLR","NeurIPS"],
    "author": null,
    "year_range": null,
    "recent_years": 3,
    "must_have_pdf": true,
    "paper_type": "research",
    "min_citations": null,
    "max_results": 10,
    "sort_by": "relevance",
    "language": "en"
  },
  "api_params": {
    "endpoint": "https://api.semanticscholar.org/graph/v1/paper/search",
    "query": "用户原始自然语言",
    "s2_query_built": "用于 /paper/search 的关键字",
    "fields": "paperId,title,url,abstract,authors,year,venue,externalIds,citationCount,influentialCitationCount,openAccessPdf,publicationTypes,publicationDate,fieldsOfStudy",
    "note": "paper/search 不支持服务端过滤；过滤逻辑在客户端"
  },
  "results": [
    {
      "title": "...",
      "authors": ["A", "B"],
      "abstract": "...",
      "year": 2024,
      "doi": "10.xxxx/...",
      "journal": "ICLR",
      "url": "https://...",
      "citations": 123,
      "influential_citations": 10,
      "open_access": true,
      "publication_types": ["Conference"],
      "publication_date": "2024-05-12",
      "fields_of_study": ["Computer Science"]
    }
  ]
}
```

> 说明：`api_params` 便于复现请求；**所有过滤**在客户端完成（见 `s2_client.py`）。

---

## 6. 模块详解（函数级）

### 6.1 `main.py`

* `setup_logging("INFO")`
  初始化控制台日志级别与格式。

* `app = FastAPI(...)`
  创建应用并启用 CORS。

* `@app.get("/health") -> {"ok": True}`
  健康检查。

* `@app.get("/search", response_model=SearchResponse)`
  **主流程**：

  1. `parse_user_intent(user_query)`：LLM 解析自然语言，输出受控 JSON（FoS/venues/年限/作者等）；
  2. `search_papers(intent)`：构造 `/paper/search` 的 `query`，分页拉取 → 客户端过滤；
  3. `rank_papers(papers, mode=intent.sort_by)`：综合打分或按引用/新近性排序；
  4. 组装 `api_params` 并返回。

---

### 6.2 `llm_parser.py`

* 常量 `SYSTEM`
  LLM **系统提示词**（必须 JSON-only 输出），明确每个字段的语义与约束：

  * `fields_of_study` 必须从**官方集合**中选（详见 `schemas.CANONICAL_FOS`）；
  * `venues` 必须来自**白名单**（详见 `top_venues.TOP_VENUES`）；
  * `year_range` / `recent_years`；`paper_type` 枚举；`sort_by` 枚举；

* 辅助函数

  * `_safe_json(text: str) -> dict`：安全解析 JSON；失败则返回 `{}` 并给日志；
  * `_guess_language(s: str) -> "zh"|"en"`：简单中文检测。

* 核心函数

  * `async def parse_user_intent(user_input: str) -> SearchIntent`

    1. 先用 `normalizer.parse_years` 粗提“近 X 年/区间/之后”；
    2. 调 LLM，按 `SYSTEM` 输出 JSON；
    3. **规范化/兜底**：FoS 映射、语言推断、年限补全、默认项补齐；
    4. 构造 `SearchIntent`（Pydantic 会校验类型），返回。

> 解析失败时，至少保证 `main_topics=[user_input]`，其余字段走默认。

---

### 6.3 `normalizer.py`

* `normalize_fos(fos_in: List[str]) -> List[str]`
  将中文/别名映射到官方 FoS 集合（`schemas.CANONICAL_FOS`）。

* `normalize_venues(vs: List[str], whitelist: set) -> List[str]`
  将期刊/会议简称标准化到白名单（本项目中 venues 的受控主要由 LLM 负责；此函数可用于二次兜底）。

* 年限解析：

  * `parse_years(text: str) -> (recent_years: Optional[int], year_range: Optional[List[Optional[int]]])`
    识别“近 X 年”“YYYY-YYYY”“YYYY 之后”等中文表达。
  * `recent_to_year_param(n: int) -> str`
    将“近 X 年”转换为 `YYYY-` 形式（**用于 bulk 端点时**；目前 `/paper/search` 不用）。

---

### 6.4 `s2_client.py`

* 常量

  * `SEARCH_URL`：`https://api.semanticscholar.org/graph/v1/paper/search`
  * `FIELDS`：请求的字段列表（逗号分隔，不含空格）

* 限流/重试

  * `_rate_limit()`：按 `S2_RPS` 控制 RPS（无 Key 建议低一些）
  * `_get(params) -> dict`：带指数退避的 GET（重试 429/5xx）

* 召回关键词构造

  * `_build_query(intent: SearchIntent) -> str`

    * 多词短语自动加引号（提升精准匹配）；
    * **仅**包含 `main_topics/sub_topics/author/venues`（把会议缩写也作为关键词，有利于把 ICLR/NeurIPS 文献召回到前几页）；
    * 其它约束都在**客户端过滤**。

* 匹配函数（**客户端过滤核心**）

  * `_author_match(p, target)`：作者包含/相等；
  * `_fos_match(p, fos)`：**允许缺失的 FoS 通过**（避免把没有填 FoS 的记录误删）；
  * `_venue_match(p, venues)`：对会议/期刊做**规范化+同义匹配**（`NeurIPS/NIPS/全称` 等）；
  * `_pubtype_match(p, t)`：`research` 近似映射为 `JournalArticle|Conference`；`review/survey` 识别 “review”；
  * `_pdf_match(p, must)`：是否必须开源；
  * `_year_match(p, recent_years, year_range)`：按年过滤；
  * `_min_citations_match(p, mc)`：最小引用数。

* 数据转换

  * `_item_to_paper(item) -> PaperMetadata`：把 S2 返回条目转为统一模型。

* 主函数

  * `async def search_papers(intent: SearchIntent) -> List[PaperMetadata]`

    1. `query = _build_query(intent)`；
    2. 分页请求（无 Key 时更保守：小页数+少翻页）；
    3. **逐级过滤**（作者→FoS→venue→开源→年份→类型→最小引用）；
    4. 合并、去重（可扩展）并返回。

> 可选：为了**精确定位 0 结果**，可改为返回 `(papers, filter_stats)`，在 `main.py` 通过 `debug=1` 一并返回统计（见“调试与常见问题”）。

---

### 6.5 `ranking.py`

* `importance(p: PaperMetadata) -> float`

  ```
  score = 0.45*log1p(citations) 
        + 0.25*recency_score 
        + 0.20*venue_score 
        + 0.10*log1p(influential_citations) 
        + 0.10*(open_access?1:0)
  ```

  * `recency_score`：近两年高分，之后指数衰减；
  * `venue_score`：在 `TOP_VENUES` 里满分，否则中等；
  * `open_access`：有开源 PDF 加微量分。

* `rank_papers(papers, mode="relevance") -> List[PaperMetadata]`

  * `importance|relevance`：综合打分；
  * `influentialCitationCount|influentialCitations`：按引用数；
  * `publicationDate|freshness|date|newest`：按新近性。

---

### 6.6 `schemas.py`

* **常量**

  * `CANONICAL_FOS`：官方 FoS 集合（中文解析/LLM 受控选择的基准集合）

* **模型**

  * `SearchIntent`：从 LLM 解析得到、并经规范化后的**受控意图**对象

    * `main_topics/sub_topics`、`fields_of_study`、`venues`、`author`、`year_range`、`recent_years`、`must_have_pdf`、`paper_type`（review|survey|research）、`min_citations`、`max_results`、`sort_by`、`language`
  * `PaperMetadata`：论文卡片信息
  * `SearchResponse`：接口响应（含 `normalized_intent` 与 `api_params`）

---

### 6.7 `top_venues.py`

* `TOP_VENUES: Set[str]`

  * 维护期刊/会议**白名单**（首版给了主流顶会顶刊，可按需扩展）。
  * **LLM 限制**：`venues` 字段必须从这里选择。

---

### 6.8 `config.py`

* 环境变量读取：

  * LLM：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`
  * S2：`S2_API_KEY`（可空）、`S2_RPS`（匿名建议 0.3–0.5，有 Key 可 1.0）
  * Server：`HOST`、`PORT`

---

### 6.9 `logging_setup.py`

* `setup_logging(level="INFO")`

  * 统一日志格式：`[time] LEVEL logger: message`

---

## 7. 排序策略

默认 **importance**（等价于你们的启发式）：

* **影响力**：`log1p(citations)` 与 `log1p(influential_citations)`；
* **时效性**：最近两年加权，之后指数衰减；
* **场馆权重**：顶会顶刊更高；
* **开源 PDF**：略加分（用户通常偏好能直接下载）。

可通过 `intent.sort_by` 切换：`citations`、`publicationDate`、`importance|relevance`。

---

## 8. 调试与常见问题

### 8.1 “结果为 0”的常见原因

1. **场馆匹配过严**：`venue` 常是**全称**，与你传的缩写不一致（如 *NeurIPS* vs *Advances in Neural Information Processing Systems*）。

   * 已在 `_venue_match` 做了**同义/包含式**匹配。
2. **FoS 缺失**：很多记录没有 `fieldsOfStudy`；**缺失已放行**以避免误删。
3. **第一页召回不足**：`/paper/search` 只按关键词召回，无 Key 时页小且翻页少；多翻一页/把 venue 缩写也加入 `query` 可改善。
4. **论文类型/开源 PDF 过滤过严**：临时放开 `must_have_pdf` 或 `paper_type` 验证链路是否通畅。

### 8.2 一键定位瓶颈（可选增强）

将 `s2_client.search_papers` 改为返回 `(papers, filter_stats)`，在 `main.py` `debug=1` 时返回：

```json
"debug": {
  "filter_stats": {
    "raw_count": 78,
    "after_author": 78,
    "after_fos": 74,
    "after_venue": 6,
    "after_pdf": 4,
    "after_year": 3,
    "after_pubtype": 3,
    "after_min_citations": 3
  },
  "sample_venues_from_results": [
    "Advances in Neural Information Processing Systems",
    "International Conference on Learning Representations"
  ]
}
```

这样能**直观看到是哪一步砍到了 0**。

---

## 9. 性能与限流

* 无 `S2_API_KEY` 时使用**匿名共享池**，限制较严：

  * 建议 `S2_RPS ≤ 0.5`；
  * 每页 `limit` 小一点（25），翻页少一点（2–3 页）；
* 有 Key：可 `S2_RPS = 1.0`，`limit` 50–100，翻到 3–4 页，召回更稳；
* `_get()` 对 429/5xx 做了**指数退避**重试。

---

## 10. 扩展建议

1. **切换 `/paper/search/bulk`**（推荐后续升级）：

   * 可以将 `fieldsOfStudy/year/venue/publicationTypes/openAccessPdf/mininfluentialCitationCount/sort` 等**下放到服务端过滤**，第一页就返回高质量候选；
   * 当前代码结构已分层，迁移很容易（把客户端过滤器迁到请求参数；`_build_query` 保持不变）。

2. **OpenAlex 兜底**：

   * 当连续 429 或召回为 0 时调用 OpenAlex（同样的关键词与过滤），合并去重再排序。

3. **作者精确 ID**：

   * 先 `GET /graph/v1/author/search` 获取 `authorId`，再 `GET /graph/v1/author/{id}/papers` 做精确作者限定（成本更高，按需启用）。

4. **前端**：

   * 用 `/docs` 自测；或做一个简单 HTML + Fetch 页展示结果卡片（标题/作者/年份/venue/开源标识/链接）。

---

### 附：LLM 解析的提示词（精简版）

> 定位：`llm_parser.py` → `SYSTEM` 常量
> 作用：保证 JSON-only、字段受控、FoS/venues 从白名单中选择、年限表达统一。

```text
你是学术论文检索意图解析助手。只输出 JSON，不要任何多余文本。
请从用户自然语言中抽取以下结构：
{
  "main_topics": ["string"],
  "sub_topics": ["string"],
  "fields_of_study": ["string"],        # 从官方集合中选择，不确定留空
  "venues": ["string"],                  # 从 whitelist 中选择，不确定留空
  "author": "string|null",
  "year_range": [int|null, int|null],   # [2022,2024] 或 [2023,null]
  "recent_years": int|null,             # 近N年，与 year_range 同时出现时优先
  "must_have_pdf": true/false,
  "paper_type": "review|survey|research|null",
  "min_citations": int|null,
  "max_results": int,
  "sort_by": "relevance|influentialCitationCount|publicationDate",
  "language": "zh|en"
}
注意：
- fields_of_study、venues 必须从提供集合中选择；不确定为空数组。
- 主题简洁；多词短语不要拆开。
```

---

## 最后

* 这套 README 对应你现在的代码骨架（`/paper/search` 端点 + 客户端过滤 + 受控 LLM 解析 + 综合排序）。
* 如果想要，我也可以提供一份 **“切换到 `/paper/search/bulk` 的差异补丁”**（把过滤参数迁到服务端，客户端只做少量兜底）。
* 有任何 0 结果或限流问题，建议开启上文的 **filter_stats 调试**，一看就能知道卡在哪一步。
