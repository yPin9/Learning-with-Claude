# Debian 打包學習筆記：從 apt 使用者到維護自己的 APT infrastructure

> 給懂一點 C、想把 Debian/Ubuntu 套件打包從頭學到底的工程師。

這系列從 `dpkg`/`apt` 的底層機制出發，帶你拆解 `.deb` 檔案格式、`debian/` 目錄的每個檔案、debhelper 的 lifecycle、clean build 與品質保證工具（sbuild/lintian/autopkgtest），深入簽署、APT repository 結構與分發（reprepro/aptly/PPA），最後整合成一條從 source 到私有 repo 的完整 CI/CD 管線。讀完你能打包任意 C/Python/Go 專案、建立自己的 APT repo、看懂 Debian Policy 並有能力走 Debian mentors 流程。

## 為什麼學這個？

- **Linux 軟體分發的事實標準**：Debian/Ubuntu 系生態佔據伺服器與桌面的大半江山；不會打包，你只能 `make install` 把檔案灑進 `/usr/local`，無法乾淨升級、無法回滾、無法被依賴解析器管理
- **理解套件管理的底層設計**：dpkg 的 database 狀態機、apt 的依賴解析、版本比較演算法、maintainer script 的執行時機——這些是值得花時間理解的系統設計，理解後你 debug 「裝不起來」的問題會快十倍
- **職涯實用性**：SRE / DevOps / 平台工程 / Linux 發行版維護幾乎一定碰到內部套件分發；能維護一條 APT pipeline 是稀缺技能

## 先修知識

- **C 語言**（程度：會編譯、知道 shared library 和 `.so` 是什麼；不需要寫複雜程式）
- **Linux 基礎**（程度：會用 shell、知道檔案權限、environment variable）
- **make 基礎**（程度：看得懂簡單的 Makefile；`debian/rules` 本質是 Makefile）
- 不需要：發行版維護經驗、GPG 使用經驗、CI 經驗（課程會補足）

## 課程地圖

### Part 1 — 使用者視角：apt/dpkg 底層（Ch 0–5）
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 為什麼學 Debian 打包？](./01-why-debian-packaging.md)
- [Ch 2 dpkg：底層套件管理員](./02-dpkg-internals.md)
- [Ch 3 apt：高層依賴解析](./03-apt-resolver.md)
- [Ch 4 .deb 檔案格式解剖](./04-deb-format.md)
- [Ch 5 dpkg 的 maintainer scripts](./05-maintainer-scripts.md)
- [練習 A：手工組裝一個 .deb](./practice-a-handcraft-deb.md)

### Part 2 — Source Package 與 debian/ 目錄（Ch 6–13）
- [Ch 6 Source package 格式](./06-source-package-format.md)
- [Ch 7 debian/control：套件 metadata](./07-debian-control.md)
- [Ch 8 debian/rules：建置腳本](./08-debian-rules.md)
- [Ch 9 debian/changelog 與版本號](./09-changelog-versioning.md)
- [Ch 10 debian/copyright：授權追蹤](./10-debian-copyright.md)
- [Ch 11 Quilt patches 系統](./11-quilt-patches.md)
- [Ch 12 debhelper 深入](./12-debhelper-deep-dive.md)
- [Ch 13 Build profiles 與條件建置](./13-build-profiles.md)
- [練習 B：打包一個真實 C 專案](./practice-b-package-c-project.md)

### Part 3 — 建置工具與品質保證（Ch 14–19）
- [Ch 14 dpkg-buildpackage 全流程](./14-dpkg-buildpackage.md)
- [Ch 15 Clean build：sbuild 與 pbuilder](./15-clean-build-sbuild.md)
- [Ch 16 Lintian：靜態品質分析](./16-lintian.md)
- [Ch 17 autopkgtest：自動化測試](./17-autopkgtest.md)
- [Ch 18 Multi-arch 支援](./18-multiarch.md)
- [Ch 19 符號管理與 ABI 追蹤](./19-symbols-abi.md)
- [練習 C：零 warning 的套件建置](./practice-c-zero-warning-build.md)

