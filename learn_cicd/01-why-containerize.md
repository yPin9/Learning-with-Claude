# Ch 1 — 容器到底在解什麼問題？CI/CD 全貌

> 目標：拋棄「容器是輕量 VM」這個爛比喻，看清楚容器解的到底是什麼問題；同時把 CI/CD pipeline 的全貌在腦中畫出來。

這章不碰鍵盤。純思考 + 一張心智地圖。後面 15 章都是在填這張地圖的格子。

## 「容器是輕量 VM」為什麼是爛比喻

很多入門教材會說：「容器就像輕量的 VM，共用 host kernel 所以比較快」。這個說法 **技術上對，但教學上災難**，因為它暗示你：容器 = 隔離技術。

錯。容器的主要貢獻不是隔離 — LXC、Solaris zones、FreeBSD jails 都隔離了幾十年，沒人寫 Dockerfile 也沒人因此瘋狂。

容器真正解的是 **「把一個 runtime 打包成可攜的 artifact」** 這個問題。

用兩個場景說明：

### 場景 A：「在我電腦可以跑」

你開發在 Mac（macOS 14、Python 3.12、libssl 3.x），同事拿去 Ubuntu 22.04（glibc 2.35、libssl 1.1）跑，炸。你本機 `pip install -r requirements.txt` 拉到 wheel，同事在 ARM 機器拉到需要編譯的 sdist，編譯失敗，炸。部署到生產，又一次炸。

Docker 做的事：**把 OS userland + runtime + 依賴 + 你的 code 全部凍成一個 image**。這張 image 在任何跑得了 Docker 的機器上，行為一致。

這不是隔離問題，是 **packaging** 問題。

### 場景 B：生產環境是哪個版本

沒容器化的世界：
- 「生產這台跑的是哪個 commit？」→「大概是上週 deploy 的？」
- 「上次 deploy 之後改了什麼？」→「不知道，我打 rsync 的時候多了幾個檔」

容器化後：
- 生產跑的是 `tasktrack:sha-a1b2c3d`
- 你打 `docker inspect` 看得到 build time、base image、label
- rollback 是一行 `docker run tasktrack:sha-前一版`

這是 **shipping** 問題。

## 容器 vs VM 的正確對比

| 面向 | VM | 容器 |
|---|---|---|
| 跑的是 | 整個 OS（kernel + userland） | 只有 userland（共用 host kernel） |
| 啟動時間 | 秒到分鐘 | 毫秒 |
| Image 大小 | GB 級 | MB 到 百 MB |
| 隔離強度 | 強（CPU / MMU 層） | 弱（Linux namespace + cgroups） |
| 設計目標 | 在一台硬體跑多個 OS | **把 runtime 可攜地打包** |

**重點不是誰比較快**，是最後一欄 — 兩者在解不同問題。你可以 VM 裡跑容器、可以容器裡跑 VM，兩個觀念正交。

### 隔離強度這欄很重要

容器共用 kernel → kernel 有洞，容器逃逸就直達 host。所以 **不要在 untrusted workload 上只靠 Docker**（跑陌生人的 code 要用 gVisor / Kata / Firecracker 這種加了一層 VM 的）。你自家團隊的 API service 這種 workload，Docker 的隔離夠。

## Docker 貢獻的三個東西

LXC 早就在，但沒人為它瘋狂。Docker 把三件事綁一起：

1. **OCI image 格式** — 分層、可 hash 定位、可推可拉
2. **Dockerfile** — reproducible build 的宣告式 DSL
3. **Registry** — image 可以像 npm package 一樣傳送

這三個加起來才是革命。後面 Ch 2–3 教第一、第二個，Ch 12 教第三個。

## CI/CD 是什麼、不是什麼

CI 跟 CD 是 **兩個獨立的概念**，常被混用：

- **CI（Continuous Integration）** — 每次 push / PR 自動跑驗證（build、lint、test）。目標：讓 main 永遠是綠的。
- **CD** — 有兩種，別搞混：
  - **Continuous Delivery** — 驗證通過自動產出可部署 artifact（image push 到 registry、binary 上 GitHub Release）。**部署還是手動按鈕。**
  - **Continuous Deployment** — 驗證通過 **自動部到生產**，沒人類批准。

這門課教到 **CI + Continuous Delivery**。Continuous Deployment 我們略過（它跟你的部署架構綁太深，K8s / Nomad / ECS 都不一樣）。

## 一張 pipeline 心智地圖

這是後面 15 章都在填的那張圖：

