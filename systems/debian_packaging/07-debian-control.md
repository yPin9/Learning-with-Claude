# Ch 7 — debian/control：套件 metadata

> **目標**：徹底理解 `debian/control` 的結構（source stanza + 多個 binary stanza）、每個關鍵欄位的語意、依賴關係的版本約束語法、`${...}` 替換變數、以及為什麼一個 source 能產出多個 binary package。

> **環境**：dpkg-dev 1.21.x、debhelper 13。欄位語意由 Debian Policy §5 定義。

## 為什麼 control 是 debian/ 目錄的心臟？

`debian/control` 回答打包最核心的問題：**這個 source 要產出哪些套件？每個套件叫什麼、依賴什麼、屬於哪個分類？** apt 的依賴解析、dpkg 的安裝決策、archive 的分類管理，全都讀這個檔案生成的 metadata。

寫錯 `control` 的後果很具體：依賴漏宣告 → 套件在乾淨系統裝不起來；版本約束寫錯 → apt 拒絕安裝或裝了不相容版本；忘記宣告 Build-Depends → build 在 sbuild 裡失敗。這章把每個欄位講清楚。

## 先建立直覺：一個 source，多個 binary

```
debian/control 的雙層結構：

┌─── Source stanza（一個）────────────────────┐
│  Source: foo                                 │
│  Build-Depends: ...    ← build 整個 source   │
│  Maintainer: ...          需要什麼            │
└──────────────────────────────────────────────┘
        │ 這個 source 產出 ↓
┌─── Binary stanza #1 ──────┐ ┌─ Binary stanza #2 ─┐
│  Package: foo             │ │ Package: libfoo1    │
│  Depends: libfoo1 ...     │ │ Depends: libc6 ...  │
│  （執行檔套件）            │ │ （runtime library）  │
└───────────────────────────┘ └─────────────────────┘
                              ┌─ Binary stanza #3 ──┐
                              │ Package: libfoo-dev │
                              │ Depends: libfoo1    │
                              │ （開發用標頭+靜態庫） │
                              └─────────────────────┘
```

一個 upstream 專案（如 libfoo）常拆成多個 binary package：執行檔、runtime library、開發檔（headers）、文件。使用者只裝需要的——跑程式的人裝 `libfoo1`，開發的人才裝 `libfoo-dev`。這個拆分在 `debian/control` 用多個 binary stanza 表達。

## control 的結構

stanza 之間用**空行**分隔，欄位是 `Key: value` 格式（RFC822 風格）。

```
Source: hello
Section: devel
Priority: optional
Maintainer: Santiago Vila <sanvila@debian.org>
Build-Depends: debhelper-compat (= 13)
Standards-Version: 4.6.2
Homepage: https://www.gnu.org/software/hello/
Rules-Requires-Root: no

Package: hello
Architecture: any
Depends: ${shlibs:Depends}, ${misc:Depends}
Description: example package based on GNU hello
 The GNU hello program produces a familiar, friendly greeting.
 .
 Seriously, though: this is an example of how to do a Debian package.
```

第一個 stanza 沒有 `Package:` 欄位但有 `Source:`——這是 source stanza。之後每個有 `Package:` 的是 binary stanza。

## Source stanza 的關鍵欄位

| 欄位 | 意義 |
|---|---|
| `Source` | source package 名稱 |
| `Maintainer` | 維護者（一個）|
| `Uploaders` | 共同維護者（多個，team 維護常用）|
| `Build-Depends` | **build 時**需要的套件（編譯器、library headers、debhelper）|
| `Build-Depends-Indep` | 只在 build 架構無關套件時需要（如文件生成工具）|
| `Standards-Version` | 遵守的 Debian Policy 版本 |
| `Section` | 分類（devel/libs/net/...）|
| `Priority` | 重要性（required/important/standard/optional）|
| `Homepage` | upstream 網站 |
| `Vcs-Git` / `Vcs-Browser` | 打包的版本控制位置 |
| `Rules-Requires-Root` | build 是否需要 root（`no` 是現代推薦，加速 build）|

> **Build-Depends vs Depends 是最重要的區分**。`Build-Depends`（在 source stanza）是**編譯時**要的：`gcc`、`libssl-dev`（含 headers）。`Depends`（在 binary stanza）是**執行時**要的：`libssl3`（只有 `.so`）。新手最常犯的錯是把 build 依賴和 runtime 依賴搞混。

## Binary stanza 的關鍵欄位

