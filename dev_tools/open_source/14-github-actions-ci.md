# Ch 14 — GitHub Actions / CI

> **目標**：從**協作者的角度**理解 CI（持續整合）——PR 上的 status check 是什麼、為什麼你的 PR 變紅、怎麼讀 CI log 找出失敗原因、怎麼本地先跑避免丟臉、以及 fork PR 的 CI 安全限制。本章不教怎麼寫複雜 CI pipeline（那是 [dev_tools/cicd](../cicd/README.md) 的事），專注「身為貢獻者怎麼跟 CI 共處」。

> **環境**：GitHub Actions。CI 概念也適用 GitLab CI、CircleCI 等。

## 為什麼貢獻者要懂 CI

你開了 PR，下面出現一堆綠勾勾或紅叉叉——那是 **CI（Continuous Integration）**在自動檢查你的改動。在現代協作中，CI 是 PR 的**第一道守門員**：在人類 reviewer 看你之前，機器先跑測試、檢查格式、build——通不過，很多專案根本不讓你 merge（也不會有人 review）。

身為貢獻者，你不需要會寫 CI（那是維護者的事，Ch 33/cicd 課），但你必須會：看懂 PR 上的檢查結果、知道為什麼紅了、讀 log 找原因、本地先跑避免丟臉。不懂 CI 的貢獻者會開出一堆紅的 PR，顯得沒測試、浪費維護者時間。

## 先建立直覺：機器幫你（和維護者）把關

```
   你 push 到 PR branch
        │
        ▼
   CI 自動觸發（不用人手動）：
   ┌──────────────────────────────┐
   │ ✅ 跑測試（test suite 過了嗎）  │
   │ ✅ 檢查格式（lint/format）      │
   │ ✅ 編譯/build（能 build 嗎）     │
   │ ✅ 型別檢查、安全掃描...         │
   └──────────────────────────────┘
        │
        ▼
   PR 上顯示結果：全綠 → 可以 review/merge
                紅 → 你有東西壞了，先修
```

CI 的價值：**在問題進入主線前自動攔截**，且不靠人記得跑測試。對協作尤其重要——維護者不可能手動驗證每個貢獻者的 PR 都沒壞東西，CI 自動做這件事。對你（貢獻者）的好處：CI 綠 = 你的改動至少沒明顯壞掉，reviewer 能專注看設計而非幫你抓低級錯誤。

## PR 上的 status check

PR 頁面底部會列出所有檢查（status checks）：

```
   Some checks were not successful
   ┌────────────────────────────────────────────┐
   │ ✅ build (ubuntu-latest)        Passed       │
   │ ✅ build (windows-latest)       Passed       │
   │ ❌ test (ubuntu-latest)         Failed       │  ← 點這個看 log
   │ ✅ lint                         Passed       │
   │ ⏳ deploy-preview               In progress  │
   └────────────────────────────────────────────┘
```

每個檢查：

- **✅ 綠勾**：通過。
- **❌ 紅叉**：失敗——點它看 log 找原因。
- **⏳ 黃點**：進行中（等它跑完）。
- **required check**（Ch 23）：被設為「必須通過才能 merge」的檢查——紅的話 merge 按鈕會被鎖。

> 矩陣（matrix）測試：你常看到同一個 job 跑好幾次（`ubuntu-latest`/`windows-latest`/`macos-latest`，或多個語言版本）——這是 CI 在多個環境驗證你的改動。某個環境紅、別的綠，代表「你的改動在那個環境有問題」（如用了 Linux-only 的 API）——這是 CI 幫你抓跨平台問題的價值。

## 你的 PR 為什麼變紅

常見原因，由你最該先查的排起：

1. **測試失敗**：你的改動弄壞了某個測試（或你沒加測試但破壞了既有行為）。最常見。
2. **lint / format 不過**：程式碼格式不符專案規範（縮排、引號、import 順序…）。
3. **build / 編譯失敗**：語法錯、型別錯、缺相依。
4. **跨環境失敗**：在某個 OS/版本壞掉（matrix 裡只有部分紅）。
5. **既有的壞**：有時不是你的錯——main 本身的 CI 就壞了（flaky test、上游問題）。這時禮貌告知維護者。

## 讀 CI log 找原因

PR 上點失敗的檢查 → 進到 Actions 的 log。怎麼讀：

```
   1. 找紅色的 step（log 裡標 ❌ 的那一步）——別從頭讀，直接找失敗點
   2. 看那一步的輸出，往上找第一個 error/failure
   3. 對測試失敗：找 "FAILED"/"AssertionError" 那行，看是哪個測試、預期 vs 實際
```

