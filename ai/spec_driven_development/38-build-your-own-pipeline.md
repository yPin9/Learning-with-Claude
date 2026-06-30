# Ch 38 — 自建一條 spec→plan→tasks→implement→verify 流水線

> **目標**：用 Claude Agent SDK 把規格解析、任務拆分、實作、驗收串成一條最小自動化流水線，理解每個環節背後的機制與取捨，並把這條流水線和 `ai/harness_engineering` 課程學過的 agent 工程技能銜接起來。
> **環境**：Python 3.11+、`anthropic` SDK、GitHub Spec Kit v0.11.x（查證日期 2026-06-30）。Spec Kit 指令名稱以版本為準，執行 `specify self check` 確認當下版本。

---

## 心智圖像：流水線不是瀑布

「流水線（pipeline）」這個詞讓人直覺聯想到瀑布——線性、一次走到底、中間不回頭。

> 如果你對瀑布的真實意涵還不熟，先回看 [Ch 4 瀑布的真相：Royce 1970 與一個誤會](./04-waterfall-myth.md)。

這裡說的流水線，更像是工廠的**裝配線加上品管回路**：每個工站做一件事，做完輸出給下個工站，但每個工站都有檢查點，發現問題可以把工件退回上游。

```
┌────────┐    ┌────────┐    ┌────────┐    ┌──────────┐    ┌────────┐
│  SPEC  │───▶│  PLAN  │───▶│ TASKS  │───▶│IMPLEMENT │───▶│ VERIFY │
│  解析  │    │  規劃  │    │  拆分  │    │  實作    │    │  驗收  │
└────────┘    └────────┘    └────────┘    └──────────┘    └────────┘
     ▲              │             │              │               │
     │         constitution   tasks 缺口    test failure    驗收失敗
     └─────────────────────────────────────────────────────────────┘
                              退回機制（非瀑布）
```

每個方框是獨立可測的 agent 步驟；箭頭是結構化資料（JSON / Markdown），不是口頭交接；退回機制是流水線健壯的關鍵，也最容易被略去。

---

## 歷史脈絡：在這條流水線之前

### 振動式開發（2022–2024）

LLM 進入開發工作流的第一波形態是「口頭驅動」：描述想要什麼，LLM 吐程式碼，口頭反饋，無限循環。這個方式對小功能有效，對大型功能有三個系統性問題：

1. **無可復現的意圖**：對話上下文存在記憶體裡，今天的決定明天找不到根據。
2. **無法讓 agent 接力**：第二個 agent 接手時，沒有結構化的起點。
3. **驗收標準散落**：「應該可以新增使用者」這句話沒有邊界——哪些欄位、驗證規則、錯誤狀態都未定義。

> 如果你對自然語言需求的病症還不熟，先回看 [Ch 8 為什麼需求這麼難：自然語言的八種病](./08-why-requirements-hard.md)。

### Spec Kit 的出現（2025-09）

Den Delimarsky（GitHub，Principal Product Manager）在 GitHub Blog 2025-09-02 宣布了 GitHub Spec Kit，README 的根本論點是：

> "We treat coding agents like search engines when we should be treating them more like literal-minded pair programmers."

Spec Kit 把人類意圖結構化成幾份 Markdown 文件（spec、plan、tasks），再讓 agent 從明確輸入出發。但 Spec Kit 的預設是**人和 agent 互動**完成這幾個步驟，不是全自動。本章把這個思想自建成可程式化的流水線。

---

## 五個環節的機制

### 環節一：SPEC 解析

輸入是自由文字需求，輸出是結構化的規格物件。

為什麼不直接把原文丟給下一個 agent？因為後繼的每個環節都需要**可機器讀取的特定欄位**：驗收條件（acceptance criteria）、範圍外（out of scope）、術語表（glossary）。埋在散文裡的資訊，每個 agent 重新解讀可能不一致。

用 `pydantic` 強迫每個欄位是明確型別：

