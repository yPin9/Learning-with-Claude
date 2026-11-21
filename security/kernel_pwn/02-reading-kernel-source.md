# Ch 2 — 讀 kernel 原始碼的方法：tree 結構、追一條 syscall、版本與 config

> **目標**：建立一套可重複使用的方法論——拿到任何一個 kernel 函式名或 syscall，知道怎麼在幾分鐘內找到它的真實定義、算出相關 struct 的 offset、確認某個 mitigation 的 config 有沒有開、看懂這段程式碼是什麼時候、為什麼被改成現在這樣。本書後面每一章都會要求你實際去源碼樹裡核對，這章是那個核對動作的操作手冊。

> **環境**：以 Linux 6.6 樹（`git clone` 或 tarball 皆可，見 Ch 0）為對象，工具用 ripgrep（`rg`）、`ctags`/`cscope`、`pahole`、`git`。全部是跨平台 CLI 工具，Linux/WSL2/macOS 皆可用。

## 為什麼需要這一章？

User-space pwn 讀原始碼的範圍通常很小——一個 binary、一個已知版本的 libc。Kernel 的原始碼樹（6.6 完整 checkout 大約 7–8 萬個檔案，數千萬行）大到你不可能靠「整個讀過一遍」建立理解。這逼出一個完全不同的閱讀策略：**不是從頭讀到尾，是帶著一個具體問題去源碼裡挖**——「這個 syscall 真正執行到哪個函式？」「這個 struct 的這個欄位在記憶體裡的 offset 是多少？」「這個 config 選項預設開不開？」

本書後面的每一章在提到一個函式、一個 struct、一個 offset 時，都假設你有能力自己去核對——不是因為我們懶得寫出來，是因為**這個核對能力本身就是 kernel pwn 的核心技能**：真實世界的 CVE 分析永遠是「拿到一個 commit hash 或函式名，自己去挖上下文」，沒有人替你把所有細節都寫好。

## 先建立直覺：把 kernel tree 想成一個城市地圖

6.6 的原始碼樹頂層長這樣（只列和 kernel pwn 相關的部分）：

```
linux-6.6/
├── arch/x86/          ← 架構相關程式碼(x86 的 boot、syscall entry、page table 格式)
├── fs/                ← 檔案系統(ext4、f2fs、VFS 共同層)
├── net/               ← 網路 stack(TCP/IP、netfilter、socket 層)
├── mm/                ← 記憶體管理(buddy allocator、SLUB、page table 操作)
├── kernel/            ← 核心子系統(排程器、cgroup、trace、module 載入)
├── drivers/           ← 各種裝置驅動(我們的 vulnerable module 屬於這類)
├── ipc/               ← IPC 機制(訊號、共享記憶體、message queue)
├── security/          ← LSM(SELinux、AppArmor)、capability、keyring
├── include/           ← 公開的 header,struct 定義多半在這裡
│   └── linux/
├── scripts/           ← 編譯/設定輔助工具(kconfig、checkpatch……)
└── Documentation/     ← 官方文件,包含 kernel-parameters.txt
```

把這當一張城市地圖——你不需要認得城市裡每一棟樓,但你需要知道「銀行在哪一區」「醫院在哪一區」。對 kernel pwn 而言：

- 想找「記憶體怎麼配置的」→ `mm/`
- 想找「某個 syscall 的實作」→ 先猜它屬於哪個子系統（檔案操作在 `fs/`，網路在 `net/`，行程管理在 `kernel/`），再往下鑽
- 想找「某個 driver 的漏洞」→ `drivers/` 底下按裝置類型分類（`drivers/tty/`、`drivers/net/`、`drivers/usb/`……）
- 想找「某個 struct 的定義」→ 先試 `include/linux/`，找不到再去對應子系統目錄下找私有 header

本章接下來教你怎麼在這張地圖上快速定位，而不用死記每個檔案在哪。

## 工具一：Bootlin Elixir ——不用下載原始碼就能查

