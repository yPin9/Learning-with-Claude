# Ch 0 — 環境與語料庫：WSL、kernel v6.18、讀碼工具鏈

> **目標**：搭好整門課共用的讀碼環境：釘在 v6.18、保有完整 git 歷史的 kernel tree，四份 user-space 暖身語料，一套不需要 sudo 的讀碼工具鏈，一份能拿來單步與 trace 的 kernel build，外加一支檢查腳本，跑完全綠才算環境就緒。
> **鴻溝**：本章不直接練鴻溝，而是替後面練**間接鴻溝**（需要工具與動態追蹤）和**脈絡鴻溝**（需要完整 git 歷史）打地基。

> **環境**：Windows 11 + WSL2 Ubuntu 22.04.3（WSL kernel `6.18.58.1-microsoft-standard-WSL2`），16 執行緒、21 GB RAM，**沒有 sudo**。git 2.34.1、gcc 11.4、clang 14、gdb 12.1、QEMU 6.2。其他 Linux 發行版的差異只在套件名稱，指令本身通用。

## 為什麼需要這個？

讀碼教材最常見的失敗，是讀者打開自己的 tree，發現行號對不上、函式名稱不一樣、整段邏輯被重構掉了。kernel 尤其嚴重：它每九到十週發一個主版本，每個版本合進一萬多個 commit。本課後面會反覆遇到這種事——例如你記憶中（或 LLM 記憶中）pipe 的寫入函式叫 `pipe_write()`，但在 v6.18 它叫 `anon_pipe_write()`：2025 年 2 月 Oleg Nesterov 先用 commit `262b2fa99cbe` 把匿名 pipe 和具名 FIFO 的 `file_operations` 拆成 `pipeanon_fops` 與 `pipefifo_fops`，再用 commit `f017b0a4951f` 把 `pipe_read`／`pipe_write` 改名為 `anon_pipe_read`／`anon_pipe_write`，另加給 FIFO 用的 `fifo_pipe_read`／`fifo_pipe_write`（兩者都在 v6.15 進入主線）。

所以這門課做三個決定：

1. **版本釘死在 v6.18**。教材裡每一段 kernel 程式碼都標 `檔案:行號（v6.18）`，你的 tree 打開同一行應該一字不差。
2. **保留完整 git 歷史**。Part 6 的考古（`git log -L`、`git blame`、`git log -S`）需要從 2005 年至今的每一個 commit。沒有歷史的 tree 只能回答「現在長怎樣」，不能回答「為什麼長這樣」——那正是脈絡鴻溝。
3. **一切都不需要 sudo**。很多讀者的工作機、學校伺服器、公司 VM 都沒有 root。本章所有工具都裝到 `~/.local` 或 `~/.cargo`。

## 先建立直覺：整個環境的形狀

```
~/code_reading/
├── linux/           ← torvalds/linux blobless clone，HEAD = v6.18（detached）
│   └── .git/        ← 1,398,773 個 commit 的完整歷史；檔案內容（blob）按需下載
├── build/           ← O=../build 的 out-of-tree build：vmlinux、bzImage、compile_commands.json
├── qemu/            ← initramfs + run.sh / run-script.sh：在 TCG 模式開 v6.18 guest
├── lua-5.4.9/       ← 暖身語料：小而精的直譯器（Part 2 練習 B）
├── redis-7.2.17/    ← 暖身語料：中型 C 伺服器（Part 2 練習 B）
├── freebsd/         ← 暖身語料：wc / uniq / cat（Ch 6、練習 A）
└── scratch/         ← 你自己的實驗區，不放進任何 repo

~/.cargo/bin/rg            ← ripgrep
~/.local/bin/ctags         ← Universal Ctags（靜態 binary）
~/.local/bin/{gtags,global,cscope}  ← 從原始碼裝
```

心法上可以這樣想：`linux/` 是**文本**，`build/` 是**這份文本在某個設定下真正被編出來的樣子**，`qemu/` 是**讓文本動起來的實驗台**。讀碼時三者會交替出現——讀文本卡住了，去 `build/` 看巨集展開後的 `.i`、去 `qemu/` 裡 trace 它實際走哪條路。

## WSL：為什麼不直接在 Windows 上讀

kernel tree 裡有大小寫只差一個字母的檔名（例如 `include/uapi/linux/netfilter/xt_CONNMARK.h` 和 `xt_connmark.h`），NTFS 預設不分大小寫，checkout 在 Windows 檔案系統上會互相覆蓋。另外，Kbuild、`scripts/config`、git hook 全部假設 POSIX 環境。所以：

- tree 放在 WSL 的 ext4 裡（`~/code_reading/`），**不要**放在 `/mnt/c/...`。跨檔案系統存取慢一個數量級以上，而且有上面的大小寫問題。
- 編輯器可以用 Windows 端的 VS Code 透過 Remote-WSL 打開，clangd 跑在 WSL 端（Ch 49）。

一個值得注意的巧合：這台機器的 WSL kernel 本身就是 6.18 系列（Microsoft 的分支，帶自家 patch）。所以在 WSL 裡跑 user-space 實驗，打到的就是 6.18 的 pipe、eventfd 程式碼。Ch 3 會用到這件事：一個卡在滿 pipe 上的 process，`/proc/<pid>/wchan` 會直接告訴你它睡在 `anon_pipe_write`。但涉及 kernel 設定、ftrace、gdb 的實驗，我們仍在 QEMU 裡用自己 build 的 v6.18 做，因為 WSL kernel 的 config 和 patch 不受我們控制。

