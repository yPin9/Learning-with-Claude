# Ch 4 — eBPF ISA 與 JIT 編譯器

> **目標**：理解 eBPF 的指令集架構（ISA）——11 個暫存器、64-bit 指令格式、主要指令類別——以及 JIT compiler 如何把 BPF bytecode 翻譯成 native x86-64 code，這讓你在看 verifier log 和 debug 的時候能讀懂底層在做什麼。

## 為什麼需要這個？

你不需要手寫 BPF bytecode（就像你不需要手寫 x86 assembly）。但在這兩種情況下，理解底層 ISA 非常有幫助：

1. **讀 verifier log**：verifier 拒絕你的程式時，錯誤訊息是以 BPF 指令為單位的（"register R0 is not initialized at insn 42"）——如果你不知道什麼是「register R0」，你看不懂這個訊息

2. **效能 debug**：`bpftool prog dump xlated` 輸出的是 BPF 指令；`bpftool prog dump jited` 輸出的是 JIT 後的 native 指令。能讀這些輸出才能找出 hot path 在哪

3. **理解限制的原因**：為什麼 BPF 程式不能 sleep？為什麼 stack 只有 512 bytes？這些限制的根源在 ISA 和 JIT 的設計選擇上

## 先建立直覺：eBPF 像一個精簡的 RISC CPU

eBPF 的設計參考了 RISC 處理器（類似 ARM 或 RISC-V）：

- 11 個通用暫存器（Classic BPF 只有 2 個）
- Load-store 架構（ALU 只操作暫存器，記憶體存取是獨立指令）
- 固定長度指令（8 bytes，或 16 bytes 的 wide instruction）
- 64-bit 運算（Classic BPF 是 32-bit）

```
eBPF Virtual CPU

暫存器（全部 64-bit）：
  r0   — 回傳值 / 傳入 BPF-to-BPF 呼叫的結果
  r1   — 函式第 1 個參數（進入 program 時是 ctx）
  r2   — 函式第 2 個參數
  r3   — 函式第 3 個參數
  r4   — 函式第 4 個參數
  r5   — 函式第 5 個參數
  r6–r9 — callee-saved（BPF-to-BPF call 保存這些）
  r10  — read-only frame pointer（stack pointer，只能讀）

特殊：
  pc   — program counter（不能直接存取）
  stack — 512 bytes（r10 - offset 定址）
```

和 x86-64 的對應關係（JIT 會做這個映射）：

| BPF register | x86-64 register | 角色 |
|---|---|---|
| r0 | rax | 回傳值 |
| r1 | rdi | 第 1 個參數 |
| r2 | rsi | 第 2 個參數 |
| r3 | rdx | 第 3 個參數 |
| r4 | rcx | 第 4 個參數 |
| r5 | r8  | 第 5 個參數 |
| r6 | rbx | callee-saved |
| r7 | r13 | callee-saved |
| r8 | r14 | callee-saved |
| r9 | r15 | callee-saved |
| r10| rbp | frame pointer |

這個映射讓 JIT 可以直接把 BPF 暫存器映射到 x86-64 暫存器，幾乎不需要 spilling，效能很好。

## 指令格式：每條 8 bytes

```
BPF 指令格式（fixed 8 bytes）

 7       0   11      8   15     12   31      16   63      32
┌─────────┬──────────┬──────────┬────────────┬────────────┐
│  opcode │ dst reg  │ src reg  │   offset   │  immediate │
│  (8b)   │  (4b)    │  (4b)    │   (16b)    │   (32b)    │
└─────────┴──────────┴──────────┴────────────┴────────────┘
```

`opcode` 的高 3 bit 決定指令類別（class）：

```
BPF_LD    = 0x00   # Load（到 r0 或其他）
BPF_LDX   = 0x01   # Load from memory（src → dst）
BPF_ST    = 0x02   # Store immediate（immediate → memory）
BPF_STX   = 0x03   # Store（src → memory）
BPF_ALU   = 0x04   # 32-bit ALU operation
BPF_JMP   = 0x05   # Jump（64-bit condition）
BPF_JMP32 = 0x06   # Jump（32-bit condition，kernel 5.1+）
BPF_ALU64 = 0x07   # 64-bit ALU operation
```

## 主要指令類別

### ALU 指令

ALU 指令格式：`dst op= src_or_imm`

```c
/* 用 bpf_asm 符號（實際上你用 C 寫，clang 生成這些） */

/* 64-bit ALU */
r1 += r2           /* r1 = r1 + r2 */
r3 += 42           /* r3 = r3 + 42 */
r4 &= 0xFF         /* r4 = r4 & 0xFF（取低 8 bits）*/
r5 >>= r6          /* r5 = r5 >> r6（邏輯右移）*/
r7 = -r7           /* r7 = -r7（negation）*/

/* 32-bit ALU（結果存在 dst 的低 32 bits，高 32 bits 清零）*/
w1 += w2           /* w1 是 r1 的低 32-bit view */
```

