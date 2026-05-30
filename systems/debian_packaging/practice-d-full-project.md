# 練習 D — 含 service + library 的完整專案

> **目標**：整合 Part 5 的進階打包技巧（shared library Ch 26 + systemd service Ch 29 + 完整的 maintainer scripts Ch 5），打包一個「daemon + 它依賴的 library + CLI 控制工具」的完整專案。這模擬真實世界的 server 軟體打包（如 redis、postgresql 這類「library + daemon + client」的結構）。

## 背景與動機

練習 B 你打包了 library + CLI。這個練習更進一步——加入一個 **systemd daemon**，需要專屬使用者、設定檔、服務生命週期管理。這是真實 server 軟體的典型結構：

```
真實對照：
  redis      → libhiredis（library）+ redis-server（daemon）+ redis-cli（client）
  postgresql → libpq（library）+ postgresql（daemon）+ psql（client）
  你的專案    → libgreet（library）+ greetd（daemon）+ greetctl（client）
```

完成後你能打包絕大多數 server 軟體——這是打包技能的一個里程碑。

## 任務規格

打包專案 `greetd`，產出**四個** binary package：

| 套件 | 內容 | Arch |
|---|---|---|
| `libgreet1` | shared library（runtime）| any |
| `libgreet-dev` | headers + dev symlink | any |
| `greetd` | systemd daemon（用 libgreet）+ unit file | any |
| `greetctl` | CLI client（控制 daemon）| any |

**功能規格**：
- `greetd` 是個 daemon，啟動後寫問候訊息到 log file（`/var/log/greetd/greetd.log`），每 5 秒一次
- `greetctl` 能查 daemon 狀態（簡化：讀 log file 最後一行）
- daemon 以專屬使用者 `greetd` 執行（非 root）
- 設定檔 `/etc/greetd/greetd.conf`（conffile，設定問候語和間隔）

**驗收標準（整合前面所有所學）**：
- 四個套件正確 build，`greetd` 自動依賴 `libgreet1`（`${shlibs:Depends}`，Ch 7/19）
- 裝 `greetd` 後 systemd service 自動 enable + start（Ch 29），以 `greetd` 使用者跑
- `greetd` 使用者在 service start 前被建立（postinst 順序，Ch 5/29）
- `/etc/greetd/greetd.conf` 是 conffile（改過後升級會提示，Ch 2）
- purge 時清理 log、使用者、設定（postrm，Ch 5）
- sbuild 乾淨建置 + 零 lintian warning + autopkgtest（Part 3）

## 如果你卡住了

1. 這是前面所有練習的綜合——回顧練習 B（library 拆分）、Ch 29（systemd）、Ch 5（postinst 順序）
2. service 使用者建立必須在 postinst 的 `#DEBHELPER#` **之前**（否則 start 時 User= 不存在）
3. log 目錄 `/var/log/greetd/` 要在 postinst 建立並 chown 給 greetd 使用者
4. conffile：把 `greetd.conf` 裝到 `/etc/`，debhelper 自動標記為 conffile（或用 `debian/greetd.conffiles`）
5. daemon 不能 fork 到背景（systemd `Type=simple` 要求前景執行），或用對應的 Type
6. autopkgtest 測 service：裝完後 `systemctl is-active greetd` 應該是 active（用 isolation-container）

## 實作步驟建議

### Step 1：upstream 專案（library + daemon + client）
### Step 2：四個套件的 control + install
### Step 3：systemd unit + postinst（使用者/目錄/順序）
### Step 4：conffile + postrm 清理
### Step 5：sbuild + lintian + autopkgtest 全綠

## 完整參考解答

**寫完再看！**

<details>
<summary>Step 1：upstream 專案結構</summary>

```
greetd-1.0/
├── Makefile
├── include/greet.h          (同練習 B 的 library)
├── lib/greet.c
├── daemon/greetd.c          (daemon，用 libgreet)
└── client/greetctl.c        (client)
```

`daemon/greetd.c`（簡化的 daemon）：
```c
#include "greet.h"
#include <stdio.h>
#include <unistd.h>
#include <time.h>

int main(void) {
    FILE *log = fopen("/var/log/greetd/greetd.log", "a");
    if (!log) return 1;
    setvbuf(log, NULL, _IOLBF, 0);   /* line-buffered */
    while (1) {
        time_t t = time(NULL);
        fprintf(log, "[%ld] %s\n", (long)t, greet_make("systemd"));
        sleep(5);
    }
    return 0;
}
```

`client/greetctl.c`：
```c
#include <stdio.h>
int main(void) {
    FILE *log = fopen("/var/log/greetd/greetd.log", "r");
    if (!log) { printf("greetd not running or no log\n"); return 1; }
    char line[256], last[256] = "";
    while (fgets(line, sizeof(line), log)) snprintf(last, sizeof(last), "%s", line);
    fclose(log);
    printf("Last message: %s", last);
    return 0;
}
```

