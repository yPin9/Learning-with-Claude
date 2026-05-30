# Ch 12 — 供應鏈與模型安全

> **目標**：能辨識 LLM 供應鏈中的風險點——model hosting、model format、dependency、fine-tuning pipeline——並知道各自的防禦策略。
>
> **環境**：Python 3.11, LangChain 0.3.x, Ollama + llama3.2:3b, Ubuntu 22.04

---

## 為什麼需要這個？

傳統軟體供應鏈攻擊你已經知道：惡意 npm 套件、被污染的 Docker image、SolarWinds 那種透過合法更新管道植入後門。這些都發生在 **code dependency** 層。

AI 供應鏈比傳統軟體更廣。你不只依賴 code——還依賴 **model weights**（從 Hugging Face 下載的 `.bin` 或 `.safetensors`）、**training data**（你拿來 fine-tune 的資料集）、**embedding model**（向量化你知識庫的那個模型）、以及 **fine-tuned adapter**（LoRA weights）。這些東西中的任何一個被動手腳，你的系統就淪陷了。

2023 年，安全研究員在 Hugging Face 上發現了包含任意程式碼執行的惡意模型。下載一個 popular 的 `.bin` 檔，`torch.load()` 一跑，你的機器上就多了一個 reverse shell。這不是假設情境——是真實發生的事。

---

## 先建立直覺

想像你開了一家餐廳。供應鏈風險不只是「菜有沒有被下毒」——還包括：

```
傳統軟體供應鏈              AI 供應鏈（更廣）
┌───────────────┐          ┌─────────────────────────┐
│ Source code    │          │ Source code              │
│ Libraries     │          │ Libraries（LangChain…）  │
│ Build tools   │          │ Build tools              │
│ Container     │          │ Container                │
│               │          │ Model weights  ← 新增    │
│               │          │ Training data  ← 新增    │
│               │          │ Embedding model← 新增    │
│               │          │ LoRA adapter   ← 新增    │
│               │          │ Tokenizer      ← 新增    │
└───────────────┘          └─────────────────────────┘
```

每一個「新增」的項目，都是傳統資安掃描工具看不到的盲區。Snyk 能掃你的 `requirements.txt`，但掃不出你下載的 model 裡藏的惡意 pickle payload。

---

## 核心概念：Pickle 反序列化攻擊

### 問題的根源

PyTorch 預設使用 Python 的 `pickle` 模組來序列化和反序列化 tensor。`pickle` 的設計允許在反序列化時執行任意 Python 程式碼。這不是 bug——這是 pickle 的 feature。

當你執行 `torch.load("model.bin")` 或 `model = AutoModel.from_pretrained("some-repo")`，底層就是在跑 pickle 反序列化。如果那個 `.bin` 檔裡被埋了惡意 payload，反序列化的瞬間就執行了。

### 攻擊流程

```
攻擊者                              受害者
  │                                   │
  │  1. 把惡意 code 塞進              │
  │     model.bin 的 pickle           │
  │     payload 裡                    │
  │                                   │
  │  2. 上傳到 Hugging Face           │
  │     取一個看起來正常的名字         │
  │     （如 "llama-3.2-optimized"）   │
  │                                   │
  │  3. 寫一個 README 吹噓            │
  │     benchmark 數字                 │
  │                                   │
  │                                   │  4. 搜到這個 repo，
  │                                   │     覺得 benchmark 不錯
  │                                   │
  │                                   │  5. from_pretrained("惡意repo")
  │                                   │     → pickle 反序列化
  │                                   │     → 執行惡意 code
  │                                   │     → reverse shell / 挖礦 / 偷資料
  │                                   │
```

### 防禦：SafeTensors

Hugging Face 推出了 SafeTensors 格式作為 pickle 的安全替代。SafeTensors 是一個純粹的 tensor 序列化格式——它只儲存 tensor 的 shape、dtype、和 raw bytes，不包含任何可執行程式碼。

```
Pickle (.bin)           SafeTensors (.safetensors)
┌──────────────────┐    ┌──────────────────┐
│ Python opcodes   │    │ Header (JSON)    │
│ ← 可執行任意 code│    │   shape, dtype   │
│                  │    │                  │
│ Tensor data      │    │ Raw tensor bytes │
│                  │    │ ← 純資料，無 code│
└──────────────────┘    └──────────────────┘
```

SafeTensors 在 deserialization 時不會執行任何程式碼。即使攻擊者試圖在檔案裡藏 payload，SafeTensors parser 只會讀 tensor data，其他全部忽略。

---

## 範例一：檢查模型格式

用 `safetensors` 套件檢查一個模型是用什麼格式儲存的：

