# Ch 0 — 環境與「可除錯的 build」

> **目標**：把 GDB 環境一次架好，並且理解一件很多人忽略的事——**能不能順利 debug，從你怎麼編譯就決定了**。學完你會知道 `-g`、`-O0`、debug info、build-id、debuginfod 各是什麼，以及為什麼 release binary 那麼難搞。

> **環境**：GDB 13/14，gcc 12+ 與 clang 15+，Ubuntu 22.04 / Debian 12 / Arch，x86_64。

## 為什麼從「build」開始，而不是從 `break` 開始？

幾乎所有 GDB 教學都從「打開 gdb、下 break、run」開始。這是個錯誤的起點，因為它跳過了一個決定成敗的前提：**你的執行檔裡到底有沒有除錯資訊**。

你一定遇過這種場面：對著一個程式下 `break main`，GDB 回你 `Function "main" not defined.`；或者 `print x` 得到 `No symbol "x" in current context.`；又或者一路 `<optimized out>`。這些都不是 GDB 壞了，是**編譯時沒給它線索**。

Debugger 不會魔法般知道你的原始碼長怎樣。它需要編譯器在執行檔裡留下一份地圖——哪一行原始碼對應哪段機器碼、哪個變數放在哪個暫存器或 stack offset、struct 的每個欄位佔幾個 byte。這份地圖就是 **debug info**（在 Linux/ELF 上是 DWARF 格式）。沒有它，GDB 只能看到一堆位址和機器碼。

所以這一章先把「怎麼產生一個好 debug 的 binary」講清楚，後面所有章節才站得穩。

## 先建立直覺：原始碼、機器碼、地圖

想像你在一個沒有路牌的城市開車（CPU 執行機器碼），手上有一張地圖（debug info）把每個 GPS 座標對應到「某某路某號」（原始碼行號與變數）。

```
   原始碼 (.c)            機器碼 (.text)         debug info (DWARF)
   ┌───────────┐         ┌────────────┐        ┌──────────────────────┐
   │ int x = 3;│  gcc    │ mov $3, ...│        │ line 5  -> 0x1149     │
   │ foo(x);   │ ──────> │ call foo   │  +     │ var x   -> rbp-0x4    │
   │           │         │ ...        │        │ foo()   -> 0x1130     │
   └───────────┘         └────────────┘        └──────────────────────┘
                              ↑                          ↑
                         CPU 真正執行的            GDB 用來把上面翻譯回
                                                  人看得懂的東西
```

- 沒有右邊那張地圖（沒 `-g`）：GDB 只能用左下的機器碼和位址跟你溝通，`print x` 沒辦法，因為它不知道 `x` 在哪。
- 有地圖但被最佳化打亂（`-O2`）：地圖會「失真」——`x` 可能根本沒進記憶體、被優化掉，於是 `<optimized out>`。

記住這張圖，Ch 6（符號）、Ch 32（最佳化 binary）、Ch 38（DWARF）都會回來。

## 安裝 GDB 與工具鏈

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install -y gdb gcc g++ clang make
sudo apt install -y gdb-multiarch        # Ch 37 跨架構會用到
gdb --version
```

預期輸出（版本號可能不同）：

```
GNU gdb (Ubuntu 12.1-0ubuntu1~22.04.2) 12.1
```

> ⚠️ 如果你的 distro 給的是 GDB 12 或更舊，本課多數內容仍適用，但 Ch 28 的 Python TUI window API、部分 `info` 子命令、`-Dndebug` 細節以 13/14 為準。Ubuntu 22.04 預設 12.1 可用；要 13+ 可考慮從 source 編或用較新 distro。

### Arch

```bash
sudo pacman -S gdb gcc clang make qemu-user
```

### 從 source 編 GDB（想要最新版或要 Ch 42 讀 source 時）

```bash
wget https://ftp.gnu.org/gnu/gdb/gdb-14.2.tar.xz
tar xf gdb-14.2.tar.xz && cd gdb-14.2
./configure --with-python=/usr/bin/python3 --enable-targets=all
make -j$(nproc)
sudo make install
```

`--with-python` 很關鍵——少了它整個 Part 5 都不能玩。確認方式：

```bash
gdb -batch -ex "python print(__import__('sys').version)"
```

有 Python 的話會印出版本字串；沒有會說 `Python scripting is not supported in this copy of GDB.`

## `-g`：把地圖塞進去

最基本的一步。比較有沒有 `-g` 的差別：

```bash
cat > hello.c <<'EOF'
#include <stdio.h>

