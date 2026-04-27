# Ch 13 — Lazy Binding 與 _dl_runtime_resolve

> 目標：接續 Ch 10 對 PLT 的介紹，深入 lazy binding 的具體機制、`_dl_runtime_resolve` 做了什麼、以及 RELRO 如何防止 GOT overwrite 攻擊。這章讓你能在面試解釋「一個 shared library call 第一次跟第二次的差別」。

## Lazy vs Eager binding

先分清楚：

- **Lazy binding**：symbol 第一次呼叫時才 resolve。快啟動、但第一次 call 慢。
- **Eager binding**：load 時 resolve 所有 symbol。啟動慢、runtime 無額外成本。

默認 lazy，透過 `-Wl,-z,now` 或 `LD_BIND_NOW=1` 切 eager。

**為什麼默認 lazy**：

- 典型程式用到的 symbol 少（只用 `libc` 的 10% function）
- eager resolve 所有 symbol 很浪費
- startup latency 對 interactive 程式重要

**為什麼有時要 eager**：

- Security：lazy 的 GOT 是 writable，可能被攻擊改寫
- Real-time：第一次呼叫的不可預測 latency 不能接受
- Benchmarking：想排除首呼叫的 warmup 成本

## Lazy binding 的運作回顧（Ch 10）

簡短回顧：

```
First call:
    caller → printf@plt → PLT[0] → _dl_runtime_resolve
                              ↓
                           resolve printf → update GOT[printf] → jump to printf

Second call:
    caller → printf@plt → GOT[printf] → printf (直接)
```

關鍵：**第一次後 GOT[printf] 就是 printf 真實地址**，之後每次呼叫只多一個 indirect jump。

## `_dl_runtime_resolve` 做了什麼

glibc 的 `_dl_runtime_resolve` 是 dynamic linker 的核心 function。RISC-V 版：

```c
// 簡化版 pseudo
void *_dl_runtime_resolve(struct link_map *l, Elf_Word reloc_index) {
    // 1. 找 reloc 對應的 relocation entry
    ElfW(Rela) *reloc = &l->l_rela_plt[reloc_index];

    // 2. 查 symbol
    Sym sym = lookup_symbol(l, reloc);

    // 3. 找 symbol 在哪個 library
    ElfW(Addr) value = find_in_library(sym);

    // 4. 更新 GOT
    reloc_target = (ElfW(Addr) *)(l->l_addr + reloc->r_offset);
    *reloc_target = value;

    // 5. 返回 symbol 地址
    return (void *)value;
}
```

關鍵動作：

1. **找 relocation info**：從 PLT[0] 傳過來的 index 查 `.rela.plt`
2. **Symbol lookup**：走 `.dynsym` / hash 表（Ch 10 提過的 `.hash` / `.gnu.hash`）
3. **Scope 搜尋**：按 search order 找（executable → LD_LIBRARY_PATH → default lib path）
4. **寫入 GOT**：改寫 `GOT[N]` 成真實地址
5. **跳到 symbol**：通常 caller 會從 stack 拿返回值跳過去

## Symbol hash 機制

大量 symbol 查詢要效率。`.hash` / `.gnu.hash` 是 hash 表：

- `.hash` 是舊 System V hash，簡單但 hit rate 差
- `.gnu.hash` 是 2006 後 GNU 加的，bloom filter + 更好的 hash，快 40%

現代 binary 都有 `.gnu.hash`。你可以：

```bash
readelf --sections hello | grep hash
```

看到 `.gnu.hash` 表示現代 linker。

## PLT entry 的 encoding

RISC-V PLT 的每個 entry 16 byte（4 條 32-bit 指令），編碼精巧：

```asm
# PLT entry N for symbol X
plt_entry_N:
1:  auipc  t3, %pcrel_hi(got_entry_N)
    l[d/w] t3, %pcrel_lo(1b)(t3)        # t3 = GOT[N]
    jalr   t1, t3                        # jump, t1 = index helper
    nop                                  # alignment
```