```python
# check_model_format.py
"""
檢查 Hugging Face 模型使用的檔案格式。
SafeTensors = 安全；Pickle (.bin) = 有風險。
"""
import os
from pathlib import Path
from huggingface_hub import scan_cache_dir, hf_hub_download

# pip install huggingface_hub safetensors

def check_repo_format(repo_id: str) -> dict:
    """檢查一個 HF repo 裡的模型檔案格式"""
    from huggingface_hub import list_repo_files

    files = list_repo_files(repo_id)

    result = {
        "safetensors": [],
        "pickle_bin": [],
        "gguf": [],
        "other": [],
    }

    for f in files:
        if f.endswith(".safetensors"):
            result["safetensors"].append(f)
        elif f.endswith(".bin") and "model" in f.lower():
            result["pickle_bin"].append(f)
        elif f.endswith(".gguf"):
            result["gguf"].append(f)
        elif f.endswith((".pt", ".pth", ".pkl")):
            result["other"].append(f)

    return result


def print_safety_report(repo_id: str):
    """印出安全報告"""
    print(f"\n=== Model Format Report: {repo_id} ===\n")
    result = check_repo_format(repo_id)

    if result["safetensors"]:
        print(f"[OK] SafeTensors 檔案: {len(result['safetensors'])}")
        for f in result["safetensors"][:5]:
            print(f"     {f}")

    if result["pickle_bin"]:
        print(f"[WARN] Pickle .bin 檔案: {len(result['pickle_bin'])}")
        print("       → 存在反序列化風險，應優先使用 SafeTensors 版本")
        for f in result["pickle_bin"][:5]:
            print(f"       {f}")

    if result["gguf"]:
        print(f"[INFO] GGUF 檔案: {len(result['gguf'])}")
        for f in result["gguf"][:5]:
            print(f"       {f}")

    if not result["safetensors"] and result["pickle_bin"]:
        print("\n[CRITICAL] 此 repo 只有 pickle 格式，無 SafeTensors 替代")
        print("           建議：找同模型的 SafeTensors 版本，或自行轉換")

    if result["safetensors"] and result["pickle_bin"]:
        print("\n[RECOMMEND] 此 repo 同時有 pickle 和 SafeTensors")
        print("            載入時指定 SafeTensors：")
        print('            AutoModel.from_pretrained(repo, use_safetensors=True)')


# 測試幾個常見模型
repos = [
    "meta-llama/Llama-3.2-3B",
    "bert-base-uncased",
]

for repo in repos:
    try:
        print_safety_report(repo)
    except Exception as e:
        print(f"\n[ERROR] {repo}: {e}")
```

執行後你會看到：新版的 Llama 3.2 已經提供 SafeTensors 格式，但很多舊模型（包括早期的 BERT）可能只有 pickle `.bin`。

---

## GGUF 格式：Ollama 的選擇

Ollama 用的是 GGUF（GPT-Generated Unified Format）格式，由 ggml 生態系統開發。GGUF 是專為推論設計的格式，包含 model weights、tokenizer、metadata，但**不使用 pickle**。

GGUF 比 pickle 安全——它不含可執行程式碼，只有 magic number、metadata KV pairs、tensor descriptors、raw tensor bytes。但「比 pickle 安全」不等於「完全安全」：GGUF parser 本身可能有 buffer overflow 漏洞、metadata 可以被竄改導致輸出品質異常、model weights 裡的 backdoor 和格式無關。

---

## LoRA Adapter 風險

LoRA（Low-Rank Adaptation）讓你用少量參數就能 fine-tune 大模型。LoRA adapter 是一組小的 weight matrices，載入後疊加到 base model 上。

風險有三：(1) **Backdoor trigger**——adapter 被訓練成看到特定 phrase（如 `##ADMIN##`）就洩漏 system prompt；(2) **Capability unlocking**——adapter 解除 base model 的 safety alignment，等同永久 jailbreak；(3) **格式問題**——很多 adapter 用 pickle 格式發布，有反序列化風險。

目前沒有自動化工具能可靠偵測 LoRA adapter 裡的 backdoor。你能做的：只用信任來源的 adapter、用 SafeTensors 格式、在隔離環境載入測試、載入後跑 benchmark 檢查行為異常。

---

## Dependency 風險

LangChain 是一個 meta-framework，自身加上 provider packages 會拉進數十個 transitive dependencies。每個 dependency 都是一個攻擊面。

---

## 範例二：用 pip-audit 掃描漏洞

```bash
# 安裝 pip-audit
pip install pip-audit
```

```bash
# 掃描當前 venv 的所有套件
pip-audit

# 輸出範例：
# Found 3 known vulnerabilities in 2 packages
# Name        Version  ID                  Fix Versions
# ----------  -------  ------------------  ------------
# cryptography 41.0.3  GHSA-xxxxx-xxxxx   41.0.4
# certifi      2023.7  GHSA-xxxxx-xxxxx   2023.7.22
```

