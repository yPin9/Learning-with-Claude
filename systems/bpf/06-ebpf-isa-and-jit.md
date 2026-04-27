# Ch 6 — eBPF instruction set、register、JIT 與 sandboxing

> 目標：拆解 eBPF VM 的硬體模型、calling convention、指令格式、JIT 流程，看清楚你寫的 C code 最終變成 kernel 裡執行的 native CPU 指令的全程。

## cBPF → eBPF 的躍升

Ch 5 的 cBPF 是個 32-bit、2 register、約 30 條指令的 toy VM。eBPF 在 2014 把它**整個重造**，目標是「能寫真程式」 — 但仍要保留「verifier 沙盒、不會 panic kernel」的核心承諾。

對照：

| | cBPF | eBPF |
|---|---|---|
| Register 寬度 | 32-bit | **64-bit** |
| Register 數量 | 2（A, X） | **11**（R0–R10） |
| Stack | 16 word scratch | **512 bytes** |
| Calling convention | 無（沒函式呼叫） | **R1–R5 傳參、R0 回傳** |
| 呼叫 helper | 不能 | **能**（kernel 提供白名單） |
| Maps（跨呼叫狀態） | 不能 | **能** |
| Backward jump | 禁止 | **有限度允許**（bounded loop） |
| 指令寬度 | 64 bit | 64 bit（同） |
| 指令數量 | ~30 | ~100 |

注意：eBPF 故意保留「64-bit 指令寬度」與「整數操作為主」的精神 — 它仍然是個 RISC-like ISA，只是「能寫的東西」大幅擴張。

## eBPF VM 硬體模型

```
┌─────────────────────────────────────────────────────┐
│                    eBPF VM                          │
│                                                     │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐       │
│  │  R0  │ │  R1  │ │  R2  │ │  R3  │ │  R4  │       │
│  │回傳值 │ │ arg1 │ │ arg2 │ │ arg3 │ │ arg4 │       │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘       │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐       │
│  │  R5  │ │  R6  │ │  R7  │ │  R8  │ │  R9  │       │
│  │ arg5 │ │callee│ │callee│ │callee│ │callee│       │
│  │      │ │saved │ │saved │ │saved │ │saved │       │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘       │
│  ┌──────┐                                           │
│  │ R10  │ ← frame pointer (read-only)               │
│  └──────┘                                           │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │   Stack (512 bytes, R10 指向頂端)             │   │
│  └──────────────────────────────────────────────┘   │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │   Maps（外部存取，透過 helper）                │   │
│  └──────────────────────────────────────────────┘   │
│                                                     │
│  Helper functions（可呼叫的 kernel 白名單函式）      │
└─────────────────────────────────────────────────────┘
```

對應到熟悉的東西：**這就是一個簡化版的 x86_64 + ABI 約定**。

## 11 個 Register 的 calling convention

eBPF 的 calling convention **故意對齊 x86_64**，這樣 JIT 才能 1:1 對應，幾乎不用做 register allocation：

| eBPF reg | 角色 | x86_64 對應 |
|---|---|---|
| **R0** | 函式回傳值；helper / program 結果 | rax |
| **R1–R5** | 函式參數（最多 5 個） | rdi, rsi, rdx, rcx, r8 |
| **R6–R9** | callee-saved（呼叫 helper 後值會保留） | rbx, r13, r14, r15 |
| **R10** | frame pointer，**唯讀** | rbp |

**Callee-saved 的意思**：你呼叫 helper（如 `bpf_map_lookup_elem`）時，R0–R5 的值會被覆蓋，但 R6–R9 不會。**因此你想保留資料跨 helper 呼叫，要存在 R6–R9 或 stack**。

R10 是 frame pointer，**只能讀，不能寫**。你想「分配 local 變數」就是從 R10 往下做負偏移：

```
R10 ──→ ┌──────────┐  ← stack 頂
        │   ...     │
        │ local var │  R10 - 8
        │ local var │  R10 - 16
        │   ...     │
        └──────────┘  R10 - 512  ← stack 底
```

C 編譯出來的 stack 變數就是用這個方式定址。

## 指令格式

eBPF 指令固定 64 bit，欄位：

