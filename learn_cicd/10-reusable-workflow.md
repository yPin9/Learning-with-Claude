# Ch 10 — reusable workflow 與 composite action

> 目標：分清 reusable workflow（`workflow_call`）與 composite action 的差異、什麼時候用哪個、把「build + push image」這類重複邏輯抽出來重用。

## 為什麼需要抽象

你已經有 `tasktrack` 的 CI。假設公司另外三個 repo 也是 FastAPI + Docker，每個 repo 的 workflow 長得差不多：

- checkout
- setup-python + cache
- build Docker image
- push 到 GHCR
- release 策略

**四個 repo 各自抄一份 YAML** — 改一次要改四個地方，不出事才怪。

GitHub Actions 提供兩個抽象機制：

1. **Reusable workflow**（`workflow_call`）— **整個 job 級**的重用
2. **Composite action** — **step 級**的重用

兩個都有用，選哪個看你要抽什麼。

## Composite action

### 用途

把幾個 step 組合成一個可叫的 action。**像 shell 函式**。

### 定義

放在 repo 裡（自己用）或獨立 repo（公開）。慣例位置：

```
your-repo/
└── .github/
    └── actions/
        └── setup-python-env/
            └── action.yml
```

`action.yml` 長這樣：

```yaml
name: 'Setup Python + cache + install'
description: 'Python 環境一次到位'
inputs:
  python-version:
    description: 'Python version'
    required: false
    default: '3.12'
  requirements:
    description: 'path to requirements file'
    required: false
    default: 'requirements.txt'
runs:
  using: 'composite'
  steps:
    - uses: actions/setup-python@v5
      with:
        python-version: ${{ inputs.python-version }}
        cache: 'pip'
        cache-dependency-path: ${{ inputs.requirements }}
    - shell: bash
      run: pip install -r ${{ inputs.requirements }}
```

注意：

- `runs.using: 'composite'`
- 每個 `run:` **必須指定 `shell:`**（composite 的坑）
- Input 用 `${{ inputs.X }}` 取，不是 `github.event.inputs`

### 使用

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/setup-python-env    # ← 本地路徑
        with:
          python-version: '3.12'
          requirements: 'requirements-dev.txt'
      - run: pytest
```

或從別 repo 用：

```yaml
- uses: your-org/shared-actions/setup-python-env@v1
```

### 特性

- 跑在 **呼叫 job 的同一台 VM** — 不會開新 VM，啟動成本 0
- 可以跟其他 step 混用、共享 env、共享 workspace 檔案
- **不能用 `uses:` 呼叫其他 composite action 時相容性有點微妙**（歷史原因），大部分情境可以，複雜時會踩雷

## Reusable workflow

### 用途

把整個 workflow（一或多個 job）抽出來，別的 workflow 可以呼叫。**像叫子程序**。

### 定義

一個普通 workflow 檔，只是 `on:` 用 `workflow_call`：

```yaml
# .github/workflows/build-and-push.yml
name: Build and Push Image

on:
  workflow_call:
    inputs:
      image-name:
        required: true
        type: string
      push:
        required: false
        type: boolean
        default: false
    secrets:
      registry-token:
        required: false
    outputs:
      digest:
        value: ${{ jobs.build.outputs.digest }}