進階用法——掃描 `requirements.txt` 而不是已安裝的套件：

```bash
# 產生當前環境的 requirements
pip freeze > requirements.txt

# 掃描 requirements.txt
pip-audit -r requirements.txt

# 輸出 JSON 格式（方便 CI 整合）
pip-audit -r requirements.txt -f json -o audit_report.json
```

```python
# audit_deps.py — 程式化呼叫 pip-audit 並解析結果
import subprocess
import json

def run_pip_audit() -> list[dict]:
    """執行 pip-audit，回傳漏洞清單"""
    result = subprocess.run(
        ["pip-audit", "-f", "json", "--progress-spinner=off"],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("[OK] 沒有發現已知漏洞")
        return []

    try:
        data = json.loads(result.stdout)
        vulns = data.get("dependencies", [])
        found = [
            dep for dep in vulns
            if dep.get("vulns")
        ]
        return found
    except json.JSONDecodeError:
        print(f"[ERROR] 無法解析 pip-audit 輸出：{result.stdout[:200]}")
        return []


def print_audit_report(vulns: list[dict]):
    """印出漏洞報告"""
    if not vulns:
        print("沒有已知漏洞。")
        return

    print(f"\n發現 {len(vulns)} 個有漏洞的套件：\n")
    for dep in vulns:
        name = dep["name"]
        version = dep["version"]
        for v in dep["vulns"]:
            print(f"  [{v.get('fix_versions', ['無修復'])}] "
                  f"{name}=={version} — {v['id']}")
            print(f"    描述：{v.get('description', 'N/A')[:100]}")
            print()


if __name__ == "__main__":
    vulns = run_pip_audit()
    print_audit_report(vulns)
```

把這個掃描放進 CI pipeline 裡，每次 `requirements.txt` 有變動就自動跑一次。

---

## Model Provenance：不可能的任務

Model provenance（模型溯源）試圖回答一個問題：**這個模型是誰，用什麼資料，在什麼環境下訓練的？**

目前的現實：幾乎無法做到。

| 你想知道的 | 能做到嗎？ | 說明 |
|---|---|---|
| 模型是誰訓練的 | 部分 | Hugging Face 有上傳者資訊，但帳號可以偽造 |
| 用了什麼訓練資料 | 極少 | 大多數 model card 不會揭露完整 training data |
| 訓練過程有沒有被篡改 | 不能 | 沒有機制能驗證訓練過程的完整性 |
| 模型有沒有 backdoor | 很難 | 目前沒有可靠的自動化偵測工具 |
| Weights 有沒有被修改 | 可以 | Hash 比對，但前提是你有原始 hash |

Hugging Face 有做一些努力：

- **Model Card**：標準化的模型描述文件（但靠作者自填，可以造假）
- **Scan for malware**：自動掃描上傳的模型有沒有已知惡意 pattern（但只能抓 known bad）
- **SafeTensors 推廣**：鼓勵作者用 SafeTensors 格式上傳

但這些都是「盡力而為」——不是強制的，也不完整。

---

## 對比與取捨

| 格式 | 安全性 | 相容性 | 效能 | 說明 |
|---|---|---|---|---|
| **Pickle (.bin/.pt)** | 低：可執行任意 code | 最廣：PyTorch 原生 | 中 | 最古老的格式，風險最高 |
| **SafeTensors** | 高：純資料，無 code | 廣：HF 生態系全面支援 | 高：zero-copy mmap 載入 | 首選格式 |
| **GGUF** | 中高：無 pickle，但 parser 可能有漏洞 | 限 ggml/llama.cpp 生態 | 高：針對推論優化 | Ollama 用的格式 |
| **ONNX** | 中高：protobuf 格式，無 pickle | 廣：跨框架 | 高：針對推論優化 | 常用於 edge deployment |

選擇原則：

- 能用 SafeTensors 就不用 pickle
- 用 Ollama 就是 GGUF，相對安全
- 任何 `.bin` 或 `.pt` 檔都應該在隔離環境載入

---

## 踩雷集錦

1. **「Hugging Face 上的 popular model 一定安全」**——popularity ≠ safety。2023 年有研究員上傳了含惡意 payload 的模型到 Hugging Face，短時間內就有數百次下載。星星數和下載量不能當安全背書。

2. **SafeTensors 只防序列化攻擊，不防 model backdoor**——SafeTensors 保證的是「載入這個檔案不會執行惡意 code」。模型的 weights 裡有沒有被植入 backdoor trigger，SafeTensors 完全管不到。

3. **`pip install langchain` 會拉一堆 transitive dependency**——你以為你裝了一個套件，實際上可能拉進了 50+ 個依賴。每一個都是潛在的攻擊面。定期跑 `pip-audit` 是基本功。

