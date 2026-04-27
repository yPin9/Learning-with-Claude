# Ch 10 — 動態連結全貌：GOT / PLT / .dynamic

> 目標：從零理解 dynamic linking 的運作。`.dynamic` 怎麼當 roadmap、GOT 怎麼存地址、PLT 怎麼做 function call trampoline。讀完你能在面試解釋「從 `printf()` 呼叫到實際執行之間經過哪些跳躍」。

## 為什麼要動態連結

static linking 的世界：每個 binary 含自己需要的所有 library code。一個 `ls` 50 KB、一個 `cp` 55 KB、library 的 `printf` 重複 100 次。

dynamic linking：

- **省空間**：glibc 的 `printf` 只在 `libc.so.6` 裡一份、所有 binary 共用
- **省記憶體**：runtime `libc.so.6` 只 load 一次，映射到所有 process 的 VA
- **可 update**：修 `libc` 的 security bug 不用重編所有 binary
- **方便 plugin**：`dlopen("plugin.so")` 執行時載入

代價：**複雜**。load 時要做 symbol resolution、要有 GOT / PLT、要有 `ld.so`（dynamic linker）。

## 三個角色

```
┌─────────────────────────────────────┐
│ Executable (我的 program)            │
│   依賴 libfoo.so, libc.so.6          │
└─────────────────────────────────────┘
          │ 透過
          ▼
┌─────────────────────────────────────┐
│ Shared Objects (.so)                 │
│   libfoo.so, libc.so.6 ...           │
└─────────────────────────────────────┘
          │ 被
          ▼
┌─────────────────────────────────────┐
│ Dynamic Linker (ld.so / ld-linux.so) │
│   load、relocate、symbol resolve      │
└─────────────────────────────────────┘
```

dynamic linker 本身**也是一個 `.so`**，但它是 kernel 直接 load 的（透過 PT_INTERP）。

## `.interp` section

你的 executable 裡有：

```bash
$ readelf -x .interp hello
  0x00000238 2f6c6962 2f6c642d 6c696e75 782d7269 /lib/ld-linux-ri
  0x00000248 73637636 342d6c70 36346400          scv64-lp64d.
```

字串 `/lib/ld-linux-riscv64-lp64d.so.1` 告訴 kernel：「exec 我之前，先 load 這個 interpreter、控制權先給它」。

kernel 的 `execve()` 流程：

1. mmap 你的 executable 到 memory
2. 檢查有 PT_INTERP → mmap interpreter
3. set `pc` 到 interpreter 的 entry
4. 跳進 interpreter 跑

**interpreter 再負責**：load 你的 executable 依賴的所有 `.so`、做 relocation、跳到你的 `_start`。

## `.dynamic` section：dynamic linker 的 roadmap

```bash
$ readelf -d hello

Dynamic section at offset 0x1f00 contains 24 entries:
  Tag        Type                         Name/Value
 0x0000000000000001 (NEEDED)             Shared library: [libc.so.6]
 0x000000000000000c (INIT)               0x5b0
 0x000000000000000d (FINI)               0x850
 ...
 0x0000000000000005 (STRTAB)             0x358
 0x0000000000000006 (SYMTAB)             0x280
 0x000000000000000a (STRSZ)              105 (bytes)
 0x000000000000000b (SYMENT)             24 (bytes)
 0x0000000000000015 (DEBUG)              0x0
 0x0000000000000003 (PLTGOT)             0x2050
 0x0000000000000002 (PLTRELSZ)           72 (bytes)
 0x0000000000000014 (PLTREL)             RELA
 0x0000000000000017 (JMPREL)             0x4a0
 0x0000000000000007 (RELA)               0x440
 0x0000000000000008 (RELASZ)             96 (bytes)
 0x0000000000000009 (RELAENT)            24 (bytes)
 ...
```

**這張表是 dynamic linker 的 roadmap**。每個 tag 告訴它一件事：

- `NEEDED`：要 load 哪個 `.so`
- `STRTAB` / `SYMTAB`：dynamic symbol table 在哪
- `RELA`：relocation 表在哪
- `PLTGOT`：GOT 在哪
- `INIT` / `FINI`：初始化 / 終結函式在哪

dynamic linker 讀完 `.dynamic` 就知道要做什麼。

## GOT — Global Offset Table

GOT 是一張**地址表**。裡面存外部 symbol 的 runtime 地址。

### 為什麼要有 GOT

考慮 shared library（`.so`）呼叫外部函式：

```c
extern int printf(const char *, ...);
void foo(void) { printf("hi\n"); }
```

編譯成 `.so` 時，`printf` 的地址**還沒決定**（runtime load 到 `libc.so` 哪個地址也不知道）。如果 code 裡寫死：

```asm
call 0x12345    # printf 的地址
```

那 load 時就要改這條指令 → `.text` 變 writable → 違反 RX 原則。

