#!/usr/bin/env python3
"""
国内每日大小羊毛自动化抓取、去重、交叉验证与纯规则评审引擎（零第三方依赖）
- 仅使用 Python 标准库，确保 GitHub Actions 秒级运行且无依赖安装失败风险
- 内置 ETag/Last-Modified 条件请求缓存、随机抖动、单源熔断退避（降低风控风险）
- 支持 T1/T2/T3 信源可信度分级、实体指纹跨源聚类、自动“排黄”归档与 0-100 透明打分
"""

import argparse
import datetime as dt
import difflib
import hashlib
import html
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "rules.json"
DATA_DIR = ROOT / "docs" / "data"
ACTIVE_PATH = DATA_DIR / "active.json"
ARCHIVE_PATH = DATA_DIR / "archive.json"
STATUS_PATH = DATA_DIR / "status.json"
CACHE_PATH = DATA_DIR / "source_cache.json"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1",
]

OFFICIAL_DOMAINS = (
    ".gov.cn", "jfdaily.com", "meituan.com", "jd.com", "taobao.com",
    "icbc.com.cn", "ccb.com", "abchina.com", "boc.cn", "cmbchina.com",
    "bankcomm.com", "psbc.com", "spdb.com.cn", "cib.com.cn", "alipay.com",
    "unionpay.com", "95516.com"
)


def now_cst() -> dt.datetime:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))


