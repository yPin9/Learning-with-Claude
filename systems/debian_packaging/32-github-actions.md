# Ch 32 — GitHub Actions 打包管線

> **目標**：在 GitHub（非 Debian 生態）建一條完整的打包 CI/CD pipeline——用容器跑 build、lintian、autopkgtest，簽署，發布到自建 repo（aptly）。這是 Final Project 的技術基礎。

> **環境**：GitHub Actions、Debian container、aptly（Ch 23）。本章假設你會基本 GitHub Actions（workflow YAML）。

## 為什麼自建 GitHub Actions pipeline？

Salsa CI（Ch 31）很完整，但它綁 Debian/Salsa 生態。很多情況你需要在 GitHub 自建：

- 你的專案在 GitHub，不在 Salsa
- 私有/企業套件，要發布到**自己的** repo（不是 Debian archive）
- 要完全掌控 pipeline 的每一步（簽署用自己的 key、發布到自己的 aptly）

這章建一條「push → build → 檢查 → 簽署 → 發布到 aptly」的完整 pipeline。這也是 Final Project 要做的，本章是技術鋪墊。

## 先建立直覺：在 container 裡複製 Debian build 環境

```
GitHub Actions runner（Ubuntu，但我們用 Debian container）：

  ┌────────────────────────────────────────────┐
  │  Debian container（debian:bookworm）         │
  │                                             │
  │  1. 裝 build 工具 + Build-Depends            │
  │  2. dpkg-buildpackage（build .deb）          │
  │  3. lintian（品質檢查）                       │
  │  4. autopkgtest（功能測試）                   │
  │  5. debsign（用 CI 的 GPG key 簽署）          │
  │  6. aptly 發布到 repo（或上傳）               │
  └────────────────────────────────────────────┘
        │
  artifacts（.deb）/ 發布到你的 apt repo
```

關鍵：在 **Debian container** 裡跑（不是 runner 預設的 Ubuntu），確保 build 環境和目標一致。container 提供乾淨、可重現的環境（類似 sbuild 的精神）。

## 基本 pipeline：build + lintian

`.github/workflows/package.yml`：

```yaml
name: Debian Package CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  build:
    runs-on: ubuntu-latest
    container:
      image: debian:bookworm        # 在 Debian container 裡跑
    steps:
      - uses: actions/checkout@v4

      - name: Install build tools
        run: |
          apt-get update
          apt-get install -y build-essential devscripts debhelper lintian

      - name: Install build dependencies
        run: |
          # 用 mk-build-deps 裝齊 Build-Depends（Ch 14）
          apt-get install -y equivs
          mk-build-deps -ir -t "apt-get -y --no-install-recommends" debian/control

      - name: Build package
        run: dpkg-buildpackage -us -uc -b

      - name: Run lintian
        run: lintian ../*.changes || lintian ../*.deb

      - name: Upload artifacts
        uses: actions/upload-artifact@v4
        with:
          name: debs
          path: ../*.deb
```

這條 pipeline：每次 push 在 Debian container 裡裝依賴、build、跑 lintian、上傳 `.deb` 作為 artifact。

## 加入 autopkgtest

autopkgtest（Ch 17）需要隔離環境。在 CI container 裡用 null backend（簡單）或 lxc（隔離較好）：

```yaml
      - name: Install autopkgtest
        run: apt-get install -y autopkgtest

      - name: Run autopkgtest
        run: |
          # 在 container 裡用 null backend（container 本身已是隔離）
          autopkgtest ../*.changes -- null || true
          # 生產環境建議用 lxc/podman backend 做更好的隔離
```

> 在 CI container 裡跑 autopkgtest 的隔離考量：container 本身已隔離 runner，container 內用 `null` backend 會「污染這個 container」——但 container 用完即丟，所以可接受。要更乾淨用 podman/lxc backend（需要 container 內套 container，設定較複雜）。

## 簽署：用 CI 的 GPG key

簽署需要 private key，但不能明文放進 repo。用 GitHub Secrets：

```yaml
      - name: Import GPG key
        env:
          GPG_PRIVATE_KEY: ${{ secrets.GPG_PRIVATE_KEY }}
          GPG_PASSPHRASE: ${{ secrets.GPG_PASSPHRASE }}
        run: |
          echo "$GPG_PRIVATE_KEY" | gpg --batch --import
          # 設定 non-interactive 簽署
          echo "use-agent" >> ~/.gnupg/gpg.conf
          echo "pinentry-mode loopback" >> ~/.gnupg/gpg.conf

      - name: Sign package
        env:
          GPG_PASSPHRASE: ${{ secrets.GPG_PASSPHRASE }}
        run: |
          debsign -p "gpg --batch --passphrase $GPG_PASSPHRASE --pinentry-mode loopback" \
                  ../*.changes
```

