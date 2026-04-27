# 練習 D — 用 Triton 寫 taint 追蹤小工具

> 目標：實作一個 binary-level DTA 工具。source = stdin read, sink = 特定 API。輸出 taint flow 圖跟 alert。

## 目標範圍

做一個工具 `mytaint`，能：

1. Load 一個 binary（簡單的 ELF）
2. 模擬執行（Triton 不自帶 DBI，可以配 Unicorn 或自寫 emulation loop）
3. 標 stdin 讀進的 buffer 為 tainted
4. 追蹤 taint propagation 到任一 `system()` / `execve()` call
5. 若 tainted 到達 sink，印出 alert + 從入口到 sink 的 taint trace

架構：

```
   binary
      │
      │ CLE (借 angr 的 loader)
      ▼
   Memory state + CPU state (由 Triton 管)
      │
      │ dispatch loop
      ▼
   Triton.processing(inst)
      │
      │ taint updates by instruction semantic
      ▼
   每條 instruction 檢查：
      - 是否為 syscall/function call
      - 若為 sink 且 arg tainted → alert
```

## Step 1 - 最小 target

寫一個有簡單 command injection 的 target：

```c
// tgt.c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(int argc, char** argv) {
    char input[100];
    if (!fgets(input, sizeof(input), stdin)) return 1;
    input[strcspn(input, "\n")] = 0;
    
    char cmd[200];
    snprintf(cmd, sizeof(cmd), "echo Hello, %s", input);
    system(cmd);
    return 0;
}
```

```bash
gcc -no-pie -O0 tgt.c -o tgt
```

在你的 taint tool 裡跑，stdin 餵 `"world"`。期待看到 alert：**taint 從 stdin 流到了 system**。

## Step 2 - 設計 emulation loop

Triton 不自己跑。你需要：

1. 從 binary load code / data section 到 Triton 的 memory
2. 設 stack、rsp、entry point
3. loop：fetch instruction → Triton.processing → 看 RIP 下一個值

```python
import angr
from triton import TritonContext, ARCH, Instruction, MODE, MemoryAccess

def load_binary(path):
    """用 CLE load binary，回傳 memory 內容 + entry point"""
    proj = angr.Project(path, auto_load_libs=False)
    mem_segments = []
    for seg in proj.loader.main_object.segments:
        data = proj.loader.memory.load(seg.vaddr, seg.memsize)
        mem_segments.append((seg.vaddr, data))
    entry = proj.entry
    return mem_segments, entry

def setup_triton(binary_path):
    ctx = TritonContext(ARCH.X86_64)
    ctx.setMode(MODE.ALIGNED_MEMORY, True)
    
    segments, entry = load_binary(binary_path)
    for addr, data in segments:
        ctx.setConcreteMemoryAreaValue(addr, data)
    
    # setup stack
    stack_base = 0x7ffffffde000
    ctx.setConcreteRegisterValue(ctx.registers.rsp, stack_base)
    ctx.setConcreteRegisterValue(ctx.registers.rbp, stack_base)
    ctx.setConcreteRegisterValue(ctx.registers.rip, entry)
    
    return ctx, entry
```

## Step 3 - 識別 syscall / function call

x86-64 linux 的 syscall 是 `syscall` instruction（opcode `0f 05`）。function call 是 `call`（`e8 ...` 或 `ff d?`）。

每次 processing 完，看新的 rip、判斷 context：

```python
def is_syscall(inst):
    return inst.getType() == OPCODE.X86.SYSCALL

def is_call_to(inst, target_addr):
    if inst.getType() == OPCODE.X86.CALL:
        op = inst.getOperands()[0]
        if op.getType() == OPERAND.IMM:
            return op.getValue() == target_addr
    return False
```

## Step 4 - 把 stdin 標 tainted

在 target 呼叫 `fgets(input, 100, stdin)` 時，input buffer 被寫。你要在 fgets 返回時把 buffer 標 tainted。

簡單做法：hook fgets（在 emulator 裡 intercept）：

