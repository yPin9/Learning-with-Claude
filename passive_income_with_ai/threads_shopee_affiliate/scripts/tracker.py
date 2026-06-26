#!/usr/bin/env python3
"""tracker.py — Threads 貼文成效與分潤追蹤（CSV）。

記每篇貼文：觸及/讚/留言/連結點擊/分潤，算出「哪種鉤子 × 哪類選品」最會轉換。
Threads 上『讚 ≠ 錢』——要盯的是觸及→留言連結點擊→下單→分潤這條漏斗。
數據從 Threads 內建洞察 + 蝦皮分潤後台手動回填。

純標準庫，複製即跑：
    python tracker.py add    --post bt-commute-01 --hook 痛點 --topic 通勤耳機 --product "B款"
    python tracker.py update --post bt-commute-01 --reach 18000 --likes 1200 --comments 340 --clicks 95 --commission 60
    python tracker.py report
"""
import sys
import csv
import argparse
import datetime
from pathlib import Path

DB = Path("threads_tracker.csv")
FIELDS = ["post", "hook", "topic", "product", "posted",
          "reach", "likes", "comments", "clicks", "commission"]
NUMERIC = {"reach", "likes", "comments", "clicks", "commission"}


def load() -> list[dict]:
    if not DB.exists():
        return []
    with DB.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def save(rows: list[dict]) -> None:
    with DB.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def find(rows, post):
    return next((r for r in rows if r["post"] == post), None)


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def cmd_add(args) -> int:
    rows = load()
    if find(rows, args.post):
        print(f"已存在：{args.post}（改數據用 update）")
        return 1
    rows.append({
        "post": args.post, "hook": args.hook or "", "topic": args.topic or "",
        "product": args.product or "", "posted": args.date or datetime.date.today().isoformat(),
        "reach": 0, "likes": 0, "comments": 0, "clicks": 0, "commission": 0,
    })
    save(rows)
    print(f"已新增：{args.post}（鉤子={args.hook}，選品={args.product}）")
    return 0


def cmd_update(args) -> int:
    rows = load()
    r = find(rows, args.post)
    if not r:
        print(f"找不到：{args.post}（先用 add 建立）")
        return 1
    for k in NUMERIC:
        v = getattr(args, k)
        if v is not None:
            r[k] = v
    save(rows)
    print(f"已更新：{args.post}")
    return 0


def cmd_report(_args) -> int:
    rows = load()
    if not rows:
        print("（threads_tracker.csv 還沒有資料）")
        return 0
    print(f"{'貼文':<16}{'鉤子':<8}{'觸及':>8}{'讚':>7}{'留言':>6}{'點擊':>6}{'分潤':>7}  漏斗")
    print("-" * 86)
    by_hook: dict[str, list[float]] = {}
    tot = {k: 0.0 for k in NUMERIC}
    for r in sorted(rows, key=lambda x: _f(x["commission"]), reverse=True):
        reach, likes = _f(r["reach"]), _f(r["likes"])
        comments, clicks, comm = _f(r["comments"]), _f(r["clicks"]), _f(r["commission"])
        for k in NUMERIC:
            tot[k] += _f(r[k])
        by_hook.setdefault(r["hook"] or "（未標）", []).append(comm)
        # 漏斗診斷：留言比（演算法友善度）、點擊/觸及（內容→留言連結的導流力）
        c_ratio = f"留言比{comments/likes:.0%}" if likes else ""
        ctr = f"點閱{clicks/reach:.1%}" if reach else ""
        flag = ""
        if reach >= 5000 and clicks == 0:
            flag = "← 高觸及零點擊：留言連結沒人點，檢查 CTA/連結位置"
        elif clicks >= 30 and comm == 0:
            flag = "← 有點擊零分潤：選品沒轉換或追蹤被覆蓋（實測連結！）"
        print(f"{r['post']:<16}{(r['hook'] or '-'):<8}{reach:>8.0f}{likes:>7.0f}"
              f"{comments:>6.0f}{clicks:>6.0f}{comm:>7.0f}  {c_ratio} {ctr} {flag}")
    print("-" * 86)
    print(f"{'合計':<16}{'':<8}{tot['reach']:>8.0f}{tot['likes']:>7.0f}"
          f"{tot['comments']:>6.0f}{tot['clicks']:>6.0f}{tot['commission']:>7.0f}")

    print("\n各鉤子的累計分潤（看哪種鉤子最會轉換）：")
    for hook, comms in sorted(by_hook.items(), key=lambda kv: sum(kv[1]), reverse=True):
        print(f"  {hook:<10} 分潤合計 {sum(comms):.0f}（{len(comms)} 篇）")
    print("\n提醒：讚 ≠ 錢。盯『觸及→留言連結點擊→分潤』；分潤費率/歸因以蝦皮後台為準。")
    return 0


def cmd_ab(args) -> int:
    """A/B 評估：依某維度（鉤子/選品/主題）分組，比累計成效 + 樣本量警告。"""
    rows = load()
    if not rows:
        print("（threads_tracker.csv 還沒有資料）")
        return 0
    key = args.by
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r.get(key) or "（未標）", []).append(r)

    print(f"A/B 比較（依 {key} 分組）。主指標：連結點擊與分潤；樣本太小不算數。\n")
    print(f"{key:<12}{'篇數':>5}{'總觸及':>9}{'總點擊':>7}{'總分潤':>8}{'點閱率':>8}{'分潤/篇':>9}")
    print("-" * 64)
    stats = []
    for g, rs in groups.items():
        n = len(rs)
        reach = sum(_f(r["reach"]) for r in rs)
        clicks = sum(_f(r["clicks"]) for r in rs)
        comm = sum(_f(r["commission"]) for r in rs)
        ctr = clicks / reach if reach else 0
        per_post = comm / n if n else 0
        stats.append((g, n, reach, clicks, comm, ctr, per_post))
    for g, n, reach, clicks, comm, ctr, per_post in sorted(stats, key=lambda x: x[6], reverse=True):
        warn = "  ⚠ 樣本<5，先別下結論" if n < 5 else ""
        print(f"{g:<12}{n:>5}{reach:>9.0f}{clicks:>7.0f}{comm:>8.0f}{ctr:>7.1%}{per_post:>9.0f}{warn}")
    print("\n判讀：一次只比一個變數；每組至少 5–10 篇才有意義；贏家要拿到新一批 held-out 再驗"
          "（避免過擬合到某次運氣，見 workflow.md 第 2 節）。")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Threads 貼文成效與分潤追蹤")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="新增一篇貼文追蹤")
    a.add_argument("--post", required=True, help="貼文代號")
    a.add_argument("--hook", help="用的鉤子（問句/痛點/清單/對立/好奇/故事）")
    a.add_argument("--topic", help="主題")
    a.add_argument("--product", help="主推選品")
    a.add_argument("--date", help="發佈日期 YYYY-MM-DD（預設今天）")
    a.set_defaults(func=cmd_add)

    u = sub.add_parser("update", help="回填數據")
    u.add_argument("--post", required=True)
    for k in ("reach", "likes", "comments", "clicks", "commission"):
        u.add_argument(f"--{k}", type=float)
    u.set_defaults(func=cmd_update)

    r = sub.add_parser("report", help="輸出成效報表")
    r.set_defaults(func=cmd_report)

    b = sub.add_parser("ab", help="A/B 比較（依鉤子/選品/主題分組）")
    b.add_argument("--by", choices=["hook", "product", "topic"], default="hook",
                   help="比較的維度（預設 hook）")
    b.set_defaults(func=cmd_ab)

    args = ap.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
