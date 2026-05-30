# Ch 40 — ASLR / PIE / 符號重定位

> **目標**：搞懂為什麼位址每次執行都不同、怎麼把 runtime 位址對回符號。理解 ASLR、PIE、load bias、GOT/PLT、符號重定位，以及 GDB 怎麼處理這些（`set disable-randomization`）。這是逆向、pwn、Ch 41 自寫 debugger 都繞不開的位址問題。

> **環境**：GDB 13/14，Linux x86_64，gcc（預設產 PIE）。

## 為什麼位址會「跑來跑去」

你逆向時一定遇過：今天 `main` 在 `0x5555_5555_5149`，明天變 `0x5563_xxxx_5149`——同一個程式，位址每次不同。或者你記下一個位址，重開 GDB 就失效了。這不是 bug，是 **ASLR**（位址空間佈局隨機化）。

理解位址怎麼來的，你才能：

- 在 PIE 程式裡下對位址斷點
- 算出 ASLR 的隨機基址（pwn 的 leak 利用）
- 把 leak 出的 runtime 位址對回符號
- 自己寫 debugger 時正確處理位址（Ch 41）

## ASLR：為什麼隨機化

ASLR 是 OS 的安全機制——每次執行把程式的各段（程式碼、stack、heap、library）載入到**隨機位址**。目的：讓攻擊者無法預測「shellcode 在哪、libc 在哪」，大幅提高 exploit 難度。

```
   無 ASLR（每次一樣）            有 ASLR（每次隨機）
   程式碼  0x400000              程式碼  0x5555_xxxx_x000
   libc    0x7ffff7a00000        libc    0x7fxx_xxxx_x000
   stack   0x7fffffffe000        stack   0x7ffx_xxxx_x000
   heap    0x602000              heap    0x55xx_xxxx_x000
   ↑ 攻擊者可預測                ↑ 攻擊者要先 leak 才知道
```

控制 ASLR：

```bash
cat /proc/sys/kernel/randomize_va_space   # 0=關 1=部分 2=完全（預設）
```

## PIE：讓程式碼也能隨機

ASLR 要能隨機化**程式碼段**，程式必須編成 **PIE**（Position-Independent Executable）——位置無關，能載入到任意位址。現代 distro 的 gcc 預設產 PIE。

```bash
gcc hello.c -o hello_pie          # 預設 PIE（位址隨機）
gcc -no-pie hello.c -o hello_nopie # 非 PIE（固定位址 0x400000）
file hello_pie                     # "PIE executable"
file hello_nopie                   # "executable"
```

差別：

- **PIE**：載入位址隨機（`0x5555...`），ELF 裡的位址是相對 offset，載入時加上隨機的 **load bias**。
- **非 PIE**：固定載到 `0x400000`，ELF 裡就是絕對位址。逆向簡單（位址固定），但不安全。

```
   PIE:  檔案 offset 0x1149  +  load bias 0x555555554000  =  runtime 0x555555555149
   非PIE: 檔案位址 0x401149   =  runtime 0x401149（不變）
```

## GDB 怎麼處理：`set disable-randomization`

GDB 預設**關掉 ASLR**（為了 debug 方便——每次位址一致）：

```
(gdb) show disable-randomization
Disabling randomization of debuggee's virtual address space is on.
```

所以你在 GDB 裡 `run` 兩次，PIE 程式的位址**一樣**（GDB 關了 ASLR）——但直接在 shell 跑就每次不同。這解釋了一個經典困惑：「GDB 裡位址固定，但實際跑/pwn 時位址變了」。

要在 GDB 裡重現「真實的 ASLR 行為」（測 exploit）：

```
(gdb) set disable-randomization off    # 讓 GDB 不關 ASLR，每次 run 位址隨機
```

> 這是 pwn 的關鍵設定。開發 exploit 時用 `off` 重現真實隨機，驗證 leak 計算對不對。Ch 3 提過這個設定，現在你懂為什麼了。

## load bias：算出隨機基址

PIE 程式的 runtime 位址 = 檔案 offset + load bias。要把兩者互轉：

```
(gdb) run
(gdb) info proc mappings           # 看程式載到哪
      Start Addr        ...   objfile
      0x555555554000   ...   /path/hello_pie    ← load bias（程式基址）
(gdb) print &main                  # runtime 位址
$1 = 0x555555555149 <main>
(gdb) info symbol 0x555555555149   # 反查
main in section .text
```

算 offset：`runtime - load_bias = 0x555555555149 - 0x555555554000 = 0x1149`（檔案 offset）。

