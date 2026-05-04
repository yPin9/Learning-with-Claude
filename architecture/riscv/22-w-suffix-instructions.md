# Ch 22 — W 後綴指令全解：ADDW/SUBW/SLLW/SRLW/SRAW 的 sign-extension 語意

> 目標：精確掌握每條 W 後綴指令的語意；能預測 compiler 在什麼情況下選 W 後綴；能用手動追蹤避開 32-bit wrap-around 的陷阱。

---

## 22.1 為什麼需要 W 後綴

RV64I 的 `add` 是 64-bit 加法。但 C 語言的 `int` 是 32-bit，它的 overflow 語意是 32-bit 的——加到 `0x7FFFFFFF` 再加 1 要得到 `0x80000000`（對 `int` 而言是 -2147483648）。

如果用 64-bit 的 `add` 做這件事：

```
t0 = 0x000000007FFFFFFF
add t0, t0, 1
t0 = 0x0000000080000000   # 這是正數 2147483648，不是 -2147483648
```

C 的 `int` overflow 是 undefined behavior，但實務上 compiler 期待 32-bit wrap-around。W 後綴指令解決這個問題：它只對暫存器的低 32-bit 操作，然後把結果 **sign-extend（符號延伸）到 64-bit** 存回暫存器。

---

## 22.2 完整 W 後綴指令列表

| 指令              | 格式   | 語意                                                    |
|-----------------|------|-------------------------------------------------------|
| `addw rd,rs1,rs2` | R 型  | rd = sext32(rs1[31:0] + rs2[31:0])                    |
| `subw rd,rs1,rs2` | R 型  | rd = sext32(rs1[31:0] - rs2[31:0])                    |
| `sllw rd,rs1,rs2` | R 型  | rd = sext32(rs1[31:0] << rs2[4:0])                    |
| `srlw rd,rs1,rs2` | R 型  | rd = sext32(rs1[31:0] >> rs2[4:0])（邏輯右移）           |
| `sraw rd,rs1,rs2` | R 型  | rd = sext32(rs1[31:0] >>> rs2[4:0])（算術右移）          |
| `addiw rd,rs1,imm`| I 型  | rd = sext32(rs1[31:0] + sext12(imm))                  |
| `slliw rd,rs1,shamt`| I 型| rd = sext32(rs1[31:0] << shamt[4:0])                  |
| `srliw rd,rs1,shamt`| I 型| rd = sext32(rs1[31:0] >> shamt[4:0])（邏輯）            |
| `sraiw rd,rs1,shamt`| I 型| rd = sext32(rs1[31:0] >>> shamt[4:0])（算術）           |

`sext32(x)` 的定義：把 32-bit 值的 bit 31 延伸到 bit 63。

```c
// sext32 等價 C code
int64_t sext32(uint32_t x) {
    return (int64_t)(int32_t)x;
}
```

---

## 22.3 手動追蹤：addiw 的 sign-extension

**案例 1：正常加法**

```
t0 = 0x0000000000000001   # long long 1
addiw t0, t0, 1
  低 32 bit 加法：0x00000001 + 1 = 0x00000002
  sext32：bit 31 = 0，高位填 0
t0 = 0x0000000000000002   # 正確
```

**案例 2：32-bit overflow，bit 31 翻轉**

```
t0 = 0x000000007FFFFFFF   # int32_t 最大值
addiw t0, t0, 1
  低 32 bit 加法：0x7FFFFFFF + 1 = 0x80000000
  sext32：bit 31 = 1，高位填 1
t0 = 0xFFFFFFFF80000000   # = int64_t 的 -2147483648
```

這就是 C `int` overflow 的正確行為。

**案例 3：同樣情況用 addi**

```
t0 = 0x000000007FFFFFFF
addi t0, t0, 1
  64-bit 加法：0x7FFFFFFF + 1 = 0x80000000
  沒有 sext，高位還是 0
t0 = 0x0000000080000000   # = int64_t 的 2147483648，語意錯誤
```

---

## 22.4 RV32I vs RV64I 指令選擇對照

| C 型別操作          | RV32I 用     | RV64I 用     |
|-------------------|-------------|-------------|
| `int a = b + c`   | `add`       | `addw`      |
| `int a = b - c`   | `sub`       | `subw`      |
| `int a = b + 1`   | `addi`      | `addiw`     |
| `int a = b << 2`  | `slli`      | `slliw`     |
| `int a = b >> 2`（有符號）| `srai` | `sraiw`  |
| `long a = b + c`  | N/A（沒有 long in RV32I LP32）| `add` |
| `size_t a = b + c`| `add`       | `add`       |
| `uint32_t a = b + c`| `add`     | `addw`（compiler 選這個）|

