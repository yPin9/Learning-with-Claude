#!/usr/bin/env python3
"""funnel_calc.py — 分潤漏斗/損益試算（把「務實期待」變成可算的數字）。

收入漏斗：觸及 → 留言連結點擊 → 下單 → 分潤。
正算：給每篇平均觸及、發文數與各轉換率，估每月分潤。
反算：給每月分潤目標，反推「在這些轉換率下要多少觸及/篇數」。

⚠️ 重要：所有比率都是**你自己的假設**，不確定性極高（尤其轉換率與觸及，差一個量級很常見）。
這是一個**試算模型，不是收入保證**。先用保守值算、再用你 tracker.py 的真實數據校正。

純標準庫，複製即跑：
    python funnel_calc.py                         # 用示意預設值正算
    python funnel_calc.py --reach 4000 --posts 60 --ctr 1.5 --cr 3 --aov 600 --commission 4
    python funnel_calc.py --target 3000 --reach 4000 --ctr 1.5 --cr 3 --aov 600 --commission 4
參數：
    --reach 每篇平均觸及   --posts 每月發文數
    --ctr   觸及→留言連結點擊率(%)   --cr 點擊→下單轉換率(%)
    --aov   平均客單價(元)   --commission 平均分潤率(%)
    --cost  每月成本(元，API+你的時間估值，可選)   --target 每月分潤目標(元，啟用反算)
"""
import sys
import argparse


def forward(reach, posts, ctr, cr, aov, commission):
    impressions = reach * posts
    clicks = impressions * ctr / 100
    orders = clicks * cr / 100
    gmv = orders * aov
    commission_amt = gmv * commission / 100
    return dict(impressions=impressions, clicks=clicks, orders=orders, gmv=gmv, commission=commission_amt)


def fmt(n):
    return f"{n:,.0f}"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="分潤漏斗/損益試算")
    ap.add_argument("--reach", type=float, default=4000, help="每篇平均觸及（示意預設 4000）")
    ap.add_argument("--posts", type=float, default=60, help="每月發文數（示意預設 60＝一天兩篇）")
    ap.add_argument("--ctr", type=float, default=1.5, help="觸及→留言連結點擊率%%（示意 1.5）")
    ap.add_argument("--cr", type=float, default=3.0, help="點擊→下單轉換率%%（示意 3）")
    ap.add_argument("--aov", type=float, default=600, help="平均客單價元（示意 600）")
    ap.add_argument("--commission", type=float, default=4.0, help="平均分潤率%%（示意 4；以後台為準）")
    ap.add_argument("--cost", type=float, default=0, help="每月成本元（可選）")
    ap.add_argument("--target", type=float, help="每月分潤目標元（給了就做反算）")
    args = ap.parse_args(argv[1:])

    using_defaults = len(argv) == 1
    print("分潤漏斗試算" + ("（全用示意預設值，請換成你的數字）" if using_defaults else ""))
    print(f"假設：每篇觸及 {fmt(args.reach)}、每月 {fmt(args.posts)} 篇、"
          f"點擊率 {args.ctr}%、轉換率 {args.cr}%、客單 {fmt(args.aov)}、分潤率 {args.commission}%\n")

    r = forward(args.reach, args.posts, args.ctr, args.cr, args.aov, args.commission)
    print("【正算】每月預估")
    print(f"  總觸及   {fmt(r['impressions'])}")
    print(f"  連結點擊 {fmt(r['clicks'])}")
    print(f"  下單     {fmt(r['orders'])}")
    print(f"  成交額   {fmt(r['gmv'])} 元")
    print(f"  ▶ 分潤   {fmt(r['commission'])} 元/月")
    if args.cost:
        print(f"  扣成本 {fmt(args.cost)} → 淨 {fmt(r['commission'] - args.cost)} 元/月")

    if args.target:
        # 反算：固定各比率與客單，要多少「每月總觸及」才到目標分潤
        per_impression = (args.ctr / 100) * (args.cr / 100) * args.aov * (args.commission / 100)
        print(f"\n【反算】要月分潤 {fmt(args.target)} 元")
        if per_impression <= 0:
            print("  參數有 0，無法反算。")
        else:
            need_impr = args.target / per_impression
            need_posts = need_impr / args.reach if args.reach else float('inf')
            print(f"  需每月總觸及 ≈ {fmt(need_impr)}")
            print(f"  ＝ 每篇觸及 {fmt(args.reach)} 的話，每月約 {fmt(need_posts)} 篇"
                  f"（每天約 {need_posts/30:.1f} 篇）")
            print("  → 若每天篇數不切實際，代表得提高『觸及/篇』或『轉換率/客單/分潤率』，"
                  "或把目標調務實。")

    print("\n⚠️ 這是試算模型不是保證。轉換率/觸及極不穩，先用保守值、再用 tracker 真實數據回頭校正。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
