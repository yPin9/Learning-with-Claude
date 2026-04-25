# Ch 6 — workflow 檔案結構

> 目標：搞懂 `.github/workflows/*.yml` 的 events / jobs / steps 三層結構。為 `tasktrack` 寫第一條 workflow，在 PR 觸發時跑 lint 與測試。

## 先把 `tasktrack` 推到 GitHub

Part 2 開始會需要一個真正的 GitHub repo — 本章起所有 workflow 都要靠 GitHub event 觸發。

```bash
cd ~/projects/tasktrack      # 或你放的位置
git init
git add .
git commit -m "initial tasktrack"

gh repo create tasktrack --public --source=. --push
```

`gh repo create` 會建 repo + 把本地 push 上去。public / private 看你決定（public 才能用完整 Actions 免費額度；這課用 public 示範）。

## workflow 長什麼樣

最小 workflow：

```yaml
# .github/workflows/hello.yml
name: Hello
on: push

jobs:
  say-hi:
    runs-on: ubuntu-latest
    steps:
      - run: echo "Hello from Actions"
```

就這樣。Commit + push，到 GitHub repo 的 **Actions** 分頁看 — 會有一個 run。

三層結構：

```
Workflow (整個 yml 檔)
├── on: (觸發事件)
└── jobs: (幾份工作)
     ├── <job-name>:
     │    ├── runs-on: (什麼機器)
     │    └── steps:
     │         ├── - <step 1>
     │         └── - <step 2>
     └── <another-job>: ...
```

## `on:` — 觸發事件

常見 event：

```yaml
# 任何 branch push
on: push

# 只 main
on:
  push:
    branches: [main]

# PR（開啟、更新）
on:
  pull_request:
    branches: [main]

# 排程（cron）
on:
  schedule:
    - cron: '0 3 * * *'   # 每天 UTC 03:00

# 手動觸發
on:
  workflow_dispatch:

# 推 tag
on:
  push:
    tags: ['v*']

# 複合
on:
  push:
    branches: [main]
  pull_request:
    branches: [main]
```

**重點**：一個 workflow 可以被多個 event 觸發。每次觸發都是獨立一次 run。

### `pull_request` vs `push` 的差別

PR 觸發時，`GITHUB_SHA` 是 **merge commit 的 SHA**（一個 GitHub 合成的虛擬 commit），不是 PR branch 頭。這意味：

- PR CI 跑的是「如果我現在 merge 會變怎樣」的 code
- **通常這就是你要的**，但若你要跑 PR branch 本身（例如為了某些安全檢查），要用 `pull_request_target` 或自己 checkout 指定 ref

## `jobs:` — 一份 job 一台 VM

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    steps: [...]
  lint:
    runs-on: ubuntu-latest
    steps: [...]
```

兩個 job **預設平行**跑，各自一台全新 VM。這是 Actions 的基本設計：**每個 job 是獨立 sandbox**。

Runner 選項：

| `runs-on:` | 用途 |
|---|---|
| `ubuntu-latest` | 最常用，免費額度涵蓋 |
| `ubuntu-22.04` / `ubuntu-20.04` | pin 版本 |
| `macos-latest` | Mac，更貴（x10 分鐘計價） |
| `windows-latest` | Windows，x2 分鐘計價 |
| `self-hosted` | 你自己的機器（Ch 14） |

### job 之間怎麼傳東西

默認 **不互通**。要傳：

- **artifact**（`actions/upload-artifact` + `download-artifact`）— 檔案
- **job output**（`outputs:` + `needs.<job>.outputs.<name>`）— 字串
- **Docker registry**（一個 job push、另一個 pull）— image

Ch 11 會細講。

## `steps:` — 一條 step 一個動作

每個 step 要嘛 `uses:`（叫別人寫好的 action），要嘛 `run:`（直接跑 shell）：

```yaml
steps:
  - uses: actions/checkout@v4             # 叫現成 action：checkout 原始碼
  - name: Install deps                    # name 是 UI 顯示用
    run: |
      python -m pip install --upgrade pip
      pip install -r requirements.txt
  - run: pytest -v                        # 沒 name 也可以
```

### `actions/checkout@v4` 的真面目

**你幾乎每個 workflow 第一步都會用它**。為什麼？

因為 runner 初始時是 **空的** — 沒有你的 code。`checkout` 會 `git clone` 你的 repo 到 runner 的 `GITHUB_WORKSPACE`（通常是 `/home/runner/work/<repo>/<repo>`）。

**沒 checkout 你的 step 就是裸跑一台空 VM**，很多人第一次栽在這。

### 本地能跑的就在 `run:` 裡跑

原則（Ch 1 講過）：**能在 shell script 跑的事，就別讓它長在 YAML 裡**。

差：

```yaml
steps:
  - run: python -m pip install --upgrade pip
  - run: pip install -r requirements.txt
  - run: pip install -r requirements-dev.txt
  - run: pytest --cov=app --cov-report=term tests/
  - run: echo "coverage done"