> **CI 簽署的安全原則**：
> - private key 和 passphrase 放 GitHub Secrets（加密儲存），絕不明文進 repo
> - 用**專用的 CI 簽署 key**（不是你的個人主 key）——權限隔離，CI key 洩漏不影響你的主身份
> - 考慮用 subkey（主 key 離線，CI 只有簽署 subkey）

## 發布到 aptly

build + 檢查 + 簽署後，發布到 aptly repo（Ch 23）：

```yaml
  publish:
    needs: build
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'    # 只有 main 分支才發布
    container:
      image: debian:bookworm
    steps:
      - name: Download artifacts
        uses: actions/download-artifact@v4
        with:
          name: debs

      - name: Install aptly
        run: |
          apt-get update && apt-get install -y aptly gnupg

      - name: Import GPG key
        env:
          GPG_PRIVATE_KEY: ${{ secrets.GPG_PRIVATE_KEY }}
        run: echo "$GPG_PRIVATE_KEY" | gpg --batch --import

      - name: Publish to aptly
        run: |
          # 用 Ch 23 的 aptly 三層流程
          aptly repo create -distribution=bookworm -component=main myrepo || true
          aptly repo add myrepo *.deb
          aptly snapshot create snap-${{ github.sha }} from repo myrepo
          aptly publish snapshot -gpg-key=YOUR_KEY_ID -batch snap-${{ github.sha }}
          # 把 aptly 的 public/ 同步到你的 server / S3 / GitHub Pages
```

注意這裡用了 Ch 23 的 aptly snapshot 模式——每個 commit 一個 snapshot（`snap-${{ github.sha }}`），讓 repo 狀態對應 git commit，可追溯可回滾。

## 完整的 CI/CD 流程圖

```
git push to main
      │
  ┌───▼──── build job（Debian container）────┐
  │  install build-deps → dpkg-buildpackage  │
  │  → lintian → autopkgtest                 │
  │  → upload .deb artifacts                 │
  └───┬──────────────────────────────────────┘
      │ （build job 通過）
  ┌───▼──── publish job（只在 main）─────────┐
  │  download artifacts → import GPG key     │
  │  → aptly repo add → snapshot create      │
  │  → aptly publish snapshot                │
  │  → sync to web server / S3               │
  └───┬──────────────────────────────────────┘
      │
  使用者：apt update → apt install（從你的 repo）
```

## 矩陣 build：多個 Debian/Ubuntu 版本

用 matrix 為多個目標版本 build：

```yaml
jobs:
  build:
    strategy:
      matrix:
        release: [bookworm, trixie, jammy, noble]
    container:
      image: debian:${{ matrix.release }}    # 或 ubuntu:${{ matrix.release }}
    steps:
      # ... 每個 release 各 build 一次
```

這讓你的套件同時支援多個發行版版本（呼應 Ch 24 的 PPA 多 release 上傳，但這裡是自建）。

## 故意弄壞：簽署 key 在 PR 觸發時洩漏

```yaml
# 危險：在 pull_request 觸發時也跑簽署
on:
  pull_request:    # 外部 PR 也會觸發！

jobs:
  sign:
    steps:
      - name: Sign
        env:
          KEY: ${{ secrets.GPG_PRIVATE_KEY }}    # 危險！
        run: ...
```

> **安全雷**：GitHub Secrets 在 fork 的 PR 中**預設不可用**（GitHub 的保護），但設定不當（如 `pull_request_target`）可能洩漏。原則：
> - 簽署/發布只在 `push` 到受信任分支（main）時跑，**不**在 PR
> - PR 只跑 build + 檢查（不碰 secrets）
> - 用 `if: github.ref == 'refs/heads/main'` 限定發布 job
> - 絕不在能被外部 PR 觸發的 job 用 secrets

## 踩雷集錦

1. **在 Ubuntu runner build 而非 Debian container**：runner 預設 Ubuntu，環境和你的目標可能不同。用 `container: debian:bookworm` 確保一致

2. **secrets 在 PR job 暴露**：簽署/發布只在 push 到 main，不在 PR。用 `if` 限定，PR 只做不碰 secrets 的檢查

3. **GPG non-interactive 沒設好**：CI 沒有互動終端，GPG 簽署要 `--batch --pinentry-mode loopback` + passphrase from secret，否則卡在密碼提示

4. **用個人主 key 簽 CI**：CI 環境風險高，用專用 CI key 或 subkey，隔離風險

