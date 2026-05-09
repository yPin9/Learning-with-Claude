# 練習 B — 用 Ollama 架本地 Chat API，接 Python 客戶端

> 目標：把 Ollama 包裝成一個有系統提示、對話記憶、串流輸出的 chat 客戶端程式。

## 任務規格

| 項目 | 規格 |
|------|------|
| 後端 | Ollama（qwen2.5:7b 或 llama3.2:3b） |
| 功能 | 保留對話歷史（多輪對話）、串流輸出、系統提示可設定 |
| 介面 | 命令列互動（不需要 Web UI） |
| 加分 | 統計每輪回應的 tokens/s |

## 期望輸出範例

```
$ python chat.py --model qwen2.5:7b --system "你是台灣資深工程師，用繁體中文回答。"

[系統] 已載入 qwen2.5:7b
[你] 什麼是 Transformer？
[助理] Transformer 是一種基於注意力機制的神經網路架構...（串流輸出）
（0.8s，42 tokens，52.3 tok/s）

[你] 它有什麼優缺點？
[助理] 優點：...（能記住上一輪問的是 Transformer）

[你] /clear
[系統] 對話歷史已清空

[你] /quit
再見！
```

## 實作步驟建議

### Step 1：確認 Ollama 能連線

```python
import requests

def check_ollama():
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=3)
        models = [m["name"] for m in resp.json()["models"]]
        print(f"Ollama 已連線，可用模型：{models}")
        return True
    except Exception as e:
        print(f"無法連線到 Ollama：{e}")
        print("請確認 Ollama 已啟動（ollama serve）")
        return False
```

### Step 2：對話類別，維護歷史

```python
class LocalChat:
    def __init__(self, model, system_prompt=""):
        self.model   = model
        self.history = []
        if system_prompt:
            self.history.append({"role": "system", "content": system_prompt})

    def send(self, user_message):
        self.history.append({"role": "user", "content": user_message})
        # 呼叫 Ollama API，拿回回應
        # ...回應加入 self.history
        pass

    def clear(self):
        system_msgs = [m for m in self.history if m["role"] == "system"]
        self.history = system_msgs  # 保留系統提示，清空對話
```

### Step 3：串流輸出

用 `stream=True` 呼叫 API，逐 token 印出：

```python
import json, time, requests

def stream_chat(model, messages):
    start = time.time()
    token_count = 0
    full_response = ""

    resp = requests.post("http://localhost:11434/api/chat", json={
        "model": model,
        "messages": messages,
        "stream": True,
    }, stream=True)

    print("[助理] ", end="", flush=True)
    for line in resp.iter_lines():
        if line:
            data = json.loads(line)
            if not data.get("done"):
                chunk = data["message"]["content"]
                print(chunk, end="", flush=True)
                full_response += chunk
                token_count += 1
            else:
                elapsed = time.time() - start
                print(f"\n（{elapsed:.1f}s，{token_count} tokens，{token_count/elapsed:.1f} tok/s）")

    return full_response
```

### Step 4：命令列介面

```python
import argparse

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model",  default="qwen2.5:7b")
    parser.add_argument("--system", default="你是一個有幫助的助理，請用繁體中文回答。")
    args = parser.parse_args()

    if not check_ollama():
        return

    chat = LocalChat(args.model, args.system)
    print(f"[系統] 已載入 {args.model}")
    print("指令：/clear 清空歷史，/quit 離開\n")

    while True:
        try:
            user_input = input("[你] ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再見！")
            break

        if not user_input:
            continue
        elif user_input == "/quit":
            print("再見！")
            break
        elif user_input == "/clear":
            chat.clear()
            print("[系統] 對話歷史已清空\n")
        elif user_input == "/history":
            for msg in chat.history:
                if msg["role"] != "system":
                    print(f"  [{msg['role']}] {msg['content'][:50]}...")
        else:
            response = chat.send(user_input)

if __name__ == "__main__":
    main()
```

## 完整參考解答

**先自己實作 `LocalChat.send()` 和整合串流邏輯，再看解答。**

<details>
<summary>點開完整實作</summary>

