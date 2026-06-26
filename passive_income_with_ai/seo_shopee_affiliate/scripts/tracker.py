#!/usr/bin/env python3
"""tracker.py — 文章成效與 ROI 追蹤（CSV）。

把每篇文章的：曝光/點擊/下單/分潤/製作成本記下來，算粗略 ROI，標出該檢討的文章。
數據從你的分潤後台 + Google Search Console 手動回填——這就是「用真實數字而非感覺」
判斷一篇文章值不值得留。

純標準庫，複製即跑：
    python tracker.py add    --article bt-budget --keyword "藍牙耳機 平價 推薦" --url https://site/bt --cost 1.5
    python tracker.py update --article bt-budget --clicks 320 --orders 7 --commission 210
    python tracker.py report
"""
import sys
import csv
import argparse
import datetime
from pathlib import Path

DB = Path("tracker.csv")
FIELDS = ["article", "keyword", "url", "published", "clicks", "orders", "commission", "cost"]
NUMERIC = {"clicks", "orders", "commission", "cost"}


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


def find(rows, article):
    return next((r for r in rows if r["article"] == article), None)


def cmd_add(args) -> int:
    rows = load()
    if find(rows, args.article):
        print(f"已存在：{args.article}（要改數據用 update）")
        return 1
    rows.append({
        "article": args.article, "keyword": args.keyword, "url": args.url,
        "published": args.date or datetime.date.today().isoformat(),
        "clicks": 0, "orders": 0, "commission": 0, "cost": args.cost or 0,
    })
    save(rows)
    print(f"已新增：{args.article}（{args.keyword}）")
    return 0


def cmd_update(args) -> int:
    rows = load()
    r = find(rows, args.article)
    if not r:
        print(f"找不到：{args.article}（先用 add 建立）")
        return 1
    for k in ("clicks", "orders", "commission", "cost"):
        v = getattr(args, k)
        if v is not None:
            r[k] = v
    save(rows)
    print(f"已更新：{args.article}")
    return 0


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def cmd_report(_args) -> int:
    rows = load()
    if not rows:
        print("（tracker.csv 還沒有資料）")
        return 0
    today = datetime.date.today()
    tot_clicks = tot_orders = tot_comm = tot_cost = 0.0
    print(f"{'文章':<18}{'點擊':>6}{'下單':>6}{'分潤':>8}{'成本':>7}{'ROI':>7}  備註")
    print("-" * 78)
    for r in sorted(rows, key=lambda x: _f(x["commission"]), reverse=True):
        clicks, orders = _f(r["clicks"]), _f(r["orders"])
        comm, cost = _f(r["commission"]), _f(r["cost"])
        tot_clicks += clicks; tot_orders += orders; tot_comm += comm; tot_cost += cost
        roi = f"{(comm - cost) / cost:+.0%}" if cost > 0 else "—"
        notes = []
        # 上線夠久卻沒流量/沒轉換 → 該檢討（門檻見上層 README 第五節）
        try:
            age_days = (today - datetime.date.fromisoformat(r["published"])).days
        except ValueError:
            age_days = 0
        if age_days >= 180 and clicks == 0:
            notes.append("半年無流量→重寫或砍")
        elif clicks >= 100 and orders == 0:
            notes.append("有流量零轉換→換選品/角度")
        cr = f"轉換{orders/clicks:.1%}" if clicks else ""
        print(f"{r['article']:<18}{clicks:>6.0f}{orders:>6.0f}{comm:>8.0f}{cost:>7.1f}{roi:>7}  "
              f"{cr} {' '.join(notes)}")
    print("-" * 78)
    site_roi = f"{(tot_comm - tot_cost) / tot_cost:+.0%}" if tot_cost > 0 else "—"
    print(f"{'整站':<18}{tot_clicks:>6.0f}{tot_orders:>6.0f}{tot_comm:>8.0f}{tot_cost:>7.1f}{site_roi:>7}")
    print("\n提醒：賺錢的通常是少數長尾文，看分群別看總數；分潤費率/歸因以分潤後台為準。")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="文章成效與 ROI 追蹤")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="新增一篇追蹤")
    a.add_argument("--article", required=True)
    a.add_argument("--keyword", required=True)
    a.add_argument("--url", required=True)
    a.add_argument("--cost", type=float, help="製作成本（API＋時間估值，單位自訂）")
    a.add_argument("--date", help="上線日期 YYYY-MM-DD（預設今天）")
    a.set_defaults(func=cmd_add)

    u = sub.add_parser("update", help="回填數據")
    u.add_argument("--article", required=True)
    u.add_argument("--clicks", type=float)
    u.add_argument("--orders", type=float)
    u.add_argument("--commission", type=float)
    u.add_argument("--cost", type=float)
    u.set_defaults(func=cmd_update)

    r = sub.add_parser("report", help="輸出成效報表")
    r.set_defaults(func=cmd_report)

    args = ap.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
