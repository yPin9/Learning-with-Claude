# Ch 15 — 完整 pipeline 審視

> 目標：把 Ch 0–14 學的全部拉高檢視一次 — 拓樸、失敗診斷、成本、觀測性。這章不教新東西，它是幫你把 Part 1–3 收攏成一張能獨立運作的地圖。

## 最終 pipeline 長這樣

PR 與 main 的兩條路徑：

```
                    ┌──── PR opened / updated ────┐
                    │                              │
                    ▼                              │
         ┌─────────────────────────┐               │
         │  ci.yml                 │               │
         │  concurrency: cancel    │               │
         │                         │               │
         │  ┌───┐ ┌────┐ ┌────┐   │               │
         │  │lnt│ │tc  │ │unit│   │ ← 平行         │
         │  └───┘ └────┘ └─┬──┘   │               │
         │                 │      │               │
         │            ┌────▼────┐ │               │
         │            │integ    │ │ ← service:postgres
         │            └────┬────┘ │               │
         │                 ▼      │               │
         │            all-green   │ ← gate 給 branch protection
         └─────────────────────────┘              │
                                                   │
PR merge to main ──────────────────────────────────┘
                    │
                    ▼
         ┌─────────────────────────┐
         │  cd.yml                 │
         │  on: push: main         │
         │                         │
         │  build + push :sha-XXX  │
         │              :main      │
         └────────────┬────────────┘
                      │
  打 tag v0.1.0 ──────┘
                      │
                      ▼
         ┌─────────────────────────┐
         │  release.yml            │
         │  on: push: tags: v*     │
         │                         │
         │  build + push :0.1.0    │
         │              :0.1       │
         │              :latest    │
         │  + GitHub Release       │
         └────────────┬────────────┘
                      │ workflow_run
                      ▼
         ┌─────────────────────────┐
         │  deploy.yml（可選）     │
         │  environment: production│
         │  SSH / K8s / Cloud Run  │
         └─────────────────────────┘
```

三個 workflow 檔案對應三個觸發點。

## 檢視你自己的 pipeline

對著上面這張圖，問自己：

- [ ] 我 `ci.yml` 有 `concurrency: cancel-in-progress`？
- [ ] `cd.yml` 的 `concurrency: cancel-in-progress` 是 `false`？
- [ ] 每個 workflow 都有 `permissions:`、預設 `read`，只有需要的 job 升 `write`？
- [ ] 所有 `pip install`、`docker build` 都有 cache？
- [ ] `all-green` gate 有配 branch protection？
- [ ] Image tag 策略一致（`:sha-`、`:main`、semver）？
- [ ] `release.yml` 用 `metadata-action` 自動產 tag？
- [ ] 有 `production` environment + required reviewer？

少一個就回去補。

## 失敗診斷速查表

看到紅色不慌，按這表走：

| 症狀 | 先看哪 |
|---|---|
| Job 一起 step 就紅（0 秒）  | syntax error、`uses:` 路徑錯 |
| `checkout` 後找不到檔 | `ref:` 寫對了嗎？fetch-depth 不夠？ |
| `pip install` 超慢或錯 | cache key 是不是不小心改了 |
| `docker build` 比本地慢很多 | `cache-from/to` 有配嗎？registry cache vs GHA cache |
| push image 時 `denied: permission_denied` | `permissions: packages: write` 忘了；或 fork PR |
| integration test 連不到 DB | `services:` 的 host 用 `localhost` 不是 service 名 |
| `${{ secrets.X }}` 是空的 | fork PR 沒 secret；或 env scope 沒設 |
| Matrix 跑一個失敗其他被 cancel | `fail-fast: false` 才會繼續 |
| 同一 PR 連續 push 每次都跑完 | 沒配 `concurrency: cancel-in-progress` |
| `all-green` 沒 gate PR | required check 沒選到 `all-green` name |
| `main` push 沒觸發 CD | tag push 用 `on.push.tags`、branch push 用 `on.push.branches` — 檢查寫對 |
| Workflow 本身跑但該跑的 step 被 skip | `if:` 條件；或 `needs:` 上游 skip 下游跟著 skip |

## Billable minutes

免費額度（GitHub 個人 + public repo）：

- Public repo：**Actions 無限制**
- Private repo：每月 **2000 分鐘**（Free）、**3000 分鐘**（Pro）

分鐘計價乘數：

| Runner | 乘數 |
|---|---|
| Linux | 1× |
| Windows | 2× |
| macOS | 10× |

