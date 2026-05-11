# 練習 B — 把自己的程式打包成可安裝的 deb

> 目標：把 Ch 16–22 學到的 debhelper 打包流程完整走一遍，從源碼到 `dpkg -i` 安裝成功，並通過 lintian 零 Error。

## 任務規格

你要打包一個 C 命令列工具 `sysinfo`，它的功能是：

```bash
sysinfo          # 列出 hostname / uptime / load average / memory usage
sysinfo --json   # 輸出 JSON 格式
sysinfo --help   # 顯示說明
```

這個工具存在，你需要：
1. 從下面的源碼開始
2. 建立完整的 `debian/` 目錄（用 debhelper 13）
3. 用 `dpkg-buildpackage` build 出 .deb
4. 通過 `lintian -EW` 零 Error
5. 成功 `sudo dpkg -i` 安裝並測試

## 源碼

### sysinfo.c

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/sysinfo.h>

static void print_normal(void) {
    char hostname[256];
    struct sysinfo si;

    gethostname(hostname, sizeof(hostname));
    sysinfo(&si);

    long hours = si.uptime / 3600;
    long mins  = (si.uptime % 3600) / 60;
    double load = si.loads[0] / 65536.0;
    long total_mb = si.totalram * si.mem_unit / 1024 / 1024;
    long free_mb  = si.freeram  * si.mem_unit / 1024 / 1024;

    printf("Hostname : %s\n", hostname);
    printf("Uptime   : %ldh %ldm\n", hours, mins);
    printf("Load     : %.2f\n", load);
    printf("Memory   : %ld MB total, %ld MB free\n", total_mb, free_mb);
}

static void print_json(void) {
    char hostname[256];
    struct sysinfo si;

    gethostname(hostname, sizeof(hostname));
    sysinfo(&si);

    long hours = si.uptime / 3600;
    long mins  = (si.uptime % 3600) / 60;
    double load = si.loads[0] / 65536.0;
    long total_mb = si.totalram * si.mem_unit / 1024 / 1024;
    long free_mb  = si.freeram  * si.mem_unit / 1024 / 1024;

    printf("{\n");
    printf("  \"hostname\": \"%s\",\n", hostname);
    printf("  \"uptime_hours\": %ld,\n", hours);
    printf("  \"uptime_mins\": %ld,\n", mins);
    printf("  \"load_1min\": %.2f,\n", load);
    printf("  \"memory_total_mb\": %ld,\n", total_mb);
    printf("  \"memory_free_mb\": %ld\n", free_mb);
    printf("}\n");
}

int main(int argc, char *argv[]) {
    if (argc == 1) {
        print_normal();
        return 0;
    }
    if (argc == 2 && strcmp(argv[1], "--json") == 0) {
        print_json();
        return 0;
    }
    if (argc == 2 && strcmp(argv[1], "--help") == 0) {
        printf("Usage: sysinfo [--json] [--help]\n");
        printf("  --json  Output in JSON format\n");
        printf("  --help  Show this help\n");
        return 0;
    }
    fprintf(stderr, "sysinfo: unknown option '%s'\n", argv[1]);
    return 1;
}
```

### Makefile

```makefile
CC      ?= gcc
CFLAGS  ?= -O2 -Wall
PREFIX  ?= /usr
BINDIR  := $(PREFIX)/bin
MANDIR  := $(PREFIX)/share/man/man1

sysinfo: sysinfo.c
	$(CC) $(CFLAGS) -o $@ $<

install: sysinfo
	install -D -m 755 sysinfo $(DESTDIR)$(BINDIR)/sysinfo
	install -D -m 644 sysinfo.1 $(DESTDIR)$(MANDIR)/sysinfo.1

clean:
	rm -f sysinfo

.PHONY: install clean
```

### sysinfo.1（man page）

```
.TH SYSINFO 1 "2025-05-11" "1.0" "User Commands"
.SH NAME
sysinfo \- display basic system information
.SH SYNOPSIS
.B sysinfo
[\fB\-\-json\fR]
[\fB\-\-help\fR]
.SH DESCRIPTION
.B sysinfo
prints hostname, uptime, load average, and memory usage.
.SH OPTIONS
.TP
.B \-\-json
Output in JSON format.
.TP
.B \-\-help
Show usage information.
.SH EXAMPLES
.B sysinfo
.PP
Hostname : myhost
.br
Uptime   : 2h 30m
.br
Load     : 0.15
.br
Memory   : 15872 MB total, 12345 MB free
.SH AUTHOR
Written as a packaging exercise.
```

## 實作步驟建議

### Step 1：建立目錄結構

```bash
# 版本號放進目錄名（這是 Debian 慣例）
mkdir -p sysinfo-1.0/debian/source
cd sysinfo-1.0/

# 把源碼放進來
# （把上面的 sysinfo.c、Makefile、sysinfo.1 放到 sysinfo-1.0/）
```

### Step 2：建立 debian/ 必填檔案

先把四個必填檔案建好：
- `debian/control`
- `debian/rules`
- `debian/changelog`
- `debian/source/format`

記得：
- `Build-Depends` 只需要 `debhelper-compat (= 13)` 和 `gcc`（sysinfo 不連結額外函式庫）
- `Architecture: any`（因為有 C binary）
- `source/format` 用 `3.0 (native)`（自己的程式）

### Step 3：建立 copyright

```bash
# lintian 要求 debian/copyright 存在且格式正確
# 最簡單的版本：
cat > debian/copyright << 'EOF'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: sysinfo
Source: https://example.com/sysinfo

