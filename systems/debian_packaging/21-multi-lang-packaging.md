# Ch 21 — 打包不同語言的程式

> 目標：理解 C、Python、Go 三種語言在 Debian 打包上的關鍵差異，知道每種語言需要哪些額外的 debhelper add-on 和 Build-Depends。

## 語言差異的本質

打包流程不變（`debian/rules` 呼叫 `dh $@`），但每種語言有不同的：

1. **Build 系統**：CMake/Makefile vs setup.py/pyproject.toml vs go build
2. **執行時依賴**：libc vs python3 vs 無（Go 靜態連結）
3. **debhelper add-on**：Python 需要 `dh-python`，Go 不需要額外 add-on
4. **Install 位置**：Go binary 裝 `/usr/bin/`；Python module 裝 `/usr/lib/python3/dist-packages/`

## C 程式打包（Makefile / CMake）

這是最直接的案例，`dh_auto_*` 原生支援：

```
mygreet-1.0/
├── CMakeLists.txt
├── src/mygreet.c
└── debian/
    ├── control
    ├── rules
    ├── changelog
    └── source/format
```

```
# debian/control
Source: mygreet
Build-Depends: debhelper-compat (= 13), cmake, gcc
Standards-Version: 4.6.2

Package: mygreet
Architecture: any          ← 架構相關（編譯出 binary）
Depends: ${shlibs:Depends}, ${misc:Depends}
Description: A greeting tool in C
 Simple greet command.
```

```makefile
# debian/rules（CMake 專案）
#!/usr/bin/make -f
%:
	dh $@

override_dh_auto_configure:
	dh_auto_configure -- -DCMAKE_BUILD_TYPE=RelWithDebInfo
```

C 程式的特點：
- `Architecture: any`（每個架構都要編譯）
- `${shlibs:Depends}` 讓 `dh_shlibdeps` 自動偵測 `.so` 依賴
- 如果連結了 OpenSSL，`Depends` 會自動補上 `libssl3`

## Python 程式打包

Python 打包分兩個時代：舊版 `setup.py`、新版 `pyproject.toml`。

### 舊版：setup.py / setuptools

```
mypy-1.0/
├── setup.py
├── mypy/
│   ├── __init__.py
│   └── main.py
└── debian/
    ├── control
    ├── rules
    └── ...
```

```
# debian/control
Source: mypy-tool
Build-Depends: debhelper-compat (= 13),
               dh-python,              ← 必須！處理 Python 版本依賴
               python3-all,            ← 提供所有 Python 3 版本
               python3-setuptools
Standards-Version: 4.6.2

Package: mypy-tool
Architecture: all             ← Python 通常架構無關（純 .py 檔）
Depends: ${python3:Depends},  ← dh-python 填入正確的 python3 版本範圍
         ${misc:Depends}
Description: My Python tool
 A tool written in Python.
```

```makefile
# debian/rules
#!/usr/bin/make -f
%:
	dh $@ --with python3   ← 啟用 dh-python add-on
```

`dh-python` 的 `--with python3` 做了什麼：
- 呼叫 `dh_python3` 掃描 `.py` 檔
- 自動填入 `${python3:Depends}`（例如 `python3 (>= 3.10)`）
- 處理 `.pyc` 的編譯和清理

### 新版：pyproject.toml + pep517

```toml
# pyproject.toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.backends.legacy:build"

[project]
name = "mypy-tool"
version = "1.0"
```

```
# debian/control（pyproject.toml 版）
Build-Depends: debhelper-compat (= 13),
               dh-python,
               python3-all,
               pybuild-plugin-pyproject,  ← 處理 PEP 517
               python3-setuptools
```

```makefile
# debian/rules（pyproject 版）
#!/usr/bin/make -f
export PYBUILD_NAME = mypy-tool    ← 告訴 pybuild 套件名稱

%:
	dh $@ --with python3 --buildsystem=pybuild
```

### Python 安裝路徑

```bash
# 純 Python 模組裝到：
/usr/lib/python3/dist-packages/mypy_tool/

# 命令列工具裝到：
/usr/bin/mypy-tool

# 查看實際安裝位置
dpkg -L mypy-tool
```

### C Extension Python 模組

如果 Python 套件含有 C extension（`*.so`）：

```
Architecture: any       ← 改成 any！C extension 是架構相關的
```

```makefile
# debian/rules
#!/usr/bin/make -f
%:
	dh $@ --with python3 --buildsystem=pybuild
```

pybuild 會自動偵測 C extension 並在各架構上編譯。

