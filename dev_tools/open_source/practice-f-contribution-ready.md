# 練習 F — 打造一個準備好接受貢獻的專案

> **目標**：把 Part 6（維護者視角）綜合起來，把一個你的小專案（或新建一個）打造成「準備好接受貢獻、安全、可持續」的開源專案——完整的基礎設施、協作規則、安全配備、維護者流程。完成後你會具備「從零經營一個健康開源專案」的能力，這是 Part 6 的集大成。

> 前置：Part 6 全部（Ch 28-34），以及練習 E（協作規則）。

## 背景與動機

前面所有 Part 6 章節是「維護者該會什麼」，這個練習是「把它們全部做出來」。你要把一個專案從「一坨能跑的 code」變成「一個別人來了會被歡迎、被引導、能順利貢獻，而且安全、可持續」的開源專案。

這是維護者的集大成：基礎設施（Ch 33）、協作規則（Ch 23/24/練習 E）、release（Ch 32）、社群（Ch 31）、安全（Ch 34）。做完這個，你不只會貢獻別人的專案，還會經營自己的——這是 open source 的另一半，也是很多工程師職涯的轉捩點（從使用者到維護者）。

## 任務規格

### 你要做的事

選一個專案（你寫過的小工具/函式庫，或新建一個簡單的），把它武裝成完整的健康開源專案。

### 完整 checklist（驗收標準）

**基礎設施（Ch 33）**
- [ ] README（一句話 pitch + Features + Installation + Quick Start + badge）
- [ ] LICENSE（選一個，Ch 21）
- [ ] CONTRIBUTING.md（環境設定、跑測試、workflow、規範、從哪開始）
- [ ] CODE_OF_CONDUCT.md（採用 Contributor Covenant，Ch 31）
- [ ] issue forms（bug + feature，YAML，含必填）+ config.yml（question 導向 Discussions）
- [ ] PR 範本
- [ ] SECURITY.md（私下回報管道，Ch 34）

**協作規則（Ch 22-24，練習 E）**
- [ ] branch protection（main：PR + review + CI + 禁 force-push）
- [ ] CODEOWNERS
- [ ] CI（test + lint，required check）
- [ ] pre-commit（格式化 + 擋 secret）

**維護者配備（Ch 30-34）**
- [ ] 一套 label 系統（含 good first issue）
- [ ] 至少一個寫清楚的 good first issue（招募）
- [ ] CHANGELOG.md + 一個 release（tag + GitHub Release，Ch 32）
- [ ] Dependabot 開啟（Ch 34）
- [ ] （選）規範自動化（Conventional Commits）

**驗證維護者流程**
- [ ] 用一個測試 PR 走一遍「你當維護者 review + merge」（Ch 29）
- [ ] 用一個測試 issue 走一遍「你 triage」（Ch 30）
- [ ] 寫一份「維護者手冊」：你怎麼 review、triage、release、處理安全回報

## 期望成果

一個任何陌生人來到都能：30 秒看懂這是什麼（README）、知道怎麼用（quick start）、知道怎麼貢獻（CONTRIBUTING）、開 issue/PR 有引導（範本）、感到社群友善（CoC）、知道怎麼回報漏洞（SECURITY）——而且 main 受保護、CI 把關、有 good first issue 招募新人、能安全發 release。一個「健康開源專案」的範本。

## 如果你卡住了

1. **沒有專案可用？** 寫一個超簡單的（一個小 CLI 工具、一個小函式庫）——重點是基礎設施和流程，不是 code 多厲害。或 fork 一個你的舊 side project。
2. **要做的太多不知從何開始？** 從必備的開始（README + LICENSE + CONTRIBUTING），再逐步加。別想一次完美（Ch 33 踩雷）。
3. **GitHub Community Standards 幫你檢查**：Insights → Community Standards 列出缺什麼，照著補。
4. **branch protection 設了自己也不能 push？** 對（include admins，Ch 23）——後續改動走 PR，正好驗證流程。或先把基礎設施 push 好再設保護。
5. **怎麼測「維護者 review」？** 用分身帳號發 PR、你 review；或自己開 PR 體驗 review 介面（雖然不能 approve 自己）。

## 實作步驟建議

### Step 1：基礎設施（Ch 33）

建/選專案，補齊 README、LICENSE、CONTRIBUTING、CoC、範本、SECURITY。

### Step 2：協作規則（練習 E 的內容，Ch 22-24）