```python
def hook_fgets(ctx, input_bytes):
    """當 call 到 fgets plt entry 時執行"""
    rdi = ctx.getConcreteRegisterValue(ctx.registers.rdi)  # buf
    rsi = ctx.getConcreteRegisterValue(ctx.registers.rsi)  # size
    # 寫 input_bytes 到 rdi
    for i, b in enumerate(input_bytes):
        ctx.setConcreteMemoryValue(rdi + i, b)
        ctx.taintMemory(MemoryAccess(rdi + i, 1))
    # null-terminate
    ctx.setConcreteMemoryValue(rdi + len(input_bytes), 0)
    # return
    ctx.setConcreteRegisterValue(ctx.registers.rax, rdi)  # fgets returns buf
    # 模擬 ret
    rsp = ctx.getConcreteRegisterValue(ctx.registers.rsp)
    ret_addr = int.from_bytes(
        bytes(ctx.getConcreteMemoryAreaValue(rsp, 8)), 'little')
    ctx.setConcreteRegisterValue(ctx.registers.rip, ret_addr)
    ctx.setConcreteRegisterValue(ctx.registers.rsp, rsp + 8)
```

找 fgets 的 PLT entry：可以用 angr 的 symbol table：

```python
fgets_plt = proj.loader.find_symbol('fgets').rebased_addr
```

## Step 5 - 檢查 system() 的 arg taint

system 的 arg 在 rdi（x86-64 System V calling convention）。當 rip 到 `system` 的 plt entry，檢查 rdi 指向的字串 byte 是否 tainted：

```python
def check_system_call(ctx):
    rdi = ctx.getConcreteRegisterValue(ctx.registers.rdi)
    # 讀 string byte by byte until null
    tainted_bytes = []
    for i in range(500):  # max length
        v = ctx.getConcreteMemoryValue(rdi + i)
        if v == 0: break
        if ctx.isMemoryTainted(MemoryAccess(rdi + i, 1)):
            tainted_bytes.append(i)
    if tainted_bytes:
        # print alert
        print(f"[ALERT] command injection! tainted offsets: {tainted_bytes}")
        print(f"        command: {bytes(ctx.getConcreteMemoryAreaValue(rdi, 500))}")
```

## Step 6 - 完整 main loop

```python
def run(ctx, entry, input_bytes, fgets_plt, system_plt, exit_addr):
    pc = entry
    max_steps = 10000
    
    for step in range(max_steps):
        if pc == exit_addr:
            print(f"[INFO] reached exit, halt")
            break
        
        # hook PLT entries
        if pc == fgets_plt:
            hook_fgets(ctx, input_bytes)
            pc = ctx.getConcreteRegisterValue(ctx.registers.rip)
            continue
        if pc == system_plt:
            check_system_call(ctx)
            # pretend system succeeded, ret
            rsp = ctx.getConcreteRegisterValue(ctx.registers.rsp)
            ret_addr = int.from_bytes(
                bytes(ctx.getConcreteMemoryAreaValue(rsp, 8)), 'little')
            ctx.setConcreteRegisterValue(ctx.registers.rax, 0)
            ctx.setConcreteRegisterValue(ctx.registers.rip, ret_addr)
            ctx.setConcreteRegisterValue(ctx.registers.rsp, rsp + 8)
            pc = ret_addr
            continue
        
        # 一般 instruction
        opcodes = bytes(ctx.getConcreteMemoryAreaValue(pc, 16))
        inst = Instruction(opcodes)
        inst.setAddress(pc)
        
        if not ctx.processing(inst):
            print(f"[ERROR] cannot process at {hex(pc)}")
            break
        
        pc = ctx.getConcreteRegisterValue(ctx.registers.rip)
```

## Step 7 - 把它們串起來

```python
def main():
    ctx, entry = setup_triton('./tgt')
    
    proj = angr.Project('./tgt', auto_load_libs=False)
    fgets_plt = proj.loader.find_symbol('fgets').rebased_addr
    system_plt = proj.loader.find_symbol('system').rebased_addr
    exit_addr = proj.loader.find_symbol('exit').rebased_addr
    
    input_bytes = b"world"
    run(ctx, entry, input_bytes, fgets_plt, system_plt, exit_addr)

main()
```