| 欄位 | 意義 |
|---|---|
| `Package` | binary package 名稱 |
| `Architecture` | `any`（每個架構各自編）/ `all`（架構無關）|
| `Depends` | runtime 硬依賴 |
| `Recommends` | 強建議（預設一起裝）|
| `Suggests` | 弱建議（不自動裝）|
| `Conflicts` / `Breaks` | 不能/會破壞共存 |
| `Provides` | 提供虛擬套件 |
| `Replaces` | 取代對方的檔案 |
| `Description` | 描述（首行 synopsis + 縮排長描述）|
| `Multi-Arch` | 多架構共存策略（Ch 18）|

### Architecture: any vs all

```
Architecture: any   → 含編譯出的 binary，每個 CPU 架構各自 build
                      （foo_1.0-1_amd64.deb, foo_1.0-1_arm64.deb...）
Architecture: all   → 架構無關（純 script、文件、設定）
                      （foo_1.0-1_all.deb，一份到處用）
```

判斷：套件裡有沒有編譯出的執行檔/library？有 → `any`；純 shell/Python/文件 → `all`。

## 依賴的版本約束語法

```
Depends: libssl3 (>= 3.0.0), libc6 (>= 2.34), foo | bar
         ────┬───  ────┬────                  ──┬──
         套件名      版本約束                  替代（or）
```

版本約束運算子：

| 運算子 | 意義 | 範例 |
|---|---|---|
| `(>= 1.0)` | 大於等於 | 最常用，「至少這個版本」 |
| `(<< 2.0)` | 嚴格小於 | 注意是 `<<` 不是 `<` |
| `(>> 1.0)` | 嚴格大於 | 注意是 `>>` |
| `(= 1.0-1)` | 精確等於 | 綁死版本（library 套件常用）|
| `(<= 1.5)` | 小於等於 | 較少用 |

> 為什麼是 `<<` 和 `>>` 不是 `<` `>`？因為 `<` `>` 在早期 dpkg 有歧義（曾被解讀成 `<=` `>=`），為了明確改用雙字元。寫單字元 dpkg 會警告。

替代依賴用 `|`：`foo | bar` 表示「foo 或 bar 任一即可」。常用於虛擬套件：`Depends: mail-transport-agent` 或更明確 `Depends: postfix | mail-transport-agent`（偏好 postfix，但任何 MTA 都行）。

## Provides / Conflicts / Replaces / Breaks 的組合

這四個常一起出現，理解它們的配合很重要：

```
情境：套件 foo 取代了舊套件 old-foo，且它們有檔案衝突

Package: foo
Provides: old-foo       ← foo 滿足任何 "Depends: old-foo"
Conflicts: old-foo      ← 但不能和 old-foo 同時裝
Replaces: old-foo       ← foo 可以覆蓋 old-foo 的檔案
```

| 欄位 | 解決什麼 |
|---|---|
| `Provides` | 「我也算是 X」——滿足對 X 的依賴（虛擬套件 / 套件改名）|
| `Conflicts` | 「我和 X 絕對不能共存」——dpkg 拒絕同時裝 |
| `Breaks` | 「X 的某些版本和我不相容」——比 Conflicts 弱，允許 unpacked 但不能同時 configured |
| `Replaces` | 「我可以接管 X 的檔案」——允許檔案覆蓋（配合 Breaks/Conflicts 用）|

> `Breaks` vs `Conflicts` 的細微差別：`Conflicts` 完全不允許共存（連暫時都不行）；`Breaks` 允許 dpkg 在升級過程暫時兩者都 unpacked，只是不能都 configured。現代打包多用 `Breaks + Replaces`（較溫和），`Conflicts` 留給真正水火不容的情況。

## 替換變數 ${...}

`control` 裡常見 `${shlibs:Depends}`、`${misc:Depends}` 這種變數，它們在 build 時被 debhelper 自動填入：

```
Depends: ${shlibs:Depends}, ${misc:Depends}
         ────────┬──────── ──────┬───────
         自動偵測的 shared       debhelper 自己
         library 依賴            需要加的依賴
```

| 變數 | 誰填 | 內容 |
|---|---|---|
| `${shlibs:Depends}` | `dpkg-shlibdeps` | 掃描 binary 連結了哪些 `.so`，自動算出對應的 library 套件依賴 |
| `${misc:Depends}` | debhelper | debhelper 的某些操作需要的額外依賴 |
| `${binary:Version}` | dpkg | 這個 binary 套件的完整版本（library 綁定用）|
| `${source:Version}` | dpkg | source 的版本 |
| `${perl:Depends}` / `${python3:Depends}` | 語言 helper | 語言 runtime 依賴 |

