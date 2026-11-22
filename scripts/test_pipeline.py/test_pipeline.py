#!/usr/bin/env python3
"""验证抓取与评审流水线的关键规则（黑名单、T3封顶、跨源去重、失效排黄）。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_and_score as engine


def run_tests():
    rules = engine.load_json(engine.CONFIG_PATH, {})

    # 1. 黑名单测试（AI 免费额度、VPS、拉人头必须直接判 C / 0 分）
    for bad_title in [
        "某大模型送 100 万免费 API Token 额度",
        "轻量应用服务器 VPS 首年 38 元特惠",
        "拼多多拉人头邀请好友砍一刀领 50 元"
    ]:
        res = engine.score_deal({"title": bad_title, "benefit": "免费领", "steps": "", "trust_tier": "T1"}, rules)
        assert res["tier"] == "C" and res["score"] == 0, f"Blacklist failed for: {bad_title}"

    # 2. T3 单源封顶测试（即使金额很大，单源 T3 绝不评 S 级）
    t3_big = engine.score_deal({
        "title": "某渠道立减 100 元全国通用红包",
        "benefit": "最高立减 100 元",
        "steps": "搜口令直接领",
        "trust_tier": "T3",
        "verified_sources": ["某单源线报"]
    }, rules)
    assert t3_big["tier"] != "S", f"T3 should never get S tier, got {t3_big}"
    assert t3_big["promo_warning"] is True, "Promo warning should trigger for 搜口令"

    # 3. 跨源聚类去重测试（相似标题合并且源数量累加）
    clustered = engine.cluster_and_dedupe([
        {"title": "上海静安文旅消费券酒店满800减150", "source_name": "线报站A", "source_url": "https://a.com", "trust_tier": "T3"},
        {"title": "上海静安文旅消费券酒店满800减150来了", "source_name": "解放日报", "source_url": "https://www.jfdaily.com/sgh/detail?id=4098551", "trust_tier": "T1"},
    ])
    assert len(clustered) == 1, f"Expected 1 cluster, got {len(clustered)}"
    assert clustered[0]["trust_tier"] == "T1", "Higher trust tier should win"
    assert len(clustered[0]["verified_sources"]) == 2, "Should track both sources"

    # 4. 失效排黄测试
    dead = engine.score_deal({"title": "某银行10元立减金（已黄无货）", "benefit": "立减10元", "steps": "", "trust_tier": "T2"}, rules)
    assert dead.get("expired_flag") is True and dead["tier"] == "C", "Expired deal should be flagged"

    print("ALL PIPELINE TESTS PASSED!")


if __name__ == "__main__":
    run_tests()