**[Bootlin Elixir](https://elixir.bootlin.com/linux/latest/source)** 是一個線上的 kernel source browser，把整個 git 歷史的每個 tag/branch 都索引好，支援跳到定義（cross-reference）。這是最快的「我只是想確認一下」用的工具。

用法：網址列直接改版本與路徑，例如 `https://elixir.bootlin.com/linux/v6.6/source/mm/slub.c`，會看到完整原始碼，每個識別字（函式名、變數名）都是可點的連結，點了直接跳去它的定義,右側欄會列出這個符號在整個樹裡被引用的所有位置（cross-reference,簡稱 xref）。

它的限制：沒有本機索引快，查詢跨檔案的複雜關係（例如「這個 macro 展開之後實際長什麼樣」）不一定清楚。對這類情況，你需要本機工具。

## 工具二：ripgrep ——日常搜尋的主力

`rg`（ripgrep）是比 `grep` 快一到兩個量級的搜尋工具，對 kernel 這種規模的樹，速度差異很明顯。基本用法：

```bash
cd linux-6.6

# 找一個函式的定義(宣告與定義都會出現,要自己分辨)
rg -n '^long do_sys_openat2' fs/open.c

# 只在某個子樹搜尋
rg -n 'misc_register' drivers/

# 搜 struct 定義(用 "struct xxx {" 這種模式精準找定義,不是所有引用)
rg -n '^struct miscdevice \{' --type c

# 搜某個 config 選項在哪裡被用到(ifdef 保護的程式碼區塊)
rg -n 'CONFIG_PAGE_TABLE_ISOLATION'
```

`rg --type c` 限制只搜 `.c`/`.h`，減少雜訊。對初學者最常用的模式是「我記得函式名但不確定在哪個檔案」——直接全樹 `rg -n '^中大括號前的函式簽名'`，kernel 的 coding style 絕大多數函式定義是頂格寫（column 0），這個習慣讓 `^函式名` 這種 anchor 搜尋非常有效。

## 工具三：cscope 與 ctags ——結構化跳轉

`rg` 是文字搜尋，不理解程式語言結構——它找不到「誰呼叫了這個函式」這種語義層的問題（除非你搜函式名字串,但那會混進註解、字串常數等假陽性）。`cscope` 和 `ctags` 建立真正的符號索引，速度和準確度都更好。

### 建立索引

```bash
cd linux-6.6
make cscope        # kernel Makefile 內建的目標,自動產生 cscope.out
make tags           # 產生 ctags 用的 tags 檔(vim/emacs 可直接讀)
```

> **未實測，理論預期行為**：`make cscope`、`make tags` 是 kernel 原始碼樹 Makefile 長期提供的標準目標（這一慣例在 Documentation 與社群文章中廣泛提及），本書假設 6.6 延續此行為；若你的樹沒有這兩個目標，改用 `scripts/tags.sh` 手動產生，或直接用全樹 ctags：`ctags -R .`。

### 用 cscope 互動查詢

```bash
cscope -d     # 讀取現有的 cscope.out,進入互動介面
```

cscope 的互動介面支援幾個關鍵查詢（數字鍵選擇）：

- `0`：找這個符號的定義
- `1`：找所有呼叫這個函式的地方（**這是 cscope 相對 `rg` 最大的優勢**——反向呼叫關係）
- `7`：找這個檔案被哪些檔案 `#include`

這個「找所有呼叫者」的能力，在追一條 syscall 怎麼從 entry 走到底層實作時非常關鍵——你經常需要反過來問「這個底層函式是被誰呼叫的，上面還有哪一層」。

## 追一條 syscall：從 entry 到實作的完整路徑

這是本章最重要的實作練習。以 `openat()` 這個常見 syscall 為例，示範怎麼從「user-space 呼叫的名字」追到「真正執行的程式碼」。

### 第一步：syscall 編號與 ABI 層

x86-64 上，syscall 的呼叫慣例是：`rax` 放 syscall number，`rdi`/`rsi`/`rdx`/`r10`/`r8`/`r9` 依序放最多六個參數,執行 `syscall` 指令。syscall number 到名字的對照表在：

```bash
rg -n 'openat' arch/x86/entry/syscalls/syscall_64.tbl
```

6.6 樹上這一行大致是：

```
257	common	openat			sys_openat
```

格式是 `編號  abi類型  名稱  對應的 entry 符號`。這張表是 Ch 4 細講 syscall ABI 時的核心資料來源。

### 第二步：`SYSCALL_DEFINEn` 宏展開

kernel 原始碼裡你找不到一個叫 `sys_openat` 的函式字面定義——它是透過宏展開出來的。搜尋：

```bash
rg -n 'SYSCALL_DEFINE4\(openat' fs/
```

會找到類似（6.6 的 `fs/open.c`，實際行號以你的樹為準）：

```c
SYSCALL_DEFINE4(openat, int, dfd, const char __user *, filename, int, flags, umode_t, mode)
{
    if (force_o_largefile())
        flags |= O_LARGEFILE;
    return do_sys_open(dfd, filename, flags, mode);
}
```

`SYSCALL_DEFINE4` 這個宏（定義在 `include/linux/syscalls.h`）做兩件事：產生一個真正叫 `sys_openat` 的函式（供上面那張 syscall table 用符號名稱接上），並且在函式開頭插入一些基礎設施程式碼（像是某些架構上的參數型別檢查）。**宏展開是 kernel 原始碼閱讀的一大門檻**——`SYSCALL_DEFINEn` 的 `n` 代表參數個數（1 到 6），每個 `n` 都有獨立的宏定義,想看展開後的實際樣子，可以用編譯器直接吐出展開結果：

```bash
gcc -E -I include -I arch/x86/include fs/open.c | rg -A5 'sys_openat'
```

> **未實測，理論預期行為**：`gcc -E` 直接展開 kernel 原始碼需要正確的 include path 與一堆 kernel 專屬的編譯 flag（`__KERNEL__`、特定架構的條件編譯），上面這行指令是示意，實際上要展開成功通常需要完整的 kernel build 環境變數。更實用的作法是用 `make fs/open.o` 搭配在 `Makefile` 裡暫時加 `-save-temps`，看 `fs/open.i`（展開後的中介檔）。

### 第三步：往下追真正的邏輯實作

`sys_openat` 呼叫 `do_sys_open`，這才是真正做事的函式（路徑解析、權限檢查、分配檔案描述符）。用 cscope 的「找呼叫者」反過來驗證,或者直接 `rg -n '^static (long|int) do_sys_open'`。追下去你會發現這條路徑最終落到 VFS（virtual filesystem）層，再往下分派到具體檔案系統（ext4、devtmpfs……）或者如果是我們自己的 `/dev/vulndevice`，分派到我們 module 註冊的 `file_operations.open`（Ch 5 細講這條路徑）。

**這整個追蹤練習要傳達的方法論是**：kernel 的 syscall 實作幾乎永遠分層——ABI 層（entry、參數打包）→ 宏展開出來的 wrapper（`sys_xxx`）→ 真正的邏輯函式（往往叫 `do_xxx` 或直接是一個更具體的名字）→ 可能繼續往下分派到子系統或 driver 層。讀一篇 CVE 分析時，作者通常直接跳到最後一層講漏洞，你需要有能力自己往回爬，理解「這個函式是怎麼被觸發到的」，這對之後評估「普通 user 有沒有辦法碰到這段程式碼」（攻擊面分析）至關重要。

## 找 struct 定義與算 offset：pahole 與 gdb ptype

Kernel exploit 經常需要知道一個 struct 裡某個欄位的**精確 offset**（例如 Ch 21 的 tty_struct ops hijack，需要知道 function pointer 欄位在整個 struct 裡的位移）。源碼裡的 struct 定義本身不會告訴你 offset——要考慮 padding、對齊（alignment）、以及**編譯器版本、`CONFIG_` 選項都可能改變同一個 struct 在不同編譯出來的 kernel 裡的實際佈局**。

### 用 `pahole` 看編譯後的真實佈局

`pahole`（來自 `dwarves` 套件）讀取編譯產物（`vmlinux`，需要 `CONFIG_DEBUG_INFO` 開著，見 Ch 0）裡的 DWARF 除錯資訊，印出 struct 的真實記憶體佈局：

```bash
pahole -C miscdevice vmlinux
```

輸出大致是（示意，實際欄位順序與 padding 以你編出的 vmlinux 為準）：

```
struct miscdevice {
        int                        minor;                /*     0     4 */
        const char  *              name;                 /*     8     8 */
        const struct file_operations  * fops;             /*    16     8 */
        struct list_head           list;                 /*    24    16 */
        struct device *            parent;               /*    40     8 */
        struct device *            this_device;          /*    48     8 */
        const struct attribute_group ** groups;          /*    56     8 */
        const char  *              nodename;             /*    64     8 */
        umode_t                    mode;                 /*    72     2 */

        /* size: 80, cachelines: 2, members: 9 */
        /* padding: 6 */
        /* last cacheline: 16 bytes */
};
```

每個欄位後面 `/* offset  size */` 就是你要的數字。**這個數字只對「這次編譯出的這顆 vmlinux」保證準確**——別的 kernel 版本、別的 `.config`、別的編譯器版本都可能改變它，這是為什麼本書每次提到一個 offset 都會標注「依你的 kernel 實測填入」。

### 沒有 pahole 時，用 gdb ptype

如果你已經有 `vmlinux` 接著 gdb（Ch 0 的環境)，也可以直接問 gdb：

