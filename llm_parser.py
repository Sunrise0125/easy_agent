# paper_survey/llm_parser.py
import json, logging, re
from datetime import datetime
from typing import Any, Dict, List
from openai import OpenAI
from schemas import SearchIntent, TOP_VENUES  , FOS_TO_SOURCES
from config import OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_MODEL


logger = logging.getLogger("paper_survey.llm_parser")

client = OpenAI(
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL,
)

# FOS_ALLOWED = ", ".join(CANONICAL_FOS)

# 仅允许的会议/期刊白名单
VENUES_ALLOWED = ", ".join(sorted(TOP_VENUES))
SYSTEM = (
    "你是学术论文检索意图解析助手。\n"
    "严格输出 JSON，不允许任何多余文字或解释。\n"
    "禁止使用 Markdown 代码块标记（如 ```json），直接输出纯 JSON!\n"
    "请尽可能从用户输入中提取所有信息，并遵守以下规则：\n"
    "\n"
    "【论文主题关心信息提取（用于检索查询）（必填）】\n"
    "- any_groups: string[][]   # AND-of-OR。同一子数组内为同义短语/句“或”关系，子数组之间为“且”关系,用于准确表达需要检索论文的主题。\n"
    "- 统一输出为英文可检索短语短句；多词短语无需手动加引号；每组 1~3个词为宜；至少给出 1 组。\n"
    "  例：[[\"large language models\",\"LLM\",\"foundation models\"],[\"code generation\",\"program synthesis\"]]\n"
    "- 如果用户的检索意图十分复杂，实在无法用关键词的与或关系表示，可以考虑生成适合检索的且能准确表达用户语义的复杂风格的短句。如示例2\n"
    "\n"
    "\n"
    "【检索来源确定（必填）】\n"
    "- enabled_sources:  [\"s2\",\"openalex\",\"crossref\",\"arxiv\",\"pubmed\",\"eupmc\"],必须从以上名单中选择1-3个检索来源，根据用户的输出判断合适的学科，以及该学科适合的论文检索来源，s2必须选择，其他选择0-2个。\n"
    f"- 学科与来源对应关系参考：{{ {json.dumps(FOS_TO_SOURCES, ensure_ascii=False)} }}\n"
    "\n"
    "【受控词表】\n"
    "- venues：必须从以下白名单中选择；用户未提及或不在白名单则置为 []，不要臆造期刊/会议名称。\n"
    f"- 白名单：{{ {VENUES_ALLOWED} }}\n"
    "\n"
    "【作者/类型/语言】\n"
    "- author：作者姓名字符串或 null（未提及）。\n"
    "- publication_types：从 [\"JournalArticle\",\"Conference\",\"Review\"] 中选择若干项；若用户未提及则置为 []。\n"
    "  （约定：用户说“综述/Survey”→ [\"Review\"]；用户说“研究论文/Research”→ [\"JournalArticle\",\"Conference\"]）\n"
    "- language：从 zh / en / null 中三选一（明确要求中文→zh；明确要求英文→en；未提及→null）。\n"
    "\n"
    "【时间约束统一格式】\n"
    "无论用户输入何种时间表达，统一解析为：\n"
    "  date_start: \"YYYY-MM-DD\" | null\n"
    "  date_end:    \"YYYY-MM-DD\" | null\n"
    "规则：\n"
    "- 明确区间：直接对应起止；\n"
    "- 给出年份/月份：用整年/整月范围（如 2024年→2024-01-01~2024-12-31；2024年06月→2024-06-01~2024-06-30）；\n"
    "- “近 N 天/周/月/年/半年”：以今天为基准反推 start，end 为今天（近1周=7天，近1月≈30天，近半年≈182天，近1年=365天）；\n"
    "- 无法识别时间：两者均为 null。\n"
    "\n"
    "【其它约束】\n"
    "- must_have_pdf：true/false（需要可下载/开源 PDF→true；未提及→false）。\n"
    "- min_influential_citations null（仅按有影响力引用数做阈值，未提及→null）。\n"
    "- max_results：整数，推荐 5/10/20；未提及→10。\n"
    "\n"
    "【排序策略 sort_by】\n"
    "- 允许值：relevance / citationCount / publicationDate（区分大小写按示例输出）。\n"
    "- 否则默认使用 relevance。\n"
    "\n"
    "【健壮性】\n"
    "- 所有键必须出现；未提及处置为 null 或空数组；类型必须与下方规范一致。\n"
    "\n"
    "【输出 JSON 结构（键名固定，不得缺漏）】\n"
    "{\n"
    "  \"any_groups\": [[\"string\", ...], [\"string\", ...]],\n"
    "  \"enabled_sources\": [\"string\", ...],\n"
    "  \"venues\": [\"string\", ...],\n"
    "  \"author\": \"string\" | null,\n"
    "  \"date_start\": \"YYYY\" | \"YYYY-MM\" | \"YYYY-MM-DD\" | null,\n"
    "  \"date_end\": \"YYYY\" | \"YYYY-MM\" | \"YYYY-MM-DD\" | null,\n"
    "  \"must_have_pdf\": true | false,\n"
    "  \"publication_types\": [\"JournalArticle\" | \"Conference\" | \"Review\", ...],\n"
    "  \"min_influential_citations\": int | null,\n"
    "  \"max_results\": int,\n"
    "  \"sort_by\": \"relevance\" | \"citationCount\" | \"publicationDate\",\n"
    "  \"language\": \"zh\" | \"en\" | null\n"
    "}\n"
    "\n"
    "【示例 1】\n"
    "用户：我想找2022到2024年发表在CVPR或ICCV的深度学习目标检测综述论文，要求PDF可下载\n"
    "输出：\n"
    "{\n"
    "  \"any_groups\": [[\"object detection\"],[\"deep learning\"]],\n"
    "  \"enabled_sources\": [\"s2\",\"openalex\",\"arxiv\"],\n"
    "  \"venues\": [\"CVPR\",\"ICCV\"],\n"
    "  \"author\": null,\n"
    "  \"date_start\": \"2022\",\n"
    "  \"date_end\": \"2024\",\n"
    "  \"must_have_pdf\": true,\n"
    "  \"publication_types\": [\"Review\"],\n"
    "  \"min_influential_citations\": null,\n"
    "  \"max_results\": 10,\n"
    "  \"sort_by\": \"relevance\",\n"
    "  \"language\": null\n"
    "}\n"
    "\n"
    "【示例 2】\n"
    "用户：给我一些论文，证明在小数据集上进行大型语言模型预训练可以比在大数据集上训练出更好的模型\n"
    "输出：\n"
    "{\n"
    "  \"any_groups\": [\"advantages of small curated datasets for LLM pretraining\",\"small high-quality dataset outperform large noisy dataset language model pretraining\",\"data quality vs quantity in language model pretraining\"]\n"
    "  \"venues\": [\"s2\",\"openalex\",\"arxiv\"],\n"
    "  \"venues\": [],\n"
    "  \"author\": null,\n"
    "  \"date_start\": \"null\",\n"
    "  \"date_end\": \"null\",\n"
    "  \"must_have_pdf\": false,\n"
    "  \"publication_types\": [],\n"
    "  \"min_influential_citations\": null,\n"
    "  \"max_results\": 10,\n"
    "  \"sort_by\": \"relevance\",\n"
    "  \"language\": null\n"
    "}\n"
    "\n"
    "请严格按照此 JSON 结构输出，并尽量填满所有字段，确保类型与集合约束正确。"
)

