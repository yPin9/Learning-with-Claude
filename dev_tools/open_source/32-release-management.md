# Ch 32 — release 管理

> **目標**：學會維護者的交付工作——語意化版本（semver）、git tag、changelog、GitHub Releases、以及怎麼用 Conventional Commits 自動化整個發布流程。release 是「把改動交付給使用者」的儀式，做好它使用者才能安心升級。

> **環境**：git 2.40+、GitHub、`gh` CLI。前置：Ch 27（Conventional Commits）、Ch 14（CI）。

## 為什麼 release 管理重要

PR 合進 main 不等於使用者拿到了。**release** 是把累積的改動「打包、標版本、公告、交付」給使用者的儀式。做好它：使用者知道有什麼變、能不能安心升級、怎麼升級。做不好：使用者升級就壞（不知道有 breaking change）、不知道改了什麼、不敢升級。

對函式庫尤其關鍵——別人的程式依賴你的版本，你的 release 紀律直接影響整個生態。會 release，是維護者「對使用者負責」的具體展現。

## 先建立直覺：版本號是給使用者的承諾

```
   使用者看到你發了 v2.0.0，他要能判斷：
   - 我能安心從 v1.x 升上去嗎？
   - 會不會壞掉我的程式？
   - 有什麼新東西？

   版本號 + changelog 就是回答這些的「承諾」：
   - 版本號的「形狀」告訴他「升級風險多大」
   - changelog 告訴他「具體變了什麼」
```

亂跳的版本號、沒有 changelog = 使用者不敢升級（不知道風險）。有紀律的版本 + 清楚 changelog = 使用者能做明智的升級決定。release 的核心是**讓使用者能判斷升級風險**。

## Semantic Versioning（semver）

最廣用的版本號規範——**MAJOR.MINOR.PATCH**（如 `2.4.1`），每個數字有明確意義：

```
   MAJOR.MINOR.PATCH
     │     │     │
     │     │     └─ PATCH：bug 修復，向後相容（安心升）
     │     └─────── MINOR：新功能，向後相容（安心升，多了東西）
     └───────────── MAJOR：breaking change，不相容（升級要小心、改 code）

   2.4.1 → 2.4.2  只修了 bug，安心升
   2.4.1 → 2.5.0  加了新功能，安心升（舊的還能用）
   2.4.1 → 3.0.0  有 breaking change，升級可能要改你的 code！
```

semver 的核心承諾：**MAJOR 變了才可能壞你的程式**。所以使用者看到 patch/minor 升級可以放心，看到 major 升級就知道要讀 changelog、可能要改 code。

對應 Conventional Commits（Ch 27）：

```
   fix:                → PATCH +1
   feat:               → MINOR +1
   feat! / BREAKING CHANGE → MAJOR +1
```

這個對應是自動化發布的基礎（下面）——commit 的 type 直接決定版本怎麼跳。

> 認識論誠實：semver 的「向後相容」在實務上有灰色地帶（什麼算 breaking？修 bug 改了行為算不算？）。且不是所有專案嚴格遵守（有些用日期版本 CalVer 如 `2024.03`、有些 0.x 階段不保證相容）。但 semver 是函式庫生態的主流共識——遵守它，使用者才能靠版本號判斷風險。`0.x.y` 是特例（公開 API 還不穩定，任何版本都可能 break）。

## Git tag：標記版本

release 在 git 層面是一個 **tag**——指向「這個 commit 是 v2.4.1」的標記：

```bash
# 建一個 annotated tag（推薦，含訊息/作者/日期）
git tag -a v2.4.1 -m "Release v2.4.1"
git push origin v2.4.1            # tag 要單獨 push（不會跟著 git push 走）

# 看 tag
git tag                          # 列出所有 tag
git show v2.4.1                  # 看某個 tag

# 從 tag checkout（使用者拿特定版本）
git checkout v2.4.1
```

> annotated tag（`-a`）vs lightweight tag：annotated 是完整的 git 物件（有作者、日期、訊息、可簽署），release 一律用 annotated。lightweight 只是個指標，臨時標記用。tag 慣例加 `v` 前綴（`v2.4.1`）。

## Changelog：人讀的「變了什麼」

tag 是給機器的，**changelog**（`CHANGELOG.md`）是給人讀的「這個版本變了什麼」。好的 changelog 讓使用者一眼看懂該不該升、升級要注意什麼：

```markdown
# Changelog

## [2.5.0] - 2024-03-15
### Added
- Support for async handlers (#234)
- New `--verbose` flag for debugging

### Fixed
- Crash when config file is empty (#240)

### Changed
- Improved error messages for network failures

## [3.0.0] - 2024-04-01
### ⚠ BREAKING CHANGES
- `connect()` now requires an explicit timeout argument (#250)
  Migration: add `timeout=30` to existing calls.

### Added
- ...
```