初始 GOT[N] 指向 `plt_entry_N + 8`（即 `jalr t1, t3` 的下一條）。所以：

1. 第一次：load GOT[N] = 自己的 jalr 位置 → 跳到自己的下一條（繼續執行）
2. 下一條其實會繼續流向 PLT[0]
3. PLT[0] 呼叫 `_dl_runtime_resolve`
4. resolve 後 GOT[N] 被改成真實地址
5. 未來 load GOT[N] 直接拿到地址 → `jalr` 跳去真正的 function

巧妙在：**初始 entry 被自己跳回來**，靠 PC 的流動自動進入 resolve 路徑。

## `_dl_runtime_resolve` 的 asm stub

glibc 的 `sysdeps/riscv/dl-trampoline.S` 定義：

```asm
_dl_runtime_resolve:
    # 存所有 caller-saved registers (要避免破壞 caller state)
    addi   sp, sp, -...
    sd     t0, 0(sp)
    sd     t1, 8(sp)
    ...                      # 存很多 reg

    # 參數：a0 = link_map, a1 = reloc index
    mv     a0, t0
    mv     a1, t1
    call   _dl_fixup          # C 版 resolver

    # _dl_fixup 返回 symbol 地址到 a0
    mv     t0, a0

    # 還原 registers
    ld     a0, ...(sp)
    ...
    addi   sp, sp, ...

    # 跳到 resolved symbol
    jr     t0
```

**為什麼要 asm**：resolver 本身不能破壞 caller 的任何 register state。C compiler 生的 code 會用 caller-saved register → 必須 asm 手動存/還原全部。

## Dynamic linker scope search

Symbol search 順序對結果有決定性影響。glibc 的規則（簡化）：

1. 當前 namespace 的 **main executable**
2. 它的 **`DT_NEEDED` library** 們（BFS 順序）
3. `LD_PRELOAD` 指定的 library
4. 如果用 `dlopen(lib, RTLD_GLOBAL)` 額外加進來的
5. 系統 `/etc/ld.so.conf` 與 default path

找到第一個就停。

**這造成一些 subtle bug**：

- 同名 symbol 在兩個 library 都有 → 優先 main executable > 早 link 的 `.so`
- `LD_PRELOAD` 可以 hook 任何 function（功能強大但 security 風險）

## `LD_PRELOAD` 的魔法

```bash
cat > myhook.c << 'EOF'
#include <stdio.h>
int puts(const char *s) {
    printf("[hooked] %s\n", s);
    return 0;
}
EOF

gcc -shared -fPIC myhook.c -o libhook.so
LD_PRELOAD=./libhook.so ls   # ls 的 puts 被 hook
```

`LD_PRELOAD` 在 scope search 最前面 → 我們的 `puts` 優先 → 所有呼叫 `puts` 的都走我們的版本。

**這是 dynamic linking 最強大也最危險的 feature**。安全性上 distro 會限制某些 binary（如 setuid）用 `LD_PRELOAD`。

## RELRO：保護 GOT 的機制

lazy binding 的 GOT 是 writable（runtime 要被 dynamic linker 填）。這讓攻擊者能透過 buffer overflow 改 GOT slot → 劫持 control flow。

**RELRO**（Relocation Read-Only）分兩種：

### Partial RELRO（`-Wl,-z,relro`）

load 結束後，把**部分** dynamic section 設 read-only：

- `.dynamic` 設 RO
- `.got`（非 PLT 的）設 RO
- `.got.plt` **仍 writable**（lazy binding 要）

### Full RELRO（`-Wl,-z,relro,-z,now`）

**強制 eager binding** + 全部 GOT 設 RO。

```bash
# 查 binary 是否 full RELRO
checksec --file hello
```

典型輸出：

```
RELRO        STACK CANARY  NX  PIE
Full RELRO   Canary found  NX  PIE
```

Full RELRO 代價：啟動慢（eager resolve 所有 symbol）。好處：GOT hijacking 不可能。

**現代 distro 的 system binary** 多數 Full RELRO。一般應用程式 partial 居多。

