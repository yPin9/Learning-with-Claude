# Final Project — Hybrid binary 漏洞分析

> 目標：挑一個中等規模（幾 KB 到幾百 KB）的 binary，用 symex + taint + fuzzing 的組合找至少一個真實 bug、生成 PoC、寫完整 writeup。這個 project 把整門課所有東西 exercise 一次。

## 適合的 target 來源

你需要一個**有 bug 但不是玩具**的 binary。以下三類：

### 類別 1：LAVA-M (預埋 bug 的 benchmark)

**LAVA-M** 是 DARPA 專門做給 fuzzer / symex 研究的 benchmark — 在 `base64`、`md5sum`、`uniq`、`who` 四個 coreutils 工具裡預埋 bug，每個 bug 有明確 input 觸發。

- Repo：<https://github.com/panda-re/lava>
- 對 DTA / symex 友善，bug 是 integer overflow / OOB write

Pros：
- 有 "golden truth" — 知道該找到多少 bug
- 社群用它比較工具
- 論文可對照

Cons：
- 已經是被多次研究過的 benchmark
- 預埋 bug 比真實 bug 工整

### 類別 2：OSS-Fuzz fixed bug 重現

挑一個 **已經被 patch 的 OSS-Fuzz bug**：
- 從 OSS-Fuzz dashboard 拿 bug report
- checkout 到 buggy version 的 library
- build
- 驗證你的工具鏈能找到那個 bug

例子：libxml2 的舊 CVE、curl 的 history bug。

Pros：
- 真實 bug、有 ground truth
- patch 已經驗證過

Cons：
- 大型 library 常難 setup
- 挑 target 不 good 會 KLEE / angr 跑不動

### 類別 3：教學用小 CVE

挑一個**小型軟體的 known CVE**：
- `rsync` 的舊 bug
- `busybox` utility 的 bug
- 學校 OS 作業的 bug

LOC 控制在 **1-10 KLOC** 範圍。可以是 vulnerable-by-design 的教學 binary（<https://github.com/veracode/verademo>）。

**我的推薦**：LAVA-M 的 `base64` 或 `md5sum`。大小適中、bug 可控、可以對比 paper。

## Project 要求

必做：

### 1. Setup 與初步分析

- 拿 target binary（+ source 可選）
- 描述 target、列出應找到的 bug
- 確認你的工具鏈（KLEE / angr / Triton / AFL）都能接上

### 2. Fuzzing baseline

- 用 AFL++ 跑 24 小時
- 記錄 found crashes、coverage
- 分析：哪些 bug 被找到、哪些沒

### 3. Symex

- 用 KLEE（如有 source）或 angr 跑
- 做 harness design 文件
- 記錄 found bug、path count、runtime

### 4. Taint analysis

- 用 Triton 或 libdft 寫一個簡單 taint 追蹤
- 定義 source / sink
- 對 bug trigger input 跑，產生 taint trace

### 5. Hybrid

- 三種 analysis 的結果對照
- 哪些 bug 只被一種工具找到？為什麼
- 如果時間允許，整合 AFL+SymCC 或 Driller

### 6. PoC 與報告

- 對每個找到的 bug，寫一個 minimal reproducer（不用 fuzzer/symex，直接 input file）
- Writeup 文件，格式如下

### 7. 開源

- 把你的 scripts 放 GitHub repo
- README 清楚說明目標、用法、結果

## Writeup 的標準結構

```
# <Target> Vuln Analysis — Final Project

## Abstract
- Target: <bin name>
- Scope: 找 N 個 bug
- Tools: AFL++, KLEE/angr, Triton
- Found: M bugs, Y PoC

## 1. Target
- 功能
- source / binary
- LOC 估計

## 2. Methodology
- 我的 workflow
- 工具組合理由

## 3. Fuzzing
- Setup (corpus, dict)
- 執行時長
- 結果 (crashes, coverage)
- 分析

## 4. Symbolic Execution
- 為什麼選 KLEE/angr
- Harness 設計
- 結果

## 5. Taint Analysis
- Source/sink spec
- Tool: Triton-based
- Results: taint traces

## 6. Bug Details
For each bug:
- Location (file:line or addr)
- Category (OOB / UAF / integer overflow / ...)
- Trigger input
- Root cause
- 工具 X 找到、工具 Y 沒找到 的分析

## 7. Comparisons
- Time to first crash (AFL vs KLEE vs Hybrid)
- Coverage
- False positive / negative

## 8. Lessons Learned
- 哪個 tool 在這個 target 表現最好 / 最差
- 什麼 surprise 了你
- 下次你會怎麼做不同

## 9. References
- 相關 paper、原始 CVE、OSS-Fuzz report
```

