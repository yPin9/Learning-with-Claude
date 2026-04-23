# Ch 19 — 如何送 upstream：review 流程與文化

> 目標：理解 LLVM / GCC upstream contribution 的完整流程、review 文化、常見 pitfall。能從「自己改 local」升級成「patch 被 merge 進 upstream」。這是 SiFive 工程師的 career track。

## Upstream contribution 的意義

為什麼要 upstream：

- **Maintenance**：merge 進 upstream = 不用 maintain 自己的 fork
- **生態影響力**：你的修改全世界用 → RISC-V 真實貢獻
- **職涯**：GitHub 貢獻記錄是面試最強武器
- **學習**：review 過程強迫你提升 code quality

代價：

- 慢（weeks 到 months）
- 要寫 test
- 要處理 review comment
- Style / convention 要符合

## LLVM contribution 流程

LLVM 2023 後用 GitHub PR（之前是 Phabricator，已廢）。

### Step 1: Fork + branch

```bash
# Fork llvm/llvm-project on GitHub
git clone git@github.com:你的帳號/llvm-project.git
cd llvm-project
git checkout -b riscv-xmyext-support
```

### Step 2: 實作 + test

按 Ch 9 跟 Ch 14 的流程加 feature。重要：

- 每個 commit 都編得過
- 每個 commit 都 test pass
- 寫對應的 LLVM test

### Step 3: 跑全套測試

```bash
cd build
ninja check-all
# 或只跑 RISC-V:
ninja check-llvm-codegen-riscv
ninja check-llvm-mc-riscv
```

**任何 regression 要修**。

### Step 4: Format

LLVM 用 clang-format + 特定 rules：

```bash
clang-format -i your_changes.cpp
```

Style guide: `llvm/docs/CodingStandards.rst`。基本：

- 80 columns
- 2-space indent
- namespace 不 indent
- `///` for doxygen comment

### Step 5: 建 commit

```bash
git add .
git commit -m "[RISCV] Add XMyExt extension support

This adds support for the XMyExt extension, which includes:
- XMADD instruction
- Compiler pattern matching
- Assembler/disassembler support
- Tests for encoding and codegen

Differential Revision: https://reviews.llvm.org/..."
```

慣例：

- 標題：`[RISCV]` or `[RISCV][MC]` 類 prefix
- 第二行空
- 之後 detail description

### Step 6: Push + PR

```bash
git push origin riscv-xmyext-support
```

到 GitHub 開 Pull Request。

### Step 7: Review

Reviewer 會留 comment。回應：

- 小改：直接 commit 到同 branch、push（PR 自動更新）
- 大改：discussion、可能需要重新設計

常見 review comment：

- Style / naming
- 缺 test
- 邊界 case
- Generic 抽象 (寫得更通用)
- 效能考量

Review cycle 2-5 輪是正常。

### Step 8: 等 approval

小 PR（bug fix）：幾天到一週
中 PR（feature）：一兩週
大 PR（新 extension）：幾週到幾個月

**耐心**。不要催 reviewer。

### Step 9: Merge

Approval 後有 commit access 的人 merge（通常是 reviewer）。你的 patch 進 LLVM main！

## Reviewer 怎麼選

LLVM 的 CODEOWNERS 跟 email list：

- RISC-V 有固定 maintainer list（Alex Bradbury, Craig Topper, 等）
- 自動通知 reviewer
- 也可手動 @ 某人

### RISC-V 主要 reviewer

```
Alex Bradbury (@asb)          ; RISC-V overall maintainer
Craig Topper (@topperc)       ; codegen expert
Philip Reames                 ; performance
Luke Lau (@lukel97)
Yingwei Zheng
Wang Pengcheng
```

Reviewer 不會每個 PR 都看、但 RISC-V 核心維護者會關注。

## LLVM 社群的交流管道

- **GitHub PR**：主要 review 場所
- **LLVM Discourse**：<https://discourse.llvm.org> — 討論設計、技術問題
- **Slack** (`#risc-v` channel)：即時聊
- **LLVM Bi-weekly RISC-V sync meeting**：某些 maintainer 會有
- **Dev Meetings**：每年的 LLVM Dev Meeting（US / EU）

## 典型的「我加 extension」PR

通常拆成多個小 PR：

```
PR 1: [RISCV] Add XMyExt subtarget feature
PR 2: [RISCV] Add XMyExt XMADD instruction + MC support
PR 3: [RISCV] Add XMyExt codegen pattern
PR 4: [Clang][RISCV] Add XMyExt builtin
PR 5: [RISCV] Add XMyExt scheduling info for sifive-xyz
```

**小 PR 比大 PR 好**：

- Reviewer 容易看
- 快 merge
- bisect 好 debug
- rollback 簡單

大 PR（1000+ line）很少直接 merge，會被要求拆。

## 寫好 commit message

模板：

```
[RISCV] Subject line (max 72 chars)

Paragraph explaining the WHY.
What problem this solves, what alternatives were considered.

Second paragraph for HOW if needed.
Describe the approach at high level.

Reviewed By: @asb, @topperc
Pull Request: https://github.com/llvm/llvm-project/pull/12345
```

**Why > How > What**。code 本身講 what，commit 講 why。

## 常見 reviewer comment + 應對

### "Please add test"

永遠配 test。常見 test 類型：

- `llvm/test/CodeGen/RISCV/...`: codegen test (FileCheck based)
- `llvm/test/MC/RISCV/...`: encoding test
- `clang/test/CodeGen/RISCV/...`: clang-level test

每個新 feature 至少一個 test。

