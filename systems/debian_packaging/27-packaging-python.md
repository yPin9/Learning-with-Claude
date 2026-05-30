# Ch 27 — 打包 Python 套件

> **目標**：理解如何用 `dh-python` + `pybuild` 把 Python 專案打包成 `.deb`、Debian 的 Python 套件命名與佈局慣例、為什麼 Debian 不用 pip/venv 而要打包、以及 Python 特有的依賴處理。

> **環境**：dh-python、pybuild、Python 3.11（Debian 12）。本章假設你會基本 Python 打包概念（setup.py / pyproject.toml）。

## 為什麼把 Python 打包成 .deb？「pip install 不就好了？」

這是最常見的疑問。pip/venv 適合**應用層**（你的專案的依賴），但系統層的 Python 工具和 library 需要 `.deb`：

```
為什麼系統 Python 套件要 .deb 而非 pip：
  - 系統工具用的 library（如 apt 本身用 python3-apt）
    必須和系統一致、被套件管理、能安全更新
  - 多個系統工具共享同一個 library → 統一管理避免衝突
  - 安全更新：library 有 CVE，apt 統一更新所有依賴它的東西
    （pip 裝的散落各處，無法統一更新）
  - 不污染系統 Python（pip install 到系統會搞亂 dpkg 管理的檔案）
```

> Debian 12 之後，系統 Python 預設**禁止** `pip install` 直接裝到系統（PEP 668，"externally-managed-environment"）。系統 Python 由 dpkg 管，pip 要裝就到 venv。這強化了「系統 Python library 用 .deb，應用依賴用 venv」的分工。

## 先建立直覺：pybuild 自動處理 Python build 系統

```
Python 專案（setup.py / pyproject.toml）
        │
   dh $@ --buildsystem=pybuild
        │  pybuild 偵測 build backend：
        │    setuptools / flit / poetry / hatchling...
        ▼
   pybuild 自動：
     - 為每個支援的 Python 版本 build
     - 把 .py 裝到正確的 dist-packages 路徑
     - 處理 entry_points（命令列工具）
        │
   產出 python3-foo（library）和/或 foo（CLI 工具）
```

`pybuild` 是 dh-python 的核心——它認得各種 Python build backend（setuptools、flit、poetry...），用統一流程處理。你不用管 upstream 用哪個 backend。

## Python 套件的命名慣例

```
Python library 套件名 = python3- + 模組名
  import requests  → 套件 python3-requests
  import yaml       → 套件 python3-yaml

CLI 工具套件名 = 工具名（不加 python3- 前綴）
  /usr/bin/black    → 套件 black（不是 python3-black）

注意：
  python3-foo  = 給其他 Python 程式 import 的 library
  foo          = 給使用者執行的命令列工具
  （一個專案可能同時產出兩者）
```

> 命名規則反映用途：`python3-` 前綴 = 「這是給 Python import 的 library」；無前綴 = 「這是給人執行的工具」。一個專案如果既是 library 又有 CLI（如 `black`），可能拆成 `python3-black`（library）+ `black`（CLI），或合併。

## 最小 Python 套件範例

upstream（`pyfoo/`，用 pyproject.toml）：

```
pyfoo/
├── pyproject.toml
├── src/pyfoo/__init__.py
└── src/pyfoo/cli.py
```

`pyproject.toml`：
```toml
[project]
name = "pyfoo"
version = "1.0.0"
dependencies = ["requests"]

[project.scripts]
pyfoo = "pyfoo.cli:main"

[build-system]
requires = ["setuptools"]
build-backend = "setuptools.build_meta"
```

`debian/control`：
```
Source: pyfoo
Section: python
Priority: optional
Maintainer: Your Name <you@example.com>
Build-Depends:
 debhelper-compat (= 13),
 dh-python,
 python3-all,
 python3-setuptools,
 python3-requests <!nocheck>,
Standards-Version: 4.6.2
Rules-Requires-Root: no

Package: python3-pyfoo
Architecture: all
Depends:
 ${python3:Depends},
 ${misc:Depends},
Description: example Python library
 A demonstration Python library packaged for Debian.

Package: pyfoo
Architecture: all
Depends:
 python3-pyfoo (= ${binary:Version}),
 ${python3:Depends},
 ${misc:Depends},
Description: command-line tool using pyfoo
 The command-line front-end for the pyfoo library.
```