```python
# pipeline/models.py
from pydantic import BaseModel
from typing import List

class AcceptanceCriterion(BaseModel):
    id: str          # "AC-1"
    given: str       # GIVEN 條件
    when: str        # WHEN 動作
    then: str        # THEN 預期結果
    priority: str    # "MUST" | "SHOULD" | "MAY"

class ParsedSpec(BaseModel):
    feature_name: str
    acceptance_criteria: List[AcceptanceCriterion]
    out_of_scope: List[str]
    glossary: dict[str, str]   # 術語表：跨環節傳遞，固定 LLM 詞彙
```

注意 `glossary`——這是 [Ch 34 通用語言作為 LLM 的詞彙表](./34-ubiquitous-language-as-glossary.md) 在流水線的落地：術語表在 SPEC 環節建立，往後每個環節都注入 system prompt，讓整條流水線用同一套語言。

> 如果你對 Given-When-Then 還不熟，先回看 [Ch 10 從驗收條件到 BDD：Given-When-Then](./10-acceptance-criteria-bdd.md)。

呼叫 Claude SDK 做解析的骨幹：

```python
# pipeline/spec_parser.py
import anthropic, json
from .models import ParsedSpec

client = anthropic.Anthropic()

def parse_spec(raw_requirement: str) -> ParsedSpec:
    schema = ParsedSpec.model_json_schema()
    resp = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=2048,
        system="你是需求分析師。把需求解析成嚴格 JSON，不要多餘文字。"
               "驗收條件用 Given-When-Then，priority 只能是 MUST/SHOULD/MAY。",
        messages=[{"role": "user", "content":
            f"需求：\n{raw_requirement}\n\n輸出符合此 Schema 的 JSON：\n"
            f"{json.dumps(schema, ensure_ascii=False)}"}]
    )
    return ParsedSpec.model_validate_json(resp.content[0].text.strip())
```

測試輸入：「食譜平台新增購物清單：使用者可建立清單、加入食材（名稱/數量/單位）、標記已購買、分享唯讀清單給其他使用者。不做群組協作編輯。」

期望輸出：`out_of_scope` 含「群組協作編輯」；`glossary` 定義「食材」；AC-1 覆蓋「最多 X 個食材」邊界（若有）。

### 環節二：PLAN 規劃

輸入是 `ParsedSpec`，輸出是技術方案（資料模型、API 端點、注意事項）。

這個環節的關鍵設計決定：**constitution 比 spec 更早注入**。

如果專案有 `.specify/memory/constitution.md`（定義技術棧、架構約束），PLAN 的 system prompt 先放 constitution，再放 spec，讓技術選型永遠在約束內完成。混用的後果：SPEC agent 看到 constitution 說「不允許 Redis」，可能把業務上合理的 WebSocket 需求也過濾掉——那是業務需求，不是技術選型。

> 如果你對 Spec Kit 的 constitution 機制還不熟，先回看 [Ch 27 GitHub Spec Kit（一）：安裝與 bootstrap](./27-spec-kit-install.md)。

```python
# pipeline/planner.py（骨幹）
def plan(spec: ParsedSpec, constitution: str = "") -> TechPlan:
    system = []
    if constitution:
        system.append(f"## 專案憲法（不可違背）\n{constitution}")
    system.append("根據規格設計最小可行技術方案，輸出 JSON。"
                  "out_of_scope 的功能絕對不要設計進來。")
    glossary = "\n".join(f"- {k}：{v}" for k, v in spec.glossary.items())
    # 呼叫 SDK，傳入 glossary + spec JSON，解析回傳為 TechPlan
```

### 環節三：TASKS 拆分

輸入是 `ParsedSpec` + `TechPlan`，輸出是有依賴關係的任務清單。

Spec Kit 用 `[P]` 標記可並行任務；AWS Kiro 用「波次（waves）」排程（kiro.dev/docs/specs/ 的逐字確認：「Waves execute sequentially; tasks within a wave execute concurrently.」查證日期 2026-06-30）。我們用有向無環圖（Directed Acyclic Graph，DAG）：