## Kernel tree：blobless clone 的原理與取捨

### git 物件模型 30 秒複習

git 倉庫裡有三種主要物件：**commit**（作者、時間、訊息、指向一棵 tree）、**tree**（目錄：檔名 → blob 或子 tree）、**blob**（檔案內容）。kernel 的歷史裡，blob 佔了絕大部分體積：每一次修改一個檔案，就多一份完整的新 blob（pack 時會 delta 壓縮，但總量仍然最大）。

### 三種 clone 的比較

| 方式 | 指令 | 拿到什麼 | 考古能力 | 代價 |
|---|---|---|---|---|
| full clone | `git clone URL` | 全部 commit、tree、blob | 全部可用，離線也行 | 最大，下載最久 |
| shallow clone | `git clone --depth=1 URL` | 只有最新 commit 和它的 tree、blob | **幾乎為零**：`git log` 只有一筆，`blame` 全部算在那一筆 | 最小 |
| blobless clone | `git clone --filter=blob:none URL` | 全部 commit 和 tree，**blob 只拿 checkout 需要的** | 全部可用；舊 blob 第一次用到時才下載 | 中等；需要網路 |

shallow clone 對「讀現在的程式碼」夠用，但這門課的一半心法是考古：`git log -L` 追一個函式的演化、`git blame` 找某一行是哪個 commit 加的、`git log -S` 找某個字串第一次出現。shallow clone 把這些全部廢掉。

blobless clone（partial clone 的一種）的關鍵是：**`git log`、`git log -- <path>`、`git log --grep` 用到的只有 commit 和 tree**，所以全部離線可用且很快；只有要看舊版**檔案內容**的操作（`git show v6.1:fs/pipe.c`、`git blame`、`git log -p`、`git log -S`）才會觸發下載。

### 實測：哪些操作快、哪些慢

在本課的 tree 上實際跑：

```
$ time (git log --oneline -3 -- fs/pipe.c)
db2ab24a341c Add RWF_NOSIGNAL flag for pwritev2
dd765ba87239 fs/pipe: set FMODE_NOWAIT in create_pipe_files()
f3def8270c67 sort.h: hoist cmp_int() into generic header file

real	0m0.153s

$ time (git show v6.1:fs/pipe.c | wc -l)      # 第一次：要去 GitHub 抓這個 blob
1511
real	0m15.223s

$ time (git show v6.1:fs/pipe.c | wc -l)      # 第二次：已經在本地
1511
real	0m0.014s
```

第一次 15 秒、第二次 14 毫秒，差異來自 git 在背後啟動一次 fetch：連上 GitHub、協商、下載這個 blob、寫成一個新的 pack 檔。`git blame` 和 `git log -S` 更慘：它們要看**一連串**舊版本的內容，每個缺的 blob 都可能各觸發一次下載。實測 `git blame -L 400,410 fs/pipe.c` 跑超過兩分鐘還沒完成，期間 git 還不斷印出 `Auto packing the repository in background`——每次按需下載都產生一個小 pack 檔，累積多了觸發自動 gc。寫這章的那段時間（同一棵 tree 上還有其他考古指令在跑），pack 數從 2 個漲到 272 個。

這是 blobless clone 的真實代價：**考古第一次很慢，第二次很快**。實務上的對策：

- 先用不需要 blob 的指令縮小範圍：`git log --oneline -- <path>`、`git log --grep=<關鍵字>`，找到可疑 commit 再 `git show <commit>`（只抓那個 commit 的 diff 需要的 blob）。
- `git log -S` / `git log -L` 一定要同時限定**路徑**和**版本範圍**，並包一層 `timeout`，例如 `timeout 300 git log -S poll_usage --oneline v5.10..v6.18 -- fs/pipe.c`。不要對整棵樹、整段歷史 pickaxe。
- 確定某個檔案會反覆考古，可以一次把它的歷史 blob 抓下來：`git log --format=%H -- fs/pipe.c | ...` 搭配 `git show` 預熱。

### 實測：為什麼 clone 來源是 GitHub 而不是 git.kernel.org

kernel 的官方倉庫在 git.kernel.org，但它**不支援 partial clone 的 filter**。這可以直接問伺服器：git protocol v2 的 `fetch` 能力清單裡，有 `filter` 才代表支援。

```
$ GIT_TRACE_PACKET=1 git -c protocol.version=2 ls-remote \
    https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git refs/tags/v6.18 2>&1 | grep 'fetch='
packet:          git< fetch=shallow wait-for-done

$ GIT_TRACE_PACKET=1 git -c protocol.version=2 ls-remote \
    https://github.com/torvalds/linux.git refs/tags/v6.18 2>&1 | grep 'fetch='
packet:          git< fetch=shallow wait-for-done filter
```

git.kernel.org 只宣告 `shallow`，GitHub 的鏡像多了 `filter`。對 git.kernel.org 下 `--filter=blob:none` 時，git 會警告 filtering not recognized by server 並退回完整 clone。兩邊的 `refs/tags/v6.18` 都指向同一個 tag 物件 `f7b88edb52c8…`，所以內容等價——tag 物件的 hash 一樣，底下的每一個 commit、tree、blob 也必然一樣，這是 git 內容定址的保證。

