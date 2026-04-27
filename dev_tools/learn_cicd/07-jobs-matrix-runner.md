# Ch 7 — job 機制、matrix、runner 生命週期

> 目標：搞懂「每個 job 一台乾淨 VM」這條原理造成的全部後果、用 matrix 跑多版本、用 `needs:` 把 jobs 串成 DAG。

## 原理：每個 job 一台全新 VM

這句話很簡單，但後果深遠。整理一下它意味什麼：

1. **job 之間不共享檔案** — job A build 的檔在 job B 的 VM 不存在。要傳？用 artifact 或 cache
2. **job 之間不共享環境變數** — job A `export FOO=bar`，job B 看不到
3. **job 預設平行** — 沒依賴就一起起，10 個 job 就 10 台 VM 同時跑
4. **每次 run 都是全新** — 上次 run 裝的 package 這次沒了（Ch 8 cache 處理這個）
5. **VM 啟動要 ~20–30 秒** — 你 pipeline 的 floor 大概就是這個時間

第 5 點很常被忽略。你就算 job 裡只跑 `echo hello`，那個 job 還是會花 30 秒。**不要動不動就拆 job**。

## 什麼時候該拆 job、什麼時候不該

拆：

- **平行可賺時間** — lint 跟 test 可以並行，分 job
- **邏輯獨立** — build image 跟 push image 可以分（push 用 secret、build 不用）
- **runner 不同** — 一個要 ubuntu、一個要 macos
- **需要 fail-fast 或 skip** — 只想 main push 才 build image，PR 時 skip

不拆：

- 「我覺得分開比較乾淨」 — 啟動成本不值得
- 兩個 step 其實依賴相同 setup（裝 Python、pip install）— 拆了各裝一次很蠢

## matrix：同一 job 跑多組參數

想測試 Python 3.11 + 3.12 在 Ubuntu + macOS 都能過？用 matrix：

```yaml
jobs:
  test:
    strategy:
      matrix:
        python: ['3.11', '3.12']
        os: [ubuntu-latest, macos-latest]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: pytest -v
```

這會展開成 `2 × 2 = 4` 個 job，全部平行跑。

### `fail-fast`

預設 matrix 一個 job 紅，其他同 matrix 的 job **會被取消**。這叫 fail-fast，通常是你要的（一個失敗，debug 用不著等其他）。

不想要？

```yaml
strategy:
  fail-fast: false       # 所有 matrix 都跑完
  matrix:
    python: ['3.11', '3.12']
```

想看是不是某個版本特有問題時關掉有用。

### `include` / `exclude`：特例處理

只想在 Python 3.12 跑 macOS、不要所有組合？

```yaml
strategy:
  matrix:
    python: ['3.11', '3.12']
    os: [ubuntu-latest]
    include:
      - python: '3.12'
        os: macos-latest
```

這樣會有 3 個 job：`{3.11, ubuntu}`、`{3.12, ubuntu}`、`{3.12, macos}`。

`exclude` 反過來（先全展開再排除）。兩個可以同時用但通常 `include` 就夠。

## `needs:`：把 job 串成 DAG

`needs: <job-id>` 讓一個 job 等另一個完成才開始：

```yaml
jobs:
  lint:
    runs-on: ubuntu-latest
    steps: [...]

  test:
    runs-on: ubuntu-latest
    steps: [...]

  build:
    needs: [lint, test]          # lint 和 test 都綠才跑
    runs-on: ubuntu-latest
    steps: [...]

  deploy:
    needs: build
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps: [...]
```

畫出來是：

```
     lint ──┐
             ├──► build ──► deploy (只在 main)
     test ──┘
```

`needs` 可以是字串（單個）或陣列（多個）。**`needs` 成為預設 output 傳遞機制**（下節）。

### job output：job 之間傳字串

```yaml
jobs:
  version:
    runs-on: ubuntu-latest
    outputs:
      semver: ${{ steps.get-version.outputs.semver }}
    steps:
      - id: get-version
        run: echo "semver=1.2.3" >> $GITHUB_OUTPUT

  build:
    needs: version
    runs-on: ubuntu-latest
    steps:
      - run: echo "Building ${{ needs.version.outputs.semver }}"
```

兩個機制：

1. `steps.<id>.outputs.<name>` — step 輸出。寫法：`echo "name=value" >> $GITHUB_OUTPUT`
2. `jobs.<id>.outputs.<name>` — 把 step output 暴露給下游 job

## `if:` 條件執行

