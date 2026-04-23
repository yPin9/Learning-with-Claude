# Final Project — Mini RV32I Emulator

> 目標：把整門課變成 code。寫一個 C/C++ emulator，能載入 ELF、執行 RV32I 指令、支援基本 ecall（printf、exit），能跑過 20+ 條手寫測試程式。完成後你會有一個**面試時可以拿出來給對方看的具體作品** — 遠勝於口頭「我學過 RISC-V」。

## 為什麼這是好 final project

1. **涵蓋整門課**：指令 decode、暫存器語意、memory、trap、ELF 載入 — 每一章的知識都派上用場。
2. **是 toolchain 工程師的天然熱身**：你之後要寫的 disassembler / assembler / JIT，骨架都跟這個類似。
3. **可以放 GitHub**：面試 SiFive 時拿出 repo 連結比任何履歷描述有說服力。
4. **可以 incremental**：先過 100 行 code，跑 hello world 就有成就感。再慢慢加 feature。
5. **時間彈性**：最小可用版本 3 天、完整版本 2 週。

## 目標版本定義（最低門檻）

**MVP (Minimum Viable Product)**:

- 支援 RV32I base ISA 所有 47 條指令
- 能載入 ELF 32-bit 檔
- 支援 `ecall` 的 4 個 syscall：write、read、exit、brk
- 能跑 `int main() { printf("hello\n"); return 0; }`（用 newlib 的 riscv32-unknown-elf-gcc 編）
- 有 trace mode（印每條指令 + register state）
- 有 test suite 10+ 題，能自動化驗證

這份 MVP 大約 1000–1500 行 C/C++。

**Stretch**:

- 支援 M 擴充
- 支援 C 擴充（16-bit 指令 decode）
- 支援基本的 CSR（cycle、instret）
- misaligned access 測試
- Spike 風格的 `-l` trace output 格式
- 性能：至少 10 MIPS 以上

## 建議架構

```
emulator/
├── src/
│   ├── main.c            // ELF 載入、主迴圈
│   ├── cpu.c / cpu.h     // CPU state, fetch, execute
│   ├── decode.c / .h     // 指令 decode 與 dispatch
│   ├── memory.c / .h     // flat virtual memory
│   ├── elf_loader.c / .h // ELF 32-bit parser
│   ├── syscall.c / .h    // ecall handling
│   └── trace.c / .h      // 可選 trace
├── test/
│   ├── test_add.s        // 測試程式（.S → .elf）
│   ├── test_loop.c
│   ├── test_fib.c
│   └── run_tests.sh
├── Makefile
└── README.md
```

## 階段性 milestone

### Milestone 1 — Hello integer (1 day)

目標：能執行一條 `addi` 並印結果。

- 建 `cpu_t` struct：32 顆 reg + pc
- 手寫一段 code：`addi x5, x0, 42` 的 encoding 直接塞進 memory
- 寫最簡 fetch + decode + execute
- 印 `x5 = 42`

**pass criteria**：你的 emulator 讀到正確 register value。

### Milestone 2 — 七種格式 decode

目標：能 decode 所有 R/I/S/B/U/J 型指令 field 並 pretty-print。

- 實作 decode() 回傳一個 decoded_inst struct
- 加 trace mode 印：`pc, hex, opcode_name, rd, rs1, rs2, imm`
- 塞 10 條手寫 encoding 跑看看

**pass criteria**：trace 跟 `objdump -d` 的輸出完全一致。

### Milestone 3 — RV32I 所有指令 execute

目標：47 條指令的 semantic 全部實作。

- 寫 dispatch table（switch 或 function pointer）
- 每條指令的 semantic 對照 spec 實作
- 特別注意：sign extension（load、addi）、unsigned/signed 比較（bltu / bgeu）、shift 的 shamt 範圍

**pass criteria**：手寫 20 題 assembly 測試，每題結果都對。

### Milestone 4 — Memory 架構

目標：實作一個「假 32-bit 地址空間」。

- 分 code / data / stack / heap 四塊
- 支援對齊 / 非對齊 access（非對齊打印警告或支援）
- Load / Store 各種寬度（lb / lh / lw / lbu / lhu / sb / sh / sw）

**pass criteria**：測試 buffer 讀寫、pointer arithmetic 都正確。

### Milestone 5 — ELF 載入

目標：能讀一個真實的 RV32 ELF 檔，把 code / data 放對位置。

- parse ELF header（小心 32 vs 64-bit ELF 結構不同）
- 對每個 PT_LOAD segment，複製到 memory 對應位置
- 設 `pc = ELF entry point`
- 設 `sp` 指向 stack 頂

**pass criteria**：`riscv32-unknown-elf-gcc -nostdlib -o test.elf test.S` 編的 binary 你能載入並跑起來。

### Milestone 6 — ecall / syscall

目標：接 Linux syscall / newlib syscall 介面，讓 C 程式能 printf。

最少支援：

- `write (fd, buf, len)`: syscall 64
- `read  (fd, buf, len)`: syscall 63
- `exit  (code)`:          syscall 93
- `brk   (addr)`:          syscall 214 (很重要，newlib 的 malloc 靠這個)

