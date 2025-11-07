#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试脚本：通过预设查询测试论文检索功能
记录完整的中间过程：LLM 解析结果 + S2 API 查询结果
"""

import asyncio
import json
import os
from datetime import datetime
from typing import List, Dict, Any
from pathlib import Path

# 导入项目模块
from llm_parser import parse_user_intent
from s2_client import search_papers
from ranking import rank_papers
from schemas import SearchIntent, PaperMetadata


# ========== 测试用例定义 ==========
TEST_QUERIES = [
    # "找一些2023年到2024年关于大语言模型的论文，发表在NeurIPS或ICLR",
    # "深度学习目标检测综述，CVPR会议，最近三年",
    # "Transformer架构的最新研究，要求有PDF，按引用数排序",
    # "多模态学习在医学图像中的应用",
    # "强化学习与机器人控制，2024年，按时间排序",
    # "图神经网络在推荐系统中的应用，需要开源PDF",
    # "自然语言处理中的few-shot learning",
    "计算机视觉中的对抗样本攻击与防御",
]


class TestLogger:
    """测试日志记录器，保存到 JSON 和 Markdown 文件"""
    
    def __init__(self, output_dir: str = "test_results"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.results: List[Dict[str, Any]] = []
        
    def log_test_case(self, 
                     query: str,
                     intent: SearchIntent,
                     llm_raw_response: str,
                     papers: List[PaperMetadata],
                     stats: Dict[str, Any],
                     papers_final: List[PaperMetadata],
                     error: str = None):
        """记录单个测试用例的完整信息"""
        
        result = {
            "query": query,
            "timestamp": datetime.now().isoformat(),
            "error": error,
        }
        
        if not error:
            result.update({
                "llm_parsing": {
                    "raw_response": llm_raw_response,
                    "parsed_intent": intent.dict() if intent else None,
                },
                "s2_api": {
                    "query_combinations": stats.get("query_combinations"),
                    "queries": stats.get("queries"),
                    "total_raw_fetched": stats.get("total_raw_fetched"),
                    "total_raw_unique": stats.get("total_raw_unique"),
                    "final_unique_count": stats.get("final_unique_count"),
                    "total_pages": stats.get("total_pages"),
                    "individual_stats": stats.get("individual_stats"),
                },
                "ranking_and_cutoff": {
                    "sort_mode": intent.sort_by if intent else None,
                    "max_results": intent.max_results if intent else None,
                    "final_count": len(papers_final),
                },
                "final_results": [
                    {
                        "title": p.title,
                        "authors": p.authors,
                        "year": p.year,
                        "venue": p.journal,
                        "citations": p.citations,
                        "influential_citations": p.influential_citations,
                        "url": p.url,
                        "has_pdf": p.open_access,
                    }
                    for p in papers_final
                ]
            })
        
        self.results.append(result)
        
    def save_results(self):
        """保存测试结果到文件"""
        # 保存为 JSON
        json_path = self.output_dir / f"test_results_{self.timestamp}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2)
        print(f"\n✓ JSON 结果已保存到: {json_path}")
        
        # 保存为 Markdown（更易读）
        md_path = self.output_dir / f"test_results_{self.timestamp}.md"
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(f"# 论文检索测试报告\n\n")
            f.write(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            f.write(f"**测试用例数**: {len(self.results)}\n\n")
            f.write("---\n\n")
            
            for idx, result in enumerate(self.results, 1):
                f.write(f"## 测试 {idx}: {result['query']}\n\n")
                
                if result.get('error'):
                    f.write(f"**❌ 错误**: {result['error']}\n\n")
                    continue
                
                # LLM 解析结果
                f.write("### 1️⃣ LLM 意图解析\n\n")
                llm_data = result.get('llm_parsing', {})
                f.write("**原始 LLM 响应**:\n```json\n")
                f.write(llm_data.get('raw_response', ''))
                f.write("\n```\n\n")
                
                intent = llm_data.get('parsed_intent', {})
                f.write("**解析后的查询意图**:\n")
                f.write(f"- 查询关键词组: `{intent.get('any_groups')}`\n")
                f.write(f"- 目标会议/期刊: `{intent.get('venues')}`\n")
                f.write(f"- 作者: `{intent.get('author')}`\n")
                f.write(f"- 日期范围: `{intent.get('date_start')}` ~ `{intent.get('date_end')}`\n")
                f.write(f"- 必须有PDF: `{intent.get('must_have_pdf')}`\n")
                f.write(f"- 论文类型: `{intent.get('publication_types')}`\n")
                f.write(f"- 最小影响力引用: `{intent.get('min_influential_citations')}`\n")
                f.write(f"- 最大结果数: `{intent.get('max_results')}`\n")
                f.write(f"- 排序方式: `{intent.get('sort_by')}`\n\n")
                
                # S2 API 查询结果
                f.write("### 2️⃣ S2 API 查询\n\n")
                s2_data = result.get('s2_api', {})
                f.write(f"**查询组合数**: `{s2_data.get('query_combinations')}`\n\n")
                
                queries = s2_data.get('queries', [])
                if queries:
                    f.write("**执行的查询组合**:\n")
                    for i, q in enumerate(queries, 1):
                        f.write(f"{i}. `{q}`\n")
                    f.write("\n")
                
                f.write("**查询统计（汇总）**:\n")
                f.write(f"- 总抓取条数: `{s2_data.get('total_raw_fetched')}`\n")
                f.write(f"- 总去重后条数: `{s2_data.get('total_raw_unique')}`\n")
                f.write(f"- 最终唯一条数: `{s2_data.get('final_unique_count')}`\n")
                f.write(f"- 总翻页数: `{s2_data.get('total_pages')}`\n\n")
                
                # 显示每个查询的详细统计
                individual_stats = s2_data.get('individual_stats', [])
                if individual_stats:
                    f.write("**各查询详细统计**:\n")
                    for i, stat in enumerate(individual_stats, 1):
                        f.write(f"\n查询 {i}: `{stat.get('query')}`\n")
                        f.write(f"- 抓取: {stat.get('raw_fetched')}, ")
                        f.write(f"去重: {stat.get('raw_unique')}, ")
                        f.write(f"过滤后: {stat.get('after_filter')}, ")
                        f.write(f"页数: {stat.get('pages')}\n")
                    f.write("\n")
                
                # 排序和截断
                f.write("### 3️⃣ 排序与截断\n\n")
                rank_data = result.get('ranking_and_cutoff', {})
                f.write(f"- 排序模式: `{rank_data.get('sort_mode')}`\n")
                f.write(f"- 请求数量: `{rank_data.get('max_results')}`\n")
                f.write(f"- 最终返回: `{rank_data.get('final_count')}` 篇\n\n")
                
                # 最终结果
                f.write("### 4️⃣ 最终结果\n\n")
                papers = result.get('final_results', [])
                if not papers:
                    f.write("*未找到符合条件的论文*\n\n")
                else:
                    for i, paper in enumerate(papers, 1):
                        f.write(f"#### [{i}] {paper.get('title', 'N/A')}\n\n")
                        authors = paper.get('authors', [])
                        f.write(f"- **作者**: {', '.join(authors[:3])}")
                        if len(authors) > 3:
                            f.write(f" 等 {len(authors)} 人")
                        f.write("\n")
                        f.write(f"- **年份**: {paper.get('year', 'N/A')}\n")
                        f.write(f"- **会议/期刊**: {paper.get('venue', 'N/A')}\n")
                        f.write(f"- **引用数**: {paper.get('citations', 0)} "
                               f"(影响力引用: {paper.get('influential_citations', 0)})\n")
                        f.write(f"- **开放获取**: {'✓' if paper.get('has_pdf') else '✗'}\n")
                        f.write(f"- **链接**: [{paper.get('url', 'N/A')}]({paper.get('url', '#')})\n\n")
                
                f.write("---\n\n")
        
        print(f"✓ Markdown 结果已保存到: {md_path}")
        return json_path, md_path


async def test_single_query(query: str, logger: TestLogger):
    """测试单个查询"""
    print(f"\n{'='*60}")
    print(f"测试查询: {query}")
    print(f"{'='*60}")
    
    try:
        # 1. 调用 LLM 解析意图
        print("⏳ 调用 LLM 解析意图...")
        
        # 为了获取原始响应，我们需要修改一下调用方式
        # 直接使用 llm_parser 中的 client 和逻辑
        from llm_parser import client, SYSTEM, _safe_json
        
        current_date = datetime.now().strftime("%Y-%m-%d")
        messages = [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"当前日期：{current_date}\n用户输入：{query}"},
        ]
        
        from config import OPENAI_MODEL
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            temperature=0.2,
        )
        llm_raw = (resp.choices[0].message.content or "").strip()
        print(f"✓ LLM 响应: {llm_raw[:100]}...")
        
        # 手动解析为 SearchIntent（避免重复调用）
        data = _safe_json(llm_raw)
        data.setdefault("must_have_pdf", False)
        data.setdefault("max_results", 10)
        data.setdefault("sort_by", "relevance")
        intent = SearchIntent(**data)
        print(f"✓ 解析完成: {intent.any_groups}")
        
        # 2. 调用 S2 API 搜索
        print("⏳ 调用 S2 API 搜索...")
        papers, batch, stats = await search_papers(intent)
        print(f"✓ 搜索完成: 找到 {len(papers)} 篇论文")
        
        # 3. 排序和截断
        print("⏳ 排序和截断...")
        papers_sorted = rank_papers(papers, mode=intent.sort_by)
        papers_final = papers_sorted[:intent.max_results]
        print(f"✓ 最终返回: {len(papers_final)} 篇论文")
        
        # 4. 记录结果
        logger.log_test_case(
            query=query,
            intent=intent,
            llm_raw_response=llm_raw,
            papers=papers,
            stats=stats,
            papers_final=papers_final,
        )
        
        # 5. 打印简要信息
        print(f"\n📊 统计:")
        print(f"  - 查询组合数: {stats.get('query_combinations')}")
        print(f"  - 总抓取条数: {stats.get('total_raw_fetched')}")
        print(f"  - 总去重后: {stats.get('total_raw_unique')}")
        print(f"  - 最终唯一: {stats.get('final_unique_count')}")
        print(f"  - 最终返回: {len(papers_final)}")
        
        if papers_final:
            print(f"\n📄 前3篇论文:")
            for i, p in enumerate(papers_final[:3], 1):
                print(f"  {i}. {p.title[:60]}...")
                print(f"     {p.year} | {p.journal} | 引用: {p.citations}")
        
    except Exception as e:
        print(f"❌ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()
        logger.log_test_case(
            query=query,
            intent=None,
            llm_raw_response="",
            papers=[],
            stats={},
            papers_final=[],
            error=str(e),
        )


async def main():
    """主函数：运行所有测试"""
    print("=" * 80)
    print(" 论文检索系统测试")
    print("=" * 80)
    print(f"\n共有 {len(TEST_QUERIES)} 个测试用例\n")
    
    # 检查环境变量
    if not os.getenv("OPENAI_API_KEY"):
        print("⚠️  警告: OPENAI_API_KEY 未设置")
    
    if not os.getenv("S2_API_KEY"):
        print("⚠️  警告: S2_API_KEY 未设置（将使用默认速率限制）")
    
    logger = TestLogger()
    
    # 运行所有测试
    for idx, query in enumerate(TEST_QUERIES, 1):
        print(f"\n[{idx}/{len(TEST_QUERIES)}] 开始测试...")
        await test_single_query(query, logger)
        
        # 避免过快请求
        if idx < len(TEST_QUERIES):
            print("\n⏸️  等待 2 秒...")
            await asyncio.sleep(2)
    
    # 保存结果
    print("\n" + "=" * 80)
    print(" 测试完成，保存结果...")
    print("=" * 80)
    
    json_path, md_path = logger.save_results()
    
    # 统计
    success = sum(1 for r in logger.results if not r.get('error'))
    failed = len(logger.results) - success
    
    print(f"\n📊 测试总结:")
    print(f"  - 总计: {len(logger.results)} 个")
    print(f"  - 成功: {success} 个")
    print(f"  - 失败: {failed} 个")
    
    print(f"\n✅ 完成！请查看以下文件:")
    print(f"  - JSON: {json_path}")
    print(f"  - Markdown: {md_path}")


if __name__ == "__main__":
    # 确保在项目根目录运行
    asyncio.run(main())

