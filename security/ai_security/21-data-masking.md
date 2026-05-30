# Ch 21 — Data Masking 與 PII 偵測

> **目標**：能用 Microsoft Presidio 和自建 regex 做 PII 偵測與遮蔽，理解 data masking 在 LLM pipeline 中的三個插入點。
>
> **環境**：Python 3.11, LangChain 0.3.x, Ollama + llama3.2:3b, ChromaDB, Ubuntu 22.04

---

## 為什麼需要這個？

LLM 是一台「什麼都願意回答的影印機」。使用者在 prompt 裡打了信用卡號，LLM 不會說「這是敏感資訊，我不幫你處理」——它會照收。更糟的是，如果 RAG 知識庫裡有含 PII（Personally Identifiable Information，個人可識別資訊）的文件，LLM 可能在回答裡直接引用這些 PII。

三個洩漏路徑：

1. **使用者 → LLM**：使用者在 prompt 裡輸入 PII（「幫我分析這份含有身分證號的資料」）
2. **LLM → 使用者**：LLM 在 response 裡洩漏 RAG 知識庫中的 PII（「根據文件，員工 A 的手機號碼是 0912-345-678」）
3. **文件 → RAG**：含 PII 的文件被 ingest 進向量資料庫，任何 query 都可能撈到

GDPR（General Data Protection Regulation，通用資料保護規範）第 25 條要求「Data Protection by Design」——你不能事後補救，必須在設計階段就把 PII 保護內建到 pipeline 裡。Data masking 是實現這個要求的核心技術。

---

## 先建立直覺

Data masking 就是把敏感資訊「塗黑」。你看過法院文件裡被黑色方塊遮住的段落嗎？同樣的事，用程式自動做。

```
原始文本：
  「員工張三的手機號碼是 0912-345-678，
    身分證號 A123456789，
    信用卡 4111-1111-1111-1111。」

遮蔽後：
  「員工 [PERSON] 的手機號碼是 [PHONE_NUMBER]，
    身分證號 [TW_ID]，
    信用卡 [CREDIT_CARD]。」
```

聽起來不難？難的地方在三個問題：

- **怎麼找到 PII**：email 和信用卡有固定格式，但人名、地址沒有——需要 NLP
- **遮蔽到什麼程度**：全部替換成 `[REDACTED]` 會讓 LLM 無法理解上下文；留太多線索又不安全
- **要不要能還原**：有些場景需要遮蔽後還能解密回原文（reversible masking）

---

## 核心概念：三個插入點

### 範例一：用 Microsoft Presidio 偵測 PII

先安裝 Presidio：

```bash
pip install presidio-analyzer presidio-anonymizer
python -m spacy download en_core_web_lg
```

Presidio 由兩個元件組成：

- **Analyzer**：偵測文本中的 PII entity
- **Anonymizer**：對偵測到的 entity 做遮蔽處理

```python
# presidio_detect.py — PII 偵測
from presidio_analyzer import AnalyzerEngine

analyzer = AnalyzerEngine()

text = """
Dear Mr. John Smith,
Your credit card 4111-1111-1111-1111 has been charged $500.
Please contact us at john.smith@company.com or call 555-123-4567.
Your SSN 123-45-6789 is on file.
IP address: 192.168.1.100
"""

results = analyzer.analyze(
    text=text,
    language="en",
    entities=None,  # None = 偵測所有已知類型
)

print(f"Found {len(results)} PII entities:\n")
for result in sorted(results, key=lambda x: x.start):
    entity_text = text[result.start:result.end]
    print(f"  [{result.entity_type}] "
          f"'{entity_text}' "
          f"(confidence: {result.score:.2f}, "
          f"pos: {result.start}-{result.end})")
```

輸出：

```
Found 6 PII entities:

  [PERSON] 'John Smith' (confidence: 0.85, pos: 10-20)
  [CREDIT_CARD] '4111-1111-1111-1111' (confidence: 1.00, pos: 43-62)
  [EMAIL_ADDRESS] 'john.smith@company.com' (confidence: 1.00, pos: 107-129)
  [PHONE_NUMBER] '555-123-4567' (confidence: 0.75, pos: 138-150)
  [US_SSN] '123-45-6789' (confidence: 0.85, pos: 161-172)
  [IP_ADDRESS] '192.168.1.100' (confidence: 0.60, pos: 189-202)
```

注意 confidence score：CREDIT_CARD 和 EMAIL_ADDRESS 用 regex 匹配，confidence 高；PERSON 靠 NLP NER（Named Entity Recognition，命名實體辨識），confidence 較低。

---

## 底層機制：Presidio 的偵測 Pipeline

