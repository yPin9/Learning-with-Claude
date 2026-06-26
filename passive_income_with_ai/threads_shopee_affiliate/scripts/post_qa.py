#!/usr/bin/env python3
"""post_qa.py — Threads 貼文發佈前的閘（不可繞過的 verifier）。

檢查一篇貼文「能不能發」。以下是 Threads 創作者圈的操作共識（非 Meta 官方規格，當假設用），跟 SEO 不同：
  • 內文不放外部連結（一般觀察：放內文觸及較低，連結放第一則留言；本閘照此把關）
  • 要有逼互動的鉤子 / CTA（留言被認為是權重最高的互動）
  • 長度合理（太短撐不起停留、太長可能吃節奏）
  • 業配感不要太重（去商業化在 Threads 上通常表現較好）
如果你把「第一則留言」也存成檔，用 --comment 一起檢查：留言要有分潤連結 + 揭露。

純標準庫，複製即跑：
    python post_qa.py body.txt
    python post_qa.py body.txt --comment comment.txt
"""
import sys
import re
import argparse

LINK_RE = re.compile(r"(https?://\S+|shopee\.tw|s\.shopee\.tw|shp\.ee|portaly\.cc)", re.I)
AFFILIATE_RE = re.compile(r"(shopee\.tw|s\.shopee\.tw|shp\.ee|portaly\.cc|\{\{affiliate_url)", re.I)

DISCLOSURE_MARKERS = ["分潤", "聯盟", "回饋", "佣金"]
DISCLOSURE_CONTEXT = ["揭露", "透過", "可能", "我會", "賺取", "回饋金"]

CTA_MARKERS = ["留言", "+1", "想要", "扣 1", "扣1", "👇", "你會選", "你覺得", "底下", "放留言"]
SALESY_MARKERS = ["快買", "必買", "手刀", "現在下單", "錯過再等一年", "史上最低", "破盤", "限時搶", "馬上買"]

BODY_MIN, BODY_MAX = 30, 600   # 內文中文字數：MIN 是硬下限（過短直接擋）、MAX 是軟上限（只警告不擋）


def cjk_len(t: str) -> int:
    return len(re.findall(r"[一-鿿]", t))


def has_disclosure(text: str) -> bool:
    for block in re.split(r"\n\s*\n", text):
        if any(m in block for m in DISCLOSURE_MARKERS) and any(c in block for c in DISCLOSURE_CONTEXT):
            return True
    return False


def check_body(text: str) -> tuple[list[str], list[str]]:
    """回 (blockers, warnings)。blockers 會擋發佈；warnings 只提醒、不擋。"""
    blockers, warnings = [], []
    if LINK_RE.search(text):
        blockers.append("內文有外部連結——創作者圈普遍觀察帶外連的內文觸及較低；本閘要求連結只放第一則留言")
    # 鉤子/CTA：明確的逼留言詞，或「對讀者提問」（含『你』的問句）才算數；單純一個問號太鬆
    has_cta = any(m in text for m in CTA_MARKERS) or (("？" in text or "?" in text) and "你" in text)
    if not has_cta:
        blockers.append("缺鉤子/CTA——沒有逼留言的引導或對讀者提問，留言（被認為是最高權重互動）會很少")
    n = cjk_len(text)
    if n < BODY_MIN:
        blockers.append(f"內文過短：{n} 字 < {BODY_MIN}，撐不起停留時間")
    elif n > BODY_MAX:
        warnings.append(f"內文偏長：{n} 字 > {BODY_MAX}，Threads 上節奏可能拖（不擋，但建議精簡或拆系列）")
    hits = [m for m in SALESY_MARKERS if m in text]
    if hits:
        blockers.append(f"業配感過重（{ '、'.join(hits) }）——去商業化是 Threads 命門，太像廣告會被埋")
    return blockers, warnings


def check_comment(text: str) -> list[str]:
    blockers = []
    if not AFFILIATE_RE.search(text):
        blockers.append("第一則留言沒有分潤連結（或佔位）——導購的連結就靠這則，少了等於沒變現")
    if not has_disclosure(text):
        blockers.append("第一則留言缺分潤揭露——通常需要揭露；以當期法規與蝦皮條款為準")
    return blockers


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Threads 貼文發佈前的閘")
    ap.add_argument("body", help="內文檔")
    ap.add_argument("--comment", help="第一則留言檔（含分潤連結與揭露）")
    args = ap.parse_args(argv[1:])

    with open(args.body, encoding="utf-8") as f:
        body = f.read()
    body_blockers, warnings = check_body(body)
    blockers = [("內文", b) for b in body_blockers]

    if args.comment:
        with open(args.comment, encoding="utf-8") as f:
            comment = f.read()
        blockers += [("留言", b) for b in check_comment(comment)]
    else:
        print("（沒給 --comment，略過第一則留言的分潤連結與揭露檢查；正式發佈前一定要連留言一起過。）\n")

    for w in warnings:
        print(f"⚠️  提醒：{w}")
    if warnings:
        print()

    if blockers:
        print(f"❌ QA 未通過（{len(blockers)} 個 blocker），不准發：")
        for where, b in blockers:
            print(f"   • [{where}] {b}")
        print("\n改完再跑。記住：閘是地板不是天花板，別為了過閘硬塞鉤子或買假互動。")
        return 1
    print("✅ QA 通過。但這只是底線——挑一個變體、改成你自己的口吻、補真實心得再發。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
