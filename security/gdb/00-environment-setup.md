# Ch 0 — 環境搭建

> 目標：在你的機器上裝好 `gdb` 與 `gcc`，能編出一隻帶 debug info 的 `a.out`，並用 GDB 成功下第一個斷點。

## 環境選擇：Linux 是主場

GDB 能在各種系統上跑，但差別很大。本課程的所有範例假設你在：

| 項目 | 需求 |
|---|---|
| OS | Linux x86_64（Ubuntu / Debian / Fedora / Arch 都行） |
| gdb | **≥ 10**，建議直接用最新 |
| gcc | ≥ 9（任何近代版本都行，`-g` 不挑） |
| clang | 有也好，某些章會比較 gcc / clang 產出的 DWARF 差異 |
| Python | GDB 要能啟用 Python 3 支援（Part 5 會用到） |

**macOS 使用者**：macOS 的原生 debugger 是 `lldb`，`gdb` 要自己編、還要 codesign，痛。建議裝 Docker Desktop 或 UTM/VMware 跑一個 Linux 來學。

**Windows 使用者**：用 WSL2。原生 MinGW 的 gdb 也能跑前幾章，但 Ch 9（signal / fork）、Ch 11（多執行緒）、Ch 12（gdbserver）、Ch 17（ptrace）之後會處處踩雷。WSL2 是最少痛的路。

## 安裝 GDB

### Ubuntu / Debian / WSL2

```bash
sudo apt update
sudo apt install gdb gcc g++ build-essential
```

### Fedora / RHEL

```bash
sudo dnf install gdb gcc gcc-c++
```

### Arch

```bash
sudo pacman -S gdb gcc base-devel
```

## 驗證安裝

```bash
$ gdb --version
GNU gdb (Ubuntu 12.1-0ubuntu1~22.04) 12.1
...

$ gcc --version
gcc (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0
```

版本 ≥ 10 就可以。如果你的發行版預設版本太舊（例如 CentOS 7 是 gdb 7.6），裝 `devtoolset` 或從 source 編一個。

## 驗證 Python 支援

GDB 的 Python scripting 是 Part 5 的主角。確認你的 gdb 內建 Python 3：

```bash
$ gdb -batch -ex "python import sys; print(sys.version)"
3.10.12 (main, ...)
```

如果報錯 `Python scripting is not supported in this copy of GDB`，代表你的 gdb 編譯時沒開 Python。解法：

- Ubuntu/Debian：`apt install gdb` 預設就有，無須額外處理。
- Arch：`gdb` package 預設有。
- 自己編：configure 時加 `--with-python=python3`。

## 第一支程式：hello.c

建立 `hello.c`：

```c
#include <stdio.h>

int add(int a, int b) {
    int result = a + b;
    return result;
}

int main(void) {
    int x = 3;
    int y = 4;
    int sum = add(x, y);
    printf("sum = %d\n", sum);
    return 0;
}
```

### 關鍵：`-g` 旗標

編譯一次**沒有** debug info 的版本：

```bash
gcc hello.c -o hello-nodbg
```

再編一次**有** debug info 的版本：

```bash
gcc -g hello.c -o hello
```

兩個都能跑，但你用 GDB 打開會差很多：

```bash
$ gdb -q hello-nodbg
(gdb) break main
Breakpoint 1 at 0x1139
(gdb) run
Starting program: /tmp/hello-nodbg
Breakpoint 1, 0x0000555555555139 in main ()
(gdb) list
No symbol table is loaded.  Use the "file" command.
```

```bash
$ gdb -q hello
(gdb) break main
Breakpoint 1 at 0x1149: file hello.c, line 9.
(gdb) run
Breakpoint 1, main () at hello.c:9
9           int x = 3;
(gdb) list
4           int result = a + b;
5           return result;
6       }
7
8       int main(void) {
9           int x = 3;
...
```

**沒有 `-g`，GDB 只看得到位址，看不到原始碼、變數名、行號。** 這是整個課程的前提：**每一次編譯都要帶 `-g`**。之後的範例 `Makefile` 我們都會加上 `-g -O0`。

### 為什麼也要 `-O0`？

因為 optimization 會讓「原始碼跟實際執行的指令對不上」。最常見的兩種痛：