## Go 程式打包

Go 的特點：**靜態連結**。編譯出來的 binary 幾乎不依賴任何共享函式庫（除非用了 CGO）。

```
mygoapp-1.0/
├── main.go
├── go.mod
└── debian/
    ├── control
    ├── rules
    └── ...
```

```
# debian/control
Source: mygoapp
Build-Depends: debhelper-compat (= 13),
               golang-go              ← Go 編譯器
Standards-Version: 4.6.2

Package: mygoapp
Architecture: any
Depends: ${misc:Depends}   ← 不需要 ${shlibs:Depends}！Go 靜態連結
Description: My Go application
 Built with Go.
```

```makefile
# debian/rules
#!/usr/bin/make -f

# Go 不用 dh_auto_configure，直接 override build 和 install
%:
	dh $@

override_dh_auto_build:
	go build -o mygoapp ./...

override_dh_auto_install:
	install -D -m 755 mygoapp \
		$(CURDIR)/debian/mygoapp/usr/bin/mygoapp

override_dh_auto_test:
	go test ./...
```

### Go 的 Vendor 模式

Go modules 依賴在 build 時需要網路下載，但 Debian build 環境沒有網路。解法：

```bash
# 在打包前把依賴 vendor 進去
go mod vendor

# debian/rules 中告訴 go build 使用 vendor
override_dh_auto_build:
	go build -mod=vendor -o mygoapp ./...
```

或者使用 `dh-golang`：

```
# debian/control
Build-Depends: debhelper-compat (= 13), dh-golang, golang-go, ...
```

```makefile
# debian/rules（使用 dh-golang）
#!/usr/bin/make -f
include /usr/share/dpkg/pkg-info.mk

export DH_GOPKG := github.com/myorg/mygoapp

%:
	dh $@ --buildsystem=golang --with=golang
```

### Go 靜態連結的好處

```
mygoapp 安裝後的依賴：
$ dpkg -I mygoapp_1.0-1_amd64.deb | grep Depends
 Depends: (空的，或只有 misc:Depends)

$ ldd /usr/bin/mygoapp
	not a dynamic executable   ← 靜態連結
```

## 三種語言比較表

| 項目 | C | Python | Go |
|-----|---|--------|-----|
| `Architecture` | `any` | `all`（純 .py）/ `any`（有 C ext） | `any` |
| Build-Depends 額外 | cmake 或 make | `dh-python`, `python3-all` | `golang-go` |
| rules `--with` | 無 | `--with python3` | 無（或 `--with golang`） |
| `Depends` 特殊 | `${shlibs:Depends}` | `${python3:Depends}` | 通常為空 |
| 安裝路徑 | `/usr/bin/` | `/usr/lib/python3/dist-packages/` | `/usr/bin/` |
| 網路依賴問題 | 無 | 無 | 需要 vendor |

## 混合語言：Python C Extension

```
mysqlclient-1.0/
├── setup.py
├── _mysql.c     ← C extension
└── debian/
    └── control
```

```
Package: python3-mysqlclient
Architecture: any          ← 因為有 .c 要編譯
Depends: libmariadb3,      ← 動態連結的 C library
         ${python3:Depends},
         ${shlibs:Depends},
         ${misc:Depends}
```

## 動手練習

為你自己的腳本語言工具建立最小可打包的 debian/ 目錄：

```bash
# 選一個你有的 Python 或 Go 小工具
cd mytool-1.0/

# 快速建立 debian/ 骨架
dh_make --single --native --email you@example.com

# 修改 debian/control：
#   - 加上正確的 Build-Depends
#   - 設對 Architecture

# 嘗試 build
dpkg-buildpackage -us -uc -b

# 查看依賴是否正確填入
dpkg-deb -f ../mytool_1.0-1_*.deb Depends
```

## 自我檢核

- [ ] C：`Architecture: any`，`${shlibs:Depends}`，不需額外 add-on
- [ ] Python：`--with python3`，`dh-python` 填入 `${python3:Depends}`；純 .py 用 `all`
- [ ] Go：靜態連結 → 不需要 `${shlibs:Depends}`；離線 build 需 vendor 或 dh-golang
- [ ] pyproject.toml 需要 `pybuild-plugin-pyproject` + `--buildsystem=pybuild`
- [ ] C extension Python 套件 `Architecture: any` 且同時需要 `${shlibs:Depends}` + `${python3:Depends}`

→ [Ch 22 lintian 靜態分析](./22-lintian.md)
