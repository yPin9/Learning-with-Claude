# Ch 18 — debhelper 與 dh 自動化

> 目標：理解 debhelper 的工作流程，掌握 dh 命令序列，知道如何 override 特定步驟，以及常用的 dh_* 工具。

## 安裝打包工具

```bash
sudo apt install debhelper devscripts build-essential dh-make
```

## debhelper 是什麼

debhelper 是一組 `dh_*` 工具的集合，每個工具負責 build 流程中的一個步驟。`dh` 是統一的前端，按正確順序執行所有 `dh_*` 工具。

```
dh binary
  ↓
執行這些步驟（按順序）：
  dh_testdir          ← 確認在正確目錄
  dh_auto_configure   ← 執行 ./configure 或 cmake 或 meson
  dh_auto_build       ← 執行 make 或 cmake --build
  dh_auto_test        ← 執行測試（make test 等）
  dh_auto_install     ← 安裝到 debian/tmp/
  dh_install          ← 根據 debian/*.install 分發到各套件目錄
  dh_installdocs      ← 安裝文件
  dh_installchangelogs← 安裝 changelog
  dh_installman       ← 安裝 man page
  dh_strip            ← strip debug symbols
  dh_compress         ← 壓縮大型文件
  dh_fixperms         ← 修正檔案權限
  dh_shlibdeps        ← 分析動態庫依賴
  dh_gencontrol       ← 生成 DEBIAN/control
  dh_md5sums          ← 生成 md5sums
  dh_builddeb         ← 打包成 .deb
```

## 一個完整的打包範例

以打包一個 C 程式為例：

```
mygreet/
├── Makefile
├── mygreet.c
└── debian/
    ├── control
    ├── rules
    ├── changelog
    ├── source/
    │   └── format
    └── copyright
```

```c
// mygreet.c
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char *argv[]) {
    if (argc != 2) { fprintf(stderr, "Usage: mygreet <name>\n"); return 1; }
    printf("Hello, %s!\n", argv[1]);
    return 0;
}
```

```makefile
# Makefile
PREFIX ?= /usr/local

mygreet: mygreet.c
	$(CC) $(CFLAGS) -o $@ $<

install: mygreet
	install -D -m 755 mygreet $(DESTDIR)$(PREFIX)/bin/mygreet

clean:
	rm -f mygreet
```

```
# debian/control
Source: mygreet
Section: utils
Priority: optional
Maintainer: Your Name <you@example.com>
Build-Depends: debhelper-compat (= 13)
Standards-Version: 4.6.0

Package: mygreet
Architecture: any
Depends: ${shlibs:Depends}, ${misc:Depends}
Description: A greeting tool written in C
 Greet a person by name.
```

```makefile
# debian/rules
#!/usr/bin/make -f
%:
	dh $@
```

```
# debian/changelog（用 dch 生成）
mygreet (1.0-1) unstable; urgency=medium

  * Initial release.

 -- Your Name <you@example.com>  Sun, 11 May 2025 10:00:00 +0800
```

## 執行 build

```bash
# 方法 1：dpkg-buildpackage（標準）
dpkg-buildpackage -us -uc -b
# -us = unsigned source, -uc = unsigned changes, -b = binary only

# 方法 2：debuild（包含 lintian 檢查）
debuild -us -uc -b

# 輸出在上一層目錄
ls ../mygreet_1.0-1_amd64.deb
```

## Override 特定步驟

`debian/rules` 的 `override_dh_*` 讓你替換或添加到特定步驟：

```makefile
#!/usr/bin/make -f
%:
	dh $@

# 覆蓋 configure 步驟（例如傳自訂選項給 cmake）
override_dh_auto_configure:
	dh_auto_configure -- \
		-DCMAKE_BUILD_TYPE=Release \
		-DENABLE_TESTS=ON

# 安裝前做額外設定
override_dh_auto_install:
	dh_auto_install
	# 手動安裝額外的設定檔
	install -D -m 644 config/default.conf \
		debian/mygreet/etc/mygreet/config.conf

# 完全跳過測試
override_dh_auto_test:
	# 不做任何事（空覆蓋）
```

## 常用 dh_* 工具

```bash
# 自動分析動態庫依賴（填入 ${shlibs:Depends}）
dh_shlibdeps

# 生成 DEBIAN/control（填入占位符）
dh_gencontrol

# 安裝 systemd service（自動處理 enable/start/stop）
dh_installsystemd

# 安裝 man page
dh_installman mygreet.1

# 壓縮大型文件（> 4KB 的文件自動 gzip）
dh_compress

# 修正常見的檔案權限問題
dh_fixperms

# strip debug symbol（減小 binary 大小）
dh_strip

# 安裝文件（README、CHANGELOG 等）
dh_installdocs README.md

# 建立 symlink
dh_link usr/bin/mygreet usr/bin/mg
```

## 用 dh-make 快速建立骨架

```bash
# 進入有源碼的目錄
cd mygreet-1.0/

# 生成 debian/ 骨架（-s = single binary package）
dh_make --single --native --email you@example.com

# 互動式詢問套件類型後，debian/ 目錄就建好了
```

dh_make 生成的範本包含很多注解說明，適合第一次打包時參考。

## 查看 dh 做了什麼

```bash
# 詳細模式：看 dh 執行了哪些子命令
DH_VERBOSE=1 dpkg-buildpackage -us -uc -b

# 只執行特定目標（debug 用）
debian/rules clean
debian/rules build
debian/rules install
debian/rules binary
```

## 自我檢核

- [ ] `dh $@` 一行 rules 讓 debhelper 自動執行所有步驟
- [ ] `override_dh_auto_configure:` 等覆蓋特定步驟，空覆蓋 = 跳過
- [ ] `${shlibs:Depends}` 由 `dh_shlibdeps` 填入；`${misc:Depends}` 由 `dh_gencontrol` 填入
- [ ] `dpkg-buildpackage -us -uc -b` = 不簽章、只 build binary（測試時用）
- [ ] `DH_VERBOSE=1` 查看 dh 實際執行的每個 dh_* 命令

→ [Ch 19 control 進階](./19-control-advanced.md)