int add(int a, int b) {
    int sum = a + b;
    return sum;
}

int main(void) {
    int x = 3, y = 4;
    int z = add(x, y);
    printf("%d\n", z);
    return 0;
}
EOF

gcc -O0 hello.c -o hello_nog          # 沒有 -g
gcc -g -O0 hello.c -o hello_g         # 有 -g
```

對沒有 `-g` 的版本：

```
$ gdb -q ./hello_nog
(gdb) break add
Breakpoint 1 at 0x1131
(gdb) run
...
Breakpoint 1, 0x0000555555555131 in add ()    # 只有位址跟函式名，沒有原始碼行
(gdb) print sum
No symbol "sum" in current context.           # 看不到區域變數
```

對有 `-g` 的版本：

```
$ gdb -q ./hello_g
(gdb) break add
Breakpoint 1 at 0x1131: file hello.c, line 4.  # 對到原始碼了
(gdb) run
...
Breakpoint 1, add (a=3, b=4) at hello.c:4
4           int sum = a + b;                    # 看得到原始碼
(gdb) print sum
$1 = 21845                                      # 看得到變數（此時還沒賦值，是垃圾值）
```

差別一目了然。函式名 `add` 兩個版本都看得到，是因為那來自 ELF 的 symbol table（`.symtab`），不是 DWARF；但**行號與區域變數**只有 DWARF 才有。這個區別 Ch 6 會展開。

### debug 等級：`-g1` / `-g2` / `-g3`

`-g` 等同 `-g2`。三個等級的差異：

| 等級 | 內容 | 用途 |
|---|---|---|
| `-g1` | 只有 backtrace 需要的資訊（函式、行號），**沒有區域變數** | 縮小體積、只要能看 stack |
| `-g2`（=`-g`）| 加上區域變數、型別 | 預設，日常用這個 |
| `-g3` | 再加上**巨集定義**（macro） | 要 `print` 一個 `#define` 的值時 |

`-g3` 的威力示範：

```bash
gcc -g3 -O0 hello.c -o hello_g3
```

```
(gdb) break main
(gdb) run
(gdb) print sizeof(int)       # 一般也行
(gdb) macro expand SOME_MACRO # 只有 -g3 能展開巨集
```

> 一個常見誤會：以為 `-g` 會讓程式變慢。**不會。** `-g` 只是往執行檔塞 debug info，不改變一個 byte 的機器碼。會變慢的是 `-O0`（關掉最佳化），那是另一回事，下一節說。

## `-O0` vs `-O2`：最佳化是除錯的天敵

`-O` 系列是**最佳化等級**，和 `-g` 完全正交（可以同時用）。但最佳化會重排、合併、刪除程式碼，讓 debug info 那張地圖嚴重失真。

```bash
gcc -g -O0 hello.c -o hello_O0
gcc -g -O2 hello.c -o hello_O2
```

對 `-O2` 版本：

```
(gdb) break add
(gdb) run
(gdb) print sum
$1 = <optimized out>            # sum 被優化掉了，根本沒存在記憶體
```

甚至 `add` 可能整個被 **inline** 進 `main`，於是 `break add` 直接落空。這就是為什麼**開發時一律 `-g -O0`**，而 Ch 32 會專門教怎麼硬啃 `-O2` 的 release binary——因為線上崩潰的往往就是 release 版。

我的建議：

- **開發 / 學這門課**：`-g -O0`
- **要重現只在最佳化下出現的 bug**：`-g -Og`（`-Og` 是「為除錯而生的最佳化」，比 `-O0` 快、比 `-O2` 好 debug）
- **線上 release**：`-g -O2`，但 debug info 用下一節的方式拆出來

## build-id 與 separate debug info

線上 binary 你不會想把肥大的 debug info 一起部署（DWARF 可能比程式本體還大）。標準做法是**把 debug info 拆成獨立檔案**，用 **build-id** 串起來。

