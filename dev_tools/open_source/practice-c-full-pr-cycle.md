# 練習 C — 完整跑一次貢獻循環

> **目標**：把 Part 3（fork、PR、issue、code review、CI、gh CLI）綜合起來，在你**自己控制的 repo** 完整跑一次貢獻循環：開 issue → fork → branch → PR → CI → review → merge。這是發真實 PR（練習 D）前的全流程預演——在安全環境把每一步走熟，真實貢獻時就不會手忙腳亂。

> 前置：Part 3 全部（Ch 10–15）。

## 背景與動機

練習 D 你要對真實專案發 PR——那時你不想因為「不熟流程」而出糗（開錯 base、CI 紅了不會看、review 不會回應）。這個練習讓你先在自己的 repo 把整個循環走一遍，每一步親手操作，建立肌肉記憶。你會扮演**兩個角色**：貢獻者（發 PR）和維護者（審 PR + merge）——這也讓你提前體會 Part 6 的維護者視角。

## 任務規格

### 你要建立的東西

1. 一個「上游專案」repo（扮演維護者，含 CI）。
2. fork 它（或用第二帳號/同帳號模擬外部貢獻者）。
3. 走完整循環：issue → fork → branch → 改 code → PR → CI → review → merge。

### 兩種做法（擇一）

- **做法 A（推薦，最真實）**：用**兩個 GitHub 帳號**——主帳號當維護者建上游 repo，分身帳號 fork 並貢獻。最接近真實的「外部貢獻」。
- **做法 B（單帳號）**：用主帳號建上游 repo，**fork 到同帳號**（GitHub 允許 fork 自己的 org repo，或用兩個 repo 模擬）。或乾脆 branch-based（同 repo 開 branch 發 PR），體驗流程但少了跨 fork 的部分。

> 單帳號限制：GitHub 不允許 fork 自己**個人帳號**的 repo 到同一帳號。變通：(1) 建一個 org 放上游 repo、fork 到個人帳號；(2) 用 branch-based（同 repo branch→PR，體驗 PR/review/CI/merge，跳過 fork）;(3) 開分身帳號（最完整）。本練習以做法 A 描述，做法 B 自行調整。

### 完整循環的每一步（驗收標準）

- [ ] **建上游 repo**（維護者）：含一個有 bug 的小程式 + 一個簡單的 CI（跑測試）+ CONTRIBUTING + issue/PR 範本
- [ ] **開 issue**（貢獻者）：用好的 bug report 格式回報那個 bug（Ch 12）
- [ ] **fork + clone**（貢獻者）：`gh repo fork --clone`，設好 upstream（Ch 10/15）
- [ ] **開 branch + 修 bug**（貢獻者）：基於最新 main 開 branch，修好 bug，寫好 commit（Ch 2/3）
- [ ] **開 PR**（貢獻者）：好的標題/描述、`Closes #N`、CI 跑起來（Ch 11）
- [ ] **CI 綠**（貢獻者）：本地先跑、確保 PR 全綠（Ch 14）
- [ ] **review**（維護者）：用另一身分 review，留 inline comment + request changes（Ch 29 預習）
- [ ] **回應 review**（貢獻者）：改 code、push、回應每條 comment、re-request（Ch 13）
- [ ] **approve + merge**（維護者）：approve、squash merge，確認 issue 自動關閉
- [ ] **清理**（貢獻者）：同步 fork、刪除已合併的 branch

## 期望成果

走完後你應該有：
- 一個 merged PR（在上游 repo）
- 一個自動關閉的 issue（因為 `Closes #N`）
- 完整體驗過 review 來回（changes requested → 改 → approve）
- CI 在 PR 上跑過（紅→綠的經驗加分）

## 如果你卡住了

1. **CI 怎麼設？** 最簡單：一個 `.github/workflows/ci.yml` 跑你的測試指令。本練習重點不是寫 CI（那是 cicd 課），用最簡單的能跑就好——下面解答給範例。
2. **單帳號怎麼 review 自己的 PR？** GitHub 不讓你 approve 自己的 PR。做法 A（分身）才能完整 review。單帳號就只能「自己留 comment」體驗介面，merge 用維護者身分。
3. **fork PR 的 CI 沒跑？** 第一次貢獻者的 fork PR 可能需要維護者批准 CI（Ch 14）。用維護者身分按「Approve and run」。
4. **不知道怎麼讓程式有「可修的 bug」？** 下面解答給一個現成的：一個沒處理空陣列的函式 + 一個會抓到它的測試。

## 實作步驟建議

