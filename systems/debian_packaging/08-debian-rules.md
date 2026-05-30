# Ch 8 — debian/rules：建置腳本

> **目標**：理解 `debian/rules` 本質是個 Makefile、dh sequencer 如何把 build 拆成標準化的階段、`dh $@` 一行背後跑了什麼、以及如何用 `override_dh_*` 客製化特定步驟。

> **環境**：debhelper 13、make 4.x。`dh` sequencer 是現代打包的核心，本章以它為主。

## 為什麼 rules 是個 Makefile？

`debian/control` 描述「要產出什麼」，`debian/rules` 描述「怎麼從原始碼做出來」。它必須能被自動化呼叫（build farm 對所有架構跑同一個 rules），所以選了 make——一個成熟、到處都有的自動化工具。

`dpkg-buildpackage` 不直接編譯你的程式，它呼叫 `debian/rules` 的特定 target（`build`、`binary` 等），由 rules 負責實際的編譯與打包。理解 rules 就是理解 build 流程的中樞。

## 先建立直覺：rules 的標準 target

```
dpkg-buildpackage 依序呼叫 debian/rules 的這些 target：

  debian/rules clean          ← 清理上次 build 的產物
  debian/rules build          ← 編譯（./configure && make）
  debian/rules binary         ← 把編譯結果打包成 .deb
       │
       └── binary 內部又分：
           binary-arch         ← 架構相關套件（含 binary）
           binary-indep        ← 架構無關套件（all）
```

這些 target 名稱是**約定**——`dpkg-buildpackage` 一定會呼叫它們。你的 `rules` 必須提供這些 target。問題是：自己手寫這些 target 的完整邏輯（解開、configure、make、install 到暫存目錄、設權限、生成 control...）非常繁瑣且重複。這就是 debhelper 和 `dh` 出現的原因。

## 三代 rules 的演進

理解 `dh` 的價值，先看它取代了什麼。

### 第一代：手寫所有東西（古老，別寫）

```makefile
#!/usr/bin/make -f
build:
	./configure --prefix=/usr
	make

binary: build
	mkdir -p debian/foo/usr
	make install DESTDIR=$(CURDIR)/debian/foo
	# 手動設權限、strip、壓縮 man page、生成 md5sums...
	# 手動 dpkg-gencontrol、dpkg-deb --build...
	# 幾十行重複的樣板
```

每個套件都重寫這堆樣板，又臭又長又容易出錯。

### 第二代：debhelper 工具（2000 年代）

debhelper 提供一堆 `dh_*` 小工具，每個做一件標準化的事：

```makefile
#!/usr/bin/make -f
build:
	dh_testdir
	./configure --prefix=/usr
	make

binary: build
	dh_testroot
	dh_installdirs
	dh_install
	dh_installdocs
	dh_installman
	dh_strip
	dh_compress
	dh_fixperms
	dh_makeshlibs
	dh_shlibdeps
	dh_gencontrol
	dh_md5sums
	dh_builddeb
```

好多了——每個 `dh_*` 是經過驗證的標準操作。但你還是要手動列出並排序這幾十個工具，順序錯了會出問題。

### 第三代：dh sequencer（現代，就用這個）

`dh` 是個「meta 工具」，它知道標準的 `dh_*` 呼叫順序，自動幫你跑：

```makefile
#!/usr/bin/make -f
%:
	dh $@
```

**就這三行**。`%:` 是 make 的 pattern rule（匹配任何 target），`dh $@` 把 target 名（`$@`）傳給 `dh`，`dh` 自動執行該 target 對應的整串 `dh_*`。

`dpkg-buildpackage` 呼叫 `debian/rules build` → make 匹配 `%:` → 執行 `dh build` → dh 自動跑 `dh_auto_configure`、`dh_auto_build` 等。

## dh 的 sequence：它到底跑了什麼

`dh` 把 build 分成幾個 sequence（對應 rules 的 target），每個 sequence 是一串有序的 `dh_*`。看實際順序：

```bash
# 看 dh build 會跑哪些 dh_*（--no-act = 只列出不執行）
dh build --no-act
#   dh_update_autotools_config
#   dh_autoreconf
#   dh_auto_configure       ← 偵測 build 系統並 configure
#   dh_auto_build           ← make / cargo build / python setup.py build...
#   dh_auto_test            ← 跑 upstream 測試

dh binary --no-act
#   dh_testroot
#   dh_prep
#   dh_auto_install         ← make install DESTDIR=...
#   dh_install              ← 把檔案分配到各 binary package
#   dh_installdocs
#   dh_installchangelogs
#   dh_installman
#   dh_strip                ← 分離 debug symbols
#   dh_compress             ← 壓縮 man pages 等
#   dh_fixperms             ← 修正檔案權限
#   dh_makeshlibs           ← 生成 shlibs 資訊
#   dh_shlibdeps            ← 計算 ${shlibs:Depends}
#   dh_gencontrol           ← 生成最終 control
#   dh_md5sums
#   dh_builddeb             ← 組裝 .deb
```

