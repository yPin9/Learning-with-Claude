# Final Project — 私有 APT Infrastructure

> **目標**：整合本課 70%+ 的核心概念，從一個 C 專案（library + daemon + CLI）出發，建立一條完整的生產級流水線：打包成多個 `.deb` → GPG 簽署 → 建立簽署的 aptly repo → GitHub Actions CI 自動化（build + lintian + autopkgtest + 發布）→ 使用者能 `apt install`。完成後你擁有一套可運維的私有 Debian 套件分發基礎設施。

## 專案概覽

你要建立 **`taskd`**——一個小型的任務排程系統，包含：

```
taskd 專案結構：
  libtask     → 任務排程的核心 library（C shared library）
  taskd       → systemd daemon，執行排程的任務
  taskctl     → CLI 工具，新增/查詢任務

打包成五個 .deb：
  libtask1        runtime library
  libtask-dev     開發檔（headers）
  taskd           daemon + systemd service
  taskctl         CLI client
  libtask1-dbgsym debug symbols（自動）

分發基礎設施：
  GPG 簽署的 aptly repo
  GitHub Actions CI/CD pipeline
  使用者：add repo → apt install taskd
```

這個專案是你學的所有東西的綜合——多套件拆分、library ABI、systemd service、簽署、repo、CI。

## 整合的核心概念（對照表）

| 概念 | 章節 | 在本專案的應用 |
|---|---|---|
| .deb 結構 | Ch 4 | 理解產出的套件內部 |
| maintainer scripts | Ch 5 | taskd 的 postinst（使用者/目錄/順序）|
| source package | Ch 6 | 3.0 (quilt) + orig tarball |
| control 多套件 | Ch 7 | 5 個 binary stanza |
| rules / dh | Ch 8 | dh sequencer + override |
| changelog / 版本 | Ch 9 | 正確的版本號 |
| copyright | Ch 10 | DEP-5 完整授權 |
| quilt patches | Ch 11 | 對 upstream 的修改 |
| debhelper / install | Ch 12 | 5 個 .install 分配檔案 |
| dpkg-buildpackage | Ch 14 | build 流程 |
| sbuild | Ch 15 | 乾淨建置驗證 |
| lintian | Ch 16 | 零 warning |
| autopkgtest | Ch 17 | service + library 測試 |
| Multi-Arch / symbols | Ch 18-19 | library 的 ABI 追蹤 |
| GPG 簽署 | Ch 20 | repo 簽署 |
| repo 結構 / aptly | Ch 21-23 | snapshot-based repo |
| library 打包 | Ch 26 | libtask1/-dev/-dbgsym |
| systemd service | Ch 29 | taskd.service |
| GitHub Actions | Ch 32 | CI/CD pipeline |
| Policy | Ch 34 | 全程合規 |

## 任務規格

### 階段一：打包（整合 Part 1-3, 5）

從 upstream 專案（你建立）開始，打包成 5 個 `.deb`：

- `libtask1`（runtime）、`libtask-dev`（dev，`= ${binary:Version}` 綁定）、`taskd`（daemon + service）、`taskctl`（CLI）
- `${shlibs:Depends}` 自動算 library 依賴
- `debian/libtask1.symbols` ABI 追蹤（`-c4`）
- taskd 以專屬使用者跑、有設定檔（conffile）、postinst 順序正確
- 通過 sbuild、零 lintian warning、autopkgtest 全綠

### 階段二：簽署的 aptly repo（整合 Part 4）

- 建立 GPG 簽署的 aptly repo
- 用 snapshot 模式（每次發布一個 snapshot）
- 可從 web server 提供，使用者能 `apt install`

### 階段三：GitHub Actions CI/CD（整合 Part 6）

- push 觸發：build（Debian container）→ lintian → autopkgtest
- main 分支：簽署 → aptly snapshot → 發布
- secrets 管理 GPG key（專用 CI key，不在 PR 用）
- 每個 commit 一個 aptly snapshot（可追溯/回滾）

## 驗收標準