**GOT 的解法**：code 永遠讀一個「指向 printf 地址的 slot」，dynamic linker load 時把真實地址填進 slot：

```asm
auipc t0, %got_pcrel_hi(printf)
ld    t0, %pcrel_lo(1b)(t0)     # t0 = *(GOT[printf])
jalr  t0                         # call
```

`.text` 讀 GOT、GOT 讀 printf 地址。`.text` 只讀不寫、GOT 可寫。

### GOT 佈局

```
.got:
  offset 0:  _DYNAMIC 地址
  offset 8:  link_map 指標
  offset 16: _dl_runtime_resolve 地址
  offset 24: symbol A 的地址
  offset 32: symbol B 的地址
  ...
```

前三個 slot 是 **runtime 基礎設施**（RISC-V ABI 規定）。

### GOT 讀取指令：RISC-V 的寫法

```asm
# 讀 got[printf] 得到 printf 真實地址
auipc t0, %got_pcrel_hi(printf)      # R_RISCV_GOT_HI20
ld    t0, %pcrel_lo(1b)(t0)          # R_RISCV_PCREL_LO12_I
```

或在較新的 code model：

```asm
la    t0, printf                 # pseudo, 展開成上面兩條 (GOT 版 la)
```

## PLT — Procedure Linkage Table

PLT 是 function 呼叫的 **trampoline**。每個外部 function 有一個 PLT entry。

### 為什麼要有 PLT

GOT 需要 dynamic linker 填值。**lazy binding** 下，不是每個外部 function 都立刻 resolve，而是**第一次呼叫時才 resolve**。這需要一段 code 做「resolve 或跳」的判斷 —— 那就是 PLT entry。

### RISC-V 的 PLT entry 長這樣

```asm
# PLT[0] - shared code for lazy resolution
plt0:
    auipc  t2, %pcrel_hi(_GLOBAL_OFFSET_TABLE_)
    sub    t1, t1, t3
    l[w|d] t3, %pcrel_lo(...)(t2)   # t3 = GOT[1] = link_map
    addi   t1, t1, -0x40            # t1 = reloc index
    l[w|d] t0, %pcrel_lo(...)(t2)   # t0 = GOT[2] = &_dl_runtime_resolve
    ...
    jalr   t0                        # 進 dl runtime

# PLT[N] - individual function (printf, etc.)
printf_plt:
1:  auipc  t3, %pcrel_hi(got_slot_of_printf)
    l[w|d] t3, %pcrel_lo(1b)(t3)     # t3 = *GOT[printf]
    jalr   t1, t3                    # 跳過去（t1 保留 return）
    nop
```

**初始狀態**：GOT[printf] 指向 `printf_plt + 8`（即 `jalr` 的下一條）。所以第一次：

1. 呼叫者跳進 `printf_plt`
2. load `GOT[printf]` = `printf_plt + 8`
3. 跳到 `printf_plt + 8`
4. 繼續執行（某些 offset 計算）
5. 最終跳到 `plt0`
6. `plt0` 呼叫 `_dl_runtime_resolve(reloc_idx, link_map)`
7. `_dl_runtime_resolve` 找到 printf 真實地址
8. **改寫 `GOT[printf]` 指向真實的 printf**
9. 跳到 printf 執行
10. 未來再呼叫：直接從 GOT 讀出真實地址、一跳到位

**第二次以後只花一次 indirect jump**。這叫 lazy binding。Ch 13 深入。

## 完整的 function call 軌跡

以 `printf("hi\n")` 為例，從 source 到硬體指令：

```
Source: printf("hi");
  │
  │ compile
  ▼
Instructions:
  auipc a0, %pcrel_hi("hi\n")       # load 字串地址
  addi  a0, a0, %pcrel_lo(1b)
  auipc ra, %pcrel_hi(printf@plt)
  jalr  ra, %pcrel_lo(2b)(ra)        # call printf@plt
  │
  │ 跳到 PLT
  ▼
PLT entry for printf:
  auipc t3, %pcrel_hi(GOT_slot)
  ld    t3, %pcrel_lo(..)(t3)        # load GOT[printf]
  jalr  t1, t3                       # jump through GOT slot
  │
  │ (第一次) GOT[printf] 指向 plt0
  │ (第二次+) GOT[printf] 指向真實 printf
  ▼
(第一次) 走 _dl_runtime_resolve → 解析 → 改 GOT → 跳 printf
(第二次) 直接跳到 libc.so 裡的 printf
  │
  ▼
libc.so 的 printf() 實際執行
```

這是 Linux userspace 每一次 library function call 走的路。理解這個 = 理解 dynamic linking。

## `.rela.dyn` vs `.rela.plt`

兩個 relocation section：

