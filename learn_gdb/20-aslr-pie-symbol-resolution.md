# Ch 20 — ASLR / PIE / 符號重定位

> 目標：理解 ASLR（位址空間隨機化）、PIE（位置無關執行檔）、shared library 動態載入、GDB 如何跟 dynamic linker 合作即時處理 symbol。

## 為什麼你的斷點位址看起來怪怪的

`objdump -d hello` 可能顯示：

```
0000000000001149 <main>:
    1149:  55                   push   %rbp
    ...
```

但在 gdb 裡：

```
(gdb) p main
$1 = {int (void)} 0x555555555149 <main>
```

位址變成 `0x555555555149`。差了 `0x555555554000`。

這是 PIE + ASLR 造成的。

## ASLR（Address Space Layout Randomization）

**目的**：把 process 各段的實際位址隨機化，讓攻擊者（例如 buffer overflow 後試圖跳到已知位址）難以預測。

Linux 下 ASLR 隨機化：

- Stack 起始位址
- Heap 起始位址
- 共享函式庫的載入位址
- 如果啟用 PIE：main executable 本身的載入位址

Kernel 啟動 process 時在每段映射時加隨機 offset。

### 關 / 開

```bash
# 查看現在設定
cat /proc/sys/kernel/randomize_va_space
# 0 = 關、1 = stack/heap/mmap 隨機、2 = 全開（預設）

# 暫時關（需 root）
sudo sysctl -w kernel.randomize_va_space=0
```

不建議常關。下面會看到 debug 時 ASLR 其實不是問題。

## PIE（Position-Independent Executable）

**傳統的 executable**：linker 在連結時決定了所有絕對位址。main 永遠在 `0x400000` 之類固定位址。ASLR 對它無效。

**PIE**：executable 本身也是 position-independent（像 shared library 一樣可以載入到任意位址）。配合 ASLR，main executable 也可以每次跑都在不同位址。

編譯時：

- `gcc -fpie -pie foo.c` → PIE
- `gcc -no-pie foo.c` → 傳統 absolute 位址

現代 Linux 發行版（Ubuntu、Debian、Fedora）預設 **全部 PIE**。macOS、近代 Windows 也是。

## 實務觀察

```bash
# PIE binary，每次跑位址都不同
$ gcc -g hello.c -o hello_pie       # 預設 PIE
$ ./hello_pie & sleep 0.1; cat /proc/$!/maps | head -3
55d4f... r--p 00000000 ...  /tmp/hello_pie
55d4f... r-xp 00001000 ...  /tmp/hello_pie
...

$ ./hello_pie & sleep 0.1; cat /proc/$!/maps | head -3
5627e... r--p 00000000 ...  /tmp/hello_pie   ← 位址變了
```

```bash
# non-PIE binary
$ gcc -no-pie -g hello.c -o hello_nopie
$ ./hello_nopie & sleep 0.1; cat /proc/$!/maps | head -3
00400000-00401000 r--p 00000000 ...  /tmp/hello_nopie
00401000-00402000 r-xp 00001000 ...  /tmp/hello_nopie
...

$ ./hello_nopie & sleep 0.1; cat /proc/$!/maps | head -3
00400000-00401000 r--p 00000000 ...  /tmp/hello_nopie   ← 固定
```

## GDB 的處理

### `set disable-randomization`

```
(gdb) show disable-randomization
Disabling randomization of debuggee's virtual address space is on.
```

**GDB 預設開著 `disable-randomization`** — 意思是它幫你在跑 inferior 時關掉 ASLR。這就是為什麼你在同個 gdb session 裡連續 `run` 幾次位址都一樣 — GDB 讓 kernel 不要隨機化。

這讓斷點位址穩定，便於 debug。

想保留 ASLR（例如 debug 一個只在 ASLR 下才發生的 bug）：

```
(gdb) set disable-randomization off
```

### PIE 的 load address

GDB 讀 binary 時，可以從 ELF header 看出它是 PIE。啟動 inferior 後，GDB 詢問 kernel 拿實際 load base（透過 `/proc/PID/maps`），然後：

- 把 DWARF / symbol 的「file offset」加上 load base = 實際 runtime 位址
- 下斷點時，把「source 層位址」先翻譯成 runtime 位址
- 所有 user-facing 的位址都顯示 runtime 版

所以你看到的 `0x555555555149` 是 runtime 位址。DWARF 裡存的是 `0x1149`（file offset）。

## Shared library 的處理