```gdb
(gdb) ptype struct miscdevice
(gdb) print &((struct miscdevice *)0)->fops
```

第二行是一個常見技巧——對 NULL 指標取某個欄位的位址,印出來的數字就是這個欄位的 offset（這招在 user-space 也通用，`offsetof()` 宏本質上做的是同一件事，只是 kernel 的某些私有 struct 不方便直接用 `offsetof` 寫一支小程式去算，用 gdb 對已經編好的 vmlinux 直接問更快）。

## Config 系統：defconfig、menuconfig、確認一個選項開了沒

### `defconfig` 是什麼

`defconfig` 不是「default config」的意思（雖然常被這樣誤解）——它是一種**特定架構提供的、適合絕大多數常見硬體的起始設定**，存放在 `arch/<arch>/configs/` 底下。Ch 0 用的 `x86_64_defconfig` 就是其中之一。它不代表「kernel 的真正預設值」——不同發行版（Ubuntu、Fedora、Android）各自維護自己的 defconfig，差異可能很大（尤其 hardening 相關選項）。

### `menuconfig`：互動式瀏覽/修改

```bash
make menuconfig
```

這會開一個 ncurses 介面，用方向鍵瀏覽所有 `CONFIG_` 選項的分類樹，`/` 可以搜尋選項名稱或關鍵字,搜到之後它會告訴你這個選項在選單裡的路徑（哪個分類下）、依賴哪些其他選項。對初次確認「某個 mitigation 選項到底叫什麼名字、藏在哪個分類下」非常有用——比起直接猜 `scripts/config` 的參數名字，`menuconfig` 的搜尋可以幫你確認拼寫和存在性。