Makefile（節錄關鍵——library 設 SONAME、daemon/client 連結 libgreet）：
```makefile
PREFIX  ?= /usr
LIBDIR  ?= $(PREFIX)/lib
DESTDIR ?=
CC      ?= cc
CFLAGS  ?= -O2 -g -Wall

SONAME  = libgreet.so.1
LIBFILE = libgreet.so.1.0.0

all: $(LIBFILE) greetd greetctl

$(LIBFILE): lib/greet.c
	$(CC) $(CFLAGS) -fPIC -shared -Iinclude -Wl,-soname,$(SONAME) -o $@ $<
	ln -sf $(LIBFILE) libgreet.so.1
	ln -sf $(LIBFILE) libgreet.so

greetd: daemon/greetd.c $(LIBFILE)
	$(CC) $(CFLAGS) -Iinclude -o $@ $< -L. -lgreet

greetctl: client/greetctl.c
	$(CC) $(CFLAGS) -o $@ $<

install: all
	install -d $(DESTDIR)$(LIBDIR)
	install -m644 $(LIBFILE) $(DESTDIR)$(LIBDIR)/
	ln -sf $(LIBFILE) $(DESTDIR)$(LIBDIR)/libgreet.so.1
	ln -sf $(LIBFILE) $(DESTDIR)$(LIBDIR)/libgreet.so
	install -d $(DESTDIR)$(PREFIX)/include && install -m644 include/greet.h $(DESTDIR)$(PREFIX)/include/
	install -d $(DESTDIR)$(PREFIX)/bin
	install -m755 greetd   $(DESTDIR)$(PREFIX)/bin/
	install -m755 greetctl $(DESTDIR)$(PREFIX)/bin/

clean:
	rm -f $(LIBFILE) libgreet.so* greetd greetctl
```

</details>

<details>
<summary>Step 2–4：debian/ 完整內容</summary>

`debian/control`：
```
Source: greetd
Section: admin
Priority: optional
Maintainer: Your Name <you@example.com>
Build-Depends: debhelper-compat (= 13)
Standards-Version: 4.6.2
Rules-Requires-Root: no

Package: libgreet1
Section: libs
Architecture: any
Multi-Arch: same
Depends: ${shlibs:Depends}, ${misc:Depends}
Description: greeting library (runtime)
 Shared library providing greeting string generation.

Package: libgreet-dev
Section: libdevel
Architecture: any
Multi-Arch: same
Depends: libgreet1 (= ${binary:Version}), ${misc:Depends}
Description: greeting library (development files)
 Headers and development symlink for libgreet.

Package: greetd
Architecture: any
Depends: ${shlibs:Depends}, ${misc:Depends}, adduser
Description: greeting daemon
 A background service that periodically logs greeting messages,
 demonstrating systemd integration and a dedicated service user.

Package: greetctl
Architecture: any
Depends: ${shlibs:Depends}, ${misc:Depends}
Description: greeting daemon control client
 Command-line client to query the greetd daemon status.
```

install 檔案：
```
# debian/libgreet1.install
usr/lib/*/libgreet.so.*

# debian/libgreet-dev.install
usr/include/greet.h
usr/lib/*/libgreet.so

# debian/greetd.install
usr/bin/greetd
debian/greetd.conf  etc/greetd/

# debian/greetctl.install
usr/bin/greetctl
```

`debian/greetd.conf`（會成為 conffile）：
```
# greetd configuration
GREETING="Hello"
INTERVAL=5
```

`debian/greetd.service`（systemd unit）：
```ini
[Unit]
Description=Greeting daemon
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/greetd
Restart=on-failure
User=greetd
Group=greetd

[Install]
WantedBy=multi-user.target
```

`debian/greetd.postinst`（使用者 + 目錄，在 #DEBHELPER# 之前）：
```bash
#!/bin/sh
set -e
case "$1" in
    configure)
        # 建立 service 使用者（必須在 #DEBHELPER# 的 service start 之前！）
        if ! getent passwd greetd >/dev/null; then
            adduser --system --group --no-create-home \
                    --home /var/lib/greetd greetd
        fi
        # 建立 log 目錄並授權給 greetd
        mkdir -p /var/log/greetd
        chown greetd:greetd /var/log/greetd
        chmod 750 /var/log/greetd
        ;;
    abort-upgrade|abort-remove|abort-deconfigure)
        ;;
    *)
        echo "postinst called with unknown argument \`$1'" >&2
        exit 1
        ;;
esac
#DEBHELPER#
exit 0
```

`debian/greetd.postrm`（purge 清理）：
```bash
#!/bin/sh
set -e
case "$1" in
    purge)
        rm -rf /var/log/greetd
        rm -rf /etc/greetd
        if getent passwd greetd >/dev/null; then
            deluser --system greetd || true
        fi
        ;;
    remove|upgrade|failed-upgrade|abort-install|abort-upgrade|disappear)
        ;;
esac
#DEBHELPER#
exit 0
```

