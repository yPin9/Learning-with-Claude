# Ch 20 — Evaluation:沒 eval 的 LLM app 是玩具

> 目標:把「感覺對」變成「可量化」。建一個能每天跑、每次改 prompt / 換模型都能跑的 eval pipeline。

## 沒 eval = 沒產品

LLM app 沒 eval 會發生什麼:

- 你改 prompt,不知道變好變壞
- 換模型,不知道 regression
- Prod 出 bug,不能 reproduce
- 想 A/B test 兩版,沒 metric 比
- LLM 廠商釋出新模型,你不敢 migrate

**Eval 不是 nice-to-have,是 LLM app 的單元測試 + 整合測試**。

---

## Eval 的三層

### 層 1:Unit tests of deterministic parts

LLM 輸出會變,但你的 pipeline 有很多**確定性**部分:

- JSON parsing
- Retrieval metadata filter
- Tool 執行本身
- 前後處理

這些寫**普通 unit test**。`pytest` 就好,跟 LLM 一點關係沒有。

### 層 2:LLM behavior tests(semi-deterministic)

LLM 輸出,但你檢查**它滿足某些不變性**:

- 答案**必包含** X 關鍵字
- 答案**不能包含** Y(禁用詞、PII)
- 輸出**符合 JSON schema**
- 回應長度在某範圍

這類叫 **assertion-based eval**。

```python
def test_no_hallucinated_name():
    resp = our_app("Who is the CEO of Acme Corp?")
    known_ceos = ["Alice Tan"]
    assert any(ceo in resp for ceo in known_ceos), f"Unexpected: {resp}"

def test_output_is_valid_json():
    resp = our_app("List 3 products")
    json.loads(resp)   # no exception
```

### 層 3:Quality-based eval(subjective)

**「這回答好不好?」**——沒有 0/1 正確,要打分。

兩種做法:

- **Human eval**:真人打分。最準,最貴、最慢。
- **LLM-as-judge**:讓另一個 LLM 打分。便宜、快,有 bias 要注意。

**Production 通常**:
- Human eval 建 golden set(50–200 cases)
- LLM-as-judge daily / per-deploy
- Human 定期 re-calibrate(每季 1 次)

---

## 建你的 first eval pipeline(最小可行)

### Step 1:收集 20 筆 golden examples

```python
# golden.json
[
    {
        "query": "What's our refund policy?",
        "expected_keywords": ["30 days", "original payment"],
        "must_not_contain": ["no refunds"],
        "notes": "Standard refund question"
    },
    ...
]
```

**來源**:

- 真實 user queries(anonymized)
- 你自己想的 edge case
- Prod 出過問題的 case(回歸 test)

### Step 2:寫 eval runner

```python
import json
from my_app import answer_query

def run_eval(golden_file="golden.json"):
    golden = json.load(open(golden_file))
    results = []
    for case in golden:
        actual = answer_query(case["query"])
        checks = {
            "has_keywords": all(k.lower() in actual.lower() for k in case.get("expected_keywords", [])),
            "no_forbidden": not any(k.lower() in actual.lower() for k in case.get("must_not_contain", [])),
        }
        results.append({
            "query": case["query"],
            "actual": actual,
            "checks": checks,
            "pass": all(checks.values())
        })

    pass_rate = sum(1 for r in results if r["pass"]) / len(results)
    return results, pass_rate

results, rate = run_eval()
print(f"Pass rate: {rate:.1%}")
for r in results:
    if not r["pass"]:
        print(f"FAILED: {r['query']}")
        print(f"  actual: {r['actual'][:200]}")
```

### Step 3:加進 CI