好 changelog 的要素（[Keep a Changelog](https://keepachangelog.com/) 規範）：

- **分類**：Added / Changed / Fixed / Deprecated / Removed / Security。
- **突出 breaking change**：major 版本的 breaking change 要醒目，並附**遷移指引**（怎麼改 code）。
- **連結 issue/PR**：可追溯。
- **給人讀，不是 commit log dump**：篩選、整理成使用者關心的（不是把所有 commit 倒出來）。

> changelog 是寫給「**使用者**」的，不是貢獻者——重點是「對使用我的東西的人有什麼影響」，尤其 breaking change 的遷移指引。一個只 dump commit message 的 changelog 沒用（使用者不在乎你的內部重構）。

## GitHub Releases

GitHub 把 tag + changelog + 下載檔包成 **Release**（repo 的 Releases 頁面）：

```bash
gh release create v2.5.0 \
  --title "v2.5.0" \
  --notes "$(cat <<'EOF'
## What's Changed
- Add async handler support
- Fix empty config crash
...
EOF
)"
# 或附上 build 產物（binary、壓縮檔）：
gh release create v2.5.0 ./dist/myapp-linux ./dist/myapp-mac --notes "..."

# auto-generate release notes（從 PR 標題/label 自動生成）
gh release create v2.5.0 --generate-notes
```

GitHub Release 的價值：使用者有一個「正式版本」的頁面（含 release notes、附件、訂閱通知）。`--generate-notes` 能從合併的 PR 自動產生 release notes（基於 PR 標題、作者、label）——省去手寫。

## 自動化發布：semantic-release / release-please

承 Ch 27——如果專案用 Conventional Commits，整個發布可以**全自動**：

```
   commit 用 Conventional Commits（fix:/feat:/feat!）
        │
   工具（semantic-release / release-please）讀 commit：
   ├─ 算出新版本號（fix→patch, feat→minor, breaking→major）
   ├─ 生成 changelog（分類 commit）
   ├─ 建 git tag
   ├─ 建 GitHub Release
   └─ （函式庫）發布到 npm/PyPI 等
        │
   全部自動，維護者不用手動算版本/寫 changelog/打 tag
```

兩個主流工具：

- **semantic-release**：merge 到 main 就自動發布（每個合進去的 feat/fix 立刻成新版本）。
- **release-please**：開一個「release PR」累積變更，你 merge 那個 PR 才發布（較可控）。

```yaml
# 用 GitHub Action 自動發布（範例：release-please）
- uses: googleapis/release-please-action@v4
  with:
    release-type: node
```

這是 Ch 27 規範自動化的終極回報——commit 寫好格式，版本/changelog/release 全自動。對活躍專案省下大量手動 release 工。

> 取捨：全自動發布（semantic-release）很爽但較難控制發布時機（每個 fix 都發新版可能太頻繁）。release-please 的「release PR」模式較可控（你決定何時發）。手動 release 最可控但最費工。依專案節奏選。

## 一個完整的 release 流程

手動版（理解流程）：

```bash
# 1. 確認 main 是要發布的狀態（CI 綠、該合的都合了）
# 2. 決定版本號（依 semver：這次有 breaking？feature？只有 fix？）
# 3. 更新 CHANGELOG.md（整理這版的變更，突出 breaking + 遷移）
git commit -m "docs: update changelog for v2.5.0"
# 4. 打 tag
git tag -a v2.5.0 -m "Release v2.5.0"
git push origin v2.5.0
# 5. 建 GitHub Release
gh release create v2.5.0 --notes-file <(sed -n '/## \[2.5.0\]/,/## \[/p' CHANGELOG.md)
# 6. （函式庫）發布到 package registry（npm publish / ...）
```

自動版（Conventional Commits + release-please）：你只管寫好格式的 commit，工具自動做 2-6。

## 踩雷集錦

1. **亂跳版本號**：不照 semver（breaking 卻只跳 minor）——使用者依版本號判斷風險，跳錯害他升級壞掉。遵守 semver。
2. **沒有 changelog**：使用者不知道變了什麼、不敢升。一定要有，且寫給使用者（不是 dump commit）。
3. **breaking change 不突出/沒遷移指引**：使用者升 major 版本壞了還不知道為什麼。breaking 要醒目 + 教怎麼改。
4. **tag 沒 push**：`git push` 不會帶 tag，要 `git push origin <tag>`（或 `--tags`）。沒 push 別人看不到。
5. **用 lightweight tag**：release 用 annotated tag（`-a`，含訊息/作者/可簽署）。
6. **changelog 是 commit log dump**：把所有 commit 倒出來沒用。篩選整理成使用者關心的。
7. **0.x 階段以為要嚴格 semver**：0.x 是「API 還不穩」，可以隨意 break（慣例）。到 1.0 才開始 semver 的相容承諾。

## 進階：再往深一層

- **pre-release / RC**：`v2.5.0-rc.1`（release candidate）、`-beta`、`-alpha`——正式發布前讓人測。semver 有 pre-release 語法。
- **發布到 package registry**：函式庫要發到 npm/PyPI/crates.io/Maven 等，使用者才裝得到。CI 自動發布（配 registry token）。
- **簽署 release**：簽署 tag（`git tag -s`）和 release 產物——讓使用者驗證來源（供應鏈安全，Ch 34）。
- **release branch**（Ch 22 Git Flow）：批次發布/維護多版本的專案用 release branch 凍結、出版本。
- **deprecation 流程**：移除功能前先標 deprecated（給使用者時間遷移），下個 major 才移除——別無預警 break。
- **backport**：把修復 cherry-pick（Ch 9）到舊的維護版本（如 v1.x 還在支援，security fix 要 backport）。
- **release notes 的行銷面**：大版本的 release notes 不只列變更，還可以「推銷」亮點功能——吸引使用者升級、宣傳專案。

## 動手練習

1. 對一個版本變更情境判斷 semver：(a) 修了個 bug；(b) 加了向後相容的新功能；(c) 改了函式簽名——各該跳 patch/minor/major？
2. 在測試 repo 寫一個 `CHANGELOG.md`（Keep a Changelog 格式，含一個 breaking change + 遷移指引）。
3. 打一個 annotated tag、push、用 `gh release create` 建一個 GitHub Release（含 notes）。
4. 試 `gh release create --generate-notes`，看它從 PR 自動生成的 release notes。
5. 看一個用 Conventional Commits + 自動發布的專案（很多），看它的 commit → 自動生成的 changelog/release 的對應關係。
6. 看一個大型函式庫的 CHANGELOG 和某個 major 版本的 release notes，學「怎麼寫 breaking change 的遷移指引」。

## 本章重點整理

- release 是把改動「打包、標版本、公告、交付」給使用者；核心是讓使用者能判斷升級風險。
- Semver（MAJOR.MINOR.PATCH）：major=breaking（要小心）、minor=新功能（相容）、patch=修 bug（相容）——版本號是給使用者的相容承諾。
- git tag 標記版本（用 annotated `-a`，要單獨 push）；changelog 是給**使用者**讀的「變了什麼」（分類、突出 breaking + 遷移指引，不是 commit dump）。
- GitHub Releases 包裝 tag+notes+附件；`--generate-notes` 從 PR 自動生成。
- Conventional Commits（Ch 27）→ semantic-release / release-please 全自動發布（算版本+changelog+tag+release）——規範自動化的終極回報。

## 自我檢核

- [ ] semver 的三個數字各代表什麼？使用者看到 major/minor/patch 升級各該有什麼預期？
- [ ] changelog 是寫給誰的？什麼內容最重要（提示：breaking）？
- [ ] annotated tag 和 lightweight tag 差在哪？tag 怎麼讓別人看到？
- [ ] Conventional Commits 怎麼讓發布自動化？commit type 怎麼對應版本跳法？
- [ ] 為什麼 0.x 階段不用嚴格 semver？

## 延伸閱讀

### 規範

- **[Semantic Versioning](https://semver.org/)**
  - **讀哪裡**:規範本身（不長）+ FAQ。
  - **和本章的關聯**:semver 的權威定義。

- **[Keep a Changelog](https://keepachangelog.com/)**
  - **讀哪裡**:整篇 + 範例。
  - **和本章的關聯**:changelog 格式與原則的權威。

### 工具

- **[release-please](https://github.com/googleapis/release-please)** 與 **[semantic-release](https://semantic-release.gitbook.io/)**
  - **讀哪裡**:各自的 how-it-works。
  - **和本章的關聯**:自動發布的兩個主流工具，配 Conventional Commits（Ch 27）。

- **[GitHub Docs: Managing releases / Automatically generated release notes](https://docs.github.com/en/repositories/releasing-projects-on-github)**
  - **讀哪裡**:建 release、auto-generate notes。
  - **和本章的關聯**:GitHub Releases 的操作權威。

release 是交付給使用者，但使用者要能順利貢獻/使用，專案本身要有完整的「基礎設施」。下一章是維護者建立專案門面的工作——README/CONTRIBUTING/範本/badge。

→ [Ch 33 專案基礎建設](./33-project-infrastructure.md)