### 確認一個選項的狀態：三種方法

1. **直接讀 `.config` 文字檔**（最直接，但要先編譯過一次才有這個檔）：
   ```bash
   grep CONFIG_PAGE_TABLE_ISOLATION .config
   # 輸出 CONFIG_PAGE_TABLE_ISOLATION=y 代表開著
   # 輸出 # CONFIG_PAGE_TABLE_ISOLATION is not set 代表關著
   ```

2. **在已經跑起來的系統上查**（不需要原始碼樹，只需要一台跑著目標 kernel 的機器，前提是 kernel 編譯時留了這個資訊）：
   ```bash
   zcat /proc/config.gz 2>/dev/null | grep CONFIG_XXX
   # 或者
   cat /boot/config-$(uname -r) | grep CONFIG_XXX
   ```
   `/proc/config.gz` 不是所有 kernel 都有（需要 `CONFIG_IKCONFIG_PROC` 開著）；`/boot/config-*` 是多數主流發行版打包時留下的副本，可用性視發行版而定。

3. **用 `scripts/config` 的查詢模式**：
   ```bash
   ./scripts/config --state CONFIG_PAGE_TABLE_ISOLATION
   # 輸出 y / n / m / undef
   ```

這三種方法分別對應三種情境：你在原始碼樹裡改設定（方法 1、3）、你拿到一台已跑起來的目標機器想知道它開了什麼（方法 2，這在真實滲透/CTF 裡很常見——拿到 shell 第一件事常常是確認目標開了哪些 mitigation）。

