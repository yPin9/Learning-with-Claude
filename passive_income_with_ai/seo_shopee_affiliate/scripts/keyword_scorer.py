#!/usr/bin/env python3
"""keyword_scorer.py — 幫聯盟內容站的關鍵字選題打分排序。

輸入一份候選關鍵字 CSV，依「購買意圖 × 分潤潛力 × 競爭難度 × 流量」打分，
並用『硬門檻』剔除不值得做的題目（純資訊查詢、競爭過高、該類別零分潤）。
硬門檻優先於總分——分數再高，踩到硬門檻一樣不建議做。

純標準庫，複製即跑。沒帶檔案就跑內建示範資料：
    python keyword_scorer.py            # 跑示範
    python keyword_scorer.py kw.csv     # 跑你的 CSV

CSV 欄位（標頭必須一致）：
    keyword,volume,competition,intent,commission
      volume      : 每月搜尋量（整數）
      competition : 0.0–1.0，越高越難排（可用工具的 KD/競爭度換算）
      intent      : buy | compare | info | nav   （購買意圖；buy/compare 才有聯盟價值）
      commission  : high | med | low | none       （該商品類別的分潤潛力）
"""
import sys
import csv
from dataclasses import dataclass

# 各維度的權重（加總 100）。聯盟行銷裡「購買意圖」最值錢，所以權重最高。
WEIGHTS = {"intent": 35, "commission": 25, "ease": 25, "volume": 15}

INTENT_SCORE = {"buy": 5, "compare": 4, "info": 1, "nav": 1}
COMMISSION_SCORE = {"high": 5, "med": 3, "low": 2, "none": 1}


def volume_score(v: int) -> int:
    # 流量分桶（1–5）。注意：超高流量常伴隨超高競爭，所以流量權重刻意低。
    for threshold, score in [(1000, 5), (300, 4), (100, 3), (30, 2)]:
        if v >= threshold:
            return score
    return 1


def ease_score(comp: float) -> int:
    # 競爭越低越好排 → ease 分數越高（1–5）。
    for threshold, score in [(0.2, 5), (0.4, 4), (0.6, 3), (0.8, 2)]:
        if comp <= threshold:
            return score
    return 1


@dataclass
class Candidate:
    keyword: str
    volume: int
    competition: float
    intent: str
    commission: str

    def score(self) -> float:
        s_intent = INTENT_SCORE.get(self.intent, 1)
        s_comm = COMMISSION_SCORE.get(self.commission, 1)
        s_ease = ease_score(self.competition)
        s_vol = volume_score(self.volume)
        return round(
            s_intent / 5 * WEIGHTS["intent"]
            + s_comm / 5 * WEIGHTS["commission"]
            + s_ease / 5 * WEIGHTS["ease"]
            + s_vol / 5 * WEIGHTS["volume"],
            1,
        )

    def gates(self) -> list[str]:
        """硬門檻：踩到任何一條，分數再高也不建議做。"""
        g = []
        if self.intent in ("info", "nav"):
            g.append("純資訊/導航查詢，沒人會在這頁買東西")
        if self.competition >= 0.8:
            g.append("競爭過高，新站幾乎排不上")
        if self.commission == "none":
            g.append("該類別幾乎沒分潤")
        return g


SAMPLE = [
    Candidate("藍牙耳機 平價 推薦", 720, 0.45, "compare", "med"),
    Candidate("sony wf-c510 評價", 260, 0.30, "buy", "med"),
    Candidate("藍牙耳機 推薦", 9900, 0.88, "compare", "med"),
    Candidate("藍牙耳機 是什麼", 480, 0.20, "info", "med"),
    Candidate("登山杖 新手 怎麼選", 170, 0.35, "compare", "high"),
    Candidate("免費 line 貼圖 下載", 5400, 0.50, "nav", "none"),
]


def load_csv(path: str) -> list[Candidate]:
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append(Candidate(
                keyword=row["keyword"].strip(),
                volume=int(row["volume"]),
                competition=float(row["competition"]),
                intent=row["intent"].strip().lower(),
                commission=row["commission"].strip().lower(),
            ))
    return out


def main(argv: list[str]) -> int:
    if len(argv) > 1:
        candidates = load_csv(argv[1])
        src = argv[1]
    else:
        candidates = SAMPLE
        src = "（內建示範資料；帶一個 CSV 路徑可換成你的）"

    ranked = sorted(candidates, key=lambda c: c.score(), reverse=True)
    print(f"關鍵字選題打分　來源：{src}\n")
    print(f"{'分數':>5}  {'門檻':<4} 關鍵字")
    print("-" * 60)
    recommended = []
    for c in ranked:
        gates = c.gates()
        mark = "⛔" if gates else "✅"
        print(f"{c.score():>5}  {mark:<4} {c.keyword}")
        if gates:
            for g in gates:
                print(f"{'':>7}   └ {g}")
        elif c.score() >= 55:
            recommended.append(c)

    print("\n建議優先做（無硬門檻、且分數 ≥ 55）：")
    if recommended:
        for c in recommended:
            print(f"  • {c.keyword}（{c.score()} 分）")
    else:
        print("  （這批沒有達標的，回去找競爭更低、購買意圖更明確的長尾字）")
    print("\n提醒：分數是相對排序的工具，不是真理；硬門檻才是底線。"
          "競爭度/流量請用真實工具的數字，別憑感覺填。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