> 認識論誠實：這是 2026 年 11 月的實測結果。伺服器能力會變，你讀到這裡時可以用上面那行 `ls-remote` 自己再驗一次。

### 實際指令

```bash
mkdir -p ~/code_reading && cd ~/code_reading
git clone --filter=blob:none --no-checkout https://github.com/torvalds/linux.git
cd linux
git checkout v6.18          # detached HEAD；只下載 v6.18 這一版的 blob
```

結果：`.git` 約 2.3 GB（1,398,773 個 commit 加上全部 tree），工作目錄約 1.7 GB。`--no-checkout` 是為了避免 clone 完先 checkout 一次 master 的最新版、再切到 v6.18 時又下載一批 blob。

這棵 tree 的規矩：**不要 checkout 別的版本、不要改它**。要看其他版本，用 `git show v6.1:fs/pipe.c`；要比較，用 `git diff v6.1 v6.18 -- fs/pipe.c`。這樣全課的行號永遠有效。

還有一種 `--filter=tree:0`（treeless clone）連舊 tree 都不拿，更小，但 `git log -- <path>` 需要比對每個 commit 的 tree 才知道某個路徑有沒有變，於是連這種最基本的考古都要觸發下載。對讀碼來說得不償失。

## User-space 暖身語料

kernel 是終點，不是起點。Part 1、Part 2 先在 user-space 程式上練心法，因為它們可以直接編譯、執行、改壞、加 printf，回饋迴圈短得多。

| 語料 | 版本 | 規模 | 為什麼選它 | 來源 |
|---|---|---|---|---|
| FreeBSD `wc`、`uniq`、`cat` | 14.3（tag `release/14.3.0`） | 367 / 385 / 539 行 | 短、完整、風格乾淨；Spinellis 原書也大量用 BSD utility 當例子 | `freebsd/freebsd-src` GitHub 鏡像的 raw 檔 |
| Lua | 5.4.9 | 約 3 萬行 C | 一個完整的直譯器，慣用法密度高（巨集、tagged union、手寫 GC） | `lua/lua` GitHub 倉庫的 `v5.4.9` tag |
| Redis | 7.2.17 | 中型伺服器 | 事件迴圈、callback、自製資料結構，間接呼叫的入門版 | `download.redis.io/releases/` |

FreeBSD 三個檔案我們用 sha1 比對過 GitHub `release/14.3.0` 上的 raw 檔，完全一致。Lua 取自 GitHub 的 `lua/lua` 倉庫而不是 lua.org 的 tarball，所以目錄是扁平的（沒有 `src/`），還多了 `ltests.c` 和 `testes/` 測試目錄——讀碼時這反而是好事，測試就是規格（Ch 40）。

### 為什麼 Redis 停在 7.2

這是授權問題，不是技術問題。Redis 的授權經過三個階段：

- **7.2.x 及更早**：BSD 3-Clause。`redis-7.2.17/COPYING` 開頭就是 `Copyright (c) 2006-2020, Salvatore Sanfilippo` 加上 BSD 三條款。
- **7.4 起**（2024 年 3 月 20 日宣布）：改為 RSALv2 / SSPLv1 雙授權。Redis 官方自己也說 RSALv2 不是開源授權；SSPL 也不被 OSI 承認。社群因此在 Linux Foundation 下 fork 出 BSD 授權的 Valkey。
- **8.0 起**（2025 年 5 月）：在 RSALv2、SSPLv1 之外加上 AGPLv3 作為第三個選項。

選 7.2 的理由是：它是最後一個純 BSD 授權的系列，教材裡大段引用與改寫它的程式碼沒有任何疑慮；而本課關心的部分（`ae.c` 事件迴圈、`dict.c`、`sds.c`）在 7.2 和之後的版本之間架構沒有根本改變。

## 讀碼工具鏈：不靠 sudo 安裝

沒有 sudo，就不能 `apt install`。每個工具各有一條無 root 的路：

| 工具 | 版本 | 安裝方式 | 為什麼這樣裝 |
|---|---|---|---|
| ripgrep | 15.2.0 | `cargo install ripgrep` | Rust 工具鏈本身可以用 rustup 裝在 `~/.cargo`，不需要 root |
| Universal Ctags | 6.2.0（nightly 2026.10.08） | 下載官方 nightly 的**靜態連結** binary | 靜態 binary 不依賴系統 libc 版本，丟進 `~/.local/bin` 就能跑 |
| GNU Global | 6.6.15 | 原始碼 `./configure --prefix=$HOME/.local` | 只依賴 libc |
| cscope | 15.9 | 原始碼 `./configure --prefix=$HOME/.local` | 依賴 ncurses（這台機器剛好已有 `libncurses-dev`） |

另外有幾個工具，是後面章節用到時才裝進 `~/.local` 的，這裡先列出，需要時再回來看：