## git log / git blame：找改動與 fixing commit

### 為什麼這對 kernel pwn 特別重要

真實世界的 N-day exploit（Ch 50）流程幾乎都是「看到一個 fixing commit → 理解它修了什麼 → 推回修補前的漏洞長什麼樣 → 對沒升級的舊版本寫 exploit」。這個「看 commit 推漏洞」的能力，核心工具就是 `git log`/`git blame`。

### `git log` 追一個檔案或函式的歷史

```bash
git log --oneline -- mm/slub.c | head -20          # 這個檔案最近的改動
git log -p -L :kmem_cache_alloc:mm/slub.c | less    # 追蹤一個函式本身的逐次修改(-L 指定函式範圍)
```

`-L :函式名:檔案` 是特別好用的功能——它不是搜尋整個檔案的歷史，是**只追蹤這個函式這段程式碼範圍**的修改歷史，過濾掉同檔案裡其他不相關的改動雜訊。

### `git blame` 找「這行是什麼時候、為什麼被改成這樣」

```bash
git blame -L 100,120 mm/slub.c
```

每一行會標出最後修改它的 commit hash、作者、時間。拿到 hash 後：

```bash
git show <hash>
```

看完整的 commit message 與 diff——好的 kernel commit message 會解釋「為什麼」而不只是「改了什麼」，這段說明往往就是 bug 的 root cause 敘述（如果這是一個 security fix）。

### 找 fixing commit 的實務技巧

真實 CVE 分析裡,一個常見起點是拿到一條 CVE 編號或一段 panic log，想找「哪個 commit 修了它」。幾個搜尋角度：

```bash
git log --all --oneline --grep="CVE-2023-"        # commit message 裡提到 CVE 編號(不是所有都會提)
git log --oneline --grep="UAF\|use-after-free"      # 用常見漏洞關鍵字搜 commit message
```

多數 security fix 的 commit message **不會**明寫 CVE 編號（CVE 編號往往是之後才被對應上去的,甚至永遠沒有對應），這時只能靠：函式/檔案範圍（已知漏洞大致在哪個子系統）+ 時間範圍（已知大概哪個版本修的）+ 關鍵字,交叉縮小範圍。Ch 42 會把這整套「patch 到 root cause」的方法論展開成完整一章。

## 對比與取捨

| 工具 | 查詢類型 | 速度 | 需要本機環境 |
|---|---|---|---|
| Bootlin Elixir | 單一符號定義、簡單 xref | 線上即時,但受限於網速 | 不需要，瀏覽器就夠 |
| ripgrep | 文字模式搜尋 | 極快 | 需要本機有原始碼樹 |
| cscope/ctags | 語義層符號跳轉（呼叫者/被呼叫者） | 快（索引建好之後） | 需要先建索引 |
| pahole | 編譯後真實記憶體佈局 | 快 | 需要編好的 vmlinux（帶 DWARF） |
| gdb ptype | 同上，互動式 | 快 | 同上，且需要 gdb 環境 |
| git log/blame | 修改歷史、root cause 敘述 | 快 | 需要完整 git 歷史（tarball 沒有） |

## 踩雷集錦

1. **錯誤直覺：「源碼裡寫的 struct 定義就是記憶體裡的真實佈局。」** 正確認識：C 編譯器會插入 padding 做對齊，不同 `CONFIG_` 選項（例如某些 debug 選項會在 struct 裡插入額外欄位）、不同編譯器版本都可能改變實際佈局。永遠用 `pahole` 或 `gdb ptype` 核對**這一次編譯出的這顆 vmlinux**的真實 offset,不要只讀原始碼就假設 offset。

