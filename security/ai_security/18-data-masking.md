# Ch 18 — Data Masking 實作

> 目標：用 Microsoft Presidio 在 RAG pipeline 的兩個關鍵點遮蔽 PII（個人識別資訊），並理解替換策略與中文的額外挑戰。

---

## 什麼是 Data Masking

資料遮蔽（Data Masking）是把敏感資訊在進入不可信系統（LLM API、向量 DB）之前替換掉，回傳結果時再還原。

```
使用者輸入：「幫我查王小明（0912-345-678）的合約狀態」
      |
      v
[Masking Layer]  偵測 PII，替換為 token
      |
      v
送給 LLM：「幫我查 <PERSON_1>（<PHONE_NUMBER_1>）的合約狀態」
      |
      v
LLM 回傳：「<PERSON_1> 的合約在 2025-03-01 到期」
      |
      v
[Unmasking Layer]  還原 token
      |
      v
使用者看到：「王小明 的合約在 2025-03-01 到期」
```

核心價值：LLM API（含第三方）永遠看不到真實 PII，即使 API 被攻擊或 logs 被讀，損失有限。

---

## 兩個遮蔽層次

| 時機 | 名稱 | 目的 |
|---|---|---|
| 文件入庫前 | Indexing-time masking | 向量 DB 和 embedding model 看不到原始 PII |
| 查詢送出前 | Query-time masking | LLM API 看不到使用者輸入裡的 PII |

兩個都要做。只做其中一個：

- 只做 query-time：向量 DB 裡的 chunk 還是帶著原始 PII，RAG 召回的 context 會把 PII 送給 LLM
- 只做 indexing-time：使用者在 query 裡帶的 PII 還是直送 LLM

---

## Microsoft Presidio 安裝與基本使用

```bash
pip install presidio-analyzer presidio-anonymizer
# 下載英文 NLP 模型
python -m spacy download en_core_web_lg
```

Presidio 分兩個元件：

- `presidio-analyzer`：偵測文字中的 PII，回傳 entity type + 位置
- `presidio-anonymizer`：根據 analyzer 結果執行替換操作

### Presidio 支援的 Entity 類型（部分）

| Entity Type | 說明 |
|---|---|
| `EMAIL_ADDRESS` | 電子郵件 |
| `PHONE_NUMBER` | 電話號碼 |
| `CREDIT_CARD` | 信用卡號 |
| `PERSON` | 人名 |
| `NRP` | 國家識別碼（身分證） |
| `IBAN_CODE` | 銀行帳號 |
| `IP_ADDRESS` | IP 位址 |
| `LOCATION` | 地名 |
| `DATE_TIME` | 日期時間（可選） |

---

## 完整範例：偵測 + 替換 + 還原

```python
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

analyzer  = AnalyzerEngine()
anonymizer = AnonymizerEngine()

text = "Please contact John Smith at john.smith@example.com or call 0912-345-678."

# Step 1: 偵測 PII
results = analyzer.analyze(
    text=text,
    language="en",
    entities=["PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER"],
)

print("偵測結果：")
for r in results:
    print(f"  {r.entity_type}: '{text[r.start:r.end]}' (score={r.score:.2f})")

# Step 2: 替換（使用 token 模式，可還原）
operators = {
    "PERSON":        OperatorConfig("replace", {"new_value": "<PERSON>"}),
    "EMAIL_ADDRESS": OperatorConfig("replace", {"new_value": "<EMAIL>"}),
    "PHONE_NUMBER":  OperatorConfig("replace", {"new_value": "<PHONE>"}),
    # 用 hash 模式：不可還原但保留格式一致性
    # "CREDIT_CARD": OperatorConfig("hash", {"hash_type": "sha256"}),
}

anonymized = anonymizer.anonymize(
    text=text,
    analyzer_results=results,
    operators=operators,
)

print(f"\n遮蔽後：{anonymized.text}")
# 輸出：Please contact <PERSON> at <EMAIL> or call <PHONE>.
```

### 帶編號 token 的可還原映射

如果同一段文字有多個不同的人名，需要帶編號才能正確還原：

```python
from collections import defaultdict

class MappingAnonymizer:
    def __init__(self):
        self.analyzer  = AnalyzerEngine()
        self.anonymizer = AnonymizerEngine()

    def anonymize(self, text: str) -> tuple[str, dict]:
        """回傳 (遮蔽後文字, 還原映射表)"""
        results = self.analyzer.analyze(text=text, language="en")
        
        mapping = {}
        counters = defaultdict(int)
        operators = {}

        # 依 entity 類型分配編號
        for r in sorted(results, key=lambda x: x.start):
            original = text[r.start:r.end]
            if original not in mapping:
                counters[r.entity_type] += 1
                token = f"<{r.entity_type}_{counters[r.entity_type]}>"
                mapping[token] = original

        # 建立 operators（Presidio 不直接支援動態 token，用 replace 後再後處理）
        anonymized_text = anonymizer.anonymize(
            text=text,
            analyzer_results=results,
            operators={"DEFAULT": OperatorConfig("replace", {"new_value": "PLACEHOLDER"})},
        ).text

        # 簡化版：直接字串替換（適合示範）
        for token, original in mapping.items():
            anonymized_text = anonymized_text.replace("PLACEHOLDER", token, 1)

        return anonymized_text, mapping

    def deanonymize(self, text: str, mapping: dict) -> str:
        """根據映射表還原"""
        for token, original in mapping.items():
            text = text.replace(token, original)
        return text

# 使用
anon = MappingAnonymizer()
masked, mapping = anon.anonymize("Alice emailed Bob at bob@corp.com")
print(masked)   # Alice emailed <PERSON_1> at <EMAIL_ADDRESS_1>
response = "I found <PERSON_1>'s account linked to <EMAIL_ADDRESS_1>."
print(anon.deanonymize(response, mapping))
```