```bash
# 用 gh CLI 直接看 PR 的 CI 狀態與 log（Ch 15），不用開瀏覽器
gh pr checks                      # 看當前 PR 所有檢查的狀態
gh run view --log-failed          # 看最近一次 run 的「失敗部分」log
```

`gh pr checks` + `gh run view --log-failed` 讓你在命令列快速定位 CI 失敗——比在瀏覽器點來點去快。

## 本地先跑：別讓 CI 替你 debug

新手常 push 上去靠 CI 告訴他壞了，改了再 push，紅紅綠綠來回好幾次——**又慢又丟臉**（PR 歷史一堆 "fix CI"）。

專業做法：**push 前在本地跑 CI 會跑的東西。** 怎麼知道 CI 跑什麼？看專案的 CI 設定（`.github/workflows/*.yml`），它列出了所有檢查指令：

```yaml
# .github/workflows/ci.yml（你讀它，不是寫它）
jobs:
  test:
    steps:
      - run: npm install
      - run: npm run lint        # ← 本地先跑這個
      - run: npm test            # ← 和這個
      - run: npm run build       # ← 和這個
```

```bash
# 本地照著跑一遍，綠了再 push
npm run lint && npm test && npm run build
```

很多專案的 CONTRIBUTING（Ch 16）會直接告訴你「push 前跑這些」。有些專案還提供 **pre-commit hooks**（Ch 27），在你 commit 時自動跑 lint/format，根本不讓你 commit 壞東西。