2. **錯誤直覺：「tarball 下載下來的原始碼和 git clone 的一樣，git 功能只是附加好處。」** 正確認識：tarball 是某個版本的快照，**沒有 git 歷史**——`git log`/`git blame` 完全用不上。如果你的工作流程需要追修改歷史（幾乎所有 N-day 分析都需要），一開始就要用 `git clone` 而不是下載 tarball。

3. **錯誤直覺：「`defconfig` 就是『這個 kernel 真正會用的設定』。」** 正確認識：`defconfig` 是上游給你的一個起始點，真實發行版（Ubuntu、Android 廠商）的實際 config 和它可能差異巨大，尤其 hardening 相關選項。分析一個真實目標時,永遠去確認**那台機器實際跑的**是哪份 config，不要假設它用了某個 defconfig。

4. **錯誤直覺：「`SYSCALL_DEFINEn` 裡的 `n` 無所謂，反正宏會自動處理。」** 正確認識：`n` 必須精確對應參數個數（1 到 6），而且這個宏在不同架構上還牽涉到參數型別的處理細節（例如某些架構上 32-bit 與 64-bit 參數的傳遞方式不同，這也是為什麼有些 syscall 會有獨立的 compat 版本，Ch 4 細談）。讀程式碼時看到 `SYSCALL_DEFINE4` 要立刻反應「這個函式有 4 個參數,型別分別是……」，不要跳過宏本身的資訊。

5. **錯誤直覺：「commit message 裡沒寫 CVE 編號，代表這個 commit 跟安全無關。」** 正確認識：kernel 的 security fix commit 絕大多數**不會**在原始 commit message 裡提到 CVE——CVE 編號通常是後續由安全研究者或發行版維護者對應上去的，甚至很多修了真實漏洞的 commit 永遠沒有被分配 CVE。判斷一個 commit 是不是 security fix，要看 diff 本身的性質（修了一個邊界檢查、修了一個 race、修了一個 UAF）而不是看它有沒有提 CVE。

## 進階：再往深一層

- **讀 `scripts/checkpatch.pl`**：這是 kernel 社群用來檢查 patch 風格的工具，附帶的規則集間接反映了 kernel 開發者社群對程式碼品質的共識（例如對某些危險函式用法的警告）。瀏覽它的規則本身是理解「kernel 開發者認為什麼樣的程式碼有風險」的捷徑。
- **訂閱 Linux Kernel Mailing List（LKML）的 security 相關討論**，或者至少定期看 [KSPP（Kernel Self Protection Project）的 wiki](https://kernsec.org/wiki/index.php/Kernel_Self_Protection_Project)——理解「現在社群在爭論哪些 hardening 該不該合併」能幫你預判未來幾個版本 mitigation 的走向（Ch 26 會深談現狀,但這個領域持續在變動）。
- **自己動手追一次 CVE**：挑一個近年、root cause 相對單純的 Linux kernel CVE（例如某個 UAF），用本章的工具鏈（git log 找 fixing commit → git show 看 diff → 回推 patch 前的程式碼長怎樣）完整走一次，這比讀十篇別人的分析更有效。

## 動手練習

1. 用 Bootlin Elixir 打開 6.6 的 `drivers/char/misc.c`，找到 `misc_register` 的定義，順著它的實作讀一次（不需要全懂，抓住「它把 `struct miscdevice` 塞進一個 list，並且分配/註冊一個 device number」這個主軸）。
2. 在你自己的 6.6 樹跑 `pahole -C task_struct vmlinux | head -30`，觀察這個全核心最常被引用的 struct，前 30 個欄位的 offset 分布（這個 struct 極大，之後 Ch 12 的提權原語會用到它裡面的 `cred` 欄位)。
3. 挑一個你自己 `.config` 裡確定開著的選項（例如 `CONFIG_SMP`），用三種方法（讀 `.config`、`scripts/config --state`、`menuconfig` 搜尋）各確認一次，體會三者資訊是否一致。
4. 用 `git log --oneline -- mm/slub.c | wc -l` 看這個檔案在整個 git 歷史裡被改了多少次，抓幾筆 commit message 看看哪些像是 bugfix、哪些像是 feature。