這份 writeup 跟 bug bounty / vuln research report 的格式很像。寫好這份**就是職場證據**。

## 評分自 checklist（給自己打分）

- [ ] 目標 binary 不是玩具，有真實 code
- [ ] 三種 analysis 都跑了、有具體 output
- [ ] 找到至少 **1 個真實 bug**
- [ ] 產生至少 **1 個 minimal PoC**
- [ ] Writeup 有完整 7+ section
- [ ] 有對照比較、不只是把 report 堆在一起
- [ ] Github repo 可讓別人重現

做到 5/7 以上，project 合格。全部做到，你可以把這個 project 放履歷。

## 時間預算

參考：

| 階段 | 預估時間 |
|------|---------|
| Setup + target 分析 | 4-8 小時 |
| Fuzzing 24 小時 (background) | 1 天 |
| Symex + harness | 8-16 小時 |
| Taint tool + trace | 8-12 小時 |
| Hybrid + comparison | 4-8 小時 |
| Writeup | 6-10 小時 |
| 總計 | **30-60 小時** |

分佈在 **2-4 週**（每天 2-3 小時）。別硬擠、別跳過 writeup — writeup 是整個 project 最有價值的 output。

## 進階擴展（選擇性）

完成基本要求後，想加分可以做：

### 擴展 1：整合 SymCC

build SymCC 版本的 target、配 AFL++ 跑 hybrid。對照 pure AFL 的 coverage / bug rate。

### 擴展 2：Automated PoC 驗證

寫 CI workflow：
- 每晚 AFL fuzz 1 小時
- 如果有新 crash，自動 replay
- 用 your symex tool 產生最小化 PoC
- Slack / email 通知

這是 **vuln research lab 的實際 pipeline**。你實作一遍，對 vuln research 工業化有第一手經驗。

### 擴展 3：多 target

把整個 LAVA-M (base64, md5sum, uniq, who) 都跑完。產生 leaderboard：
- 每個 target，fuzzer vs symex 各找到幾個 bug
- 哪種 bug class 哪種工具強

### 擴展 4：Paper 對比

挑一篇 hybrid fuzzing paper (SymCC, QSYM, Angora)，在你的 target 上 reproduce 它的部分結果。論文掃一眼就會、實驗做出來才是真懂。

## 心法：整合 > 深度

做這個 project 時你會有衝動：「這個工具我想再調 10 小時讓它完美」。

**抗拒這種衝動**。Final project 的價值是 **系統性的整合**：

- fuzzer 找表面 bug
- symex 找 fuzzer 卡住的
- taint 解釋資料流
- writeup 把它們連起來

每個工具做到 **80% 就夠** — 剩下 20% 往往是 diminishing return。把時間花在 integration 跟 writeup，收益最大。

## 結語

做完這個 project，你在 symex + taint 的光譜上已經不是學生 — 你是個**有端到端工作經驗的工程師**。

接下來的深入方向看 Ch 28 — 是 vuln research、fuzzing、formal verification、還是 academic research，選一條繼續。

這整門課到這裡結束。謝謝你的耐心。go hack something real。

## 自我檢核

- [ ] 完成 project，交一份 writeup + repo
- [ ] 知道自己的 workflow 可複製到下一個 target
- [ ] 對每個工具的限制有第一手體驗
- [ ] 有信心對別人解釋 symex、taint、hybrid fuzzing 的工作原理
- [ ] 知道下一步要往哪走

→ [README](./README.md)