> 進階：用 [`act`](https://github.com/nektos/act) 能在本地用 Docker 跑 GitHub Actions workflow（近似 CI 環境），但通常「直接跑 CI 裡的那幾個指令」就夠了，不需要完整模擬。

## fork PR 的 CI 安全限制

承 Ch 10 的 fork-based 流程，外部貢獻者的 PR 有個重要的 CI 安全機制：

```
   外部貢獻者開 PR → CI 要在「維護者的 repo」跑（用維護者的資源/secret）
        │
   風險：惡意 PR 可能在 CI 裡偷 secret（API key、deploy token）或濫用資源
        │
   GitHub 的保護：
   - fork PR 的 CI 預設「不給 secret」（涉及 secret 的步驟拿不到）
   - 第一次貢獻者的 PR，CI 需要維護者「approve」才跑（防濫用）
```

所以你（外部貢獻者）可能遇到：

- PR 開了但 CI 顯示「等待維護者批准才會跑」——正常，等維護者按。
- 某些需要 secret 的檢查在你的 fork PR 上 skip/fail——因為安全限制拿不到 secret，不是你的錯。

這也是為什麼維護者 review 外部 PR 要更謹慎（Ch 29/34）——CI 在跑你的 code，有供應鏈風險。

## 一個完整的「保持 CI 綠」流程

```bash
# 1. 改完，本地先跑 CI 會跑的（讀 .github/workflows 或 CONTRIBUTING）
npm run lint && npm test
#    紅的話本地先修，別 push

# 2. 綠了才 push
git push

# 3. 等 CI 跑，用 gh 看狀態
gh pr checks --watch              # 持續監看直到跑完

# 4. 若紅了（漏看的環境/flaky）
gh run view --log-failed          # 看失敗 log
#    修 → 本地驗 → push

# 5. 全綠 → 等 review
```

目標：**讓 reviewer 第一次看到你的 PR 就是全綠的**——這代表你測試過、尊重流程，第一印象大加分。

## 踩雷集錦

1. **push 上去靠 CI 幫你 debug**：紅紅綠綠來回、PR 一堆 "fix CI" commit。本地先跑，綠了才 push。
2. **CI 紅了不看 log 就重 push**：盲目重試沒用。點失敗的 check、讀 log、找第一個 error。
3. **lint 失敗以為是大問題**：多半是格式（縮排/引號/import 順序）。專案常有 `npm run format`/`make fmt` 一鍵修。
4. **某環境紅就慌**：matrix 裡單一環境紅 = 你的改動在那環境有問題（跨平台 bug），CI 幫你抓到了。針對那環境修。
5. **fork PR 的 CI 沒跑/缺 secret 以為壞了**：是安全限制（等維護者批准、fork 拿不到 secret），不是你的錯。
6. **main 本身 CI 壞了怪自己**：有時是 flaky test 或 main 的問題，不是你的改動。重跑（`gh run rerun`）或禮貌告知維護者。
7. **不看 CONTRIBUTING 就猜 CI 跑什麼**：專案多半寫明「push 前跑這些」。讀它（Ch 16）。

## 進階：再往深一層

- **flaky test**：偶爾紅偶爾綠的不穩定測試（時間、順序、網路依賴）。不是你的錯時，`gh run rerun` 重跑;但別養成「紅了就重跑」的習慣（可能真有 bug）。
- **required vs optional checks**：維護者設哪些 check 是 merge 必過（Ch 23）。optional 的紅了不一定擋 merge，但最好還是綠。
- **CI 的 cache**：CI 常 cache 相依（npm/pip）加速。偶爾 cache 壞導致詭異失敗，重跑或維護者清 cache。
- **本地用 act 跑 Actions**：`act` 用 Docker 在本地跑 workflow，近似 CI——複雜 CI debug 時有用，但多數情況直接跑指令就夠。
- **讀懂 workflow yaml**：`.github/workflows/*.yml` 是 CI 的定義（trigger、job、step）。會讀它你就知道 CI 跑什麼、為什麼。寫它是 Ch 33 / [cicd 課](../cicd/README.md)。
- **status check 與 branch protection**：required check + 「PR 必須通過才能 merge」是 protected branch 的核心規則（Ch 23）。

## 動手練習

1. 找一個用 GitHub Actions 的開源專案，看它的 `.github/workflows/*.yml`，列出 CI 會跑哪些指令（test/lint/build）。
2. 在你自己的 repo 加一個最簡單的 CI（跑測試）——體驗從維護者角度看 PR 的綠勾（深入見 [cicd 課](../cicd/README.md)）。
3. 故意開一個會讓測試失敗的 PR，看它變紅，用 `gh pr checks` 看狀態、`gh run view --log-failed` 讀失敗 log、找出原因。
4. 練習「本地先跑」：對一個專案，照 CONTRIBUTING/workflow 在本地跑 lint+test，綠了再 push。
5. 對一個 fork 的 PR，觀察 CI 的批准機制/secret 限制（如果該專案有設）。
6. 用 `gh pr checks --watch` 監看一個 PR 的 CI 跑完——體驗命令列追蹤 CI。

## 本章重點整理

- CI 是 PR 的第一道守門員：push 自動跑測試/lint/build，通不過很多專案不讓 merge、沒人 review。
- PR 上的 status check：綠（過）/紅（失敗，點看 log）/黃（進行中）；required check 紅會鎖 merge。
- 你 PR 變紅最常見是測試失敗、lint/format、build、跨環境問題；偶爾是 main 本身壞了（非你的錯）。
- **本地先跑** CI 會跑的指令（讀 workflow / CONTRIBUTING），綠了才 push——別靠 CI 替你 debug。
- fork PR 有 CI 安全限制（拿不到 secret、需維護者批准才跑）——保護維護者不被惡意 PR 偷 secret。
- 目標：讓 reviewer 第一次看到就是全綠的 PR。

## 自我檢核

- [ ] CI 在協作中扮演什麼角色？為什麼它在人類 review 之前？
- [ ] 你的 PR 變紅，排查順序是什麼？怎麼讀 log 找原因？
- [ ] 為什麼「本地先跑」比「push 上去靠 CI 告訴你」好？怎麼知道 CI 跑什麼？
- [ ] fork PR 的 CI 有什麼安全限制？為什麼？
- [ ] matrix 裡只有某個環境紅，代表什麼？

## 延伸閱讀

### 本 repo

- **[dev_tools/cicd](../cicd/README.md)**
  - **這門課的定位**：教你**寫** CI/CD pipeline（GitHub Actions + Docker，把服務做成可交付 pipeline）。本章只教「貢獻者怎麼跟 CI 共處」，想自己建 CI（維護者視角，Ch 33）就讀這門。

### 官方文件

- **[GitHub Docs: About status checks](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/collaborating-on-repositories-with-code-quality-features/about-status-checks)** 與 **[About continuous integration](https://docs.github.com/en/actions/automating-builds-and-tests/about-continuous-integration)**
  - **讀哪裡**：status check、required check、CI 概念。
  - **和本章的關聯**：本章概念的權威。

- **[GitHub Docs: Approving workflow runs from public forks](https://docs.github.com/en/actions/managing-workflow-runs/approving-workflow-runs-from-public-forks)**
  - **讀哪裡**：fork PR 的 CI 批准機制。
  - **和本章的關聯**：fork PR CI 安全限制的官方說明。

### 工具

- **[gh CLI: gh run / gh pr checks](https://cli.github.com/manual/gh_run)**
  - **讀哪裡**：`gh run view --log-failed`、`gh pr checks`。
  - **和本章的關聯**：命令列查 CI 的利器（Ch 15 深入 gh）。

CI 懂了，下一章把整個 Part 3 的平台操作命令列化——`gh` CLI，讓你不開瀏覽器就能管 PR、issue、review。

→ [Ch 15 gh CLI 與自動化](./15-gh-cli.md)