### Step 1：建上游 repo（維護者帽子）

建一個含 bug 的小專案 + CI + 協作檔案。

### Step 2：開 issue 回報 bug（貢獻者帽子）

用 Ch 12 的 bug report 格式開 issue。

### Step 3：fork + clone + 修 bug

`gh repo fork --clone`，開 branch，修 bug，寫測試，commit。

### Step 4：開 PR + 確保 CI 綠

本地先跑測試，push，`gh pr create`，`gh pr checks --watch`。

### Step 5：review 來回（兩頂帽子切換）

維護者 review（request changes）→ 貢獻者回應（改+回覆）→ 維護者 approve。

### Step 6：merge + 清理

squash merge、確認 issue 關閉、同步 fork、刪 branch。

## 完整參考解答

**自己先試著走，卡住再看。**

<details>
<summary>點開完整循環（含可用的範例檔案）</summary>

### Step 1：建上游 repo

```bash
# 維護者帽子
mkdir upstream-demo && cd upstream-demo
git init && git switch -c main

# 一個有 bug 的小程式（Python）
mkdir src tests
cat > src/stats.py <<'EOF'
def average(numbers):
    return sum(numbers) / len(numbers)   # BUG: 空陣列會 ZeroDivisionError
EOF

cat > tests/test_stats.py <<'EOF'
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from stats import average

def test_average_basic():
    assert average([2, 4, 6]) == 4

def test_average_empty():
    # 這個測試會抓到 bug（目前會崩）
    assert average([]) == 0
EOF

# 最簡單的 CI
mkdir -p .github/workflows
cat > .github/workflows/ci.yml <<'EOF'
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.x' }
      - run: python -m pytest tests/ -v
EOF

# 協作檔案（Ch 33 會深入）
cat > CONTRIBUTING.md <<'EOF'
# Contributing
1. Fork & clone
2. Run tests before pushing: `python -m pytest tests/`
3. Open a PR with a clear description, link the issue with `Closes #N`
EOF

mkdir -p .github/ISSUE_TEMPLATE
cat > .github/ISSUE_TEMPLATE/bug.md <<'EOF'
---
name: Bug Report
about: Report a bug
---
### Environment
### Steps to reproduce
### Expected
### Actual
EOF

git add . && git commit -m "Initial project with known empty-list bug"

# 建上游 repo 並 push（用 gh）
gh repo create upstream-demo --public --source=. --push
```

CI 會在 push 後跑——`test_average_empty` 會**失敗**（bug 還在）。這正常，這就是我們要修的。

### Step 2：開 issue（貢獻者帽子）

```bash
gh issue create --repo <維護者>/upstream-demo \
  --title "average() crashes on empty list" \
  --body "$(cat <<'EOF'
### Environment
- Python 3.x

### Steps to reproduce
1. `from stats import average`
2. `average([])`

### Expected
Returns 0 (or handles empty gracefully).

### Actual
ZeroDivisionError: division by zero
EOF
)"
# 記下 issue 號，假設 #1
```

### Step 3：fork + clone + 修

```bash
# 貢獻者帽子（分身帳號，或同帳號 from org）
gh repo fork <維護者>/upstream-demo --clone
cd upstream-demo

git fetch upstream
git switch -c fix/empty-list-average upstream/main

# 修 bug
cat > src/stats.py <<'EOF'
def average(numbers):
    if not numbers:
        return 0
    return sum(numbers) / len(numbers)
EOF

# 本地先跑測試（Ch 14）
python -m pytest tests/ -v        # 應該全綠了

git add src/stats.py
git commit -m "Handle empty list in average()

Return 0 for an empty list instead of raising ZeroDivisionError.

Closes #1"
git push -u origin fix/empty-list-average
```

### Step 4：開 PR + CI

```bash
gh pr create --repo <維護者>/upstream-demo --base main \
  --title "Handle empty list in average()" \
  --body "$(cat <<'EOF'
## What
Guard `average()` against empty input.

## Why
`average([])` raises ZeroDivisionError (see #1). Real callers may
pass empty collections.

## Testing
`test_average_empty` now passes. All tests green locally.

Closes #1
EOF
)"

gh pr checks --watch       # 看 CI 跑，應該綠（因為你修好了）
```

### Step 5：review 來回

```bash
# 維護者帽子：review，故意 request changes（練回應）
gh pr review <PR號> --repo <維護者>/upstream-demo --request-changes \
  --body "Looks good! One thing: should empty average be 0 or raise a custom error? Also please add a docstring."
# （或在 web 留 inline comment）

