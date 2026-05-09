# Final Project — 打造你自己的地端繁體中文小模型

> 目標：整合 34 章所有技術，從語料準備到部署，完整走一遍，最後交出一個可以跑的繁體中文 chatbot。

## 專案概述

你要建造的東西：

```
一個能回答台灣工程師日常問題的地端 chatbot
├── 基礎能力來自現成 pre-trained 模型（Qwen2.5-1.5B）
├── 繁體中文風格 + 格式遵守來自你的 instruction tuning 資料
├── 私有知識來自 RAG（你選定的文件庫）
└── 完整可用的本地 API 服務
```

**不是玩具**：完成後你會有一個真正可以用的工具，不依賴任何雲端服務。

## 四個里程碑

### Milestone 1：準備語料與訓練資料（~2 小時）

**M1.1：準備 RAG 知識庫文件**

選一個你在意的領域，準備 10–30 份文件（每份 500–5000 字）：

- 選項 A：本課程的 34 章內容（適合測試）
- 選項 B：你自己工作中的技術文件
- 選項 C：台灣工程師常用的技術文章（Hahow、iT 邦幫忙等）

**M1.2：準備 Instruction Tuning 資料（至少 100 筆）**

```
類別分布建議：
  30 筆：台灣工程師日常問題（程式、系統、工具）
  30 筆：繁體中文詞彙強化（確保說「軟體」不說「軟件」）
  20 筆：特定格式（條列、表格、標題）
  20 筆：你的特殊需求（這個模型要幫你做什麼）
```

品質審查標準：
- [ ] 沒有簡體字
- [ ] Response 直接回應 instruction
- [ ] 每筆 response 50–400 字
- [ ] 格式指令有正確的格式輸出

### Milestone 2：Fine-tuning（~4–8 小時，視硬體）

**M2.1：下載基礎模型**

```bash
huggingface-cli download Qwen/Qwen2.5-1.5B-Instruct-GGUF \
    qwen2.5-1.5b-instruct-q4_k_m.gguf \
    --local-dir models/
```

**M2.2：轉換訓練資料格式**

```python
# 用 Ch 29 的 format_chatml() 函數
# 把 JSONL → ChatML 純文字
```

**M2.3：執行 fine-tuning**

```bash
./build/bin/llama-finetune \
    --model-base models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
    --train-data data/train_chatml.txt \
    --epochs 3 \
    --batch 4 \
    --ctx 512 \
    --lora-r 8 \
    --lora-alpha 16 \
    --threads $(nproc) \
    --lora-out output/tw-engineer.bin
```

**M2.4：合併並匯入 Ollama**

```bash
./build/bin/llama-export-lora \
    -m models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
    --lora output/tw-engineer.bin \
    -o models/tw-engineer-merged.gguf

# 建立 Modelfile 並匯入
ollama create tw-engineer -f Modelfile
```

### Milestone 3：建立 RAG 知識庫（~1 小時）

```bash
# 確認 embedding 模型已下載
ollama pull nomic-embed-text
```

```python
# 用 Ch 33 的 SimpleVectorDB
# 1. 讀取所有文件
# 2. 切片（chunk_size=500）
# 3. Embed + 存入 vector_db.json

from pathlib import Path

db = SimpleVectorDB("knowledge_base.json")
for path in Path("docs/").glob("*.txt"):
    text = path.read_text(encoding='utf-8')
    chunks = chunk_text(text, 500, 50)
    for chunk in chunks:
        db.add(chunk)
db.save()
print(f"知識庫建立完成：{len(db.chunks)} 個片段")
```

### Milestone 4：部署完整服務（~1 小時）