```
┌────────┬────────┬────────┬────────────┬──────────────────────────┐
│ opcode │  dst   │  src   │   offset   │       imm (32-bit)       │
│ 8 bit  │ 4 bit  │ 4 bit  │   16 bit   │                          │
└────────┴────────┴────────┴────────────┴──────────────────────────┘
```

- `dst` / `src`：register index（0–10）
- `offset`：jump 偏移 / memory 偏移
- `imm`：立即值或 helper id

**Wide instructions**：少數指令（如 64-bit immediate load）需要 16 byte，靠連續兩條 8-byte 指令拼成。

## 八大指令類別（opcode 低 3 bit 決定）

| Class | 例子 | 做什麼 |
|---|---|---|
| `BPF_LD` | `BPF_LD_IMM64` | 載入 64-bit 立即值 |
| `BPF_LDX` | `BPF_LDX_MEM` | 從記憶體讀（`R = *(R+off)`） |
| `BPF_ST` | `BPF_ST_MEM` | 把立即值寫記憶體 |
| `BPF_STX` | `BPF_STX_MEM` | 把 register 寫記憶體 |
| `BPF_ALU` | `BPF_ADD`, `BPF_MOV` | 32-bit ALU |
| `BPF_ALU64` | `BPF_ADD`, `BPF_MOV` | 64-bit ALU |
| `BPF_JMP` | `BPF_JEQ`, `BPF_CALL`, `BPF_EXIT` | jump、helper 呼叫、結束 |
| `BPF_JMP32` | `BPF_JEQ_32` | 32-bit jump（5.1+） |

注意 ALU 與 ALU64 是分開的 class — eBPF 同時支援 32-bit 與 64-bit 整數操作。

## Helper function — eBPF 通往 kernel 能力的橋

eBPF 不能任意 call kernel function，但 kernel 提供一個**白名單**（helper functions），透過 `BPF_CALL` 指令呼叫：

```c
// kernel-side BPF C code
struct task_struct *task = (struct task_struct *)bpf_get_current_task();
u32 pid = bpf_get_current_pid_tgid() >> 32;
bpf_map_update_elem(&my_map, &pid, &value, BPF_ANY);
```

每個 helper 有個 ID（`__BPF_FUNC_MAPPER` macro 列舉），verifier 確認你 call 的是合法 helper、且參數型別對。

helper 數量目前 200+，分散在：
- map 操作（`bpf_map_lookup_elem`, `bpf_map_update_elem`...）
- 取 task / process info（`bpf_get_current_pid_tgid`, `bpf_get_current_comm`...）
- 取時間（`bpf_ktime_get_ns`）
- 印 log（`bpf_printk` — 有了這個 debug 容易 100 倍）
- 改 packet（`bpf_skb_store_bytes`...）
- 安全的 memory 讀（`bpf_probe_read_kernel`, `bpf_probe_read_user`）

完整 list：

```bash
sudo bpftool feature probe | grep "bpf_" | head -30
# 或：
man 7 bpf-helpers
```

## Stack：512 bytes 的硬上限

eBPF 的 stack 只有 **512 bytes**。沒有 dynamic allocation，所有 local 變數佔的空間在 compile time 就決定。

這意味著：

- 大 struct（例如想塞個 path string）放不下，要拆成 chunks 處理或塞 map
- 遞迴在 eBPF 裡幾乎不可能（每層都吃 stack，verifier 會拒絕）
- 想「把整個 packet 拷貝到 local buffer 慢慢處理」 — 不行，packet 常常 > 512 bytes

這是一個**故意的限制** — 限制 stack 也限制了 verifier 要分析的狀態空間，讓 verification 在合理時間內完成。

## JIT：從 bytecode 到 native code

eBPF program 載入後，verifier 通過，下一步是 **JIT 編譯**。kernel 把 eBPF bytecode **直接翻譯成本機 CPU 指令**（x86_64 / arm64 / riscv 等），然後執行的是 native code，**不是解釋執行**。

為什麼可以這麼直接？因為 eBPF 的 calling convention 故意對齊 x86_64：

```
eBPF instruction:               x86_64 instruction:
  R1 = R2                  →     mov rdi, rsi
  R0 = R1 + R2             →     mov rax, rdi; add rax, rsi
  if (R1 == 0) goto +5     →     test rdi, rdi; je +5
  call helper_id           →     call helper_addr
```

幾乎是 1:1 翻譯。沒有複雜的 register allocation，沒有 spilling。

