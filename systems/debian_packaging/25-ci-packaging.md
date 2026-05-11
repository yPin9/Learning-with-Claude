# Ch 25 — CI 自動打包推送（GitHub Actions）

> 目標：把 debhelper 打包流程放進 GitHub Actions，每次 git tag 自動產生 .deb 並推送到私有 apt repo，客戶端 `apt upgrade` 就能拿到新版。

## 整體架構

```
git tag v2.0
    ↓
GitHub Actions 觸發
    ↓
Docker 容器（ubuntu:22.04）
  1. 安裝 build 依賴
  2. dpkg-buildpackage 產生 .deb
  3. lintian 檢查
    ↓
上傳 .deb 到 artifact / release
    ↓
reprepro / aptly API 加入 repo
    ↓
客戶端 apt update + apt upgrade
```

## GitHub Actions Workflow

### .github/workflows/build-deb.yml

```yaml
name: Build Debian Package

on:
  push:
    tags:
      - 'v*'          # 只有 tag（如 v1.0, v2.0-beta）才觸發

jobs:
  build:
    runs-on: ubuntu-22.04   # 用 jammy 環境 build，確保依賴一致

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Install build dependencies
        run: |
          sudo apt-get update
          sudo apt-get install -y \
            debhelper \
            devscripts \
            lintian \
            build-essential

      - name: Extract version from tag
        id: version
        run: |
          # tag 是 v1.0，去掉 v 得到 1.0
          VERSION="${GITHUB_REF_NAME#v}"
          echo "version=$VERSION" >> $GITHUB_OUTPUT
          echo "deb_name=sysinfo_${VERSION}-1_amd64.deb" >> $GITHUB_OUTPUT

      - name: Update changelog version
        run: |
          VERSION="${{ steps.version.outputs.version }}"
          # 用 dch 更新 changelog（-v = 新版本，-D = 發行版）
          dch \
            --newversion "${VERSION}-1" \
            --distribution unstable \
            --force-distribution \
            "Release ${VERSION}"

      - name: Build package
        run: |
          dpkg-buildpackage -us -uc -b
          # 產生的 .deb 在上一層目錄
          ls ../*.deb

      - name: Run lintian
        run: |
          lintian -EW ../${{ steps.version.outputs.deb_name }}
          # 如果有 Error 就 exit 1，整個 job 失敗

      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: debian-package
          path: ../${{ steps.version.outputs.deb_name }}

      - name: Create GitHub Release
        uses: softprops/action-gh-release@v2
        with:
          files: ../${{ steps.version.outputs.deb_name }}
          generate_release_notes: true
```

## 推送到私有 repo

### 用 reprepro（SSH 推送方式）

```yaml
  deploy:
    needs: build
    runs-on: ubuntu-22.04

    steps:
      - name: Download artifact
        uses: actions/download-artifact@v4
        with:
          name: debian-package

      - name: Deploy to apt repo (SSH)
        env:
          REPO_HOST: ${{ secrets.REPO_HOST }}        # repo.myorg.com
          REPO_USER: ${{ secrets.REPO_USER }}        # deploy
          REPO_SSH_KEY: ${{ secrets.REPO_SSH_KEY }}  # SSH private key
          REPO_DIR: ${{ secrets.REPO_DIR }}          # /home/deploy/myrepo
        run: |
          # 設定 SSH key
          mkdir -p ~/.ssh
          echo "$REPO_SSH_KEY" > ~/.ssh/deploy_key
          chmod 600 ~/.ssh/deploy_key
          ssh-keyscan -H "$REPO_HOST" >> ~/.ssh/known_hosts

          # 找到 deb 檔名
          DEB_FILE=$(ls *.deb | head -1)
          PKG_NAME=$(dpkg-deb -f "$DEB_FILE" Package)

          # 複製到 repo 機器
          scp -i ~/.ssh/deploy_key \
            "$DEB_FILE" \
            "$REPO_USER@$REPO_HOST:/tmp/$DEB_FILE"

          # 遠端執行 reprepro
          ssh -i ~/.ssh/deploy_key \
            "$REPO_USER@$REPO_HOST" \
            "cd $REPO_DIR && \
             reprepro remove jammy $PKG_NAME; \
             reprepro includedeb jammy /tmp/$DEB_FILE && \
             rm /tmp/$DEB_FILE"
```

### 用 aptly API（HTTP 推送方式）

