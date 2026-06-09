# Ch 33 — 專案基礎建設

> **目標**：學會為專案建立「準備好接受貢獻」的基礎設施——README、LICENSE、CONTRIBUTING、CODE_OF_CONDUCT、issue/PR 範本、SECURITY、badge、文件。這些檔案是專案的門面與規則，決定貢獻者來了是被引導還是碰壁。這是把你的專案從「一坨 code」變成「健康開源專案」的關鍵。

> **環境**：GitHub。前置：整個 Part 1-6（你現在懂貢獻者需要什麼了，反過來為他們建）。

## 為什麼基礎設施決定專案能不能被貢獻

你開源了一個有用的東西。但如果它只有 code、沒有任何說明文件——貢獻者來了會：不知道這是什麼（沒 README）、不知道能不能用（沒 LICENSE）、不知道怎麼貢獻（沒 CONTRIBUTING）、開 issue 不知道填什麼（沒範本）。結果：好的潛在貢獻者碰壁離開，issue/PR 品質低落（沒引導）。

**基礎設施是專案的門面 + 規則 + 引導**。它把你前面所有章節學到的「貢獻者需要什麼」反過來提供——你當過貢獻者，知道好的 CONTRIBUTING/範本多重要（Ch 16/12/11），現在為你的貢獻者建好它們。這是維護者「降低貢獻門檻」的核心工作。

## 先建立直覺：站在第一次來的人的角度

```
   一個陌生人第一次來到你的 repo，他想知道：
   1. 這是什麼？能解決我什麼？      → README
   2. 我能用它嗎（法律）？          → LICENSE
   3. 怎麼安裝/開始用？             → README（quickstart）
   4. 我想貢獻，怎麼做？            → CONTRIBUTING
   5. 開 issue/PR 要填什麼？        → 範本
   6. 這社群友善嗎？               → CODE_OF_CONDUCT
   7. 發現安全漏洞怎麼回報？        → SECURITY
   8. 這專案還活著/品質如何？       → badge、最近活動

   每個問題沒被回答 = 一個潛在貢獻者/使用者流失的點
```

維護者的工作：站在「第一次來的人」角度，把這些問題的答案都準備好。基礎設施就是這些答案的集合。

## README：專案的門面

README 是第一印象，最重要的檔案。一個好 README 回答「這是什麼、為什麼用、怎麼用」：

```markdown
# 專案名

> 一句話說清楚這是什麼、解決什麼問題。

[badges: build status / version / license / coverage]

## Features
- 核心賣點（為什麼用這個而非別的）

## Installation
```bash
pip install yourproject      # 最快上手的方式
```

## Quick Start
```python
# 最小的「能跑起來」範例——讓人 30 秒看到它能幹嘛
```

## Documentation
連到完整文件

## Contributing
歡迎貢獻！見 [CONTRIBUTING.md](CONTRIBUTING.md)

## License
MIT（連到 LICENSE）
```

README 的關鍵：

- **開頭一句話講清楚是什麼**：別讓人讀三段還不知道這幹嘛的。
- **quick start 要能 30 秒跑起來**：最小可動範例，讓人立刻看到價值。
- **降低上手門檻**：安裝、第一個範例越簡單越好。
- **badge**：build 狀態、版本、覆蓋率、license——一眼看出專案健康/品質（也是 Ch 16 貢獻者評估的）。

> README 是給「不認識你專案的人」看的——別假設讀者知道任何脈絡。一個工程師覺得「顯然」的東西，第一次來的人不知道。把「這是什麼、為什麼、怎麼用」講到陌生人能懂。

## LICENSE：法律基礎

承 Ch 21——**沒有 LICENSE 的 repo = 保留所有權利 = 別人不能合法使用/貢獻**。所以開源專案**必須**有 LICENSE。

```bash
# GitHub 建 repo 時可選；或事後：
# 用 choosealicense.com 選，放 LICENSE 檔
```

選授權（Ch 21）：

- **MIT / Apache 2.0**：寬鬆，最廣用，想讓人（含商用）自由使用就選這。
- **GPL**：copyleft，要求衍生物也開源。
- 不確定 → MIT（最簡單、最自由、生態最接受）。

GitHub 認得標準 LICENSE 檔，會在 repo 頁面顯示授權類型。

## CONTRIBUTING：貢獻指引

承 Ch 16（貢獻者必讀 CONTRIBUTING）——現在你寫它。`CONTRIBUTING.md` 告訴貢獻者「怎麼貢獻你的專案」：

```markdown
# Contributing

感謝你想貢獻！

## Setup
怎麼設定開發環境（clone、裝相依、build）。

## Running tests
`pytest tests/`  ← push 前必跑

## Workflow
1. Fork & branch (off `main`)
2. Make changes, add tests
3. Run tests & linter
4. Open a PR with a clear description, link issues with `Closes #N`

## Conventions
- Commit messages: Conventional Commits (feat:/fix:/...)
- Code style: enforced by black + flake8 (pre-commit)
- Branching: GitHub Flow

