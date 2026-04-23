# 練習 B — Debug 一個 relax 炸掉的 bug

> 目標：體驗 RISC-V linker relaxation 造成的 bug 長什麼樣、如何系統化 debug。這個練習你要**故意**造 bug、診斷、修復。

## 背景故事

想像你在 SiFive 幫客戶 debug：

> 客戶：「我們的 `libcrypto.so` 裡的 AES-CBC function，在某些 build 下 hash 值跟 reference 不匹配。奇怪的是只有用 LLD 編會錯，GNU ld 沒事。」

你的任務：**重現 + 診斷 + 修復**。

## 準備檔案

### `victim.c`

```c
#include <stdio.h>

// 故意寫一段依賴精確地址的 code
// 假設這是某個特殊的 table lookup 或 trampoline
void compute_aligned(int *table) {
    // 這段要求 PC 與 table 的 delta 是固定的
    // 一旦 relax 改變距離，某些 offset 假設就錯
    printf("compute at %p, table at %p\n", (void*)compute_aligned, (void*)table);
    int sum = 0;
    for (int i = 0; i < 16; i++) sum += table[i];
    printf("sum = %d\n", sum);
}
```

### `main.c`

```c
#include <stdio.h>
int table[16] = {1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16};
void compute_aligned(int *);
int main(void) {
    compute_aligned(table);
    return 0;
}
```

正常情況下沒問題，因為 compiler / linker 會正確處理 relocation。**我們要創造一個假的 bug scenario**。

## 實驗 1：看 relax 有沒有影響這個 code

```bash
# Without relax
riscv64-linux-gnu-gcc -O1 victim.c main.c -Wl,--no-relax -o no_relax
# With relax (default)
riscv64-linux-gnu-gcc -O1 victim.c main.c -o with_relax

# Diff objdump
riscv64-linux-gnu-objdump -d no_relax > no_relax.asm
riscv64-linux-gnu-objdump -d with_relax > with_relax.asm
diff no_relax.asm with_relax.asm | head -30
```

你會看到一些 instruction 在 `with_relax` 版被縮短了。

## 實驗 2：用 inline asm 造依賴 offset 的 code

```c
// victim2.c
#include <stdio.h>

extern int table[];

__attribute__((naked)) void trampoline(void) {
    // 假設這個 trampoline 嘗試 load 從本 function + 固定 offset 處
    // 的資料。如果 relax 砍 byte，offset 會錯。
    asm volatile(
        "auipc t0, 0\n"
        "lw    a0, 24(t0)\n"  // 硬寫 +24 的 offset
        "ret\n"
        ".word 42\n"          // 預期在 offset +12
        ".word 43\n"          // offset +16
        ".word 44\n"          // offset +20
        ".word 45\n"          // offset +24 <- 想要 load 的 target
    );
}

int main(void) {
    int r;
    // call trampoline 印 45
    asm volatile("call trampoline" : "=a"(r));
    printf("got %d\n", r);
    return 0;
}
```

這是人為 fragile pattern：**code 跟 data 的相對 offset 被寫死**。

試 relax vs no-relax：

```bash
gcc victim2.c -o v2        # default relax
gcc victim2.c -Wl,--no-relax -o v2_no
./v2
./v2_no
```

如果你讓「上游的 code 被 relax 縮了」，`trampoline` 跟 data 的對應可能破壞。這種 bug 在 JIT / 手寫 stub 常見。

## 實驗 3：diag 真實 scenario — misaligned function entry

模擬「我要求某個 function 入口 16-byte 對齊，但 relax 破壞了」：

```c
// victim3.c
#include <stdio.h>

__attribute__((aligned(16))) void aligned_func(void) {
    printf("aligned_func at %p (should be 16-aligned)\n",
           (void*)aligned_func);
}

__attribute__((noinline)) void some_call(void) {
    printf("calling aligned_func\n");
    aligned_func();
}

int main(void) {
    some_call();

    // 檢查對齊
    unsigned long addr = (unsigned long)aligned_func;
    if (addr % 16 != 0) {
        printf("ERROR: aligned_func not 16-byte aligned: 0x%lx\n", addr);
        return 1;
    }
    printf("OK: aligned\n");
    return 0;
}
```