```
                        輸入文本
                           │
                           ▼
              ┌─────────────────────────┐
              │   Presidio Analyzer     │
              │                         │
              │  ┌───────────────────┐  │
              │  │ Predefined        │  │
              │  │ Recognizers       │  │
              │  │                   │  │
              │  │ • CreditCard      │◄─── Regex: Luhn 校驗 + 格式
              │  │ • Email           │◄─── Regex: RFC 5322
              │  │ • Phone           │◄─── Regex: 各國格式
              │  │ • SSN             │◄─── Regex: \d{3}-\d{2}-\d{4}
              │  │ • IP Address      │◄─── Regex: IPv4/IPv6
              │  └───────────────────┘  │
              │                         │
              │  ┌───────────────────┐  │
              │  │ NLP Recognizer    │  │
              │  │                   │  │
              │  │ • Person Name     │◄─── spaCy NER model
              │  │ • Location        │◄─── spaCy NER model
              │  │ • Organization    │◄─── spaCy NER model
              │  └───────────────────┘  │
              │                         │
              │  ┌───────────────────┐  │
              │  │ Custom            │  │
              │  │ Recognizers       │◄─── 你自己寫的（見下節）
              │  └───────────────────┘  │
              │                         │
              │    所有 recognizer 並行跑 │
              │    → 合併結果            │
              │    → 去重疊             │
              │    → 按 confidence 排序  │
              └────────────┬────────────┘
                           │
                           ▼
                    List[RecognizerResult]
                           │
                           ▼
              ┌─────────────────────────┐
              │   Presidio Anonymizer   │
              │                         │
              │  對每個 entity 套用策略：│
              │  • Replace（替換）       │
              │  • Redact（刪除）        │
              │  • Hash（雜湊）          │
              │  • Encrypt（加密）       │
              │  • Mask（部分遮蔽）      │
              └────────────┬────────────┘
                           │
                           ▼
                      遮蔽後文本
```

關鍵設計：Presidio 不是一個 model，而是一個 pipeline。Regex recognizer 快且精準（但只能抓固定格式），NLP recognizer 慢但能抓自由格式的 entity（人名、地址）。兩者並行，結果合併。

---

## 進一步用法：自建 Recognizer + LLM Pipeline 整合

### 範例二：台灣身分證號 + 手機號碼的自建 Recognizer

Presidio 內建的 recognizer 以歐美格式為主。台灣的身分證號（A123456789）和手機號碼（09xx-xxx-xxx）不在預設清單裡。

```python
# tw_recognizer.py — 台灣 PII 自建 recognizer
from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

# 台灣身分證號：1 英文字母 + 第 2 位是 1 或 2 + 8 個數字
tw_id_recognizer = PatternRecognizer(
    supported_entity="TW_NATIONAL_ID",
    patterns=[Pattern("tw_id", r"\b[A-Z][12]\d{8}\b", score=0.85)],
    supported_language="en",
)

# 台灣手機號碼：09xx-xxx-xxx 或 09xxxxxxxx
tw_phone_recognizer = PatternRecognizer(
    supported_entity="TW_PHONE",
    patterns=[Pattern("tw_phone", r"\b09\d{2}[-]?\d{3}[-]?\d{3}\b", score=0.80)],
    supported_language="en",
)

# 註冊到 analyzer
analyzer = AnalyzerEngine()
analyzer.registry.add_recognizer(tw_id_recognizer)
analyzer.registry.add_recognizer(tw_phone_recognizer)

text = "身分證號：A123456789，手機：0912-345-678，Email：wang@company.com"
results = analyzer.analyze(text=text, language="en")

# 遮蔽
anonymizer = AnonymizerEngine()
anonymized = anonymizer.anonymize(
    text=text, analyzer_results=results,
    operators={
        "TW_NATIONAL_ID": OperatorConfig("replace", {"new_value": "[TW_ID]"}),
        "TW_PHONE": OperatorConfig("replace", {"new_value": "[TW_PHONE]"}),
        "DEFAULT": OperatorConfig("replace", {"new_value": "[PII]"}),
    },
)
print(anonymized.text)
```

輸出：`身分證號：[TW_ID]，手機：[TW_PHONE]，Email：[PII]`。注意如果文本裡有「王小明」，Presidio 不會偵測到——它的 NLP model 是英文的，對中文人名的 recall 極低。這是踩雷第一條。

### 整合到 LLM Pipeline：三個插入點

