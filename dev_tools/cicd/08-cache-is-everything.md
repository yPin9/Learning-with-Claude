# Ch 8 — cache 是一切

> 目標：搞懂 `actions/cache` 的 key 機制、看清 CI 慢的真正原因是 install 不是 test、把 `tasktrack` pipeline 從 3 分鐘壓到 30 秒。

## 先看真相：你的 CI 時間都花在哪

給一個典型未 cache 的 Python CI job 拆帳：

| 階段 | 時間 |
|---|---|
| runner 啟動 + checkout | ~20s |
| `setup-python` 裝 Python | ~5s |
| **`pip install` 裝 50 個依賴** | **~40s** |
| lint 或 pytest 本身 | ~5s |
| job teardown | ~5s |
| **總計** | **~75s** |

**真正 productive 的時間只有 5 秒**。其他 70 秒都在「setup」。

這就是為什麼 CI cache 是 CI/CD 課的核心 — 你不做 cache，50% 的 CI 時間都在裝套件，每次都裝。

## `actions/cache` 的心智模型

非常簡單：

```yaml
- uses: actions/cache@v4
  with:
    path: ~/.cache/pip
    key: pip-${{ hashFiles('requirements.txt') }}
    restore-keys: |
      pip-
```

兩件事發生：

1. **開始時**：用 `key` 去 cache store 查。
   - **命中**：把那個 cache 解壓到 `path`
   - **miss**：用 `restore-keys` 找 prefix match 的 cache 解壓（如果有）
2. **結束時（post-job）**：如果當初完全 miss，把 `path` 現在的內容壓成 cache、存起來，key 就是當初那個 key

**Key 是字串**，你決定它怎麼組。慣例：

- **包含 OS**（`${{ runner.os }}`）— 不同 runner 依賴不同
- **包含 lockfile hash**（`${{ hashFiles('requirements.txt') }}`）— 依賴變了 key 就變
- **可能包含 tool version**

### 例：完整寫法

```yaml
- uses: actions/cache@v4
  with:
    path: ~/.cache/pip
    key: ${{ runner.os }}-pip-${{ hashFiles('requirements.txt', 'requirements-dev.txt') }}
    restore-keys: |
      ${{ runner.os }}-pip-
```

`hashFiles` 是 GitHub Actions 的 expression function，對多個檔案算組合 SHA。一個檔案改一字節，hash 變。

### `restore-keys` 是什麼

萬一精確 key 沒命中（例如你改了 requirements.txt 多加一個 package），`restore-keys` 會往下掃 prefix：

```yaml
restore-keys: |
  ${{ runner.os }}-pip-                 # prefix，會找到舊版
```

拿到舊 cache 後，pip install 只會去裝「新增/變更」的 package，比從頭裝快很多。

**重點**：精確 key 命中後會 **不更新** cache（省時間，但新依賴沒進 cache）。只有精確 key miss 時才會在 post-job 更新。

## `setup-python` 內建 cache 是怎麼做的

Ch 7 看過：

```yaml
- uses: actions/setup-python@v5
  with:
    python-version: '3.12'
    cache: 'pip'
    cache-dependency-path: requirements.txt
```

它內部做的事：

```yaml
# 相當於：
- uses: actions/cache@v4
  with:
    path: ~/.cache/pip
    key: setup-python-${{ runner.os }}-pip-${{ python-version }}-${{ hashFiles('requirements.txt') }}
    restore-keys: |
      setup-python-${{ runner.os }}-pip-${{ python-version }}-
```

**自動化常見 case，不用自己寫**。但進階需求（cache 其他工具、自訂 key、cache volume 掛特定位置）還是要用 `actions/cache`。

`setup-node`、`setup-go`、`setup-java` 都有類似內建 cache。

## Cache 容量與淘汰

每個 repo 的 Actions cache 有 **10GB 上限**，超過會 LRU 淘汰。

- 每個 entry 有 **7 天 idle TTL**（沒被命中就失效）
- Cache miss 時生的新 cache 大小要合理（不要把整個 `/` 打包）

查看目前 cache：

```bash
gh cache list
# ID  KEY                    SIZE   CREATED
# ... pip-deadbeef           85MB   ...
```

清掉：

```bash
gh cache delete <id>
gh cache delete --all          # 核彈
```

### Cache scope

Cache 有 **branch scope** 的隔離：

- Default branch（通常 `main`）建立的 cache，**所有 branch 都能讀**
- Feature branch 建立的 cache，**只有自己能讀**（避免亂污染）
- PR branch 讀 cache 從：自己 → base branch → default branch

意思是：**第一次 PR 通常會 miss**，但只要你 merge 到 main 一次，後續 PR 就有 main 的 cache 可用。

## Docker layer cache

