# Ch 11 — 進階：service container、artifact、concurrency

> 目標：在 CI 跑真正的 integration test（接 Postgres）、用 artifact 傳 build 產物、用 concurrency 避免 PR 被重複跑。

## service container：在 CI 裡跑 Postgres

Ch 4 本地用 compose 起 Postgres。在 CI 裡怎麼做？Actions 有個內建機制叫 **service container**：

```yaml
jobs:
  integration:
    runs-on: ubuntu-latest
    services:
      postgres:                                  # ← service 名變 hostname
        image: postgres:16
        env:
          POSTGRES_PASSWORD: devpass
          POSTGRES_DB: tasktrack
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U postgres"
          --health-interval 3s
          --health-timeout 3s
          --health-retries 10
    env:
      DATABASE_URL: postgresql+psycopg://postgres:devpass@localhost:5432/tasktrack
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: 'pip'
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: pytest tests/integration -v
```

幾個關鍵點：

1. **`services.<name>` 就是 docker run**：那 key 成為 hostname
2. **`options: --health-*`** 會讓 Actions 等 service healthy 才跑 step
3. **`ports:` 映射的是 runner 的 localhost**：所以 `DATABASE_URL` 連 `localhost:5432`，不是 `postgres:5432`
4. **`env:` 在 job 層級**：所有 step 都會拿到

### service 跟 compose 的差別

compose 是 service-to-service 通訊（`db` 是 hostname）。service container 是 **service-to-runner** 通訊（runner 透過 localhost 連到 service，service 開在 docker network 上但 port 映射到 runner）。

這是歷史因素，大部分時候不影響你。

### 真的讓 `tasktrack` 在 CI 跑 integration test

先把 tests 分一下。新增 `tests/integration/`：

```python
# tests/integration/conftest.py
import os
import pytest
from fastapi.testclient import TestClient

# 從 env 吃 DATABASE_URL，不 fallback
assert "DATABASE_URL" in os.environ, "integration tests require DATABASE_URL"

from app.db import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
def _reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
```

`tests/integration/test_tasks_pg.py` 可以先用跟 unit 測試相同的內容（測試層要跟 `tests/unit/` 分，unit 用 SQLite、integration 用 Postgres）。

讓我們修正 workflow：

```yaml
jobs:
  unit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/setup-python-env
      - run: pytest tests/unit -v

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
```

unit 和 integration 分兩個 job，平行跑。integration 多了 20 秒左右的 Postgres 啟動時間，值得。

## artifact：job 之間傳檔

有時 build job 要把結果（binary、report、dist 資料夾）給下游 job 用。`actions/upload-artifact` + `download-artifact`：

```yaml
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo "hello" > build/output.txt
      - uses: actions/upload-artifact@v4
        with:
          name: build-output
          path: build/
          retention-days: 7

  test:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: build-output
          path: build/
      - run: cat build/output.txt                 # → hello
```

### 用途

- **上傳 coverage report**：`pytest --cov --cov-report=xml`，傳給 Codecov 或單純收藏
- **上傳測試結果 XML**：JUnit format，讓 GitHub 幫你解析失敗
- **傳 build binary 給後續 deploy job**

### 重要注意

- **Artifact 預設 90 天過期**（可調，`retention-days`）
- **大小上限**：repo 層級 artifact 總量、單檔 2GB。別亂傳整個 `node_modules/`
- **Artifact 可以在 UI 下載**：你或其他人能從 Actions run 頁面點下載。**別傳 secret 內容進去**（log 也同理）

### 想 job 間傳小字串？用 output

Artifact 是檔案。小字串（version、sha、boolean）用 job output（Ch 7 教過）更輕。

## concurrency：不要讓同個 PR 跑好幾版

場景：你改 code push 一個 PR、發現 typo 再 push、再發現 typo 再 push。**GitHub 預設會為每次 push 起 CI**。如果 CI 跑 5 分鐘，你連續 push 三次，會同時有三個 CI 在跑（前兩個其實沒意義）。

解法：

```yaml
concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true
```

放在 workflow 頂層或 job 層。

- **`group:`** — 同個 group 的 run 會互斥。按 ref（branch）分組，同一 branch 一次只跑一個
- **`cancel-in-progress: true`** — 新 run 來時，取消舊 run

### 什麼時候不要 cancel

Deploy 到生產的 workflow 不應該 cancel-in-progress — 你不想中斷部署：

```yaml
concurrency:
  group: deploy-${{ github.ref }}
  cancel-in-progress: false        # ← 排隊跑，不 cancel
```

### 進階：不同 event 分組

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true
```

PR 時用 PR number 分組（同 PR 重推會取消舊 CI），push 時用 ref。

## 實作：`tasktrack` 完整 CI v3

把 Part 2 學的全部整合：

```yaml
# .github/workflows/ci.yml
name: CI

on:
  pull_request:
    branches: [main]
  push:
    branches: [main]

concurrency:
  group: ci-${{ github.workflow }}-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/setup-python-env
      - run: ruff check app tests
      - run: mypy app --ignore-missing-imports

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
    needs: [lint, unit, integration]
    runs-on: ubuntu-latest
    steps:
      - run: echo "OK"
```

這份 workflow：

- lint、unit（matrix）、integration **三組平行**
- unit 只在 3.12 上傳 coverage（避免重複）
- integration 用 service container 跑 Postgres
- `all-green` 是匯總 gate（加到 branch protection rule）
- concurrency 避免重複跑

Push 試試看。第一次全 miss cache，~2 分鐘。第二次 < 30 秒。

## 動手練習

1. 把 workflow 改成上面這版
2. 在 `tasktrack/tests/` 新建 `unit/` 和 `integration/` 子目錄（把原本 test_tasks.py 複製一份到 unit/ 作為 unit test，新增一個 integration/ 用 Postgres 的變體）
3. Push，確認三個 job 平行、全綠
4. 開一個 PR，第一次 push 後立刻再 push 一個 commit，看舊 run 被 cancel
5. 到 Actions 詳情頁點 Artifacts，下載 coverage.xml

## 常見誤解

- 「**service container 的 host 是 service name**」 — 不是。是 `localhost` + 映射 port
- 「**artifact 可以無限大**」 — 單 artifact 有 limit、repo 總量有 quota
- 「**artifact 檔案隱私**」 — 你 repo 有 read 權限的都能下載。別傳 secret 進 artifact
- 「**concurrency cancel-in-progress 永遠要 `true`**」 — deploy 情境要 `false`（排隊）
- 「**service container 跟 compose 是一樣的東西**」 — 觀念接近，語法不同、範圍不同。service container 只活在那個 job 的 runner 上

## 驗收標準

- [ ] 有 `integration` job、用 service container 跑 Postgres
- [ ] 有 `concurrency:` 避免重複 run
- [ ] 有至少一個 artifact（coverage 或其他）
- [ ] CI 結束後 Actions UI 看得到 artifact 可下載
- [ ] 所有 job 平行跑、整份 workflow 5 分鐘內結束

## 自我檢核

- [ ] 我會在 Actions 配 service container、知道 host 用 localhost
- [ ] 我會用 upload/download-artifact 在 job 間傳檔
- [ ] 我知道 concurrency group + cancel-in-progress 的取捨（CI 要 cancel、deploy 不要）
- [ ] 我能拆 unit vs integration test 兩個 job

Part 2 結束。練習 B 把這章的整合版變成完整的 PR CI。

→ [練習 B：為 tasktrack 設計完整 PR CI](./practice-b-tasktrack-pr-ci.md)