對應的 C code（clang 會生成上面的 BPF 指令）：

```c
uint64_t a = 10, b = 3;
a += b;           /* → BPF_ALU64_REG(BPF_ADD, r_a, r_b) */
a &= 0xFF;        /* → BPF_ALU64_IMM(BPF_AND, r_a, 0xFF) */
```

### Memory 指令

BPF 有三個記憶體空間可以存取：
1. **Stack**：`r10`（read-only frame pointer）- offset 定址，大小 512 bytes
2. **BPF maps**：透過 helper function 存取
3. **Packet data / ctx**：透過 bounds-checked load/store

```c
/* 在 stack 上分配空間（stack grows downward）*/
/* C 的 stack variable 會被 clang 編譯成這種形式 */

r10 - 8     /* stack 的最後 8 bytes（第一個 local variable）*/
r10 - 16    /* 往下 8 bytes（第二個 local variable）*/

/* Load/store 指令 */
*(u64 *)(r10 - 8) = r1     /* STX: 把 r1 存到 stack */
r2 = *(u64 *)(r10 - 8)     /* LDX: 從 stack 載入到 r2 */

/* 存取 context（例如 tracepoint 的 args） */
r3 = *(u32 *)(r1 + 16)     /* 從 ctx（r1）的 offset 16 讀 4 bytes */
```

> **為什麼 stack 只有 512 bytes？** 這是個設計選擇，不是硬體限制。512 bytes 是 verifier 決定靜態追蹤所有 stack 存取的可行上限。如果需要更多空間，用 `BPF_MAP_TYPE_PERCPU_ARRAY` 或其他 map type 當 heap。

### Jump 指令

```
/* 無條件跳轉 */
goto +N      /* pc += N + 1 */

/* 條件跳轉（比較 dst 和 src_or_imm） */
if r1 == r2 goto +N    /* jeq */
if r1 != r2 goto +N    /* jne */
if r1 > r2  goto +N    /* jgt（unsigned）*/
if r1 >= r2 goto +N    /* jge（unsigned）*/
if r1 s> r2 goto +N    /* jsgt（signed）*/
if r1 & r2  goto +N    /* jset（bitwise AND non-zero）*/
```

**重要**：在 kernel 5.3 之前，BPF verifier 不接受任何向後跳轉（back edge），這意味著沒有迴圈。5.3+ 允許有界的向後跳轉（bounded loop），verifier 證明迴圈會終止。

### Call 指令

```c
/* 呼叫 BPF helper function */
call bpf_map_lookup_elem    /* opcode: BPF_CALL，imm = helper 號碼 */

/* 呼叫另一個 BPF 函式（BPF-to-BPF call，kernel 4.16+） */
call another_bpf_func       /* opcode: BPF_CALL，src_reg = BPF_PSEUDO_CALL */
```

呼叫 helper 時，參數放在 `r1–r5`，回傳值在 `r0`。每次呼叫 helper 之後，verifier 會把 `r1–r5` 標記為 "uninitialised"（因為 helper 可能改動它們），只有 `r6–r9` 是 callee-saved 的。

## 看一段真實的 BPF bytecode

寫一個簡單的 BPF 程式，然後看它被編譯成什麼：

```c
/* simple_count.bpf.c */
#include <linux/bpf.h>
#include <bpf/bpf_helpers.h>

struct {
    __uint(type, BPF_MAP_TYPE_ARRAY);
    __uint(max_entries, 1);
    __type(key, u32);
    __type(value, u64);
} counter SEC(".maps");

SEC("tracepoint/syscalls/sys_enter_write")
int count_writes(void *ctx)
{
    u32 key = 0;
    u64 *val = bpf_map_lookup_elem(&counter, &key);
    if (val)
        __sync_fetch_and_add(val, 1);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
```

```bash
clang -g -O2 -target bpf -c simple_count.bpf.c -o simple_count.bpf.o
sudo bpftool prog load simple_count.bpf.o /sys/fs/bpf/count
sudo bpftool prog dump xlated pinned /sys/fs/bpf/count
```

輸出類似（帶行號和暫存器狀態）：