5. **artifact 在 jobs 間沒傳遞**：build job 的 `.deb` 要 `upload-artifact`，publish job 才能 `download-artifact`。container job 之間檔案不自動共享

6. **aptly publish 沒 `-batch`**：CI 非互動，aptly 簽署要 `-batch` + GPG 設好 loopback，否則卡住

## 進階：自託管 runner 與 sbuild 整合

CI 在 container 裡 build 已經不錯，但要更接近「真正的 sbuild 乾淨環境」（Ch 15），可以：

**自託管 runner + sbuild**：在你控制的機器跑 self-hosted runner，用真正的 sbuild：

```yaml
jobs:
  build:
    runs-on: self-hosted    # 你的機器，已設好 sbuild chroot
    steps:
      - name: sbuild
        run: |
          dpkg-buildpackage -S -us -uc
          sbuild -d bookworm ../*.dsc    # 真正的 sbuild 乾淨建置（Ch 15）
```

這結合了 CI 的自動化和 sbuild 的嚴格隔離。但要維護自託管 runner 和 chroot。

**container 裡的 sbuild（unshare 模式）**：Ch 15 提過 sbuild 的 `--chroot-mode=unshare`，能在 container 裡跑（不需要 schroot 的特權）：

```yaml
      - name: sbuild in container
        run: |
          apt-get install -y sbuild mmdebstrap uidmap
          mmdebstrap bookworm /tmp/bookworm.tar.zst
          sbuild --chroot-mode=unshare --chroot=/tmp/bookworm.tar.zst \
                 -d bookworm ../*.dsc
```

這讓 GitHub-hosted runner 也能跑接近 sbuild 的乾淨建置，不需要自託管。是 CI + clean build 的現代解。

## 動手練習

1. 建一個 GitHub repo 放練習 B 的 greet 專案，寫 `.github/workflows/package.yml` 做 build + lintian，push 看 Actions 跑

2. 加 GPG 簽署：生成測試 key，把 private key 放 GitHub Secrets，pipeline 加簽署步驟（記得只在 push 不在 PR）

3. 加 publish job：用 aptly 發布到 repo，把 `public/` 推到 GitHub Pages（GitHub Pages 能當 apt repo 的 static host）

4. 試 matrix build：為 bookworm + trixie 各 build 一次，看兩個 container 並行跑

## 本章重點整理

- 在 Debian container 裡跑 CI（不是 runner 預設 Ubuntu），確保環境一致
- pipeline：install build-deps（mk-build-deps）→ build → lintian → autopkgtest → 簽署 → aptly 發布
- 簽署用 GitHub Secrets 存 private key + passphrase，用專用 CI key，`--batch --pinentry-mode loopback`
- 安全原則：簽署/發布只在 push 到 main（不在 PR），避免 secrets 洩漏
- aptly snapshot 用 `snap-${github.sha}` 對應 commit（可追溯/回滾，Ch 23）；matrix build 支援多 release
- 進階：自託管 runner + sbuild，或 container 裡 sbuild unshare 模式（接近真正乾淨建置）

## 自我檢核

- [ ] 能寫一個基本的 GitHub Actions pipeline（Debian container + build + lintian）
- [ ] 知道為什麼用 Debian container 而非 runner 預設 Ubuntu
- [ ] 能說出 CI 簽署的安全原則（專用 key、secrets、不在 PR）
- [ ] 知道如何用 aptly snapshot 讓 repo 狀態對應 git commit
- [ ] 知道如何在 CI 裡跑接近 sbuild 的乾淨建置（unshare 模式）

## 延伸閱讀

### 官方文件

- **[GitHub Actions documentation](https://docs.github.com/en/actions)**
  - **讀哪裡**：container jobs、secrets、artifacts、matrix
  - **學什麼**：本章用到的 Actions 機制的完整文件
  - **前提**：讀完本章

- **[GitHub Pages as apt repo](https://wiki.debian.org/DebianRepository/SetupWithReprepro)** 或相關靜態托管說明
  - **讀哪裡**：靜態 host apt repo 的方法
  - **學什麼**：把 aptly/reprepro 的輸出托管在 GitHub Pages/S3
  - **前提**：Ch 21（repo 是靜態檔案）

### 部落格 / 文章

- **[Building Debian packages with GitHub Actions](https://github.com/jtdor/build-deb-action)** 或類似的 action
  - **這篇說什麼**：現成的 GitHub Action 封裝了 Debian build（可參考或直接用）
  - **讀哪裡**：action 的實作和用法
  - **為什麼值得讀**：看別人怎麼封裝這個流程，可借鏡或站在它肩上

→ [Ch 33 Backports 與版本遷移](./33-backports-transitions.md)
