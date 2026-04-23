# Ch 22 — DBI 工具比較：Pin / DynamoRIO / Frida / QEMU TCG

> 目標：搞清楚四個主流 Dynamic Binary Instrumentation 工具的設計、能力、取捨。DTA 跟 dynamic analysis 都需要 DBI 當底層，挑錯就慢兩個數量級。

## DBI 是什麼

把 instrumentation code 注入到 target process 的 instruction stream。不需要改 source、不需要重 compile。每個 instruction 可以被你 observe 或改寫。

技術分兩派：

- **Code cache JIT**：動態翻譯 target instruction 到 instrumented 版本，放 code cache 執行（Pin、DynamoRIO、QEMU TCG）
- **Inline hooking**：在特定 address 插 trampoline（Frida）

兩派的 overhead、能力差很多。

## Pin（Intel）

**起源**：Intel 2005 公司內部工具，後開放給學術。

**授權**：**閉源**，免費商業可用（某些限制），需要 Intel license。

**架構**：
- JIT trace：每個 trace（一連串 instruction）被 instrument 後放 code cache
- instrument API：C++，完整 ISA 抽象層
- 支援 x86、x86-64、ARM

**優點**：
- 穩定、成熟（20 年）
- 豐富 ManualExamples 跟 tutorials
- 對 x86 的 instruction coverage 最全
- 學術 DTA 工具（libdft、TaintInduce、LIFT）都基於 Pin

**缺點**：
- 閉源 —  bug 很難自己修
- API 比較 low-level
- Windows 上 setup 痛苦
- Intel CPU only（AMD 理論上可 但沒 support）

**典型 API**：

```c++
VOID RecordMemRead(VOID * ip, VOID * addr) {
    fprintf(trace, "%p R %p\n", ip, addr);
}

VOID Instruction(INS ins, VOID *v) {
    if (INS_IsMemoryRead(ins)) {
        INS_InsertPredicatedCall(
            ins, IPOINT_BEFORE, (AFUNPTR)RecordMemRead,
            IARG_INST_PTR, IARG_MEMORYREAD_EA,
            IARG_END);
    }
}
```

每條 instruction call 一個 function，記下 IP 跟 memory address。基本 DTA 就是這個 pattern 擴展。

**何時選 Pin**：
- DTA / memory analysis
- 複雜 instrumentation，需要全面 API
- 別的 tool 不支援你的 x86 instruction

## DynamoRIO

**起源**：MIT 2001 spin-off，後 Google 主力開發。

**授權**：**BSD 開源**。

**架構**：類似 Pin，JIT-based trace + code cache。

**優點**：
- 開源：bug 能自己 fix、可發論文
- Performance 跟 Pin 相當或更快
- 支援 x86、x86-64、ARM、AArch64
- API 比 Pin 乾淨

**缺點**：
- 學術 DTA 工具相對少（historical inertia，大家都用 Pin）
- Documentation 比 Pin 稀
- Windows 支援 OK 但常有小 bug

**典型 API**：C，比 Pin 的 C++ 低一點層級。

```c
static dr_emit_flags_t
bb_event(void *drcontext, void *tag, instrlist_t *bb, ...) {
    for (instr_t *instr = instrlist_first(bb); instr != NULL;
         instr = instr_get_next(instr)) {
        if (instr_reads_memory(instr)) {
            dr_insert_clean_call(...);
        }
    }
    return DR_EMIT_DEFAULT;
}
```

**何時選 DynamoRIO**：
- 你想 fork / modify DBI 本身
- DMS Memory Sanitizer、Dr. Heap 等 family 工具
- 性能要求極端、需要 DR 的進階 API

## Frida

**起源**：Ole André V. Ravnås 2013 開始，後公司化。

**授權**：**LGPL 開源**。

**架構**：
- Gum（核心 instrumentation 庫）+ Stalker（code tracing）
- 用 inline hook（patching）為主
- JavaScript API 跑在 V8
- 跨平台：Linux、Windows、macOS、iOS、Android

**優點**：
- JavaScript 上手超快 — 寫一個 hook 10 行
- 多平台（iOS / Android reverse 神器）
- REPL 模式（上線後互動 debugging）
- 對 mobile / closed-source app 超合適

