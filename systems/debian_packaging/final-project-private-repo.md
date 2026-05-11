# Final Project — 從 git push 到 apt install 全通

> 目標：整合全課程所有知識，建立一條完整的 pipeline：本地開發 → git tag → GitHub Actions 自動打包 → 推送到私有 apt repo → 任意機器 `apt install` 安裝。

## 專案說明

你要完成這整條管線，最終狀態：

```
開發者 git tag v1.0 → push → GitHub Actions
                                    ↓
                            build .deb
                            lintian 驗證
                                    ↓
                            推送到私有 repo 機器
                                    ↓
使用機器：sudo apt upgrade → 拿到新版
```

使用的工具和技術（全部來自本課程）：
- **Ch 16–18**：手工打包基礎、debhelper
- **Ch 19–21**：control 進階、rules、多語言打包
- **Ch 22**：lintian 品質把關
- **Ch 23**：reprepro 管理 repo
- **Ch 25**：GitHub Actions CI

## Phase 1：準備源碼套件

### 要打包的程式：`netcheck`

一個用 C 寫的網路狀態檢查工具：

```c
/* netcheck.c */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <netdb.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <errno.h>

#define DEFAULT_HOST "8.8.8.8"
#define DEFAULT_PORT "53"
#define TIMEOUT_SEC  3

static int check_tcp(const char *host, const char *port) {
    struct addrinfo hints = {0}, *res;
    hints.ai_family   = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;

    if (getaddrinfo(host, port, &hints, &res) != 0)
        return -1;

    int fd = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (fd < 0) { freeaddrinfo(res); return -1; }

    /* 設定 connect timeout */
    struct timeval tv = { TIMEOUT_SEC, 0 };
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));

    int ret = connect(fd, res->ai_addr, res->ai_addrlen);
    close(fd);
    freeaddrinfo(res);
    return (ret == 0) ? 0 : -1;
}

int main(int argc, char *argv[]) {
    const char *host = DEFAULT_HOST;
    const char *port = DEFAULT_PORT;
    int verbose = 0;

    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "-v"))        { verbose = 1; }
        else if (!strcmp(argv[i], "-h"))   { host = argv[++i]; }
        else if (!strcmp(argv[i], "-p"))   { port = argv[++i]; }
        else if (!strcmp(argv[i], "--help")) {
            printf("Usage: netcheck [-h host] [-p port] [-v]\n");
            printf("  -h HOST  Target host (default: %s)\n", DEFAULT_HOST);
            printf("  -p PORT  Target port (default: %s)\n", DEFAULT_PORT);
            printf("  -v       Verbose output\n");
            return 0;
        }
    }

    if (verbose) printf("Checking %s:%s ...\n", host, port);

    if (check_tcp(host, port) == 0) {
        if (verbose) printf("OK: %s:%s reachable\n", host, port);
        else         printf("OK\n");
        return 0;
    } else {
        if (verbose) printf("FAIL: %s:%s unreachable (%s)\n",
                            host, port, strerror(errno));
        else         printf("FAIL\n");
        return 1;
    }
}
```

```makefile
# Makefile
CC      ?= gcc
CFLAGS  ?= -O2 -Wall -Wextra
PREFIX  ?= /usr
BINDIR  := $(PREFIX)/bin

netcheck: netcheck.c
	$(CC) $(CFLAGS) -o $@ $<

install: netcheck
	install -D -m 755 netcheck $(DESTDIR)$(BINDIR)/netcheck

clean:
	rm -f netcheck

.PHONY: install clean
```

### 任務

1. 建立 `netcheck-1.0/` 目錄，放入 `netcheck.c` 和 `Makefile`
2. 建立完整的 `debian/` 目錄（control、rules、changelog、copyright、source/format）
3. 寫 man page `netcheck.1`
4. 確認 `lintian -EW` 零 Error
5. 提交到 GitHub repo（如果沒有 GitHub 就 skip 這步，用本地 git tag）

## Phase 2：架設 reprepro repo

選擇一台 Linux 機器當 repo server（可以就是你的開發機）：

```bash
# 在 repo server 上執行
sudo apt install reprepro nginx gnupg

# 生成簽章 key
gpg --full-generate-key
KEYID=$(gpg --list-secret-keys --keyid-format LONG \
  | grep "^sec" | awk '{print $2}' | cut -d/ -f2)

# 建立 repo 目錄
REPODIR="$HOME/netcheck-repo"
mkdir -p $REPODIR/conf

cat > $REPODIR/conf/distributions << EOF
Origin: DevWorkshop
Label: DevWorkshop Packages
Codename: jammy
Architectures: amd64
Components: main
Description: Local packaging workshop repo
SignWith: $KEYID
EOF

# 匯出公鑰（客戶端需要）
gpg --export --armor $KEYID > $REPODIR/repo.gpg.pub

# nginx 設定
sudo tee /etc/nginx/sites-available/netcheck-repo << 'NGINX'
server {
    listen 8088;
    root /home/USER/netcheck-repo;   # 換成你的實際路徑
    autoindex on;
}
NGINX
sudo ln -sf /etc/nginx/sites-available/netcheck-repo \
            /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

手動測試第一次推送：

```bash
# 先本地 build
cd netcheck-1.0/
dpkg-buildpackage -us -uc -b

# 推進 repo
cd $REPODIR
reprepro includedeb jammy ../netcheck_1.0-1_amd64.deb
reprepro list jammy

# 客戶端設定（在同一台機器上測試）
curl -fsSL http://localhost:8088/repo.gpg.pub \
  | sudo gpg --dearmor -o /etc/apt/keyrings/devworkshop.gpg

echo "deb [signed-by=/etc/apt/keyrings/devworkshop.gpg] \
  http://localhost:8088 jammy main" \
  | sudo tee /etc/apt/sources.list.d/devworkshop.list