```python
class Task(BaseModel):
    id: str
    title: str
    acceptance_criterion_ids: List[str]  # 對應哪些 AC（必填）
    depends_on: List[str]                # 依賴的 task id
    can_run_parallel: bool               # 前置都完成後，可否和兄弟並行
    verification_command: str | None     # e.g. "pytest tests/test_cart.py::test_add"
```

`acceptance_criterion_ids` 是流水線的可追溯性鏈——每個 task 要能回溯到至少一個 AC，VERIFY 才能精確定位失敗。

接收 tasks 後立刻校驗 DAG：

```python
def validate_task_dag(tasks: List[Task]) -> None:
    ids = {t.id for t in tasks}
    for t in tasks:
        unknown = set(t.depends_on) - ids
        if unknown:
            raise ValueError(f"Task {t.id} 依賴不存在的 id: {unknown}")
        if not t.acceptance_criterion_ids:
            raise ValueError(f"Task {t.id} 沒有對應任何 AC，無法驗收")
```

### 環節四：IMPLEMENT 實作

輸入是單一 `Task`，輸出是寫入磁碟的程式碼修改。

這個環節和 `ai/harness_engineering` 的銜接最直接——需要 tool calling 讓 agent 讀寫檔案：

> 如果你對 tool calling protocol 還不熟，先回看 [Ch 5 Tool Calling Protocol](../harness_engineering/05-tool-calling-protocol.md)。

```python
# pipeline/implementer.py（骨幹）
TOOLS = [
    {"name": "read_file",  "description": "讀取檔案",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "write_file", "description": "寫入檔案",
     "input_schema": {"type": "object", "properties":
        {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "run_command","description": "執行 shell 指令，回傳 stdout+stderr",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "task_complete","description": "回報任務完成",
     "input_schema": {"type": "object", "properties":
        {"success": {"type": "boolean"}, "summary": {"type": "string"}}, "required": ["success"]}},
]

MAX_TURNS = 20   # 安全帶：沒有上限的 agentic loop 是預算定時炸彈

def implement_task(task: Task, spec: ParsedSpec, project_root: str) -> ImplementResult:
    messages = [{"role": "user", "content":
        f"任務：{task.model_dump_json(ensure_ascii=False)}\n"
        f"術語表：{spec.glossary}\n"
        "用工具讀取現有程式碼、實作任務、執行 verification_command 確認通過，"
        "完成後呼叫 task_complete。"}]
    for _ in range(MAX_TURNS):
        resp = client.messages.create(
            model="claude-opus-4-5", max_tokens=4096,
            tools=TOOLS, messages=messages)
        tool_calls = [b for b in resp.content if b.type == "tool_use"]
        if not tool_calls:
            raise RuntimeError(f"Task {task.id}: agent 未呼叫工具即停止")
        results = []
        for tc in tool_calls:
            if tc.name == "task_complete":
                return ImplementResult(task_id=task.id, success=tc.input["success"])
            results.append({"type": "tool_result", "tool_use_id": tc.id,
                            "content": dispatch_tool(tc.name, tc.input, project_root)})
        messages += [{"role": "assistant", "content": resp.content},
                     {"role": "user", "content": results}]
    raise RuntimeError(f"Task {task.id} 超過 {MAX_TURNS} 轉未完成")
```

> 詳見 [Ch 7 Stop Conditions and Turns](../harness_engineering/07-stop-conditions-turns.md)，了解為什麼 `MAX_TURNS` 是必備而非選配。

### 環節五：VERIFY 驗收

VERIFY 有兩個層次：

**機械層**：執行 `verification_command`，收集 pytest / 測試框架的輸出。

**語意層**：把測試結果對照 `AcceptanceCriterion`，判斷「測試通過」是否真的等於「AC 被滿足」。

