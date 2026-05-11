# Ch 20 — rules 檔與 dh_auto_*

> 目標：理解 debian/rules 的 Makefile 結構，掌握 dh_auto_* 工具如何自動偵測 build 系統，以及如何在各種情況下客製化 build 流程。

## rules 檔的本質

`debian/rules` 是一個 **Makefile**，dpkg-buildpackage 呼叫它來執行打包流程。最小版本：

```makefile
#!/usr/bin/make -f
%:
	dh $@
```

`%:` 是萬用模式目標，捕獲所有 make 目標（`clean`、`build`、`install`、`binary` 等）並委派給 `dh $@`。

## dpkg-buildpackage 呼叫的目標

```
dpkg-buildpackage -b  →  呼叫：
  debian/rules clean
  debian/rules build
  debian/rules binary
```

- `clean`：清除 build artifacts
- `build`：編譯（`build-arch` + `build-indep`）
- `install`：安裝到 staging 目錄
- `binary`：打包成 .deb（`binary-arch` + `binary-indep`）

`dh $@` 會根據當前目標決定執行哪些 dh_* 步驟。

## dh_auto_* 的自動偵測

`dh_auto_configure`、`dh_auto_build`、`dh_auto_test`、`dh_auto_install` 會自動偵測使用哪種 build 系統：

```
偵測順序（先找到先用）：
1. CMakeLists.txt → cmake
2. Makefile.PL    → perl
3. Build.PL       → perl
4. setup.py / pyproject.toml → python
5. Makefile       → make
6. meson.build    → meson
7. configure      → autoconf
8. GNUmakefile    → make
```

```bash
# 查看偵測到的 build 系統
DH_VERBOSE=1 debian/rules build 2>&1 | head -20
```

## CMake 專案的 rules

```makefile
#!/usr/bin/make -f
%:
	dh $@

override_dh_auto_configure:
	dh_auto_configure -- \
		-DCMAKE_BUILD_TYPE=RelWithDebInfo \
		-DBUILD_SHARED_LIBS=ON \
		-DBUILD_TESTS=ON

override_dh_auto_test:
	dh_auto_test -- CTEST_OUTPUT_ON_FAILURE=1
```

`--` 後面的參數傳給底層工具（`cmake`、`make`、`ctest` 等）。

## Meson 專案的 rules

```makefile
#!/usr/bin/make -f
%:
	dh $@ --buildsystem=meson

override_dh_auto_configure:
	dh_auto_configure -- \
		--buildtype=plain \
		-Dtests=true
```

## 多個 binary 套件的 install 步驟

當 control 裡有多個 Package 時，`dh_install` 負責把 `debian/tmp/` 的內容分配到各套件的 staging 目錄：

```
build 輸出 → dh_auto_install → debian/tmp/
                                  ↓
                            dh_install 依 debian/*.install 分配
                                  ↓
                       debian/myproject/      ← Package: myproject 的 staging
                       debian/myproject-dev/  ← Package: myproject-dev 的 staging
                                  ↓
                            dh_builddeb 打包
                                  ↓
                       myproject_1.0-1_amd64.deb
                       myproject-dev_1.0-1_amd64.deb
```

```
# debian/myproject.install
usr/bin/myproject
usr/share/myproject/

# debian/myproject-dev.install
usr/include/myproject/
usr/lib/x86_64-linux-gnu/libmyproject.a
usr/lib/x86_64-linux-gnu/libmyproject.so
```

```makefile
# 在 rules 中確認安裝到 debian/tmp/
override_dh_auto_install:
	dh_auto_install --destdir=debian/tmp
```

## 環境變數控制 build

dpkg-buildpackage 會設定一系列環境變數：

```bash
# 查看 build 環境
dpkg-buildflags --list

# 常用 build flags（Debian 安全強化）
CFLAGS=-g -O2 -fstack-protector-strong -Wformat -Werror=format-security
CPPFLAGS=-Wdate-time -D_FORTIFY_SOURCE=2
LDFLAGS=-Wl,-Bsymbolic-functions -Wl,-z,relro

# 如果要加自訂 flags
override_dh_auto_build:
	DH_OPTIONS="" dh_auto_build -- CFLAGS="$(CFLAGS) -DMY_FLAG"
```

## 執行系統命令的 rules 範例

```makefile
#!/usr/bin/make -f

# 這個變數在 rules 所有目標中都可用
export DEB_BUILD_MAINT_OPTIONS = hardening=+all

# 取得版本號（從 changelog）
VERSION := $(shell dpkg-parsechangelog -S Version)
UPSTREAM_VERSION := $(shell echo "$(VERSION)" | sed 's/-[^-]*$$//')

%:
	dh $@

override_dh_auto_configure:
	dh_auto_configure -- \
		--version=$(UPSTREAM_VERSION) \
		--prefix=/usr

override_dh_install:
	dh_install
	# 安裝後做額外處理
	chmod 4755 debian/myproject/usr/bin/myproject-privileged
```

## build 產物在哪裡

```bash
# build 結束後查看 staging 目錄
ls debian/myproject/     # 套件 1 的安裝內容
ls debian/myproject-dev/ # 套件 2 的安裝內容
ls debian/tmp/           # dh_auto_install 的原始輸出
```

```bash
# 查看完整的 build 步驟
dpkg-buildpackage -us -uc -b 2>&1 | tee /tmp/build.log
```

## 自我檢核

- [ ] `debian/rules` 是 Makefile；`%: dh $@` 委派所有目標給 debhelper
- [ ] `dh_auto_*` 自動偵測 build 系統（CMake → cmake、Makefile → make、meson.build → meson）
- [ ] `override_dh_auto_configure:` 傳額外參數；`-- <args>` 後的參數直接傳給底層 build 工具
- [ ] 多套件：`dh_auto_install --destdir=debian/tmp`，再用 `debian/<pkg>.install` 分配
- [ ] `dpkg-buildflags --list` 查看 Debian 的安全強化 build flags

→ [Ch 21 打包不同語言的程式](./21-multi-lang-packaging.md)