**缺點**：
- 不適合 per-instruction 級 DTA — Frida 以 function / address hook 為主
- Stalker 的 per-instruction tracing 效能不如 Pin / DR
- 複雜 instrumentation 寫 JS 會痛

**典型 API**：

```javascript
Interceptor.attach(Module.getExportByName('libc.so', 'open'), {
    onEnter(args) {
        this.path = args[0].readUtf8String();
        console.log('open', this.path);
    },
    onLeave(retval) {
        console.log('  → fd', retval);
    }
});
```

**何時選 Frida**：
- Android / iOS reverse
- 輕量 tracing、hook 特定 function
- Interactive 分析

**不選**：full DTA、symex instrumentation、performance critical

## QEMU TCG

**起源**：Fabrice Bellard 2003。

**授權**：**GPL 開源**。

**架構**：
- TCG（Tiny Code Generator）：把 guest ISA 翻譯到 host ISA
- Per-basic-block JIT
- Full system emulation 或 user-mode emulation

**優點**：
- **跨 ISA**：ARM binary 在 x86 host 跑、MIPS 在 ARM 上跑
- 適合 **firmware / IoT / embedded** 分析
- PANDA、S2E、Rev.ng 都基於 QEMU TCG
- Full-system：能 trace kernel、driver

**缺點**：
- 重 — setup 時間久
- 效能比 Pin / DR 慢（multi-level translation）
- instrument API 要改 QEMU 的 TCG ops

**典型 API**：修改 QEMU 的 translate.c 或用 QEMU plugin（較新 feature）。

**何時選 QEMU TCG**：
- Non-x86 target
- Full-system 需要 kernel trace
- Firmware 或 malware on different arch

## 效能比較（粗略）

跑簡單 DTA-like instrumentation（每個 memory access call 一個 function）：

| 工具 | Slowdown | 註 |
|------|----------|-----|
| 原生 | 1× | baseline |
| Frida (Stalker) | 100–500× | per-instruction 非強項 |
| QEMU TCG user-mode | 10–30× | 跨 ISA 成本 |
| Pin | 5–10× | 優秀 |
| DynamoRIO | 5–10× | 相當 |
| 最佳 inline instrumentation | 3–5× | 極致優化後 |

純 function-level hook（Frida 最強）：

| 工具 | 單 hook overhead |
|------|-----------------|
| Frida inline | ~100 ns |
| Pin | ~1 μs |
| DynamoRIO | ~1 μs |
| QEMU | ~2 μs |

**結論**：不同 granularity 選不同工具。full DTA → Pin / DR；API 監控 → Frida。

## libdft 為什麼挑 Pin

libdft（Ch 21 提到的 DTA 框架）挑 Pin 的理由：

- 學術圈 2011 時 Pin 最主流
- Pin 的 instruction-level API 最全面
- 有 shadow memory 插入的 hook point

今天如果重寫 libdft，可能會用 DynamoRIO — BSD 授權、performance 相當、社群持續活躍。

## Triton 的 instrumentation

Triton 自己不是 DBI，它是**一個 concolic / symex 引擎**，輸入是 instruction、輸出是 symbolic analysis。它需要 DBI 當**上游**把 instruction 餵給它。

常見組合：
- Pintool 把 instruction 餵 Triton
- 或你自己用 Pyda（Python DBI）包裝

Triton 有 `triton.loaders.pintool` 直接支援 Pin integration。Ch 23 細講。

## 寫一個最小 Pin tool 試試水

Pin 的 hello world — counts instructions:

```cpp
// inscount.cpp
#include "pin.H"
#include <iostream>
UINT64 icount = 0;

VOID CountBBL(UINT32 num) { icount += num; }

VOID Trace(TRACE trace, VOID *v) {
    for (BBL bbl = TRACE_BblHead(trace); BBL_Valid(bbl); bbl = BBL_Next(bbl)) {
        BBL_InsertCall(bbl, IPOINT_ANYWHERE, (AFUNPTR)CountBBL,
                       IARG_UINT32, BBL_NumIns(bbl), IARG_END);
    }
}

VOID Fini(INT32 code, VOID *v) {
    std::cerr << "Count: " << icount << std::endl;
}

int main(int argc, char *argv[]) {
    PIN_Init(argc, argv);
    TRACE_AddInstrumentFunction(Trace, 0);
    PIN_AddFiniFunction(Fini, 0);
    PIN_StartProgram();
    return 0;
}
```

