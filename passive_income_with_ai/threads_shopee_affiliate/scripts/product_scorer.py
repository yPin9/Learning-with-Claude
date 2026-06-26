#!/usr/bin/env python3
"""product_scorer.py — 幫 Threads 蝦皮分潤「該推哪個商品」打分排序。

社群導購選品最直接影響收入。輸入一份候選商品 CSV，依
「分潤率 × 需求/熱度 × 可內容化 × 競爭難度」打分，並用硬門檻剔除不值得推的：
幾乎沒分潤、做不出內容/沒話題、需求太冷。硬門檻優先於總分。

純標準庫，複製即跑：
    python product_scorer.py            # 跑內建示範（等同 --demo）
    python product_scorer.py items.csv  # 跑你的 CSV

CSV 欄位（標頭必須一致，分數 1–5）：
    product,commission,demand,content_ability,low_competition
      commission       : 分潤率高低（高=分潤% 高或客單高 → 單筆賺得多）
      demand           : 需求/熱度（多少人想買、有沒有搭到話題/檔期）
      content_ability  : 可內容化（能不能拍開箱、有沒有痛點/話題、好不好說故事）
      low_competition  : 競爭低（5=這題還沒被洗爛、你切得進去；1=紅海）

⚠️ 這是相對排序工具，不是真理；分潤率以分潤後台為準，別憑感覺填高。
"""
import sys
import csv
from dataclasses import dataclass

# 社群導購裡「做不做得出好內容」與「有沒有人想要」最關鍵，所以權重最高。
WEIGHTS = {"content_ability": 30, "commission": 30, "demand": 25, "low_competition": 15}


@dataclass
class Product:
    product: str
    commission: int
    demand: int
    content_ability: int
    low_competition: int

    def __post_init__(self):
        for f in ("commission", "demand", "content_ability", "low_competition"):
            v = getattr(self, f)
            if type(v) is not int or not (1 <= v <= 5):
                raise ValueError(f"{f} 必須是 1–5 的整數，收到 {v!r}")

    def score(self) -> float:
        return round(sum(getattr(self, k) / 5 * w for k, w in WEIGHTS.items()), 1)

    def gates(self) -> list[str]:
        g = []
        if self.commission <= 1:
            g.append("幾乎沒分潤（私人賣場/排除類別？換有分潤的）")
        if self.content_ability <= 2:
            g.append("做不出內容/沒話題（社群導購的命門，先跳過）")
        if self.demand <= 1:
            g.append("需求太冷（沒人想買，導了也不轉換）")
        return g


SAMPLE = [
    Product("平價降噪藍牙耳機", 3, 5, 5, 3),
    Product("人體工學辦公椅", 4, 4, 4, 3),
    Product("某冷門收藏品", 5, 1, 4, 5),       # 分潤高但沒人要 → 需求門檻
    Product("大廠旗艦手機", 1, 5, 4, 1),        # 大家都推、分潤低 → 分潤門檻 + 紅海
    Product("質感居家收納盒", 3, 4, 5, 4),
    Product("無聊的耗材螺絲", 2, 3, 1, 4),       # 做不出內容 → 內容門檻
]


def load_csv(path: str) -> list[Product]:
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append(Product(
                product=row["product"].strip(),
                commission=int(row["commission"]),
                demand=int(row["demand"]),
                content_ability=int(row["content_ability"]),
                low_competition=int(row["low_competition"]),
            ))
    return out


def main(argv: list[str]) -> int:
    if len(argv) > 1 and argv[1] != "--demo":
        items, src = load_csv(argv[1]), argv[1]
    else:
        items, src = SAMPLE, "（內建示範資料；帶一個 CSV 路徑可換成你的）"

    ranked = sorted(items, key=lambda p: p.score(), reverse=True)
    print(f"選品打分　來源：{src}\n")
    print(f"{'分數':>5}  {'門檻':<4} 商品")
    print("-" * 56)
    recommended = []
    for p in ranked:
        gates = p.gates()
        mark = "⛔" if gates else "✅"
        print(f"{p.score():>5}  {mark:<4} {p.product}")
        for g in gates:
            print(f"{'':>7}   └ {g}")
        if not gates and p.score() >= 60:
            recommended.append(p)

    print("\n建議優先推（無硬門檻、且分數 ≥ 60）：")
    if recommended:
        for p in recommended:
            print(f"  • {p.product}（{p.score()} 分）")
    else:
        print("  （這批沒有達標的；找『你做得出好內容、又有人想買、分潤不為零』的品）")
    print("\n提醒：分潤率/可否分潤以分潤後台為準；需求與競爭請參考實際搜尋/熱度，別憑感覺。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