`debian/rules`：
```makefile
#!/usr/bin/make -f
export PYBUILD_NAME=pyfoo
%:
	dh $@ --buildsystem=pybuild
```

`debian/control` 的關鍵：
- `Build-Depends: dh-python, python3-all`（dh-python 提供 pybuild，python3-all 支援 build 所有 Python 版本）
- `${python3:Depends}`：由 dh-python 自動填入 Python runtime 依賴（類似 `${shlibs:Depends}` 之於 C）
- `Architecture: all`：純 Python 是架構無關（除非含 C extension，見下）

## ${python3:Depends}：自動 Python 依賴

```
${python3:Depends} 由 dh_python3 自動計算，包含：
  - python3 (>= 3.x)：需要的 Python 版本
  - 從 pyproject.toml/setup.py 的 install_requires 推導的依賴
    （如 requests → python3-requests）
```

> dh_python3 會把 upstream 宣告的 Python 依賴（`requests`）對應到 Debian 套件名（`python3-requests`）。但這個對應不總是完美——upstream 的 PyPI 套件名和 Debian 套件名可能不同，或 Debian 沒有對應套件。你可能要在 `debian/control` 手動補 `Depends` 或用 `debian/py3dist-overrides` 調整對應。

## C extension：架構相關的 Python 套件

純 Python 是 `Architecture: all`。但如果套件含 C extension（如 numpy、lxml 用 C 加速）：

```
含 C extension 的 Python 套件：
  Architecture: any        ← 不是 all！每個架構各自編 C 部分
  Build-Depends: ..., python3-dev   ← 需要 Python 的 C headers
  Depends: ${python3:Depends}, ${shlibs:Depends}  ← 同時有 Python 和 C 依賴
```

C extension 編出的是 `.so`（如 `_foo.cpython-311-x86_64-linux-gnu.so`），所以是架構相關，`Architecture: any`，且要 `${shlibs:Depends}`（C 部分的 library 依賴）。

## 多 Python 版本支援

Debian 同時可能有多個 Python 3 版本（過渡期）。`python3-all` 讓 pybuild 為所有版本 build：

```
Build-Depends: python3-all   ← build 時為每個 Python 3.x 版本各 build 一次
        │
  純 Python 套件通常裝到版本無關路徑（一份服務所有版本）
  C extension 套件為每個版本各編一個 .so
```

```bash
# 看系統支援的 Python 版本
py3versions -s   # supported
py3versions -d   # default
```

## 故意弄壞：用了 pip 的依賴格式 / 架構標錯

```bash
# 錯誤一：純 Python 標 Architecture: any
# Package: python3-pyfoo
# Architecture: any        ← 錯！純 Python 是 all
# 後果：為每個架構各 build 一份相同的純 Python（浪費），lintian 警告

# 錯誤二：忘記 dh-python
# Build-Depends 沒有 dh-python
dpkg-buildpackage -b
# dh: --buildsystem=pybuild 但找不到 pybuild
#   → 需要 dh-python 提供 pybuild

# 錯誤三：依賴對應錯誤
# upstream install_requires = ["PyYAML"]
# dh_python3 找 python3-pyyaml（對）
# 但如果 upstream 寫 ["yaml"]（PyPI 上不存在的名字）→ 對應失敗
```

教訓：純 Python `Architecture: all`、含 C extension 才 `any`；一定要 `Build-Depends: dh-python`；注意 PyPI 名和 Debian 套件名的對應。

## 踩雷集錦

1. **純 Python 標 `Architecture: any`**：純 Python 架構無關，應該 `all`。標 any 會為每架構重複 build 相同內容

2. **含 C extension 標 `Architecture: all`**：C extension 是架構相關，必須 `any`，否則 build farm 不會為各架構編 C 部分

3. **忘記 `dh-python` 或 `--buildsystem=pybuild`**：pybuild 來自 dh-python。沒有它，Python build 不會被正確處理

4. **PyPI 名 vs Debian 名不對應**：upstream 的依賴名（PyPI）和 Debian 套件名可能不同。dh_python3 多數能對應，少數要手動或用 `py3dist-overrides`

5. **試圖在系統 Python 用 pip 裝依賴**：Debian 12+ 禁止（PEP 668）。Build-Depends 要列 Debian 的 `python3-*` 套件，不是靠 pip

6. **entry_points/scripts 沒正確處理**：pyproject.toml 的 `[project.scripts]` 定義的 CLI 工具，pybuild 會生成 `/usr/bin/` 的 wrapper。如果沒出現，檢查 pybuild 是否認得你的 build backend

