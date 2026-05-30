# Ch 11 — Quilt patches 系統

> **目標**：理解為什麼 Debian 用 patch 而非直接改 upstream 程式碼、quilt 的 patch stack 模型、`debian/patches/series` 的角色、以及完整的 patch 工作流（new/add/refresh/pop/push）。

> **環境**：quilt 0.67、Format 3.0 (quilt)。本章假設你已理解 Ch 6 的 source package 格式。

## 為什麼不直接改 upstream 程式碼？

你拿到 upstream 的 `foo-1.0.tar.gz`，發現一個 bug 要修。最直覺的做法是打開 `src/foo.c` 直接改。但 Format 3.0 (quilt) **禁止**這樣做（Ch 6 我們踩過這個雷）。為什麼？

- **可追溯**：每個修改是一個獨立、有名字、有說明的 patch。別人能看到「Debian 對 upstream 改了哪 5 個地方、各自為什麼」
- **可轉發 upstream**：好的修復應該送回 upstream。獨立的 patch 能直接 `git am` 或 email 給 upstream
- **可在新版本重套**：upstream 出 1.1 時，你的 patch 試著套到新版上。能套 = 修復還需要；套不上（衝突）= 提醒你 upstream 可能已修復或程式碼變了
- **乾淨分離**：`.orig.tar` 保持 byte-for-byte 是 upstream 的，所有 Debian 修改在 `debian/patches/`，一清二楚

quilt 是管理這疊 patch 的工具。

## 先建立直覺：quilt 是一疊可推可拉的補丁

```
想像你的修改是一疊盤子（patch stack）：

   debian/patches/series（決定順序的清單）：
     fix-segfault.patch        ← 底層先套
     add-ipv6-support.patch
     fix-manpage-typo.patch    ← 最上層後套

   quilt 的操作：
     push  → 把下一個 patch「套上去」（apply）
     pop   → 把最上面的 patch「拿下來」（unapply）
     new   → 在當前位置開一個新 patch（開始記錄修改）
     refresh → 把你的修改寫進當前 patch 檔
```

quilt 維護一個「目前套到哪裡」的指標。你 push 到某個 patch、改檔案、refresh，修改就被記進那個 patch。整疊 patch 按 `series` 的順序套用，就從原始 upstream 變成 Debian 要的樣子。

## series 檔案：patch 的順序清單

```bash
cat debian/patches/series
# fix-segfault.patch
# add-ipv6-support.patch
# fix-manpage-typo.patch
```

`series` 是純文字，每行一個 patch 檔名，**順序就是套用順序**。build 時 dpkg-source 按這個順序把 patch 套到解開的 upstream 程式碼上。

## 完整工作流：新增一個 patch

設定 quilt（讓 patch 存對地方）：

```bash
# quilt 預設把 patch 放 ./patches，我們要它放 debian/patches
# 設定環境變數（寫進 ~/.quiltrc）
cat > ~/.quiltrc <<'EOF'
QUILT_PATCHES=debian/patches
QUILT_NO_DIFF_INDEX=1
QUILT_NO_DIFF_TIMESTAMPS=1
QUILT_REFRESH_ARGS="-p ab"
QUILT_DIFF_ARGS="--color=auto"
EOF
```

新增一個修復 patch 的完整流程：

```bash
# 在解開的 source 目錄裡（dpkg-source -x 之後）
cd foo-1.0/

# 1. 先把現有 patch 全部套上（如果有的話）
quilt push -a
# Applying patch fix-segfault.patch
# Now at patch fix-segfault.patch
# （如果沒有 patch，會說 "File series fully applied"）

# 2. 開一個新 patch
quilt new fix-buffer-overflow.patch
# Patch debian/patches/fix-buffer-overflow.patch is now on top

# 3. 告訴 quilt「我要改這個檔案」（quilt 先備份它）
quilt add src/parser.c
# File src/parser.c added to patch fix-buffer-overflow.patch

# 4. 現在實際編輯檔案
vim src/parser.c   # 修 bug

# 5. 把修改寫進 patch 檔
quilt refresh
# Refreshed patch debian/patches/fix-buffer-overflow.patch

# 6. 加上 patch 的說明（DEP-3 header，見下）
quilt header -e --dep3   # 開編輯器寫 metadata
```

關鍵順序：**先 `quilt add <file>` 再編輯**。`quilt add` 讓 quilt 記住檔案的原始狀態（備份），之後 `refresh` 才能 diff 出你的修改。如果先改再 add，quilt 抓不到改動。

## quilt 的核心指令

```bash
# === 查看狀態 ===
quilt series          # 列出所有 patch（series 檔的內容）
quilt applied         # 已套用的 patch
quilt unapplied       # 未套用的 patch
quilt top             # 目前最上層的 patch

# === 套用/取消 ===
quilt push            # 套用下一個 patch
quilt push -a         # 套用全部
quilt pop             # 取消最上層 patch
quilt pop -a          # 取消全部（回到原始 upstream）
quilt push fix-x.patch  # 套用到指定 patch 為止

# === 編輯 patch ===
quilt new NAME.patch  # 開新 patch
quilt add FILE        # 把檔案納入當前 patch（編輯前必做）
quilt refresh         # 把修改寫進當前 patch
quilt edit FILE       # = quilt add FILE + 開編輯器（方便）
quilt delete NAME     # 刪除 patch

# === 檢視 ===
quilt diff            # 看當前 patch 的內容
quilt files           # 當前 patch 改了哪些檔案
quilt header          # 看/編輯 patch 的說明 header
```

