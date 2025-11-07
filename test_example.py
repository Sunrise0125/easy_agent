#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简单示例：测试单个查询，快速验证功能
"""

import asyncio
import os
from llm_parser import parse_user_intent
from s2_client import search_papers
from ranking import rank_papers


async def simple_test():
    """简单测试示例"""
    
    # 检查环境变量
    if not os.getenv("OPENAI_API_KEY"):
        print("⚠️  警告: OPENAI_API_KEY 未设置")
        return
    
    # 测试查询
    query = "找一些2023年关于Transformer的论文，发表在NeurIPS"
    
    print("=" * 60)
    print(f"测试查询: {query}")
    print("=" * 60)
    
    try:
        # 1. LLM 解析意图
        print("\n⏳ 步骤1: LLM 解析用户意图...")
        intent = await parse_user_intent(query)
        print(f"✓ 解析完成")
        print(f"  - 关键词组: {intent.any_groups}")
        print(f"  - 目标会议: {intent.venues}")
        print(f"  - 日期范围: {intent.date_start} ~ {intent.date_end}")
        print(f"  - 排序方式: {intent.sort_by}")
        print(f"  - 最大结果: {intent.max_results}")
        
        # 2. S2 API 搜索
        print("\n⏳ 步骤2: 调用 Semantic Scholar API 搜索...")
        papers, batch, stats = await search_papers(intent)
        print(f"✓ 搜索完成")
        print(f"  - 查询组合数: {stats.get('query_combinations')}")
        print(f"  - 总抓取条数: {stats.get('total_raw_fetched')}")
        print(f"  - 总去重后: {stats.get('total_raw_unique')}")
        print(f"  - 最终唯一: {stats.get('final_unique_count')}")
        queries = stats.get('queries', [])
        if queries:
            print(f"  - 查询组合:")
            for i, q in enumerate(queries, 1):
                print(f"    {i}. {q}")
        
        # 3. 排序和截断
        print("\n⏳ 步骤3: 排序和截断...")
        papers_sorted = rank_papers(papers, mode=intent.sort_by)
        papers_final = papers_sorted[:intent.max_results]
        print(f"✓ 完成，返回 {len(papers_final)} 篇论文")
        
        # 4. 显示结果
        if papers_final:
            print("\n" + "=" * 60)
            print("📄 查询结果:")
            print("=" * 60)
            
            for i, paper in enumerate(papers_final, 1):
                print(f"\n[{i}] {paper.title}")
                print(f"    作者: {', '.join(paper.authors[:3])}")
                if len(paper.authors) > 3:
                    print(f"          等 {len(paper.authors)} 人")
                print(f"    年份: {paper.year}")
                print(f"    会议/期刊: {paper.journal}")
                print(f"    引用数: {paper.citations} (影响力: {paper.influential_citations})")
                print(f"    开放获取: {'✓' if paper.open_access else '✗'}")
                print(f"    链接: {paper.url}")
        else:
            print("\n未找到符合条件的论文")
        
        print("\n" + "=" * 60)
        print("✅ 测试完成！")
        print("=" * 60)
        
    except Exception as e:
        print(f"\n❌ 测试失败: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(simple_test())