### "This looks like existing code in XXX.cpp"

reviewer 說跟既有實作類似。可能要 refactor 成共用 function。

### "What about this edge case?"

想沒想到的 corner。回去 fix + 加 test。

### "Can we make this more generic?"

reviewer 想把你的修改變成 reusable。**Trade-off**：太 generic 變 over-engineering、太具體變 not-reusable。discussion 找平衡。

### "Please split into smaller patches"

PR 太大。耐心拆。

### "Ping"

如果 stalled 幾天沒回應，polite `ping`：

```
ping @reviewer, can you take another look?
```

一週一次 max。

## LLVM 的 coding style

```cpp
// Good
static bool isSpecialInstruction(const MachineInstr &MI) {
    return MI.getOpcode() == RISCV::XMADD;
}

// Bad (多餘 parens, space 不對)
static bool isSpecialInstruction( const MachineInstr & MI )
{
    return (MI.getOpcode() == RISCV::XMADD);
}
```

細節：

- `CamelCase` for class, `camelCase` for function, `camelCase` for variable
- 頭字母單字 capitalize: `MyFoo()` not `MyFOO()` for `Foo`; but `XXXIsSet()` for abbreviation `XXX`
- Comments: `//` not `/* */` (except file header)
- `auto` 明顯時用（型別 trivial）、不明顯時寫全

## Test-driven development

建議 workflow：

1. 先寫 failing test（`llvm/test/CodeGen/RISCV/xmyext.ll` - expected output）
2. 跑 `ninja check-llvm-codegen-riscv` → 確認 fail
3. 改 code
4. 跑 test → 應該 pass
5. 改其他 code → test 仍 pass 當 regression

這確保你的修改不破壞現有 behavior。

## GCC 的 contribution 流程

差不多但工具不同：

- 用 **email + patch file** (not PR)
- Mail list: `gcc-patches@gcc.gnu.org`
- Review 也 email
- Commit access 由 maintainer 統一 push

較「老派」，但流程成熟。對多數 RISC-V 開發者，**LLVM 先、GCC 跟**。

## Upstream 的時間成本

真實數據（我觀察）：

- Trivial bug fix: 幾天到兩週
- 中型 feature（一個 extension 的 MC 支援）：2-6 週
- 大型 feature（完整 V 擴充 opcode set）：6-12 個月
- 跨 ecosystem 改動（ABI 改）：1 年 +

**Plan 足夠時間**。不要 promise 老闆「下週 merge」。

## 第一個 PR：怎麼開始

策略：**選 "good first issue"**。

```
https://github.com/llvm/llvm-project/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22+label%3Ariscv
```

很多小 bug、documentation、clarification。做 3-5 個，熟悉流程、跟 reviewer 互動、建立記錄。

**然後**才挑戰新 feature。

## 撰寫 Release Notes

每個 LLVM release 有 `llvm/docs/ReleaseNotes.rst`。加 feature 要自己加 entry：

```
* Added experimental support for XMyExt extension.
```

這讓用戶知道新功能。

## Reference

官方 guide:

- **LLVM Contributing**: <https://llvm.org/docs/Contributing.html>
- **LLVM Developer Policy**: <https://llvm.org/docs/DeveloperPolicy.html>
- **GCC Contribution**: <https://gcc.gnu.org/contribute.html>

實用 tutorial:

- Alex Bradbury 的 RISC-V LLVM blog
- LLVM Discourse 的 "Beginner's guide to LLVM"

## 真實 story：第一個 PR 的心路

大多人的第一個 LLVM PR 經歷：

- 花 1 週實作（幾十行）
- Push PR
- 等 3 天
- 收到 20 個 review comment
- "為什麼這樣寫不對"、"test 呢"、"style"
- 改 + push + reply
- 再等 2 週
- 收到 5 個 comment
- 再改
- 反覆 3-5 次
- **某天 merge 了**
- 你是 LLVM contributor 了

**第一次最痛、之後習慣**。社群大體上 friendly、願意教新人。

## 常見誤會

1. **「upstream 很難」**：學習曲線有，但不難。多數人 3-6 個月上手。
2. **「要是大 expert 才能貢獻」**：新人 bug fix 也歡迎。
3. **「patch 被拒是羞恥」**：不是。多數被要求 rework，rare 全拒。
4. **「reviewer 很兇」**：直接是常態、並非惡意。讀 comment 學東西。
5. **「自己 maintain fork 也行」**：短期。長期人力成本巨大、跟 upstream divergence 越深越難 merge。

## 動手練習

1. 在 LLVM GitHub 找 3 個 `good first issue` 的 RISC-V bug，讀 discussion 理解 contex。
2. 嘗試 reproduce 一個 bug：checkout 當前 main、讀 reproduce steps、驗證。
3. 讀最近 10 個 RISC-V PR，看 review 怎麼進行、什麼 comment 常見。
4. 寫一個極小 fix（比如改 comment、fix typo），真的 open PR。
5. 去 LLVM Discourse 的 `#community` 區讀最近的 RISC-V discussion。

## 自我檢核

- [ ] 我知道 LLVM PR 的流程（fork → branch → PR → review → merge）
- [ ] 我能寫合格的 commit message
- [ ] 我知道 RISC-V 主要 reviewer 是誰
- [ ] 我知道要拆大 PR 成小 PR
- [ ] 我有 first-PR 的 plan 並選好 issue

下一章是全課收尾 — RISC-V backend source 的完整地圖，讓你知道每個問題該查哪個檔。

→ [Ch 20 RISC-V target 源碼地圖](./20-source-code-map.md)