```python
# masking_pipeline.py — 在 LLM pipeline 三個點插入 masking
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage

analyzer = AnalyzerEngine()
anonymizer = AnonymizerEngine()

def mask_pii(text: str) -> str:
    """偵測並遮蔽 PII"""
    results = analyzer.analyze(text=text, language="en")
    if not results:
        return text
    return anonymizer.anonymize(text=text, analyzer_results=results).text

llm = ChatOllama(model="llama3.2", temperature=0)

# === 插入點 1: Input Masking ===
user_input = "我的信用卡號是 4111-1111-1111-1111，幫我查餘額"
masked_input = mask_pii(user_input)
print(f"[Input Masking] 原始: {user_input}")
print(f"[Input Masking] 遮蔽: {masked_input}")

# 送遮蔽後的 input 給 LLM
response = llm.invoke([
    SystemMessage(content="你是客服助理。"),
    HumanMessage(content=masked_input),
])

# === 插入點 2: Output Masking ===
raw_output = response.content
masked_output = mask_pii(raw_output)
print(f"\n[Output Masking] 原始: {raw_output[:200]}")
print(f"[Output Masking] 遮蔽: {masked_output[:200]}")

# === 插入點 3: Document Masking（RAG ingest 前）===
raw_document = """
HR 紀錄：
員工 John Doe，SSN 123-45-6789，
年薪 $85,000，直屬主管 jane.doe@company.com
"""
masked_document = mask_pii(raw_document)
print(f"\n[Document Masking] 原始:\n{raw_document}")
print(f"[Document Masking] 遮蔽:\n{masked_document}")
# 遮蔽後的文件才 embed 進向量 DB
```

---

## 對比與取捨

| 面向 | Presidio | 自建 Regex | AWS Comprehend | Google DLP |
|------|----------|-----------|----------------|------------|
| **PII 類型覆蓋** | 30+ entity types | 你寫幾種就幾種 | 50+ entity types | 150+ infoTypes |
| **中文支援** | 差（需自建 recognizer） | 你自己加 | 部分支援 | 部分支援 |
| **NLP 能力** | spaCy NER | 無 | 有（雲端 ML） | 有（雲端 ML） |
| **Self-hosted** | 是 | 是 | 否（AWS 雲端） | 否（GCP 雲端） |
| **延遲** | ~50ms / 段落 | <1ms / 段落 | ~200ms / API call | ~200ms / API call |
| **成本** | 免費 | 免費 | 按 API call 計費 | 按量計費 |
| **自訂 entity** | 支援（PatternRecognizer） | 天生就是自訂 | 支援 | 支援 |
| **適用場景** | self-hosted + 需要 NLP | 格式固定的 PII | 已在 AWS 生態 | 已在 GCP 生態 |

建議組合：**Presidio + 自建 regex** 覆蓋英文和台灣格式的 PII，self-hosted，零成本。對中文人名用 `ckip-transformers`（中研院 NLP 工具）補強。

---

## Reversible Masking：遮蔽後還能還原

有些場景需要遮蔽後還能還原。例如：你把使用者的 query 遮蔽後送進 LLM，拿到回答後需要把佔位符替換回原始值。

Presidio Anonymizer 支援 `encrypt` operator（底層用 Fernet），搭配 `DeanonymizeEngine` 做解密還原：

```python
# reversible_masking.py — 核心流程
from presidio_anonymizer import AnonymizerEngine, DeanonymizeEngine
from presidio_anonymizer.entities import OperatorConfig
from cryptography.fernet import Fernet

key = Fernet.generate_key()  # 這把 key 必須存在 secrets manager

# 加密遮蔽
encrypted = anonymizer.anonymize(
    text=text, analyzer_results=results,
    operators={"DEFAULT": OperatorConfig("encrypt", {"key": key.decode()})},
)

# 解密還原
deanonymizer = DeanonymizeEngine()
restored = deanonymizer.deanonymize(
    text=encrypted.text, entities=encrypted.items,
    operators={"DEFAULT": OperatorConfig("decrypt", {"key": key.decode()})},
)
```

重點：encryption key 必須存在獨立的 secrets manager（HashiCorp Vault、AWS Secrets Manager），不能和遮蔽後的文本放同一個資料庫——否則等於沒遮蔽。

---

## 踩雷集錦

**1. Presidio 對中文 PII 的 recall 極低**

Presidio 的 NLP backend 是 spaCy 的英文 model。它不認識「王小明」是人名、「台北市信義區」是地址。你需要：

- 用 `ckip-transformers` 做中文 NER，把結果餵給 Presidio 的 custom recognizer
- 或自建 regex 抓台灣格式的固定 PII（身分證號、手機、統一編號）

**2. Masking 不能用固定替換——攻擊者用 context 猜原始值**

如果你把所有人名都替換成 `***`，攻擊者從上下文可以推斷：「*** 是 CEO」→ 公司 CEO 只有一個人。更好的做法是用 fake data 替換：「王小明」→「陳大華」，保持語意結構但改掉身分。Presidio 支援 `fake` operator（需裝 `faker` 套件）。

**3. Over-masking 讓 LLM 無法正常回答**

```
使用者問：「[PERSON] 在 [LOCATION] 的分公司電話是什麼？」
LLM 回答：「我無法判斷您詢問的是哪位員工在哪個分公司。」
```