build-id 是 linker 塞進 ELF 的一段 hash，唯一標識這個 build：

```bash
gcc -g hello.c -o hello
readelf -n hello | grep -A1 'Build ID'
```

```
    Build ID: a1b2c3d4e5f6...
```

拆 debug info 的標準流程：

```bash
objcopy --only-keep-debug hello hello.debug      # 把 debug info 抽出來
strip --strip-debug hello                         # 從本體拿掉 debug info（本體變小）
objcopy --add-gnu-debuglink=hello.debug hello     # 在本體留一個指標指向 .debug
```

之後 GDB debug `hello` 時，會循 build-id / debuglink 自動去找 `hello.debug`。找的路徑可用 `show debug-file-directory` 看（通常 `/usr/lib/debug`）。這就是為什麼 distro 的 `-dbgsym` / `-debuginfo` 套件能讓你 debug 系統函式庫。

## debuginfod：除錯資訊的「線上倉庫」

GDB 13+ 內建 **debuginfod** 支援——一個 HTTP 協定，讓 GDB 在需要某個 build-id 的 debug info / source 時自動上網抓。再也不用手動裝一堆 `-dbgsym`。

```bash
export DEBUGINFOD_URLS="https://debuginfod.ubuntu.com"   # Ubuntu；Debian/Arch/Fedora 各有 URL
gdb -q ./hello
```

第一次連線 GDB 會問你要不要啟用：

```
This GDB supports auto-downloading debuginfo from the following URLs:
  <https://debuginfod.ubuntu.com>
Enable debuginfod for this session? (y or [n])
```

啟用後，當你 `bt` 進 libc，它會自動把 libc 的 debug info（甚至原始碼）抓下來，backtrace 直接有行號。要永久開：在 `~/.gdbinit` 加 `set debuginfod enabled on`。

> 認識論誠實：debuginfod 是**有條件**的便利——它要對應的 distro 有架 server、且你連得上網。離線、內網、或自編的 binary 它幫不上忙，那時還是得靠上一節的 separate debug info。

## 裝一個增強插件：gef 或 pwndbg

原生 GDB 的介面對逆向 / pwn 不太友善（沒有 context 視窗、不會自動 telescope 記憶體）。社群有兩大插件，本課 Final Project 就是要你寫一個自己的。先裝一個來感受目標長什麼樣：

```bash
# gef（單檔，輕量）
bash -c "$(curl -fsSL https://gef.blah.cat/sh)"

# 或 pwndbg（功能多，pwn 取向）
git clone https://github.com/pwndbg/pwndbg
cd pwndbg && ./setup.sh
```

裝完隨便 debug 一個程式 `run` 一下，你會看到自動跳出 registers / stack / code / backtrace 的彩色 context。**記住這個畫面**——Part 5 結束時你會自己做出類似的東西。

> 注意：gef 和 pwndbg 不能同時載入（都會 hook 同一批事件）。它們本質就是一個塞進 `~/.gdbinit` 的 Python script，用的全是 Ch 22–28 要教的 API。

## 踩雷集錦

1. **「我加了 `-g` 還是看不到變數」**：八成你同時開了 `-O2`。`-g` 給地圖，最佳化把地圖撕了。開發請 `-O0`。
2. **「`break main` 說 function not defined」**：通常是 strip 過的 binary（debug info 與 symbol 都被拿掉），或者你 debug 的是錯的檔案。`file` 指令確認，`readelf -S` 看有沒有 `.debug_info` section。
3. **把 `-g` 當成會拖慢程式**：不會。拖慢的是 `-O0`。CI 裡的 release 永遠可以帶 `-g`，再 strip 出來。
4. **以為 strip 之後就完全不能 debug**：還是能看組語、下位址斷點（Ch 4、Ch 11），只是失去原始碼與變數層級。逆向工程就是在這個層級工作。
5. **debuginfod 沒反應**：忘了 `export DEBUGINFOD_URLS`，或在不支援的 distro 上。它不是萬靈丹，自編 binary 它不認得。

## 進階：再往深一層