## 進階：pybuild 的 plugin 與測試整合

pybuild 支援多種 build backend，透過 plugin 機制：

```bash
# pybuild 支援的 build 系統
PYBUILD_SYSTEM=distutils   # setup.py（傳統）
PYBUILD_SYSTEM=pyproject   # pyproject.toml（PEP 517，現代）
PYBUILD_SYSTEM=flit
PYBUILD_SYSTEM=custom      # 自訂

# 在 rules 指定（pybuild 通常能自動偵測）
export PYBUILD_SYSTEM=pyproject
```

**測試整合**：pybuild 能跑 upstream 的 Python 測試（pytest/unittest）：

```makefile
# rules — pybuild 自動跑 pytest（如果偵測到）
export PYBUILD_TEST_PYTEST=1
# 或自訂測試命令
export PYBUILD_TEST_ARGS=--verbose

%:
	dh $@ --buildsystem=pybuild
```

pybuild 會為每個 Python 版本跑測試——這在 `dh_auto_test` 階段，確保套件在所有支援的 Python 版本都能用。配合 `<!nocheck>` 標記測試依賴（Ch 13）。

> Python 套件的測試特別重要——Python 是動態語言，編譯期抓不到的錯誤要靠測試。pybuild 為每個 Python 版本跑測試，加上 autopkgtest（Ch 17）測安裝後可 import，是 Python 套件品質的雙保險。

## 動手練習

1. 打包一個簡單的純 Python library + CLI（用 pyproject.toml），確認 `python3-pyfoo`（all）和 `pyfoo`（CLI）正確產出，`${python3:Depends}` 自動填入

2. 看一個真實 Python 套件的打包：`apt source python3-requests`，看它的 control（命名、依賴）、rules（pybuild 設定）

3. 故意標錯架構：把純 Python 套件標 `Architecture: any`，build 看 lintian 警告，改回 `all`

4. 加測試：在你的 Python 套件加 pytest 測試，設 `PYBUILD_TEST_PYTEST=1`，看 pybuild 在 build 時跑測試

## 本章重點整理

- 系統 Python library/工具用 `.deb`（被套件管理、能安全更新）；應用依賴用 venv（Debian 12+ 禁止 pip 裝系統）
- `dh-python` 提供 pybuild，自動處理各種 Python build backend；`${python3:Depends}` 自動算依賴
- 命名：`python3-foo`（library）/ `foo`（CLI）；純 Python `Architecture: all`，含 C extension `any`
- `python3-all` 讓 pybuild 為所有 Python 版本 build；pybuild 能跑 upstream 測試
- 注意 PyPI 名和 Debian 套件名的對應

## 自我檢核

- [ ] 能解釋為什麼系統 Python library 要打包成 .deb 而非 pip install
- [ ] 知道 `python3-foo` 和 `foo` 的命名差別（library vs CLI）
- [ ] 能判斷一個 Python 套件該標 `all` 還是 `any`（純 Python vs C extension）
- [ ] 知道 `${python3:Depends}` 的作用（類比 `${shlibs:Depends}`）
- [ ] 知道 Debian 12 的 PEP 668 對系統 pip 的限制

## 延伸閱讀

### 官方文件

- **[Debian Python Policy](https://www.debian.org/doc/packaging-manuals/python-policy/)**
  - **讀哪裡**：命名、佈局、依賴那幾節
  - **學什麼**：Python 套件的 Debian 規範；本章是教學版
  - **前提**：讀完本章

- **[dh_python3(1) man page](https://manpages.debian.org/bookworm/dh-python/dh_python3.1.html)** 和 **[pybuild(1)](https://manpages.debian.org/bookworm/dh-python/pybuild.1.html)**
  - **讀哪裡**：pybuild 的 build system 偵測和 PYBUILD_* 環境變數
  - **學什麼**：pybuild 的完整選項、測試整合
  - **前提**：本章

### 部落格 / 文章

- **[Python/LibraryStyleGuide (Debian Wiki)](https://wiki.debian.org/Python/LibraryStyleGuide)**
  - **這篇說什麼**：Python library 打包的風格指南和常見模式
  - **讀哪裡**：整頁
  - **為什麼值得讀**：補足 Policy 沒講的實務慣例

→ [Ch 28 打包 Go 程式](./28-packaging-go.md)