語意層非常重要。François Zaninotto（Marmelab CEO，2025-11-12）實測記錄了一個失效案例：agent 把驗收任務標記完成，卻沒有寫任何測試——exit code 0 是因為沒有測試可以跑失敗。語意驗收層要讓另一個 Claude 實例批判性地審查：

```python
# pipeline/verifier.py（骨幹）
def verify_feature(spec: ParsedSpec, test_output: str) -> VerificationReport:
    prompt = (
        "你是嚴格的 QA 工程師。對每一個 MUST 等級的 AC，"
        "判斷測試輸出是否真的覆蓋了它。"
        "找不到對應測試案例，一律判為 NOT_COVERED，不要猜測。\n\n"
        f"AC 清單：{spec.acceptance_criteria_str()}\n\n"
        f"測試輸出：{test_output}\n\n"
        "輸出 JSON：每個 AC 的 {id, status: PASS|FAIL|NOT_COVERED, notes}"
    )
    # 呼叫 SDK，解析結果為 VerificationReport
```

---

## 把五個環節串起來

```python
# pipeline/runner.py
import asyncio
from .models import ParsedSpec, TechPlan, Task

async def run_pipeline(raw_requirement: str, project_root: str,
                       constitution_path: str | None = None) -> VerificationReport:
    constitution = open(constitution_path).read() if constitution_path else ""

    spec   = parse_spec(raw_requirement)          # [1/5] SPEC
    plan   = plan_feature(spec, constitution)     # [2/5] PLAN
    tasks  = decompose_tasks(spec, plan)          # [3/5] TASKS
    validate_task_dag(tasks)

    results = await implement_dag(tasks, spec, project_root)  # [4/5] IMPLEMENT
    test_out = run_all_tests(project_root)
    return verify_feature(spec, test_out)         # [5/5] VERIFY

async def implement_dag(tasks, spec, project_root):
    """依 DAG 調度：前置依賴完成後，can_run_parallel 的任務同時跑。"""
    completed: dict[str, ImplementResult] = {}
    pending = list(tasks)
    while pending:
        ready = [t for t in pending if all(d in completed for d in t.depends_on)]
        if not ready:
            raise RuntimeError("DAG 死結：有未完成任務但無可執行任務")
        results = await asyncio.gather(
            *[asyncio.to_thread(implement_task, t, spec, project_root) for t in ready],
            return_exceptions=True)
        for t, r in zip(ready, results):
            pending.remove(t)
            if isinstance(r, Exception):
                raise RuntimeError(f"Task {t.id} 失敗：{r}") from r
            completed[t.id] = r
    return list(completed.values())
```

執行入口：

```python
# main.py
import asyncio
from pipeline.runner import run_pipeline

REQUIREMENT = """
食譜平台新增購物清單：使用者可建立清單、加入食材（名稱/數量/單位）、
標記已購買、分享唯讀清單給其他使用者。不做群組協作編輯。
"""

if __name__ == "__main__":
    report = asyncio.run(run_pipeline(
        raw_requirement=REQUIREMENT,
        project_root="./recipe_platform",
        constitution_path="./.specify/memory/constitution.md"
    ))
    print(report.summary())
```

---

## 底層機制

### 為什麼每個環節輸出結構化資料

當環節 A 輸出自然語言，環節 B 需要解讀它——兩個解讀步驟都可能出錯，且錯誤是乘法的。結構化資料把「解讀」步驟從環節 B 中分離，放到環節 A 的輸出驗證裡。

這對應 [Ch 32 Structured Output](../harness_engineering/32-structured-output.md) 的核心論點：結構化輸出不只是方便解析，它是減少 agent 之間「語意漂移」的工程手段。

### 為什麼 constitution 在 PLAN 而非 SPEC 注入

