#!/usr/bin/env python3
"""content_qa.py — 文章上線前的品質/合規閘（不可繞過的 verifier）。

吃一份 Markdown 草稿，檢查它能不能上站。回非零 = 不准發，照 blocker 改完再過。
這道閘擋的就是「會害你整站被 Google 降權」的薄內容與漏揭露。

純標準庫，複製即跑：
    python content_qa.py draft.md
    python content_qa.py draft.md --keyword "藍牙耳機 平價 推薦"

設計原則（見上層 README 第二節）：這些檢查是『地板』，是幫你守底線的代理指標，
不是要刷的分數。為了過閘而灌字數/塞關鍵字 = 對自己作弊，演算法會替你打真分數。
"""
import sys
import re
import argparse

MIN_CHARS = 800           # 中文字元數下限（薄內容是 Google 反垃圾的主要打擊對象）
MAX_AFFILIATE_LINKS = 12  # 分潤連結上限；太多 = 廣告農場訊號
KW_DENSITY_MAX = 0.03     # 目標關鍵字密度上限（>3% 視為堆砌）

DISCLOSURE_MARKERS = ["分潤", "聯盟", "affiliate", "回饋金", "佣金"]
DISCLOSURE_CONTEXT = ["揭露", "本文包含", "本文含", "透過", "可能獲得", "可能賺取"]

# 原創價值訊號：純 AI 八股文通常缺這些；至少要有 VALUE_MIN 種。
VALUE_MIN = 2
VALUE_SIGNALS = {
    "比較表": lambda t: t.count("|") >= 6 and t.count("---") >= 1,
    "優缺點": lambda t: ("優點" in t and "缺點" in t) or "優缺點" in t,
    "規格細節": lambda t: bool(re.search(r"\d+\s?(mm|cm|g|kg|mAh|小時|hr|W|GB|吋|元)", t)),
    "第一手經驗": lambda t: any(w in t for w in ["實測", "實際用", "我自己", "我的經驗", "用了", "親自"]),
    "FAQ": lambda t: "FAQ" in t or "常見問題" in t or t.count("？") >= 3,
    "適合誰": lambda t: "適合" in t and ("不適合" in t or "推薦給" in t),
}


def cjk_len(text: str) -> int:
    return len(re.findall(r"[一-鿿]", text))


def count_affiliate_links(text: str) -> int:
    # 指向 shopee 的實際 URL + 還沒換成連結的 {{affiliate_url:...}} 佔位符都算
    # （草稿階段連結多半還是佔位符，不一起算的話這道上限在主要流程等於沒檢查）
    links = re.findall(r"https?://[^\s\)\]]+", text)
    real = sum(1 for u in links if "shopee" in u.lower())
    placeholders = len(re.findall(r"\{\{affiliate_url", text))
    return real + placeholders


def has_disclosure(text: str) -> bool:
    # 同一段（兩個換行內）同時出現「分潤類字」與「揭露語境字」才算數
    for block in re.split(r"\n\s*\n", text):
        if any(m in block for m in DISCLOSURE_MARKERS) and any(c in block for c in DISCLOSURE_CONTEXT):
            return True
    return False


def keyword_density(text: str, keyword: str) -> float:
    total = cjk_len(text)
    if total == 0 or not keyword:
        return 0.0
    occ = text.count(keyword)
    return occ * cjk_len(keyword) / total


def check(text: str, keyword: str | None) -> list[str]:
    blockers = []

    n = cjk_len(text)
    if n < MIN_CHARS:
        blockers.append(f"內容過薄：中文字 {n} < {MIN_CHARS}（薄內容是 Google 反垃圾首要打擊對象）")

    if not has_disclosure(text):
        blockers.append("缺分潤揭露：找不到『本文包含分潤連結…』之類的揭露聲明（法律＋蝦皮條款要求）")

    n_links = count_affiliate_links(text)
    if n_links > MAX_AFFILIATE_LINKS:
        blockers.append(f"分潤連結過多：{n_links} > {MAX_AFFILIATE_LINKS}（廣告農場訊號）")

    signals = [name for name, fn in VALUE_SIGNALS.items() if fn(text)]
    if len(signals) < VALUE_MIN:
        blockers.append(
            f"原創價值訊號不足：只偵測到 {len(signals)} 種（{signals or '無'}），"
            f"至少要 {VALUE_MIN} 種。補比較表/優缺點/規格/實測心得/FAQ。")

    if keyword:
        # 多字關鍵字（如「藍牙耳機 平價 推薦」）在自然文句裡幾乎不會原樣連續出現，
        # 所以拆成詞、檢查核心詞（最長那個）有沒有出現、以及它的密度有沒有過高。
        tokens = keyword.split()
        primary = max(tokens, key=len) if tokens else keyword
        if text.count(primary) == 0:
            blockers.append(f"核心關鍵字『{primary}』完全沒出現（標題與首段至少要自然帶到）")
        d = keyword_density(text, primary)
        if d > KW_DENSITY_MAX:
            blockers.append(f"關鍵字堆砌：『{primary}』密度 {d:.1%} > {KW_DENSITY_MAX:.0%}（負分，自然寫就好）")

    return blockers


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="文章上線前品質/合規閘")
    ap.add_argument("file", help="Markdown 草稿路徑")
    ap.add_argument("--keyword", help="目標關鍵字（檢查密度與是否出現）")
    args = ap.parse_args(argv[1:])

    with open(args.file, encoding="utf-8") as f:
        text = f.read()

    blockers = check(text, args.keyword)
    if blockers:
        print(f"❌ QA 未通過（{len(blockers)} 個 blocker），不准發：")
        for b in blockers:
            print(f"   • {b}")
        print("\n改完再跑一次。記住：閘是地板不是天花板，別為了過閘作弊。")
        return 1
    print("✅ QA 通過。但這只是底線——別忘了補上只有真人有的第一手經驗（E-E-A-T）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