# _CANON_BY_LOWER = {c.lower(): c for c in CANONICAL_FOS}
# def normalize_fos(fos_in: List[str]) -> List[str]:
#     """
#     - 验证LLM输出的领域field 非法项直接忽略
#     - 去重并保持原有顺序
#     -LLM大概率不会出错吧
#     """
#     out, seen = [], set()
#     for x in fos_in or []:
#         key = str(x).strip().lower()
#         canon = _CANON_BY_LOWER.get(key)
#         if canon and canon not in seen:
#             out.append(canon)
#             seen.add(canon)
#     return out


def _safe_json(text: str) -> Dict[str, Any]:
    """
    尝试检查LLM 输出的 JSON 字符串，失败则返回空字典并记录日志。
    """
    try:
        return json.loads(text)
    except Exception as e:
        logger.warning(f"LLM JSON parse failed: {e}; text={text[:200]}")
        return {}


async def parse_user_intent(user_input: str) -> SearchIntent:
    """
    使用 LLM 解析用户的自然语言输入，生成 SearchIntent 结构体。
    data：LLM 解析得到的原始JSON，需进一步规范化处理。
    intent：最终的 SearchIntent 结构体。
    """

    current_date = datetime.now().strftime("%Y-%m-%d")

    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"当前日期：{current_date}\n用户输入：{user_input}"},
    ]
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=messages,
        temperature=0.2,
    )
    raw = (resp.choices[0].message.content or "").strip()

    data = _safe_json(raw)

    # fields_of_study 规范化
    #data["fields_of_study"] = normalize_fos(data.get("fields_of_study") or [])

    # 默认项
    data.setdefault("must_have_pdf", False)
    data.setdefault("max_results", 10)
    data.setdefault("sort_by", "relevance")

    intent = SearchIntent(**data)  # Pydantic 会做类型校验
    logger.info(f"[INTENT] {intent.dict()}")
    return intent