## Code of Conduct
參與即同意 [CoC](CODE_OF_CONDUCT.md)

## Where to start
找 `good first issue` 標籤的 issue。有問題開 Discussion。
```

CONTRIBUTING 該回答貢獻者的所有「怎麼做」（Ch 16 列的）：環境設定、跑測試、commit/branch 規範、CLA/DCO、code style、從哪開始。寫得好 = 貢獻順暢、PR 品質高；沒有 = 貢獻者瞎猜、踩雷、PR 品質低。

GitHub 會在「開 issue/PR」時自動連結 CONTRIBUTING（提示貢獻者讀）。

## issue / PR 範本

承 Ch 12/11（從貢獻者看範本）——現在你建。範本引導貢獻者提供你需要的資訊：

```
   .github/
   ├── ISSUE_TEMPLATE/
   │   ├── bug_report.yml        ← issue form（YAML，可設必填欄位）
   │   ├── feature_request.yml
   │   └── config.yml            ← 設定（如把 question 導向 Discussions）
   └── PULL_REQUEST_TEMPLATE.md   ← PR 範本
```

issue forms（YAML）比舊的 markdown 範本強——可設必填欄位、下拉、checkbox，**強制**貢獻者提供完整資訊（少 needs-info 來回，Ch 30）：

```yaml
# .github/ISSUE_TEMPLATE/bug_report.yml
name: Bug Report
description: Report a bug
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

`config.yml` 可把「問題」導向 Discussions（不讓開成 issue，Ch 12/30）：

```yaml
blank_issues_enabled: false
contact_links:
  - name: Question
    url: https://github.com/owner/repo/discussions
    about: Ask usage questions here, not in issues.
```

## CODE_OF_CONDUCT 與 SECURITY

承 Ch 31（CoC）、Ch 34（安全）：

```
   CODE_OF_CONDUCT.md   ← 採用 Contributor Covenant（Ch 31）
   SECURITY.md          ← 怎麼私下回報安全漏洞（Ch 34）
```

`SECURITY.md` 特別重要（Ch 34 詳述）——它告訴人「發現漏洞別開公開 issue，私下這樣回報」。GitHub 會在 repo 的 Security 頁面顯示它。

## GitHub 的「community profile」

GitHub 有個 **Community Standards** 檢查表（repo 的 Insights → Community Standards），列出健康專案該有的檔案：

```
   ☑ Description（repo 描述）
   ☑ README
   ☑ Code of conduct
   ☑ Contributing
   ☑ License
   ☑ Security policy
   ☑ Issue templates
   ☑ Pull request template
```

把這些補齊 = GitHub 認證你的專案「基礎設施完整」。這也是貢獻者評估專案健康度的訊號（Ch 16）。

> 放置位置：這些檔案可放 repo 根目錄或 `.github/` 目錄（GitHub 兩處都認）。`.github/` 較整潔（把 meta 檔案集中）。LICENSE/README 慣例放根目錄（顯眼）。

## 其他基礎建設

完整的健康專案還可能有：

- **文件站**：超過 README 的完整文件（用 MkDocs、Docusaurus、Sphinx 等，host 在 GitHub Pages / Read the Docs）。
- **CHANGELOG.md**（Ch 32）：版本變更紀錄。
- **CI**（Ch 14/練習 E）：自動測試/lint/build。
- **badge**：README 上的狀態徽章（CI、版本、覆蓋率、下載數）——一眼看品質。
- **CODEOWNERS**（Ch 24）：責任地圖。
- **`.github/dependabot.yml`**（Ch 34）：自動更新相依。
- **`FUNDING.yml`**：贊助連結（GitHub Sponsors 等，Ch 31 永續）。

## 一個完整的專案基礎設施 checklist

把你的專案武裝成健康開源專案（綜合本章 + 前面）：

```
   必備：
   ☑ README（門面：是什麼/為什麼/怎麼用，quick start）
   ☑ LICENSE（Ch 21，沒有就不能合法用）
   ☑ CONTRIBUTING（怎麼貢獻，Ch 16）
   ☑ issue/PR 範本（引導，Ch 11/12）
   ☑ CODE_OF_CONDUCT（社群底線，Ch 31）
   ☑ SECURITY.md（漏洞回報，Ch 34）

   強烈建議：
   ☑ CI（Ch 14）+ badge
   ☑ branch protection（Ch 23）+ CODEOWNERS（Ch 24）
   ☑ CHANGELOG（Ch 32）
   ☑ good first issue 標籤（招募，Ch 30）
   ☑ pre-commit + 規範自動化（Ch 27）
```

這基本上就是練習 F（打造一個準備好接受貢獻的專案）。

## 踩雷集錦

