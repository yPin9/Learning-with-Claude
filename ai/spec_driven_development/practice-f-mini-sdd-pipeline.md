# 練習 F — 自建最小 SDD pipeline

> **目標**：寫一個能讀 spec、拆任務、呼叫 coding agent 實作、再跑驗收的最小 pipeline（PoC 等級），不依賴任何現成 SDD 工具，從第一原則把整條流水線親手接起來。
>
> **環境**：Python 3.11+、任一支援 API 或 CLI 的 AI coding agent、Git。若要用 Claude API，請確認你有 Anthropic API key；若用本地 LLM（Ollama 等）則不需要。工具版本與定價以官方最新為準。（查證日期 2026-06-30）

---

## 背景與動機

用 Spec Kit 或 Kiro 跑過完整流程後（練習 E），你知道這些工具能做什麼。但當工具替你做完所有事，你其實不清楚「pipeline 的邊界在哪裡」——哪些是 SDD 的本質，哪些是特定工具加上去的包裝？

這條練習的目的是讓你回到第一原則：用 200 行左右的 Python 把 `spec → tasks → implement → verify` 這條路自己走一遍。你不會得到一個生產等級的工具，但你會知道每一個銜接點在哪裡卡住、為什麼會卡住。這個過程讓你日後讀任何 SDD 工具的設計決策，都能帶著「我知道他在解什麼問題」的眼光。

這也是整門課 Part 6「DDD 與 SDD 的融合」的最後一道實作題。前面幾章從概念（[Ch 33](./33-ddd-sdd-same-fight.md)、[Ch 34](./34-ubiquitous-language-as-glossary.md)、[Ch 35](./35-bounded-context-agent-scope.md)、[Ch 36](./36-domain-model-as-spec-backbone.md)、[Ch 37](./37-modeling-first-prompting.md)、[Ch 38](./38-build-your-own-pipeline.md)）討論了「pipeline 應該長什麼樣」，現在你要把它寫出來。

> 如果你還沒跑過練習 E，先回看 [練習 E — 用 Spec Kit 把練習 D 的 spec 跑成可動小功能](./practice-e-spec-kit-run.md)。親手跑過商業工具，再自建才有比較基準。

---

## 心智圖像：pipeline 的五個隘口

```
┌──────────────────────────────────────────────────────────┐
│  spec.md                                                 │
│  （WHAT + WHY + Acceptance Criteria）                    │
└────────────────────┬─────────────────────────────────────┘
                     │  parse_spec()
                     ▼
┌──────────────────────────────────────────────────────────┐
│  tasks[]                                                 │
│  [{id, description, acceptance, depends_on}]             │
└────────────────────┬─────────────────────────────────────┘
                     │  call_coding_agent(task)
                     ▼
┌──────────────────────────────────────────────────────────┐
│  code diff / new files                                   │
│  （agent 產出的程式碼）                                   │
└────────────────────┬─────────────────────────────────────┘
                     │  run_tests()
                     ▼
┌──────────────────────────────────────────────────────────┐
│  test results                                            │
│  （pass / fail + stdout）                                │
└────────────────────┬─────────────────────────────────────┘
                     │  verify_acceptance(task, results)
                     ▼
┌──────────────────────────────────────────────────────────┐
│  report.md                                               │
│  （每個 task：PASS / FAIL + 理由）                       │
└──────────────────────────────────────────────────────────┘
```

五個隘口裡，三個是「決策點」，兩個是「執行點」：

- **parse_spec**（決策）：你選擇怎麼解讀 spec 的格式。結構化（YAML front matter）還是自然語言？
- **call_coding_agent**（執行）：你選擇把任務怎麼遞給 agent，agent 的回應怎麼寫回磁碟。
- **run_tests**（執行）：你選擇信任哪種驗收訊號——`pytest` 退出碼、custom script、或 LLM 裁判。
- **verify_acceptance**（決策）：你選擇「任何一個 AC 失敗就停下來」還是「全跑完再報告」。
- **report**（決策）：你選擇把結果給誰看——人工審、CI、下一個 agent。

這五個決策點，每一個在現成工具裡都有「預設答案」，而我們要親手選一遍。

---

## 任務規格

### 精確輸入

你需要：

1. **一份 `spec.md`**，格式如下（最小集合）：

```markdown
# Feature: <功能名稱>

## Overview
<一到三句功能定位>

## Acceptance Criteria
- AC1: <Given-When-Then 或 EARS 格式，精確到可自動驗收>
- AC2: ...

## Tasks
- T1: <一句話任務描述> [depends_on: none]
- T2: <一句話任務描述> [depends_on: T1]
- T3: <一句話任務描述> [depends_on: none]  # [P] 和 T1 平行
```

Tasks 區塊可以有也可以沒有——有的話 pipeline 直接讀；沒有的話 pipeline 要呼叫 agent 拆。

2. **一個 Git repo**（可以是空的 `git init` 出來的）。

3. **一個 coding agent 的 CLI 或 API**。本練習的範例使用 `claude` CLI（Claude Code），但你可以替換成任何支援 stdin prompt 的工具（`ollama run <model>`、`openai` CLI 等）。

### 精確輸出（你需要繳交的產物）

| 產物 | 路徑 | 說明 |
|---|---|---|
| `pipeline.py` | repo 根目錄 | 主程式，≤ 250 行 |
| `spec.md` | repo 根目錄 | 你設計的 spec（功能自選，見下方） |
| `tasks.json` | `pipeline_output/` | parse_spec 的輸出，pipeline 執行中間產物 |
| 實作產出的程式碼 | 由 agent 決定 | 至少有一個能執行的 Python 檔案 |
| `report.md` | `pipeline_output/` | 驗收報告，每條 AC 一行，標 PASS/FAIL |
| `postmortem.md`（你手寫） | repo 根目錄 | 三欄：「哪個隘口最難接 / 和 Spec Kit 比差在哪裡 / 下次會怎麼改」 |