| 工具 | 版本 | 在哪一章裝 | 備註 |
|---|---|---|---|
| clangd | 23.1.0 | [Ch 49](./49-clangd-lsp.md) | 系統的 clang 14 沒附 clangd，改用 clangd/clangd 的官方 release binary |
| sparse | v0.6.5-rc1-21 | [Ch 18](./18-annotations-and-sparse.md) | 從原始碼編 |
| unifdef | 來自 kernel tree | [Ch 13](./13-preprocessor-basics.md) | 編 `scripts/unifdef.c` |
| graphviz（`dot`） | Ubuntu 22.04 套件 | [Ch 7](./07-reading-notes-and-diagrams.md) | 把 .deb 解開到 `~/.local/graphviz`，不需要 root |
| pip + b4 | b4 0.15.2 | [Ch 38](./38-mailing-list-archaeology.md) | 用 `get-pip.py --user` 裝 pip |

注意：`cargo install ripgrep` 預設**不含 PCRE2**，所以 `rg --pcre2`（`-P`）在這套環境不能用；需要 lookaround 時改用 `grep -P` 或兩段式 pipeline，見 [Ch 47](./47-grep-and-regex-search.md)。要 PCRE2 版可以 `cargo install ripgrep --features pcre2`，但需要系統有 PCRE2 開發檔或能從原始碼編。

```bash
# ripgrep：先裝 rustup（裝到 ~/.cargo，不碰系統）
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
~/.cargo/bin/cargo install ripgrep

# Universal Ctags：nightly 靜態 binary
mkdir -p ~/.local/bin ~/code_reading/tools-src && cd ~/code_reading/tools-src
curl -fLo uctags.tar.xz 'https://github.com/universal-ctags/ctags-nightly-build/releases/download/2026.10.08%2B82494361bb3e7d4f7cf1d2bdc0d0e9001c926e45/uctags-2026.10.08-linux-x86_64.release.tar.xz'
mkdir uctags && tar -xJf uctags.tar.xz -C uctags
cp uctags/uctags-*/bin/ctags ~/.local/bin/

# GNU Global
curl -fLO https://ftp.gnu.org/pub/gnu/global/global-6.6.15.tar.gz
tar xzf global-6.6.15.tar.gz && cd global-6.6.15
./configure --prefix=$HOME/.local --disable-gtagscscope && make -j8 && make install
cd ..

# cscope
curl -fLo cscope.tgz https://sourceforge.net/projects/cscope/files/cscope/v15.9/cscope-15.9.tar.gz/download
tar xzf cscope.tgz && cd cscope-15.9
./configure --prefix=$HOME/.local && make -j8 && make install
```

幾個細節：

- Ubuntu 預設的 `~/.profile` 會在 `~/.local/bin` 存在時把它加進 PATH，`~/.cargo/env` 由 rustup 加進 `~/.profile`。所以這些工具只在**登入 shell** 裡看得到。從 Windows 端用 `wsl -- bash -s` 餵腳本時要改成 `wsl -- bash -l -s`，否則會得到 `rg: command not found`——寫這章時第一次跑檢查就踩到了。
- `ctags --version` 第一行必須是 `Universal Ctags`。Ubuntu 22.04 的 `exuberant-ctags` 套件停在 2009 年的 5.8 版，不認得很多現代 C 語法，對 kernel 的巨集更無能為力。
- Global 加 `--disable-gtagscscope` 是因為我們另外裝了真正的 cscope，不需要 Global 附帶的 cscope 相容前端。
- 若 cscope 的 configure 抱怨找不到 curses：那代表系統沒有 ncurses 開發標頭，沒 sudo 的話要先把 ncurses 也從原始碼裝到 `~/.local`，再用 `CPPFLAGS=-I$HOME/.local/include LDFLAGS=-L$HOME/.local/lib` 指給 configure。本機沒遇到這個情況，這段是依 autoconf 的一般行為推論。

裝好之後，三秒內驗證工具真的能用在 kernel 上：

```
$ cd ~/code_reading/linux
$ time (rg -n 'anon_pipe_write' --type c .)
./fs/pipe.c:431:anon_pipe_write(struct kiocb *iocb, struct iov_iter *from)
./fs/pipe.c:606:	int ret = anon_pipe_write(iocb, from);
./fs/pipe.c:1260:	.write_iter	= anon_pipe_write,

real	0m0.182s

$ ctags -x --c-kinds=f fs/eventfd.c | head -3
SYSCALL_DEFINE1  function    428 fs/eventfd.c     SYSCALL_DEFINE1(eventfd, unsigned int, count)
SYSCALL_DEFINE2  function    423 fs/eventfd.c     SYSCALL_DEFINE2(eventfd2, unsigned int, count, int, flags)
do_eventfd       function    379 fs/eventfd.c     static int do_eventfd(unsigned int count, int flags)
```

ripgrep 掃完整棵 kernel tree（三萬多個 `.c` 檔）不到 0.2 秒。ctags 的輸出藏著一個伏筆：它把 `SYSCALL_DEFINE1` 當成函式名稱，因為它不展開巨集。真正的函式名稱 `__x64_sys_eventfd` 在原始碼裡**一個字都沒有出現**——這是你第一次碰到「純文字工具的極限」，Ch 29 和 Ch 49 會處理它。

## Kernel build：讓文本可以被編譯、追蹤、單步

為什麼讀碼課要 build kernel？三個理由：