重點：**不管有無符號，只要操作語意是 32-bit，compiler 就用 W 後綴**。這是因為 32-bit 的 wrap-around 是相同的（補碼）。

---

## 22.5 迴圈計數器的陷阱

這是實際遇到的 bug 類型：

```c
// 32-bit 迴圈計數器，正確
for (int i = 0; i < n; i++) {
    arr[i] = i;
}
```

compiler 在 RV64I 上會用 `addiw` 遞增 `i`。如果你用組語手寫這個迴圈，用 `addi` 會怎樣？

```asm
# 錯誤版：用 addi
    li   a0, 0x7FFFFFFF    # i = 2147483647
loop:
    addi a0, a0, 1         # a0 = 0x80000000（正數 2147483648）
    blt  a0, a1, loop      # 以 64-bit signed 比較，0x80000000 > 0，可能不 overflow
```

```asm
# 正確版：用 addiw
    li   a0, 0x7FFFFFFF    # i = 2147483647
loop:
    addiw a0, a0, 1        # a0 = 0xFFFFFFFF80000000（= -2147483648）
    blt   a0, a1, loop     # 以 64-bit signed 比較，負數 < 0，迴圈終止
```

差別是：`addi` 在 RV64 上永遠是 64-bit 算術，`addiw` 保持 32-bit 的 wrap-around 語意。

---

## 22.6 Compiler 輸出對照

```c
#include <stdint.h>

void loop_int(int *arr, int n) {
    for (int i = 0; i < n; i++)
        arr[i] = i;
}

void loop_long(long *arr, long n) {
    for (long i = 0; i < n; i++)
        arr[i] = i;
}
```

```bash
riscv64-unknown-elf-gcc -O2 -S loop.c
```

`loop_int` 的計數器遞增：
```asm
    addiw a3, a3, 1    # int i++，W 後綴
```

`loop_long` 的計數器遞增：
```asm
    addi  a3, a3, 1    # long i++，64-bit
```

---

## 22.7 移位指令的細節

`sllw`、`srlw`、`sraw` 的 shift amount 只用 rs2 的 bit [4:0]（最多移 31 位），因為操作的是 32-bit 值。

```
sllw rd, rs1, rs2
  shamt = rs2[4:0]          # 注意：不是 rs2[5:0]
  result32 = rs1[31:0] << shamt
  rd = sext32(result32)
```

這和 64-bit 的 `sll` 不同——`sll` 用 rs2[5:0]，最多移 63 位。

**陷阱**：如果你把 rs2 的值設成大於 31，`sllw` 還是只取低 5-bit，不會出錯，但可能不是你想要的。

---

## 22.8 實際應用：手動計算

追蹤以下程式碼的執行：

```asm
    li   t0, -1          # t0 = 0xFFFFFFFFFFFFFFFF
    addiw t1, t0, 1      # t1 = ?
    addi  t2, t0, 1      # t2 = ?
```

`addiw t1, t0, 1`：
- t0[31:0] = 0xFFFFFFFF，+ 1 = 0x00000000（32-bit overflow）
- sext32(0x00000000) = 0x0000000000000000
- t1 = 0

`addi t2, t0, 1`：
- t0 = 0xFFFFFFFFFFFFFFFF，+ 1 = 0x0000000000000000（64-bit overflow）
- t2 = 0

這次結果一樣，但原因不同。試試 t0 = 0x7FFFFFFF00000000 就會看到差異。

---

## 自我檢核

- [ ] 能默寫 `addiw` 的精確語意（低 32-bit 操作 + sext32 到 64-bit）
- [ ] 能說出為什麼 32-bit 迴圈計數器要用 `addiw` 而不是 `addi`
- [ ] 知道 `sllw` 的 shamt 是幾位（5-bit，不是 6-bit）
- [ ] 能追蹤 `addiw t0, t0, 1` 在 t0 = 0x7FFFFFFF 時的結果
- [ ] 能用 compiler 輸出驗證 `int` 和 `long` 使用不同指令

→ [Ch 23 — 64 位元 Load/Store](23-rv64-load-store.md)
