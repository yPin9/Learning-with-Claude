# Ch 18 — GCC 對照篇：machine description / match.pd

> 目標：理解 GCC backend 的架構、跟 LLVM 的差異、RISC-V 在 GCC 的實作。job spec 明確提 GNU toolchain，這章讓你有能力改 GCC 而不是只能改 LLVM。

## 為什麼要懂 GCC

- **RISC-V production toolchain 大多 GCC**：Ubuntu、Fedora、多數 vendor 的 SDK 主力是 GCC
- **客戶支援**：SiFive 客戶用 GCC 是 default 選擇
- **SiFive job spec 明寫**：「work with ... GNU toolchain recipes」—— 不懂 GCC 不行
- **跨 check**：某個 bug 在 LLVM 上發生，看 GCC 怎麼做對可以 guide 修法

本章不會把你變成 GCC hacker，但你會有「看 GCC RISC-V code 不陌生」的能力。

## GCC vs LLVM 架構對比

| 議題 | GCC | LLVM |
|------|-----|------|
| Frontend | C, C++, Fortran, Go, Ada | Clang (C/C++), Rust, Swift via LLVM |
| IR | GIMPLE + RTL（兩層）| LLVM IR 一層 |
| Optimization | GIMPLE passes | LLVM passes |
| Backend | RTL + machine description | SelectionDAG + TableGen |
| Pattern language | `.md` + `match.pd` | TableGen |
| License | GPL | Apache 2 |
| Development | Free Software Foundation / 社群 | Apache + community |

**最大差異**：GCC 有兩個 IR（GIMPLE 跟 RTL），LLVM 只有一個。

## GIMPLE：GCC 的高階 IR

GIMPLE 類似 LLVM IR，SSA-like，target-neutral。

```c
int foo(int a, int b) { return a + b; }
```

GIMPLE dump：

```
foo (int a, int b)
{
    int _2;
    _2 = a + b;
    return _2;
}
```

比 LLVM IR 更接近 C 語法。看 dump：

```bash
gcc -fdump-tree-all hello.c
# 產生 hello.c.001t.tu, hello.c.004t.gimple, hello.c.030t.optimized 等
```

## RTL：GCC 的低階 IR

RTL (Register Transfer Language) 是 backend IR。long S-expression 式：

```
(insn 1 0 2 (parallel [
    (set (reg:SI 100) (plus:SI (reg:SI 5) (reg:SI 6)))
    (clobber (reg:SI 101))
]) "" -1 nil)
```

看起來像 Lisp。每條 insn 描述一個 RTL statement。

RTL 比 GIMPLE 低階，**接近 assembly 但還沒完全**。

## Machine Description (`.md`) — GCC 的 TableGen

**`.md` 檔**描述 target machine 的 instruction。RISC-V 的在：

```
gcc/config/riscv/riscv.md           ← 主 file
gcc/config/riscv/bitmanip.md         ← Zba/Zbb/Zbc/Zbs
gcc/config/riscv/vector.md           ← V extension
gcc/config/riscv/sync.md             ← Atomic
gcc/config/riscv/predicates.md       ← operand predicates
```

語法像 Lisp：

```
;; RISC-V ADD
(define_insn "adddi3"
  [(set (match_operand:DI 0 "register_operand" "=r")
        (plus:DI (match_operand:DI 1 "register_operand" "r")
                 (match_operand:DI 2 "arith_operand"    "rI")))]
  "TARGET_64BIT"
  "add\t%0,%1,%2"
  [(set_attr "type" "arith")
   (set_attr "mode" "DI")])
```

拆解：

- `define_insn "adddi3"`：宣告指令，名字 `adddi3`（ADD of 64-bit int）
- `(set ... (plus ...))`：這條指令的語意 pattern
- `match_operand:DI 0 "register_operand" "=r"`：operand 0 = DI (double int) mode, register, write-only
- `"TARGET_64BIT"`：condition (only RV64)
- `"add\t%0,%1,%2"`：asm template
- attribute list

**類似 LLVM 的 `def ADD : ...`** 但語法完全不同。

## `match.pd` — 全新的 pattern language

2014 後 GCC 引入 `match.pd`，取代很多舊 peephole rule：

```lisp
/* x + 0 → x */
(simplify
    (plus @0 integer_zerop)
    @0)

/* x * 1 → x */
(simplify
    (mult @0 integer_onep)
    @0)
```

像 LLVM 的 InstCombine。比 `.md` 更乾淨的 rewriting language。

## RISC-V GCC backend 地圖