constitution 是技術約束，SPEC 的工作是理解業務意圖，不應受技術約束干擾。PLAN 的工作才是技術選型。兩者混用的後果：SPEC agent 可能因為 constitution 說「不允許 Redis」而過濾掉一個業務上合理的 WebSocket 需求——這不是技術選型，是業務需求被誤殺。

### 為什麼 VERIFY 需要語意層

機械測試通過 ≠ AC 被滿足。AC 是業務語言，測試是工程語言。測試可以通過，但測試覆蓋範圍不等於 AC 的範圍。語意驗收層是最後一道防護，防止「綠燈但業務需求未達成」的錯誤信心。

### 並行 agent 的記憶體隔離

`implement_dag` 的並行協程維護各自獨立的 `messages` 串列，不共享 Claude 的對話上下文，只透過檔案系統交換資料。

> 如果你對 subagent 隔離還不熟，先回看 [Ch 26 Subagents](../harness_engineering/26-subagents.md) 和 [Ch 27 Multi-Agent Orchestration](../harness_engineering/27-multi-agent-orchestration.md)。

這個設計的代價：兩個並行 agent 可能同時修改同一個檔案，造成 read-modify-write 衝突。緩解策略：task 拆分時讓 LLM 把「接觸同一個檔案的任務」排在同一個依賴鏈（序列化），不進並行組。

---

## 對比取捨

| 面向 | 振動式開發 | Spec Kit 手動工作流 | 自建流水線（本章） |
|---|---|---|---|
| **人工介入點** | 每一步 | 每個 /speckit.* 指令前 | 僅 spec 輸入與 verify 報告 |
| **可程式化** | 不可 | 有限 | 完全可程式化 |
| **constitution 支援** | 靠 system prompt 記憶 | 原生（.specify/memory/） | 需自行讀入 |
| **任務並行** | 靠人工判斷 | [P] 標記（文件層） | DAG 調度（程式層） |
| **驗收閉環** | 靠人工看結果 | /speckit.converge 補剩餘 | 自動語意驗收 + 退回 |
| **適合場景** | 探索性開發 | 有明確需求的功能開發 | CI/CD 整合、批次功能 |
| **最大風險** | 意圖漂移 | 指令集版本變動（version-dependent） | LLM 隨機性影響整批 |

Thoughtworks Technology Radar Vol. 34（Nov 2025）把 SDD 放在 Assess 環：「fascinating, though the workflows remain elaborate and opinionated」。自建流水線讓這個「elaborate」的工作流受控、可測，但它不能降低底層 LLM 的不確定性。

---

## 踩雷集錦

### 雷 1：SPEC 環節輸出了技術意見

**錯誤直覺**：SPEC 解析時順便讓 LLM 判斷如何實作，省一個環節的 API 呼叫。

**正確認識**：SPEC 和 PLAN 的職責分離是整條流水線的根基。SPEC 一旦混入技術意見，PLAN 會把它當約束接受，constitution 反而被架空。更嚴重的是 SPEC 和 constitution 可能產生矛盾（SPEC 說「用 Redis」，constitution 說「不允許 Redis」），PLAN 看到矛盾輸入，行為不可預測。

### 雷 2：任務沒有對應 AC

**錯誤直覺**：任務拆分只管「把工作切小」，不需要和 AC 掛鉤。

**正確認識**：沒有對應 AC 的任務，VERIFY 環節無法判斷它是否正確完成，也無法在失敗時定位到要退回哪個任務。`validate_task_dag` 應該強制要求 `acceptance_criterion_ids` 不為空。每個 task 和 AC 一對一（或多對一），讓驗收和重試精確到 AC 粒度。

### 雷 3：VERIFY 只看 exit code

**錯誤直覺**：`pytest` 回傳 0 就是驗收通過。

**正確認識**：Zaninotto（2025-11-12）記錄了 agent 把驗收任務標記完成卻沒有寫任何測試——exit code 0 是因為沒有測試可以跑失敗。正確的 VERIFY 要先確認「每個 MUST 等級的 AC 有對應的測試案例」，再確認那些測試通過。兩個條件都要成立。