```yaml
      - name: Deploy to apt repo (aptly API)
        env:
          APTLY_API_URL: ${{ secrets.APTLY_API_URL }}   # http://repo.internal:8080
          APTLY_TOKEN: ${{ secrets.APTLY_TOKEN }}        # Bearer token（如果有設）
          REPO_NAME: myorg-internal
          CODENAME: jammy
        run: |
          DEB_FILE=$(ls *.deb | head -1)
          UPLOAD_DIR="${DEB_FILE%.deb}"

          # 1. 上傳 .deb 到 aptly
          curl -f -X POST \
            -F "file=@${DEB_FILE}" \
            "${APTLY_API_URL}/api/files/${UPLOAD_DIR}"

          # 2. 把上傳的 .deb 加入 repo
          curl -f -X POST \
            "${APTLY_API_URL}/api/repos/${REPO_NAME}/file/${UPLOAD_DIR}"

          # 3. 建立新 snapshot
          SNAP_NAME="myorg-$(date +%Y%m%d-%H%M%S)"
          curl -f -X POST \
            -H "Content-Type: application/json" \
            -d "{\"Name\":\"${SNAP_NAME}\"}" \
            "${APTLY_API_URL}/api/repos/${REPO_NAME}/snapshots"

          # 4. 切換發布到新 snapshot
          curl -f -X PUT \
            -H "Content-Type: application/json" \
            -d "{\"Snapshots\":[{\"Component\":\"main\",\"Name\":\"${SNAP_NAME}\"}],\"Signing\":{\"Skip\":false}}" \
            "${APTLY_API_URL}/api/publish/:./jammy"
```

## 完整 workflow 範例（build + deploy 一體）

```yaml
name: Build and Deploy Debian Package

on:
  push:
    tags: ['v*']

env:
  CODENAME: jammy
  PKG_NAME: sysinfo

jobs:
  build-and-deploy:
    runs-on: ubuntu-22.04
    permissions:
      contents: write    # 需要建立 GitHub Release

    steps:
      - uses: actions/checkout@v4

      - run: sudo apt-get update && sudo apt-get install -y debhelper devscripts lintian build-essential

      - name: Set version
        run: echo "VERSION=${GITHUB_REF_NAME#v}" >> $GITHUB_ENV

      - name: Update changelog
        run: dch --newversion "${VERSION}-1" --distribution unstable --force-distribution "Release ${VERSION}"

      - name: Build
        run: |
          dpkg-buildpackage -us -uc -b
          echo "DEB_FILE=$(ls ../*.deb | head -1)" >> $GITHUB_ENV

      - name: Lint
        run: lintian -EW $DEB_FILE

      - name: Release + Deploy
        env:
          GH_TOKEN: ${{ github.token }}
          REPO_SSH_KEY: ${{ secrets.REPO_SSH_KEY }}
          REPO_HOST: ${{ secrets.REPO_HOST }}
          REPO_USER: ${{ secrets.REPO_USER }}
          REPO_DIR: ${{ secrets.REPO_DIR }}
        run: |
          # GitHub Release
          gh release create "${GITHUB_REF_NAME}" \
            "$DEB_FILE" \
            --title "Release ${VERSION}" \
            --generate-notes

          # SSH 推到 repo
          mkdir -p ~/.ssh
          echo "$REPO_SSH_KEY" > ~/.ssh/id | chmod 600 ~/.ssh/id
          ssh-keyscan -H "$REPO_HOST" >> ~/.ssh/known_hosts
          scp -i ~/.ssh/id "$DEB_FILE" "$REPO_USER@$REPO_HOST:/tmp/"
          DEB_BASE=$(basename $DEB_FILE)
          ssh -i ~/.ssh/id "$REPO_USER@$REPO_HOST" \
            "cd $REPO_DIR && \
             reprepro remove $CODENAME $PKG_NAME 2>/dev/null || true && \
             reprepro includedeb $CODENAME /tmp/$DEB_BASE && \
             rm /tmp/$DEB_BASE"
```

## 在 Secrets 設定什麼

| Secret | 說明 |
|--------|------|
| `REPO_HOST` | repo 機器的 hostname / IP |
| `REPO_USER` | SSH 使用者 |
| `REPO_SSH_KEY` | SSH private key（對應的 pubkey 放在 repo 機器的 authorized_keys） |
| `REPO_DIR` | reprepro 的根目錄路徑 |

## 客戶端的更新體驗

```bash
# 客戶端只需要做一次設定（見 Ch 23）
# 之後每次推新 tag：
sudo apt update
apt-cache policy sysinfo   # 看到新版本
sudo apt upgrade sysinfo   # 安裝新版
```

## 最佳實踐

```yaml
# 1. 在 CI 裡用乾淨的 Docker 環境，避免本機污染
#    → 用 ubuntu:22.04 image，不用 runner 的系統套件

# 2. lintian 失敗要讓 job 失敗
#    → 不要 continue-on-error: true 給 lintian 步驟

# 3. secrets 不要寫死在 workflow 裡
#    → 全部放 GitHub Secrets

# 4. 版本號從 git tag 來，不要在 debian/changelog 手改
#    → dch --newversion 自動更新

# 5. 用 artifact 保留 .deb 以便後續調查
#    → actions/upload-artifact 存 30 天
```

## 自我檢核

- [ ] GitHub Actions 用 `push: tags: ['v*']` 觸發打包
- [ ] `dch --newversion` 在 CI 裡動態更新 changelog 版本
- [ ] lintian 失敗 → job 失敗 → 不推到 repo（把關）
- [ ] SSH 方式：scp 傳 .deb → ssh 遠端執行 reprepro
- [ ] aptly API 方式：curl 上傳 + 建 snapshot + publish switch
- [ ] 客戶端一次設定後，每次 `apt upgrade` 就能拿到新版

→ [Final Project：從 git push 到 apt install 全通](./final-project-private-repo.md)
