下面是一份干净、可落地的 **README.md**，基于你当前的目录与启动方式（`pwsh .\start.ps1 -EnvFile "paper_survey\.env"`）。直接保存为 `README.md` 即可。

---

# Paper Survey Agent

一个基于 **FastAPI** 的学术论文检索服务：
**自然语言 →（LLM 解析）→ Semantic Scholar Bulk API 检索 → 服务器端/本地过滤 → 排序与返回**。
适合“按主题 + 时间窗 + 场馆/作者 + PDF 可用性 +（可选）引用阈值”的组合检索。

---

## 目录结构

```
paper_survey/
├─ .env                   # 你的环境变量（本地）
├─ .env.example           # 示例环境变量
├─ config.py              # 读取 .env 的配置中心
├─ llm_parser.py          # LLM 将自然语言解析为结构化 SearchIntent
├─ logging_setup.py       # 日志格式与级别
├─ main.py                # FastAPI 入口与 /search 路由
├─ ranking.py             # 论文排序（包含“按日”粒度新鲜度）
├─ s2_client.py           # S2 Bulk API 调用、服务端参数构造、本地兜底过滤
├─ schemas.py             # Pydantic 模型（SearchIntent / PaperMetadata / SearchResponse）
├─ start.ps1              # Windows 启动脚本（加载 .env、启动 Uvicorn）
└─ requirements.txt       # 依赖
```

---

## 运行要求

* Python ≥ 3.9（推荐 3.10/3.11）
* Windows PowerShell 7+（使用提供的 `start.ps1`）
* 能访问 `https://api.semanticscholar.org/graph/v1`
* （强烈推荐）申请 **Semantic Scholar API Key**（提升稳定性和页容量）

---

## 安装依赖

```powershell
# 建议在虚拟环境内执行
pip install -r requirements.txt
```

---

## 配置 .env

参考 `.env.example` 填写到 `paper_survey\.env`（示例）：

```ini
# === Semantic Scholar ===
S2_BASE=https://api.semanticscholar.org/graph/v1
S2_API_KEY=YOUR_S2_API_KEY   # 可为空；为空时 bulk 仍可用，但页容量/稳定性受限
S2_RPS=1                     # 每秒请求速率（建议 1）

# === LLM（DeepSeek 作为 OpenAI 兼容）===
OPENAI_API_KEY=YOUR_DEEPSEEK_KEY
OPENAI_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat

# === 服务 ===
HOST=127.0.0.1
PORT=8000
LOG_LEVEL=INFO
```

> 说明
>
> * `S2_API_KEY` 可选，但**强烈建议**设置；有 key 时 `bulk` 端点能稳定排序/分页。
> * 我们使用 OpenAI 兼容 SDK 访问 DeepSeek：`OPENAI_BASE_URL` 与 `OPENAI_API_KEY` 必填。
> * `LLM_MODEL` 改成你实际在 `llm_parser.py` 使用的模型名（默认 `deepseek-chat`）。

---

## 启动服务（Windows）

```powershell
pwsh .\start.ps1 -EnvFile "paper_survey\.env"
```

启动后默认监听：`http://127.0.0.1:8000`

---

## 如何调用

### 浏览器直接访问

把自然语言 URL 编码后拼到 `user_query`：

```
http://127.0.0.1:8000/search?user_query=%E8%BF%91%E4%B8%89%E5%B9%B4+code+generation+LLM+NeurIPS+ICLR+%E5%BC%80%E6%BA%90PDF
```

### curl 示例

```bash
curl "http://127.0.0.1:8000/search?user_query=近三年 代码生成 大模型 ICLR 或 NeurIPS 需要开源PDF"
```

> Tips：命令行看到的“中文乱码”是**URL 编码**，不是服务异常。

---

## 请求到响应的完整流程

1. **LLM 解析（`llm_parser.py`）**

   * 将用户自然语言解析为结构化的 `SearchIntent`：

     * `any_groups`: AND-of-OR 主题词组（同义词组内“或”，组与组之间“与”）
     * `venues`: 指定会议/期刊（白名单简写，如 `ICLR, NeurIPS, CVPR`）
     * `author`: 作者名（用于召回 + 本地严格匹配）
     * `date_start/date_end`: 统一到 `YYYY-MM-DD`（支持“近 N 天/周/月/年/半年”的相对时间）
     * `must_have_pdf`: 仅要开放 PDF
     * `publication_types`: `["JournalArticle","Conference","Review"]`（内部通过服务端/本地映射）
     * `min_influential_citations`: 最小“有影响力引用数”（**本地过滤**）
     * `max_results`、`sort_by`（`publicationDate`/`citationCount`/`relevance`）

2. **S2 检索（`s2_client.py`）**

   * **仅使用 /graph/v1/paper/search/bulk**
   * 构造 query：将 `any_groups` 组装为 AND-of-OR 的“关键词提示串”（组内 `OR`，组间空格连接）。

     > 注意：S2 的 query 是关键词匹配，布尔语法支持有限，上述构造是“增强召回”的**最佳实践**，真正精准过滤交给服务端参数与本地兜底过滤。
   * **服务端过滤/排序（尽可能交给 S2）**：

     * `publicationDateOrYear=<start>:<end>`（支持 `YYYY`/`YYYY-MM`/`YYYY-MM-DD`）
     * `publicationTypes=Review` 或 `JournalArticle,Conference`
     * `openAccessPdf=true`
     * `venue=ICLR,NeurIPS,...`（服务端不够稳，本地再核）
     * `sort=publicationDate | citationCount`（bulk 不保证 `relevance`，`relevance` 建议交给本地排序）