Files: *
Copyright: 2025 Your Name <you@example.com>
License: GPL-2+

License: GPL-2+
 On Debian systems, the full text of the GNU General Public License
 version 2 can be found in `/usr/share/common-licenses/GPL-2'.
EOF
```

### Step 4：build 並測試

```bash
# 在 sysinfo-1.0/ 目錄執行
dpkg-buildpackage -us -uc -b

# 查看輸出（應在上一層目錄）
ls ../*.deb

# 用 lintian 掃描
lintian -EW ../sysinfo_1.0-1_amd64.deb

# 安裝測試
sudo dpkg -i ../sysinfo_1.0-1_amd64.deb
sysinfo
sysinfo --json
man sysinfo

# 清除
sudo apt remove sysinfo
```

### Step 5：修 lintian 警告

每次 lintian 有 Error，就修、重 build、再掃描，直到 `-EW` 零輸出。

## 完整參考解答

**先自己做！** 這個練習的價值在於遇到問題、去翻 Ch 16–22、解決它。

<details>
<summary>debian/control 參考</summary>

```
Source: sysinfo
Section: utils
Priority: optional
Maintainer: Your Name <you@example.com>
Build-Depends: debhelper-compat (= 13), gcc
Standards-Version: 4.6.2
Homepage: https://example.com/sysinfo
Rules-Requires-Root: no

Package: sysinfo
Architecture: any
Depends: ${shlibs:Depends}, ${misc:Depends}
Description: Display basic system information
 sysinfo prints hostname, uptime, load average, and memory usage
 of the current system. Supports plain text and JSON output.
```

注意：
- `Rules-Requires-Root: no` 告訴 lintian 這個套件 build 時不需要 root（減少警告）
- synopsis 不以 "A"/"The" 開頭
- 第二行描述有一個空格縮排

</details>

<details>
<summary>debian/rules 參考</summary>

```makefile
#!/usr/bin/make -f
%:
	dh $@
```

就這樣。Makefile 有正確的 install 目標和 DESTDIR，`dh_auto_*` 全部自動處理。

</details>

<details>
<summary>debian/changelog 參考</summary>

```
sysinfo (1.0-1) unstable; urgency=medium

  * Initial packaging.

 -- Your Name <you@example.com>  Sun, 11 May 2025 10:00:00 +0800
```

用 `dch --create --package sysinfo --newversion 1.0-1 "Initial packaging."` 自動生成更安全。

</details>

<details>
<summary>完整 build 指令序列</summary>

```bash
# 從零開始完整流程
mkdir -p sysinfo-1.0/debian/source

# 把源碼放進去
cp sysinfo.c sysinfo.1 Makefile sysinfo-1.0/

# 建立 debian/ 檔案（略，見上方各個參考）

# build
cd sysinfo-1.0
dpkg-buildpackage -us -uc -b 2>&1 | tee /tmp/build.log

# 掃描
lintian -EW ../sysinfo_1.0-1_amd64.deb

# 如果有 E/W，對照 Ch 22 修正，然後重跑

# 安裝驗證
sudo dpkg -i ../sysinfo_1.0-1_amd64.deb
sysinfo
sysinfo --json | python3 -m json.tool   # 驗證 JSON 格式正確
man sysinfo

# 查看安裝了什麼
dpkg -L sysinfo
dpkg -s sysinfo

# 移除
sudo apt remove sysinfo
```

</details>

## 測試用例

安裝後驗證這些全部通過：

```bash
# 功能正確
sysinfo | grep -q "Hostname"       && echo "PASS: normal mode"
sysinfo --json | python3 -m json.tool > /dev/null && echo "PASS: json valid"
sysinfo --help | grep -q "Usage"   && echo "PASS: help works"
sysinfo --bad 2>&1 | grep -q "unknown" && echo "PASS: error handling"

# 安裝正確
test -x /usr/bin/sysinfo           && echo "PASS: binary installed"
test -f /usr/share/man/man1/sysinfo.1.gz && echo "PASS: man page installed"
dpkg -s sysinfo | grep -q "^Status: install ok installed" && echo "PASS: dpkg status"
```

## 自我檢核

- [ ] `debian/control` 有正確 `Build-Depends`，`Architecture: any`，`${shlibs:Depends}`
- [ ] `debian/rules` 的 rules 檔有執行權限（`chmod +x debian/rules`）
- [ ] `debian/changelog` 格式正確，`dch` 可以解析
- [ ] `debian/copyright` 存在且 lintian 接受
- [ ] `lintian -EW` 零 Error（Warning 酌情處理）
- [ ] 安裝後 `/usr/bin/sysinfo` 和 `sysinfo.1.gz` 都在

→ [Ch 23 reprepro 架設私有 apt repo](./23-reprepro.md)