1. **沒 LICENSE**：別人不能合法用/貢獻（Ch 21）。開源必有。
2. **README 假設讀者懂脈絡**：開頭三段還不知道這幹嘛。一句話講清楚 + 30 秒 quick start。
3. **沒 CONTRIBUTING**：貢獻者瞎猜流程、踩雷、PR 品質低。寫清楚怎麼貢獻。
4. **沒 issue/PR 範本**：收到資訊不足的 issue/PR，triage 累（Ch 30）。用 forms 強制完整資訊。
5. **CoC 放著不執行**（Ch 31）：有比沒有好，但要願意執法。
6. **沒 SECURITY.md**：漏洞被開成公開 issue（暴露給攻擊者，Ch 34）。提供私下回報管道。
7. **基礎設施一次想做完美**：從必備的開始（README/LICENSE/CONTRIBUTING），逐步補。別因為「要做的太多」而都不做。

## 進階：再往深一層

- **README 的「為什麼選我」**：競品多時，README 要說清楚「為什麼用這個而非別的」（差異化）——吸引使用者。
- **文件即程式碼（docs-as-code）**：文件放 repo、用 PR 流程維護、CI 檢查（壞連結、build）——讓文件和 code 一起演進、社群能貢獻文件。
- **範本的進階**：多個 issue 範本（bug/feature/不同類型）、PR 範本的 checklist、`config.yml` 的 contact_links。
- **`.github` repo**：org 可建一個特殊的 `.github` repo，放「整個 org 共用」的範本/CoC/CONTRIBUTING（個別 repo 沒有的就用 org 的）——大組織省得每 repo 重複。
- **GitHub Pages / 文件 host**：超過 README 的專案需要文件站；GitHub Pages 免費 host。
- **all-contributors**：自動認可各種貢獻（含非 code，Ch 31）的工具，在 README 列出貢獻者。
- **template repo**：把你的「健康專案骨架」做成 GitHub template repo，下次開新專案一鍵套用所有基礎設施。

## 動手練習

1. 看一個你覺得「文件做得好」的開源專案，對照本章 checklist——它有哪些基礎設施？README 怎麼寫的（一句話 pitch？quick start？badge？）。
2. 用 GitHub 的 Community Standards 檢查（Insights → Community Standards）看一個你的 repo 缺什麼。
3. 為一個（你的或假想的）專案寫一個好 README：一句話 pitch + Features + Installation + Quick Start。
4. 寫一個 CONTRIBUTING.md（環境設定、跑測試、workflow、規範、從哪開始）。
5. 建 issue forms（YAML，bug + feature，含必填欄位）+ config.yml（把 question 導向 Discussions）+ PR 範本。
6. （這就是練習 F）把一個 repo 補齊所有必備基礎設施（README/LICENSE/CONTRIBUTING/範本/CoC/SECURITY）。

## 本章重點整理

- 基礎設施是專案的門面+規則+引導——把你當貢獻者時需要的東西反過來提供，決定貢獻者來了被引導還是碰壁。
- 站在「第一次來的人」角度準備答案：README（是什麼/怎麼用）、LICENSE（能不能用）、CONTRIBUTING（怎麼貢獻）、範本（填什麼）、CoC（社群）、SECURITY（漏洞回報）。
- README 是門面：一句話 pitch + 30 秒 quick start + badge；別假設讀者懂脈絡。
- LICENSE 必有（Ch 21，沒有不能合法用）；CONTRIBUTING 回答所有「怎麼貢獻」（Ch 16）；issue forms 強制完整資訊（Ch 30）。
- GitHub Community Standards 是健康專案的 checklist；從必備開始逐步補，別因要做的多而不做。

## 自我檢核

- [ ] 為什麼說「基礎設施決定專案能不能被貢獻」？
- [ ] 一個第一次來的人想知道哪些事？對應哪些檔案？
- [ ] 好 README 的關鍵要素是什麼（pitch、quick start、為什麼）？
- [ ] 為什麼 LICENSE 是必須的（Ch 21）？
- [ ] issue forms 比舊範本強在哪？config.yml 能做什麼？

## 延伸閱讀

### 官方指南

- **[Open Source Guides: Starting an Open Source Project](https://opensource.guide/starting-a-project/)** — GitHub
  - **讀哪裡**:整篇——README、LICENSE、CONTRIBUTING、CoC 的建立。
  - **和本章的關聯**:建立專案基礎設施的官方權威。

- **[GitHub Docs: Setting up your project for healthy contributions](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions)**
  - **讀哪裡**:CONTRIBUTING、範本、CoC、Community Standards。
  - **和本章的關聯**:每個檔案的操作權威（含 issue forms YAML）。

### 工具 / 站點

- **[choosealicense.com](https://choosealicense.com/)**（Ch 21）、**[Make a README](https://www.makeareadme.com/)**、**[Keep a Changelog](https://keepachangelog.com/)**（Ch 32）
  - **這些是什麼**:選授權、寫 README、寫 changelog 的工具/指南。
  - **和本章的關聯**:各基礎設施檔案的實用範本。

基礎設施裡有一塊特別嚴肅、不能馬虎——安全。下一章是維護者的安全責任：security policy、責任揭露、Dependabot、供應鏈。

→ [Ch 34 安全與責任揭露](./34-security-disclosure.md)