```
階段一（打包）：
  □ 5 個 .deb 正確產出
  □ taskd Depends 自動含 libtask1（${shlibs:Depends}）
  □ libtask-dev Depends: libtask1 (= ${binary:Version})
  □ debian/libtask1.symbols 存在，build 用 -c4 通過
  □ taskd 裝完 systemd service 自動 enable+start，以 taskd 使用者跑
  □ /etc/taskd/taskd.conf 是 conffile
  □ purge 清理 log/使用者/設定
  □ sbuild -d bookworm 成功
  □ lintian -iI 零輸出
  □ autopkgtest 全綠（測 service 運行 + library 可編譯）

階段二（repo）：
  □ aptly repo 用 GPG 簽署
  □ 用 snapshot 模式發布
  □ 客戶端能 add repo + apt install taskd
  □ 信任鏈完整（Signed-By）

階段三（CI/CD）：
  □ push 觸發完整 pipeline
  □ build 在 Debian container（乾淨環境）
  □ main 分支自動簽署 + 發布到 aptly
  □ GPG key 用 secrets，不在 PR job
  □ snapshot 對應 git commit
```

## 實作藍圖

### 藍圖一：upstream 專案（你建立）

```
taskd-1.0/
├── Makefile               (設 SONAME，支援 DESTDIR/PREFIX/LIBDIR)
├── include/task.h         (library public API)
├── lib/task.c             (排程核心 library)
├── daemon/taskd.c         (daemon，用 libtask)
└── client/taskctl.c       (CLI，用 libtask)
```

library API（`task.h`）至少：`task_add()`、`task_list()`、`task_run_due()`。daemon 週期性呼叫 `task_run_due()`，client 呼叫 `task_add()`/`task_list()`。

### 藍圖二：debian/ 目錄（整合所有打包知識）

```
debian/
├── control              (5 個 stanza，正確的依賴)
├── rules                (dh + override_dh_auto_install 傳 LIBDIR + dh_makeshlibs -c4)
├── changelog            (1.0-1)
├── copyright            (DEP-5)
├── source/format        (3.0 (quilt))
├── libtask1.install     (usr/lib/*/libtask.so.*)
├── libtask-dev.install  (headers + libtask.so)
├── taskd.install        (usr/bin/taskd + conf)
├── taskctl.install      (usr/bin/taskctl)
├── libtask1.symbols     (ABI 追蹤)
├── taskd.service        (systemd unit)
├── taskd.conf           (conffile → /etc/taskd/)
├── taskd.postinst       (使用者+目錄，在 #DEBHELPER# 之前)
├── taskd.postrm         (purge 清理)
├── patches/             (quilt patches，如果改了 upstream)
└── tests/
    ├── control          (service-runs + lib-usable)
    ├── service-runs
    └── lib-usable
```

### 藍圖三：aptly 發布腳本

```bash
#!/bin/sh
# publish.sh — 發布到簽署的 aptly repo
set -e
REPO=taskd-repo
KEY=YOUR_GPG_KEY_ID
SNAP="snap-$(date +%Y%m%d-%H%M%S)"   # 或用 git sha

aptly repo create -distribution=bookworm -component=main "$REPO" 2>/dev/null || true
aptly repo add "$REPO" ../*.deb
aptly snapshot create "$SNAP" from repo "$REPO"
# 首次 publish，後續用 switch
if aptly publish list | grep -q bookworm; then
    aptly publish switch -gpg-key="$KEY" -batch bookworm "$SNAP"
else
    aptly publish snapshot -gpg-key="$KEY" -batch "$SNAP"
fi
# 同步 ~/.aptly/public/ 到你的 web server
```

### 藍圖四：GitHub Actions（整合 Ch 32）

```yaml
name: taskd CI/CD
on:
  push: { branches: [main] }
  pull_request:

jobs:
  build:
    runs-on: ubuntu-latest
    container: { image: debian:bookworm }
    steps:
      - uses: actions/checkout@v4
      - name: deps
        run: |
          apt-get update
          apt-get install -y build-essential devscripts debhelper lintian \
                             autopkgtest equivs
          mk-build-deps -ir -t "apt-get -y" debian/control
      - name: build
        run: dpkg-buildpackage -us -uc -b
      - name: lintian
        run: lintian -iI ../*.changes
      - name: autopkgtest
        run: autopkgtest ../*.changes -- null || true
      - uses: actions/upload-artifact@v4
        with: { name: debs, path: "../*.deb" }

  publish:
    needs: build
    if: github.ref == 'refs/heads/main'    # 不在 PR
    runs-on: ubuntu-latest
    container: { image: debian:bookworm }
    steps:
      - uses: actions/download-artifact@v4
        with: { name: debs }
      - name: deps
        run: apt-get update && apt-get install -y aptly gnupg
      - name: import key
        env: { KEY: "${{ secrets.GPG_PRIVATE_KEY }}" }
        run: echo "$KEY" | gpg --batch --import
      - name: publish
        run: |
          aptly repo create -distribution=bookworm -component=main taskd-repo || true
          aptly repo add taskd-repo *.deb
          aptly snapshot create snap-${{ github.sha }} from repo taskd-repo
          aptly publish snapshot -gpg-key=YOUR_KEY -batch snap-${{ github.sha }}
          # rsync/aws s3 sync ~/.aptly/public/ to your server
```