**macOS 超貴**。除非你需要 build iOS / 測 macOS，千萬別當預設。

### 省分鐘的技巧

- **平行 job 各自算**：5 job × 3 分鐘 = 15 分鐘。矩陣 4 組 × 3 分鐘 = 12 分鐘。**矩陣爆炸是 billable 第一殺手**
- **Cache 命中**：cache 好的 CI 從 3 分鐘降到 1 分鐘，省 70%
- **用 `paths:` 篩觸發**：不該跑的 event 就不跑
  ```yaml
  on:
    push:
      paths:
        - 'app/**'
        - 'tests/**'
        - 'requirements*.txt'
        - '.github/workflows/**'
  ```
- **PR 才跑重的 job、push main 跳過**：反過來也可以
- **避免 macOS 出現在 matrix**：真的需要才加

### 看自己用多少

Settings → Billing → Actions usage。

```bash
gh api repos/<owner>/<repo>/actions/workflows --jq '.workflows[] | {id, name, state}'
```

可以看每個 workflow 狀態。配個自動關閉舊 workflow 的 script（`gh run delete`），定期清。

## 觀測性：當 CI 變慢或變不穩

幾個值得追蹤的訊號：

1. **平均 run 時間**：趨勢圖。某天突然變慢 → 依賴或 image 漲了
2. **失敗率**：10% 就該慌。通常是 flaky test 或網路抖
3. **cache hit rate**：某些 workflow 有 debug log 看得到。下滑通常是 key 設計變了
4. **image size 趨勢**：每次 build 都看 size。慢慢漲就是有人在 runtime stage 加東西

GitHub 本身沒內建這些 dashboard。進階團隊會：

- 把 workflow event 推到 Datadog / Honeycomb（有 action）
- 用 `gh run list --json` 寫 script 抓數據

小專案不用做這些，**知道要警覺**就好。

## 如果從零重做會怎樣

給你寫履歷用的一個心智濃縮：

1. **`.dockerignore`** 最先寫
2. **Dockerfile**：multi-stage + slim + USER + HEALTHCHECK + exec CMD
3. **`docker-compose.yml`**：dev 用、service 串
4. **CI (`ci.yml`)**：concurrency cancel、cache、lint + typecheck + unit (matrix) + integration (service) + `all-green` gate
5. **CD (`cd.yml`)**：main push → build + push 到 GHCR 帶 `:sha-` `:main` tag
6. **Release (`release.yml`)**：tag push → build multi-platform、`:semver` + `:latest`、GitHub Release 頁面
7. **Branch protection**：`all-green` 必綠、require review
8. **Environment `production`**：Deploy 前人審批，secret 放這層

總共 ~6–8 個檔案 + 少量設定。**這就是一個完整的 CI/CD**。

## 最常見的「做過頭」

新手做完 Part 2、Part 3 後會想繼續堆：

- 加 code coverage threshold（值得，但放 pytest config 不是 workflow）
- 加 Sentry / Datadog 告警（看團隊）
- 加 Slack 通知（`8398a7/action-slack`，進階）
- 加 Docker image signing（`cosign`，企業級）
- 加 SBOM 生成（`syft`）
- 加 Dependabot auto-merge（進階）

**這些都能加，但不要一次加**。**先讓現有的 pipeline 穩定運作兩週**，再挑一個實際有痛點的補。這門課最核心的 meta 原則，你不會想在這裡違反。

## 動手練習

1. 對照本章的「檢視」清單，把你 `tasktrack` 的 workflow 全過一次
2. 看一下 billable minutes（即使 public 無限，看一下月用量）
3. 找兩個可以砍的時間（通常是 matrix、macOS、重複 install）
4. 回去 Ch 12–13 確認 image tag 都有 semver 策略
5. 把最終 pipeline 畫一張圖貼 repo README（讓接手的人 30 秒看懂）

## 自我檢核

- [ ] 我能畫出 PR 到 deploy 的完整 pipeline 拓樸
- [ ] 我有診斷表能快速定位紅燈原因
- [ ] 我知道怎麼看 billable minutes、怎麼省
- [ ] 我不會一次加太多「進階」，知道先穩定再補
- [ ] 我能接手任何 CI/CD codebase、讀懂設計意圖

Part 3 結束。練習 C 補上你 pipeline 該有的最後一塊：安全性。

→ [練習 C：給 pipeline 加安全檢查](./practice-c-security-checks.md)