## 本章重點整理

- Kernel 原始碼樹的規模逼出「帶著問題去挖」的閱讀策略，而不是從頭讀到尾；本章的工具鏈（Bootlin、ripgrep、cscope、pahole、git）是支撐這個策略的日常工具。
- 一條 syscall 從 user-space 呼叫到真正邏輯實作要經過多層（ABI → `SYSCALL_DEFINEn` 宏展開 → `do_xxx` 實作函式 → VFS/子系統分派），追蹤這條路徑的能力是評估攻擊面的基礎。
- struct 的真實記憶體佈局（offset）只有編譯後才能確定，`pahole`/`gdb ptype` 是唯一可靠的查詢方式，原始碼裡的欄位順序不能直接等於 offset。
- 真實世界 N-day 分析的核心方法論是「fixing commit → diff → 回推漏洞」，`git log -L`、`git blame`、`git show` 是這條方法論的具體工具。

## 自我檢核

- [ ] 不看筆記，能說出從 `openat()` 到真正邏輯實作要經過哪幾層，各層的作用是什麼
- [ ] 能解釋為什麼「原始碼裡 struct 欄位的順序」不能直接當成記憶體裡的 offset
- [ ] 知道 `defconfig` 不等於「這顆 kernel 真正用的設定」，能說出至少一種確認「目標機器實際開了哪些 config」的方法
- [ ] 能說出為什麼 tarball 下載和 git clone 對「找 fixing commit」這件事差異巨大

## 延伸閱讀

### 官方文件 / Spec

- **[Documentation/process/submitting-patches.rst](https://www.kernel.org/doc/html/latest/process/submitting-patches.html)**
  - **讀哪裡**：講 commit message 規範的段落
  - **和本章的關聯**：理解 kernel 社群對「好的 commit message 該寫什麼」的期望，能幫你更快從 `git log` 的輸出判斷一個 commit 是不是值得細看的 security fix

### 部落格 / 技術文章

- **[Bootlin Elixir 使用介紹](https://elixir.bootlin.com/)**
  - **這篇說什麼**：首頁本身有簡短的使用說明，列出支援哪些專案（不只 Linux，還有 U-Boot、FreeBSD 等）
  - **讀哪裡**：直接上手操作比讀文件更快，首頁的搜尋框試幾次就懂
  - **為什麼值得讀**：這是追蹤 kernel 原始碼最低門檻的起點工具，不需要本機環境

### 工具文件

- **[pahole / dwarves 專案頁](https://git.kernel.org/pub/scm/devel/pahole/pahole.git/about/)**
  - **讀哪裡**：README 裡 `-C`（class/struct 篩選）與常用參數的說明
  - **和本章的關聯**：本章「找 struct offset」一節直接用到它；之後 Ch 19/21（heap spray 物件、tty_struct ops hijack）會大量依賴 `pahole` 核對真實佈局

### 書籍

- **《Linux Kernel Development》第 3 版** — Robert Love（Addison-Wesley, 2010）
  - **這本書的定位**：雖然版本老，但對 kernel 原始碼樹整體組織方式、coding style、開發流程的說明至今大致適用,適合當本章方法論的背景補充
  - **讀哪幾章**：第 1 章（Introduction to the Linux Kernel）與附錄的 coding style 部分，其餘技術細節章節有不少已過時，不建議當技術權威來源

有了讀原始碼的方法論，下一步是進入 kernel 自己的「執行環境」——沒有 libc、GFP flags、per-cpu 變數、atomic/sleepable context,這些是後面每一個 vulnerable module 都要面對的基本規則。

→ [Ch 3 kernel C 與執行環境](./03-kernel-c-runtime.md)