```
gcc/config/riscv/
├── riscv.c            ← 主要 backend 實作（target hooks 等）
├── riscv.h            ← Target macros
├── riscv.md           ← 主 machine description
├── bitmanip.md
├── vector.md
├── sync.md
├── riscv-cores.def    ← 定義 -mcpu= 的 core
├── riscv-ext.def      ← 定義 extension
├── riscv-common.cc    ← march parsing
├── riscv-vsetvl.cc    ← VSETVLI insertion pass (類 LLVM 的)
├── riscv-vector-builtins.cc  ← RVV intrinsic
└── (many more)
```

檔案比 LLVM 少，因為 GCC backend 結構更 monolithic。

## Adding instruction to GCC backend

加一條指令（例 `XMADD`）：

1. **在 `riscv-ext.def` 宣告 extension**：
   ```c
   DEFINE_RISCV_EXT(XMyExt, "xmyext", ...)
   ```
2. **在 `riscv.md` 或新 `.md` 檔加 `define_insn`**：
   ```
   (define_insn "riscv_xmadd"
     [(set (match_operand:DI 0 "register_operand" "=r")
           (plus:DI (mult:DI (match_operand:DI 1 "register_operand" "r")
                             (match_operand:DI 2 "register_operand" "r"))
                    (match_operand:DI 3 "register_operand" "r")))]
     "TARGET_XMYEXT"
     "xmadd\t%0,%1,%2,%3")
   ```
3. **加 builtin**：`riscv-builtins.cc` 新增 `__builtin_riscv_xmadd`
4. **改 march parser**：讓 `-march=rv64gc_xmyext` 認得
5. **Test + doc**

類似 LLVM 的流程，但每步都在不同檔案、語法不同。

## Target hook

GCC 的「target 可以 override 的 function」叫 target hook。類似 LLVM 的 `TargetLowering` 方法。

```c
// gcc/config/riscv/riscv.c
static bool riscv_legitimate_address_p(machine_mode mode, rtx x, bool strict_p, ...) {
    // 判斷這個地址是否合法 for this target
    ...
}

#undef TARGET_LEGITIMATE_ADDRESS_P
#define TARGET_LEGITIMATE_ADDRESS_P riscv_legitimate_address_p
```

宣告 hook 實作、然後綁定。GCC 的 machine 抽象很多透過 hook。

## Pass pipeline 的對比

GCC：

```
Front end → GENERIC → GIMPLE → RTL → assembly
                      ↓              ↓
                  GIMPLE passes   RTL passes
                  (~200 個)       (~80 個)
```

LLVM：

```
Front end → LLVM IR → MIR → assembly
                     ↓      ↓
                  IR passes  Backend passes
```

GCC 的 pass 比 LLVM 多，但很多是 GIMPLE 層的 high-level optimization。

## TableGen vs machine description：語法對比

相同 `ADD` 指令：

**LLVM TableGen**：
```tablegen
def ADD : ALU_rr<0b0000000, 0b000, "add", /*Commutable=*/1>;
def : Pat<(add GPR:$rs1, GPR:$rs2), (ADD GPR:$rs1, GPR:$rs2)>;
```

**GCC .md**：
```
(define_insn "adddi3"
  [(set (match_operand:DI 0 "register_operand" "=r")
        (plus:DI (match_operand:DI 1 "register_operand" "r")
                 (match_operand:DI 2 "register_operand" "r")))]
  "TARGET_64BIT"
  "add\t%0,%1,%2")
```

核心概念一樣（輸入 / pattern / 輸出），語法差很多。

## GCC 的 intrinsic 實作

`gcc/config/riscv/riscv-builtins.cc`：

```c
static const struct riscv_builtin_description riscv_builtins[] = {
    { "__builtin_riscv_cpop_32", RISCV_BUILTIN_UNOP_INT, CODE_FOR_ctzsi2, ... },
    ...
};
```

每個 builtin 一 entry，bind 到 `.md` 的 insn name。

Clang 那邊的 `TARGET_BUILTIN` macro 類似但不同結構。

## GCC 的 vsetvl pass

GCC 也有自己的 VSETVLI insertion pass：`gcc/config/riscv/riscv-vsetvl.cc`。類似 LLVM 的 `RISCVInsertVSETVLI.cpp`、但實作獨立。

**重點**：LLVM 跟 GCC 各自實作同一個 optimization。**兩邊都要跟**、bug 可能一邊有另一邊沒有。

## 要看哪個 compiler

- **用戶需求在 GCC**：改 GCC
- **upstream LLVM 快**：新 feature 常 LLVM 先做
- **兩個都要有** 是現實：新 extension 同時 port

多數 SiFive 工程師**同時熟悉兩個**，但主力會在一個。新人 onboarding 通常從 LLVM 開始（source 較乾淨）、再學 GCC。

