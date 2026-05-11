# Debian 套件管理：從 apt install 到自己打包發佈

> 給完全沒碰過套件管理底層、想從用戶到打包者全通的工程師。

從「apt install 是怎麼運作的？」到「我要怎麼把自己寫的程式做成 deb 讓別人 apt install？」——這門課三層走完：用法 → 架構 → 打包 + 私有 repo。

## 為什麼學這個？

- **Linux 工程師的基礎**：不管是 DevOps、嵌入式、後端，遲早要處理套件依賴和 repo 管理
- **自架發佈流程**：公司內部工具、IoT 裝置韌體，打成 deb 比 scp 丟 binary 可靠太多
- **讀懂錯誤訊息**：`E: Unable to satisfy dependencies`、`dpkg: dependency problems` 不再是謎

## 課程地圖

### Part 1 — 使用者層
- [Ch 1 套件管理器的存在意義](./01-why-package-manager.md)
- [Ch 2 apt 基本操作](./02-apt-basics.md)
- [Ch 3 dpkg：apt 的底層工具](./03-dpkg.md)
- [Ch 4 apt vs apt-get vs aptitude](./04-apt-variants.md)
- [Ch 5 sources.list 解析](./05-sources-list.md)
- [Ch 6 update / upgrade / dist-upgrade](./06-update-upgrade.md)
- [Ch 7 版本鎖定與 pinning](./07-pinning.md)

### Part 2 — 架構理解
- [Ch 8 deb 套件格式](./08-deb-format.md)
- [Ch 9 DEBIAN/control 與 metadata](./09-control-metadata.md)
- [Ch 10 依賴關係系統](./10-dependency-system.md)
- [Ch 11 APT 快取與本地儲存](./11-apt-cache.md)
- [Ch 12 APT 依賴解決演算法](./12-dependency-solver.md)
- [Ch 13 Repository 結構](./13-repo-structure.md)
- [Ch 14 GPG 簽章與信任鏈](./14-gpg-signing.md)
- [Ch 15 dpkg 資料庫](./15-dpkg-database.md)

### Part 3 — 打包
- [Ch 16 手工建立第一個 deb](./16-manual-deb.md)
- [Ch 17 debian/ 目錄結構全覽](./17-debian-directory.md)
- [Ch 18 debhelper 與 dh 自動化](./18-debhelper.md)
- [Ch 19 control 進階](./19-control-advanced.md)
- [Ch 20 rules 檔與 dh_auto_*](./20-rules-file.md)
- [Ch 21 打包不同語言的程式](./21-multi-lang-packaging.md)
- [Ch 22 lintian 靜態分析](./22-lintian.md)
- [練習 A：修復壞掉的依賴環境](./practice-a-broken-deps.md)
- [練習 B：把自己的程式打包成 deb](./practice-b-package-your-tool.md)

### Part 4 — 私有 Repo
- [Ch 23 reprepro 架設私有 apt repo](./23-reprepro.md)
- [Ch 24 aptly：現代 repo 管理](./24-aptly.md)
- [Ch 25 CI 自動打包推送](./25-ci-packaging.md)
- [Final Project：從 git push 到 apt install 全通](./final-project-private-repo.md)

## 學習方式建議

1. **一定要有 Ubuntu 22.04+ 環境**：WSL2 或 VM 都行，本課所有命令都能跑
2. **拆開裝看**：遇到不懂的套件就 `dpkg -L` / `dpkg -c` 看裡面有什麼
3. **故意搞壞再修**：Part 2 的理解靠的是看錯誤訊息，不是背文件

## 參考資料

- [Debian Policy Manual](https://www.debian.org/doc/debian-policy/) — 打包的終極規範
- [Debian New Maintainers' Guide](https://www.debian.org/doc/manuals/maint-guide/) — 官方入門
- `man apt`, `man dpkg`, `man sources.list`, `man deb-control`
- [reprepro](https://manpages.debian.org/reprepro) / [aptly docs](https://www.aptly.info/doc/)