build & run：

```bash
# build
cd $PIN_ROOT/source/tools/ManualExamples
# 複製你的 inscount.cpp
make TARGET=intel64

# run
pin -t obj-intel64/inscount.so -- /bin/ls
# Count: 1234567
```

這是所有 Pin tool 的 skeleton。DTA 就是把 `CountBBL` 換成 shadow update 邏輯。

## 寫一個最小 DynamoRIO tool

DR 的 hello world：

```c
// inscount_dr.c
#include "dr_api.h"
static uint64 count;

static void inst_callback(void) { count++; }

static dr_emit_flags_t event_bb(void *drcontext, void *tag,
                                 instrlist_t *bb, bool for_trace, bool translating) {
    for (instr_t *i = instrlist_first(bb); i; i = instr_get_next(i)) {
        dr_insert_clean_call(drcontext, bb, i, (void *)inst_callback, false, 0);
    }
    return DR_EMIT_DEFAULT;
}

static void event_exit(void) { dr_printf("Count: %lu\n", count); }

DR_EXPORT void dr_client_main(client_id_t id, int argc, const char **argv) {
    dr_register_bb_event(event_bb);
    dr_register_exit_event(event_exit);
}
```

cmake + drrun 跑：

```bash
# build
mkdir build && cd build
cmake -DDynamoRIO_DIR=... ..
make
# run
drrun -c ./inscount_dr.so -- /bin/ls
```

API 相對 Pin **更明確**（沒有 IARG_ 系列 macro），但一樣的 pattern。

## 寫一個最小 Frida script

JS 極簡：

```javascript
// inscount.js
let count = 0;
Stalker.follow(Process.getCurrentThreadId(), {
    events: {
        call: false, ret: false, exec: true,
        block: false, compile: false
    },
    onReceive(events) {
        count += Stalker.parse(events).length;
    }
});

Process.setExceptionHandler(ex => {
    console.log('Count:', count);
    return false;
});
```

```bash
frida -l inscount.js /bin/ls
```

Frida 的 Stalker 比 Pin / DR 慢很多，但 10 行 JS 就能跑。

## 推薦選擇

| 你要做什麼 | 選什麼 |
|-----------|-------|
| x86 DTA / symex with custom engine | Pin |
| Open-source DTA，性能敏感 | DynamoRIO |
| Android / iOS 反逆向 | Frida |
| Firmware on ARM/MIPS | QEMU TCG |
| 快速 prototype、功能簡單 | Frida |
| 複雜 production DTA | Pin |
| Cross-arch emulation | QEMU |

## 常見踩雷

- **Pin 遇到 AVX512**：某些新 instruction support 來晚 — 檢查 Pin 版本
- **DynamoRIO 在 Linux kernel 6.x**：有時 signal handling 出錯 — 用 patch 版
- **Frida 在 Android 13+**：system hooks 越來越難 — 需要 root 或 patch
- **QEMU 版本選擇**：研究 paper 常綁定特定版本（QEMU 3.x vs 7.x），binary 相容性看運氣

每個工具都有 sharp edges。生產使用前做 smoke test。

## 心法

DBI 是 DTA 跟 dynamic symex 的 base layer。跟其他層一樣，**選對 base 決定你的工具上限**。

Pin 跟 DynamoRIO 是業界標配。Frida 是 RE / Android 霸主。QEMU 是跨 ISA 唯一選擇。

想做一個實用的 DTA 工具：**用 Pin 起步**（資源多、例子多），熟了再考慮 DR。

## 自我檢核

- [ ] 解釋 code cache JIT 與 inline hook 的差別
- [ ] 對每個工具，講得出最典型的 use case
- [ ] 知道 libdft / Triton 的 upstream DBI 是什麼
- [ ] 能寫出 Pin 或 DR 的最小 instruction counter
- [ ] 知道哪些 case 該用 Frida（app hook）vs Pin（full DTA）

下一章拆 libdft 與 Triton 的**架構內部** — 這兩個是 DTA 世界的代表作，看它們怎麼把 Ch 20-22 的概念落地。

→ [Ch 23 — libdft 與 Triton 的架構解剖](./23-libdft-triton.md)