## DEP-3：patch 的 metadata header

好的 patch 在開頭有結構化的 metadata（DEP-3 格式），說明這個 patch 是什麼、從哪來、要不要送 upstream：

```
Description: Fix buffer overflow in config parser
 The parser did not check the length of the input line before
 copying into a fixed buffer, allowing a stack overflow with
 crafted config files.
Author: Your Name <you@example.com>
Origin: upstream, https://github.com/upstream/foo/commit/abc123
Bug: https://github.com/upstream/foo/issues/456
Bug-Debian: https://bugs.debian.org/789012
Forwarded: https://github.com/upstream/foo/pull/457
Last-Update: 2025-05-29
---
（patch 內容從這裡開始）
--- a/src/parser.c
+++ b/src/parser.c
@@ ...
```

| 欄位 | 意義 |
|---|---|
| `Description` | 這個 patch 做什麼、為什麼 |
| `Author` | 誰寫的 |
| `Origin` | 來源（`upstream` / `backport` / `vendor`）|
| `Bug` / `Bug-Debian` | 對應的 upstream / Debian bug |
| `Forwarded` | 有沒有送回 upstream（`yes`/`no`/URL）|
| `Last-Update` | 最後更新日期 |

> `Forwarded` 欄位是 Debian 社群很看重的：好的修復應該送回 upstream，不該只留在 Debian。`Forwarded: no` 會被質疑「為什麼不送 upstream」。標 `Forwarded: not-needed`（如純 Debian 特定的修改）或附上 URL 證明你送了。

## patch 的應用時機（在 build 流程裡）

```
dpkg-source -x foo.dsc（解包）：
  1. 解開 .orig.tar       ← 純 upstream
  2. 疊上 debian/ 目錄
  3. 按 series 順序套用所有 patch  ← 自動！
  → 得到「打過 patch 的 source」，可以 build

dpkg-buildpackage 開始 build 時（dh sequence）：
  dh_auto_configure 之前，patch 已經套好了
  （Format 3.0 quilt 在解包時就套用，不是 build 時）
```

> 注意：Format 3.0 (quilt) 在 **dpkg-source -x 解包時**就把 patch 套上了。所以你 `apt source` 抓下來的 source 目錄已經是「打過 patch」的狀態。`quilt applied` 會顯示全部已套用。

## 故意弄壞：patch 套不上（upstream 變了）

最常見的 quilt 問題：upstream 出新版，舊 patch 套不上了。

```bash
# 假設 upstream 從 1.0 升到 1.1，你把舊的 patch 試著套到新 source
quilt push fix-buffer-overflow.patch
# Applying patch debian/patches/fix-buffer-overflow.patch
# patching file src/parser.c
# Hunk #1 FAILED at 42.    ← 衝突！upstream 改了這附近的程式碼
# 1 out of 1 hunk FAILED -- rejects in file src/parser.c
# Patch debian/patches/fix-buffer-overflow.patch does not apply (enforce with -f)
```

處理：

```bash
# 1. 強制套用（產生 .rej 拒絕檔）
quilt push -f
# 2. 手動修正衝突
vim src/parser.c        # 看 src/parser.c.rej，手動套用該改的
rm src/parser.c.rej
# 3. 重新 refresh patch
quilt add src/parser.c  # 如果還沒 add
quilt refresh           # 更新 patch 以適配新 upstream
```

或者：upstream 在 1.1 已經修了這個 bug → 你的 patch 不再需要 → 從 series 刪掉：

```bash
quilt delete fix-buffer-overflow.patch
# 並在 changelog 記錄「drop patch, fixed upstream in 1.1」
```

> 升級 upstream 版本時逐一檢查每個 patch 是否還套得上、是否還需要，是維護者的例行工作。`gbp pq`（patch queue，見進階）讓這個流程更順。

## dpkg-source --commit：不用 quilt 也能做 patch

如果你不想用 quilt 的 push/pop，可以直接改檔案，用 dpkg-source 把改動變成 patch：

```bash
# 直接改 upstream 檔案
vim src/parser.c

# dpkg-source 把所有未記錄的改動做成一個新 patch
dpkg-source --commit
# 它會問你 patch 名字，然後把改動寫進 debian/patches/ 並加進 series
```

這對「快速做一個 patch」很方便，但 quilt 對「管理多個 patch、調整順序、refresh 既有 patch」更強。兩者可混用。

## 踩雷集錦

1. **先改檔案再 `quilt add`**：quilt 靠 add 時的備份來 diff。先改再 add，quilt 抓不到你的改動，refresh 出空 patch。永遠**先 add 再改**（或用 `quilt edit` 一步做完）

