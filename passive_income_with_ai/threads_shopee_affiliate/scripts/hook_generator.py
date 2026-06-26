#!/usr/bin/env python3
"""hook_generator.py — 主題 + 商品 → N 個 Threads 內文鉤子變體 + 第一則留言文案（Claude API）。

用法 B 的產文工具。產出的是『草稿』：AI 給你不同鉤子的內文起手式，你要挑一個、
改成自己的口吻、補真實心得——千篇一律的 AI 貼文在 Threads 一樣會被埋（去商業化是命門）。

規則內建：內文**不放連結**（連結放第一則留言），第一則留言含分潤連結佔位 + 揭露 + 留言 CTA。

用法：
    pip install anthropic ; export ANTHROPIC_API_KEY=sk-...
    python hook_generator.py --topic "通勤藍牙耳機" --product products.json --n 5 --dry-run
    python hook_generator.py --topic "通勤藍牙耳機" --product products.json --n 5 > drafts.md
"""
import os
import sys
import json
import argparse

MODEL = "claude-opus-4-8"   # 想壓成本可改 claude-haiku-4-5-20251001

SYSTEM = """你是台灣 Threads 的資深內容操盤手，專做蝦皮分潤導購，但你最討厭「業配味」。
你深知 Threads 的操作共識（創作者歸納、非官方）：互動權重大致是留言>轉發>讚，靠鉤子逼留言、靠停留時間發酵，連結要放第一則留言不要放內文。
你的任務是給「不同鉤子」的內文起手式草稿，口吻像真人在跟朋友聊天，不是廣告。鐵則：
1. 內文**絕對不要**放任何連結或網址。
2. 每個變體用不同鉤子：問句、痛點/踩雷、清單、對立選邊、好奇缺口、個人故事——標明用了哪種。
3. 去商業化：像分享經驗，不像推銷；可以有口語、有觀點、甚至有缺點。
4. 結尾要有「逼留言」的 CTA（例如問讀者偏好、請他們留言索取）。
5. 另外產一則「第一則留言」文案：含分潤連結佔位 {{affiliate_url:商品名}}、一句分潤揭露、和「連結放這」的引導。
6. 只根據我給的商品事實，不杜撰規格。需要真人經驗的地方標 [請補：你的真實心得]。
輸出純 Markdown，每個變體一個 H2。"""

USER_TEMPLATE = """主題：{topic}
要產 {n} 個不同鉤子的內文變體。

商品事實（只能用這些，不要杜撰）：
{products}

請輸出：
## 變體 1（鉤子：____）
（內文，不含任何連結）

…共 {n} 個變體…

## 共用的第一則留言文案
（含 {{affiliate_url:商品名}} 佔位、分潤揭露、留言引導）"""


def build_messages(topic: str, n: int, products: list[dict]) -> tuple[str, str]:
    lines = [
        f"- {p.get('name','?')}｜價格 {p.get('price','?')}｜重點 {p.get('specs','?')}"
        f"｜優點 {p.get('pros','?')}｜缺點 {p.get('cons','?')}"
        for p in products
    ]
    return SYSTEM, USER_TEMPLATE.format(topic=topic, n=n, products="\n".join(lines))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Threads 內文鉤子變體產生器")
    ap.add_argument("--topic", required=True)
    ap.add_argument("--product", required=True, help="products.json 路徑")
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--max-tokens", type=int, default=3072)
    ap.add_argument("--dry-run", action="store_true", help="只印 prompt，不呼叫 API、不需金鑰")
    args = ap.parse_args(argv[1:])

    with open(args.product, encoding="utf-8") as f:
        products = json.load(f)
    system, user = build_messages(args.topic, args.n, products)

    if args.dry_run:
        print("=== SYSTEM ===\n" + system)
        print("\n=== USER ===\n" + user)
        print(f"\n=== 設定 ===\nmodel={args.model}  max_tokens={args.max_tokens}  n={args.n}")
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
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    print("".join(b.text for b in resp.content if b.type == "text"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
