#!/usr/bin/env python3
"""brief_and_draft.py — 關鍵字 + 商品事實 → SEO 大綱與 zh-TW 評測草稿（Claude API）。

產出的是『草稿』，不是成品：你還要補上只有真人有的第一手經驗、實測照片、最終取捨判斷
（這正是 Google E-E-A-T 與 scaled-content 政策要求的，純 AI 內容給不出）。
草稿一律先過 content_qa.py 再上站。

用法：
    pip install anthropic ; export ANTHROPIC_API_KEY=sk-...
    python brief_and_draft.py --keyword "藍牙耳機 平價 推薦" --products products.json --dry-run
    python brief_and_draft.py --keyword "藍牙耳機 平價 推薦" --products products.json > draft.md

--dry-run 不呼叫 API、不需要金鑰，只印出將送出的 system / prompt，方便你先檢視。

products.json 格式（你手動整理、不要爬蟲）：
    [{"name": "...", "price": "990–1290", "specs": "...", "pros": "...",
      "cons": "...", "affiliate_url": "從分潤後台產生的官方連結"}]
"""
import os
import sys
import json
import argparse

MODEL = "claude-opus-4-8"   # 基石文章用 Opus 把品質做滿；量產次要草稿可改 haiku 控成本

SYSTEM = """你是台灣在地的資深 SEO 內容編輯，專長把商品評測寫得「對讀者真的有用」而非為了塞關鍵字。
你寫的是繁體中文、給台灣讀者看的聯盟評測/選購指南。鐵則：
1. 內容要有真實決策價值：比較表、各商品優缺點（一定要寫缺點）、適合誰/不適合誰、FAQ。
2. 誠實，不誇大、不杜撰規格；只根據我提供的商品事實寫，不確定的就標示「需查證」。
3. 關鍵字自然融入，嚴禁堆砌。
4. 文章開頭必須放分潤揭露聲明。
5. 在需要真人經驗的地方，明確標出 [請補：實測心得/照片] 佔位符——提醒作者補第一手內容。
6. 分潤連結用 {{affiliate_url:商品名}} 佔位符標示，不要自己編造連結。
輸出純 Markdown。"""

USER_TEMPLATE = """目標關鍵字：{keyword}

請依序產出三部分：
## 1. 搜尋意圖分析
這個關鍵字背後的讀者想解決什麼、處在購買決策的哪一步、最想看到什麼。

## 2. 文章大綱
H2/H3 結構，涵蓋：選購要點、商品比較、各商品優缺點、適合誰、FAQ。

## 3. 文章草稿（繁體中文）
依大綱寫成完整草稿，含開頭的分潤揭露、比較表、每個商品的優缺點與 {{affiliate_url:商品名}} 連結佔位符、
以及至少兩處 [請補：實測心得/照片] 佔位符。

可用的商品事實（只能用這些，不要杜撰）：
{products}"""


def build_messages(keyword: str, products: list[dict]) -> tuple[str, str]:
    lines = []
    for p in products:
        lines.append(
            f"- {p.get('name','?')}｜價格 {p.get('price','?')}｜規格 {p.get('specs','?')}"
            f"｜優點 {p.get('pros','?')}｜缺點 {p.get('cons','?')}"
            f"｜連結佔位 {{{{affiliate_url:{p.get('name','?')}}}}}"
        )
    user = USER_TEMPLATE.format(keyword=keyword, products="\n".join(lines))
    return SYSTEM, user


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="關鍵字+商品 → SEO 草稿")
    ap.add_argument("--keyword", required=True)
    ap.add_argument("--products", required=True, help="products.json 路徑")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--dry-run", action="store_true", help="只印 prompt，不呼叫 API、不需金鑰")
    args = ap.parse_args(argv[1:])

    with open(args.products, encoding="utf-8") as f:
        products = json.load(f)

    system, user = build_messages(args.keyword, products)

    if args.dry_run:
        print("=== SYSTEM ===\n" + system)
        print("\n=== USER ===\n" + user)
        print(f"\n=== 設定 ===\nmodel={args.model}  max_tokens={args.max_tokens}")
        print("（--dry-run：未呼叫 API。拿掉 --dry-run 並設好 ANTHROPIC_API_KEY 才會真的產草稿。）")
        return 0

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("錯誤：未設定 ANTHROPIC_API_KEY。", file=sys.stderr)
        return 2
    try:
        import anthropic
    except ImportError:
        print("錯誤：請先 pip install anthropic。", file=sys.stderr)
        return 2

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=args.model,
        max_tokens=args.max_tokens,
        # 對固定的 system 開 prompt caching，量產多篇時省錢（見 harness 課 Ch 17）
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    print("".join(b.text for b in resp.content if b.type == "text"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