1. **`compile_commands.json`**：clangd 需要知道每個 `.c` 檔用什麼 `-I`、`-D` 編譯，才能正確跳轉定義、展開巨集（Ch 49）。
2. **`.i` 與 `.s` 檔**：`make O=../build fs/pipe.i` 給你巨集全部展開後的樣子，是慣用法鴻溝的終極解藥（Ch 50）。
3. **帶 debug info 的 `vmlinux`**：QEMU + gdb 單步（Ch 52）、ftrace 動態觀察（Ch 51）都靠它。

實際用過的指令：

```bash
cd ~/code_reading/linux
make O=../build x86_64_defconfig
make O=../build kvm_guest.config
./scripts/config --file ../build/.config \
  -e DEBUG_INFO_DWARF5 -d DEBUG_INFO_NONE -e GDB_SCRIPTS -d DEBUG_INFO_REDUCED \
  -e FTRACE -e FUNCTION_TRACER -e FUNCTION_GRAPH_TRACER -e DYNAMIC_FTRACE \
  -e KPROBES -e KPROBE_EVENTS -e BPF_SYSCALL -e BPF_JIT -e BPF_EVENTS \
  -e DEBUG_FS -e IKCONFIG -e IKCONFIG_PROC -e EVENTFD -e KUNIT -e DEBUG_INFO_BTF
make O=../build olddefconfig
make O=../build -j14
make O=../build compile_commands.json
```

逐行解釋：

- **`O=../build`**：out-of-tree build。所有產物寫進 `~/code_reading/build/`，原始碼 tree 保持乾淨，`git status` 永遠是 clean。這也是為什麼我們可以「不改這個 tree」卻仍然 build。
- **`x86_64_defconfig`**：x86-64 的預設設定，一個能開機的合理起點。
- **`kvm_guest.config`**：一個 config fragment（`kernel/configs/kvm_guest.config`），打開 virtio 等虛擬機需要的驅動，讓 QEMU 開得起來。
- **`scripts/config`**：kernel 自帶的設定修改工具，`-e` 開、`-d` 關。DWARF5 debug info 給 gdb，`GDB_SCRIPTS` 產生 `vmlinux-gdb.py`（提供 `lx-dmesg`、`lx-ps` 等 gdb 指令），`FTRACE` 系列給 Ch 51，`IKCONFIG_PROC` 讓 guest 裡可以 `zcat /proc/config.gz` 確認設定，`DEBUG_INFO_BTF` 給 bpftrace 和 `pahole`。
- **`olddefconfig`**：`scripts/config` 只改你點名的選項，不處理相依性；這一步依 Kconfig 規則補齊新選項的預設值、解決相依。**跳過這步**，build 時 Kbuild 會停下來互動式問你一堆問題。
- **`-j14`**：16 個執行緒留 2 個給系統和其他工作。
- **`compile_commands.json`**：從 build 產生的 `.cmd` 檔整理出每個編譯單元的完整指令。

本機實測（16 執行緒、21 GB RAM、`-j14`）：整個 build 約 13 分鐘。產物：

```
build/vmlinux                    419 MB   （含 DWARF5 debug info，沒 strip）
build/arch/x86/boot/bzImage      17 MB   （壓縮後可開機的 kernel image）
build/compile_commands.json      8.6 MB  （3000 個編譯單元）
build/                           4.8 GB  （整個 build 目錄）
```

vmlinux 419 MB 裡絕大部分是 debug info。build 時間依機器差異很大，四核筆電可能要一小時以上。

> **踩雷預告**：`make O=../build fs/eventfd.i` 這類單檔目標必須在 `~/code_reading/linux` 底下執行（`O=` 指向 build 目錄），實測 9 秒產出一個 76,894 行的 `.i`——原本 432 行的 `fs/eventfd.c`，展開所有 `#include` 和巨集之後膨脹了一百多倍。

## QEMU：讓 v6.18 動起來

`/dev/kvm` 在這台 WSL 裡存在，但 QEMU 用不了：

```
$ qemu-system-x86_64 -enable-kvm -m 256M -nographic -kernel build/arch/x86/boot/bzImage ...
Could not access KVM kernel module: No such device
qemu-system-x86_64: failed to initialize kvm: No such device
```

裝置節點在、底下的 KVM 模組不可用（WSL2 的巢狀虛擬化取決於主機設定）。所以我們用 QEMU 的 TCG（純軟體模擬），慢，但對讀碼實驗夠用：開機約 3–4 秒。

`~/code_reading/qemu/` 裡有三樣東西：

- **`initramfs/`**：一個最小根檔案系統，只有靜態連結的 busybox。它的 `/init` 掛上 `proc`、`sysfs`、`devtmpfs`、`tmpfs`、`debugfs`、`tracefs`，然後如果有 `/run.sh` 就執行它並關機，否則丟給你一個 shell。
- **`run.sh`**：互動式開機，`console=ttyS0 nokaslr panic=-1 quiet`。`nokaslr` 關掉 kernel 位址隨機化，讓 gdb 的符號位址和 `vmlinux` 一致（Ch 52）。
- **`run-script.sh <guest 腳本> [額外檔案...]`**：把腳本打包成 guest 的 `/run.sh`，額外檔案放進 guest 的 `/bin`，開機、執行、關機，輸出印到 stdout。user-space 測試程式要 `gcc -static` 編，因為 guest 裡沒有 libc。環境變數 `QEMU_EXTRA` 可加 qemu 參數（例如 `-s -S` 讓 gdb 連進來），`QEMU_TIMEOUT` 調 timeout。