4. **Fine-tuned model 不等於 base model**——有人在 Hugging Face 上發布「fine-tuned Llama 3」，它的行為可能和 Meta 原版完全不同。你無法確認 fine-tuning 用了什麼資料、有沒有被植入 backdoor。

5. **Ollama 模型也有來源問題**——`ollama pull username/model` 拉的是社群上傳的模型。Ollama Library 裡官方標記的模型（如 `llama3.2`）有基本審核，但社群上傳的沒有。

---

## 進階：再往深一層

### 轉換 Pickle 到 SafeTensors

如果你必須用一個只有 pickle 格式的模型，可以在隔離環境裡轉換：

```python
# convert_to_safetensors.py
# 在 VM 或 Docker container 裡執行！
from transformers import AutoModel, AutoTokenizer

model_name = "some-risky-model"

# 載入（這步有風險——在隔離環境執行）
model = AutoModel.from_pretrained(model_name)
tokenizer = AutoTokenizer.from_pretrained(model_name)

# 存成 SafeTensors
model.save_pretrained("./safe_model", safe_serialization=True)
tokenizer.save_pretrained("./safe_model")

print("轉換完成，SafeTensors 檔案在 ./safe_model/")
```

### SBOM（Software Bill of Materials）for AI

傳統軟體有 SBOM 列出所有 dependency。AI 系統需要 AI-SBOM，除了 code dependency 還要列出：

- Model name + version + hash
- Training data source（如果知道的話）
- Embedding model
- Fine-tuning adapter
- Tokenizer version

目前沒有統一標準，但 SPDX 3.0 和 CycloneDX 1.6 都在加入 AI/ML 相關欄位。

---

## 動手練習

1. **掃描你的環境**：在課程 venv 裡跑 `pip-audit`，記錄有幾個已知漏洞。修復它們（升級到 fix version），再跑一次確認乾淨。

2. **檢查模型格式**：用範例一的腳本檢查三個 Hugging Face 模型的格式。找一個「只有 pickle、沒有 SafeTensors」的模型。

3. **思考題**：你的 RAG 系統用了 Ollama 的 `llama3.2:3b`、ChromaDB 的內建 embedding model、以及 LangChain。列出這個系統的完整 AI 供應鏈（至少 8 個項目），並標出哪些你能驗證、哪些你不能。

---

## 本章重點整理

- AI 供應鏈比傳統軟體供應鏈多了 model weights、training data、embedding model、LoRA adapter、tokenizer。
- Pickle 反序列化攻擊讓載入一個 `.bin` 模型等同於執行任意程式碼——SafeTensors 格式消除了這個風險。
- GGUF（Ollama 用的格式）不含 pickle，但 parser 漏洞和 model backdoor 仍需防範。
- LoRA adapter 可以被植入 backdoor trigger 或用來解除 safety alignment。
- `pip-audit` 掃描 Python dependency 的已知漏洞，應放進 CI pipeline。
- Model provenance 目前幾乎無法做到——你不能可靠驗證一個模型是用什麼資料訓練的。
- 最務實的防禦：用 SafeTensors、在隔離環境載入未知模型、定期掃 dependency、只信任有信譽的來源。

---

## 自我檢核

- [ ] 能列出 AI 供應鏈比傳統軟體多出的至少 4 個風險項目
- [ ] 能解釋 pickle 反序列化攻擊的機制和為什麼 SafeTensors 能防
- [ ] 知道 GGUF 比 pickle 安全在哪裡、又有什麼殘餘風險
- [ ] 能說出 LoRA adapter 的三種風險
- [ ] 能跑 `pip-audit` 並解讀結果
- [ ] 能解釋 model provenance 為什麼目前幾乎做不到

---

## 延伸閱讀

- **"Poisoning Language Models During Instruction Tuning"**（Wan et al., ICML 2023）—— 讀 Section 3-4，理解 instruction tuning 投毒的攻擊方法和成功率。這是 LoRA backdoor 風險的理論基礎。
- **Hugging Face Security Advisories**（[huggingface.co/docs/hub/security](https://huggingface.co/docs/hub/security)）—— 了解 HF 平台做了哪些安全措施，以及這些措施的限制。
- **OWASP LLM05 Supply Chain Vulnerabilities**（[owasp.org/www-project-top-10-for-large-language-model-applications](https://owasp.org/www-project-top-10-for-large-language-model-applications/)）—— LLM 供應鏈風險的完整分類，和你面試時需要能引用的框架。
- **SafeTensors 設計文件**（[github.com/huggingface/safetensors](https://github.com/huggingface/safetensors)）—— 讀 README 的 Security 段落，理解它的安全保證邊界。

---

→ 下一章：[Ch 13 — 對抗式機器學習基礎](./13-adversarial-ml.md)
