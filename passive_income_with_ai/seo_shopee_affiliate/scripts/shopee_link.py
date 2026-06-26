#!/usr/bin/env python3
"""shopee_link.py — 蝦皮分潤連結的正規化與追蹤對應表。

重要界線：**官方分潤短連結要從你的分潤後台（蝦皮官方分潤計畫 / Involve Asia 等）產生**，
本工具不替你「產生」官方連結，也不該——自己拼的連結不會被計入分潤。
本工具負責的是：
  1. 正規化商品 URL（去掉一次性追蹤雜訊、驗證確實是蝦皮網域）
  2.（若你的計畫支援）幫你掛上自訂的 sub_id 追蹤標記，方便回頭對帳
  3. 維護一份「文章 ↔ 商品 ↔ 連結」對應表（links.csv），讓 tracker.py 對得起來

純標準庫，複製即跑：
    python shopee_link.py add --article bt-budget --product "Sony WF-C510" \\
        --url "https://shopee.tw/product/123/456?sp=xxx" --subid bt-budget \\
        --affurl "https://s.shopee.tw/xxxx"        # 從後台產生的官方短連結（選填）
    python shopee_link.py list
"""
import sys
import csv
import argparse
import urllib.parse
from pathlib import Path

REGISTRY = Path("links.csv")
FIELDS = ["article", "product", "normalized_url", "subid", "affiliate_url"]

# 蝦皮台灣常見網域；其餘網域會警告（避免你把錯的連結存進來）
SHOPEE_HOSTS = {"shopee.tw", "s.shopee.tw", "shp.ee"}
# 正規化時要丟掉的一次性/追蹤參數（保留商品定位必要的參數）
STRIP_PARAMS = {"sp", "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "xptdk"}


def is_shopee(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    return host in SHOPEE_HOSTS


def normalize(url: str) -> str:
    """去掉追蹤雜訊參數，保留路徑與必要 query。"""
    p = urllib.parse.urlparse(url)
    kept = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if k not in STRIP_PARAMS]
    query = urllib.parse.urlencode(kept)
    return urllib.parse.urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", query, ""))


def load() -> list[dict]:
    if not REGISTRY.exists():
        return []
    with REGISTRY.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def save(rows: list[dict]) -> None:
    with REGISTRY.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def cmd_add(args) -> int:
    if not is_shopee(args.url):
        if not args.allow_non_shopee:
            print(f"❌ 拒絕：{args.url} 不是蝦皮網域（{', '.join(SHOPEE_HOSTS)}）。"
                  f"\n   --url 應該放『蝦皮商品 URL』；後台產的官方短連結請放 --affurl。"
                  f"\n   真的要存非蝦皮網域，加 --allow-non-shopee 明確覆寫。")
            return 1
        print(f"⚠️  已用 --allow-non-shopee 覆寫，存入非蝦皮網域：{args.url}")
    rows = load()
    rows.append({
        "article": args.article,
        "product": args.product,
        "normalized_url": normalize(args.url),
        "subid": args.subid or args.article,   # 預設用文章 slug 當 sub_id，方便對帳
        "affiliate_url": args.affurl or "",
    })
    save(rows)
    print(f"已加入：{args.article} → {args.product}")
    if not args.affurl:
        print("提醒：affiliate_url 留空了。記得到分潤後台產生官方短連結後，"
              "再 add 一次或手動補進 links.csv——沒有官方連結就不會有分潤。")
    return 0


def cmd_list(_args) -> int:
    rows = load()
    if not rows:
        print("（links.csv 還沒有資料）")
        return 0
    for r in rows:
        aff = r["affiliate_url"] or "⚠️ 尚未填官方分潤連結"
        print(f"[{r['article']}] {r['product']}")
        print(f"    商品: {r['normalized_url']}")
        print(f"    sub_id: {r['subid']}    分潤連結: {aff}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="蝦皮分潤連結正規化與對應表")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="新增一筆 文章↔商品↔連結 對應")
    a.add_argument("--article", required=True, help="文章 slug")
    a.add_argument("--product", required=True, help="商品名")
    a.add_argument("--url", required=True, help="商品原始 URL")
    a.add_argument("--subid", help="追蹤用 sub_id（預設用文章 slug；是否支援以你的計畫為準）")
    a.add_argument("--affurl", help="從分潤後台產生的官方短連結（選填）")
    a.add_argument("--allow-non-shopee", action="store_true", help="明確允許存入非蝦皮網域的 --url")
    a.set_defaults(func=cmd_add)

    l = sub.add_parser("list", help="列出對應表")
    l.set_defaults(func=cmd_list)

    args = ap.parse_args(argv[1:])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
