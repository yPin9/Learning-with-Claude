# Ch 31 — Salsa CI / GitLab CI

> **目標**：理解 Debian 官方的 CI 基礎設施 Salsa（GitLab 實例）、Salsa CI pipeline 的標準 stage（build → lintian → autopkgtest → reprotest...）、如何用一行 include 套用官方 pipeline、以及 git 化打包工作流。

> **環境**：salsa.debian.org（Debian 的 GitLab 實例）、GitLab CI。本章假設你會基本 git。

## 為什麼需要打包 CI？

到目前為止，build → lintian → autopkgtest 都是你手動跑。對單一套件偶爾 build 還行，但：

- 每次改 `debian/` 都手動跑完整檢查很煩，容易忘
- 不同人協作維護同一套件，需要一致的檢查標準
- 上傳前要確保「在乾淨環境 + 所有檢查通過」——手動容易漏

CI 把這些自動化：每次 git push，自動跑完整的 build + 品質檢查 pipeline。Debian 官方提供 **Salsa CI**——一套現成的、涵蓋所有打包品質檢查的 GitLab pipeline。

## 先建立直覺：Salsa 是 Debian 的 GitLab

```
Salsa（salsa.debian.org）：
  Debian 官方的 GitLab 實例
  幾乎所有 Debian 套件的打包 git repo 都在這裡
        │
  每個套件 repo 有 debian/ 目錄（你學的全部）
        │
  push 觸發 Salsa CI pipeline：
    自動跑 build + lintian + autopkgtest + 更多檢查
        │
  全綠 = 套件品質達標，可以考慮上傳 archive
```

Salsa CI 的價值：它把「一個套件應該通過的所有檢查」打包成一個現成的 pipeline，你一行 `include` 就套用，不用自己寫 CI 設定。

## Salsa CI pipeline 的標準 stages

```
Salsa CI 的標準 pipeline（簡化）：

  ┌─────────────┐
  │   build     │  在乾淨環境 build 套件（類似 sbuild）
  └──────┬──────┘
         ▼
  ┌─────────────┐
  │   lintian   │  靜態品質檢查（Ch 16）
  └──────┬──────┘
         ▼
  ┌──────────────┐
  │ autopkgtest  │  功能測試（Ch 17）
  └──────┬───────┘
         ▼
  ┌──────────────┐
  │  reprotest   │  可重現性測試（Ch 4，build 兩次比對）
  └──────┬───────┘
         ▼
  ┌──────────────┐
  │   piuparts   │  安裝/升級/移除測試（裝了又移除是否乾淨）
  └──────┬───────┘
         ▼
  ┌──────────────┐
  │  blhc        │  build log 檢查（hardening flags 有沒有生效）
  └──────────────┘
```

每個 stage 對應你學過的一個品質面向。Salsa CI 把它們全自動化——這是「把整個 Part 3 變成一鍵 CI」。

## 套用 Salsa CI：一行 include

在套件 repo 的 `debian/salsa-ci.yml`（或 `.gitlab-ci.yml`）：

```yaml
---
include:
  - https://salsa.debian.org/salsa-ci-team/pipeline/raw/master/salsa-ci.yml
  - https://salsa.debian.org/salsa-ci-team/pipeline/raw/master/pipeline-jobs.yml
```

**就這樣**。這個 include 引入官方維護的完整 pipeline——所有 stage 自動設定好。push 到 Salsa，pipeline 自動跑。

客製化（如跳過某個 stage、調整參數）用變數：

```yaml
---
include:
  - https://salsa.debian.org/salsa-ci-team/pipeline/raw/master/recipes/debian.yml

variables:
  SALSA_CI_DISABLE_REPROTEST: 1       # 跳過可重現性測試（如果暫時不過）
  RELEASE: bookworm                    # 目標 release
  SALSA_CI_DISABLE_PIUPARTS: 0         # 啟用 piuparts
```

> 一行 include 套用官方 pipeline 是 Salsa CI 的設計哲學——把「正確的打包 CI 怎麼設定」這個專業知識封裝起來，維護者不用自己摸索。Salsa CI team 持續更新這個 pipeline，你 include master 就自動跟上最佳實踐。

## git 化打包工作流

Salsa CI 配合 git 化打包（Ch 6 的 gbp）。典型的 repo 結構：