跑：

```
[ALERT] command injection! tainted offsets: [15, 16, 17, 18, 19]
        command: b'echo Hello, world'
```

**你剛做了一個 binary-level DTA 工具**。

## 延伸 1 - taint 更多 sink

加入更多 sink：
- `execve`、`execl`、`popen`
- `fopen`（for path traversal）
- 把 sink 列成 table，統一 check

## 延伸 2 - 報告 taint source line

追 taint trace：當每個 byte 被 tainted 時，記住它的 source（stdin offset）。當 alert 時印出 command 的哪個 byte 來自 stdin 的哪個 byte。

這需要用 Triton 的 **label-based taint**（多 label），不只 boolean。

## 延伸 3 - 支援 snprintf / strcat 的 semantic

目前你的 tool 只 taint 「從 fgets 直接寫入」的 buffer。但 command 是經過 snprintf 組合的。

兩個選擇：
- **讓 Triton 跑進 snprintf 的 libc code**：需要把 libc 載進 memory、正確 model syscall writes。工程大
- **Hook snprintf 做 manual taint propagation**：簡化版 — 掃 src format string 裡 %s 對應的 arg，把那段 taint 傳給 dst

第二種工程小得多，精度夠用。試試。

## 延伸 4 - 視覺化

用 graphviz 產生 taint flow graph：

```
source (stdin byte 0-4)
    │
    ▼
fgets → buf[0..4] tainted
    │
    ▼
snprintf → cmd[15..19] tainted
    │
    ▼
system (ALERT!)
```

這種報告對 security team 很有說服力。

## 延伸 5 - 效能比較

用原生 / libdft / 你的 tool 各跑同一 target，比：
- 執行時間（slowdown）
- 準確度（是否都報同樣 alert）

你會看到：**你的 toy tool 慢幾十倍（每 instruction Python call）**、但精度一樣。這就是 Python prototype 跟 production DBI 的差距。

## 踩雷

### 雷 1：Triton.processing 失敗

有些 instruction Triton semantic 沒實作（SSE、AVX 的某些）。碰到就 skip 或 concretize。

### 雷 2：rsp 不對

記得 function call 把 return address push 上 stack。你如果自己 simulate `call`、要主動 push。

### 雷 3：libc 不 load 的後果

`fgets` 是 PLT entry，實際 code 在 libc。你不 load libc 就會跑進 PLT 然後 jmp 到 GOT 填的 address — 那是 runtime linker 填的。你需要：
- 手動 hook PLT entry（上面 Step 5 的做法）
- 或把 libc load 進來 + 處理 dynamic linker

Hook PLT 最簡單、也最符合 DTA 工具的設計。

### 雷 4：taint 爆量

對複雜 target，你的 tool 可能把整個 memory 都 taint 了。看報告時過濾 false positive 的能力要練。

## 提交

```
mytaint/
├── README.md          # 你的設計 + 使用說明
├── mytaint.py         # 主要工具
├── hooks.py           # PLT hook 集
├── targets/
│   ├── tgt_inj.c     # 有 bug 的 target
│   └── tgt_safe.c    # 被 patch 後的版本
└── reports/
    ├── tgt_inj.md    # 跑 tgt_inj 的 output
    └── tgt_safe.md   # 跑 safe 版的 output (no alert)
```

## 自我檢核

- [ ] mytaint 能跑起來、對 tgt_inj 報 command injection
- [ ] 對 patch 後的 safe 版本不報（no false positive）
- [ ] 支援至少 2 個不同 sink（system、execve）
- [ ] README 講得清楚你的 tool 限制
- [ ] 知道 Triton.processing 失敗時的應對
- [ ] 知道為什麼你的 tool 比 libdft 慢

→ [Ch 25 — Hybrid fuzzing：Driller / QSYM / SymCC 的取捨](./25-hybrid-fuzzing.md)