## 看 RELRO 在 ELF 裡

```bash
readelf -l hello | grep -A2 RELRO
# GNU_RELRO       0x0000000000001...  0x0000000000001...  0x0000000000001...
#                 0x0000000000000f08  0x0000000000001000  R      0x1
```

`PT_GNU_RELRO` segment 指出要 mprotect 成 RO 的範圍。dynamic linker load 完 relocate 後呼叫 `mprotect` 把這塊設 RO。

## 動態 library 的 init / fini

`.init_array` / `.fini_array` 在 `.so` 被 load / unload 時自動呼叫。glibc 的 `_dl_init` 遍歷所有 loaded `.so` 的 `.init_array`。

```c
__attribute__((constructor)) void lib_init(void) {
    printf("libfoo loaded\n");
}

__attribute__((destructor)) void lib_fini(void) {
    printf("libfoo unloaded\n");
}
```

這對應 C++ 的 global constructor。**很多 library 用這個做初始化**（e.g., 註冊 plugin、建 singleton）。

## PLT / GOT 的效能優化

現代 CPU 對 indirect jump 做很多優化：

- **BTB (Branch Target Buffer)**：記住 indirect jump 的 target
- **Hardware prefetcher**：預抓 GOT entry

所以 lazy binding 的 runtime overhead 幾乎不可見。**只剩第一次呼叫的 `_dl_runtime_resolve` 的 latency**（約 500-1000 cycle）。

**優化建議**：如果你 startup 時間敏感且每個 function 都會用 → `-Wl,-z,now` 集中一次付費。

## `dlopen` / `dlsym` 的運作

```c
void *h = dlopen("libplugin.so", RTLD_LAZY);
void (*f)(void) = dlsym(h, "plugin_init");
f();
```

`dlopen` 觸發 dynamic linker 走 symbol search 一次（加進 scope）、load 完 library、呼叫其 `.init_array`。

**這是 lazy binding 的 runtime 版**：可以執行中加新 library。

## 常見誤會

1. **「Lazy binding 是安全漏洞」**：不完全。配合 Partial RELRO / Stack Canary / ASLR 仍安全。但 full RELRO 確實更嚴。
2. **「LD_BIND_NOW 讓程式更快」**：不。讓 startup 慢、runtime 一樣（或稍快，因為 GOT 在 L1 cache）。多半 security 考量用。
3. **「PLT 是 x86 才有」**：所有 dynamic linked ISA 都有。RISC-V、ARM、MIPS 都有等價物。
4. **「Dynamic linker 每 symbol lookup 都掃所有 library」**：有 hash 表優化。平均 O(1) 找到。
5. **「RELRO 讓 shared library 無 hot reload」**：不。RELRO 只影響單個 `.so` 內的 GOT。plugin hot reload 還是可以（`dlclose` + `dlopen`）。

## 動手練習

1. 寫 hook `malloc` 的 `LD_PRELOAD` library，印出每次 allocation size。
2. 比較 `ldd hello`（lazy）跟 `LD_BIND_NOW=1 ldd hello`（eager）時間。
3. 用 `strace -e openat` 跑一個 dynamic binary 看 dynamic linker load 了哪些 `.so`。
4. 讀 glibc `_dl_fixup()` 實作（約 300 行）。
5. 用 `checksec` 掃自己系統的 `/usr/bin/*`，統計 Full vs Partial RELRO 的比例。

## 自我檢核

- [ ] 我能畫出第一次呼叫跟第二次呼叫的差異路徑
- [ ] 我能解釋 `_dl_runtime_resolve` 的四個核心動作
- [ ] 我知道 Partial 跟 Full RELRO 的差異與 trade-off
- [ ] 我能用 `LD_PRELOAD` hook 一個 function
- [ ] 我知道 PLT / GOT 的 runtime cost 大致量級

Part 4 完。下一章開始 Part 5 — visibility 與 LTO 的互動。這是 shared library maintainer 的必修。

→ [Ch 14 Visibility、LTO 與符號](./14-visibility-and-lto.md)
