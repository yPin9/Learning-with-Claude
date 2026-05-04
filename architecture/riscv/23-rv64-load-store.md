# Ch 23 — 64 位元 Load/Store：LD / SD / LWU 與資料對齊陷阱

> 目標：掌握 RV64I 完整的 load/store 指令集；理解 sign-extend vs zero-extend 的差異；知道 misaligned access 的行為。

---

## 23.1 完整 Load/Store 指令表

RV32I 的 load 指令加上 RV64I 新增的：

```
指令     操作    符號延伸    RV32I?  RV64I?
------   ------  --------    -----   -----
lb       1 byte  sign-ext     Y       Y
lh       2 byte  sign-ext     Y       Y
lw       4 byte  sign-ext     Y       Y（結果 sext 到 64-bit）
lbu      1 byte  zero-ext     Y       Y
lhu      2 byte  zero-ext     Y       Y
lwu      4 byte  zero-ext     N       Y（新增）
ld       8 byte  n/a          N       Y（新增）

指令     操作    RV32I?  RV64I?
------   ------  -----   -----
sb       1 byte   Y       Y
sh       2 byte   Y       Y
sw       4 byte   Y       Y（store rs2[31:0]）
sd       8 byte   N       Y（新增）
```

Store 指令沒有 sign/zero-extend 的問題——只是把暫存器低 N 位寫入記憶體。

---

## 23.2 LW vs LWU：最容易踩的陷阱

在 RV32I 上，`lw` 載入 32-bit 值，結果就是 32-bit，沒有延伸問題。

在 RV64I 上，`lw` 載入 32-bit 值，要塞進 64-bit 暫存器——必須延伸。`lw` 做 **sign-extend**，`lwu` 做 **zero-extend**。

```
記憶體 addr 存著：0xDEADBEEF（bit 31 = 1）

lw  t0, 0(a0)   # t0 = 0xFFFFFFFFDEADBEEF（符號延伸，負數）
lwu t0, 0(a0)   # t0 = 0x00000000DEADBEEF（零延伸，正數）
```

**什麼時候用哪個：**

```c
int32_t  a = *(int32_t  *)ptr;   // compiler 用 lw
uint32_t b = *(uint32_t *)ptr;   // compiler 用 lwu
```

如果你在組語裡把 `uint32_t` 用 `lw` 載入，高 32-bit 會被 sign-extend 污染：

```c
uint32_t val = 0xDEADBEEF;
// 如果錯誤地用 lw：
// register = 0xFFFFFFFFDEADBEEF
// 下一步用這個值做 64-bit 運算就爛掉了
```

---

## 23.3 LD / SD：64-bit 存取

`ld`（Load Doubleword）：從記憶體載入 8 bytes 到暫存器。

```asm
ld t0, 0(a0)     # t0 = M[a0 + 0]，讀 8 bytes
sd t0, 0(a1)     # M[a1 + 0] = t0，寫 8 bytes
```

`ld` 的結果是完整的 64-bit 值，不需要延伸。

---

## 23.4 對齊（Alignment）要求

RISC-V spec 對記憶體對齊的規定是：

```
存取大小    對齊要求（最低）
--------   ---------------
byte        1 byte（無要求）
halfword    2 byte 對齊
word        4 byte 對齊
doubleword  8 byte 對齊
```

**Misaligned access 的處理**：RISC-V spec 允許兩種行為：
1. 硬體直接 raise `Load address misaligned`（cause=4）或 `Store/AMO address misaligned`（cause=6）exception
2. 硬體透明處理（多次存取然後拼起來）

哪種行為是 **implementation-defined**——你的平台說了算。在 Linux 上，kernel 可以替 misaligned 存取做 trap-and-emulate，但代價是慢很多（數百 cycles）。嵌入式系統上可能直接 crash。

**原則：別做 misaligned access。**

---

## 23.5 Load/Store 的 offset 範圍

所有 load/store 的 offset 是 12-bit sign-extended immediate，範圍是 -2048 到 2047。

```asm
ld t0, 2040(a0)    # 合法，接近最大正 offset
ld t0, -2048(a0)   # 合法，最大負 offset
ld t0, 2048(a0)    # 不合法！超過 12-bit 範圍
```