> `${shlibs:Depends}` 是打包的一大省力工具。你不用手寫「我依賴 libc6 (>= 2.34), libssl3...」——`dpkg-shlibdeps` 掃描你編出的 binary，看它 `ldd` 連結了哪些 `.so`，自動算出精確的依賴和版本。Ch 19 詳談這個機制。

## Description 的格式講究

```
Description: example package based on GNU hello
 The GNU hello program produces a familiar, friendly greeting.
 Yes, it is the canonical example of a GNU package.
 .
 This is the long description. The blank-looking line above
 (a single dot) separates paragraphs.
```

規則：
- 首行（和 `Description:` 同一行）是 **synopsis**——簡短一句，`apt search` 顯示這行。不要大寫開頭、不要句點結尾、不要重複套件名
- 後續行**每行開頭一個空格**（縮排），是長描述
- 段落間用 ` .`（空格+點）分隔（純空行會被當作 stanza 結束）

寫不好的 Description 是 lintian 最常抱怨的地方之一。

## 完整範例：一個 library 套件的 control

```
Source: libgreet
Section: libs
Priority: optional
Maintainer: Your Name <you@example.com>
Build-Depends:
 debhelper-compat (= 13),
 libssl-dev,
Standards-Version: 4.6.2
Homepage: https://example.com/libgreet
Rules-Requires-Root: no

Package: libgreet1
Architecture: any
Multi-Arch: same
Depends: ${shlibs:Depends}, ${misc:Depends}
Description: friendly greeting library (runtime)
 libgreet provides functions for generating localized greetings.
 .
 This package contains the shared library needed to run programs
 that use libgreet.

Package: libgreet-dev
Section: libdevel
Architecture: any
Depends: libgreet1 (= ${binary:Version}), ${misc:Depends}
Description: friendly greeting library (development files)
 libgreet provides functions for generating localized greetings.
 .
 This package contains the header files and static library needed
 to develop programs that use libgreet.

Package: greet
Architecture: any
Depends: ${shlibs:Depends}, ${misc:Depends}
Description: command-line greeting tool
 A small command-line program that prints localized greetings,
 built on top of libgreet.
```

注意設計決策：
- `libgreet-dev` 用 `(= ${binary:Version})` **精確綁定** runtime library 的版本——開發用的 header 必須和 runtime 完全對應
- runtime library 套件名是 `libgreet1`（含 SONAME 版本號 `1`），dev 套件是 `libgreet-dev`（無版本號）。這是 library 命名慣例（Ch 26）
- `libgreet1` 標 `Multi-Arch: same`（同一個 library 多架構可共存，Ch 18）

## 踩雷集錦

1. **Build-Depends 和 Depends 搞混**：把 `libssl-dev`（build 用，含 headers）寫進 binary 的 `Depends`，或把 `libssl3`（runtime）寫進 `Build-Depends`。記住：`-dev` 套件幾乎只出現在 Build-Depends

2. **手寫 library 依賴而不用 `${shlibs:Depends}`**：你硬寫 `Depends: libc6 (>= 2.34)`，但實際依賴的 library 版本會隨 build 環境變。讓 `${shlibs:Depends}` 自動算才準確

3. **用 `<` `>` 而非 `<<` `>>`**：dpkg 會警告。版本約束的嚴格大小於必須是雙字元

4. **Description synopsis 寫成完整句子**：synopsis 是「片語」不是「句子」，不要句點結尾，不要重複套件名（套件叫 `nginx` 就別寫 "nginx web server"，寫 "small, powerful web server"）

5. **dev 套件沒精確綁定 runtime 版本**：`libfoo-dev` 應該 `Depends: libfoo1 (= ${binary:Version})`。寫成 `(>= ...)` 可能裝到不對應的 runtime，header 和 .so 不匹配導致詭異 bug

6. **stanza 間用了多個空行或忘記空行**：stanza 嚴格用單一空行分隔。長描述裡的「空行」必須是 ` .`（空格+點），純空行會被當成 stanza 結束導致解析錯亂

## 進階：control 的 substvars 與生成流程

`control` 其實是個**模板**。build 時 dpkg-gencontrol 讀它，把 `${...}` 變數替換，生成每個 binary package 的真正 `control`（放進 `.deb` 的 control.tar）。

