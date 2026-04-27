# 練習 B — 為 tasktrack 設計完整 PR CI

> 目標：用 Ch 6–11 的所有技巧，為 `tasktrack` 寫一份完整的 PR CI — PR 觸發時平行跑 lint + type check + unit test + integration test，5 分鐘內完成。

## 任務規格

### 必備 job

| Job | 責任 | 時間上限 |
|---|---|---|
| `lint` | ruff check，格式檢查 | 30 秒 |
| `typecheck` | mypy strict mode | 1 分鐘 |
| `unit` | pytest unit（矩陣 Python 3.11 + 3.12） | 1 分鐘 |
| `integration` | pytest integration（用 Postgres service） | 2 分鐘 |
| `all-green` | 匯總 gate | 10 秒 |

### 必備機制

- ✅ 正確觸發（`on: pull_request` + `on: push branches: main`）
- ✅ `concurrency` 取消重複 run
- ✅ `permissions:` 最小化（read-only）
- ✅ Python 依賴 cache（`setup-python cache: 'pip'` 或 `actions/cache`）
- ✅ `setup-python-env` composite action（複用 Ch 10 成果）
- ✅ 上傳 coverage 為 artifact
- ✅ 匯總 job `all-green` needs 所有測試 job

### 驗收標準

- [ ] 整個 workflow 5 分鐘內完成（第二次 run，cache warm）
- [ ] 所有 job 平行（DAG 最終節點是 `all-green`）
- [ ] 故意改壞會讓某個 job 紅、其他照跑（`fail-fast: false`）
- [ ] PR 連續推兩個 commit，第一個 run 被取消
- [ ] `all-green` 可作為 branch protection required check

## 實作步驟建議

### Step 1：整理測試目錄

把 `tests/` 重整成：

```
tests/
├── __init__.py
├── unit/
│   ├── __init__.py
│   ├── conftest.py        # 用 SQLite、跟 Ch 0 一樣
│   └── test_tasks.py
└── integration/
    ├── __init__.py
    ├── conftest.py        # 要求 DATABASE_URL（Postgres）
    └── test_tasks_pg.py
```

### Step 2：`pyproject.toml`（可選但推薦）

把 ruff、mypy、pytest config 集中：

```toml
[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "N", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.12"
strict = true
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
```

### Step 3：設計 workflow

骨架：

```yaml
name: CI
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

concurrency:
  group: ci-${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  lint: ...
  typecheck: ...
  unit: ...
  integration: ...
  all-green: ...
```

### Step 4：實作各 job

一次一個，push 驗證。

### Step 5：調優

第一次通後，看 Actions 每 job 時間。哪個拖到 5 分鐘？通常是：

- `pip install` 沒命中 cache → 檢查 `setup-python` 的 cache-dependency-path
- matrix 沒必要 → 3.11 不測就拿掉
- `apt-get install` 裝 Postgres client → 通常不需要（service container 有）

### Step 6：branch protection

到 repo Settings → Branches → Add rule for `main`：

- ✅ Require pull request before merging
- ✅ Require status checks to pass → 選 `all-green`
- ✅ Require branches to be up to date before merging（可選）

## 完整參考解答

**寫完再看！**

<details>
<summary>點開參考 workflow（.github/workflows/ci.yml）</summary>

```yaml
name: CI

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

concurrency:
  group: ci-${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

permissions:
  contents: read

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/setup-python-env
      - run: ruff check app tests

  typecheck:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/setup-python-env
      - run: mypy app

  unit:
    strategy:
      fail-fast: false
      matrix:
        python: ['3.11', '3.12']
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/setup-python-env
        with:
          python-version: ${{ matrix.python }}
      - run: pytest tests/unit --cov=app --cov-report=xml -v
      - uses: actions/upload-artifact@v4
        if: matrix.python == '3.12'
        with:
          name: coverage
          path: coverage.xml
          retention-days: 7

  integration:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: devpass
          POSTGRES_DB: tasktrack
        ports: [5432:5432]
        options: >-
          --health-cmd "pg_isready -U postgres"
          --health-interval 3s
          --health-retries 10
    env:
      DATABASE_URL: postgresql+psycopg://postgres:devpass@localhost:5432/tasktrack
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/setup-python-env
      - run: pytest tests/integration -v

  all-green:
    needs: [lint, typecheck, unit, integration]
    runs-on: ubuntu-latest
    if: always()
    steps:
      - run: |
          if [ "${{ contains(needs.*.result, 'failure') }}" = "true" ] \
             || [ "${{ contains(needs.*.result, 'cancelled') }}" = "true" ]; then
            echo "some upstream job failed or was cancelled"
            exit 1
          fi
          echo "all green"
```

</details>

<details>
<summary>點開參考 setup-python-env composite action</summary>

```yaml
# .github/actions/setup-python-env/action.yml
name: Setup Python env
description: Setup Python + cache + install deps
inputs:
  python-version:
    required: false
    default: '3.12'
runs:
  using: composite
  steps:
    - uses: actions/setup-python@v5
      with:
        python-version: ${{ inputs.python-version }}
        cache: 'pip'
        cache-dependency-path: |
          requirements.txt
          requirements-dev.txt
    - shell: bash
      run: pip install -r requirements.txt -r requirements-dev.txt
```

</details>

### 解答要點

- **`all-green` 的 `if: always()` + `contains(needs.*.result, 'failure')`**：讓它無論上游狀態都跑，再自己判斷。如果只寫 `needs: [...]`、上游 fail 時 `all-green` 會 skip（不是 fail），branch protection 看到 skip 不會擋 merge
- **coverage 只在 3.12 上傳**：matrix 三份一樣的 coverage 沒意義
- **`integration` 不進 matrix**：Postgres 啟動成本高，跑一次就好
- **`permissions: contents: read`**：最小權限。push image 的 job（Ch 12）才升為 write

## 測試用例

```bash
# 觸發 CI
git checkout -b practice-b
git commit --allow-empty -m "ci: trigger"
git push --set-upstream origin practice-b
gh pr create --fill

# 第二次 push 看 cancel
git commit --allow-empty -m "another commit"
git push

# 到 Actions 頁面看：
# - 兩個 run，第一個 status = cancelled
# - 第二個 5 分鐘內全綠

# 測試 branch protection
# 到 Settings → Branches → 加 rule，require all-green
# 現在沒 all-green 綠就不能 merge
```

## 常見卡點

- **mypy 一堆錯**：`app/` 加 type hint 或 `# type: ignore`。Ch 6 先 `--ignore-missing-imports` 是偷懶
- **pytest 找不到 tests/unit**：確認有 `__init__.py`
- **integration 連不到 Postgres**：檢查 `DATABASE_URL` 拼字、Postgres healthcheck 有沒有通
- **`all-green` 該擋 PR 但沒擋**：branch protection 要點 `all-green` 這個 check name 加到 required

## 自我檢核

- [ ] 我能從零設計一份 PR CI workflow、知道每個 job 為什麼存在
- [ ] 我知道 `all-green` gate 的 `if: always()` 模式與原因
- [ ] 我會把 branch protection rule 綁到這個 gate
- [ ] 我第二次 push 時看到 cache 命中、整份 < 5 分鐘

Part 3 開始：把產品交付出去（image → registry → release）。

→ [Ch 12 Container registry 與 tag 策略](./12-container-registry.md)