```
; u32 key = 0;
   0: (b7) r1 = 0                   # r1 = 0（立即數）
   1: (63) *(u32 *)(r10 -4) = r1    # 把 key 存到 stack[r10-4]

; u64 *val = bpf_map_lookup_elem(&counter, &key);
   2: (bf) r2 = r10                 # r2 = r10（frame pointer）
   3: (07) r2 += -4                 # r2 = &key（stack 上的 key 位址）
   4: (18) r1 = 0xffffa0...         # r1 = map fd（會被 relocate）
   6: (85) call bpf_map_lookup_elem#1  # 呼叫 helper，結果在 r0

; if (val)
   7: (15) if r0 == 0x0 goto pc+3  # if val == NULL, skip

; __sync_fetch_and_add(val, 1);
   8: (b7) r1 = 1
   9: (db) lock *(u64 *)(r0 +0) += r1  # atomic add

  10: (b7) r0 = 0                  # return 0
  11: (95) exit
```

## JIT 編譯器：BPF bytecode → native code

eBPF 的 JIT compiler（位於 `arch/x86/net/bpf_jit_comp.c`）把每一條 BPF 指令翻譯成一或多條 x86-64 指令。

```bash
# 查看 JIT 後的 native code（需要 kernel.bpf_jit_enable=1）
sudo sysctl net.core.bpf_jit_enable   # 確認 JIT 已啟用

sudo bpftool prog dump jited pinned /sys/fs/bpf/count
```

輸出類似（x86-64 指令）：

```
; r1 = 0
   0:  xor    %edi,%edi           # 清零 rdi（r1）
; *(u32 *)(r10 -4) = r1
   2:  mov    %edi,-0x4(%rbp)     # 存到 stack
; r2 = r10（frame pointer）
   5:  mov    %rbp,%rsi           # rsi = rbp
; r2 += -4
   8:  add    $0xfffffffc,%esi    # rsi += -4
; call bpf_map_lookup_elem
  11:  mov    $0xffffa0...,%rdi   # 準備 map 指標
  21:  call   0xffffffff8xxxxxx   # 呼叫 helper
; if r0 == 0 goto +3
  26:  test   %rax,%rax
  29:  je     0x...               # rax 是 r0（回傳值）
; lock *(u64 *)(r0 +0) += r1
  35:  mov    $0x1,%edi
  40:  lock add %rdi,(%rax)       # 原子加法
; return 0
  43:  xor    %eax,%eax
  45:  leaveq
  46:  retq
```

這幾乎是 hand-optimized 的 native code——因為 BPF 暫存器直接映射到 x86-64 暫存器，JIT 輸出的 code 品質很高。

## JIT 的效能數字

JIT 和 interpreter 的效能差距很大：

```
在 x86-64 上，一個簡單的 BPF program（~10 條指令）：
  Interpreter：~200 ns（每條指令約 20 ns 的 dispatch overhead）
  JIT：        ~5 ns（和 native C function 差不多）

差距：40 倍
```

> **確認 JIT 是否啟用**：`sysctl net.core.bpf_jit_enable`（1 = 啟用，2 = 啟用 + 輸出 JIT code 到 dmesg）。Linux kernel 5.2 開始，x86-64 上 JIT 預設強制啟用，interpreter 被移除（除非開了 `CONFIG_BPF_JIT_ALWAYS_ON=n`）。

## Wide Instructions（16 bytes）

載入 64-bit 立即數的指令需要 16 bytes（兩條 8-byte slot）：

```
/* 載入一個 64-bit 立即數到 r1 */
r1 = 0xDEADBEEF00001234LL

/* 在 bytecode 裡是兩條相鄰的指令 */
insn[0]: opcode=0x18, dst=r1, imm=0x00001234 (low 32)
insn[1]: opcode=0x00, imm=0xDEADBEEF       (high 32)
```

這也是為什麼 `bpftool prog dump xlated` 的指令序號有時候會跳 2（例如從 4 跳到 6）——因為 wide instruction 佔了兩個 slot。

## 踩雷集錦

1. **`r1–r5` 在 helper call 之後變成 uninitialised**：很多人以為呼叫 helper 之後 `r1`–`r5` 還有之前的值，但 verifier 會把它們標記成未初始化。如果你在 helper call 之後需要之前的值，先存到 `r6`–`r9`（callee-saved）

2. **BPF 沒有 `%` 運算**：BPF ALU 沒有除法運算（eBPF 有 `div` 但效能差且有除以 0 的問題）。取模要用 mask（`& (N-1)` 當 N 是 2 的冪次）或用 helper

3. **stack 的偏移必須是 4/8 的倍數**：根據存取大小，`*(u32 *)(r10 - 4)` 是合法的，但 `*(u32 *)(r10 - 3)` 會被 verifier reject（unaligned access）

4. **JIT 輸出的指令序號和 BPF 序號不是 1:1**：一條 BPF 指令可能對應 1–10 條 x86 指令；`bpftool prog dump jited` 的行號和 `xlated` 的行號不對應，要靠 comment（`; source line`）定位