如果你把 query 裡的所有 entity 都遮蔽，LLM 根本無法回答。解法：只遮蔽高敏感度的 entity（SSN、credit card），保留低敏感度的（人名、地點）——但這需要一套 classification policy。

**4. Reversible masking 的 key 管理是另一個安全問題**

用 Fernet 加密做 reversible masking 很方便，但 encryption key 存在哪裡？如果 key 和遮蔽後的文本存在同一個資料庫裡，等於沒遮蔽。key 必須存在獨立的 secrets manager（如 HashiCorp Vault、AWS Secrets Manager）。

---

## 進階

### LLM-based PII Detection

Presidio 的限制是：它只能抓「已知類型」的 PII。如果文本裡有「我的帳號密碼是 abc123」——密碼不在 Presidio 的 entity 清單裡。新興做法是用 LLM 本身偵測 PII：給 LLM 一段 system prompt 列出要找的 PII 類型，讓它回傳 JSON 格式的偵測結果。優點是不需要預定義 entity 類型，能抓住 context-dependent 的 PII（如密碼）。缺點是延遲高、不確定性高。生產環境建議 Presidio（確定性、低延遲）+ LLM（補充偵測）雙層架構。

### Document Masking 在 RAG Ingest Pipeline 的位置

在 Load + Split 之後、Embed + Store 之前，插入 PII Detection + Masking。好處：即使向量 DB 被攻破，拿到的也是遮蔽後的文本。代價：如果使用者問的問題需要 PII 才能回答，LLM 無法給出完整答案。

---

## 動手練習

1. **Presidio 基礎**：用 Presidio Analyzer 對一段包含 email、phone、credit card 的英文文本做偵測，觀察每種 entity 的 confidence score。把 confidence threshold 從 0.0 調到 0.8，觀察哪些 entity 被過濾掉。

2. **台灣 PII recognizer**：自建 PatternRecognizer 處理台灣身分證號（`[A-Z][12]\d{8}`）和手機號碼（`09\d{2}-?\d{3}-?\d{3}`），在 Presidio Analyzer 上註冊，測試偵測準確度。

3. **三插入點整合**：在 Ch 3 的 RAG pipeline 裡，分別在 input、output、document ingest 三個點加入 Presidio masking。觀察 LLM 回答品質的變化——哪個插入點對回答品質影響最大？

4. **Over-masking 實驗**：把 Presidio 的 confidence threshold 調到 0.3（非常激進），觀察有多少正常文字被誤判為 PII。記錄 false positive 的類型。

---

## 重點整理

- LLM pipeline 的三個 PII 洩漏路徑：使用者 input、LLM output、RAG 知識庫文件。
- Data masking 的三個插入點：input masking、output masking、document masking。各有適用場景和代價。
- Microsoft Presidio = regex recognizer（快、精準、固定格式）+ NLP recognizer（慢、靈活、自由格式）。
- Presidio 對中文 PII 的 recall 極低——台灣場景必須自建 recognizer。
- Reversible masking 用加密實作，但 key 管理是額外的安全問題。
- Over-masking 讓 LLM 無法正常回答——需要一套 classification policy 決定哪些 entity 該遮蔽。
- 生產環境建議 Presidio（確定性、低延遲）+ LLM（補充偵測 context-dependent PII）雙層架構。

---

## 自我檢核

- 說出 LLM pipeline 中 PII 洩漏的三條路徑。
- Presidio Analyzer 的 regex recognizer 和 NLP recognizer 各自的優缺點是什麼？
- 為什麼 Presidio 對「王小明」這種中文人名的偵測 recall 低？你會怎麼補強？
- 解釋 over-masking 的問題。舉一個具體的場景。
- Reversible masking 和 irreversible masking 分別適合什麼場景？
- 如果你只能在三個插入點中選一個，你選哪個？為什麼？

---

## 延伸閱讀

### 工具文件

- **[Microsoft Presidio](https://github.com/microsoft/presidio)**
  - **讀哪裡**：docs/samples 目錄，特別是 custom recognizer 的範例
  - **學什麼**：如何寫 PatternRecognizer 和 EntityRecognizer，以及如何跟 LangChain 整合

### 法規

- **GDPR Article 25 — Data Protection by Design and by Default**
  - **讀哪裡**：條文本身（只有一頁），加上 EDPB（European Data Protection Board）的 guidance
  - **學什麼**：為什麼 data masking 不是 nice-to-have 而是法律要求

### 論文

- **"Protecting Privacy in Language Models: A Survey"**（2023）
  - **讀哪裡**：Section 3（Privacy Attacks on LLMs）和 Section 4（Defenses）
  - **學什麼**：LLM 的隱私攻擊全景圖——training data extraction、membership inference、PII leakage

---

→ [Ch 22 — RAG 存取控制設計](./22-rag-access-control.md)