```
套件 git repo（在 Salsa）：
  debian/         ← 你學的全部（control/rules/changelog/patches...）
  src/ 或 upstream code（依工作流而定）
  debian/salsa-ci.yml  ← CI 設定（一行 include）

分支模型（gbp 慣例，Ch 6）：
  debian/latest    ← debian 打包 + upstream code
  upstream/latest  ← 純 upstream
  pristine-tar     ← 重建 orig tarball 的 delta

工作流：
  git commit debian/ 的修改 → push → Salsa CI 自動驗證
  gbp buildpackage 本地 build
  gbp import-orig 匯入 upstream 新版
```

## 各 stage 詳解

| Stage | 做什麼 | 對應章節 |
|---|---|---|
| `build` | 乾淨環境 build（sbuild-like）| Ch 14/15 |
| `build i386` / 其他架構 | 多架構 build 驗證 | Ch 18 |
| `lintian` | 靜態品質 | Ch 16 |
| `autopkgtest` | 功能測試 | Ch 17 |
| `reprotest` | build 兩次比對（可重現）| Ch 4 |
| `piuparts` | 安裝/升級/移除乾淨性 | （新）|
| `blhc` | build log hardening 檢查 | Ch 8（hardening flags）|

**piuparts**（前面沒詳講）：測試「裝 → 升級 → 移除 → purge」整個生命週期是否乾淨——有沒有留下孤兒檔案、移除是否完整、升級是否平順。這補足了 lintian（靜態）和 autopkgtest（功能）沒覆蓋的「生命週期」面向。

**blhc**（build log hardening check）：掃 build log，確認每個編譯命令都用了 hardening flags（Ch 8 的 `dpkg-buildflags`）。抓出「某個檔案編譯時漏了 hardening」的問題。

## 故意弄壞：CI 抓出本地漏掉的問題

```
情境：你本地 build 成功（host 有某個依賴），但 Salsa CI 失敗

  本地：dpkg-buildpackage 成功（你的機器剛好有 libfoo-dev）
        │
  push 到 Salsa
        │
  Salsa CI build stage 失敗：
    在乾淨環境缺 libfoo-dev（你忘了加進 Build-Depends）
        │
  → CI 像 sbuild（Ch 15）一樣抓出漏宣告的依賴
```

Salsa CI 的 build 在乾淨環境跑（類似 sbuild），所以它抓出所有「本地 host 掩蓋的問題」。這是 CI 相比手動的核心價值——它強制每次都在乾淨環境驗證，你不會忘。

## Salsa CI vs 自建 CI（Ch 32 預告）

| 面向 | Salsa CI | 自建 GitHub Actions（Ch 32）|
|---|---|---|
| 平台 | salsa.debian.org（Debian GitLab）| GitHub / 任何 GitLab |
| pipeline | 官方現成（一行 include）| 自己寫 |
| 維護 | Salsa CI team 維護 | 你維護 |
| 適合 | 貢獻 Debian 的套件 | 私有專案、非 Debian 生態 |

> Salsa CI 是「為 Debian 套件設計的、現成的、官方維護的」CI。如果你的套件要進 Debian，用 Salsa CI（它檢查的就是 archive 要的）。如果是私有專案或在 GitHub，自建 CI（Ch 32）——但可以參考 Salsa CI 的 stage 設計。

## 踩雷集錦

1. **以為 CI 通過就能上傳 archive**：Salsa CI 全綠是「品質達標」的好訊號，但上傳 archive 還要過 NEW queue（新套件）、sponsor review 等人工流程（Ch 25）

2. **本地過了 push 卻 CI 失敗**：CI 在乾淨環境跑（像 sbuild）。本地 host 掩蓋的依賴問題會在 CI 暴露——這正是 CI 的價值，不是 CI 壞了

3. **include 用了固定版本而非 master**：include Salsa CI 的 master 才會自動跟上更新。pin 固定 commit 會停在舊版 pipeline（除非你有特殊理由要穩定）

4. **reprotest 失敗就 disable 它**：reprotest 失敗表示 build 不可重現（Ch 4），是真問題。disable 是逃避不是解決（除非你確定是 reprotest 本身的環境問題）

5. **沒有 git 化打包就想用 Salsa CI**：Salsa CI 假設你的 `debian/` 在 git repo。要先把打包工作流 git 化（gbp，Ch 6）