```
debian/control（模板，有 ${...}）
        │
        │ build 過程：
        │ 1. dpkg-shlibdeps 掃 binary → debian/<pkg>.substvars 寫入 shlibs:Depends
        │ 2. debhelper 寫入 misc:Depends
        │ 3. dpkg-gencontrol 讀模板 + substvars → 生成最終 control
        ▼
.deb 裡的 control（變數已替換成具體值）
```

```bash
# build 後看生成的 substvars
cat debian/libgreet1.substvars
# shlibs:Depends=libc6 (>= 2.34), libssl3 (>= 3.0.0)
# misc:Depends=

# 對照模板和最終結果
dpkg-deb -f ../libgreet1_*.deb Depends
# libc6 (>= 2.34), libssl3 (>= 3.0.0)   ← ${shlibs:Depends} 被替換了
```

理解這個生成流程，你就懂為什麼 `control` 裡寫 `${shlibs:Depends}` 而最終 `.deb` 裡是具體的 library 列表。

## 動手練習

1. `apt source` 一個拆成多個套件的專案（如 `apt source curl`，看 `libcurl4`、`libcurl4-openssl-dev`、`curl` 的 stanza），對照本章的 library 拆分模式

2. 找一個套件，對比它 `debian/control` 模板裡的 `${shlibs:Depends}` 和 `dpkg-deb -f` 顯示的最終 `Depends`，看變數被替換成什麼

3. 故意寫錯一個 `control`：用 `<` 代替 `<<`，跑 `dpkg-buildpackage` 看警告。再把 Description synopsis 寫成句點結尾，跑 lintian 看它抱怨

4. 找一個用 `Provides`/`Replaces`/`Breaks` 的套件（如某個套件改過名），看它怎麼宣告，理解這組欄位如何協作處理改名

## 本章重點整理

- `control` 雙層結構：一個 source stanza（build 資訊）+ 多個 binary stanza（每個產出一個 .deb）
- Build-Depends（編譯時，含 -dev）vs Depends（執行時，含 runtime .so）是最重要的區分
- 版本約束用 `>=` `<<` `>>` `=`（嚴格大小於是雙字元）；替代用 `|`；虛擬套件用 `Provides`
- `${shlibs:Depends}` 由 dpkg-shlibdeps 自動算 library 依賴，不要手寫
- library 拆 `libfoo1`（runtime）+ `libfoo-dev`（headers，精確綁定 runtime 版本）

## 自我檢核

- [ ] 不看筆記，能解釋 Build-Depends 和 Depends 的差別，各舉一個 `-dev` 和 runtime 的例子
- [ ] 知道 `Architecture: any` 和 `all` 何時各用哪個
- [ ] 能說出 `${shlibs:Depends}` 是誰、在什麼時候、填入什麼
- [ ] 知道為什麼 `libfoo-dev` 要 `Depends: libfoo1 (= ${binary:Version})` 而非 `(>= ...)`
- [ ] 能解釋 Breaks 和 Conflicts 的細微差別

## 延伸閱讀

### 官方文件

- **[Debian Policy §5 (Control files and their fields)](https://www.debian.org/doc/debian-policy/ch-controlfields.html)**
  - **讀哪裡**：§5.6（每個欄位的定義）整節，這是欄位語意的權威來源
  - **學什麼**：本章沒列到的次要欄位、每個欄位的精確規則
  - **前提**：讀完本章

- **[Debian Policy §7 (Declaring relationships between packages)](https://www.debian.org/doc/debian-policy/ch-relationships.html)**
  - **讀哪裡**：§7.1–7.4，依賴/衝突/虛擬套件的完整規則
  - **學什麼**：Depends/Provides/Conflicts/Breaks/Replaces 的精確語意和互動
  - **前提**：本章的依賴關係部分

### 部落格 / 文章

- **[Debian Library Packaging Guide](https://www.netfort.gr.jp/~dancer/column/libpkg-guide/libpkg-guide.html)** — Junichi Uekawa
  - **這篇說什麼**：library 套件怎麼拆（runtime/dev/dbg）、命名慣例、SONAME 處理
  - **讀哪裡**：前半的 package splitting 和 naming
  - **為什麼值得讀**：library 拆分是本章的重點之一，這份指南講得最透徹（Ch 26 會再深入）

→ [Ch 8 debian/rules：建置腳本](./08-debian-rules.md)