3. **本地兜底过滤（`s2_client.py`）**

   * 作者名包含/等值匹配
   * 场馆名同义词/规整化匹配（`VENUE_SYNONYMS`）
   * 文献类型（缺失视为 research 通过）
   * PDF 开放性
   * **时间窗精确到“日”**（优先 `publication_date`，无则用 `year` 的 7/1 近似）
   * 最小“有影响力引用数”阈值

4. **排序（`ranking.py`）**

   * `publicationDate`：按**天级新鲜度**（越新越靠前）；同分用场馆/影响力引用打破
   * `relevance`：内置“重要性”加权（可自定义，你的版本偏向新鲜度+场馆+有影响力引用）
   * `citationCount`：按总引用降序
   * **最终按 `max_results` 截断**

5. **响应（`main.py`）**

   * `results`: 返回排序+截断后的论文
   * `batch`: 返回“最后一页原始转换样本”（便于调试）
   * `api_params`: 返回用于 S2 的真实参数（便于复现与排错）
   * `counts`: 返回四个计数

     * `server_total`: S2 报告的总量（如有）
     * `raw_fetched`: 实际抓到条数（过滤前）
     * `after_filter`: 本地过滤后条数
     * `after_rank_cut`: 排序+截断后的条数（即前端看到的数量）

---

## API 说明

### `GET /search`

* **Query**: `user_query`（自然语言）
* **Response**（关键字段）：

  * `normalized_intent`: 解析后的 `SearchIntent`
  * `api_params`: 实际用于 S2 的参数（含 `s2_query_built`）
  * `counts`: 服务端/本地过滤前后的统计
  * `results`: 最终论文列表（字段见 `PaperMetadata`）
  * `batch`: 最后一页的原始转换（未过滤，用于对照/调试）

示例：

```json
{
  "query": "find me the most cited papers on large language models for code generation in the last 6 months",
  "normalized_intent": { ... },
  "api_params": {
    "endpoint": "graph/v1/paper/search/bulk",
    "s2_query_built": "(\"large language models\" OR LLM) (\"code generation\" OR \"neural code generation\")",
    "server_params": {
      "publicationDateOrYear": "2025-05-05:2025-11-05",
      "publicationTypes": "JournalArticle,Conference",
      "openAccessPdf": "true",
      "venue": "ICLR,NeurIPS",
      "sort": "citationCount",
      "limit": 50,
      "offset": 0
    }
  },
  "counts": {
    "server_total": 124,
    "raw_fetched": 100,
    "after_filter": 22,
    "after_rank_cut": 10
  },
  "results": [ ... ],
  "batch": [ ... ]
}
```

---

## 日志

* 级别通过 `.env` 的 `LOG_LEVEL` 控制（`DEBUG/INFO/WARNING`）。
* `s2_client.py` 会输出：端点参数、分页、统计计数（`server_total/raw_fetched/after_filter/pages`）。

---

## 常见问题（FAQ）

**Q1：中文“乱码”？**
A：浏览器/日志里看到的是 URL 编码，正常。实际服务会按 UTF-8 收到中文。

**Q2：为什么会 0 条？**

* 关键字过窄，或布尔写法被 S2 忽略 → 尝试扩大 `any_groups`，减少过细短语；
* 时间窗过窄（例如近 7 天）→ 适当放宽；
* 强约束过多（同时要求场馆+作者+PDF+窄时间）→ 逐项放宽定位问题；
* 无 API Key 时，bulk 稳定性与页容量下降 → 建议配置 `S2_API_KEY`。

**Q3：`relevance` 排序是否服务端支持？**

* bulk 端点通常保证 `publicationDate` 与 `citationCount`；`relevance` 并不稳定。
* 推荐使用本地的 `relevance/importance` 逻辑（见 `ranking.py`）。

**Q4：如何扩充会议/期刊同义词？**

* 修改 `s2_client.py` 的 `VENUE_SYNONYMS`。

**Q5：如何调整“新鲜度”权重？**

* 在 `ranking.py` 修改 `importance()` 或“按日新鲜度”的衰减参数。

---

## 开发调试建议

* 将 `.env` 中 `LOG_LEVEL=DEBUG`，观察

  * `[S2 BULK PARAMS]` 相关日志
  * `server_total/raw_fetched/after_filter` 计数
* 若 LLM 输出 JSON 被代码块包裹（```json），请确保 `llm_parser.py` 的提示词**明确禁止** Markdown 代码块（项目中已处理）。
* 若想快速回显“解析后的检索词串”，看返回体中的 `api_params.s2_query_built`。

---

## 许可

本项目用于学术研究与原型验证，调用 Semantic Scholar API 须遵守其使用条款。

---

有别的环境/部署方式（如 Linux systemd、Docker）想要一份脚本样板，我可以给你补一版。