## 進階：Salsa CI 的 pipeline 客製化與 extract-source

Salsa CI 的 pipeline 是模組化的，能深度客製：

```yaml
# 只跑特定 jobs
include:
  - https://salsa.debian.org/salsa-ci-team/pipeline/raw/master/recipes/debian.yml

variables:
  # 控制各 stage
  SALSA_CI_DISABLE_BLHC: 0
  SALSA_CI_DISABLE_LINTIAN: 0
  SALSA_CI_LINTIAN_ARGS: "-i -I --pedantic"   # 更嚴格的 lintian
  # 跨多個 release 測試
  RELEASE: "unstable"
```

```yaml
# 加自訂 job（在官方 pipeline 之外）
my-custom-check:
  stage: test
  needs: [build]
  script:
    - ./my-special-test.sh
```

**extract-source** stage：Salsa CI 的第一步通常是從 git 提取 source package（用 gbp 或直接），後續 stage 用這個 source。理解這個讓你知道 CI 怎麼從 git repo 得到可 build 的 source。

Salsa CI 還能整合**自動上傳**——pipeline 通過後，配合適當權限，能自動 `dput` 到 mentors 或（DD 的話）archive。這把「push → 驗證 → 發布」串成全自動流程。

## 動手練習

1. （需要 Salsa 帳號，或用任何 GitLab）建一個套件 git repo，加 `debian/salsa-ci.yml` 的一行 include，push 觸發 pipeline，觀察各 stage（build/lintian/autopkgtest...）

2. 故意製造 CI 失敗：本地能 build 但漏一個 Build-Depends，push 看 build stage 在乾淨環境失敗（像 sbuild）

3. 研究 Salsa CI 的 pipeline 定義：讀 `https://salsa.debian.org/salsa-ci-team/pipeline` 的 `salsa-ci.yml`，看各 stage 怎麼定義

4. 找一個真實 Debian 套件的 Salsa repo（如 `https://salsa.debian.org/debian/hello`），看它的 CI 設定和 pipeline 歷史

## 本章重點整理

- Salsa（salsa.debian.org）是 Debian 的 GitLab，幾乎所有套件的打包 git repo 在這
- Salsa CI 用一行 include 套用官方 pipeline：build → lintian → autopkgtest → reprotest → piuparts → blhc
- 每個 stage 對應你學過的品質面向；piuparts（生命週期）和 blhc（hardening log）補足前面沒講的
- CI 在乾淨環境跑（像 sbuild），抓出本地 host 掩蓋的問題——這是 CI 相比手動的核心價值
- 配合 git 化打包（gbp）；Salsa CI 是「為 Debian 套件設計的現成 CI」

## 自我檢核

- [ ] 知道 Salsa 是什麼，以及 Salsa CI 如何用一行 include 套用
- [ ] 能說出 Salsa CI 的主要 stage 各對應你學過的什麼檢查
- [ ] 知道 piuparts 和 blhc 各檢查什麼（生命週期 / hardening log）
- [ ] 能解釋為什麼「本地過了 CI 卻失敗」是 CI 的價值而非缺陷
- [ ] 知道 Salsa CI 通過後還需要什麼才能進 archive（NEW、sponsor）

## 延伸閱讀

### 官方文件

- **[Salsa CI pipeline](https://salsa.debian.org/salsa-ci-team/pipeline)**
  - **讀哪裡**：README 和 `salsa-ci.yml`（pipeline 定義）
  - **學什麼**：所有 stage 的定義、可用的變數、客製化方式
  - **前提**：讀完本章

- **[Salsa documentation](https://wiki.debian.org/Salsa)**
  - **讀哪裡**：CI 整合那節
  - **學什麼**：Salsa 平台的使用、CI 設定
  - **前提**：基本 git

### 部落格 / 文章

- **[Salsa CI: the road so far](https://salsa.debian.org/salsa-ci-team/pipeline/-/blob/master/README.md)** 或相關 DebConf talk
  - **這篇說什麼**：Salsa CI 的設計、各 stage 的目的、如何加速 Debian 的品質保證
  - **讀哪裡**：overview
  - **為什麼值得讀**：理解 Debian 如何把品質檢查工業化、自動化

→ [Ch 32 GitHub Actions 打包管線](./32-github-actions.md)