流程：

```
cpu 執行 ecall
  ↓
exception = "ecall from U-mode"
  ↓
你的 emulator 判斷：跳到 syscall handler
  ↓
讀 a7 = syscall number, a0..a6 = args
  ↓
call host 的 write / read / exit (或模擬)
  ↓
結果寫回 a0
  ↓
pc += 4, 繼續
```

注意：**buf 是 guest 的虛擬地址**。你要 memcpy 到 host 之前先把 guest address 翻譯回 host memory pointer。

**pass criteria**：用 gcc 編 `printf("hello\n")` 的 C code，你的 emulator 跑出 hello。

### Milestone 7 — 測試框架

目標：有自動化 regression test。

- 寫一個 `run_tests.sh`：遍歷 test/*.elf、跑你的 emulator、比對 expected output
- 測試涵蓋：
  - 基本 arithmetic (add, sub, mul — 需 M 擴充或軟體 mul)
  - Loop 與 branch
  - Function call（含 nested）
  - Fibonacci
  - 字串操作
  - 遞迴

**pass criteria**：至少 20 個 test case 全通過。

## 實作建議

### Decode 的寫法

**推薦**：用 switch 直接分支，不要太早用 function pointer。可讀性高、效能也不差。

```c
void execute(cpu_t *cpu, uint32_t inst) {
    uint32_t opcode = inst & 0x7F;
    uint32_t rd     = (inst >> 7) & 0x1F;
    uint32_t funct3 = (inst >> 12) & 0x7;
    uint32_t rs1    = (inst >> 15) & 0x1F;
    uint32_t rs2    = (inst >> 20) & 0x1F;
    uint32_t funct7 = (inst >> 25) & 0x7F;

    switch (opcode) {
    case 0x33:   // R-type ALU
        switch (funct3) {
        case 0x0: // ADD/SUB
            if (funct7 == 0x00)
                cpu->reg[rd] = cpu->reg[rs1] + cpu->reg[rs2];   // ADD
            else if (funct7 == 0x20)
                cpu->reg[rd] = cpu->reg[rs1] - cpu->reg[rs2];   // SUB
            break;
        // ... 其他 funct3
        }
        break;
    case 0x13:   // I-type ALU
        // ...
    // ... 其他 opcode
    }

    cpu->reg[0] = 0;                  // x0 永遠 0
    cpu->pc += 4;
}
```

### Immediate 解出來的 trick

```c
// I-type: imm[11:0] at bit [31:20], sign-extend
int32_t imm_i(uint32_t inst) {
    return (int32_t)inst >> 20;       // 先 cast int32 再算術右移 = sign extend
}

// S-type: imm[11:5] at [31:25], imm[4:0] at [11:7]
int32_t imm_s(uint32_t inst) {
    int32_t hi = (int32_t)inst >> 25;               // sign extend
    int32_t lo = (inst >> 7) & 0x1F;
    return (hi << 5) | lo;
}

// B-type: 你試試看寫（提示：bit 12 / 10:5 / 4:1 / 11，最低 bit 是 0）
// J-type: 類似但寬度不同
```

**寫一個 unit test 驗證每個 imm 解析**。這段超容易錯。

### Memory 的 abstraction

```c
typedef struct {
    uint8_t *base;             // host 側的 malloc buffer
    uint32_t size;
    uint32_t vaddr_start;      // guest 看到的起始地址
} mem_region_t;

uint32_t mem_load_w(cpu_t *cpu, uint32_t vaddr) {
    // 找出 vaddr 在哪個 region
    // 轉成 host pointer
    // memcpy 或 *(uint32_t*)ptr
}
```

### ELF loader

Linux 上有 `<elf.h>` 可以直接用：

```c
#include <elf.h>

int load_elf(const char *path, cpu_t *cpu, memory_t *mem) {
    // ... open, mmap or read
    Elf32_Ehdr *eh = ...;
    if (eh->e_machine != EM_RISCV) return -1;

    // 遍歷 program header
    Elf32_Phdr *ph = (Elf32_Phdr *)(base + eh->e_phoff);
    for (int i = 0; i < eh->e_phnum; i++) {
        if (ph[i].p_type != PT_LOAD) continue;
        // 把 ph[i].p_vaddr..+p_memsz 的 guest memory
        // 從 file 的 ph[i].p_offset..+p_filesz 複製
    }

    cpu->pc = eh->e_entry;
    return 0;
}
```

### ecall 的實作

```c
void handle_ecall(cpu_t *cpu, memory_t *mem) {
    uint32_t num = cpu->reg[17];  // a7
    switch (num) {
    case 64: { // write
        int fd = cpu->reg[10];
        uint32_t buf = cpu->reg[11];
        uint32_t len = cpu->reg[12];
        uint8_t *host_buf = mem_translate(mem, buf, len);
        ssize_t ret = write(fd, host_buf, len);
        cpu->reg[10] = ret;
        break;
    }
    case 93: // exit
        exit(cpu->reg[10]);
    // ...
    }
}
```

## Test 程式範例

```c
// test/fib.c
#include <stdio.h>

int fib(int n) {
    if (n < 2) return n;
    return fib(n-1) + fib(n-2);
}

int main(void) {
    for (int i = 0; i < 10; i++)
        printf("%d ", fib(i));
    printf("\n");
    return 0;
}
```

編成 RV32：

```bash
riscv32-unknown-elf-gcc -march=rv32im -mabi=ilp32 -o fib.elf fib.c
```

跑：

```bash
./my_emulator fib.elf
# 預期: 0 1 1 2 3 5 8 13 21 34
```

如果你的 emulator 能印出這個序列 → 你已經做完 MVP。

## 參考實作

有幾個很短的 RISC-V emulator 可以參考架構（別抄 code）：

- **rvemu** (作者 Asami Doi)：<https://github.com/d0iasm/rvemu> — 非常乾淨的 Rust 實作，約 2000 行
- **tinyrv32** (simple C)：<https://github.com/mtmk/tinyrv32>（若 GitHub 存在）
- **Spike** 本身是 C++、10000+ 行，可以讀 `insns/*.h` 看一條指令的 golden semantic

讀完看得懂，但你自己寫一遍才會學會。**絕對不要 copy-paste**，否則面試時被問細節會露餡。

## 進階延伸（選做）

做完 MVP 後可以加：

1. **M 擴充**：mul / div / rem。簡單。
2. **C 擴充**：16-bit 指令 decode。要加 dispatch 的分支（檢查 `[1:0]` 判斷 16 / 32 bit）。
3. **F / D 擴充**：浮點。工作量大。
4. **A 擴充**：atomic — 因為 single-threaded 所以 LR/SC 直接當普通 load/store。
5. **Mini-OS feature**：支援 malloc（需要 brk）、fork + wait（需要 clone）
6. **JIT**：把 decode 結果 cache、hot path 用 function pointer 加速
7. **Fuzzer**：用 qemu-riscv32 做 differential testing — 同一 binary 兩邊跑，reg 狀態應該完全一致

## README 要寫什麼

你的 repo README 應該有：

```markdown
# mini-rv32i

A minimal RV32I emulator in C, supporting newlib-based ELF programs.

## Features
- Full RV32I base ISA (47 instructions)
- ELF 32-bit loader
- newlib syscalls (write, read, exit, brk)
- Optional M extension
- Trace mode
- 25 test cases, all passing

## Usage
$ make
$ ./rv32 test/fib.elf

## Architecture
[附一張 ASCII diagram]

## What I learned
- 寫 emulator 深度理解 ISA
- ELF 格式的陷阱（32 vs 64）
- sign extension 的細節
- ...
```

SiFive 招募者打開這個看 30 秒就能判斷你的水準。

## 評估標準（自我或面試官角度）

**60 分**：能跑 hello world
**75 分**：能跑 fib、loop、字串操作
**85 分**：有自動化 test、trace 格式好看
**95 分**：支援 M / C 擴充、性能合理（>5 MIPS）
**100 分**：Differential testing 跟 qemu 一致

**60 分就能寫進履歷、贏過不做 side project 的候選人**。超過 85 分進入「面試官主動問細節」的層級。

## 時間建議

- **Week 1**: Milestone 1-3（decode + execute all RV32I）
- **Week 2**: Milestone 4-6（memory、ELF、syscall）
- **Week 3**: Milestone 7（testing）+ 文件

3 週能做到 MVP。想拚到 100 分再加 1-2 週。

## 最重要的一件事

**寫 commit message**。每個 milestone 一個有意義的 commit。SiFive 面試官會看 git log 判斷你的工程習慣。

```
feat: implement R-type ALU instructions
feat: add ELF loader for 32-bit RISC-V
test: add fib + loop test cases
fix: correct B-type immediate sign extension
```

這比 100 行漂亮 code 更能說服人你是一個**嚴肅的工程師**。

## 完成後

把 repo 推上 GitHub，在 README 附：

- Build / run 指令
- Test output 截圖
- 一段 "lessons learned"

更新履歷，加：「寫了一個 1500 行的 RV32I emulator in C，支援 newlib ELF，所有 47 條指令 + 6 個 syscall。」

寄履歷時帶著這個 repo 連結 — **你現在就是 SiFive 面試的強候選人**。

## 結束前的話

這是 `learn_riscv` 課程的終點。你從「hello from spike」走到「自己寫一個 spike」。接下來：

- **`learn_elf_linking`**：下一門課。深入 ELF 細節、relocation、linker script、relaxation。你寫過 emulator 再學這個會發現「喔原來 PT_LOAD 底下還有這麼多事」。
- **`learn_compiler_backend`**：最後一門。有了 RISC-V 跟 ELF 的基礎，看 LLVM / GCC 的 RISC-V 後端會暢通無阻。
- **LLVM contribution**：挑一個 open bug，送 patch。用你的 emulator 驗證。這是面試作品集的 cherry on top。

一步一步走。你會到 SiFive 的。

**祝順利。**
