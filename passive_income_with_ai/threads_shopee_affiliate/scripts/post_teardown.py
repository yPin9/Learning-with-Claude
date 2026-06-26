#!/usr/bin/env python3
"""post_teardown.py — 拆解一篇 Threads 貼文：它為什麼（可能）有流量？

用法 A 的核心工具。把你看到的一篇（疑似蝦皮分潤）貼文內文貼進來，它會偵測：
  1. 鉤子類型（問句 / 痛點 / 清單 / 對立 / 好奇缺口 / 留言 CTA）
  2. 連結位置檢查（連結出現在「內文」是扣分——應該放第一則留言）
  3. 互動訊號啟發式分數（打中越多「一般認為演算法偏好」的訊號，越可能被推）
  4.（若你提供觀察到的讚/留言/轉發數）留言/讚比——這是「為什麼有流量」最可量化的線索

⚠️ 誠實聲明：這是**啟發式**，根據貼文打中哪些「創作者圈歸納（非官方）的演算法訊號」來推估，**不是 Threads 後台真值**。
Threads 沒有開放 API、爬它違反 ToS，所以真實觸及只能你自己看、或把你觀察到的數字餵進 --likes 等。

純標準庫，複製即跑：
    python post_teardown.py            # 跑內建示範
    python post_teardown.py post.txt --likes 1200 --comments 340 --reposts 60
"""
import sys
import re
import argparse

LINK_RE = re.compile(
    r"(https?://\S+|shopee\.tw|s\.shopee\.tw|shp\.ee|portaly\.cc"
    r"|lihi\d?\.cc|reurl\.cc|pse\.is|piee\.\S+)", re.I)  # 含常見短連結包裝

# 鉤子偵測：每一類對應演算法「逼互動 / 勾停留」的手法
HOOKS = {
    "問句鉤子": lambda t: t.count("？") + t.count("?") >= 1,
    "留言 CTA": lambda t: any(k in t for k in ["留言", "+1", "想要", "扣 1", "扣1", "底下", "👇", "放留言"]),
    "清單體": lambda t: bool(re.search(r"[3-9]\s*(個|種|招|款|點|個理由)", t)) or bool(re.search(r"^\s*[1-9１-９]\s*[.、]", t, re.M)),
    "對立/選邊": lambda t: any(k in t for k in ["還是", "vs", "哪個", "你會選", "別再", "其實不"]),
    "好奇缺口": lambda t: any(k in t for k in ["沒人告訴你", "其實", "原來", "才發現", "我才知道", "真相", "千萬別"]),
    "痛點/踩雷": lambda t: any(k in t for k in ["後悔", "踩雷", "地雷", "別買", "雷", "翻車", "浪費錢"]),
    "個人故事": lambda t: any(k in t for k in ["我自己", "我用了", "我前陣子", "親身", "實測", "心得"]),
}


def cjk_len(t: str) -> int:
    return len(re.findall(r"[一-鿿]", t))


def detect_hooks(text: str) -> list[str]:
    return [name for name, fn in HOOKS.items() if fn(text)]


def link_in_body(text: str) -> bool:
    return bool(LINK_RE.search(text))


def signal_score(text: str, hooks: list[str]) -> tuple[int, list[str]]:
    """0–100 的啟發式『演算法友善度』。回 (分數, 判讀說明)。"""
    notes = []
    score = 0

    if any(h in hooks for h in ("問句鉤子", "留言 CTA")):
        score += 30
        notes.append("有逼留言的鉤子（留言被創作者圈認為是權重最高的互動）")
    else:
        notes.append("沒有明顯逼留言的鉤子——一般認為留言>轉發>讚，缺留言鉤子較吃虧")

    other = [h for h in hooks if h not in ("問句鉤子", "留言 CTA")]
    score += min(len(other), 3) * 12
    if other:
        notes.append(f"用了停留/好奇類鉤子：{'、'.join(other)}")

    n = cjk_len(text)
    if 40 <= n <= 500:
        score += 18
        notes.append(f"長度 {n} 字落在好區間（夠停留、又不冗長）")
    elif n < 40:
        notes.append(f"長度僅 {n} 字，偏短——不易撐停留時間")
    else:
        notes.append(f"長度 {n} 字偏長，Threads 上要注意節奏")

    if link_in_body(text):
        score -= 30
        notes.append("⚠️ 內文出現連結——一般觀察帶外連的內文觸及較低，建議連結放第一則留言（折扣碼/情報型內容例外）")

    return max(0, min(100, score)), notes


def metric_diagnosis(likes, comments, reposts) -> list[str]:
    out = []
    if likes is None or likes <= 0:
        out.append("（沒給讚數或為 0，跳過互動比診斷）")
        return out
    c_ratio = (comments or 0) / likes
    out.append(f"留言/讚比 = {c_ratio:.2f}")
    if c_ratio >= 0.15:
        out.append("→ 高留言比：一般認為是演算法偏好的訊號，這類較容易被推。值得拆解它的留言鉤子怎麼下的。")
    elif c_ratio >= 0.05:
        out.append("→ 中等留言比：有互動但還有空間，鉤子可以更逼留言。")
    else:
        out.append("→ 留言偏少：可能靠讚數但觸及上限受限；它的紅可能來自其他因素（內容本身、轉發）。")
    if reposts:
        r_ratio = reposts / likes
        out.append(f"轉發/讚比 = {r_ratio:.2f}（轉發是高權重互動、幫助破圈）")
    return out


SAMPLE = (
    "我以前買藍牙耳機只看牌子，結果連續踩雷兩次才發現重點根本不是牌子。\n"
    "通勤族最該看的其實是「通話收音」跟「單耳續航」這兩個，不是降噪。\n"
    "1500 有找我實際戴過覺得 OK 的有三款，差別蠻大的。\n"
    "你最在意的是哪一個？通話清楚、還是續航長？留言跟我說，我把對應的款式整理放留言👇"
)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="拆解一篇 Threads 貼文為什麼可能有流量")
    ap.add_argument("file", nargs="?", help="貼文內文檔（省略則跑內建示範）")
    ap.add_argument("--likes", type=int, help="你觀察到的讚數")
    ap.add_argument("--comments", type=int, help="你觀察到的留言數")
    ap.add_argument("--reposts", type=int, help="你觀察到的轉發數")
    args = ap.parse_args(argv[1:])

    if args.file:
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
        src = args.file
    else:
        text = SAMPLE
        src = "（內建示範貼文；帶一個檔案路徑可換成你蒐集的）"

    hooks = detect_hooks(text)
    score, notes = signal_score(text, hooks)

    print(f"拆解來源：{src}\n")
    print(f"偵測到的鉤子：{'、'.join(hooks) if hooks else '（無）'}")
    print(f"連結位置：{'⚠️ 內文有連結（應移到第一則留言）' if link_in_body(text) else '內文無連結（正確；連結應在留言）'}")
    print(f"\n演算法友善度（啟發式）：{score}/100")
    for n in notes:
        print(f"  • {n}")

    print("\n互動數據判讀：")
    for d in metric_diagnosis(args.likes, args.comments, args.reposts):
        print(f"  • {d}")

    print("\n提醒：分數是啟發式、不是 Threads 真值。要學的是它打中的『鉤子＋形態＋選品』結構，照抄結構別抄字。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