- **`.rela.dyn`**：一般 data relocation（如 initial GOT entries）。dynamic linker 在 load 時一次做完。
- **`.rela.plt`**：PLT 用的 function relocation。lazy 模式下延遲到第一次呼叫才做。

看一個 binary：

```bash
riscv64-linux-gnu-readelf -r hello
```

會印出兩塊。`rela.dyn` 通常幾條、`rela.plt` 跟 external function 數量對應。

## `RELRO`：post-relocation read-only

現代 binary 啟用 RELRO（Relocation Read-Only）。分兩種：

- **Partial RELRO**：`.got` 可寫（lazy binding 需要），`.dynamic` 等設 RO
- **Full RELRO**（`-Wl,-z,now,-z,relro`）：**強制 eager binding**（開機時 resolve 全部 symbol），所有 GOT 設 RO

Full RELRO 能防止「攻擊者改 GOT slot 劫持 control flow」(GOT overwrite 攻擊)，但 startup 時間變長（resolve 所有 symbol）。

多數 distro 預設 partial。security-critical 服務常用 full。

## `ldd` 跟 `ld.so`

```bash
ldd /usr/bin/cat
# linux-vdso.so.1 (0x00007ffc...)
# libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x00007f...)
# /lib64/ld-linux-x86-64.so.2 (0x00007f...)
```

`ldd` 列出 dynamic 依賴。實務上它就是跑「用 dynamic linker 假跑一遍這個 binary」。

**警告**：`ldd` 在非 host architecture 的 binary 上不 work（因為要真的跑 ld.so）。跨平台用 `readelf -d` 查 NEEDED。

## LD_* 環境變數（debug 神器）

```
LD_DEBUG=libs,bindings   # 印出 library search 跟 binding 過程
LD_DEBUG=all              # 全部
LD_LIBRARY_PATH           # 額外的 library search path（只在開發用，生產不建議）
LD_PRELOAD                # 先 load 某個 .so（hook 用）
LD_BIND_NOW=1             # 強制 eager binding
LD_TRACE_LOADED_OBJECTS=1 # 等於 ldd
```

debug dynamic linking 問題：

```bash
LD_DEBUG=all ./hello 2>&1 | head -50
```

你會看到 dynamic linker 的內部決策 —— 超詳細。

## RISC-V 的 Dynamic linker 實作

Linux 的 ld.so 是 glibc 的一部分。RISC-V port 的 arch-specific code：

- **glibc**: `sysdeps/riscv/dl-machine.h`
- **musl**: `arch/riscv64/reloc.h`

讀 `dl-machine.h` 的 `elf_machine_rela()` function 你能看到每個 RISC-V relocation type 的 runtime 處理。大約 200 行。

## 常見誤會

1. **「只有 `.so` 需要 GOT」**：executable 也可能要 GOT（if dynamic linked）。只有 static 的 executable 不用。
2. **「PLT 是 x86 專有」**：所有 dynamic linking 架構都有 PLT 類概念。RISC-V / ARM 都有。
3. **「Dynamic linker = kernel」**：不。Dynamic linker 是 userspace `.so`，由 kernel 起跑後接手。
4. **「GOT 是 compile-time 就填好」**：不。GOT slot 的初值是 PLT 起點；dynamic linker 在 load 時改寫（eager）或 lazy 第一次呼叫改寫。
5. **「Dynamic linking 比 static 慢」**：第一次呼叫慢（PLT + 可能 resolve）。之後跟 static 差一個 indirect jump（可忽略）。但 startup 時間會延長。

## 動手練習

1. `readelf -d hello` 列印你的 `hello` 的 `.dynamic` 並解讀每個 tag。
2. `objdump -d hello | grep -A5 "puts@plt"` 看 PLT entry 長什麼樣，對照本章描述。
3. `LD_DEBUG=all ./hello 2>&1 | less` 看 dynamic linker 的啟動過程。注意它 load 了哪些 `.so`、relocate 了多少 entry。
4. 比較 `-Wl,-z,now` 跟沒加（lazy）的 binary 啟動時間：`time ./hello` 跑幾次。
5. 用 `patchelf --set-interpreter` 把 `.interp` 改成不存在的路徑，觀察 kernel 的錯誤訊息。

## 自我檢核

- [ ] 我能畫出「從 call printf 到 libc 的 printf」完整流程
- [ ] 我能解釋 GOT 跟 PLT 各自的職責
- [ ] 我能讀 `readelf -d` 的輸出並解釋每個 tag
- [ ] 我知道 RELRO 兩個級別的差異
- [ ] 我能用 `LD_DEBUG` 跟 `readelf -r` 診斷 dynamic linking 問題

Part 4 的第一章完。下一章專講 PIC / PIE / code model —— 為什麼 `-fPIC` 這個 flag 幾乎變成必備、不同 code model 對 RISC-V 程式的影響。

→ [Ch 11 PIC / PIE 與 code model](./11-pic-pie-code-model.md)
