# Ch 24 — LP64D 呼叫慣例：argument passing、return value、stack frame layout

> 目標：能根據 LP64D ABI 規則，手動判斷任何函式的參數如何傳遞、stack frame 如何佈局；能看懂 gcc 產生的 prologue/epilogue。

---

## 24.1 LP64D 是什麼

ABI（Application Binary Interface）命名慣例：

```
ILP32    int=32, long=32, pointer=32
LP64     int=32, long=64, pointer=64   ← RV64 Linux 用這個
ILP64    int=64, long=64, pointer=64   ← 罕見

D 後綴   = double-precision FP 用 FP 暫存器傳遞
F 後綴   = single-precision FP 用 FP 暫存器傳遞
```

RISC-V RV64 Linux 的預設 ABI 是 **LP64D**：
- `int` 是 32-bit
- `long`、`pointer`、`size_t`、`intptr_t` 都是 64-bit
- `double` 用浮點暫存器傳遞

---

## 24.2 ILP32D vs LP64D 型別大小對照

| 型別        | ILP32D（RV32） | LP64D（RV64） |
|-----------|-------------|------------|
| `char`    | 1           | 1          |
| `short`   | 2           | 2          |
| `int`     | 4           | 4          |
| `long`    | 4           | 8          |
| `long long`| 8          | 8          |
| `pointer` | 4           | 8          |
| `float`   | 4           | 4          |
| `double`  | 8           | 8          |
| `size_t`  | 4           | 8          |

這個差異是 64-bit 移植 bug 的最大來源：把 `pointer` 存進 `int` 就爆了。

---

## 24.3 暫存器角色分配

```
暫存器       ABI 名稱     用途                      儲存責任
---------    --------     ----------------------    --------
x0           zero         永遠是 0                  N/A
x1           ra           return address            caller-saved
x2           sp           stack pointer             callee-saved
x3           gp           global pointer            N/A
x4           tp           thread pointer            N/A
x5           t0           臨時                      caller-saved
x6           t1           臨時                      caller-saved
x7           t2           臨時                      caller-saved
x8           s0/fp        saved / frame pointer     callee-saved
x9           s1           saved                     callee-saved
x10          a0           arg 1 / return 1          caller-saved
x11          a1           arg 2 / return 2          caller-saved
x12          a2           arg 3                     caller-saved
x13          a3           arg 4                     caller-saved
x14          a4           arg 5                     caller-saved
x15          a5           arg 6                     caller-saved
x16          a6           arg 7                     caller-saved
x17          a7           arg 8                     caller-saved
x18–x27      s2–s11       saved                     callee-saved
x28–x31      t3–t6        臨時                      caller-saved
```

浮點暫存器（FP）：
```
f0–f7        ft0–ft7      臨時 FP                   caller-saved
f8–f9        fs0–fs1      saved FP                  callee-saved
f10–f11      fa0–fa1      FP arg 1-2 / return       caller-saved
f12–f17      fa2–fa7      FP arg 3-8                caller-saved
f18–f27      fs2–fs11     saved FP                  callee-saved
f28–f31      ft8–ft11     臨時 FP                   caller-saved
```

---

## 24.4 參數傳遞規則

**整數/指標型別：**

1. 前 8 個整數/指標參數用 a0–a7 傳遞
2. 超過 8 個的參數 spill 到 stack（呼叫前 push，被呼叫方從 `sp+0`、`sp+8`... 取）
3. 比 XLEN 小的值（如 `int`、`short`）：sign-extend 或 zero-extend 到 XLEN，再放進暫存器

**浮點型別：**

1. 前 8 個 float/double 參數用 fa0–fa7 傳遞
2. 如果 FP 暫存器用完了，overflow 的 FP 參數放整數暫存器（a0–a7），再不夠就 spill 到 stack

**Struct 傳遞：**