2. **忘記 `quilt refresh`**：改完檔案沒 refresh，改動沒寫進 patch 檔。build 出來的套件沒有你的修改。改完一定 refresh

3. **`QUILT_PATCHES` 沒設**：quilt 預設用 `./patches`，但 Debian 要 `debian/patches`。沒設 `~/.quiltrc` 的 `QUILT_PATCHES=debian/patches`，patch 會跑錯地方

4. **build 前 patch 沒全 pop**：如果你手動 push 了 patch 在 source 目錄裡，然後 `dpkg-source -b` 重新打包，可能產生混亂的狀態。慣例是打包前 `quilt pop -a`（回到乾淨 upstream），讓 dpkg-source 自己管理

5. **patch 沒寫 DEP-3 header**：lintian 會抱怨 `patch-not-forwarded-upstream` 或缺說明。每個 patch 都該有 Description 和 Forwarded 狀態

6. **修改 generated 檔案（如 configure）**：autotools 的 `configure` 是從 `configure.ac` 生成的。patch 改 `configure` 而非 `configure.ac` 是反模式——應該 patch 源頭，讓 `dh_autoreconf` 重新生成

## 進階：gbp pq（git patch queue）

用 git 管理打包時（Ch 6 的 gbp），quilt patch 和 git commit 之間的轉換用 `gbp pq`：

```bash
# 把 debian/patches/ 的 patch 展開成 git commit（在一個臨時 branch）
gbp pq import
# 現在每個 patch 是一個 git commit，你可以用 git 工具編輯
#（rebase、合併、重排序——比 quilt 順手）

git commit ...    # 像平常一樣改 code、commit

# 把 git commit 轉回 debian/patches/
gbp pq export
# patch 檔和 series 自動更新
```

`gbp pq` 讓你用熟悉的 git 工作流（commit、rebase、cherry-pick）管理 patch，而非 quilt 的 push/pop。對習慣 git 的人更順手。匯出時自動生成 DEP-3 header（從 commit message）。這是現代維護者的主流做法。

## 動手練習

1. 設好 `~/.quiltrc`，`apt source` 一個有 patch 的套件（如 `apt source bash`，看 `debian/patches/series`），用 `quilt series` 看它有幾個 patch，`quilt applied` 確認都套用了

2. 完整走一次新增 patch：解開一個簡單套件，`quilt new test.patch`、`quilt add` 某個檔案、改它、`quilt refresh`、`quilt diff` 看結果。然後 `quilt pop` 取消它，確認檔案恢復

3. 製造衝突：對一個 patch 故意改 series 順序（讓後面的 patch 先套），`quilt push -a` 看衝突，理解順序的重要

4. 讀一個真實 patch 的 DEP-3 header（`cat debian/patches/某個.patch` 的開頭），看它的 Origin、Forwarded、Bug 欄位怎麼填

## 本章重點整理

- Debian 用 patch 而非直接改 upstream：可追溯、可轉發 upstream、可在新版重套、orig 保持乾淨
- quilt 是 patch stack 管理工具：push/pop 套用取消、new 開新 patch、add 後改再 refresh 寫入
- `debian/patches/series` 定義 patch 的套用順序；Format 3.0 (quilt) 在解包時自動套用
- DEP-3 header 記錄 patch 的來源、說明、是否 forwarded upstream
- 工作流關鍵：先 `quilt add` 再改，改完一定 `quilt refresh`
- gbp pq 讓你用 git commit 管理 patch（現代主流）

## 自我檢核

- [ ] 不看筆記，能說出為什麼 Debian 用 patch 而不直接改 upstream（至少兩個理由）
- [ ] 能完整走一次「新增一個 patch」的 quilt 流程，知道為什麼要先 add 再改
- [ ] 知道 `series` 檔案的作用，以及 patch 在 build 流程的哪個時機套用
- [ ] 知道 upstream 升級後 patch 套不上時怎麼處理
- [ ] 能說出 DEP-3 的 `Forwarded` 欄位為什麼重要

## 延伸閱讀

### 官方文件

- **[quilt(1) man page](https://manpages.debian.org/bookworm/quilt/quilt.1.html)**
  - **讀哪裡**：所有 command 的說明，特別是 push/pop/refresh/fold
  - **學什麼**：quilt 的完整指令集；本章講了常用的，這裡有全部
  - **前提**：讀完本章

- **[DEP-3: Patch Tagging Guidelines](https://dep-team.pages.debian.net/deps/dep3/)**
  - **讀哪裡**：欄位定義那節
  - **學什麼**：patch header 每個欄位的精確語意和填法
  - **前提**：本章的 DEP-3 部分

### 部落格 / 文章

- **[Using quilt for Debian packaging](https://wiki.debian.org/UsingQuilt)** — Debian Wiki
  - **這篇說什麼**：quilt 在 Debian 打包脈絡的完整工作流，含 `.quiltrc` 設定和常見場景
  - **讀哪裡**：整頁，特別是 workflow 和 troubleshooting
  - **為什麼值得讀**：把 quilt 的通用功能對應到 Debian 打包的具體用法

→ [Ch 12 debhelper 深入](./12-debhelper-deep-dive.md)