### 要選一個具體的功能

不可以用 `foo`/`bar` 範例。請選下面其中一個（或自選同等複雜度）：

- **選項 A（推薦入門）**：`wordcount` — 讀一個文字檔，輸出字數、行數、最長單字、出現頻率前五名。
- **選項 B（中等）**：`csv-diff` — 讀兩個 CSV，輸出新增、刪除、修改的 row（以 row 的第一欄為 key）。
- **選項 C（較難）**：`mini-kanban` — 一個 CLI 的任務看板，支援 `add`、`move`（todo/in-progress/done）、`list` 三個指令，資料持久化到 JSON 檔。

選項 A 用來驗收 pipeline 的邏輯比較容易（輸出是數字，可以對固定輸入比對固定輸出），所以下面的範例全用選項 A 示範。

### 限制

- `pipeline.py` 主邏輯不能超過 250 行（測試輔助函式另計）。
- 不能直接呼叫 Spec Kit 或 Kiro——這道練習的意義就是自建。
- 驗收（verify_acceptance）至少要跑一次真實的 `pytest` 或 `subprocess` 測試，不能全靠 LLM 自評。
- 每個 task 的實作和驗收要留 git commit（至少三個 commit：init、implement、verify）。

### 驗收條件

- 跑 `python pipeline.py spec.md` 能不報錯地跑到底。
- `pipeline_output/report.md` 對選項 A 的至少兩條 AC 輸出 PASS。
- `postmortem.md` 每欄至少兩條具體觀察，不接受「還好」或「很難」這種沒有根據的評語。

---

## 期望輸出範例

### 選項 A 的 `spec.md`

```markdown
# Feature: wordcount

## Overview
Read a plain text file and output word count, line count, the longest word,
and the top-5 most-frequent words with their counts.

## Acceptance Criteria
- AC1: GIVEN a file `sample.txt` with exactly 3 lines and 10 words
       WHEN `python wordcount.py sample.txt` is run
       THEN stdout contains `lines: 3` and `words: 10`
- AC2: GIVEN `sample.txt` contains the word "banana" three times and no other word more than twice
       WHEN the script runs
       THEN stdout contains `banana: 3` as the first entry of top-5
- AC3: GIVEN `sample.txt` is empty
       WHEN the script runs
       THEN stdout contains `lines: 0`, `words: 0`, and exits with code 0
- AC4: GIVEN a path that does not exist
       WHEN the script runs
       THEN stderr contains `File not found` and exits with code 1

## Tasks
- T1: Implement `wordcount.py` with line/word count and top-5 frequency [depends_on: none]
- T2: Implement edge-case handling (empty file, missing file) [depends_on: none]  # [P] with T1
- T3: Write `test_wordcount.py` with pytest cases for AC1–AC4 [depends_on: T1, T2]
```

### 跑 `python pipeline.py spec.md` 的終端輸出

```
[pipeline] Parsing spec: spec.md
[pipeline] Found 4 ACs, 3 tasks (T1∥T2 → T3)
[pipeline] Calling agent for T1: Implement wordcount.py ...
[agent]    Writing wordcount.py (47 lines)
[pipeline] Calling agent for T2: edge-case handling ...
[agent]    Patching wordcount.py (+12 lines)
[pipeline] Calling agent for T3: Write test_wordcount.py ...
[agent]    Writing test_wordcount.py (61 lines)
[pipeline] Running tests: pytest test_wordcount.py -v
[pipeline] Tests passed: 4/4
[pipeline] Verifying AC1 ... PASS (stdout matched 'lines: 3', 'words: 10')
[pipeline] Verifying AC2 ... PASS (stdout matched 'banana: 3')
[pipeline] Verifying AC3 ... PASS (empty file exits 0)
[pipeline] Verifying AC4 ... PASS (missing file exits 1, stderr matched)
[pipeline] Report written to pipeline_output/report.md
[pipeline] Done. 4/4 ACs passed.
```

### `pipeline_output/report.md` 的格式

```markdown
# Verification Report

Generated: 2026-xx-xx HH:MM
Spec: spec.md
Feature: wordcount

| AC | Status | Evidence |
|----|--------|----------|
| AC1 | PASS | stdout='lines: 3\nwords: 10\n...' exit=0 |
| AC2 | PASS | stdout='banana: 3' in top-5 |
| AC3 | PASS | stdout='lines: 0\nwords: 0\n...' exit=0 |
| AC4 | PASS | stderr='File not found' exit=1 |

All 4/4 ACs passed.
```

---

## 如果你卡住了

1. **parse_spec 寫起來很累**：先把 spec.md 的格式硬規定成 Markdown 的特定 heading pattern，不要試圖解析自然語言。「`## Tasks` 下面每個 `- T\d+:` 開頭的行是一個 task」——用正則表達式或簡單的行解析器，比呼叫 LLM 解析更穩定。可解析性（parsability）是 spec 格式設計的隱性需求。