```

好：

```yaml
steps:
  - run: ./scripts/ci-test.sh
```

然後 `scripts/ci-test.sh` 你在本地也能跑、debug。**YAML 只該是 orchestration**。

## 實作：`tasktrack` 第一條 workflow

目的：PR 時跑 lint 和 pytest。

檔案 `.github/workflows/ci.yml`：

```yaml
name: CI

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install ruff mypy
      - run: ruff check app
      - run: mypy app --ignore-missing-imports

  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: pytest -v
```

Commit + push：

```bash
git add .github/workflows/ci.yml
git commit -m "ci: initial lint + test workflow"
git push
```

到 GitHub Actions 分頁看 run。兩個 job 會同時跑。

### 注意

- **兩個 job 都從頭 `pip install`** — 慢、浪費。Ch 8 會用 cache 修
- **mypy `--ignore-missing-imports`** 是偷懶，正式 production 要配 `pyproject.toml` 跟 lib stubs
- **`ruff check`** 沒 config file 會用 default 規則，通常已足夠

## `${{ ... }}` 語法

在 workflow 裡插變數用：

```yaml
- run: echo "Branch is ${{ github.ref_name }}"
- run: echo "PR number is ${{ github.event.pull_request.number }}"
- uses: some-action@v1
  with:
    token: ${{ secrets.GITHUB_TOKEN }}
```

可用變數來源：

| 來源 | 範例 |
|---|---|
| `github.*` | event 相關（`github.sha`、`github.ref_name`、`github.actor`） |
| `env.*` | 環境變數 |
| `secrets.*` | secret（Ch 9） |
| `steps.<id>.outputs.<name>` | 前 step 的 output |
| `needs.<job>.outputs.<name>` | 前 job 的 output |
| `matrix.*` | matrix 當前值（Ch 7） |
| `runner.*` | runner 資訊（`runner.os`、`runner.arch`） |

**注意**：`${{ }}` 是 GitHub 的 expression，不是 shell 的 `$(...)`。它在 YAML parse 階段求值，不是 shell 執行時。

## 動手練習

1. 把 tasktrack push 到 GitHub repo
2. 寫上面那份 `ci.yml`，commit + push
3. 到 Actions 分頁看兩個 job 跑（應該都綠）
4. 開一個 branch、改壞 `app/main.py`（加個 typo 讓 ruff 不開心），開 PR，看 CI 變紅
5. 改好、推新 commit，看 CI 重跑變綠

## 常見誤解

- 「**workflow 裡的 `name:` 必須獨一無二**」 — 不是。多個 workflow 可同名（UI 上會混亂，但 GitHub 不阻止）。unique 的是檔名
- 「**`run:` 可以多行用 `,` 分隔**」 — 用 `|` block scalar 分多行才對
- 「**`on: push` 會跑 tag push**」 — 會。要只 branch 用 `on.push.branches`；要只 tag 用 `on.push.tags`
- 「**`jobs` 裡的 key 是顯示名**」 — 不是。是 job ID（內部引用用）。`name:` 才是顯示名
- 「**checkout@v4 之後 `cd $GITHUB_WORKSPACE` 才能用檔案**」 — 不用。runner 一開始就在 workspace

## 驗收標準

- [ ] `tasktrack` 已 push 到 GitHub 並能看到 Actions 分頁
- [ ] `.github/workflows/ci.yml` 存在，`on:` 包含 `pull_request` 與 `push: branches: [main]`
- [ ] 兩個 job（lint、test）都能跑成功、綠
- [ ] 故意寫壞能觸發紅燈
- [ ] 你能指著 workflow 的每一行說「這是什麼意思」

## 自我檢核

- [ ] 我懂 events → jobs → steps 三層結構
- [ ] 我知道 `runs-on` 決定哪種機器、`ubuntu-latest` 是免費首選
- [ ] 我知道每個 job 是獨立乾淨 VM、job 之間預設不互通
- [ ] 我知道 `actions/checkout@v4` 在幹嘛、為什麼幾乎每次都要用
- [ ] 我認同「能在 shell 跑的就不要塞 YAML」這個原則

下一章深入 job 機制：matrix、`needs:`、runner 生命週期，這些決定你 pipeline 的拓樸。

→ [Ch 7 job 機制、matrix、runner 生命週期](./07-jobs-matrix-runner.md)