## 實用對照表

| 需求 | LLVM 位置 | GCC 位置 |
|------|----------|---------|
| 加 instruction | `.td` file | `.md` file |
| 加 pattern | TableGen `Pat<>` | `.md` `define_insn` 或 `match.pd` |
| Target hook | `RISCVISelLowering.cpp` | `riscv.c` |
| 加 extension flag | `RISCVFeatures.td` + `RISCVISAInfo.cpp` | `riscv-ext.def` + `riscv-common.cc` |
| 加 intrinsic | `IntrinsicsRISCV.td` + `CGBuiltin.cpp` | `riscv-builtins.cc` |
| Scheduling | `RISCVSchedXXX.td` | `riscv.md` 的 `set_attr` + `.def` |
| MC / assembler | `MCTargetDesc/`, `AsmParser/` | Integrated into gas (`gas/config/tc-riscv.c`) |

## Debug GCC backend

```bash
# Dump GIMPLE
gcc -fdump-tree-all hello.c
# → hello.c.NNt.NAME files

# Dump RTL
gcc -fdump-rtl-all hello.c
# → hello.c.NNr.NAME files

# List all passes
gcc -fdump-passes hello.c

# 看 specific pass
gcc -fdump-tree-optimized hello.c
```

用途類似 LLVM 的 `-print-after-all`，但語法不同。

## GCC plugin

GCC 也支援 plugin（像 LLVM pass plugin）：

```c
#include "gcc-plugin.h"
#include "tree-pass.h"

int plugin_init(struct plugin_name_args *plugin_info, ...) {
    register_pass(...);
    return 0;
}
```

少用。多數時候改 GCC 本體比 plugin 直接。

## 哪個 compiler 寫起來快

主觀：

- **LLVM source 更乾淨**（C++ 模板風、結構清楚）
- **GCC 歷史包袱重**（C + macro 多、學習曲線陡）
- **TableGen 比 `.md` 抽象一點**（LLVM 較多自動 gen）
- **`match.pd` 比 LLVM InstCombine 乾淨**（純宣告式）

但 GCC 穩定、測試充分、用戶廣。**不要忽視 GCC**。

## GCC RISC-V 的核心檔案（讀這幾個）

```
gcc/config/riscv/riscv.c              ; 最重要，2 萬行 +
gcc/config/riscv/riscv.md             ; 主 instruction description
gcc/config/riscv/riscv-c.c            ; C frontend support
gcc/config/riscv/riscv-common.cc      ; march parsing 等
gcc/config/riscv/riscv-vsetvl.cc      ; RVV VSETVL pass
```

讀 `riscv.md` 最能體會 GCC 的 MD 風格。

## 常見誤會

1. **「GCC 比較老就落後」**：不。RISC-V 新 extension GCC 常常 production-first。
2. **「GCC 只能 C / C++」**：有 Fortran、Ada、Go 等。多數 RISC-V 工作是 C。
3. **「GPL 代表不能改」**：能改，但發布要遵守 GPL（source 公開）。改自己用或 upstream 都 OK。
4. **「兩個 compiler 產同樣 code」**：不完全。quality 總體相近、但 corner case 差異。
5. **「我只要熟一個就好」**：短期 OK，長期被限制。兩個都懂是 SiFive level 的基本要求。

## 動手練習

1. 裝 GCC source（`git clone git://gcc.gnu.org/git/gcc.git`），讀 `gcc/config/riscv/riscv.md` 的前 200 行。
2. 用 `-fdump-tree-optimized` 產 GIMPLE dump，對比同 C code 的 LLVM IR。
3. 用 `-fdump-rtl-all` 產 RTL dump，挑一個 pass 的 output 看。
4. 對同一 C code 比 `clang -O2 -S` 跟 `gcc -O2 -S` 的 asm，找差異。
5. 讀 GCC 的 `riscv-vsetvl.cc`，skim 比 LLVM 的 `RISCVInsertVSETVLI.cpp` 大約少多少行。

## 自我檢核

- [ ] 我能解釋 GIMPLE 跟 RTL 的差異
- [ ] 我知道 `.md` 的 `define_insn` 基本語法
- [ ] 我能在 GCC 跟 LLVM 的 RISC-V backend 找對應檔
- [ ] 我知道 `match.pd` 做什麼
- [ ] 我能讀 `-fdump-tree-optimized` 跟對應 LLVM pass dump

下一章講如何送 patch upstream — 全球 ~100 位 LLVM RISC-V contributor 的日常。

→ [Ch 19 如何送 upstream：review 流程與文化](./19-upstream-contribution.md)