Dockerfile 的 layer cache（Ch 2）在 CI 裡也要處理。問題：GitHub runner 每次都是新 VM，本地 Docker cache 沒了。

三個做法：

### 1. Registry cache（最通用）

```yaml
- uses: docker/setup-buildx-action@v3
- uses: docker/build-push-action@v6
  with:
    context: .
    push: true
    tags: ghcr.io/you/tasktrack:latest
    cache-from: type=registry,ref=ghcr.io/you/tasktrack:buildcache
    cache-to: type=registry,ref=ghcr.io/you/tasktrack:buildcache,mode=max
```

把 Docker layer cache **存在 registry 裡另一個 tag**，下次 build 從 registry 拉。Ch 12 會正式配。

### 2. GHA cache（方便但有坑）

```yaml
cache-from: type=gha
cache-to: type=gha,mode=max
```

用 GitHub Actions 的 cache 當 Docker cache backend。簡單，但受 10GB 限制影響更明顯（Docker cache 通常大）。小專案可以、大專案要用 registry cache。

### 3. `actions/cache` 存 `/tmp/.buildx-cache`

老派做法，不推薦，現在 `type=gha` 取代它。略過。

## 實作：壓縮 `tasktrack` CI 時間

當前（Ch 7 結束時）：

```yaml
test:
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
```

第一次 push 跑這個：~70 秒（全部 cache miss）
第二次 push 跑這個：~15 秒（setup-python cache 命中）

如果你還想進一步壓（例如 Docker build 也 cache），加：

```yaml
build-image:
  needs: [lint, test]
  if: github.ref == 'refs/heads/main'
  runs-on: ubuntu-latest
  steps:
    - uses: actions/checkout@v4
    - uses: docker/setup-buildx-action@v3
    - uses: docker/build-push-action@v6
      with:
        context: .
        push: false
        tags: tasktrack:ci
        cache-from: type=gha
        cache-to: type=gha,mode=max
```

第一次 build：~60 秒。第二次（改一行 code）：~10 秒，因為 Ch 2 的 COPY 順序讓只有最後一層重建，Docker cache 存在 GHA cache 裡拉回來。

## `hashFiles` 的陷阱

`hashFiles('**/requirements.txt')` 跟 `hashFiles('requirements.txt')` 不一樣：

- 前者：glob，找全 repo 所有 requirements.txt
- 後者：單檔

**要穩**：指定確切路徑。glob 在 monorepo 會誤中。

另一個坑：`hashFiles` 對不存在的檔案，產生空 hash。你可能以為「沒檔案就不 cache」，實際是「所有沒檔案的 key 都一樣」，會污染。

## 動手練習

1. 確認你 workflow 有 `setup-python` + `cache: 'pip'`
2. Push 兩次（第二次改 `app/main.py` 一行），對比兩次的 job 時間
3. 在 Actions run 詳情頁看每個 step 的時間，驗證 `pip install` 從 ~40s → < 5s
4. （進階）加一個 `build-image` job 用 `type=gha` cache，push 兩次對比 build 時間
5. `gh cache list` 看你 repo 的 cache 存了什麼

## 常見誤解

- 「**`actions/cache` 會加速當次 run**」 — 不會。第一次 run 是 miss，第二次起才快
- 「**key 一樣就會更新 cache**」 — 不會。精確命中 key 的 run **不會更新**。要更新只能等 key 變
- 「**PR 永遠不會命中 cache**」 — 會命中 main 的 cache（如果 main 有建過）
- 「**cache 大比較好**」 — 不。存 / 還原 cache 自己要時間，太大會反效果。通常 < 500MB 有意義
- 「**把 `/` 整個當 path 最省事**」 — 會把整個 runner filesystem 打包，慢到天荒地老且超過 10GB limit

## 驗收標準

- [ ] `tasktrack` workflow 有 `setup-python` + `cache: 'pip'`
- [ ] 第二次 push 的 CI 時間 < 第一次的一半
- [ ] `gh cache list` 看得到 pip 相關 cache entry
- [ ] 你理解 cache scope（main vs feature branch）的讀取規則

## 自我檢核

- [ ] 我知道 CI 慢的主因是 install，不是 test
- [ ] 我會寫 `actions/cache` 的 key + restore-keys
- [ ] 我懂精確 key hit 不更新、miss + restore-keys hit 才更新
- [ ] 我知道 Docker layer cache 在 CI 裡有 registry / gha 兩種 backend
- [ ] 我避免用 `hashFiles('**/...')` glob 除非確定

下一章談 CI 最危險的主題：secrets。怎麼存、怎麼用、怎麼不小心 leak 出去。

→ [Ch 9 secrets、環境變數、OIDC](./09-secrets-oidc.md)