def today_str() -> str:
    return now_cst().strftime("%Y-%m-%d")


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def strip_tags(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def http_get_with_cache(url: str, cache_entry: dict, timeout: int = 8):
    """发送带 ETag / Last-Modified 条件头的 GET 请求，304 直接复用缓存降低风控。"""
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/rss+xml, application/xml, text/html;q=0.9, */*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if cache_entry.get("etag"):
        headers["If-None-Match"] = cache_entry["etag"]
    if cache_entry.get("last_modified"):
        headers["If-Modified-Since"] = cache_entry["last_modified"]

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return {
                "status": resp.status,
                "body": body,
                "etag": resp.headers.get("ETag", ""),
                "last_modified": resp.headers.get("Last-Modified", ""),
            }
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return {"status": 304, "body": ""}
        raise


def parse_rss_items(xml_text: str, source: dict) -> list:
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items

    for node in root.findall(".//item")[:25]:
        title = strip_tags(node.findtext("title", ""))
        link = (node.findtext("link") or "").strip()
        desc = strip_tags(node.findtext("description", ""))
        if not title:
            continue
        items.append({
            "title": title[:120],
            "benefit": extract_benefit_snippet(title, desc),
            "steps": (desc or title)[:260],
            "source_url": link,
            "source_name": source["name"],
            "source_id": source["id"],
            "trust_tier": infer_trust_tier(link, source["trust_tier"]),
            "category": infer_category(title + " " + desc, source["default_category"]),
            "deadline": infer_deadline(title + " " + desc),
            "added_date": today_str(),
        })
    return items


def parse_html_xianbao(html_text: str, source: dict) -> list:
    """轻量解析公开线报列表页的标题与链接。"""
    items = []
    pattern = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
    seen = set()
    for href, raw_title in pattern.findall(html_text):
        title = strip_tags(raw_title)
        if len(title) < 6 or len(title) > 90:
            continue
        if not any(k in title for k in ("立减", "满", "券", "红包", "免费", "领", "元", "折", "补贴", "会员", "返现", "豆")):
            continue
        if title in seen:
            continue
        seen.add(title)
        full_url = href if href.startswith("http") else source["url"]
        items.append({
            "title": title,
            "benefit": extract_benefit_snippet(title, ""),
            "steps": f"来源线报速览：{title}（实时线报名额变动快，请点击来源核实入口）",
            "source_url": full_url,
            "source_name": source["name"],
            "source_id": source["id"],
            "trust_tier": infer_trust_tier(full_url, source["trust_tier"]),
            "category": infer_category(title, source["default_category"]),
            "deadline": infer_deadline(title, default_days=2),
            "added_date": today_str(),
        })
        if len(items) >= 20:
            break
    return items


def infer_trust_tier(url: str, default_tier: str) -> str:
    lower = (url or "").lower()
    if any(dom in lower for dom in OFFICIAL_DOMAINS):
        return "T1"
    return default_tier


def infer_category(text: str, default_cat: str) -> str:
    if any(k in text for k in ("国补", "以旧换新", "京东", "淘宝", "天猫", "拼多多", "会员年卡", "联合会员", "大促")):
        if not any(k in text for k in ("外卖", "奶茶", "咖啡")):
            return "电商补贴"
    if any(k in text for k in ("外卖", "奶茶", "咖啡", "瑞幸", "库迪", "蜜雪", "酒店", "文旅", "消费券", "话费", "电影", "百岁山", "电信", "移动", "联通")):
        return "本地生活"
    if any(k in text for k in ("银行", "立减金", "信用卡", "云闪付", "还款", "刷卡金", "支付宝", "微信支付", "i豆")):
        return "银行支付"
    return default_cat


def extract_benefit_snippet(title: str, desc: str) -> str:
    combined = f"{title} {desc}"
    m = re.search(r"(满\s*\d+(?:\.\d+)?\s*减\s*\d+(?:\.\d+)?|最高(?:立减|补贴|省)\s*\d+(?:\.\d+)?\s*元|立减\s*\d+(?:\.\d+)?\s*元|0元领[^，。；\s]{2,14}|1分钱[^，。；\s]{0,12})", combined)
    if m:
        return m.group(1).strip()
    return title[:50]


def infer_deadline(text: str, default_days: int = 7) -> str:
    year = now_cst().year
    m = re.search(r"(202\d)[年/-](\d{1,2})[月/-](\d{1,2})", text)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m2 = re.search(r"(?:截止|至|到)\s*(\d{1,2})[月./-](\d{1,2})", text)
    if m2:
        month, day = int(m2.group(1)), int(m2.group(2))
        y = year + 1 if month < now_cst().month - 1 else year
        return f"{y:04d}-{month:02d}-{day:02d}"
    return (now_cst() + dt.timedelta(days=default_days)).strftime("%Y-%m-%d")


def is_blacklisted(text: str, rules: dict) -> bool:
    lower = text.lower()
    for kw in rules["scoring"]["blacklist_keywords"]:
        if kw.lower() in lower:
            return True
    return False


def is_expired_text(text: str, rules: dict) -> bool:
    for kw in rules["scoring"]["expired_keywords"]:
        if kw in text:
            return True
    return False


def parse_value_metrics(text: str):
    """正则解析金额收益与折扣率，避免“满5000减5”虚高评分。"""
    best_amount = 0.0
    best_ratio = 0.0

    # 满 X 减 Y / 满 X 抵 Y
    for m in re.finditer(r"满\s*(\d+(?:\.\d+)?)\s*(?:元)?\s*[减抵]\s*(\d+(?:\.\d+)?)", text):
        spend = float(m.group(1))
        save = float(m.group(2))
        if spend > 0 and save > 0:
            ratio = save / spend
            if save > best_amount:
                best_amount = save
            if ratio > best_ratio:
                best_ratio = ratio

    # 补贴 / 立减 / 返现 / 红包 Y 元（仅在未命中满减或金额更大时补充，不覆盖满减真实折扣率）
    for m in re.finditer(r"(?:最高)?(?:立减|补贴|直减|返现|红包|省|抵扣)\s*(\d+(?:\.\d+)?)\s*(?:[-–~至]\s*(\d+(?:\.\d+)?))?\s*元", text):
        v = float(m.group(2) or m.group(1))
        if v > best_amount:
            best_amount = v
            if best_ratio == 0:
                best_ratio = 0.25

    # 0元 / 免费领 / 1分钱
    if re.search(r"(?<!\d)0\s*元|免费领|(?<!\d)1\s*分钱|0\.01\s*元", text):
        best_amount = max(best_amount, 12.0)
        best_ratio = max(best_ratio, 0.9)

    # 积分/i豆粗略折算（1000 i豆 ≈ 10元）
    m_dou = re.search(r"(\d{3,5})\s*(?:\+?\s*)?[iI]?\s*豆", text)
    if m_dou:
        dou_val = float(m_dou.group(1)) / 100.0
        best_amount = max(best_amount, dou_val)
        best_ratio = max(best_ratio, 0.8)

    # 买一得N联合年卡
    if "买一得" in text or "联合年卡" in text:
        best_amount = max(best_amount, 60.0)
        best_ratio = max(best_ratio, 0.4)

    return best_amount, best_ratio


def make_fingerprint(title: str) -> str:
    cleaned = re.sub(r"[^\u4e00-\u9fa5a-zA-Z0-9]", "", title.lower())
    return cleaned[:40]


def cluster_and_dedupe(items: list) -> list:
    """跨源聚类去重：相似活动合并并累计独立信源数量（交叉印证）。"""
    clusters = []
    for item in items:
        fp = make_fingerprint(item["title"])
        matched = None
        for existing in clusters:
            ratio = difflib.SequenceMatcher(None, fp, existing["_fp"]).ratio()
            if ratio >= 0.62:
                matched = existing
                break
        if matched:
            src_set = set(matched.get("verified_sources", [matched["source_name"]]))
            src_set.add(item["source_name"])
            matched["verified_sources"] = sorted(src_set)
            # 更高可信度覆盖低可信度
            rank = {"T1": 0, "T2": 1, "T3": 2}
            if rank.get(item["trust_tier"], 9) < rank.get(matched["trust_tier"], 9):
                matched["trust_tier"] = item["trust_tier"]
                matched["source_url"] = item["source_url"]
                matched["source_name"] = item["source_name"]
        else:
            copy_item = dict(item)
            copy_item["_fp"] = fp
            copy_item["verified_sources"] = [item["source_name"]]
            clusters.append(copy_item)

    for c in clusters:
        c.pop("_fp", None)
    return clusters


def score_deal(deal: dict, rules: dict) -> dict:
    """0-100 透明打分并映射 S/A/B/C 等级，附带可读评分理由。"""
    text = f"{deal.get('title', '')} {deal.get('benefit', '')} {deal.get('steps', '')}"
    reasons = []

    if is_blacklisted(text, rules):
        return {**deal, "score": 0, "tier": "C", "score_reasons": ["命中黑名单过滤"]}
    if is_expired_text(text, rules):
        return {**deal, "score": 0, "tier": "C", "score_reasons": ["原文已标注失效/已黄"], "expired_flag": True}

    # 1. 收益分 (0-40)
    est_val, ratio = parse_value_metrics(text)
    if est_val >= 50:
        val_score = 38
        reasons.append(f"大额收益(约{est_val:.0f}元)+38")
    elif est_val >= 30:
        val_score = 32
        reasons.append(f"较高收益(约{est_val:.0f}元)+32")
    elif est_val >= 10:
        val_score = 22
        reasons.append(f"中等收益(约{est_val:.0f}元)+22")
    elif est_val >= 2:
        val_score = 12
        reasons.append(f"小额顺手(约{est_val:.0f}元)+12")
    else:
        val_score = 5
        reasons.append("收益偏低/未明示+5")

    # 低折扣率惩罚（例如满5000减50折扣率仅1%，防止虚高进S级）
    if 0 < ratio < 0.02 and est_val < 100:
        val_score = max(10, val_score - 12)
        reasons.append("满额门槛偏高-12")

    # 2. 信源可信度与交叉印证 (0-30)
    trust = deal.get("trust_tier", "T3")
    src_count = len(deal.get("verified_sources", [1]))
    if trust == "T1":
        trust_score = 28
        reasons.append("T1官方直出+28")
    elif trust == "T2" or src_count >= 2:
        trust_score = min(30, 22 + (src_count - 1) * 4)
        deal["trust_tier"] = "T2" if trust != "T1" else "T1"
        reasons.append(f"T2社区/多源印证({src_count}源)+{trust_score}")
    else:
        trust_score = 12
        reasons.append("T3单源线报+12")

    # 疑似推广口令警示
    promo_warning = False
    if any(k in text for k in rules["scoring"]["promo_code_hints"]) and trust != "T1":
        promo_warning = True
        trust_score = max(6, trust_score - 6)
        reasons.append("含口令待实测-6")

    # 3. 门槛、普适性与个人画像加权 (0-30)
    profile = rules.get("user_profile", {})
    my_banks = profile.get("my_banks", [])
    my_cities = profile.get("my_cities", [])

    fit_score = 16
    matched_tags = []
    bank_aliases = {
        "工商银行": ("工商银行", "工行"),
        "建设银行": ("建设银行", "建行"),
        "农业银行": ("农业银行", "农行"),
        "中国银行": ("中国银行", "中行"),
        "招商银行": ("招商银行", "招行"),
        "交通银行": ("交通银行", "交行"),
        "邮储银行": ("邮储银行", "邮储"),
        "兴业银行": ("兴业银行", "兴业"),
        "浦发银行": ("浦发银行", "浦发"),
        "中信银行": ("中信银行", "中信"),
        "民生银行": ("民生银行", "民生"),
        "光大银行": ("光大银行", "光大"),
    }
    for b in my_banks:
        aliases = bank_aliases.get(b, (b,))
        if any(a in text for a in aliases):
            fit_score += 10
            matched_tags.append(b)
            reasons.append(f"命中常用卡({aliases[-1]})+10")
            break
    for c in my_cities:
        if c != "全国" and c in text:
            fit_score += 8
            matched_tags.append(c)
            reasons.append(f"命中关注城市({c})+8")
            break
    if any(k in text for k in ("国补", "全国", "通用", "新老用户")):
        fit_score += 6
        reasons.append("全国普适+6")
    if any(k in text for k in ("限新户", "仅限", "需持有")):
        fit_score -= 6
        reasons.append("有前置门槛-6")
    fit_score = max(0, min(30, fit_score))

    total = max(0, min(100, val_score + trust_score + fit_score))
    th = rules["scoring"]["tier_thresholds"]
    if total >= th["S"] and deal.get("trust_tier") != "T3":
        tier = "S"
    elif total >= th["A"]:
        tier = "A"
    elif total >= th["B"]:
        tier = "B"
    else:
        tier = "C"

    # 硬规则：T3 单源绝不评 S 级
    if total >= th["S"] and deal.get("trust_tier") == "T3":
        tier = "A"
        reasons.append("单源封顶A级")

    deal_id = deal.get("id") or ("deal-" + hashlib.md5(make_fingerprint(deal["title"]).encode()).hexdigest()[:10])
    return {
        "id": deal_id,
        "tier": tier,
        "score": total,
        "score_reasons": reasons,
        "trust_tier": deal.get("trust_tier", "T3"),
        "verified_sources": deal.get("verified_sources", [deal.get("source_name", "公开来源")]),
        "promo_warning": promo_warning,
        "matched_tags": matched_tags,
        "category": deal.get("category", "银行支付"),
        "title": deal["title"],
        "benefit": deal.get("benefit", ""),
        "steps": deal.get("steps", ""),
        "added_date": deal.get("added_date", today_str()),
        "deadline": deal.get("deadline", ""),
        "source_name": deal.get("source_name", "查看来源"),
        "source_url": deal.get("source_url", ""),
    }


def fetch_all_sources(rules: dict, cache: dict, no_sleep: bool = False):
    """拉取全部启用数据源，带随机抖动、条件缓存与熔断保护。"""
    fetched_items = []
    source_statuses = []
    cb_max = rules["scoring"].get("circuit_breaker_max_failures", 3)
    cb_hours = rules["scoring"].get("circuit_breaker_cooldown_hours", 24)
    mirrors = list(rules.get("rsshub_mirrors", []))
    env_mirror = os.environ.get("RSSHUB_BASE_URL", "").strip().rstrip("/")
    if env_mirror:
        mirrors.insert(0, env_mirror)

    now_ts = time.time()
    for src in rules.get("sources", []):
        if not src.get("enabled", True):
            continue
        sid = src["id"]
        c_entry = cache.setdefault(sid, {"failures": 0, "cooldown_until": 0, "etag": "", "last_modified": ""})

        # 熔断冷却检查
        if c_entry.get("cooldown_until", 0) > now_ts:
            source_statuses.append({
                "id": sid,
                "name": src["name"],
                "status": "cooldown",
                "message": "连续失败已触发冷却保护，稍后自动恢复",
                "count": 0,
            })
            continue

        if not no_sleep:
            time.sleep(random.uniform(2.0, 4.5))

        urls_to_try = []
        if src["type"] == "rsshub":
            urls_to_try = [m.rstrip("/") + src["route"] for m in mirrors]
        else:
            urls_to_try = [src["url"]]

        success = False
        last_err = ""
        for u in urls_to_try:
            try:
                resp = http_get_with_cache(u, c_entry, timeout=8)
                if resp["status"] == 304:
                    success = True
                    c_entry["failures"] = 0
                    source_statuses.append({
                        "id": sid,
                        "name": src["name"],
                        "status": "cached",
                        "message": "304 无新变动（复用缓存）",
                        "count": 0,
                    })
                    break
                body = resp["body"]
                parsed = parse_rss_items(body, src) if src["type"] == "rsshub" else parse_html_xianbao(body, src)
                fetched_items.extend(parsed)
                c_entry["failures"] = 0
                c_entry["etag"] = resp.get("etag", "")
                c_entry["last_modified"] = resp.get("last_modified", "")
                success = True
                source_statuses.append({
                    "id": sid,
                    "name": src["name"],
                    "status": "ok",
                    "message": f"抓取成功（{len(parsed)} 条原始）",
                    "count": len(parsed),
                })
                break
            except Exception as e:
                last_err = str(e)[:120]

        if not success:
            c_entry["failures"] = c_entry.get("failures", 0) + 1
            if c_entry["failures"] >= cb_max:
                c_entry["cooldown_until"] = now_ts + cb_hours * 3600
            source_statuses.append({
                "id": sid,
                "name": src["name"],
                "status": "error",
                "message": f"抓取失败({c_entry['failures']}/{cb_max}): {last_err}",
                "count": 0,
            })

    return fetched_items, source_statuses


def reconcile_and_save(new_raw_items: list, source_statuses: list, rules: dict):
    """合并既有有效活动与新抓取活动，自动归档过期项并输出 JSON。"""
    existing_active = load_json(ACTIVE_PATH, {"deals": []}).get("deals", [])
    existing_archive = load_json(ARCHIVE_PATH, {"deals": []}).get("deals", [])

    # 合并去重并重新评审
    combined = cluster_and_dedupe(existing_active + new_raw_items)
    today = today_str()
    retention_cutoff = (now_cst() - dt.timedelta(days=rules["scoring"].get("archive_retention_days", 30))).strftime("%Y-%m-%d")

    next_active = []
    newly_archived = []
    existing_ids = {d.get("id") for d in existing_active}
    new_s_deals = []

    for item in combined:
        scored = score_deal(item, rules)
        if scored.get("expired_flag") or (scored.get("deadline") and scored["deadline"] < today):
            scored["archived_date"] = today
            newly_archived.append(scored)
            continue
        if scored["tier"] == "C":
            continue
        next_active.append(scored)
        if scored["tier"] == "S" and scored["id"] not in existing_ids:
            new_s_deals.append(scored)

    tier_order = {"S": 0, "A": 1, "B": 2}
    next_active.sort(key=lambda x: (tier_order.get(x["tier"], 9), -x["score"], x.get("deadline") or "9999-12-31"))

    # 归档去重并保留近 30 天
    arch_map = {d["id"]: d for d in existing_archive if d.get("archived_date", today) >= retention_cutoff}
    for d in newly_archived:
        arch_map[d["id"]] = d
    next_archive = sorted(arch_map.values(), key=lambda x: x.get("archived_date", ""), reverse=True)

    updated_at = now_cst().strftime("%Y-%m-%d %H:%M CST")
    save_json(ACTIVE_PATH, {"updated_at": updated_at, "count": len(next_active), "deals": next_active})
    save_json(ARCHIVE_PATH, {"updated_at": updated_at, "count": len(next_archive), "deals": next_archive})
    save_json(STATUS_PATH, {"updated_at": updated_at, "sources": source_statuses, "new_s_count": len(new_s_deals)})

    return next_active, next_archive, new_s_deals


def main():
    parser = argparse.ArgumentParser(description="国内羊毛看板抓取与评审引擎")
    parser.add_argument("--seed-file", help="从本地 JSON 文件导入测试/种子数据（不发起网络请求）")
    parser.add_argument("--no-jitter", action="store_true", help="跳过启动随机抖动延迟")
    args = parser.parse_args()

    rules = load_json(CONFIG_PATH, {})
    cache = load_json(CACHE_PATH, {})

    if args.seed_file:
        raw_items = load_json(Path(args.seed_file), [])
        statuses = [{"id": "seed", "name": "初始核验数据", "status": "ok", "message": f"导入 {len(raw_items)} 条", "count": len(raw_items)}]
    else:
        if not args.no_jitter:
            time.sleep(random.uniform(15, 75))
        raw_items, statuses = fetch_all_sources(rules, cache, no_sleep=args.no_jitter)
        save_json(CACHE_PATH, cache)

    active, archive, new_s = reconcile_and_save(raw_items, statuses, rules)
    print(f"[OK] Active: {len(active)}, Archive: {len(archive)}, New S-tier: {len(new_s)}")


if __name__ == "__main__":
    main()