jobs:
  build:
    runs-on: ubuntu-latest
    outputs:
      digest: ${{ steps.push.outputs.digest }}
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4
      - uses: docker/setup-buildx-action@v3
      - if: inputs.push
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - id: push
        uses: docker/build-push-action@v6
        with:
          context: .
          push: ${{ inputs.push }}
          tags: ghcr.io/${{ github.repository }}/${{ inputs.image-name }}:${{ github.sha }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

### 使用

```yaml
# .github/workflows/ci.yml
jobs:
  test:
    runs-on: ubuntu-latest
    steps: [...]

  build-image:
    needs: test
    uses: ./.github/workflows/build-and-push.yml    # 本 repo
    with:
      image-name: tasktrack
      push: ${{ github.ref == 'refs/heads/main' }}

  use-digest:
    needs: build-image
    runs-on: ubuntu-latest
    steps:
      - run: echo "Built digest ${{ needs.build-image.outputs.digest }}"
```

或跨 repo：

```yaml
uses: your-org/shared-workflows/.github/workflows/build-and-push.yml@v1
```

### 特性

- 呼叫一個 reusable workflow = **開新 job（可能多個 job）**，有啟動成本
- 可以有自己的 `permissions:`、`secrets:`、matrix、`needs:`
- 輸出用 `outputs:`，和一般 job output 一樣引用
- **secrets 不會自動繼承**：要 `secrets: inherit` 或顯式傳

## 兩者對照

| 面向 | Composite action | Reusable workflow |
|---|---|---|
| 定位 | step 級 | job 級 |
| 執行位置 | 呼叫 job 的 VM 內 | 獨立新 job |
| 啟動成本 | 0 | ~20–30s |
| 可有自己 runner？ | ❌ 繼承 | ✅ 自選 |
| 可有自己 matrix？ | ❌ | ✅ |
| 跨 repo 呼叫 | ✅ | ✅ |
| 可以叫 step | ✅（`uses:`、`run:`） | ❌（只能 job 串 job） |
| 典型用途 | 「裝環境」「generate file」「設 env」 | 「build + push」「run full test」「deploy」 |

**判斷規則**：

- 你要抽的是 **幾個 step 的組合**？→ composite
- 你要抽的是 **一整套流程（有自己的 runner 選擇、matrix、output）**？→ reusable workflow
- **不確定？先用 composite**。啟動成本 0、遷移到 reusable 容易

## 實作：重構 `tasktrack`

### Composite：把「setup Python + install deps」抽掉

Ch 7 那個 workflow 每個 job 都寫：

```yaml
- uses: actions/setup-python@v5
  with:
    python-version: '3.12'
    cache: 'pip'
    cache-dependency-path: |
      requirements.txt
      requirements-dev.txt
- run: pip install -r requirements.txt -r requirements-dev.txt
```

抽成 `.github/actions/setup-python-env/action.yml`：

```yaml
name: Setup Python env
description: 'Setup Python with cache and install deps'
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

`ci.yml` 的 `test` job 變成：

```yaml
test:
  strategy:
    matrix:
      python: ['3.11', '3.12']
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: ./.github/actions/setup-python-env
      with:
        python-version: ${{ matrix.python }}
    - run: pytest -v
```

### Reusable：把「build + push image」抽掉

先前那份 `build-and-push.yml` 放 `.github/workflows/`。

`ci.yml` 的 build job：

```yaml
build-image:
  needs: [lint, test]
  uses: ./.github/workflows/build-and-push.yml
  with:
    image-name: tasktrack
    push: ${{ github.event_name == 'push' && github.ref == 'refs/heads/main' }}
```

注意：

- 呼叫 reusable workflow 的 job **沒有 `steps:`、`runs-on:`**，只有 `uses:` 和 `with:`
- `if:` 條件判斷放在 `jobs.<name>.if` 外層，或用 `with: push: <expr>` 傳給子 workflow（像上面）

## 坑集合

- **Composite action 的 `run:` 忘了 `shell:`**：錯誤訊息不直觀
- **Reusable workflow secrets 沒傳進去**：預設不繼承，要 `secrets: inherit` 或顯式列
- **Composite 裡用 `${{ env.X }}` 抓外部 env**：可以，但想寫 env 給呼叫者看、composite 沒有 `env:` 輸出機制 — 要用 output
- **Reusable workflow 被呼叫時，`github.*` context 是 **呼叫者的**，不是 reusable 本身**（通常這是你要的，但偶爾會搞混）
- **同檔內引用 composite 用 `./`**，跨 repo 用 `owner/repo/path@ref`，**語法不同**

## 動手練習

1. 把 `ci.yml` 的 setup Python 部分抽成 composite action
2. Push，確認 CI 還是通
3. 把 build image 邏輯抽成 reusable workflow（先不 push，只驗 build 能跑）
4. 在 `ci.yml` 用 `uses: ./.github/workflows/build-and-push.yml` 呼叫
5. 觀察 Actions UI：composite 不多 job、reusable 多一個 job

## 常見誤解

- 「**composite 比 reusable 好**」 — 看用途。整個流程（含 matrix、permissions）要抽就用 reusable
- 「**共用 action 必須放公開 repo**」 — 不用。同 repo `./` 引用、private repo 內也可（要設定 access）
- 「**Action 版本用 `@main` 最新最方便**」 — 會被破壞。用 `@v1` semver tag 或 sha pin
- 「**Reusable workflow 的 `needs:` 在呼叫者那邊生效**」 — 對，呼叫者 job 可以 `needs: build-image`
- 「**抽出去比較乾淨，能抽就抽**」 — 過早抽象是 sin。用兩次以上再抽

## 驗收標準

- [ ] `tasktrack` 有至少一個 composite action（放 `.github/actions/`）
- [ ] `tasktrack` 有至少一個 reusable workflow（放 `.github/workflows/`）
- [ ] CI 仍能成功跑
- [ ] 你能說明兩者差異、各自適合場景

## 自我檢核

- [ ] 我分得清 composite action（step 級、同 VM）與 reusable workflow（job 級、新 VM）
- [ ] 我會寫 composite action 的 `action.yml`、知道 `shell:` 必填
- [ ] 我會寫 reusable workflow 的 `on: workflow_call` 與 inputs/outputs/secrets
- [ ] 我知道 secrets 不自動繼承、要顯式或 `secrets: inherit`
- [ ] 我認同「用兩次才抽」的原則

下一章整合所有 Part 2 學的：service container 跑 Postgres、artifact 上傳下載、concurrency 防重複 run。

→ [Ch 11 進階：service container、artifact、concurrency](./11-services-artifacts-concurrency.md)