這就是 `dh $@` 一行背後的全部。每個 `dh_*` 是個獨立工具，做一件明確的事。

## dh_auto_* 的智慧

最神奇的是 `dh_auto_*` 系列——它們**自動偵測** upstream 用什麼 build 系統：

```
dh_auto_configure 偵測：
  有 configure（autotools）  → ./configure --prefix=/usr ...（一堆標準參數）
  有 CMakeLists.txt          → cmake -DCMAKE_INSTALL_PREFIX=/usr ...
  有 meson.build             → meson setup ...
  有 setup.py / pyproject    → （配合 pybuild）
  有 Cargo.toml              → （配合 dh-cargo）

dh_auto_build 偵測：
  Makefile  → make
  CMake     → cmake --build / ninja
  ...

dh_auto_install:
  → make install DESTDIR=debian/<pkg>（或對應的 install 指令）
```

這就是為什麼一個標準 autotools 專案的 `rules` 可以只有 `dh $@` 三行——dh 自動認出它、用正確的標準參數 configure、make、install。

## override_dh_*：客製化單一步驟

當預設行為不對時，你 override 特定 `dh_*`，其他保持自動：

```makefile
#!/usr/bin/make -f
%:
	dh $@

# 覆寫 configure 步驟：加自訂參數
override_dh_auto_configure:
	dh_auto_configure -- --enable-foo --disable-bar
	#                  ↑ -- 後面的傳給底層的 ./configure

# 覆寫 test：跳過測試（如果測試在 build 環境跑不了）
override_dh_auto_test:
	# 什麼都不做 = 跳過測試
	# 或：dh_auto_test || true  （測試失敗不中止 build，慎用）

# build 後做額外清理
override_dh_auto_install:
	dh_auto_install
	# 移除不想打包的檔案
	rm -f debian/foo/usr/lib/*.la
```

`override_dh_X:` 的規則：定義了它，`dh` 在輪到 `dh_X` 時就執行你的 override 而非預設。`-- args` 把參數傳給底層 build 工具。

## 常見的 rules 模式

### 模式一：autotools，加 configure 參數

```makefile
#!/usr/bin/make -f
%:
	dh $@

override_dh_auto_configure:
	dh_auto_configure -- \
		--enable-shared \
		--with-ssl \
		--sysconfdir=/etc
```

### 模式二：跳過有問題的測試

```makefile
#!/usr/bin/make -f
%:
	dh $@

override_dh_auto_test:
	# upstream 測試需要網路，build 環境沒有，跳過
	:
```

### 模式三：build 後刪除不要的檔案

```makefile
#!/usr/bin/make -f
%:
	dh $@

execute_after_dh_auto_install:
	# dh 13+ 的語法：在某個步驟「之後」插入動作
	# 比 override 更簡潔（不用重複呼叫 dh_auto_install）
	find debian/ -name '*.la' -delete
```

> debhelper 13 引入 `execute_before_dh_X` / `execute_after_dh_X`——比 override 更簡潔。如果你只是想在某步驟前/後加動作（而非完全取代），用這個，不用重新呼叫原 `dh_X`。

## 傳遞編譯選項：dpkg-buildflags

Debian 要求所有套件用標準的 hardening 編譯選項（stack protector、PIE、RELRO 等）。`dh_auto_*` 會自動套用，但如果你手動編譯（override），要自己引入：

```makefile
override_dh_auto_build:
	# 引入 Debian 標準編譯 flag（hardening 等）
	dh_auto_build -- CFLAGS="$$(dpkg-buildflags --get CFLAGS)"
	# 通常不用手動做——dh_auto_build 已經自動套用
```

```bash
# 看 Debian 標準 build flags
dpkg-buildflags
# CFLAGS=-g -O2 -fstack-protector-strong -Wformat ...
# LDFLAGS=-Wl,-z,relro ...
```

> 用 `dh_auto_*` 的好處之一：它自動套用 `dpkg-buildflags` 的 hardening 選項。手寫 build 命令很容易漏掉這些，lintian 會抱怨「hardening missing」。能用 `dh_auto_build` 就別手寫 make。

## 故意弄壞：忘記 rules 可執行權限

```bash
# rules 必須可執行（它是個 script）
chmod -x debian/rules
dpkg-buildpackage -us -uc -b
# dpkg-buildpackage: error: debian/rules build subprocess returned exit status 126
# /bin/sh: debian/rules: Permission denied
```

`debian/rules` 開頭的 `#!/usr/bin/make -f` 讓它能被直接執行，但前提是有執行權限。`chmod +x debian/rules` 修復。git clone 來的套件偶爾會掉這個權限。

## 踩雷集錦

1. **rules 用空格縮排**：make 的 recipe **必須用 Tab 縮排**，不能用空格。用空格會報 `missing separator`。這是 make 的硬規則，編輯器設好 Tab