```bash
gcc -O1 victim3.c -o v3
gcc -O1 victim3.c -Wl,--no-relax -o v3_no
./v3
./v3_no
```

在某些 toolchain 版本 + 特定 code pattern 下，會發生 `aligned_func` 不對齊。這就是 Ch 7 講的 `R_RISCV_ALIGN` 處理的對象。

## Debug workflow 應用

假設你在 production 碰到客戶的問題：

### Step 1: 確認是 relax 造成

```bash
# 用 --no-relax 重編 library
# 丟回客戶測試
# 如果解掉 → 確認 relax 是 root cause
```

### Step 2: 找出具體哪段 code

```bash
# 對比 relax vs no-relax 的 objdump
objdump -d libbad.so > bad.asm
objdump -d libgood.so > good.asm
diff bad.asm good.asm | head -100
```

找 diff 的部分。多半在 hot function 或特殊 asm。

### Step 3: 分析指令差異

檢查 diff 位置的 relocation：

```bash
objdump -r libbad.so.o | grep -A2 "offset 0x..."
```

認出是 `R_RISCV_CALL` → `jal` 的縮減、或 `R_RISCV_PCREL_HI20` → gp-relative 的 relax。

### Step 4: 確認理論分析

用 spike 或 qemu 的 trace 模式跑 both versions，看執行哪條指令走偏。

### Step 5: 修復

選項：

- **Library 層**：加 `__attribute__((section(".text.noalign_safe")))` + linker script 控制 placement
- **Toolchain 層**：`-Wl,--no-relax` 全部關
- **Source 層**：避免 fragile pattern（JIT 改走 table 而非硬編 offset）

對上游貢獻：

- 如果是 linker bug → 報告 LLD / binutils
- 如果是 compiler 產 code 錯 → 報告 Clang / GCC

## 練習：重現一個真實 LLVM issue

找 LLVM project 的 tracker：

```
https://github.com/llvm/llvm-project/issues?q=is%3Aissue+RISCV+relax+label%3Abug
```

挑一個已 closed 的 bug，讀 bug report + fix commit。

目標：能獨立重現、理解 root cause、修復方式。

## Debug 小工具：自己寫一個 diff tool

用 Python 寫：

```python
import subprocess

def disasm(path):
    r = subprocess.run(
        ['riscv64-linux-gnu-objdump', '-d', path],
        capture_output=True, text=True
    )
    return r.stdout

def find_diffs(a_path, b_path):
    a = disasm(a_path).splitlines()
    b = disasm(b_path).splitlines()

    # 找出不同的 block
    diffs = []
    for i, (la, lb) in enumerate(zip(a, b)):
        if la != lb:
            diffs.append((i, la, lb))
    return diffs

if __name__ == '__main__':
    diffs = find_diffs('v3_no', 'v3')
    for i, la, lb in diffs[:30]:
        print(f"Line {i}:\n  no-relax: {la}\n  relax:    {lb}\n")
```

這類 script 讓你快速找差異。加 color / grep 特定 pattern 會更好用。

## 自我檢核

- [ ] 我能重現一個受 relax 影響的 code pattern
- [ ] 我能用 `--no-relax` 比對驗證
- [ ] 我能用 objdump diff 找出 relax 修改的位置
- [ ] 我能分辨「真 bug」vs「使用者錯誤假設」
- [ ] 我知道如何報 upstream issue

## 面試常問

「怎麼 debug 一個 RISC-V-specific linker bug？」

你現在能答：

1. `--no-relax` 驗證是否 relax 造成
2. 對比 objdump 找 diff
3. 檢查 diff 處的 relocation type
4. 查對應 section 的 alignment / code-model 假設
5. 必要時用 spike trace 驗證
6. 修 library code、linker flag、或上游 linker

這套流程在 SiFive 的日常 debug 很值錢。

## 下一步

→ [Final Project：Mini static linker](./final-project-mini-linker.md)