CI、branch protection、CODEOWNERS、pre-commit。

### Step 3：維護者配備（Ch 30-32）

label 系統、good first issue、CHANGELOG、一個 release、Dependabot。

### Step 4：驗證維護者流程（Ch 29-30）

用測試 PR/issue 走一遍 review/triage/merge。

### Step 5：維護者手冊

寫下你的 review/triage/release/安全處理流程。

## 完整參考解答

**自己先做，卡住再看。**

<details>
<summary>點開完整的「健康專案」骨架</summary>

假設你的專案是一個簡單的 CLI 工具 `mytool`。

### 目錄結構（武裝後）

```
mytool/
├── README.md                          # 門面（Ch 33）
├── LICENSE                            # MIT（Ch 21）
├── CONTRIBUTING.md                    # 貢獻指引（Ch 16/33）
├── CODE_OF_CONDUCT.md                 # Contributor Covenant（Ch 31）
├── SECURITY.md                        # 漏洞回報（Ch 34）
├── CHANGELOG.md                       # 版本變更（Ch 32）
├── .pre-commit-config.yaml            # 規範自動化（Ch 27）
├── .github/
│   ├── CODEOWNERS                     # 責任地圖（Ch 24）
│   ├── dependabot.yml                 # 相依安全（Ch 34）
│   ├── PULL_REQUEST_TEMPLATE.md       # PR 範本（Ch 11）
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug_report.yml             # issue form（Ch 12/33）
│   │   ├── feature_request.yml
│   │   └── config.yml                 # question → Discussions
│   └── workflows/
│       ├── ci.yml                     # 測試/lint（Ch 14）
│       └── stale.yml                  # stale bot（Ch 30）
├── src/
├── tests/
└── docs/
```

### README.md（門面）

```markdown
# mytool

> A tiny CLI to do X quickly. （一句話講清楚）

[![CI](https://github.com/you/mytool/actions/workflows/ci.yml/badge.svg)](...)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

## Features
- Fast X
- Simple Y

## Installation
```bash
pip install mytool
```

## Quick Start
```bash
mytool do-thing input.txt    # 30 秒看到價值
```

## Contributing
We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md).
Look for [good first issues](https://github.com/you/mytool/labels/good%20first%20issue).

## License
MIT
```

### SECURITY.md（Ch 34）

```markdown
# Security Policy
**Do NOT open public issues for security vulnerabilities.**
Report via GitHub's "Report a vulnerability" (Security tab) or
security@example.com. We acknowledge within 48h.

## Supported Versions
| Version | Supported |
|---------|-----------|
| 1.x     | ✅        |
```

### issue form（.github/ISSUE_TEMPLATE/bug_report.yml）

```yaml
name: Bug Report
description: Report a bug
labels: ["bug", "needs-triage"]
body:
  - type: input
    attributes: { label: Version }
    validations: { required: true }
  - type: textarea
    attributes: { label: Steps to reproduce }
    validations: { required: true }
  - type: textarea
    attributes: { label: Expected vs Actual }
    validations: { required: true }
```

### config.yml（question 導向 Discussions，Ch 12/30）

```yaml
blank_issues_enabled: false
contact_links:
  - name: Question / Help
    url: https://github.com/you/mytool/discussions
    about: Ask usage questions here, not in issues.
```

### CI + branch protection + CODEOWNERS + pre-commit

（同練習 E——test/lint workflow、main 保護規則、CODEOWNERS、pre-commit config。略，見練習 E 解答。）

### Dependabot（.github/dependabot.yml，Ch 34）

```yaml
version: 2
updates:
  - package-ecosystem: "pip"
    directory: "/"
    schedule: { interval: "weekly" }
```

### stale bot（.github/workflows/stale.yml，Ch 30）

```yaml
name: Stale
on:
  schedule: [{ cron: "0 0 * * *" }]
jobs:
  stale:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/stale@v9
        with:
          days-before-stale: 60
          days-before-close: 7
          stale-issue-message: "Inactive for 60 days; closing in 7 if no activity."
```

### label 系統 + good first issue（Ch 30）

```bash
gh label create "good first issue" --color "7057ff"
gh label create "help wanted" --color "008672"
gh label create "priority: high" --color "d93f0b"
# ... 建一個寫清楚的 good first issue（招募）
gh issue create --title "Add --version flag" \
  --body "Add a --version flag that prints the version. Good for first-timers. See src/cli.py." \
  --label "good first issue"
```