pwn 的核心計算：leak 出一個 runtime 位址（例如某函式），減掉它的檔案 offset，得到 load bias，再加任意 offset 算出其他東西的 runtime 位址。GDB 的 `info proc mappings` + `info symbol` 是驗證這個計算的工具。

## GOT / PLT：動態連結的位址

程式呼叫 libc 的 `printf`——但 libc 也是 ASLR 隨機載入的，編譯時不知道 `printf` 在哪。解法是 **GOT/PLT** 間接層：

```
   程式呼叫 printf:
   call printf@plt                  → 跳到 PLT 條目
        │
   PLT[printf]:  jmp *GOT[printf]   → 透過 GOT 間接跳
        │
   GOT[printf]:  （第一次）指向 resolver → 解析出 printf 真實位址，寫回 GOT
                 （之後）   直接是 printf 的 runtime 位址
```

- **PLT**（Procedure Linkage Table）：跳板，每個外部函式一個。
- **GOT**（Global Offset Table）：存外部符號的真實 runtime 位址，由動態連結器填。

逆向/pwn 看 GOT：

```
(gdb) info functions printf
(gdb) x/i 'printf@plt'             # 看 PLT 跳板
(gdb) p (void*)printf             # printf 的真實位址（解析後）
(gdb) x/gx &printf@got.plt        # 看 GOT 條目（指向真實 printf）
```

GOT overwrite（改 GOT 條目指向 shellcode）是經典 exploit；GDB 看 GOT 是逆向動態連結、debug PLT 問題的關鍵（呼應 elf_linking / pentest 課程）。

## 符號重定位

ELF 載入時，動態連結器要「填空」——把所有「編譯時不知道、載入時才確定」的位址填進去。這個過程叫 **relocation**：

- PIE 程式碼裡的位址要加 load bias
- GOT 條目要填外部符號的真實位址
- 各種 `R_X86_64_*` relocation 類型

```bash
readelf -r hello_pie              # 看 relocation 表
objdump -R hello_pie
```

GDB 載入時知道這些 relocation，所以能把符號對到正確的 runtime 位址。Ch 41 你的 mini debugger 處理 PIE 時也要算 load bias（從 `/proc/<pid>/maps` 讀基址）。

## 一個完整的「位址對回符號」

逆向常見任務：你有一個 leak 出的 runtime 位址 `0x7f1234567890`，想知道它是什麼：

```
(gdb) info proc mappings           # 它落在哪個 objfile？
      0x7f1234500000 ... libc.so.6    ← 落在 libc
(gdb) info symbol 0x7f1234567890   # 反查符號
system + 16 in section .text of /lib/x86_64-linux-gnu/libc.so.6
```

或手動：`runtime - libc_base = offset`，再 `nm libc.so | grep <offset>`。這是 pwn 的「leak → 算 libc 基址 → 算其他函式位址」的核心循環。

## 踩雷集錦

1. **GDB 裡位址固定，實際跑變了**：GDB 預設 `disable-randomization on`。測 exploit 要 `set disable-randomization off`。
2. **PIE 位址斷點重開失效**：`break *0x5555...` 用的是這次 run 的隨機位址，重開就變。用符號（`break main`）或相對 offset。
3. **以為 `0x400000` 是常態**：那是非 PIE。現代預設 PIE，基址 `0x5555...`（隨機）。
4. **算 load bias 算錯**：`runtime - file_offset = bias`，別反。`info proc mappings` 的第一個程式條目是 bias。
5. **GOT 第一次 vs 之後**：lazy binding 下，GOT 條目第一次呼叫前指向 resolver，呼叫後才是真實位址。`p printf` 前可能還沒解析。`set environment LD_BIND_NOW=1` 強制提前解析。
6. **stack/heap 位址也隨機**：不只程式碼，stack/heap/libc 都 ASLR。記 stack 上的位址重開無效。
7. **kernel 的 KASLR**（Ch 37）：kernel 也有 ASLR，debug kernel 要 `nokaslr`。

## 進階：再往深一層

- **relocation 類型**：`R_X86_64_RELATIVE`（PIE 內部位址加 bias）、`R_X86_64_GLOB_DAT`（GOT）、`R_X86_64_JUMP_SLOT`（PLT）等——`readelf -r` 看，呼應 elf_linking 課程。
- **RELRO**：`Full RELRO` 讓 GOT 在啟動後變唯讀（防 GOT overwrite）。`checksec` 看。pwn 要先確認 RELRO 等級。
- **lazy vs now binding**：`LD_BIND_NOW` / `-Wl,-z,now` 啟動時解析所有符號（vs lazy 第一次呼叫才解析）。影響 GOT 何時被填。
- **`vmmap` / pwndbg 的記憶體視圖**：pwn 插件把 `info proc mappings` 美化成彩色 vmmap，標出哪段可寫可執行——Final Project 可做。
- **TLS 與 `fs` base**：thread-local storage 透過 `fs` 暫存器定位，也有自己的「基址」問題。
- **自寫 debugger 的 PIE 處理**（Ch 41）：從 `/proc/<pid>/maps` 讀程式基址，把 DWARF 的檔案 offset 加上基址才是 runtime 位址——否則斷點下錯地方。