5. **`bpf_jit_enable=2` 只是輸出到 dmesg，不是產生獨立的 object file**：如果你要 disassemble JIT output，用 `bpftool prog dump jited` 更方便

## 進階：BPF Verifier 怎麼用 ISA 特性

verifier 利用了 ISA 的幾個特性來做安全性分析：

- **所有暫存器有明確型別**：每個暫存器在任何程式點都有一個 "register type"（`NOT_INIT`、`SCALAR_VALUE`、`PTR_TO_MAP_VALUE`、`PTR_TO_STACK` 等），verifier 靜態追蹤這些型別
- **所有 store/load 的目標必須是已知型別的 pointer**：你不能對 `SCALAR_VALUE` 暫存器做 load/store，必須是 `PTR_TO_*` 型別
- **pointer arithmetic 受限**：你只能對 pointer 加/減 immediate，不能把兩個 pointer 相加；這讓 verifier 能靜態追蹤所有 pointer 的範圍

這些設計是在 ISA 層面就做出來的，不是 verifier 額外加的限制。

## 動手練習

1. 編譯 `simple_count.bpf.c`，執行 `llvm-objdump -d simple_count.bpf.o`，找出每一條 BPF 指令，對照本章的指令格式表，說出它們是哪種類別

2. 執行 `sudo bpftool prog dump xlated`，找到 `call` 指令，說出它呼叫的是哪個 helper（用 helper 號碼查 `include/uapi/linux/bpf.h` 的 `enum bpf_func_id`）

3. 把 `simple_count.bpf.c` 的 `bpf_map_lookup_elem` 改成查找一個 hash map，重新編譯，對比 dump xlated 的輸出有什麼不同

4. 執行 `sudo sysctl net.core.bpf_jit_enable=2`，然後載入一個 BPF program，執行 `sudo dmesg | tail -20` 看到 JIT 輸出的 x86-64 指令

## 本章重點整理

- eBPF 有 11 個 64-bit 暫存器（`r0`–`r10`）；`r10` 是 read-only frame pointer；`r1`–`r5` 是呼叫規範的參數 / 回傳
- 每條 BPF 指令是 8 bytes（wide instruction 是 16 bytes）；格式：opcode + dst + src + offset + immediate
- JIT compiler 把 BPF 暫存器直接映射到 x86-64 暫存器，輸出幾乎 native-quality 的 code；效能比 interpreter 快約 40 倍
- Stack 大小 512 bytes 是設計選擇，不是硬體限制；需要更多空間用 BPF maps

## 自我檢核

- [ ] 能說出 `r0`–`r10` 各自的用途，以及哪些是 callee-saved
- [ ] 能解釋為什麼 `r1`–`r5` 在 helper call 之後變成 uninitialised
- [ ] 能讀懂 `bpftool prog dump xlated` 的輸出，指出 load/store/ALU/call 指令
- [ ] 知道 JIT 和 interpreter 的效能差距數量級，以及如何確認 JIT 是否啟用

## 延伸閱讀

### 官方文件

- **[Linux kernel: BPF Design Q&A](https://www.kernel.org/doc/html/latest/bpf/bpf_design_QA.html)**
  - **讀哪裡**：整份；特別是關於 stack size、helper 呼叫規範的解釋
  - **學什麼**：kernel 開發者對 ISA 設計決策的官方解釋；為什麼這樣設計，為什麼不那樣設計
  - **前提**：讀完本章之後

- **[BPF ISA specification](https://www.kernel.org/doc/html/latest/bpf/instruction-set.html)**
  - **讀哪裡**：完整指令集表格；特別是 opcode encoding 那一節
  - **學什麼**：所有 BPF 指令的完整規格；作為參考文件查閱

### 部落格

- **[A look at BPF's instruction set](https://docs.ebpf.io/linux/instruction-set/)** — eBPF.io docs
  - **這篇說什麼**：用圖表說明 BPF 指令格式和各類別；比本章更詳細的指令集參考
  - **讀哪裡**：全篇；包括 arithmetic、memory、jump 那幾節
  - **為什麼值得讀**：圖表清晰，作為參考手冊比 kernel docs 更易讀

- **[BPF JIT compiler internals](https://arthurchiao.art/blog/bpf-advanced-notes-1-zh/)** — ArthurChiao (Cizixs)
  - **這篇說什麼**：深入 x86-64 JIT compiler 的實作，展示 BPF insn 到 x86 insn 的映射細節
  - **讀哪裡**：前三節（register mapping、instruction translation、prologue/epilogue）
  - **為什麼值得讀**：作者是 Cilium 工程師，文章品質高；讀完你能真正理解 JIT 生成的 code

→ [Ch 5 eBPF Verifier：安全性證明的工作原理](./05-ebpf-verifier.md)