sudo apt update
apt-cache policy netcheck   # 應該看到 1.0-1 在候選清單
sudo apt install netcheck
netcheck -h 1.1.1.1 -p 80 -v
```

## Phase 3：GitHub Actions 自動化

建立 `.github/workflows/build-deb.yml`（參考 Ch 25 的完整範例）：

```yaml
name: Build and Deploy netcheck

on:
  push:
    tags: ['v*']

jobs:
  build-and-deploy:
    runs-on: ubuntu-22.04
    permissions:
      contents: write

    steps:
      - uses: actions/checkout@v4

      - run: sudo apt-get update && sudo apt-get install -y debhelper devscripts lintian build-essential

      - name: Set version from tag
        run: echo "VERSION=${GITHUB_REF_NAME#v}" >> $GITHUB_ENV

      - name: Update changelog
        run: |
          dch --newversion "${VERSION}-1" \
              --distribution unstable \
              --force-distribution \
              "Automated release ${VERSION}"

      - name: Build .deb
        run: |
          dpkg-buildpackage -us -uc -b
          echo "DEB=$(ls ../*.deb | head -1)" >> $GITHUB_ENV

      - name: Lintian gate
        run: lintian -EW $DEB   # 失敗就擋住，不推

      - name: GitHub Release
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          gh release create "${GITHUB_REF_NAME}" \
            "$DEB" \
            --title "netcheck ${VERSION}" \
            --generate-notes

      - name: Deploy to repo server
        env:
          REPO_SSH_KEY: ${{ secrets.REPO_SSH_KEY }}
          REPO_HOST: ${{ secrets.REPO_HOST }}
          REPO_USER: ${{ secrets.REPO_USER }}
          REPO_DIR: ${{ secrets.REPO_DIR }}
        run: |
          mkdir -p ~/.ssh
          printf '%s\n' "$REPO_SSH_KEY" > ~/.ssh/id && chmod 600 ~/.ssh/id
          ssh-keyscan -H "$REPO_HOST" >> ~/.ssh/known_hosts
          DEB_BASE=$(basename $DEB)
          scp -i ~/.ssh/id "$DEB" "$REPO_USER@$REPO_HOST:/tmp/"
          ssh -i ~/.ssh/id "$REPO_USER@$REPO_HOST" \
            "cd $REPO_DIR && \
             reprepro remove jammy netcheck 2>/dev/null || true && \
             reprepro includedeb jammy /tmp/$DEB_BASE && \
             rm /tmp/$DEB_BASE"
```

## Phase 4：升版測試

```bash
# 在源碼裡改一行輸出（模擬功能更新）
# netcheck.c 裡把 "OK" 改成 "REACHABLE"
sed -i 's/printf("OK\\n")/printf("REACHABLE\\n")/' netcheck.c

# 提交並打 tag
git add netcheck.c
git commit -m "change output message to REACHABLE"
git tag v1.1
git push && git push --tags

# 等 GitHub Actions 跑完（通常 2-3 分鐘）
# 然後在客戶端：
sudo apt update
apt-cache policy netcheck   # Candidate: 1.1-1
sudo apt upgrade netcheck

# 驗證新版
netcheck 8.8.8.8
# REACHABLE   ← 新版輸出
```

## 驗收標準

完成所有步驟後，確認：

```bash
# 1. lintian 零 Error
lintian -EW netcheck_1.0-1_amd64.deb
# （沒有 E: 開頭的輸出）

# 2. apt 安裝成功
apt-cache policy netcheck | grep "Installed: 1"

# 3. 功能正常
netcheck -h 8.8.8.8 -p 53 -v | grep "OK"
netcheck -h 192.0.2.1 -p 9999; [ $? -eq 1 ] && echo "exit code correct"

# 4. 套件資訊完整
dpkg -s netcheck | grep -E "^(Package|Version|Architecture|Maintainer)"

# 5. man page 存在
man netcheck 2>/dev/null && echo "man page OK"

# 6. 升版後客戶端拿到新版
# （需要 GitHub Actions 跑完後）
sudo apt update && sudo apt upgrade -y netcheck
netcheck 8.8.8.8 | grep -q "REACHABLE" && echo "v1.1 deployed OK"
```

## 可選挑戰

完成基本任務後，試試看：

1. **加 arm64 支援**：在 GitHub Actions 用 QEMU 交叉編譯 arm64 的 .deb
2. **aptly 版本**：用 aptly API 替換 reprepro SSH 推送
3. **多環境**：`staging` tag 推到 staging repo，`v*` tag 推到 production repo
4. **Python 工具**：把課程中打包的 Python 小工具也加入同一個 repo
5. **自動化 lintian 修復**：用 `--pedantic` 模式，把所有 Warning 也清零

## 自我檢核

這個 final project 涵蓋了整個課程：

- [ ] **Ch 8–9**：.deb 格式、control 必填欄位
- [ ] **Ch 16**：手工打包流程（build → install → package 的目錄分離）
- [ ] **Ch 17–18**：`debian/` 完整結構、debhelper dh $@
- [ ] **Ch 19–20**：Build-Depends、rules 的 dh_auto_* 自動偵測
- [ ] **Ch 21**：C 語言 `Architecture: any`、`${shlibs:Depends}`
- [ ] **Ch 22**：lintian -EW 當 CI gate
- [ ] **Ch 23**：reprepro includedeb、list、remove
- [ ] **Ch 24**（選）：aptly snapshot 版本控制
- [ ] **Ch 25**：GitHub Actions tag 觸發、dch 自動更新版本

如果你把這整條 pipeline 跑通了，恭喜——你現在能把任何 C/Python/Go 程式打包成企業級可維護的 Debian 套件了。