### 雷 4：並行 agent 寫同一個檔案

**錯誤直覺**：`write_file` 是原子操作，沒有競態問題。

**正確認識**：單次 write 是原子的，但 read-modify-write 序列不是。Agent A 讀了 `models.py`、Agent B 也讀了 `models.py`，A 加了一個 class，B 也加了一個 class，後寫入者覆蓋前者。緩解：task 拆分時把「接觸同一個輸出檔案的任務」放在同一依賴鏈，序列化而非並行。

### 雷 5：沒有 MAX_TURNS 的 agentic loop

**錯誤直覺**：agent 會自己決定何時完成，不需要外部干預。

**正確認識**：一個 implement task 的 agent 可能陷入「寫程式 → 跑測試 → 看到錯誤 → 嘗試修正 → 引入新錯誤 → ...」的無盡循環。每轉數千 token，不設上限等於沒有成本保護。`MAX_TURNS = 20` 是安全帶，到達上限就報錯讓人介入。

> 詳見 [Ch 7 Stop Conditions and Turns](../harness_engineering/07-stop-conditions-turns.md) 和 [Ch 9 錯誤處理與重試](../harness_engineering/09-error-handling-retry.md)。

### 雷 6：相信 agent 會尊重 out_of_scope

**錯誤直覺**：規格裡寫了 `out_of_scope`，agent 看到就不會多做。

**正確認識**：LLM 在「設計完整功能」的驅動下，有時會「幫忙」加看起來合理的功能。`validate_task_dag` 之後應該再加一步：掃描 tasks 的 description，如果出現 `out_of_scope` 的概念就拋出例外。程式碼的顯式防護，不能靠 LLM 自我克制。

---

## 與 harness_engineering 的銜接點

| 本章元素 | harness_engineering 對應章節 |
|---|---|
| 結構化輸出（`pydantic` 模型） | [Ch 32 Structured Output](../harness_engineering/32-structured-output.md) |
| `implement_task` 的 agentic tool loop | [Ch 4 Minimal Agent Loop](../harness_engineering/04-minimal-agent-loop.md)、[Ch 5 Tool Calling Protocol](../harness_engineering/05-tool-calling-protocol.md) |
| `implement_dag` 的多 agent 並行 | [Ch 26 Subagents](../harness_engineering/26-subagents.md)、[Ch 27 Multi-Agent Orchestration](../harness_engineering/27-multi-agent-orchestration.md) |
| `verify_feature` 的批判性 agent | [Ch 34 Eval](../harness_engineering/34-eval.md) |
| constitution 注入 system prompt | [Ch 11 System Prompt Design](../harness_engineering/11-system-prompt-design.md) |
| 術語表跨環節傳遞 | [Ch 14 Memory](../harness_engineering/14-memory.md) |
| MAX_TURNS 與 stop conditions | [Ch 7 Stop Conditions and Turns](../harness_engineering/07-stop-conditions-turns.md) |

這條流水線不是在 harness_engineering 上加 SDD 魔法，而是把 spec→plan→tasks 當作 harness 的**前置配置層**：更清晰的任務定義讓 harness agent 更少迷路、更快完成、更好被驗收。

---

## 進階延伸

### 接進 CI/CD