```python
#!/usr/bin/env python3
"""local_chat.py — 地端 LLM 對話客戶端"""

import argparse
import json
import sys
import time
import requests


def check_ollama(base_url="http://localhost:11434"):
    try:
        resp = requests.get(f"{base_url}/api/tags", timeout=3)
        models = [m["name"] for m in resp.json().get("models", [])]
        return True, models
    except Exception as e:
        return False, str(e)


class LocalChat:
    def __init__(self, model, system_prompt="", base_url="http://localhost:11434"):
        self.model    = model
        self.base_url = base_url
        self.history  = []
        if system_prompt:
            self.history.append({"role": "system", "content": system_prompt})

    def send(self, user_message, stream=True):
        self.history.append({"role": "user", "content": user_message})

        start = time.time()
        token_count = 0
        full_response = ""

        resp = requests.post(
            f"{self.base_url}/api/chat",
            json={"model": self.model, "messages": self.history, "stream": stream},
            stream=stream,
            timeout=120,
        )
        resp.raise_for_status()

        print("[助理] ", end="", flush=True)

        if stream:
            for line in resp.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                if not data.get("done"):
                    chunk = data["message"]["content"]
                    print(chunk, end="", flush=True)
                    full_response += chunk
                    token_count += 1
        else:
            full_response = resp.json()["message"]["content"]
            print(full_response, end="")
            token_count = len(full_response.split())

        elapsed = time.time() - start
        print(f"\n（{elapsed:.1f}s，{token_count} tokens，{token_count/max(elapsed,0.01):.1f} tok/s）\n")

        self.history.append({"role": "assistant", "content": full_response})
        return full_response

    def clear(self):
        self.history = [m for m in self.history if m["role"] == "system"]


def main():
    parser = argparse.ArgumentParser(description="地端 LLM 對話客戶端")
    parser.add_argument("--model",  default="qwen2.5:7b",             help="Ollama 模型名稱")
    parser.add_argument("--system", default="你是一個有幫助的助理，請用繁體中文回答。", help="系統提示")
    parser.add_argument("--no-stream", action="store_true",           help="關閉串流輸出")
    args = parser.parse_args()

    ok, info = check_ollama()
    if not ok:
        print(f"錯誤：無法連線 Ollama — {info}")
        print("請執行：ollama serve")
        sys.exit(1)
    print(f"[系統] 已連線 Ollama，可用模型：{info}")

    chat = LocalChat(args.model, args.system)
    print(f"[系統] 使用模型：{args.model}")
    print(f"[系統] 系統提示：{args.system[:60]}...")
    print("指令：/clear 清空歷史，/history 查看歷史，/quit 離開\n")

    while True:
        try:
            user_input = input("[你] ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再見！")
            break

        if not user_input:
            continue
        if user_input == "/quit":
            print("再見！")
            break
        if user_input == "/clear":
            chat.clear()
            print("[系統] 對話歷史已清空\n")
            continue
        if user_input == "/history":
            for msg in chat.history:
                role = msg["role"]
                content = msg["content"][:80].replace('\n', ' ')
                print(f"  [{role}] {content}...")
            print()
            continue

        try:
            chat.send(user_input, stream=not args.no_stream)
        except requests.exceptions.Timeout:
            print("[錯誤] 請求逾時，模型可能很忙\n")
        except Exception as e:
            print(f"[錯誤] {e}\n")


if __name__ == "__main__":
    main()
```

</details>

## 測試用例

| 測試 | 期望 |
|------|------|
| 輸入「你好」 | 模型正常回應 |
| 問兩輪相關問題 | 第二輪能引用第一輪的上下文 |
| `/clear` 後再問 | 模型不記得第一輪的內容 |
| 問一個長問題 | 串流輸出，字元一個一個印出來 |
| 統計欄位 | 顯示時間和 tokens/s |

## 自我檢核

- [ ] 多輪對話能正確記住上下文
- [ ] 串流輸出正常運作，不是一次全印
- [ ] `/clear` 和 `/quit` 指令可用
- [ ] 顯示每輪的 tokens/s 統計

→ [Ch 18 資料管線：語料清洗 + tokenization 流程](./18-data-pipeline.md)