## 完整參考實作

這個 Final Project 沒有單一「正確答案」——它是整合，重點是每個決策有依據。參考實作的關鍵片段已分散在各章和練習 D：

<details>
<summary>關鍵整合點提示</summary>

- **library + service 結構**：直接擴展練習 D（greetd）的模式——練習 D 已經做了 library + daemon + client + service，taskd 是它的進階版（library 有更豐富的 API）

- **symbols 維護**：library API 多個函式（task_add/list/run_due），symbols 檔記錄每個。改 API 時 `-c4` 強制更新（Ch 19）

- **postinst 順序**：taskd 使用者建立 + log 目錄，在 `#DEBHELPER#`（service start）之前（Ch 5/29，練習 D 已踩過）

- **aptly snapshot 對應 commit**：`snap-${github.sha}`——repo 狀態可追溯到 git commit，能回滾（Ch 23）

- **CI 安全**：publish job 用 `if: github.ref == 'refs/heads/main'`，secrets 不在 PR（Ch 32）

- **全程合規**：每一步對照 Policy（Ch 34）——FHS 路徑、copyright、依賴、conffile、SONAME

完整實作可以從練習 D 的解答出發，加上：豐富的 library API、完整的 aptly publish 腳本、GitHub Actions YAML。所有零件你都在前面章節練過。

</details>

## 自我評估 Checklist

完成後，用這個檢驗你的實作品質：

**打包品質**
- [ ] 5 個套件的依賴關係完全正確（用 `dpkg-deb -f` 驗證每個 Depends）
- [ ] library ABI 追蹤完整（symbols 檔 + `-c4`）
- [ ] service 生命週期正確（enable/start/stop/restart/purge）
- [ ] sbuild 乾淨建置通過（證明 Build-Depends 完整）
- [ ] 零 lintian warning（`-iI`）
- [ ] autopkgtest 涵蓋 service 運行 + library 可編譯

**基礎設施品質**
- [ ] repo 正確簽署，信任鏈完整（客戶端 apt update 無 GPG 警告）
- [ ] aptly snapshot 模式（能回滾）
- [ ] CI pipeline 在乾淨環境 build（抓得出漏宣告依賴）
- [ ] CI 安全（secrets 不洩漏，專用 key）
- [ ] 每個 commit 對應一個可追溯的 repo snapshot

**理解深度**（能向別人解釋）
- [ ] 能解釋 `${shlibs:Depends}` 如何讓 taskd 自動依賴 libtask1（SONAME→shlibs→shlibdeps）
- [ ] 能解釋為什麼 postinst 使用者建立在 `#DEBHELPER#` 之前
- [ ] 能解釋 aptly snapshot 如何實現回滾
- [ ] 能解釋整條 CI/CD 如何把「push」變成「使用者可 apt install」

## 延伸挑戰

- **挑戰一**：多架構——CI 用 matrix 為 amd64 + arm64 build，repo 同時提供兩個架構

- **挑戰二**：library transition 演練——把 libtask 的 SONAME 從 1 升到 2（ABI 破壞），改名 libtask2，重 build taskd/taskctl，用 aptly snapshot 建立一致狀態（Ch 26/33）

- **挑戰三**：autopkgtest 在 CI 用真正的隔離（podman backend）而非 null，更貼近 Salsa CI

- **挑戰四**：加 piuparts（Ch 31）到 CI——測試安裝/升級/移除的乾淨性

- **挑戰五**：把 repo 托管在 GitHub Pages，CI 自動推送 `~/.aptly/public/` 到 gh-pages 分支，做到完全免費的公開 apt repo

## 結語

你從「`apt install` 背後發生什麼」開始，現在能：

- 從零打包 C/library/service 專案成符合 Policy 的多個 `.deb`
- 用 sbuild/lintian/autopkgtest 保證生產品質
- 追蹤 library ABI、處理 transition
- 建立簽署的 APT repository
- 用 CI/CD 自動化整條流水線

這套技能讓你能維護任何規模的 Debian 套件分發——從個人專案到企業內部 repo。如果你想更進一步，[mentors.debian.net](https://mentors.debian.net/)（Ch 25）是貢獻到 Debian 官方 archive 的入口，你現在有能力走那條路了。

去打包點真實的東西吧。