---

## 遮蔽策略選擇

| 策略 | 操作 | 可還原 | 適用場景 |
|---|---|---|---|
| Token 替換 | `<PERSON_1>` | 是（需保存映射表） | Query-time，需要還原給使用者看 |
| 合成假資料 | 換成另一個假名字 | 否（不需要） | Indexing-time，不需還原 |
| Hash | SHA-256 截斷 | 否 | 只需一致性比對，不需還原 |
| Redact | `[REDACTED]` | 否 | Audit log、不可逆遮蔽 |
| Mask | `****` | 否 | 信用卡號顯示 |

Indexing-time 推薦用合成假資料（`fake` operator），讓文件讀起來自然，
embedding 的語義不會因為全是 `<TOKEN>` 而失真。

```python
from presidio_anonymizer.entities import OperatorConfig
from faker import Faker

fake = Faker()

operators = {
    "PERSON": OperatorConfig("custom", {
        "lambda": lambda x: fake.name()
    }),
    "EMAIL_ADDRESS": OperatorConfig("custom", {
        "lambda": lambda x: fake.email()
    }),
}
```

---

## 中文 PII 的挑戰

Presidio 預設只有英文 NLP 模型，中文支援需要自訂。

### 問題點

- `spaCy` 中文模型（`zh_core_web_sm`）對人名辨識精確度不高
- 台灣身分證號（`A123456789`）Presidio 的 NRP recognizer 可能不符
- 中文電話號碼格式（`0912-345-678`、`02-1234-5678`）需要自訂 regex

### 自訂 Pattern Recognizer

```python
from presidio_analyzer import PatternRecognizer, Pattern

# 台灣手機號碼
tw_phone_pattern = Pattern(
    name="TW_PHONE",
    regex=r"09\d{2}[-\s]?\d{3}[-\s]?\d{3}",
    score=0.85,
)
tw_phone_recognizer = PatternRecognizer(
    supported_entity="TW_PHONE_NUMBER",
    patterns=[tw_phone_pattern],
    supported_language="zh",  # 或 "en"，Presidio 語言標籤
)

# 台灣身分證號
tw_id_pattern = Pattern(
    name="TW_NID",
    regex=r"[A-Z][12]\d{8}",
    score=0.9,
)
tw_id_recognizer = PatternRecognizer(
    supported_entity="TW_NATIONAL_ID",
    patterns=[tw_id_pattern],
)

# 加入 analyzer
analyzer = AnalyzerEngine()
analyzer.registry.add_recognizer(tw_phone_recognizer)
analyzer.registry.add_recognizer(tw_id_recognizer)
```

中文人名目前最可行的做法是結合 `ckip-transformers`（中研院斷詞工具）做 NER，
取出人名後再讓 Presidio 做替換。

---

## 整合進 RAG Pipeline

```python
from langchain.document_loaders import DirectoryLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from presidio_analyzer import AnalyzerEngine
from presidio_anonymizer import AnonymizerEngine

def build_rag_with_masking(docs_path: str, vectorstore):
    analyzer  = AnalyzerEngine()
    anonymizer = AnonymizerEngine()

    def mask_document(text: str) -> str:
        results = analyzer.analyze(text=text, language="en")
        if not results:
            return text
        return anonymizer.anonymize(
            text=text,
            analyzer_results=results,
            operators={"DEFAULT": OperatorConfig("replace", {"new_value": "[REDACTED]"})},
        ).text

    # 1. 載入文件
    loader   = DirectoryLoader(docs_path)
    raw_docs = loader.load()

    # 2. 遮蔽（indexing-time masking），在 split 之前做
    for doc in raw_docs:
        doc.page_content = mask_document(doc.page_content)

    # 3. 分段
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    chunks   = splitter.split_documents(raw_docs)

    # 4. 入庫（embedding model 和向量 DB 看到的已是遮蔽後的文字）
    vectorstore.add_documents(chunks)
    return vectorstore
```

重點：遮蔽要在 `split` 之前做，否則 PII 可能橫跨兩個 chunk 而偵測不到。

---

## 自我檢核

- [ ] 能說出 indexing-time 和 query-time 兩個遮蔽層各自防什麼
- [ ] 能安裝 Presidio 並跑出偵測 + 替換的完整範例
- [ ] 知道四種遮蔽策略（token / 合成 / hash / redact）的取捨
- [ ] 能寫出台灣手機號碼與身分證號的自訂 Pattern Recognizer
- [ ] 理解為何遮蔽要在文件切段之前做
- [ ] 知道中文人名辨識需要額外工具（CKIP 或自訂 NER）

RAG pipeline 現在知道怎麼擋 PII 了，下一個問題是：不同身份的使用者能看到哪些文件？

→ [Ch 19 RAG 存取控制設計](./19-rag-access-control.md)