2. **call_coding_agent 不知道怎麼把程式碼寫回磁碟**：最簡單的做法是讓 agent 的 prompt 包含「把你的程式碼用 \`\`\`python ... \`\`\` 包起來，不要有其他文字」，然後在 Python 裡用正則抽出 code block 寫到檔案。這個做法脆弱但夠用 PoC 等級；Spec Kit 用的是 agent 本身的檔案操作能力，你用 CLI 工具的話通常可以傳 `--output-file` 或直接讓 agent 呼叫 shell。

3. **run_tests 回傳非零但不知道哪裡錯**：`subprocess.run(["pytest", "-v", "--tb=short"], capture_output=True, text=True)` 的 `.stdout` 和 `.stderr` 裡有完整的 pytest 輸出，把它一起寫進 report 或打印出來。如果 pytest 找不到測試檔，那是 agent 沒有產出正確的檔名——把 T3 的任務描述改得更具體，例如「Write `test_wordcount.py` in the same directory as `wordcount.py`」。

4. **verify_acceptance 的 AC 是 Given-When-Then，不知道怎麼自動跑**：對選項 A，最省力的做法是把每個 AC 的「WHEN ... THEN」部分硬寫成 Python 測試（不是讓 LLM 產），然後在 verify_acceptance 裡 `import` 它跑。更彈性的做法是讓 LLM 把 AC 的自然語言轉成 pytest case，但那又多了一個失敗點。PoC 等級的建議：AC 格式設計成容易硬匹配（數字、exit code、關鍵字）。

5. **pipeline 跑了一半 agent 卡住或報錯**：加一個 `--dry-run` 模式，parse_spec 和拆 tasks 跑真的，call_coding_agent 只印出「would call agent with: ...」不實際呼叫。這讓你能先確認 spec 解析是對的，再花 API 費用讓 agent 跑。

---

## 實作步驟建議

### Step 1：設計 spec 格式，寫一份真正的 spec（約 20 分鐘）

在開始寫 pipeline 之前，先把 `spec.md` 寫好。原因很反直覺：pipeline 的能力上限由 spec 格式決定，不是由 pipeline 程式碼決定。如果 AC 寫成「用起來要感覺不錯」，任何 pipeline 都無法自動驗收它。

選定選項 A/B/C 之後，花 15 分鐘把 AC 寫到「給一個固定輸入，能機械性判斷 PASS/FAIL」的粒度。測試：把你的 AC 給一個不認識這個功能的人讀，他能不能在不問你的情況下寫出對應的 pytest case？如果不能，AC 還不夠具體。

### Step 2：寫 parse_spec（約 30 分鐘）

```python
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

@dataclass
class Task:
    id: str                       # "T1"
    description: str
    depends_on: list[str] = field(default_factory=list)
    parallel: bool = False        # [P] 標記

@dataclass
class Spec:
    feature: str
    overview: str
    acceptance_criteria: list[str]   # 原始文字，["AC1: GIVEN ...", ...]
    tasks: list[Task]

def parse_spec(path: Path) -> Spec:
    text = path.read_text(encoding="utf-8")

    # 抽 feature 名稱（第一個 # heading）
    feature_match = re.search(r'^#\s+Feature:\s+(.+)$', text, re.MULTILINE)
    feature = feature_match.group(1).strip() if feature_match else "unknown"

    # 抽 Overview section
    overview_match = re.search(
        r'##\s+Overview\n(.*?)(?=\n##|\Z)', text, re.DOTALL
    )
    overview = overview_match.group(1).strip() if overview_match else ""

    # 抽 Acceptance Criteria
    ac_match = re.search(
        r'##\s+Acceptance Criteria\n(.*?)(?=\n##|\Z)', text, re.DOTALL
    )
    acs = []
    if ac_match:
        for line in ac_match.group(1).splitlines():
            line = line.strip()
            if re.match(r'^-\s+AC\d+:', line):
                acs.append(line.lstrip('- ').strip())

    # 抽 Tasks
    task_match = re.search(
        r'##\s+Tasks\n(.*?)(?=\n##|\Z)', text, re.DOTALL
    )
    tasks = []
    if task_match:
        for line in task_match.group(1).splitlines():
            line = line.strip()
            m = re.match(r'^-\s+(T\d+):\s+(.+?)(?:\s+\[depends_on:\s*(.+?)\])?(?:\s+#.*)?$', line)
            if m:
                tid, desc, deps_raw = m.group(1), m.group(2).strip(), m.group(3)
                deps = [d.strip() for d in deps_raw.split(',')] if deps_raw and deps_raw != 'none' else []
                parallel = '[P]' in line
                tasks.append(Task(id=tid, description=desc, depends_on=deps, parallel=parallel))

    return Spec(feature=feature, overview=overview,
                acceptance_criteria=acs, tasks=tasks)
```

用你的 spec.md 跑一遍 `parse_spec`，把結果 `print` 出來，確認 tasks 和 ACs 都被正確抽出，再往下走。

### Step 3：接 coding agent（約 45 分鐘）

最小化介面：接受一個 task description + spec context，回傳 agent 產出的純文字（agent 應該把程式碼包在 code block 裡）。

```python
import subprocess
import json