```yaml
# .github/workflows/sdd-pipeline.yml
on:
  issues:
    types: [labeled]
jobs:
  sdd-run:
    if: github.event.label.name == 'sdd-auto'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: python main.py --issue ${{ github.event.issue.number }}
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

這個模式對「重複性高、模式固定」的功能（CRUD、配置頁、報表）投資回報率最高。探索性或高度創意性的功能，人工介入仍然更有效。

> 關於何時不要用自動化，見 [Ch 42 什麼時候不要用 SDD](./42-when-not-to-use-sdd.md)。

### 規格漂移的自動偵測

流水線跑完後，程式碼可能因 bug fix 和重構而偏離規格。在 CI/CD 定期執行 VERIFY 環節，如果 PASS 數量下降就開 PR 標記漂移位置。

> 規格漂移的完整討論見 [Ch 39 規格漂移與規格腐化](./39-spec-drift-rot.md)。

### 可觀測性

每個環節的輸入輸出都應記錄：呼叫的 model、token 用量、耗時、輸出的結構化資料 hash。沒有可觀測性，第一個失敗的生產 run 你無從判斷是哪個環節出錯。

> 詳見 [Ch 35 Observability](../harness_engineering/35-observability.md)。

---

## 動手練習

**練習 38-A：實作 SPEC 解析**

用 `pydantic` 定義 `ParsedSpec`（含 `acceptance_criteria`、`out_of_scope`、`glossary`），寫 `parse_spec()`，把以下需求轉換成 `ParsedSpec`：

```text
電商平台新增「商品比較」：使用者可選取最多三個商品對比規格（價格、重量、庫存）。
不做商品評分的比較。
```

驗證：`out_of_scope` 含「商品評分比較」；至少一個 AC 說明「不超過三個商品」的邊界。

**練習 38-B：設計 task DAG**

根據上面的商品比較功能，手動設計 5–7 個任務，標記 `depends_on` 和 `can_run_parallel`，確認無循環依賴（畫出依賴圖），確認每個任務對應至少一個 AC。

**練習 38-C：加入退回機制**

在 `run_pipeline` 加 retry：若 VERIFY 某個 AC 是 `FAIL` 或 `NOT_COVERED`，找到對應 task，退回重跑 `implement_task`，最多重試 2 次。第三次仍失敗輸出 `MANUAL_REVIEW_REQUIRED`。

這個練習是 [練習 F 自建最小 SDD pipeline](./practice-f-mini-sdd-pipeline.md) 的前哨。

---

## 本章重點整理

- 流水線不是瀑布：每個環節獨立可測、可退回，退回機制是設計的一部分。
- 五個環節職責分離：SPEC 管業務意圖，PLAN 管技術選型，TASKS 管工作拆分，IMPLEMENT 管程式碼生成，VERIFY 管業務語意確認。混用職責，錯誤乘法放大。
- 結構化資料是 agent 之間的唯一共識：每個環節輸出 `pydantic` 模型，後繼環節消費它，不依賴 agent 解讀自然語言。
- constitution 在 PLAN 注入，術語表貫穿全程：constitution 管技術約束，術語表管業務語意，不混用。
- 語意驗收層是閉環的保障：exit code 0 ≠ AC 被滿足；VERIFY 要求語意對照。
- 並行需要 DAG 調度與衝突保護：同一輸出檔案的任務序列化，不進並行組。
- MAX_TURNS 是安全帶：沒有上限的 agentic loop 是預算定時炸彈。
- 這條流水線是 harness_engineering 的組合技：spec→plan→tasks 是 harness 的前置配置層。

---

## 自我檢核

- [ ] 我能用自己的話解釋「流水線和瀑布的差別在退回機制」——如果面試官問，我怎麼答？
- [ ] 我能說出 SPEC 和 PLAN 職責分離的理由，以及混用的具體後果是什麼。
- [ ] 我能描述 `implement_dag` 怎麼做 DAG 調度，以及並行 agent 的 read-modify-write 衝突和緩解策略。
- [ ] 我能解釋為什麼 VERIFY 需要語意層，並說出「exit code 0 ≠ AC 滿足」的具體失效案例（Zaninotto 的發現）。
- [ ] 我能列出至少四個本章踩雷，各說出「錯誤直覺」和「正確認識」。
- [ ] 如果有人說「直接用 Spec Kit 手動跑就好」，我能說出什麼情況下自建流水線更有價值。

---

## 延伸閱讀

1. **github/spec-kit — 官方 repo（README）** — GitHub / Den Delimarsky
   - URL: https://github.com/github/spec-kit
   - 從這裡開始：「Detailed Process」段落的 Taskify 範例，看 spec/plan/tasks 三份產物的實際格式，對比本章的自建設計選擇
   - 本章關聯：本章流水線五個環節的設計直接參考 Spec Kit 的 constitution / spec / plan / tasks / implement 五個主要產物

2. **spec-driven.md（in-repo）** — github/spec-kit maintainers
   - URL: https://raw.githubusercontent.com/github/spec-kit/main/spec-driven.md
   - 從這裡開始：templates 如何充當 LLM 約束（constraining LLM output in productive ways）與 [NEEDS CLARIFICATION] 標記機制
   - 本章關聯：本章 SPEC 和 PLAN 環節的 template-as-constraint 設計原理直接對應這裡；讓你理解「為什麼要限制輸出格式」而非讓 LLM 自由發揮

3. **Anthropic Claude API — Messages（Tool Use）** — Anthropic（官方文件，查證日期 2026-06-30）
   - URL: https://docs.anthropic.com/en/api/messages
   - 從這裡開始：Tool use 的 `tool_use` / `tool_result` content block 格式，以及 `stop_reason: "tool_use"` 和 `"end_turn"` 的區分
   - 本章關聯：`implement_task` 的 agentic loop 依賴正確的 tool use 協議；`stop_reason` 的區分是本章迴圈邏輯的基礎

4. **Waterfall Strikes Back** — François Zaninotto（Marmelab CEO，2025-11-12）
   - URL: https://marmelab.com/blog/2025/11/12/spec-driven-development-waterfall-strikes-back.html
   - 從這裡開始：「Context Blindness」段落，以及 agent 把驗收任務標記完成但沒有寫任何測試的失效案例
   - 本章關聯：本章 VERIFY 語意層設計的主要動機；「不要信任 exit code 0」的直接實驗記錄（注意：文中 8 個檔案 / 1300 行的數字屬於 Frequentisto 的「顯示當前日期」Spec Kit 範例，不是 Kiro 範例）

5. **Agentic Code Workflows with Nick Tune** — Nick Tune（Sr Staff Eng, PayFit）via Techworld with Milan（2026-03-26）
   - URL: https://newsletter.techworld-with-milan.com/p/agentic-code-workflows-with-nick
   - 從這裡開始：dependency-cruiser 確定性邊界執行段落，以及把 AI 工作流建模為 aggregate 狀態機的做法
   - 本章關聯：本章「out_of_scope 要用程式碼顯式防護，不靠 LLM 自我克制」踩雷的實務支撐；Tune 的 deterministic enforcement 方法是同一個精神

6. **HN thread #45610996（Spec Kit 實測回報）** — Hacker News（Oct 2025 Böckeler 文章討論串）
   - URL: https://news.ycombinator.com/item?id=45610996
   - 從這裡開始：yoaviram 的「~10 天、大多數測試失敗、implement 不跟流程走」；hatmanstack 的「agent 刪程式碼且不恢復」
   - 本章關聯：MAX_TURNS 和語意驗收層的踩雷設計直接回應這些真實失效案例；最誠實的第一手 SDD 失敗記錄

7. **Spec-Driven Development | Technology Radar Vol. 34** — Thoughtworks（Nov 2025）
   - URL: https://www.thoughtworks.com/en-us/radar/techniques/spec-driven-development
   - 從這裡開始：Assess 環的定位理由，以及 Tessl「bitter lesson」警告
   - 本章關聯：提供誠實的現況錨點——自建流水線解決了「elaborate and opinionated workflow」的可控性問題，但無法改變 SDD 仍處於 Assess 環的整體評估

---

下一章轉向流水線跑起來之後的長期問題：規格和程式碼怎麼保持同步、漂移從哪裡開始、腐化的早期訊號是什麼。

→ [練習 F 自建最小 SDD pipeline](./practice-f-mini-sdd-pipeline.md)