# 貢獻者帽子：回應
cat > src/stats.py <<'EOF'
def average(numbers):
    """Return the arithmetic mean, or 0 for an empty list."""
    if not numbers:
        return 0
    return sum(numbers) / len(numbers)
EOF
git commit -am "Address review: add docstring clarifying empty behavior"
git push        # PR 自動更新

# 回覆 comment（web 或）：
gh pr comment <PR號> --repo <維護者>/upstream-demo \
  --body "Added a docstring. Kept returning 0 since the issue expected graceful handling — happy to switch to an exception if you prefer."

# 維護者帽子：滿意，approve
gh pr review <PR號> --repo <維護者>/upstream-demo --approve
```

### Step 6：merge + 清理

```bash
# 維護者帽子：squash merge
gh pr merge <PR號> --repo <維護者>/upstream-demo --squash --delete-branch

# 確認 issue #1 自動關閉（因為 Closes #1）
gh issue view 1 --repo <維護者>/upstream-demo    # state: CLOSED

# 貢獻者帽子：同步 fork、清理本地
git switch main
gh repo sync               # 同步 fork 跟上 upstream（含剛 merge 的）
git pull
git branch -d fix/empty-list-average    # 刪已合併的本地 branch
```

**解答說明**：

這個練習濃縮了真實貢獻的每一步：
- **issue 先行**：先回報 bug（Ch 12），PR 再 `Closes` 它——完整的可追溯鏈。
- **本地先跑測試**（Step 3/4）：push 前確保綠，PR 一開就是好的（Ch 14）。
- **commit/PR 都講 why**：commit 和 PR 描述都說明「為什麼」（Ch 2/11）。
- **review 來回**：request changes → 改 + 回應 + re-request → approve，走完整 review 儀式（Ch 13）。
- **兩頂帽子**：你同時體驗貢獻者和維護者——這讓你發真實 PR 時懂維護者在想什麼（Part 6 預習）。
- **squash merge + 自動關 issue + 同步 fork**：收尾的標準動作（Ch 10/25）。

走完這一遍，練習 D 對真實專案發 PR 時，你會發現「啊，流程一模一樣」——差別只在對象是陌生人、要更小心禮儀。

</details>

## 測試用例 / 檢查點

| 階段 | 檢查 |
|---|---|
| 上游 repo 建好 | CI 跑、`test_average_empty` 一開始失敗（bug 在）|
| issue 開好 | 用 bug report 格式，可複現 |
| fork + clone | `git remote -v` 有 origin（fork）+ upstream |
| PR 開好 | 好標題/描述、`Closes #1`、base 對 |
| CI | 修好後 PR 全綠 |
| review | 經歷 request changes → 回應 → approve |
| merge | squash merged，issue #1 自動 CLOSED |
| 清理 | fork 同步、本地 branch 刪除 |

## 延伸挑戰（加分）

1. **branch-based 版**：同一 repo 不 fork，直接開 branch 發 PR（你是 collaborator 的情境，Ch 10）——對比 fork-based 的差異。
2. **故意讓 CI 紅**：先 push 一個沒修好的版本（CI 紅），用 `gh run view --log-failed` 讀失敗 log，再修綠——練「PR 變紅怎麼辦」（Ch 14）。
3. **多輪 review**：維護者故意來回 request changes 三次（不同面向：邏輯、命名、測試），貢獻者每輪認真回應——練「被審得體」（Ch 13）。
4. **加 PR 範本**：給上游 repo 加 `.github/PULL_REQUEST_TEMPLATE.md`，重開一個 PR 看它自動帶出（Ch 11/33）。
5. **全程用 gh**：整個循環不開瀏覽器（除了必要時 `--web`），純命令列完成（Ch 15）。

## 自我檢核

- [ ] 我能不看筆記，從 issue → fork → branch → PR → CI → review → merge 走完整循環
- [ ] 我的 PR 一開就是好的（CI 綠、描述清楚、關聯 issue）——不靠 CI 幫我 debug
- [ ] 我能得體地回應 review（改+回應每條+re-request）
- [ ] 我體驗過維護者視角（review、merge、看 PR），知道他們在意什麼
- [ ] 我準備好對「真實的陌生專案」發 PR 了（練習 D）

Part 3 完成——你會用 GitHub 平台協作了。Part 4 進入真正的開源貢獻：怎麼找到適合的專案、貢獻前的功課、發出你的第一個**真實** PR，以及最關鍵的軟實力——溝通與禮儀。

→ [Ch 16 找到適合貢獻的專案](./16-finding-projects.md)