煙霧測試：

```
$ cat smoke.sh
#!/bin/sh
uname -r
zcat /proc/config.gz | grep -E '^CONFIG_(EVENTFD|FUNCTION_GRAPH_TRACER)='
grep -c . /sys/kernel/tracing/available_filter_functions
grep -w -m1 anon_pipe_write /proc/kallsyms

$ ~/code_reading/qemu/run-script.sh smoke.sh
== code_reading guest ready: Linux version 6.18.0 (ypp@DESKTOP-JK2JTGG) (gcc (Ubuntu 11.4.0-1ubuntu1~22.04.3) 11.4.0, ...) ==
6.18.0
CONFIG_EVENTFD=y
CONFIG_FUNCTION_GRAPH_TRACER=y
55855
ffffffff815932c0 t anon_pipe_write
[    6.424265] reboot: Power down
```

55,855 個可被 ftrace 掛勾的函式、`anon_pipe_write` 在 `nokaslr` 下的固定位址——這兩行代表動態追蹤的地基已經就位。Ch 3 會用這套環境實際 trace 一個 writer 在滿 pipe 上睡著又被叫醒的過程。

## 零安裝替代方案：Bootlin Elixir

如果你暫時沒有 Linux 環境，或只是想在手機上讀一段 code，[Elixir](https://elixir.bootlin.com/linux/v6.18/source) 是 Bootlin 維護的線上交叉參照：每一個識別字都可以點，點下去列出它在哪裡定義、在哪裡被引用。網址格式是 `https://elixir.bootlin.com/linux/v6.18/source/fs/pipe.c#L431`，版本可以切換。

它能做的：讀 code、跳定義、找引用、跨版本比較。它**不能**做的：

- **考古**：沒有 `git log -L`、`blame`、commit 訊息。脈絡鴻溝在 Elixir 上無解。
- **依 config 判斷哪些 code 會被編進去**：Elixir 索引的是全部原始碼，`#ifdef` 兩邊都會顯示（Ch 36）。
- **動態觀察**：它是靜態文本。

所以 Elixir 是好的**起點**和**旅途中的備案**，不是本課的主環境。Ch 48 會比較它和本地 cscope / Global 的差異。

## 驗證環境：檢查腳本

把下面這支腳本存成 `~/code_reading/scratch/00/check_env.sh`。它檢查語料、工具、build 三層，任何一項失敗都讓 exit code 非零。

```bash
#!/usr/bin/env bash
# check_env.sh — 檢查 code_reading 課程環境是否就緒
R=~/code_reading
ok=0; bad=0
pass() { printf '  [OK]   %s\n' "$*"; ok=$((ok+1)); }
fail() { printf '  [FAIL] %s\n' "$*"; bad=$((bad+1)); }

echo "== 語料庫"
if [ -d $R/linux/.git ]; then
  d=$(git -C $R/linux describe --tags 2>/dev/null)
  [ "$d" = v6.18 ] && pass "linux HEAD = $d" || fail "linux HEAD = $d（應為 v6.18）"
  f=$(git -C $R/linux config remote.origin.partialclonefilter)
  [ "$f" = blob:none ] && pass "blobless clone（filter=$f）" || fail "不是 blobless clone"
  [ -f $R/linux/.git/shallow ] && fail "是 shallow clone，考古會斷" || pass "完整歷史（非 shallow）"
else fail "找不到 $R/linux"; fi
[ -f $R/lua-5.4.9/lvm.c ]            && pass "Lua 5.4.9"      || fail "缺 Lua 5.4.9"
grep -q '"7.2.17"' $R/redis-7.2.17/src/version.h 2>/dev/null \
                                      && pass "Redis 7.2.17"   || fail "缺 Redis 7.2.17"
for p in usr.bin/wc/wc.c usr.bin/uniq/uniq.c bin/cat/cat.c; do
  [ -f $R/freebsd/$p ] && pass "FreeBSD $p" || fail "缺 FreeBSD $p"
done

echo "== 工具"
for t in rg ctags gtags global cscope gcc clang gdb qemu-system-x86_64 pahole; do
  if command -v $t >/dev/null; then pass "$t → $(command -v $t)"; else fail "$t 不在 PATH"; fi
done
ctags --version 2>/dev/null | grep -q Universal && pass "ctags 是 Universal Ctags" \
                                                 || fail "ctags 不是 Universal Ctags"

echo "== kernel build（選用）"
B=$R/build
[ -f $B/vmlinux ] && pass "vmlinux $(du -h $B/vmlinux | cut -f1)" || fail "沒有 vmlinux"
[ -f $B/compile_commands.json ] && pass "compile_commands.json" || fail "沒有 compile_commands.json"
grep -q '^CONFIG_DEBUG_INFO_DWARF5=y' $B/.config 2>/dev/null && pass "DWARF5 debug info" \
                                                               || fail "沒開 DEBUG_INFO_DWARF5"
[ -r /dev/kvm ] && pass "/dev/kvm 可讀（不保證 QEMU 能用 KVM）" || fail "沒有 /dev/kvm（QEMU 會用 TCG）"

echo "== 結果：$ok 項通過，$bad 項失敗"
[ $bad -eq 0 ]
```

本機實際輸出：

```
== 語料庫
  [OK]   linux HEAD = v6.18
  [OK]   blobless clone（filter=blob:none）
  [OK]   完整歷史（非 shallow）
  [OK]   Lua 5.4.9
  [OK]   Redis 7.2.17
  [OK]   FreeBSD usr.bin/wc/wc.c
  [OK]   FreeBSD usr.bin/uniq/uniq.c
  [OK]   FreeBSD bin/cat/cat.c
== 工具
  [OK]   rg → /home/ypp/.cargo/bin/rg
  [OK]   ctags → /home/ypp/.local/bin/ctags
  ...（gtags、global、cscope、gcc、clang、gdb、qemu、pahole 皆 OK）
  [OK]   ctags 是 Universal Ctags
== kernel build（選用）
  [OK]   vmlinux 400M
  [OK]   compile_commands.json
  [OK]   DWARF5 debug info
  [OK]   /dev/kvm 可讀（不保證 QEMU 能用 KVM）
== 結果：23 項通過，0 項失敗
```

注意最後一項：`/dev/kvm` 可讀並不代表 KVM 能用，上一節已經看到它實際上失敗。檢查腳本只能檢查它檢查得到的東西——這本身就是一個讀碼心法：**一個名叫 check 的函式，要讀它到底 check 了什麼**。

## 對比與取捨

| 選擇 | 本課的做法 | 替代方案 | 為什麼不選替代方案 |
|---|---|---|---|
| clone 方式 | blobless | full / shallow | full 太大；shallow 廢掉考古 |
| clone 來源 | GitHub 鏡像 | git.kernel.org | 官方站不支援 filter（實測） |
| 版本 | 釘死 v6.18 tag | 追 master | 行號會漂移，教材失效 |
| build 位置 | `O=../build` | in-tree build | in-tree 會在原始碼樹裡留下上萬個產物檔，`git status` 和 grep 都被污染 |
| 虛擬化 | QEMU TCG | KVM | 這台 WSL 的 KVM 不可用；TCG 慢但能動 |
| 線上閱讀 | Elixir 當備案 | 只用 Elixir | 沒有考古、沒有 config 判斷、沒有動態觀察 |

## 踩雷集錦

1. **以為 shallow clone 也能考古**：`git clone --depth=1` 之後 `git blame` 會把每一行都算在唯一那個 commit 頭上，看起來「有結果」，其實全是錯的。用 `[ -f .git/shallow ]` 檢查。
2. **以為 blobless clone 的 `git blame` 和 full clone 一樣快**：第一次 blame 一個熱門檔案可能要好幾分鐘，因為每個舊版本的 blob 都要下載。先用 `git log --oneline -- <path>` 和 `--grep` 縮小範圍。
3. **在非登入 shell 裡找不到工具**：`wsl -- bash -s` 不讀 `~/.profile`，PATH 裡沒有 `~/.local/bin`。改用 `bash -l`。
4. **ripgrep 不給路徑時會讀 stdin**：在管線或 `bash -s` 腳本裡寫 `rg pattern`（沒有路徑），stdin 不是終端機時 rg 會去搜 stdin——在 `bash -s` 裡，stdin 就是腳本本身的剩餘部分，rg 會把它吃掉，後面的指令全部消失且不報錯。寫這章時真的發生過。腳本裡一律寫 `rg pattern .`。
5. **`/dev/kvm` 存在 ≠ KVM 可用**：用 `qemu-system-x86_64 -enable-kvm ...` 實際試一次才算數。
6. **在 `/mnt/c` 底下 clone kernel**：大小寫衝突加上跨檔案系統的效能懲罰，兩個問題一起來。

## 進階：再往深一層

- **把考古常用檔案的歷史預先抓下來**：`git rev-list --objects --missing=print HEAD -- fs/pipe.c` 可以列出缺少的物件；partial clone 的內部機制（promisor remote、promisor pack）在 `git help partial-clone`（`Documentation/technical/partial-clone.txt`）有完整說明。
- **多版本並排**：需要同時打開 v6.1 和 v6.18 的 tree 時，用 `git worktree add ../linux-6.1 v6.1`，共享同一個 `.git`，不用再 clone。注意 worktree 的 checkout 會觸發 v6.1 全部 blob 的下載。
- **縮小 build**：只想要 `compile_commands.json` 給 clangd 用，不需要整個 build 完；但 clangd 需要 `include/generated/` 底下的產生檔（例如 `autoconf.h`），所以至少要跑過 `make prepare`。Ch 49 會細講。
- **KVM 能用的環境**：在原生 Linux 或開了巢狀虛擬化的 VM 上，`QEMU_EXTRA=-enable-kvm` 會讓 guest 快一個數量級，對 Ch 51–52 的長時間 trace 有明顯幫助。

## 動手練習

1. 在你的 tree 上跑 `git log --oneline -5 -- fs/eventfd.c`，再跑 `time git show v5.4:fs/eventfd.c | wc -l` 兩次，記下第一次和第二次的時間差。
2. 對 git.kernel.org 和 GitHub 各跑一次本章的 `ls-remote` 能力探測，確認 `filter` 能力今天是否還是一樣。
3. 用 `make O=../build fs/pipe.i` 產生 pipe 的展開檔，`wc -l` 比較 `.c` 和 `.i` 的行數，然後在 `.i` 裡搜尋 `anon_pipe_write`，看看 `wait_event_interruptible_exclusive` 展開後變成什麼。
4. 故意把 `check_env.sh` 的 `v6.18` 改成 `v6.17`，確認它真的會報 FAIL 且 exit code 非零。一個從來沒失敗過的檢查，你不知道它是否真的在檢查。

## 本章重點整理

- 版本釘在 v6.18、tree 不動，教材的每個行號才有意義。
- blobless clone 保留完整 commit 與 tree 歷史，舊檔案內容按需下載：`log` 快、`blame` 第一次慢。git.kernel.org 不支援 filter，所以從 GitHub 鏡像 clone。
- 工具全部裝在 `~/.local` 和 `~/.cargo`，只在登入 shell 的 PATH 裡。
- build 產物在 `~/code_reading/build/`，提供 `compile_commands.json`、`.i` 展開、帶 debug info 的 `vmlinux`；QEMU 用 TCG 跑 v6.18 guest。
- Elixir 適合零安裝閱讀，但不能考古、不能判斷 config、不能動態觀察。

## 自我檢核

- [ ] 不看筆記，能說出 full、shallow、blobless 三種 clone 各自拿到哪些 git 物件，以及哪一種會讓 `git blame` 給出錯誤結果
- [ ] 能解釋為什麼 `git log -- fs/pipe.c` 在 blobless clone 上很快，但 `git log -S anon_pipe_write v6.10..v6.18 -- fs/pipe.c` 第一次很慢
- [ ] 如果同事說「git.kernel.org 才是官方的，為什麼要從 GitHub clone」，你能用一行指令讓他自己看到答案
- [ ] 能說出 `make olddefconfig` 這一步省略會發生什麼事
- [ ] 能解釋 `nokaslr` 對 gdb 單步 kernel 為什麼重要
- [ ] 能說出 Elixir 至少兩件做不到、而本地環境做得到的事，並說出那對應到哪一道鴻溝

## 延伸閱讀

### 官方文件

- **[Partial Clone（git 技術文件）](https://git-scm.com/docs/partial-clone)**
  - **讀哪裡**：「Design Details」與「Fetching Missing Objects」兩節
  - **能學到什麼**：promisor remote、promisor pack 的設計，以及為什麼缺的 blob 會一個一個按需抓。看完你會明白本章那 272 個 pack 從哪裡來
  - **前提知識**：知道 git 的 commit / tree / blob 三種物件
- **[Kbuild（docs.kernel.org）](https://docs.kernel.org/kbuild/kbuild.html)**（tree 內：`Documentation/kbuild/kbuild.rst`）
  - **讀哪裡**：`KBUILD_OUTPUT` 一節（其中明講 `O=...` 優先於 `KBUILD_OUTPUT`）；同目錄的 `kconfig.rst` 有 `olddefconfig` 等 config target 的說明
  - **能學到什麼**：out-of-tree build 的正式語意、各種 config target 的差異
  - **前提知識**：會跑 `make`
- **[Debugging kernel and modules via gdb（docs.kernel.org）](https://docs.kernel.org/process/debugging/gdb-kernel-debugging.html)**（tree 內：`Documentation/process/debugging/gdb-kernel-debugging.rst`）
  - **讀哪裡**：Setup 一節，說明為什麼要 `CONFIG_GDB_SCRIPTS`、為什麼要 `nokaslr`
  - **能學到什麼**：本章 config 裡那幾個 debug 選項的官方理由；Ch 52 會實際用到
  - **前提知識**：用過 gdb

### 部落格 / 技術文章

- **[Get up to speed with partial clone and shallow clone](https://github.blog/open-source/git/get-up-to-speed-with-partial-clone-and-shallow-clone/)** — Derrick Stolee（GitHub Blog，2020）
  - **這篇說什麼**：從伺服器與客戶端兩邊比較 blobless、treeless、shallow 三種 clone 的效能特性，附圖
  - **讀哪裡**：整篇；特別是「treeless clone 讓 `git log -- path` 變慢」那段，對應本章的取捨表
  - **為什麼值得讀**：作者是 git partial clone 與 commit-graph 功能的主要貢獻者之一

### 授權背景

- **[Redis 授權頁](https://redis.io/legal/licenses/)** 與 **[Redis adopts dual source-available licensing](https://redis.io/blog/redis-adopts-dual-source-available-licensing/)**（Redis 官方，2024）
  - **讀哪裡**：授權頁列出各版本對應的授權；部落格文章是 7.4 改授權的原始公告
  - **能學到什麼**：為什麼本課的 Redis 語料停在 7.2.17
  - **前提知識**：知道 BSD、GPL 類授權的大致差別

環境好了，但工具不會告訴你「為什麼看不懂」。下一章正式定義這門課的主軸——五道鴻溝，並用一段真實的 v6.18 程式碼示範怎麼診斷自己卡在哪裡。

→ [Ch 1 為什麼你看不懂：五道鴻溝的自我診斷](./01-why-you-cant-read-it.md)