```yaml
# .github/workflows/eval.yml
name: Eval
on: [pull_request]
jobs:
  eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - run: pip install -r requirements.txt
      - run: python run_eval.py
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

PR 會跑 eval,失敗就 block merge。**這是最基本的護城河**。

---

## LLM-as-Judge

### 基本 pattern

```python
def judge_helpfulness(query, answer):
    prompt = f"""Rate the following answer's helpfulness on a scale of 1-5.

    5 = Directly and completely answers the question
    4 = Answers most of the question
    3 = Partial answer, missing key info
    2 = Off-topic or unclear
    1 = Wrong, harmful, or useless

    Query: {query}
    Answer: {answer}

    Output JSON: {{"score": <1-5>, "reason": "<brief>"}}
    """
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return json.loads(resp.content[0].text)
```

### 設計 judge prompt 的原則

**1. Rubric 具體**

不要「rate 1-10 overall」。**每個分數要有具體準則**。否則 judge 的分數不穩。

**2. 用 structured output**

LLM-as-judge 用 tool call 強制 JSON schema(Ch 8 的技巧)。

**3. 避免 leakage**

Judge prompt 不要含「正確答案」的暗示,否則 judge 趨向 match 那暗示。

**4. Judge model 要比 generator 強(或同級)**

用 Opus judge Sonnet 的輸出比用 Haiku judge 公平。

### LLM-as-judge 的偏見

- **Position bias**:先看到的答案會被打高分(比較任務)
- **Length bias**:長答案常被偏愛
- **Self-preference**:GPT judge 偏愛 GPT 輸出;Claude 偏愛 Claude

**對策**:

- 比較任務 → swap 順序平均
- Rubric 明確標「簡潔是加分」
- 用**不同** provider 的 model 當 judge(如果可能)

---

## 評估 RAG

RAG 有三個要 eval 的東西(Ch 19 提過):

### 1. Retrieval quality

你有一個 golden set:(query, list of correct doc_ids)。

```python
def eval_retrieval():
    golden = load_retrieval_golden()
    recall_at_5 = 0
    mrr = 0
    for case in golden:
        retrieved = my_retrieval(case["query"], k=5)
        retrieved_ids = [d.id for d in retrieved]
        correct = set(case["correct_ids"])
        hit = len(set(retrieved_ids) & correct) > 0
        recall_at_5 += int(hit)
        # MRR:第一個相關的 rank 倒數
        for rank, doc_id in enumerate(retrieved_ids, start=1):
            if doc_id in correct:
                mrr += 1 / rank
                break
    return {
        "recall@5": recall_at_5 / len(golden),
        "mrr": mrr / len(golden)
    }
```

### 2. Faithfulness(是否 hallucinate)

Answer 內容是否都來自 retrieved context?

```python
def judge_faithfulness(answer, context):
    prompt = f"""Given the context and the answer, classify:

HALLUCINATED: Answer contains claims NOT in context
PARTIAL: Answer uses context + adds unsupported claims
FAITHFUL: Answer is fully supported by context

Context: {context}
Answer: {answer}

Output: {{"verdict": "HALLUCINATED|PARTIAL|FAITHFUL", "unsupported_claims": [...]}}
"""
    ...
```

### 3. Answer relevance

Answer 有回答 query 嗎?

```python
def judge_relevance(query, answer):
    # "Does the answer address the question?"
    ...
```

### Ragas 工具

```python
from ragas import evaluate
from ragas.metrics import faithfulness, answer_relevancy, context_precision

result = evaluate(
    dataset=my_dataset,
    metrics=[faithfulness, answer_relevancy, context_precision]
)
```

Ragas 封裝常用 RAG metrics,適合快速 bootstrap。但它是用 LLM 打分,質量取決於 judge model + rubric。

---

## 評估 Agent

Agent eval 比單次 LLM call 難十倍。

### 難在哪

1. **Multi-step**:每步都可能錯,錯在哪看不出
2. **Non-deterministic path**:同一 task 可能走不同路徑都對
3. **Tool execution**:外部環境變動

### 可測的 metric

**1. Task success rate**

End-to-end:agent 最終有沒有完成任務?

```python
def eval_agent(task_golden):
    successes = 0
    for task in task_golden:
        result = run_agent(task["instruction"])
        if task["verify"](result):    # task-specific verifier
            successes += 1
    return successes / len(task_golden)