```bash
# 看 JIT 後的 native code：
sudo bpftool prog dump jited id <prog_id>
# 看 verified bytecode：
sudo bpftool prog dump xlated id <prog_id>
```

JIT 在 5.x kernel 預設是開的（早期是要 `sysctl net.core.bpf_jit_enable=1`）。沒 JIT 也能跑（解釋器），但慢約 5–10 倍。

## Sandboxing 的關鍵性質

eBPF 雖然能呼叫 helper、能改 map，但**仍然不能 panic kernel**。靠這幾個性質保證：

1. **Memory 隔離**：BPF 程式只能存取自己的 stack、map、明確 verified 過的指標。要讀 kernel memory 要用 `bpf_probe_read_kernel`（會處理 page fault，不會 oops）。
2. **指令數上限**：5.0 之前 4096 instruction，現在放寬到 100 萬（通過 verifier 後）。但**每條 instruction 都要被 verifier 走過至少一次**。
3. **Bounded loop**：5.3 才開放 loop，之前完全禁止。即使現在，loop 邊界必須能被 verifier 推斷。
4. **Helper 白名單**：你不能 call 任意 kernel function。每個 helper 都被 kernel 開發者 review 過。
5. **不能直接寫 kernel 資料結構**：要改 task_struct? 不行。改 packet skb 必須走 helper。

這些限制讓 BPF 程式即使「跑在 ring 0」，也**沒辦法做出讓 kernel 不一致的事**。

## 看一個真的 eBPF disassembly

寫個最簡單的 BPF C：

```c
SEC("kprobe/do_sys_openat2")
int trace_open(struct pt_regs *ctx) {
    bpf_printk("openat called\n");
    return 0;
}
```

`xlated`（verifier 通過後的 bytecode）：

```
0: (b7) r1 = 0                   ; format string ptr (簡化)
1: (b7) r2 = 14                  ; format string len
2: (85) call bpf_trace_printk#6  ; call helper id 6
3: (b7) r0 = 0                   ; return value
4: (95) exit                     ; 結束
```

`jited`（x86_64 native）大概像：

```
nop
nop
mov rdi, 0
mov rsi, 14
call <bpf_trace_printk>
xor rax, rax
ret
```

**幾乎 1:1**。這就是為什麼 eBPF 在熱路徑上也能用 — JIT 後跟手寫 asm 性能差不多。

## 一個常見誤解

「eBPF 是 Java 那種 VM，跑起來會很慢」 — **錯**。

JIT 後是 native code，性能與手寫 C 相當。「VM」這個字在 BPF 語境下指的是「ISA + sandbox 模型」 — 是個**靜態驗證 + 翻譯目標**，不是 runtime 解釋器。

## 動手練習

1. **看一個跑在你機器上的 BPF 程式的 bytecode**：
   ```bash
   sudo bpftool prog list | head -5
   sudo bpftool prog dump xlated id <挑一個 id>
   sudo bpftool prog dump jited id <同一個 id>
   ```
   對比 xlated（eBPF）與 jited（x86_64），體會「幾乎 1:1」是什麼意思。
2. **數一個 helper 用了幾次**：
   ```bash
   sudo bpftool prog dump xlated id <id> | grep "call " | sort | uniq -c
   ```
3. **故意觸發 stack 超限**：晚點 Ch 13 寫 BPF C 時，宣告 `char buf[1024]` 看看 verifier 怎麼抱怨。
4. **看 helper 完整列表**：`man 7 bpf-helpers` — 滑一遍，認識「kernel 開放給 BPF 用的能力範圍」。

## 自我檢核

- [ ] 我能畫出 eBPF VM 的 register layout、stack、calling convention
- [ ] 我能說出 R6–R9 跟 R0–R5 的差別（callee-saved vs caller-saved）
- [ ] 我能解釋 helper function 是什麼、為什麼是白名單
- [ ] 我能說出 JIT 為什麼能做到 1:1 翻譯
- [ ] 我能列出至少 3 個讓 BPF 不能 panic kernel 的 sandbox 機制

下一章我們從 ISA 拉回更高一層 — 看 BPF program 有哪些 type、各自能 attach 到哪、各自能用哪些 helper。這是寫 BPF 之前必備的「全景地圖」。

→ [Ch 7 Program types 與 attach 點全景](./07-program-types-and-attach.md)