超出範圍要先 `addi` 調整基底，或用 `lui + addi` 建立 large offset。

---

## 23.6 RV32I vs RV64I Load 行為對照表

| 指令  | RV32I                      | RV64I                                 |
|------|---------------------------|---------------------------------------|
| `lb`  | 載入 1 byte，sext 到 32-bit | 載入 1 byte，sext 到 64-bit            |
| `lh`  | 載入 2 byte，sext 到 32-bit | 載入 2 byte，sext 到 64-bit            |
| `lw`  | 載入 4 byte（就是 XLEN）    | 載入 4 byte，sext 到 64-bit            |
| `lbu` | 載入 1 byte，zext 到 32-bit | 載入 1 byte，zext 到 64-bit            |
| `lhu` | 載入 2 byte，zext 到 32-bit | 載入 2 byte，zext 到 64-bit            |
| `lwu` | 不存在                     | 載入 4 byte，zext 到 64-bit（新增）     |
| `ld`  | 不存在                     | 載入 8 byte（就是 XLEN）               |

---

## 23.7 實際 .S 範例：讀一個 uint64_t array

```asm
# sum_u64: 計算 uint64_t array 的總和
# 參數：a0 = 指標, a1 = 個數
# 返回：a0 = 總和（uint64_t）
.globl sum_u64
sum_u64:
    li   t0, 0          # sum = 0
    li   t1, 0          # i = 0
    beq  a1, zero, .done
.loop:
    ld   t2, 0(a0)      # t2 = arr[i]（8 bytes）
    add  t0, t0, t2     # sum += arr[i]
    addi a0, a0, 8      # ptr++（每個元素 8 bytes）
    addi t1, t1, 1      # i++
    blt  t1, a1, .loop
.done:
    mv   a0, t0         # 返回值放 a0
    ret
```

注意：`ld` 要求 8-byte 對齊。如果 `a0` 指向的陣列不是 8-byte 對齊的，會觸發 misaligned exception。

---

## 23.8 常見錯誤與修正

**錯誤 1：用 lw 讀 uint32_t，後續做 64-bit 比較**

```c
// C code
uint32_t a = get_value();  // 回傳 0xFFFFFFFF
if ((uint64_t)a > 0x100000000ULL) { ... }  // 永遠不成立
```

如果組語用了 `lw`：
```asm
lw t0, val         # t0 = 0xFFFFFFFFFFFFFFFF（sext 了！）
li t1, 0x100000000
bltu t0, t1, skip  # 0xFFFFFFFFFFFFFFFF > 0x100000000，跳過——但邏輯錯了
```

修正：用 `lwu`，確保 zero-extend。

**錯誤 2：store 後立刻 load，忘記 sign-extension**

```asm
li   t0, 0x80000000
sw   t0, 0(sp)       # 存 4 bytes
lw   t1, 0(sp)       # t1 = 0xFFFFFFFF80000000（sign-extended!）
# 你期望 t1 = 0x80000000，但實際上是負數
```

如果後續用 t1 做 64-bit 無符號比較就爛掉。修正：用 `lwu`。

---

## 23.9 Atomic 指令的 Load/Store 變體

RV64A extension（Atomic）也有對應的 doubleword 版本：

```
lr.w / sc.w     # 32-bit load-reserved / store-conditional
lr.d / sc.d     # 64-bit load-reserved / store-conditional
amoadd.w        # 32-bit atomic add
amoadd.d        # 64-bit atomic add
```

這些在 Ch 26（inline assembly）會用到。

---

## 自我檢核

- [ ] 能說清楚 `lw` 和 `lwu` 在 RV64I 上的差別
- [ ] 知道 `ld` 要求幾 byte 對齊
- [ ] 能說出 RISC-V 對 misaligned access 的兩種合法行為
- [ ] 知道 store 指令（sw、sd）只寫暫存器的低 N 位，沒有 sign/zero-extend 問題
- [ ] 能寫一個正確的 uint64_t array 存取迴圈

→ [Ch 24 — LP64D 呼叫慣例](24-lp64d-calling-convention.md)