### Part 4 — 簽署、Repository 與分發（Ch 20–25）
- [Ch 20 GPG 簽署機制](./20-gpg-signing.md)
- [Ch 21 APT repository 結構](./21-apt-repo-structure.md)
- [Ch 22 reprepro：靜態 repo 管理](./22-reprepro.md)
- [Ch 23 aptly：進階 repo 管理](./23-aptly.md)
- [Ch 24 Ubuntu PPA 與 Launchpad](./24-ppa-launchpad.md)
- [Ch 25 Debian archive 的運作](./25-debian-archive.md)

### Part 5 — 進階打包模式（Ch 26–30）
- [Ch 26 打包 shared library](./26-packaging-shared-library.md)
- [Ch 27 打包 Python 套件](./27-packaging-python.md)
- [Ch 28 打包 Go 程式](./28-packaging-go.md)
- [Ch 29 打包 systemd service](./29-packaging-systemd.md)
- [Ch 30 打包 kernel module（DKMS）](./30-packaging-dkms.md)
- [練習 D：含 service + library 的完整專案](./practice-d-full-project.md)

### Part 6 — CI/CD 與生產管線（Ch 31–34）
- [Ch 31 Salsa CI / GitLab CI](./31-salsa-ci.md)
- [Ch 32 GitHub Actions 打包管線](./32-github-actions.md)
- [Ch 33 Backports 與版本遷移](./33-backports-transitions.md)
- [Ch 34 Debian Policy 精讀](./34-debian-policy.md)

### Final Project
- [Final Project：私有 APT infrastructure](./final-project-apt-infrastructure.md)

## 學習方式建議

1. **讀完一章就動手**：打包是手藝，不動手讀再多也不會。每章的動手練習都要做，尤其是「故意弄壞」的部分
2. **故意把它弄壞**：把 `debian/control` 的依賴寫錯、把 changelog 版本號倒退、刪掉一個 maintainer script 的 `set -e`——看工具怎麼罵你，比讀文件更有效
3. **讀真實套件的 source**：`apt source <package>` 把任意 Debian 套件的原始碼抓下來，讀它的 `debian/` 目錄；這是最好的範例庫

## 精選資料庫

### 必讀基礎

- **[Debian Policy Manual](https://www.debian.org/doc/debian-policy/)**
  - 打包世界的「憲法」；不是讀完才開始，而是當作隨時查的仲裁；Ch 3（binary packages）、Ch 7（dependencies）、Ch 8（shared libraries）最常翻
- **[Debian Developer's Reference](https://www.debian.org/doc/manuals/developers-reference/)**
  - 流程與最佳實踐的官方指南；補 Policy 沒講的「怎麼做事」
- **[Guide for Debian Maintainers (debmake-doc)](https://www.debian.org/doc/manuals/debmake-doc/)**
  - 最新的官方打包教學（取代了老舊的 Maintainer's Guide）；從零打包的 step-by-step

### 推薦部落格 / 文章

- **[Lucas Nussbaum's blog](https://www.lucas-nussbaum.net/blog/)** — Debian 前 DPL
  - 大規模 archive 分析、QA 工具的設計思路；理解 Debian 怎麼維護五萬個套件
- **[Raphaël Hertzog's blog](https://raphaelhertzog.com/)** — dpkg 前維護者、《Debian Administrator's Handbook》作者
  - dpkg 內部機制、打包進階技巧最權威的中文外資源之一

### 工具官方文件

- **[dh(1) man page](https://manpages.debian.org/dh)** — debhelper sequencer 的核心
  - 理解 `dh $@` 到底做了什麼的最終參考
- **[sbuild wiki](https://wiki.debian.org/sbuild)**
  - clean build 環境的設定與除錯

### 讀完本課之後

- **《The Debian Administrator's Handbook》** — Hertzog & Mas（涵蓋打包之外的整個 Debian 系統管理，免費線上）
- **[Debian Mentors (mentors.debian.net)](https://mentors.debian.net/)**（真的想貢獻 Debian？這裡是上傳套件找 sponsor 的入口）
