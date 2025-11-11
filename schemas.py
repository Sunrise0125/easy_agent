# paper_survey/schemas.py
from typing import List, Optional, Dict, Any, Literal
from pydantic import BaseModel, Field

# CANONICAL_FOS = [
#     "Computer Science", "Medicine", "Biology", "Chemistry", "Physics",
#     "Materials Science", "Engineering", "Environmental Science", "Psychology",
#     "Sociology", "Political Science", "Business", "Education", "History",
#     "Mathematics", "Economics", "Geography", "Geology", "Art", "Law",
#     "Philosophy", "Linguistics", "Agricultural and Food Sciences"
# ]


#用于识别用于提及的顶级会议和期刊简称，以及用来ranking加分
TOP_VENUES = {
    # ML/AI
    "NeurIPS", "NIPS", "ICLR", "ICML", "AAAI", "IJCAI", "JMLR",
    # CV
    "CVPR", "ICCV", "ECCV", "TPAMI", "IJCV",
    # NLP
    "ACL", "EMNLP", "NAACL", "COLING", "TACL",
    # IR/Web/DB/Data
    "SIGIR", "WWW", "KDD", "VLDB", "SIGMOD", "ICDE",
    # 其它可扩展...
}
#可选的检索来源
FOS_TO_SOURCES = {
    "computer science": ["s2","openalex","arxiv"],
    "physics":          ["s2","openalex","arxiv"],
    "mathematics":      ["s2","openalex","arxiv"],
    "biomedicine":      ["s2","pubmed","eupmc"],
    "medicine":         ["s2","pubmed","eupmc"],
    "chemistry":        ["s2","openalex"],
    # … 可继续扩展 …
}
class SearchIntent(BaseModel):
    # 查询语义
    any_groups: List[List[str]] = Field(default_factory=list)  # AND-of-OR，同义词组
    enabled_sources: Optional[List[str]] =  Field(default_factory=lambda: ["s2"])
    # 允许的值：["s2","openalex","crossref","arxiv","pubmed","eupmc"]
    # 约束（全部映射到 bulk 的服务端过滤）
    venues: List[str] = Field(default_factory=list)            # 只允许白名单的简称，如: ["ICLR","NeurIPS","CVPR"]
    author: Optional[str] = None                               # 精确短语匹配，仍建议作为 must_terms 追加
    date_start: Optional[str] = None                           # "YYYY-MM-DD" 或 "YYYY-MM"
    date_end: Optional[str] = None
    must_have_pdf: bool = False                          # True=必须有 PDF

    publication_types: List[str] = Field(default_factory=list) # ["JournalArticle","Conference","Review"]
    open_access: Optional[bool] = None                         # True=必须开源 PDF；None=不限
    min_influential_citations: Optional[int] = None
    min_citations: Optional[int] = None
    # 其他
    max_results: int = 10
    sort_by: Literal["publicationDate","citationCount","relevance"] = "publicationDate"
    language: Optional[str] = None  # 仅用于后续 LLM 摘要/重排序；S2 不支持语言过滤

class PaperMetadata(BaseModel):
    title: str
    authors: List[str] = Field(default_factory=list)   #还需要得到作者的谷歌学术的被引用数
    first_author_hindex :Optional[int] = None  #作者的知名信息，H-index 或 被引用数 
    abstract: Optional[str] = None
    year: Optional[int] = None
    doi: Optional[str] = None
    journal: Optional[str] = None                                    #发表期刊
    url: Optional[str] = None
    citations: Optional[int] = None
    influential_citations: Optional[int] = None                 #有影响力引用数
    open_access: bool = False
    publication_types: List[str] = Field(default_factory=list)
    publication_date: Optional[str] = None                   #发表时间
    fields_of_study: List[str] = Field(default_factory=list)

class SearchResponse(BaseModel):
    query: str
    normalized_intent: SearchIntent
    api_params: Dict[str, Any]
    results: List[PaperMetadata]
    batch: Any