## 動手練習

1. `gcc hello.c -o pie` 和 `gcc -no-pie hello.c -o nopie`，各在 shell 跑兩次 `(gdb -batch -ex 'break main' -ex run -ex 'p &main')`，比較 PIE 位址變、非 PIE 不變。注意 GDB 預設關 ASLR。
2. `set disable-randomization off` 後 `run` 兩次 PIE 程式，看位址這次真的隨機了。
3. `info proc mappings` 找 PIE 程式的 load bias，`print &main` 拿 runtime 位址，手算檔案 offset，用 `info symbol` 驗證。
4. `x/i 'printf@plt'` 看 PLT 跳板，`x/gx &printf@got.plt` 看 GOT 條目，理解動態連結間接層。
5. 拿一個 libc 函式的 runtime 位址，`info symbol` 反查，再手動 `runtime - libc_base` 算 offset 對照 `nm`。
6. `readelf -r pie | head` 看 relocation 表，找 `R_X86_64_RELATIVE`（PIE 內部）與 `R_X86_64_JUMP_SLOT`（PLT）。

## 本章重點整理

- ASLR 每次執行隨機化各段位址（安全機制）；PIE 讓程式碼段也能隨機（runtime = 檔案 offset + load bias）。
- GDB 預設 `disable-randomization on`（位址固定好 debug）；測 exploit 要 `off` 重現真實隨機。
- load bias = `info proc mappings` 的程式基址；runtime ↔ 檔案 offset 互轉是 pwn leak 計算的核心。
- GOT/PLT 是動態連結的間接層（外部函式真實位址載入時才填）；GOT overwrite 是經典 exploit。
- relocation 是載入時「填位址空格」；自寫 debugger 處理 PIE 要從 `/proc/maps` 讀基址。

## 自我檢核

- [ ] 為什麼「GDB 裡位址固定但實際跑會變」？怎麼讓 GDB 重現真實隨機？
- [ ] PIE 的 runtime 位址怎麼從檔案 offset 算出來？load bias 哪裡看？
- [ ] GOT/PLT 解決什麼問題？為什麼 GOT overwrite 能被利用？
- [ ] 拿到一個 leak 的 runtime 位址，怎麼對回符號 / 算 libc 基址？
- [ ] 自寫 debugger 處理 PIE 程式時，位址要怎麼調整？

## 延伸閱讀

### 部落格 / 文章

- **[Position Independent Code (PIC) in shared libraries](https://eli.thegreenplace.net/2011/11/03/position-independent-code-pic-in-shared-libraries)** — Eli Bendersky
  - **這篇說什麼**：PIC/PIE 怎麼做到位置無關、GOT/PLT 的機制。
  - **讀哪裡**：整篇 + 它的 x64 續篇；本章 GOT/PLT 的可視化詳解。
  - **為什麼值得讀**：理解動態連結與位址無關的最佳資源。

- **[ASLR and how to bypass it](https://ir0nstone.gitbook.io/notes/types/stack/aslr)** — ir0nstone pwn notes
  - **這篇說什麼**：ASLR 的 pwn 視角——leak、算基址、繞過。
  - **和本章的關聯**：把位址原理用到 exploit（呼應 pentest/kernel_pwn）。

### 工具 / 規格

- **[System V AMD64 ABI](https://gitlab.com/x86-psABIs/x86-64-ABI)** 的 relocation 章節 與 **[ELF spec](https://refspecs.linuxfoundation.org/elf/elf.pdf)**
  - **讀哪裡**：relocation types、dynamic linking。
  - **和本章的關聯**：relocation 的權威；呼應 elf_linking 課程。

最後一塊原理拼齊了——ptrace（Ch 2）+ DWARF（Ch 38）+ 斷點/step（Ch 39）+ 位址（Ch 40）。下一章把它們全部組裝起來：自己寫一個 mini debugger。

→ [Ch 41 用 ptrace + DWARF 寫 mini debugger](./41-ptrace-dwarf-mini-debugger.md)