- **`.debug_*` sections**：DWARF 不是一塊，而是 `.debug_info`、`.debug_line`、`.debug_abbrev`、`.debug_str` 等多個 section。`readelf -S hello | grep debug` 看得到。Ch 38 會逐一拆解。
- **split DWARF（`-gsplit-dwarf`）**：把大部分 DWARF 放進 `.dwo` 檔，加速大型專案連結。大型 C++ 專案常用。
- **`-fdebug-types-section`**：把重複的型別資訊去重，縮小 DWARF。
- **DWARF 版本**：gcc 12+ 預設產 DWARF 5。`-gdwarf-4` 可降版以相容老工具。`readelf --debug-dump=info hello | head` 看 version。

```bash
# 觀察 DWARF 的真面目（先看一眼，Ch 38 會細講）
readelf --debug-dump=info hello_g | head -40
readelf --debug-dump=decodedline hello_g | head -20   # 行號對位址的表
```

## 動手練習

1. 把本章的 `hello.c` 分別用 `-O0`、`-Og`、`-O2` 編出三個 binary，對每個下 `break add` + `print sum`，記錄差異。
2. 用 `objcopy` 把 `hello_g` 的 debug info 拆成 `.debug` 檔，`strip` 本體後確認 GDB 還能不能對到原始碼（會，因為 debuglink）。再把 `.debug` 檔刪掉，看 GDB 怎麼抱怨。
3. `readelf -S` 比較 `hello_nog` 與 `hello_g` 多了哪些 section。

## 本章重點整理

- 能不能 debug，編譯當下就決定了：`-g` 給 debug info（地圖），`-O0` 保持地圖準確。
- `-g` 不影響執行速度；影響可讀性的是最佳化等級。
- debug info（DWARF）和 symbol table（`.symtab`）是兩回事：前者給行號/變數，後者給函式名。
- build-id + separate debug info + debuginfod 是「線上 binary 瘦身、除錯時再補資訊」的標準組合。

## 自我檢核

- [ ] 不看筆記，能不能解釋為什麼 `-O2` 的程式 `print` 變數常常 `<optimized out>`？
- [ ] 如果面試官問「`-g` 會讓程式變慢嗎」，你會怎麼回答？
- [ ] 拿到一個 strip 過的 binary，你還能用 GDB 做哪些事、不能做哪些事？
- [ ] 知道 build-id 在 separate debug info 流程裡扮演什麼角色嗎？

## 延伸閱讀

### 官方文件

- **[GCC: Options for Debugging](https://gcc.gnu.org/onlinedocs/gcc/Debugging-Options.html)**
  - **讀哪裡**：`-g`、`-glevel`、`-Og`、`-gsplit-dwarf` 各段。
  - **和本章的關聯**：本章的 build 選項就是從這裡來的；想知道某個 `-g` 變體的精確語意，這是權威。

- **[GDB Manual: Separate Debug Files](https://sourceware.org/gdb/current/onlinedocs/gdb/Separate-Debug-Files.html)**
  - **讀哪裡**：整節不長；重點是 build-id 與 debuglink 兩種尋找機制。
  - **和本章的關聯**：解釋 GDB 拿到本體後怎麼一步步找到 `.debug` 檔。

### 部落格 / 文章

- **[elfutils debuginfod 介紹](https://sourceware.org/elfutils/Debuginfod.html)** — Frank Ch. Eigler（debuginfod 作者）
  - **這篇說什麼**：debuginfod 的協定設計與 server/client 架構。
  - **讀哪裡**：開頭的 overview 與 client 設定那段。
  - **為什麼值得讀**：作者就是這套東西的設計者，講得最準。

### 書籍

- **《Linkers and Loaders》** — John R. Levine（1999）
  - **定位**：想真正搞懂 symbol table、relocation、ELF section 的底層，這是經典。
  - **讀哪幾章**：第 3 章（object files）與第 5 章（symbol management）和本章最相關；Ch 40 也會回來用。

下一章我們先不碰指令細節，而是建立整個課程的骨架觀念：debugger 到底是個什麼樣的程式、它怎麼一邊讓程式跑、一邊偷看它。

→ [Ch 1 Debugger 到底在做什麼](./01-what-a-debugger-does.md)