### release（Ch 32）

```bash
# CHANGELOG.md 寫好，打 tag、發 release
git tag -a v1.0.0 -m "Release v1.0.0"
git push origin v1.0.0
gh release create v1.0.0 --generate-notes
```

### 維護者手冊（自己的流程紀錄）

```markdown
# Maintainer Playbook

## Reviewing PRs (Ch 29)
- Priority: correctness > design > maintainability > tests > style
- Style is automated (pre-commit/CI) — don't nitpick
- Be constructive, explain why, use questions, mark nits with "nit:"
- Approve if it improves the codebase (don't demand perfection)
- Reject out-of-scope PRs early, politely (thank + explain + redirect)

## Triaging issues (Ch 30)
- Daily: label new issues, close duplicates (link original), needs-info for incomplete
- Maintain good first issues for new contributors
- Questions → Discussions

## Releasing (Ch 32)
- Follow semver; update CHANGELOG (highlight breaking + migration)
- Tag + GitHub Release

## Security (Ch 34)
- Vulnerabilities: private (Security Advisory), fix privately, coordinate disclosure, credit reporter
```

**解答說明**：

這個練習把 Part 6（+ Part 5）全部落地成一個真實的專案骨架：
- **基礎設施**（Ch 33）：README/LICENSE/CONTRIBUTING/CoC/SECURITY/範本——站在「第一次來的人」角度準備好所有答案。
- **協作規則**（練習 E/Ch 23/24）：CI + branch protection + CODEOWNERS + pre-commit——main 不可能被搞壞。
- **維護者配備**（Ch 30/32/34）：label + good first issue（招募）+ release + Dependabot + stale bot——可持續運作的機制。
- **維護者手冊**：把 Ch 29-34 的流程寫成你自己的 playbook——這逼你內化維護者的判斷。

驗證（用測試 PR/issue 走 review/triage）讓你實際體驗維護者的日常。做完這個，你的專案就是一個「健康開源專案」的範本，你也具備了從零經營專案的能力。

</details>

## 檢查點

| 類別 | 檢查 |
|---|---|
| 基礎設施 | README/LICENSE/CONTRIBUTING/CoC/SECURITY/範本齊（Community Standards 全綠）|
| 協作規則 | main 受保護、CI required、CODEOWNERS、pre-commit |
| 維護者配備 | label 系統 + good first issue、CHANGELOG + release、Dependabot |
| 流程驗證 | 走過一次維護者 review + triage |
| 手冊 | 寫下 review/triage/release/安全流程 |

## 延伸挑戰（加分）

1. **完整自動發布**：配 Conventional Commits + release-please，merge 自動算版本 + changelog + release（Ch 27/32）。
2. **文件站**：用 MkDocs/Docusaurus + GitHub Pages 建超過 README 的文件站（Ch 33）。
3. **真的開源它**：把這個專案真的公開、發到 package registry（npm/PyPI）、宣傳——體驗「真的有人來用/貢獻」。
4. **招募真實貢獻**：把 good first issue 標好，看有沒有人來貢獻（你當維護者 review）——體驗真實的維護者-貢獻者互動。
5. **模擬安全回報**：用分身私下回報一個「漏洞」，你走一遍責任揭露流程（私下修 + advisory，Ch 34）。
6. **org + 共用 .github**：建一個 org、用 `.github` repo 放共用範本/CoC（Ch 33 進階）。

## 自我檢核

- [ ] 我能從零把一個專案武裝成「健康開源專案」（基礎設施 + 規則 + 安全 + 可持續）
- [ ] 我的專案讓第一次來的人能看懂、能用、能貢獻、感到友善、知道怎麼回報漏洞
- [ ] 我設好了協作規則（保護分支 + CI + CODEOWNERS）和維護者配備（label + good first issue + release + Dependabot）
- [ ] 我走過維護者的日常流程（review + triage）並寫下自己的 playbook
- [ ] 我理解經營專案是「降低貢獻門檻 + 把關品質 + 安全 + 可持續」的綜合工作

Part 6 完成——你既會貢獻別人的專案，也會經營自己的。Part 7 是進階與整合：進階 git 協作場景、疑難雜症排解、開源生涯，以及 Final Project——對真實專案做出一個有意義的貢獻。

→ [Ch 35 進階 git 協作場景](./35-advanced-git-scenarios.md)