Step 層、job 層都可以用：

```yaml
jobs:
  deploy:
    if: github.event_name == 'push' && github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - name: Deploy
        if: success()              # 預設就是 success()，可省
        run: ./deploy.sh
      - name: Notify failure
        if: failure()              # 前面 step 失敗才跑
        run: ./notify.sh
```

常見 `if` pattern：

```yaml
if: github.ref == 'refs/heads/main'        # 只 main
if: startsWith(github.ref, 'refs/tags/v')  # tag 推送
if: github.event_name == 'pull_request'    # 只 PR
if: always()                               # 無論成敗都跑（cleanup 用）
if: contains(github.event.head_commit.message, '[skip ci]') == false
```

## `setup-python` 不只裝 Python

GitHub 官方的 `actions/setup-python@v5` 做三件事：

1. 裝指定版本（從 cache 或下載）
2. 設 `PATH`
3. **（如果你加）cache pip 依賴**

第三件事很重要：

```yaml
- uses: actions/setup-python@v5
  with:
    python-version: '3.12'
    cache: 'pip'                           # ← 自動 cache pip
    cache-dependency-path: |
      requirements.txt
      requirements-dev.txt
```

加了這幾行，你的 pip install 在 CI 從 30 秒變 3 秒。Ch 8 會詳細講 cache 原理，這裡先埋這個寶。

## 實作：升級 `tasktrack` 的 CI

Ch 6 的 workflow 太簡單，現在加 matrix + `needs` + cache：

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
          cache: 'pip'
      - run: pip install ruff mypy
      - run: ruff check app
      - run: mypy app --ignore-missing-imports

  test:
    strategy:
      fail-fast: false
      matrix:
        python: ['3.11', '3.12']
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
          cache: 'pip'
          cache-dependency-path: |
            requirements.txt
            requirements-dev.txt
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: pytest -v

  all-green:
    needs: [lint, test]
    runs-on: ubuntu-latest
    steps:
      - run: echo "All checks passed"
```

幾個設計：

- `test` 在 Python 3.11 + 3.12 各跑一次
- `all-green` 是個匯總 job — 在 GitHub PR required checks 裡，你可以只要求 `all-green` 綠（而不是逐一列出，matrix 展開的 job 名難列）
- `cache: 'pip'` 讓 pip install 快起來（下次 run）

## 動手練習

1. 把 workflow 改成上面這版，push 看 run
2. 第一次應該會看 cache **miss**（no cache saved），第二次 run 會看到 cache **hit**
3. 故意讓 Python 3.11 的 test 失敗（加個 `if sys.version_info[1] == 11: raise`），看 matrix 的其他 job 被取消
4. 改成 `fail-fast: false`，再觸發一次，看所有 matrix job 跑完才停
5. 改回去

## 常見誤解

- 「**job 可以用 `cd` 進入別的 job 的資料夾**」 — 不能。跨 job 要 artifact / cache
- 「**matrix 展開會吃很多免費額度**」 — 會。`3 × 3 × 2 = 18` job 跑 5 分鐘就 90 分鐘計價。小心組合爆炸
- 「**`needs:` 的 job 失敗下游一定 skip**」 — 下游預設 skip，但用 `if: always()` 可強制跑（cleanup）
- 「**`cache: 'pip'` 是 magic**」 — 不是。它用 `actions/cache` 包裝，key 是 `hashFiles(cache-dependency-path)` + python version
- 「**matrix 的 job output 好傳**」 — 難。matrix output 要用 `outputs:` + `fromJson`，有點痛。避免在 matrix job 間傳值

## 驗收標準

- [ ] `test` job 有 matrix，跑 Python 3.11 + 3.12
- [ ] 有 `all-green` 或類似的匯總 job，`needs: [lint, test]`
- [ ] `setup-python` 加了 `cache: 'pip'`
- [ ] 你能看懂 Actions UI 上 job 拓樸圖（dependencies 用線連起來）

## 自我檢核

- [ ] 我知道 job 啟動成本約 20–30 秒，不該亂拆
- [ ] 我會寫 matrix、知道 `include` / `exclude` 語法
- [ ] 我懂 `fail-fast` 的預設行為與切換
- [ ] 我會用 `needs:` 串 job、用 `if:` 條件執行
- [ ] 我知道 `setup-python` 附帶的 pip cache 是怎麼回事

下一章把「CI 慢」這個問題一次打爆：cache 原理與策略。

→ [Ch 8 cache 是一切](./08-cache-is-everything.md)