Linux 程式通常動態連結 libc 等 shared library。ELF header 記下「我需要 `libc.so.6`」，真正載入是 runtime 由 dynamic linker（`ld-linux.so`）做的。

### dynamic linker 的流程

1. kernel exec 你的 binary → 發現是 PIE/dynamic、跳到 interpreter（`/lib64/ld-linux-x86-64.so.2`）
2. dynamic linker 讀 binary 的 `.dynamic` 段、找到所有 needed libraries
3. mmap 每個 .so 進來（位址隨機 = ASLR + PIE）
4. 做 relocation：填好 GOT / PLT、讓你的 code 可以呼叫 libc 函式
5. 跳到 `_start` → `__libc_start_main` → 你的 `main`

GDB 要在整個過程中一直跟上。

### `_dl_debug_state` — dynamic linker 的 hook

glibc 的 dynamic linker 有個特殊 symbol `_dl_debug_state`。每次 linker 要做「lib load / unload」時，都先呼叫這個空函式。GDB 在這個 symbol 上下內部斷點，每次觸發就重新掃描 linker 的 link map（記錄當前載入的 lib），更新 symbol table。

所以你 attach 到一個已經跑中的 process、dlopen 新 lib 時，GDB 幾乎實時知道並有新 symbol 可用。

### `info sharedlibrary`

```
(gdb) info sharedlibrary
From                To                  Syms Read   Shared Object Library
0x00007ffff7fc3000  0x00007ffff7fee000  Yes         /lib64/ld-linux-x86-64.so.2
0x00007ffff7d8a000  0x00007ffff7f0f000  Yes         /lib/x86_64-linux-gnu/libc.so.6
0x00007ffff7c00000  0x00007ffff7c1f000  Yes         /lib/x86_64-linux-gnu/libpthread.so.0
...
```

### `catch load` / `catch unload`

Ch 6 的 `catch load` 就是在 `_dl_debug_state` 這層做的。

## GOT / PLT 與 lazy binding

外部函式呼叫（例如 `printf`）透過 **PLT + GOT** 機制：

- **PLT**（Procedure Linkage Table）：每個外部函式一個 stub，長這樣：
  ```asm
  printf@plt:
      jmp    *printf@got(%rip)
      push   $0x1
      jmp    _dl_runtime_resolve
  ```
- **GOT**（Global Offset Table）：存實際 function 位址

**Lazy binding**：第一次呼叫 printf 時，GOT 還指向 "go to resolver" 的 stub。Resolver 找到 libc 裡真正的 printf、把位址寫進 GOT。之後呼叫就直接 jmp 到真實位址。

這讓啟動時不用 resolve 所有函式 — 用到才 resolve。

**但有些情境希望不 lazy**（例如 CFI 保護）。環境變數 `LD_BIND_NOW=1` 或 linker flag `-Wl,-z,now` 禁用 lazy binding。

## Debug 時的影響

### 為什麼 step 進 printf 第一次會進 dynamic linker

```
(gdb) step
__GI___libc_start_call_main ...  ← 經過動態連結器
```

第一次呼叫 printf 走 resolver。你會 step 進去看到 linker 的 code。之後就直接到 libc 的 printf。

### `break printf` 在 lib 載入前

如果你 `break printf`、然後 `run`，GDB 發現 printf symbol 還沒在當前 object file 裡（libc 還沒載）。GDB 會把它設成 **pending breakpoint**：

```
(gdb) break printf
Function "printf" not defined.
Make breakpoint pending on future shared library load? (y or [n]) y
```

等 libc 載入、`_dl_debug_state` 觸發、GDB 重新 resolve，pending 斷點自動變 real。

## Build-ID — 找對 binary 的識別

ELF 的 `.note.gnu.build-id` 段存一個 SHA1 hash，代表這份 binary 的 content。GDB 用它做兩件事：

1. 找 stripped binary 的 debug info：`/usr/lib/debug/.build-id/XX/YYYYYY...`
2. 確認 core dump 跟 binary 一致

```bash
$ readelf -n hello | grep Build
    Build ID: 6d7a8e...

$ ls /usr/lib/debug/.build-id/6d/
7a8e...
```

很多發行版把 libc debug info 放在 `/usr/lib/debug/` 並用 build-id 連結。裝 `libc6-dbg`（Ubuntu）後你 step 進 libc 才會有 symbol。

## 環境變數快速備忘