```

Verifier 是 task 專屬:

- Task「建 PR」→ check GitHub API 有沒有新 PR
- Task「改 bug X」→ check test 有沒有 pass
- Task「回答客服」→ LLM-as-judge

**2. Step count / cost**

Agent 走幾步才完成?**多步數 = 更可能錯,也更貴**。

**3. Tool success rate**

每個 tool call 的成功率。掉的 tool 是哪些?

**4. Trajectory quality**

Replay agent 的軌跡,人或 LLM 看「這個流程合理嗎」。

### 實務上

很多團隊 **only** eval task success。理由:step count / tool rate 這些 metric 會和 user value 脫節,**end outcome 最重要**。

---

## Benchmarks

公開 benchmark 看「你的產品 vs 標準」:

- **MMLU / MMLU-Pro**:general knowledge
- **HumanEval / MBPP**:code generation
- **SWE-bench**:agent-style code 修復
- **BFCL**:function calling
- **HELM**:多面向

**用處**:

- 挑選 base model 時參考
- PR release 跟標準 benchmark

**不用處**:

- Benchmark 跟你的 domain 可能沒關係
- 「我在 MMLU 90% 所以產品好」是錯的推論

**你自己 domain 的 golden set 永遠更重要**。

---

## A/B Testing LLM 改動

改 prompt / 換 model 時,production 應該比較兩版。

### 實作

```python
import random

def ab_prompt(query):
    variant = "A" if random.random() < 0.5 else "B"
    if variant == "A":
        system = PROMPT_V1
    else:
        system = PROMPT_V2
    resp = call_llm(system, query)
    log(variant, query, resp)
    return resp
```

追蹤 metrics:

- User satisfaction(thumbs up/down)
- Engagement(follow-up, dwell)
- Error rate / retry rate
- Cost per conversation

**不只看 offline eval**。Prod A/B 才是金標準。

### Gradual rollout

新 prompt / model 別一次 100%:

- 10% → 觀察 metric
- 50% → 觀察
- 100%

發現 regression 及早回滾。

---

## Regression Testing

Prod bug 修完後,**把這 case 加進 golden set**,永遠防止倒退。

```python
# golden/regression/
├── 2026-02-03-hallucinated-ceo.json
├── 2026-02-15-wrong-citation-format.json
└── ...
```

這是 eval 的**最重要用途之一**——你的 production 歷史成為你的 test suite。

---

## 成本 vs 頻率

不是所有 eval 都要天天跑:

| Eval | 頻率 | Cost |
|---|---|---|
| Unit tests | 每 commit | 幾乎 0 |
| Assertion eval(20 cases) | 每 commit / PR | $0.1–1 |
| LLM-as-judge golden(100 cases) | 每 day | $1–10 |
| Full RAG eval(1000 cases) | 每 week | $10–100 |
| Human eval | 每 release cycle / quarter | $100–$1000+ |

設計時考慮 cost。Eval 太貴 → 不會跑 → 沒護城河。

---

## 平台工具

不想自己寫整套:

- **Langfuse**:open source, observability + eval integration
- **Braintrust**:SaaS, LLM eval first-class
- **LangSmith**:LangChain 家的
- **Humanloop**、**Helicone**、**Arize Phoenix**...

Build vs buy:**PoC 階段自己寫**(才懂細節);**規模後買 SaaS**(才不會自造輪子)。

---

## 反例:常見 eval 誤區

### 誤區 1:「我看結果感覺對」

每次你手動看,你已經在 eval 了——只是不系統化。**自動化+量化**才能迭代。

### 誤區 2:LLM-as-judge 當唯一來源

Judge 有 bias,也會錯。**至少一部分 case 要 human review 校準 judge**。

### 誤區 3:Eval set 太大

200 筆 case 跑一次 eval 要 30 分鐘 + $20 → 不會天天跑。50 筆精選比 500 筆 noise 好。

### 誤區 4:只有 offline eval

Offline 只能看「你以為的 case」。**Prod log 才是真相**。

### 誤區 5:沒 regression case

Bug 修完不加到 eval set → 幾個月後回歸。

---

## Checklist:你的 LLM app 有以下嗎

- [ ] 至少 20 筆 golden examples
- [ ] 每個 PR 自動跑 eval,失敗 block merge
- [ ] Production bug 修完後 case 進 golden
- [ ] 改 prompt / 換 model 時 eval 比較前後
- [ ] 至少一個 quality metric(不只 keyword)
- [ ] Eval 成本 < 你改 prompt 的頻率
- [ ] Monthly human eval 校準

少於 3 項打勾,你還沒有真的 eval。

---

## 自我檢核

- [ ] Eval 的三層是什麼?
- [ ] LLM-as-judge 的三個 bias?
- [ ] RAG eval 的三個面向?
- [ ] Agent eval 為什麼比單次 call 難?
- [ ] Golden set 應該從哪來?(三個來源)

→ [Ch 21 Observability:traces / cost / latency](./21-observability.md)