- **變數被優化掉**：`(gdb) print x` 回你 `$1 = <optimized out>`
- **行號跳來跳去**：`step` 跳一下就到別的函式，因為 inlining 發生了

學習階段一律 `-O0`。真的要練優化後的 debug，Part 6 會教你怎麼在有優化的 binary 上求生。

## 第一次互動：下斷點、跑起來、印變數

```bash
$ gdb -q hello
Reading symbols from hello...
(gdb) break main
Breakpoint 1 at 0x1149: file hello.c, line 9.
(gdb) run
Starting program: /tmp/hello

Breakpoint 1, main () at hello.c:9
9           int x = 3;
(gdb) next
10          int y = 4;
(gdb) next
11          int sum = add(x, y);
(gdb) print x
$1 = 3
(gdb) print y
$2 = 4
(gdb) step
add (a=3, b=4) at hello.c:4
4           int result = a + b;
(gdb) continue
Continuing.
sum = 7
[Inferior 1 (process ...) exited normally]
(gdb) quit
```

你剛剛做的事：

1. `break main` — 在 `main` 函式開頭設斷點
2. `run` — 啟動程式，執行到斷點停下
3. `next` — 執行下一行（不進入函式）
4. `print x` — 看變數的值
5. `step` — 執行下一行（會進入 `add`）
6. `continue` — 放它跑完

這六個指令是 80% debug 會用到的東西。Ch 2 會把它們一個一個拆開講。

## 常見坑

1. **忘記 `-g`**：你會以為 gdb 壞了。看到 `No symbol table` 就是沒 `-g`。
2. **編譯時的路徑跟跑 gdb 時不同**：DWARF 裡存的是編譯時的絕對路徑。換電腦或用 docker 時，gdb 可能找不到原始碼，需要 `directory /path/to/src` 指給它看。
3. **`Unable to find Mach task port for process-id` (macOS)**：前面說過了，這就是 macOS 要 codesign 的痛點。別跟它糾纏，用 Linux。
4. **stripped binary**：有些發行版的 `/usr/bin/*` 被 `strip` 掉了 debug info。你自己編的不會，但之後看 post-mortem 時會遇到，Ch 13 會處理。

## 為什麼不裝 gef / pwndbg / peda？

這三個是社群出名的 gdb plugin，把介面做得更漂亮。但：

- **這是學 GDB 的課，不是學 plugin 的課。** 先把原生 gdb 指令打熟，之後再裝 plugin 才會知道它幫你包裝了什麼。
- **plugin 的輸出格式跟教材不一樣**，你會混淆。

到 Part 5 你已經會寫 Python script 的時候，想自己裝再裝。前面一律用裸 gdb。

## 一點點預覽：後面章節會用到的東西

這些現在不用裝，但記得存在，免得之後看到不知所云：

| 工具 | 用在 | 章節 |
|---|---|---|
| `valgrind` | heap corruption 對照 | 練習 B |
| `gdbserver` | 遠端 debug | Ch 12 |
| `gdb-multiarch` | 跨架構 debug（ARM / RISC-V） | Ch 12 |
| `readelf` / `objdump` | 看 ELF 與 DWARF | Ch 18 |
| `libdwarf-dev` | 自己寫 DWARF parser | Final Project |

## 動手練習

1. 把 `hello.c` 編出有 `-g` 的版本，在 GDB 裡跑一次上面那整串互動。
2. 再編一份 `-O2` 的版本：`gcc -g -O2 hello.c -o hello-opt`。試試 `print x`，看 `sum` 在 `-O2` 下會發生什麼事（提示：整個 `add()` 會不見）。
3. 故意不加 `-g`，進 gdb 試試 `list` 和 `print`，感受沒有 debug info 是什麼體驗。

## 自我檢核

- [ ] 我能說出 `-g` 跟沒 `-g` 的差別
- [ ] 我知道為什麼學習階段要加 `-O0`
- [ ] 我能在 gdb 裡用 `break` / `run` / `next` / `step` / `print` / `continue`
- [ ] 我驗證過我的 gdb 支援 Python 3

環境準備好了。下一章我們先不急著記指令，而是看看 debugger 這種東西**到底在做什麼** — 它怎麼讓自己的 process 去控制別人的 process？

→ [Ch 1 Debugger 到底在做什麼](./01-debugger-mental-model.md)