```
┌─────────────────────────────────────────────────────────────────┐
│                       PR 開啟 / push branch                     │
└──────────────────────────────┬──────────────────────────────────┘
                               │  ↓ GitHub 發出 event
┌──────────────────────────────▼──────────────────────────────────┐
│                  GitHub Actions Runner（乾淨 VM）               │
│  ┌────────┐  ┌────────────┐  ┌──────────┐  ┌─────────────────┐ │
│  │  lint  │  │ type check │  │ unit test│  │ integration test│ │
│  │ (ruff) │  │   (mypy)   │  │ (pytest) │  │  + Postgres svc │ │
│  └────────┘  └────────────┘  └──────────┘  └─────────────────┘ │
│           ↑ 平行跑，任一失敗就紅                                │
└──────────────────────────────┬──────────────────────────────────┘
                               │  ↓ 全綠，人審核後 merge
┌──────────────────────────────▼──────────────────────────────────┐
│                          main 分支 push                         │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│    docker buildx build --push  →  ghcr.io/you/tasktrack:sha     │
└──────────────────────────────┬──────────────────────────────────┘
                               │  ↓ 打 tag v0.1.0
┌──────────────────────────────▼──────────────────────────────────┐
│   GitHub Release + image retag tasktrack:v0.1.0 + tasktrack:latest │
└─────────────────────────────────────────────────────────────────┘
                               │  ↓（可選、不在本課 scope）
                               ▼
                      生產環境 pull 新 image 部署
```

每一章會講其中一個格子：

| Chapter | 在圖中的位置 |
|---|---|
| Ch 2–3 | Dockerfile 那層 |
| Ch 4 | 本地 compose（圖外，但為 integration test 打基礎） |
| Ch 5 | image 進階（安全、多平台） |
| Ch 6–7 | Runner、workflow 語法（整張圖的骨架） |
| Ch 8 | cache（讓圖裡每個格子變快） |
| Ch 9 | secrets（push 到 registry 要驗證） |
| Ch 10–11 | reusable workflow、service container |
| Ch 12 | registry 那層 |
| Ch 13 | Release 那層 |
| Ch 14 | 最下面那個「部署」格子 |
| Ch 15 | 整張圖回頭審視 |

## GitHub Actions 在這之中的角色

一句話：**Actions 是 event router + 執行引擎，不是 build tool、不是 deploy tool**。

它做的事很簡單：
1. 監聽 event（push、PR、schedule、tag、手動觸發）
2. 對應到 workflow 檔
3. 開一台乾淨 VM 跑 job
4. 收集結果、回報 status

真正幹活的是 VM 裡的 `docker build`、`pytest`、`pip install` — 這些跟 Actions 無關，本地也能跑。Actions 只是幫你把「事件 → 該跑什麼」連起來。

這是好消息，也是壞消息：
- **好**：邏輯能本地 reproduce。你 workflow 裡寫 `pytest -v`，本地 `pytest -v` 一樣會跑。
- **壞**：很多新手把不該進 Actions 的邏輯硬塞進 YAML，debug 的時候哭。

原則：**能在 shell script 跑的事，就別讓它長在 YAML 裡**。

## 為什麼這套組合能對付拖延

這是這門課 meta 層的重點。認真看。

容器 + CI/CD 有一種誠實感，它不會讓你自欺欺人：

- **pipeline 紅 = 沒做完**。你不能說服自己「這個 test 失敗不重要」，因為 main 就是進不去。
- **image build 不出來 = 沒做完**。你不能說「我再改一下」然後放一個月。image 是 binary 的 — 要嘛有，要嘛沒有。
- **Release tag 打不下去 = 沒做完**。要打 `v0.1.0`，你就得決定「這版到底要不要交」。沒準備好？那把 scope 砍掉。

比起「完美地實作某個功能」，這套工具鼓勵的是：**把當下能交的交出去，缺的部分記 issue 下次再說**。

這就是為什麼選這個主題當第四門課 — 它不是另一個你可以在筆記裡反覆修改的技術。它是一個 **機制**，會逼你按下 commit → push → 觀察 → 修正的循環，不給你躲藏的地方。

## 常見誤解

- 「**Docker = 輕量 VM**」 — 錯。共用 kernel，解的是 packaging 不是 isolation。
- 「**容器隔離很安全**」 — 比你想的弱。不要在 Docker 裡跑你不信任的 code。
- 「**CI/CD 很複雜**」 — 核心就兩件：**event 觸發 → 執行 job**。其他都是 accidental complexity。
- 「**GitHub Actions 就是組 `uses:`**」 — 不。`run:` 直接寫 shell 才是主力，`uses:` 只是把常見操作打包。
- 「**CI 紅了就重跑**」 — 不要。flaky test 是癌症。紅了先懷疑程式、再懷疑 test、最後才懷疑 runner。

## 自我檢核

- [ ] 我能用 1 分鐘跟人解釋「容器和 VM 的差別」，而且不講「輕量 VM」
- [ ] 我知道 CI / Continuous Delivery / Continuous Deployment 是三件不同的事
- [ ] 我看得懂上面那張 pipeline 心智地圖，知道每階段的輸入輸出
- [ ] 我懂為什麼「pipeline 紅 = 沒做完」是這套工具的價值而不是 bug
- [ ] 我同意「能在 shell 跑的事就別長在 YAML 裡」這個原則

下一章進 Dockerfile。我們會先戳破另一個常見誤解：**Dockerfile 不是 shell script**。

→ [Ch 2 Dockerfile 與 layer 原理](./02-dockerfile-layers.md)