```python
# chatbot.py — 完整整合版

import requests, json, sys
from simple_vector_db import SimpleVectorDB  # 你的 Ch 33 實作

class TWEngineerChatbot:
    def __init__(self, model="tw-engineer", db_path="knowledge_base.json"):
        self.model   = model
        self.db      = SimpleVectorDB(db_path)
        self.history = []
        self.system  = "你是一個台灣資深工程師助理，只用繁體中文回答，保持專業簡潔。"

    def chat(self, user_input, use_rag=True):
        # 1. RAG 檢索
        context = ""
        if use_rag and self.db.chunks:
            results = self.db.search(user_input, top_k=3)
            if results:
                ctx_parts = [f"[{i+1}] {chunk}" for i, (chunk, _) in enumerate(results)]
                context = "\n參考資料：\n" + "\n\n".join(ctx_parts) + "\n\n"

        # 2. 建立 messages（含對話歷史）
        messages = [{"role": "system", "content": self.system}]
        messages.extend(self.history[-6:])  # 保留最近 3 輪對話

        final_input = context + user_input if context else user_input
        messages.append({"role": "user", "content": final_input})

        # 3. 呼叫 Ollama
        resp = requests.post("http://localhost:11434/api/chat", json={
            "model": self.model,
            "messages": messages,
            "stream": True,
        }, stream=True, timeout=120)

        # 4. 串流輸出
        full_response = ""
        print("[助理] ", end="", flush=True)
        for line in resp.iter_lines():
            if line:
                data = json.loads(line)
                if not data.get("done"):
                    chunk = data["message"]["content"]
                    print(chunk, end="", flush=True)
                    full_response += chunk
        print()

        # 5. 更新對話歷史
        self.history.append({"role": "user",      "content": user_input})
        self.history.append({"role": "assistant",  "content": full_response})

        return full_response

def main():
    bot = TWEngineerChatbot()
    print("=== 台灣工程師助理（地端 LLM）===")
    print("指令：/rag off 關閉 RAG，/clear 清歷史，/quit 離開\n")

    use_rag = True
    while True:
        try:
            inp = input("[你] ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再見！")
            break

        if not inp: continue
        if inp == "/quit": break
        if inp == "/clear": bot.history = []; print("[清空]\n"); continue
        if inp == "/rag off": use_rag = False; print("[RAG 已關閉]\n"); continue
        if inp == "/rag on":  use_rag = True;  print("[RAG 已開啟]\n"); continue

        bot.chat(inp, use_rag=use_rag)
        print()

if __name__ == "__main__":
    main()
```

## 評估報告格式

完成 Final Project 後，寫一份評估報告（約 500 字）：

```markdown
## 地端 LLM 評估報告

### 模型規格
- 基礎模型：Qwen2.5-1.5B-Instruct Q4_K_M
- Fine-tuning：100 筆繁體中文 instruction，3 epochs
- RAG 知識庫：XX 份文件，XX 個片段

### 效能評估
- 平均推論速度：XX tok/s
- 硬體：CPU XX 核心，RAM XX GB

### 品質評估（10 個測試問題，滿分 50）

| 問題 | 繁中(5) | 格式(5) | 準確(5) |
|------|---------|---------|---------|
| ... | ... | ... | ... |

### 結論與改進方向
- Fine-tuning 有效改善了哪些方面
- RAG 對哪類問題幫助最大
- 如果有 GPU，下一步會怎麼做
```

## 完成標準

| 里程碑 | 完成條件 |
|--------|---------|
| M1 | 100 筆 instruction 資料，10+ 份知識庫文件 |
| M2 | fine-tune 完成，Ollama 能跑 tw-engineer 模型 |
| M3 | 知識庫建立，RAG 能正確回應知識庫內的問題 |
| M4 | chatbot.py 可互動，串流輸出正常 |
| 評估 | 10 個測試問題，繁體中文率 > 90%，平均分 > 35/50 |

## 加分挑戰

**技術加分**：
- [ ] 加入 token 統計和 tokens/s 顯示
- [ ] 實作 `/save` 指令儲存對話紀錄
- [ ] 把 chatbot 包裝成 FastAPI 服務（Ch 32）
- [ ] 比較 RAG 開啟/關閉的回答品質差異

**資料加分**：
- [ ] 自製 200+ 筆高品質 instruction 資料
- [ ] 用 DPO 格式準備 50 筆偏好比較資料（為未來 DPO 做準備）

## 恭喜

你從一個「只會 Python、不懂 ML」的起點，走到能在自己的機器上訓練、微調、部署語言模型。這條路大多數人沒有走到這裡。

如果你有 GPU 的一天，LoRA（有 CUDA）、QLoRA、DPO——這些技術你都已經理解了原理，上手只是時間問題。