`debian/rules`：
```makefile
#!/usr/bin/make -f
%:
	dh $@
override_dh_auto_install:
	dh_auto_install -- PREFIX=/usr LIBDIR=/usr/lib/$(DEB_HOST_MULTIARCH)
DEB_HOST_MULTIARCH ?= $(shell dpkg-architecture -qDEB_HOST_MULTIARCH)
```

</details>

<details>
<summary>Step 5：autopkgtest + 驗證</summary>

`debian/tests/control`：
```
Tests: service-runs
Depends: @
Restrictions: needs-root, isolation-container

Tests: lib-usable
Depends: @, gcc, libc6-dev
Restrictions: allow-stderr
```

`debian/tests/service-runs`：
```bash
#!/bin/sh
set -e
# 確認 service 啟動了（autopkgtest 在 container 裡，service 真的會跑）
systemctl is-active greetd || { echo "FAIL: greetd not active"; exit 1; }
sleep 6   # 等 daemon 寫至少一筆 log
greetctl | grep -q "Hello" || { echo "FAIL: no greeting in log"; exit 1; }
echo "PASS: service-runs"
```

驗證：
```bash
cd greetd-1.0/
dpkg-buildpackage -S -us -uc
cd ..
sbuild -d bookworm --run-lintian --run-autopkgtest greetd_1.0-1.dsc

# 手動在 VM 驗證
sudo dpkg -i libgreet1_*.deb greetd_*.deb greetctl_*.deb
systemctl status greetd            # active，以 greetd 使用者跑
ps -u greetd                       # 確認 greetd 進程屬於 greetd 使用者
sleep 6 && greetctl                # 看到問候訊息
sudo apt purge greetd              # 清理，確認 log/使用者/設定都清掉
```

**解答說明**：

- **postinst 順序是核心**：`case configure`（建使用者+目錄）在 `#DEBHELPER#`（dh_installsystemd 注入的 start）之前。順序錯了，service start 時 `User=greetd` 不存在，啟動失敗（Ch 29 的關鍵）
- **四套件的依賴鏈**：greetd 和 greetctl 透過 `${shlibs:Depends}` 自動依賴 libgreet1（greetctl 其實沒連 libgreet，所以它的 shlibdeps 只有 libc6——這是對的，展示 shlibdeps 的精確性）
- **conffile**：`greetd.conf` 裝到 `/etc/greetd/`，debhelper 自動標記為 conffile（裝進 /etc 的檔案預設是 conffile）
- **purge vs remove**：postrm 只在 purge 清 log/使用者/設定，remove 保留（Ch 5）
- **autopkgtest 用 isolation-container**：service 測試會真的啟動 daemon、改系統狀態，要 container 隔離（Ch 17）
- **lib-usable 測試**：驗證 libgreet-dev 能編譯（整合 Ch 17 + Ch 26）

</details>

## 測試用案例

| 操作 | 預期 | 驗證 |
|---|---|---|
| build | 4 個 .deb | 多套件拆分 |
| `dpkg-deb -f greetd Depends` | 含 `libgreet1` | ${shlibs:Depends} |
| 裝 greetd 後 `systemctl is-active greetd` | active | systemd 整合 |
| `ps -u greetd` | greetd 進程存在 | service 使用者 |
| 改 conf 後重裝 | conffile 提示 | conffile 機制 |
| `greetctl` | 顯示問候訊息 | daemon 運作 + client |
| `apt purge greetd` | log/使用者/設定清除 | postrm purge |
| sbuild + lintian + autopkgtest | 全綠 | 生產品質 |

## 延伸挑戰（加分）

- **挑戰一**：讓 daemon 真的讀 `/etc/greetd/greetd.conf`（GREETING、INTERVAL），改 conf 後 `systemctl restart greetd` 生效。測試 conffile 改動後的行為

- **挑戰二**：加 socket activation（Ch 29 進階）——greetd 改成有連線才啟動，提供 `greetd.socket`

- **挑戰三**：加 logrotate 設定（`debian/greetd.logrotate` → `/etc/logrotate.d/`），讓 `/var/log/greetd/greetd.log` 定期輪替

- **挑戰四**：把 greetctl 真的透過 IPC（unix socket）和 daemon 通訊查狀態，而非讀 log file。這需要 daemon 開 socket、client 連它

## 自我檢核

- [ ] 能打包一個「library + daemon + client」的完整 server 軟體（多套件 + service）
- [ ] 理解 postinst 裡使用者建立和 `#DEBHELPER#` 的順序為什麼關鍵
- [ ] 知道 conffile、service 生命週期、service 使用者三者如何協作
- [ ] 能寫測試 service 是否真的啟動的 autopkgtest（isolation-container）
- [ ] 能把這個練習對應到真實 server 軟體（redis/postgresql）的打包結構

→ [Ch 31 Salsa CI / GitLab CI](./31-salsa-ci.md)