2. **override 裡忘記呼叫原本的 dh_X**：`override_dh_auto_test:` 後面留空 = 完全跳過測試。如果你想「先跑測試再做別的」，要在 override 裡明確呼叫 `dh_auto_test`，否則它不會跑

3. **rules 不可執行**：如上，`chmod +x debian/rules`

4. **手寫 make 而不用 dh_auto_build**：失去自動的 hardening flags、parallel build、cross-compile 支援。能用 `dh_auto_*` 就別手寫

5. **`override_dh_auto_test` 跳過所有測試圖省事**：測試是品質保證的一環。跳過要有正當理由（測試需要網路/特殊硬體）並寫註解說明，不是因為「測試 fail 很煩」

6. **以為 `dh $@` 是魔法不可理解**：它不是魔法。`dh build --no-act` 列出它跑什麼。任何時候都能用這招看穿 dh 的行為

## 進階：自訂 dh sequence 與 addon

`dh` 可以載入 addon 擴充它的 sequence：

```makefile
#!/usr/bin/make -f
%:
	dh $@ --with python3,systemd --buildsystem=pybuild
#         ─────────┬────────── ──────────┬──────────
#         載入 addon            指定 build 系統
```

- `--with python3`：載入 dh-python，在 sequence 裡插入 Python 相關的 `dh_python3` 等步驟
- `--with systemd`：插入 systemd unit 處理（新版 debhelper 預設已含，不用顯式加）
- `--buildsystem=pybuild`：強制用 pybuild（Python 套件，Ch 27）

addon 機制讓 `dh` 能擴充支援各種語言生態（Go 用 `dh-golang`、Python 用 `dh-python`、Perl、Ruby...）。Ch 27/28 會用到。

你也能寫自己的 dh addon（Perl module），在 sequence 裡插入自訂步驟，但這是進階中的進階，一般打包用不到。

## 動手練習

1. 找一個套件的 `debian/rules`，跑 `dh build --no-act` 和 `dh binary --no-act`（在 source 目錄裡），看完整的 `dh_*` 序列。挑三個你不認識的 `dh_*`，用 `man dh_xxx` 查它做什麼

2. 找一個有 `override_dh_*` 的套件（很多複雜套件有），看它 override 了什麼、為什麼。`apt source systemd` 的 rules 是個好例子（很複雜）

3. 故意弄壞：`chmod -x debian/rules` 再 build，看錯誤。改回來。再把某個 recipe 行的 Tab 換成空格，看 `missing separator` 錯誤

4. 在一個簡單套件的 rules 加 `override_dh_auto_test:` 留空跳過測試，build 看測試是否真的被跳過（對照沒 override 時測試會跑）

## 本章重點整理

- `debian/rules` 是個 Makefile，提供 `clean`/`build`/`binary` 等標準 target 給 dpkg-buildpackage 呼叫
- 現代 rules 用 `dh $@` 三行；`dh` sequencer 自動執行標準的 `dh_*` 序列
- `dh_auto_*` 自動偵測 build 系統（autotools/cmake/meson/...）並用標準參數
- `override_dh_X:` 客製化單一步驟；debhelper 13 的 `execute_before/after_dh_X` 更簡潔
- `dh build --no-act` 是看穿 dh 行為的工具，dh 不是黑盒子

## 自我檢核

- [ ] 能解釋 `debian/rules` 為什麼是個 Makefile，以及 dpkg-buildpackage 呼叫它的哪些 target
- [ ] 不看筆記，能說出 `dh $@` 這一行的 make 語法（`%:` pattern + `$@`）
- [ ] 知道怎麼看 `dh build` 實際跑哪些 `dh_*`（--no-act）
- [ ] 能寫一個 override 給 configure 加自訂參數（`dh_auto_configure -- ...`）
- [ ] 知道 rules 的 recipe 為什麼必須用 Tab 縮排

## 延伸閱讀

### 官方文件

- **[dh(1) man page](https://manpages.debian.org/bookworm/debhelper/dh.1.html)**
  - **讀哪裡**:「SEQUENCES」和「OVERRIDE AND HOOK TARGETS」兩節
  - **學什麼**：dh 的完整 sequence 定義、override/hook 的所有形式；本章的核心參考
  - **前提**：讀完本章

- **[debhelper(7) man page](https://manpages.debian.org/bookworm/debhelper/debhelper.7.html)**
  - **讀哪裡**：開頭的 overview 和 compat level 說明
  - **學什麼**：debhelper 的整體設計哲學、所有 `dh_*` 工具的索引
  - **前提**：無

### 部落格 / 文章

- **[Debian rules and debhelper (Joey Hess)](https://joeyh.name/blog/)** — Joey Hess（debhelper 原作者）
  - **這篇說什麼**：debhelper 設計者的視角，為什麼從手寫 rules 演進到 dh sequencer
  - **讀哪裡**：搜尋他 blog 關於 debhelper、dh 的文章
  - **為什麼值得讀**：來自工具創造者本人，講設計動機沒有比這更權威的

→ [Ch 9 debian/changelog 與版本號](./09-changelog-versioning.md)