| 變數 | 作用 |
|---|---|
| `LD_LIBRARY_PATH` | 額外的 lib 搜尋路徑 |
| `LD_PRELOAD` | 強制先載的 lib（hook / override 用） |
| `LD_DEBUG=files` / `libs` / `all` | 印 linker 詳細過程 |
| `LD_BIND_NOW=1` | 關 lazy binding |
| `LD_SHOW_AUXV=1` | 印 kernel 傳的 auxiliary vector |

`LD_DEBUG=libs` 在 debug 「找不到 lib」時特別好用。

## 常見坑

1. **斷點位址在 gdb 裡顯示 `0x1149`，在 `objdump` 裡也是 `0x1149`，但 runtime 看 `/proc/.../maps` 是 `0x555555555149`**：這是 PIE 正常現象。GDB 會自動換算。
2. **外部 binary 的位址算不對**：ASLR。用 `set disable-randomization off` 或觀察 runtime 位址。
3. **`break printf` 回 `No symbol`**：libc debug info 沒裝，或 libc 還沒載入。`y` 接受 pending，或 `run` 後重試。
4. **`info sharedlibrary` 沒顯示你期望的 lib**：dlopen 動態載入的 lib，要等程式執行到 dlopen 之後才看得到。
5. **`core` 打不開，顯示 「不能找到 libc.so.6」**：core 是跨機器拿回來的，本機沒對應版本 libc。`set sysroot /path/to/copy-of-target-libs`。
6. **Stripped binary 完全沒 symbol**：核心沒 symbol、但 dynamic symbol（`.dynsym`）通常還在（用於 dynamic linking）。`info functions` 可以看到 PLT 裡的項。

## 動手練習

### 練習一：PIE vs non-PIE

```bash
gcc -g hello.c -o hello_pie
gcc -g -no-pie hello.c -o hello_nopie

./hello_pie &
sleep 0.5
grep hello_pie /proc/$!/maps

# 再跑幾次，觀察位址變化

./hello_nopie &
sleep 0.5
grep hello_nopie /proc/$!/maps
```

### 練習二：ASLR 的影響

```bash
sudo sysctl -w kernel.randomize_va_space=0
./hello_pie &
grep hello_pie /proc/$!/maps        # 位址變固定了

sudo sysctl -w kernel.randomize_va_space=2
```

### 練習三：在 GDB 裡看

```
gdb -q ./hello_pie
(gdb) p main
$1 = ... 0x555555...

(gdb) start
(gdb) info proc mappings
... 看 load base ...

(gdb) set disable-randomization off
(gdb) run
(gdb) info proc mappings
... load base 變了 ...
```

### 練習四：lazy binding

```c
// lazy.c
#include <stdio.h>
int main(void) {
    puts("first");
    puts("second");
    return 0;
}
```

```
gdb -q ./lazy
(gdb) b *0x... (第一個 puts 的 call 指令)
(gdb) r
(gdb) disas main
   ... call puts@plt ...

(gdb) x/3i puts@plt
=> 0x... jmp    *0x... <puts@got>
   0x... push   $0x...
   0x... jmp    _dl_runtime_resolve

(gdb) x/gx 0x... <puts@got>
... lazy 的 stub 位址 ...
```

第一次呼叫後再看 puts@got：

```
(gdb) n   ; 執行過一次 puts
(gdb) x/gx 0x... <puts@got>
... 真實 puts 位址 ...
```

### 練習五：dlopen

```c
#include <dlfcn.h>
int main(void) {
    void *h = dlopen("libm.so.6", RTLD_NOW);
    double (*cos)(double) = dlsym(h, "cos");
    printf("%f\n", cos(0));
    dlclose(h);
    return 0;
}
```

```
gdb -q ./dl
(gdb) catch load libm
(gdb) r
...
Catchpoint 1 (loaded /lib/x86_64-linux-gnu/libm.so.6), ...
(gdb) info sharedlibrary
... libm 現在有了 ...
```

## 自我檢核

- [ ] 我能解釋 ASLR 是什麼、在哪些 segment 生效
- [ ] 我知道 PIE 跟 non-PIE binary 的差異
- [ ] 我知道 GDB 預設 `disable-randomization on`
- [ ] 我能說出 dynamic linker 的 `_dl_debug_state` hook 機制
- [ ] 我知道 pending breakpoint 會在 lib load 後自動 resolve
- [ ] 我知道 GOT / PLT 的 lazy binding 怎麼運作
- [ ] 我能用 build-id 對應 stripped binary 跟 debug info

最後一章原理：frame unwinding 怎麼做、inferior function call 怎麼在 target process 裡「跑我們的 code」。

→ [Ch 21 Frame unwinding 與 inferior call](./21-frame-unwinding-and-inferior-call.md)