- 大小 ≤ 2×XLEN（16 bytes）且只含 integer 欄位：拆開放兩個整數暫存器
- 大小 ≤ XLEN（8 bytes）：放一個整數暫存器
- 大小 > 16 bytes：caller 在 stack 上配置空間，傳指標（hidden pointer）

---

## 24.5 Return Value 規則

| 返回型別         | 存放位置                           |
|---------------|----------------------------------|
| 整數/指標 ≤ 8B  | a0                               |
| 整數/指標 ≤ 16B | a0（低 64-bit）+ a1（高 64-bit）   |
| float/double  | fa0                              |
| struct ≤ 16B  | a0 + a1（拆開）                   |
| struct > 16B  | caller 傳 hidden pointer，函式填入  |

---

## 24.6 Stack Frame Layout

函式 prologue 建立 stack frame，epilogue 拆除：

```
高地址（呼叫前的 sp）
+-------------------+  ← caller 的 sp
|  arg 9 (若有)     |  +8*(n-8)
|  arg 10 (若有)    |
|  ...             |
+-------------------+  ← 被呼叫函式的 sp + frame_size
|  ra（return addr） |
|  s0 / fp          |
|  s1               |
|  s2               |
|  ...（callee-saved）|
|  局部變數          |
|  padding（16B對齊）|
+-------------------+  ← 被呼叫函式的 sp（sp 向下移動）
低地址
```

RISC-V ABI 要求 **sp 在函式呼叫點必須是 16-byte 對齊的**。

---

## 24.7 實際例子：10 個參數的函式

```c
long foo(long a, long b, long c, long d,
         long e, long f, long g, long h,
         long i, long j);
```

呼叫時：
- a–h：分別放 a0–a7
- i：spill 到 stack，`sp+0`
- j：spill 到 stack，`sp+8`

呼叫前的 stack 準備：

```asm
# caller 的組語（simplified）
addi  sp, sp, -16      # 替 arg 9, 10 保留 stack 空間
sd    a8_val, 0(sp)    # arg 9
sd    a9_val, 8(sp)    # arg 10
# 設定 a0–a7
li    a0, 1
li    a1, 2
# ...
li    a7, 8
call  foo
addi  sp, sp, 16       # 清理 stack
```

被呼叫函式的 prologue：

```asm
foo:
    addi sp, sp, -48        # frame size（ra + s0–s3 = 5 × 8 = 40，對齊到 48）
    sd   ra, 40(sp)         # 保存 return address
    sd   s0, 32(sp)         # 保存 callee-saved
    addi s0, sp, 48         # s0 = frame pointer（指向舊 sp）
    # 從 stack 取 arg 9, 10：
    ld   t0, 48(sp)         # arg 9 = i（在 caller 的 stack frame 裡）
    ld   t1, 56(sp)         # arg 10 = j
    ...
    ld   ra, 40(sp)         # 恢復 ra
    ld   s0, 32(sp)         # 恢復 s0
    addi sp, sp, 48         # 恢復 sp
    ret
```

---

## 24.8 為什麼要背 callee-saved

函式呼叫時，compiler 的策略：
- **用 t0–t6**：快，但每次 call 前要重新載入（caller 不保證）
- **用 s0–s11**：可以跨 call 存活，但要在 prologue 保存、epilogue 恢復

Callee-saved 的意思是：被呼叫的函式負責保存並還原這些暫存器。從 caller 的角度看，call 之後這些暫存器的值不會變。

這是一個合約。打破它就是 ABI bug，很難除錯。

---

## 自我檢核

- [ ] 能說出 LP64D 中 `long` 和 `int` 的大小差異
- [ ] 知道前 8 個整數參數用哪些暫存器（a0–a7）
- [ ] 能畫出一個有 3 個 callee-saved 暫存器的 stack frame
- [ ] 知道 `sp` 在 call site 要幾 byte 對齊（16 byte）
- [ ] 能說出第 9 個整數參數放在哪裡（stack sp+0）

→ [Ch 25 — Struct/Union Layout](25-struct-union-layout.md)