def call_coding_agent(task: Task, spec: Spec, output_dir: Path) -> str:
    """呼叫 claude CLI，回傳 agent 的原始輸出。"""
    prompt = f"""You are implementing part of the feature: {spec.feature}

Feature overview:
{spec.overview}

Your task ({task.id}):
{task.description}

Requirements (Acceptance Criteria):
{chr(10).join(spec.acceptance_criteria)}

Write the implementation. Put ALL code in a single ```python ... ``` block.
Do not explain, do not add extra text outside the code block.
The file should be saved as {_task_filename(task)}.
"""
    result = subprocess.run(
        ["claude", "--print", "--no-markdown"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Agent failed for {task.id}: {result.stderr[:200]}")
    return result.stdout

def extract_code(agent_output: str) -> Optional[str]:
    """從 agent 回應中抽出 ```python ... ``` block。"""
    m = re.search(r'```python\n(.*?)```', agent_output, re.DOTALL)
    return m.group(1) if m else None

def write_task_output(task: Task, code: str, output_dir: Path) -> Path:
    filename = _task_filename(task)
    filepath = output_dir / filename
    filepath.write_text(code, encoding="utf-8")
    return filepath

def _task_filename(task: Task) -> str:
    # T1 → wordcount.py, T3 → test_wordcount.py 這類對應由 task description 決定
    # 最省事：讓 agent 在程式碼裡用 shebang-style comment 聲明檔名
    # 這裡示範簡單的 fallback
    if "test" in task.description.lower():
        return "test_feature.py"
    return "feature.py"
```

這裡有一個需要誠實承認的問題：`_task_filename` 是最薄弱的地方。Spec Kit 用 agent 的原生檔案操作能力（agent 直接 `write_file`）繞開這個問題；我們用 CLI stdout 的話，必須有一個協定讓 agent 告訴我們檔案要叫什麼。最省力的做法是在 spec.md 的 Tasks 區塊直接寫 `[file: wordcount.py]`，然後 parse_spec 讀這個 annotation。

### Step 4：驗收（約 30 分鐘）

```python
def run_tests(output_dir: Path) -> tuple[bool, str]:
    """跑 pytest，回傳 (all_passed, stdout+stderr)。"""
    result = subprocess.run(
        ["pytest", str(output_dir), "-v", "--tb=short", "--no-header"],
        capture_output=True,
        text=True,
        cwd=output_dir,
    )
    all_passed = result.returncode == 0
    return all_passed, result.stdout + "\n" + result.stderr

def verify_acceptance(spec: Spec, test_output: str, all_passed: bool,
                       output_dir: Path) -> list[dict]:
    """
    對每條 AC 做簡單的關鍵字匹配 + pytest 結果對應。
    回傳 [{"ac": "AC1: ...", "status": "PASS", "evidence": "..."}]
    """
    results = []
    for ac in spec.acceptance_criteria:
        # 從 AC 文字抽 AC id（AC1、AC2 ...）
        ac_id_match = re.match(r'(AC\d+):', ac)
        ac_id = ac_id_match.group(1) if ac_id_match else "AC?"

        # 找 pytest 輸出裡有沒有對應的 PASSED/FAILED
        # 假設 test function 名稱含 ac_id.lower() （test_ac1, test_ac2 ...）
        pattern = rf'test_{ac_id.lower()}\s+PASSED'
        passed = bool(re.search(pattern, test_output, re.IGNORECASE))

        results.append({
            "ac": ac,
            "status": "PASS" if passed else "FAIL",
            "evidence": f"pytest {'matched' if passed else 'did not match'} {pattern}",
        })
    return results
```

這裡有一個設計取捨需要明確：AC 和 test function 之間的對應是「軟對應」（靠命名慣例），不是「強對應」。Kiro 的做法是用三份文件（requirements.md / design.md / tasks.md）明確把 AC 和 task 綁起來；我們的 PoC 版本靠命名慣例，所以 T3 的 task description 應該明確要求 agent 把 test function 命名成 `test_ac1`、`test_ac2`。把這個要求寫進 prompt。

### Step 5：組裝 pipeline，寫報告（約 20 分鐘）

```python
import json
from datetime import datetime

def write_report(spec: Spec, ac_results: list[dict],
                 output_dir: Path) -> Path:
    passed = sum(1 for r in ac_results if r["status"] == "PASS")
    total = len(ac_results)
    lines = [
        "# Verification Report",
        f"\nGenerated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Spec: spec.md",
        f"Feature: {spec.feature}",
        f"\n| AC | Status | Evidence |",
        "|----|--------|----------|",
    ]
    for r in ac_results:
        ac_short = r["ac"][:60] + ("..." if len(r["ac"]) > 60 else "")
        lines.append(f"| {ac_short} | {r['status']} | {r['evidence']} |")
    lines.append(f"\n{passed}/{total} ACs passed.")
    report_path = output_dir / "report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path

def main(spec_path: str, dry_run: bool = False):
    spec_file = Path(spec_path)
    output_dir = Path("pipeline_output")
    output_dir.mkdir(exist_ok=True)

    print(f"[pipeline] Parsing spec: {spec_path}")
    spec = parse_spec(spec_file)
    print(f"[pipeline] Found {len(spec.acceptance_criteria)} ACs, "
          f"{len(spec.tasks)} tasks")

    # 存中間產物
    tasks_json = [
        {"id": t.id, "description": t.description,
         "depends_on": t.depends_on, "parallel": t.parallel}
        for t in spec.tasks
    ]
    (output_dir / "tasks.json").write_text(
        json.dumps(tasks_json, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    if dry_run:
        print("[pipeline] --dry-run: stopping before agent calls")
        return

    # 按依賴順序跑任務（簡單拓撲排序）
    completed = set()
    for task in topological_sort(spec.tasks):
        print(f"[pipeline] Calling agent for {task.id}: {task.description[:50]}")
        raw = call_coding_agent(task, spec, output_dir)
        code = extract_code(raw)
        if code:
            written = write_task_output(task, code, output_dir)
            print(f"[agent]    Written: {written}")
        else:
            print(f"[pipeline] WARNING: no code block found for {task.id}")
        completed.add(task.id)

    print("[pipeline] Running tests...")
    all_passed, test_output = run_tests(output_dir)

    ac_results = verify_acceptance(spec, test_output, all_passed, output_dir)
    for r in ac_results:
        print(f"[pipeline] {r['ac'][:40]}... {r['status']}")

    report_path = write_report(spec, ac_results, output_dir)
    passed = sum(1 for r in ac_results if r["status"] == "PASS")
    print(f"[pipeline] Report written to {report_path}")
    print(f"[pipeline] Done. {passed}/{len(ac_results)} ACs passed.")

def topological_sort(tasks: list[Task]) -> list[Task]:
    """Kahn's algorithm。平行任務（[P]）先排。"""
    by_id = {t.id: t for t in tasks}
    in_degree = {t.id: len(t.depends_on) for t in tasks}
    queue = sorted(
        [t for t in tasks if not t.depends_on],
        key=lambda t: (not t.parallel, t.id)  # 平行任務優先
    )
    result = []
    while queue:
        task = queue.pop(0)
        result.append(task)
        for other in tasks:
            if task.id in other.depends_on:
                in_degree[other.id] -= 1
                if in_degree[other.id] == 0:
                    queue.append(other)
    if len(result) != len(tasks):
        raise ValueError("Cycle detected in task dependencies")
    return result

if __name__ == "__main__":
    import sys
    dry = "--dry-run" in sys.argv
    spec_arg = next((a for a in sys.argv[1:] if not a.startswith("--")), "spec.md")
    main(spec_arg, dry_run=dry)
```

先跑 `--dry-run` 確認 parse 正確，再跑完整流程：

```bash
python pipeline.py spec.md --dry-run   # 確認 spec 解析
python pipeline.py spec.md             # 完整跑
```

---

## 完整參考解答

**寫完再看。** 你的 pipeline 和參考解答幾乎必然不同——重要的不是程式碼長得一樣，而是你的 `postmortem.md` 裡有沒有誠實記下哪裡卡住、為什麼。

<details>
<summary>點開參考實作（選項 A wordcount 完整版）</summary>

以下是一個可跑的完整實作。假設你的環境已有 `claude` CLI 和 Python 3.11+。

**`spec.md`（標準輸入）**

```markdown
# Feature: wordcount

## Overview
Read a plain text file and output word count, line count, the longest word,
and the top-5 most-frequent words with their counts.

## Acceptance Criteria
- AC1: GIVEN a file `sample.txt` with exactly 3 lines and 10 words
       WHEN `python wordcount.py sample.txt` is run
       THEN stdout contains `lines: 3` and `words: 10`
- AC2: GIVEN `sample.txt` contains "banana" three times and no other word more than twice
       WHEN the script runs
       THEN stdout first line of top-5 is `banana: 3`
- AC3: GIVEN `sample.txt` is empty
       WHEN the script runs
       THEN stdout contains `lines: 0` and `words: 0` and exit code is 0
- AC4: GIVEN a path that does not exist
       WHEN `python wordcount.py no_such_file.txt` is run
       THEN stderr contains `File not found` and exit code is 1

## Tasks
- T1: Implement `wordcount.py` with line/word count, longest word, top-5 frequency [depends_on: none]
- T2: Add error handling for empty file and missing file to `wordcount.py` [depends_on: none]  # [P] with T1
- T3: Write `test_wordcount.py` with pytest cases named test_ac1 through test_ac4 [depends_on: T1, T2]
```

**`pipeline.py`（完整版，~200 行）**

```python
"""
Minimal SDD pipeline — PoC level.
Usage:
    python pipeline.py spec.md [--dry-run]
"""
import re
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


# ── Data types ─────────────────────────────────────────────────────────────

@dataclass
class Task:
    id: str
    description: str
    filename: str                          # 明確的輸出檔名
    depends_on: list[str] = field(default_factory=list)
    parallel: bool = False

@dataclass
class Spec:
    feature: str
    overview: str
    acceptance_criteria: list[str]
    tasks: list[Task]


# ── Parsing ─────────────────────────────────────────────────────────────────

def parse_spec(path: Path) -> Spec:
    text = path.read_text(encoding="utf-8")

    feature = (re.search(r'^#\s+Feature:\s+(.+)$', text, re.MULTILINE)
               or type('', (), {'group': lambda s, n: 'unknown'})()).group(1).strip()

    def section(name: str) -> str:
        m = re.search(rf'##\s+{name}\n(.*?)(?=\n##|\Z)', text, re.DOTALL)
        return m.group(1).strip() if m else ""

    acs = [
        line.strip().lstrip('- ')
        for line in section("Acceptance Criteria").splitlines()
        if re.match(r'^\s*-\s+AC\d+:', line)
    ]

    tasks = []
    for line in section("Tasks").splitlines():
        m = re.match(
            r'^\s*-\s+(T\d+):\s+(.+?)(?:\s+\[depends_on:\s*([^\]]+)\])?(?:\s+#.*)?$',
            line
        )
        if not m:
            continue
        tid, desc, deps_raw = m.group(1), m.group(2).strip(), m.group(3)
        deps = [d.strip() for d in deps_raw.split(',')] if deps_raw and deps_raw.strip() != 'none' else []
        parallel = '[P]' in line

        # 從 desc 抽檔名（看有沒有 `filename.py` pattern）
        fname_m = re.search(r'`([a-z_]+\.py)`', desc)
        filename = fname_m.group(1) if fname_m else f"task_{tid.lower()}.py"
        tasks.append(Task(id=tid, description=desc, filename=filename,
                          depends_on=deps, parallel=parallel))
    return Spec(feature=feature, overview=section("Overview"),
                acceptance_criteria=acs, tasks=tasks)


# ── Agent call ───────────────────────────────────────────────────────────────

def call_coding_agent(task: Task, spec: Spec) -> str:
    prompt = (
        f"You are implementing part of the feature '{spec.feature}'.\n\n"
        f"Feature overview:\n{spec.overview}\n\n"
        f"Your task ({task.id}):\n{task.description}\n\n"
        f"Acceptance Criteria:\n" +
        "\n".join(f"  {ac}" for ac in spec.acceptance_criteria) +
        f"\n\nWrite the implementation. "
        f"Output ONLY a single ```python ... ``` code block. "
        f"The file will be saved as `{task.filename}`. "
        f"If this is a test file, name pytest functions test_ac1, test_ac2, etc.\n"
    )
    result = subprocess.run(
        ["claude", "--print", "--no-markdown"],
        input=prompt, capture_output=True, text=True, timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Agent failed ({task.id}): {result.stderr[:300]}")
    return result.stdout

def extract_code(agent_output: str) -> Optional[str]:
    m = re.search(r'```python\n(.*?)```', agent_output, re.DOTALL)
    return m.group(1) if m else None


# ── Testing & verification ───────────────────────────────────────────────────

def run_tests(output_dir: Path) -> tuple[bool, str]:
    result = subprocess.run(
        ["pytest", str(output_dir), "-v", "--tb=short", "--no-header", "-q"],
        capture_output=True, text=True, cwd=str(output_dir),
    )
    return result.returncode == 0, result.stdout + result.stderr

def verify_acceptance(spec: Spec, test_output: str) -> list[dict]:
    results = []
    for ac in spec.acceptance_criteria:
        m = re.match(r'(AC\d+):', ac)
        ac_id = m.group(1) if m else "AC?"
        pattern = rf'test_{ac_id.lower()}\s+PASSED'
        passed = bool(re.search(pattern, test_output, re.IGNORECASE))
        results.append({
            "ac": ac_id,
            "text": ac[:80],
            "status": "PASS" if passed else "FAIL",
            "evidence": f"pytest pattern '{pattern}' {'found' if passed else 'not found'}",
        })
    return results


# ── Reporting ────────────────────────────────────────────────────────────────

def write_report(spec: Spec, results: list[dict], output_dir: Path) -> Path:
    passed = sum(1 for r in results if r["status"] == "PASS")
    rows = "\n".join(
        f"| {r['ac']} | {r['status']} | {r['evidence']} |"
        for r in results
    )
    content = (
        f"# Verification Report\n\n"
        f"Generated: {datetime.now():%Y-%m-%d %H:%M}\n"
        f"Feature: {spec.feature}\n\n"
        f"| AC | Status | Evidence |\n|----|--------|----------|\n{rows}\n\n"
        f"**{passed}/{len(results)} ACs passed.**\n"
    )
    p = output_dir / "report.md"
    p.write_text(content, encoding="utf-8")
    return p


# ── Topology sort ────────────────────────────────────────────────────────────

def topological_sort(tasks: list[Task]) -> list[Task]:
    in_deg = {t.id: len(t.depends_on) for t in tasks}
    queue = sorted([t for t in tasks if not t.depends_on],
                   key=lambda t: (not t.parallel, t.id))
    order = []
    while queue:
        t = queue.pop(0)
        order.append(t)
        for other in tasks:
            if t.id in other.depends_on:
                in_deg[other.id] -= 1
                if in_deg[other.id] == 0:
                    queue.append(other)
    if len(order) != len(tasks):
        raise ValueError("Dependency cycle in tasks")
    return order


# ── Main ─────────────────────────────────────────────────────────────────────

def main(spec_path: str, dry_run: bool = False):
    output_dir = Path("pipeline_output")
    output_dir.mkdir(exist_ok=True)

    print(f"[pipeline] Parsing: {spec_path}")
    spec = parse_spec(Path(spec_path))
    print(f"[pipeline] {len(spec.acceptance_criteria)} ACs, {len(spec.tasks)} tasks")

    (output_dir / "tasks.json").write_text(
        json.dumps([
            {"id": t.id, "desc": t.description,
             "file": t.filename, "depends_on": t.depends_on}
            for t in spec.tasks
        ], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    if dry_run:
        print("[pipeline] --dry-run: stopping before agent calls"); return

    for task in topological_sort(spec.tasks):
        print(f"[pipeline] Agent → {task.id}: {task.description[:55]}")
        raw = call_coding_agent(task, spec)
        code = extract_code(raw)
        if code:
            out_file = output_dir / task.filename
            out_file.write_text(code, encoding="utf-8")
            print(f"[agent]    Written: {out_file} ({len(code.splitlines())} lines)")
        else:
            print(f"[pipeline] WARNING: no code block in agent output for {task.id}")

    print("[pipeline] Running pytest ...")
    all_passed, test_out = run_tests(output_dir)
    results = verify_acceptance(spec, test_out)
    for r in results:
        print(f"[pipeline]   {r['ac']}: {r['status']}")

    report = write_report(spec, results, output_dir)
    passed = sum(1 for r in results if r["status"] == "PASS")
    print(f"[pipeline] Report: {report}")
    print(f"[pipeline] Done. {passed}/{len(results)} ACs passed.")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    spec_arg = next((a for a in sys.argv[1:] if not a.startswith("--")), "spec.md")
    main(spec_arg, dry_run=dry)
```

跑法：

```bash
git init wordcount-sdd && cd wordcount-sdd
# 把上面兩個檔案存進去
python pipeline.py spec.md --dry-run   # 先確認 parse 正確
python pipeline.py spec.md             # 完整跑（需要 claude CLI）

# 如果沒有 claude CLI，可以換成 ollama：
# 把 call_coding_agent 裡的 ["claude", "--print", "--no-markdown"]
# 換成 ["ollama", "run", "codellama", prompt]
# 結果品質會不同，但 pipeline 邏輯一樣
```

注意：`claude --print --no-markdown` 是 Claude Code CLI 在 2026-06-30 前確認的 flag 組合，版本更新後請以官方文件為準。

</details>

<details>
<summary>點開範例 postmortem.md（供對照）</summary>

```markdown
# Pipeline Postmortem

日期：2026-xx-xx
功能：wordcount（選項 A）
Agent：Claude Code CLI v1.x

## 哪個隘口最難接

1. **call_coding_agent → extract_code 這個銜接**最脆弱。Agent 有時候在 code block 前面
   加解釋文字（「Here is the implementation:」），有時候在後面加「This code handles...」
   extract_code 的正則必須能容忍這些，但有一次 agent 輸出了兩個 code block（一個說明、
   一個實作），正則只抓到第一個（說明用的片段），導致 T1 寫進去的是空殼。
   修法：改成「取最後一個 code block」或「取最長的 code block」。

2. **verify_acceptance 靠命名慣例太脆弱**。T3 的 prompt 要求 agent 把 test function
   命名成 `test_ac1`、`test_ac2`，但 agent 有時候用 `test_word_count`、`test_empty_file`
   這種語意名稱，導致 PASS 找不到。下次要在 prompt 裡更明確：
   「The function testing AC1 MUST be named exactly `test_ac1`.」

## 和 Spec Kit 比差在哪裡

1. **Spec Kit 有 `check-prerequisites.sh` 擋住「沒有 spec 就跑 plan」**。
   我的 pipeline 是線性腳本，沒有這個保護：如果 T1 的 agent 輸出沒有 code block，
   T3（依賴 T1）還是照跑，最後 pytest 找不到 `wordcount.py` 而全 FAIL。
   需要在每個 task 完成後驗證「預期的輸出檔存在」才繼續。

2. **Spec Kit 的 `/speckit.plan` 會產出 data-model.md 和 contracts/**。
   我的 pipeline 把 spec 和 tasks 直接接在一起，少了「架構設計」這層。
   對 wordcount 這種小功能夠用，但對 mini-kanban（選項 C）這種有狀態的功能，
   跳過設計層會讓 agent 對資料結構做不一致的假設。

## 下次會怎麼改

1. **加 spec 格式驗證**：parse_spec 應該在 ACs 少於 2 條或 Tasks 是空的時候直接
   報錯退出，不要讓 pipeline 帶著爛輸入跑完。
2. **加 task 產出驗證**：每個 task 跑完後，確認 `task.filename` 在 output_dir 裡
   真的存在且大小 > 0，再繼續下一個 task。
3. **把 `verify_acceptance` 的 AC-to-testfn 對應寫進 spec.md**：
   在 Tasks 區塊加一個 `[test: test_ac1, test_ac2]` annotation，
   讓 parser 抽出來，而不是靠慣例推斷。
```

</details>

---

## 測試用例表

用下面這些場景驗你的 pipeline 本身（不是它產出的 `wordcount.py`）：

| 情境 | 輸入 | 期望行為 | 你的 pipeline 有處理嗎 |
|------|------|----------|----------------------|
| 正常 spec，有 Tasks 區塊 | `spec.md`（完整版） | parse 成功，tasks.json 有 3 個 task | |
| spec 缺少 Acceptance Criteria | 移除 `## Acceptance Criteria` | 程式報錯或警告，不靜默略過 | |
| Tasks 有環狀依賴 | T1 depends_on T2，T2 depends_on T1 | topological_sort 拋 ValueError | |
| Agent 輸出沒有 code block | mock call_coding_agent 回傳純文字 | 印 WARNING，不 crash | |
| Tasks 為空（spec 沒有 Tasks 區塊） | 移除 `## Tasks` | pipeline 警告「no tasks found」，不跑 agent | |
| --dry-run flag | `python pipeline.py spec.md --dry-run` | parse + tasks.json 產出後停止，不呼叫 agent | |
| 所有測試 FAIL | agent 產出的程式碼有 bug | report.md 顯示 0/4 PASS，pipeline 正常結束（不 crash）| |
| output_dir 已存在 | 重跑 pipeline | 舊的 report.md 和程式碼被覆蓋，不報錯 | |

最後一欄留白，讓你自己填。如果有「沒有處理」的格子，把它加進 `postmortem.md` 的「下次會怎麼改」欄。

---

## 踩雷集錦

**錯誤直覺 1：讓 LLM 解析 spec 比正則更可靠**

正確認識：對結構化文件，正則或簡單行解析器的行為是確定性的——你知道它什麼時候會壞。讓 LLM 解析 spec 引入了不確定性：同一份 spec 跑兩次可能得到不同的 tasks 清單。François Zaninotto（Marmelab，2025-11-12）觀察到 Spec Kit 有「Markdown Madness」問題——spec 格式一旦有任何偏離，工具行為就不可預測。自建 pipeline 的一大優勢是你控制格式，讓解析保持確定性。

---

**錯誤直覺 2：pipeline 應該先設計好架構，等設計完才動手**

正確認識：對 PoC 等級的任務，「先跑起來再優化」比「先設計再實作」更適合，原因是你在寫完 call_coding_agent 之前根本不知道 agent 的輸出長什麼樣。Spec Kit 的設計者也走過這條路：最初版本（2025-09-02 發佈）只有四個指令（/specify、/plan、/tasks、implement），之後才加入 /speckit.clarify、/speckit.analyze、/speckit.converge——不是因為前四個設計不好，而是真實使用後才發現缺什麼。

---

**錯誤直覺 3：驗收通過就代表功能正確**

正確認識：HN 用戶 yoaviram（item 45610996）在 Spec Kit 上親身經歷了「implement 任務標記完成，但測試全都跑不起來」。自建 pipeline 也一樣：如果 T3 的 agent 沒有按命名慣例命名 test function，`verify_acceptance` 全部回傳 FAIL，但 pipeline 不知道原因是「測試存在但名稱不對」還是「功能確實壞掉」。驗收系統需要比「AC 有沒有對應到 PASSED 的 test」更豐富的診斷資訊。

---

**錯誤直覺 4：接了 LLM 就能產出正確程式碼**

正確認識：LLM 的輸出品質高度依賴 prompt 的品質，而 prompt 的品質依賴 spec 的具體程度。Annegret Junker（codecentric，2026-03-04）的食譜平台案例顯示：用 EventStorming 建出領域模型後，同一個 LLM 對「食材」「份量」「自評分」的理解從 3 個 schema 增加到 9 個——不是 LLM 更好了，是輸入的 spec 更精確了。你的 pipeline 再怎麼完善，如果 spec.md 的 AC 還是寫「用起來要直覺」，輸出就不會更好。

---

**錯誤直覺 5：自建 pipeline 比 Spec Kit 更靈活，所以更好**

正確認識：靈活和可靠是取捨。Spec Kit 在 v0.11.x 有 175+ 個 release（2026-06-29 確認），每一個版本都是真實用戶踩到問題後的修正。你的 200 行 PoC 沒有 check-prerequisites 的相位門、沒有 [P] 任務的真正平行執行、沒有 constitution 這一層的原則守護、沒有 /speckit.converge 的持續同步。靈活的代價是你要自己踩這些坑。自建 pipeline 的價值在於理解，不在於取代工具。

---

## 延伸挑戰

完成基本流程後，挑以下幾個方向深入：

1. **把 parse_spec 換成讓 agent 拆 tasks**：移除 spec.md 裡的 `## Tasks` 區塊，讓 pipeline 第一步呼叫 agent「把這份 spec 拆成 3-5 個 tasks，輸出 JSON」，再把 JSON 解析成 `Task` 物件。記錄拆出來的 tasks 和你手寫的有什麼差異——這直接對應 `/speckit.plan` 做的事。

2. **加一個 verify_with_llm 函式**：對無法自動比對的 AC（例如「輸出要易讀」），讓 LLM 讀程式碼輸出並判斷 PASS/FAIL。把 LLM 裁判的結果和 pytest 裁判的結果放在同一份 report，然後問自己：你信任哪一個？為什麼？

3. **對選項 C（mini-kanban）跑 pipeline**：mini-kanban 有狀態、有多個 CLI 指令、有 JSON 持久化。你的 pipeline 要怎麼設計 tasks 才能讓 agent 不在「add」和「list」的實作之間做出不一致的 JSON schema 假設？這是 Bounded Context 在 pipeline 層面的具體問題——每個 task 應該看到多少其他 tasks 的上下文？

4. **量化你的 pipeline 和 Spec Kit 的差距**：選一個小功能，分別用你的 pipeline 和 Spec Kit 跑，記下：花的時間、產出的程式碼行數、FAIL 的 AC 數量、你需要人工修的地方。這是 Colin Eberhardt（Scott Logic，2025-11-26）在測試 Spec Kit 時做的事，你的數據和他的比較。

---

## 自我檢核

完成本練習後，你應該能回答（用自己的話，不翻資料）：

- [ ] pipeline 的五個「隘口」是什麼？哪兩個是執行點，哪三個是決策點？如果面試被問「你設計這個 pipeline 做了哪些取捨」，你能說出三個具體的取捨選擇嗎？
- [ ] `topological_sort` 在這個 pipeline 裡解決什麼問題？如果 spec.md 裡所有 tasks 都標 `[depends_on: none]`，排序結果是什麼？
- [ ] `extract_code` 用正則從 agent 輸出抽 code block，這個方法有哪些失敗模式？Spec Kit 用什麼機制避開了這個問題？
- [ ] `verify_acceptance` 靠 pytest function 命名慣例對應 AC，這個設計在 AC 數量從 4 增加到 20 時會有什麼問題？你會怎麼改 spec.md 格式來讓對應更可靠？
- [ ] 你的 `postmortem.md` 裡的「哪個隘口最難接」——這個痛點在 Spec Kit 的設計裡有沒有對應的解法？如果沒有，說明你找到了 Spec Kit 的一個設計缺口。
- [ ] 這道練習和 [Ch 38 — 自建一條 spec→plan→tasks→implement→verify 流水線](./38-build-your-own-pipeline.md) 的概念討論相比，哪裡「動手做」讓你改變了對某個概念的理解？
- [ ] Thoughtworks Technology Radar（Nov 2025）把 SDD 放在「Assess」環，理由之一是「outputs are hard to review」。你自建的 pipeline 的輸出有這個問題嗎？report.md 的格式改成什麼樣才能讓 code reviewer 更容易審查？

---

## 延伸閱讀

- **[github/spec-kit 官方 README](https://github.com/github/spec-kit)**：對照你的 pipeline 和 Spec Kit 的 scaffolded `.specify/scripts/` 目錄——它們各自解決了 pipeline 的哪個隘口？特別看 `create-new-feature.sh` 和 `check-prerequisites.sh` 的原始碼，你會看到你自己在 Step 3 踩到的問題有沒有被它處理。

- **[From Stories to Code — Annegret Junker（codecentric，2026-03-04）](https://www.codecentric.de/en/knowledge-hub/blog/from-stories-to-code-how-domain-storytelling-and-eventstorming-give-llms-the-context-they-need)**：Junker 的食譜平台案例是最接近「自建 modeling-first pipeline」的一手實驗，她的三個 prototype（v1 無模型 / v2 有 EventStorming / v3 有 Bounded Context）直接對應你在延伸挑戰 3 裡會遇到的問題。閱讀 v1→v2 的 schema 對比（3 schemas vs 9 schemas）和她對「spec 語言品質決定輸出品質」的結論。

- **[Spec-Driven Development | Technology Radar Vol 34（Thoughtworks，Nov 2025）](https://www.thoughtworks.com/en-us/radar/techniques/spec-driven-development)**：「outputs hard to review」的一手來源。讀完後回頭看你的 report.md：它算不算「hard to review」？讀 Tessl 的「bitter lesson」段落，這個警告對你的 PoC pipeline 也成立嗎？

- **[Putting Spec Kit Through Its Paces — Colin Eberhardt（Scott Logic，2025-11-26）](https://blog.scottlogic.com/2025/11/26/putting-spec-kit-through-its-paces-radical-idea-or-reinvented-waterfall.html)**：迄今最詳細的 Spec Kit 端對端測試，有逐步的時間計時。閱讀「Implementation」段落，Eberhardt 遇到的「agent 沒有跑測試就標完成」問題在你的 pipeline 裡是怎麼發生的（或怎麼被你避開的）？

- **[Waterfall Strikes Back — François Zaninotto（Marmelab，2025-11-12）](https://marmelab.com/blog/2025/11/12/spec-driven-development-waterfall-strikes-back.html)**：SDD 最有力的批評文章之一。讀「Markdown Madness」段落——Zaninotto 展示的「display current date 功能產出 8 個 Markdown 檔 / 1,300 行」是用 Spec Kit 跑 Frequentisto 案例得到的結果（不是 Kiro）。你的 200 行 pipeline 產出了幾個檔案？這和「over-engineering」的邊界在哪裡？

→ [Ch 39 規格漂移與規格腐化](./39-spec-drift-rot.md)
