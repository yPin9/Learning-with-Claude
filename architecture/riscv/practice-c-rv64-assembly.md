# 練習 C — RV64 Assembly 實戰

> 這五道題從 ABI 規則到 CSR 存取，逐步覆蓋 RV64 assembly 的核心操作。每道題先自己寫，再對答案。工具不會等你——先理解語意，再寫 code。

---

## 前置環境確認

```bash
# 需要的工具
riscv64-unknown-elf-gcc --version   # 或 riscv64-linux-gnu-gcc
riscv64-unknown-elf-objdump --version
qemu-system-riscv64 --version       # 若要執行 baremetal
# 或
qemu-riscv64 --version              # 若要執行 Linux ELF（需要 riscv64-linux-gnu-gcc）
```

---

## 題目 1：LP64D 呼叫慣例的 sum 函式

### 題目規格

用 RV64 assembly 實作以下 C 函式：

```c
// 計算 uint64_t 陣列的總和
// 參數遵循 LP64D 呼叫慣例
uint64_t sum(uint64_t *arr, int n);
```

**要求**：
- 用純 assembly（`.S` 檔案）實作，可讓 C main 呼叫
- 正確遵循 LP64D ABI：`arr` 在 a0，`n` 在 a1，返回值在 a0
- 不能破壞 callee-saved 暫存器（s0–s11）
- 在 `n <= 0` 時返回 0

**測試案例**：

```c
// test1.c
#include <stdint.h>
#include <stdio.h>

uint64_t sum(uint64_t *arr, int n);

int main() {
    uint64_t arr[] = {1, 2, 3, 4, 5, 0xFFFFFFFFULL, 0xFFFFFFFFFFFFFFFFULL};
    printf("sum(arr, 0) = %lu\n", sum(arr, 0));       // expected: 0
    printf("sum(arr, 3) = %lu\n", sum(arr, 3));       // expected: 6
    printf("sum(arr, 5) = %lu\n", sum(arr, 5));       // expected: 15
    printf("sum(arr, 6) = %lu\n", sum(arr, 6));       // expected: 15 + 0xFFFFFFFF = 4295032334
    printf("sum(arr, 7) = %lu\n", sum(arr, 7));       // expected: overflow
    return 0;
}
```

**編譯與執行**：

```bash
# 用 GNU Linux toolchain 編譯（可在 qemu-user 上跑）
riscv64-linux-gnu-gcc -O0 test1.c sum.S -o test1
qemu-riscv64 ./test1
```

**實作步驟提示**：
1. 函式入口不需要保存 ra（沒有再呼叫其他函式，leaf function）
2. 用 `blez` 或 `ble` 處理 `n <= 0` 的情況
3. 用 `ld` 載入 uint64_t，每次指標加 8
4. 用 `addiw` 還是 `addi` 遞增計數器？（`n` 是 `int`，用 `addiw`）

<details>
<summary>參考解答</summary>

```asm
# sum.S
    .globl sum
    .type  sum, @function

# uint64_t sum(uint64_t *arr, int n)
# a0 = arr（指標），a1 = n（int）
# 返回值：a0 = 總和
sum:
    # a1 是 int（32-bit），但在 64-bit 暫存器裡是 sign-extended
    # 用 blez 做 n <= 0 的檢查（64-bit signed 比較，正確因為 n 是 sign-extended）
    li    t0, 0              # sum = 0
    blez  a1, .done          # if n <= 0, return 0

.loop:
    ld    t1, 0(a0)          # t1 = *arr（載入 uint64_t）
    add   t0, t0, t1         # sum += *arr
    addi  a0, a0, 8          # arr++（每個 uint64_t 8 bytes）
    addiw a1, a1, -1         # n--（用 addiw 保持 32-bit 語意）
    bgtz  a1, .loop          # if n > 0, continue

.done:
    mv    a0, t0             # 返回值放 a0
    ret
```

</details>

---

## 題目 2：32-bit 環形計數器（W 後綴 vs 無 W 後綴對比）

### 題目規格

實作一個 32-bit 環形計數器（wrap around on overflow），分別用兩個版本：

```c
// 版本 A（正確）：32-bit 語意，溢出後回到 0（或負數）
int32_t counter_correct(int32_t val);

// 版本 B（錯誤）：64-bit 語意，溢出後變成大正數
int64_t counter_wrong(int64_t val);
```

每個函式：接收目前計數器值，加 1，返回新值。

**要求**：
- `counter_correct` 用 `addiw`，讓 `INT32_MAX + 1 = INT32_MIN`
- `counter_wrong` 用 `addi`，展示在 64-bit 暫存器上 0x7FFFFFFF + 1 不 wrap

**測試案例**：

```c
// 傳入 0x7FFFFFFF（int32 MAX），期望：
// counter_correct: 返回 0x80000000 = -2147483648（int32 wrap）
// counter_wrong:   返回 0x80000000 = 2147483648（64-bit 正數）

printf("correct: %lld\n", (long long)counter_correct(0x7FFFFFFF));  // -2147483648
printf("wrong:   %lld\n", (long long)counter_wrong(0x7FFFFFFF));    // 2147483648
```

<details>
<summary>參考解答</summary>

```asm
# counter.S
    .globl counter_correct
    .globl counter_wrong

# int32_t counter_correct(int32_t val)
# a0 = val（int32，sign-extended 到 64-bit）
counter_correct:
    addiw a0, a0, 1        # 對低 32-bit 做加法，結果 sign-extend 到 64-bit
    ret                    # 返回 sign-extended 的 32-bit 結果

# int64_t counter_wrong(int64_t val)
counter_wrong:
    addi  a0, a0, 1        # 對全 64-bit 做加法，不 wrap at 32-bit 邊界
    ret
```

```c
// 驗證：counter_correct(0x7FFFFFFF)
// addiw：0x7FFFFFFF + 1 = 0x80000000（32-bit），sext32 = 0xFFFFFFFF80000000
// 解釋為 int64_t：-2147483648（正確的 int32 overflow）

// counter_wrong(0x7FFFFFFF)
// addi：0x7FFFFFFF + 1 = 0x80000000（64-bit），沒有 sign-extend
// 解釋為 int64_t：+2147483648（64-bit 正數，語意錯誤）
```

</details>

---

## 題目 3：64-bit Linked List Insert

### 題目規格

用 `ld`/`sd` 實作一個 64-bit linked list 的 head insert：

```c
struct Node {
    uint64_t val;
    struct Node *next;   // 8-byte 指標（LP64）
};

// 把 new_node 插入到 *head 之前，成為新的 head
// 返回新的 head
struct Node *list_push_front(struct Node **head, struct Node *new_node);
```

**要求**：
- 純 assembly 實作
- 正確處理 `*head == NULL` 的情況
- 函式遵循 LP64D ABI

**注意**：`struct Node` 的 layout（LP64）：
- offset 0：`val`（uint64_t，8 bytes）
- offset 8：`next`（pointer，8 bytes）

<details>
<summary>參考解答</summary>

```asm
# list.S
    .globl list_push_front

# struct Node *list_push_front(struct Node **head, struct Node *new_node)
# a0 = head（指向 head pointer 的指標，struct Node **）
# a1 = new_node（新節點，struct Node *）
# 返回：a0 = 新的 head（= new_node）

list_push_front:
    ld   t0, 0(a0)          # t0 = *head（舊的 head pointer）
    sd   t0, 8(a1)          # new_node->next = old_head（offset 8 是 next）
    sd   a1, 0(a0)          # *head = new_node（更新 head pointer）
    mv   a0, a1             # 返回 new_node（新的 head）
    ret
```

</details>

---

## 題目 4：用 Inline Assembly 讀 cycle CSR

### 題目規格

用 C + inline assembly 實作：

```c
// 返回當前的 cycle counter 值（uint64_t）
uint64_t read_cycle(void);

// 返回當前的 instret（retired instructions）計數器
uint64_t read_instret(void);

// 測量 f() 執行了幾個 cycle
uint64_t measure_cycles(void (*f)(void));
```

**要求**：
- `read_cycle` 和 `read_instret` 用 `csrr` inline asm
- `measure_cycles` 在 f() 前後各讀一次 cycle，返回差值
- 在 Linux 使用者空間可執行（不需要特權）

**測試**：

```c
void busy_work(void) {
    volatile long sum = 0;
    for (int i = 0; i < 100000; i++) sum += i;
}

int main() {
    printf("busy_work 花了 %lu cycles\n", measure_cycles(busy_work));
    return 0;
}
```

<details>
<summary>參考解答</summary>

```c
// cycle_counter.c
#include <stdint.h>
#include <stdio.h>

static inline uint64_t read_cycle(void) {
    uint64_t val;
    __asm__ volatile ("csrr %0, cycle" : "=r"(val));
    return val;
}

static inline uint64_t read_instret(void) {
    uint64_t val;
    __asm__ volatile ("csrr %0, instret" : "=r"(val));
    return val;
}

uint64_t measure_cycles(void (*f)(void)) {
    uint64_t t0 = read_cycle();
    f();
    uint64_t t1 = read_cycle();
    return t1 - t0;
}

void busy_work(void) {
    volatile long sum = 0;
    for (int i = 0; i < 100000; i++) sum += i;
}

int main() {
    printf("busy_work: %lu cycles\n", measure_cycles(busy_work));
    printf("instret:   %lu\n", read_instret());
    return 0;
}
```

```bash
# 編譯
riscv64-linux-gnu-gcc -O0 cycle_counter.c -o cycle_counter
qemu-riscv64 ./cycle_counter
# 注意：qemu-riscv64 的 cycle 計數是模擬的，可能和真實硬體差很多
```

</details>

---

## 題目 5：RV64 最佳化 memcpy（8-byte 對齊版）

### 題目規格

實作一個 RV64 最佳化版的 `memcpy`，假設 src 和 dst 都是 8-byte 對齊：

```c
// 8-byte 對齊版本：一次複製 8 bytes
// 假設：dst 和 src 都是 8-byte 對齊，n 是 8 的倍數
void *memcpy_aligned8(void *dst, const void *src, size_t n);
```

**要求**：
- 每次循環用 `ld`/`sd` 複製 8 bytes
- 返回 dst（LP64D 呼叫慣例）
- 正確遵循 callee-saved 規則（本函式不需要 callee-saved，是 leaf function）

**進階（選做）**：
- 展開 4 次迴圈（loop unrolling），每次複製 32 bytes
- 加入 `n < 8` 的 fallback（逐 byte 複製）

**測試**：

```c
#include <string.h>
#include <stdio.h>
#include <stdint.h>
#include <assert.h>

void *memcpy_aligned8(void *dst, const void *src, size_t n);

int main() {
    uint64_t src[8] = {1,2,3,4,5,6,7,8};
    uint64_t dst[8] = {0};

    memcpy_aligned8(dst, src, 64);   // 8 * 8 bytes = 64 bytes

    for (int i = 0; i < 8; i++) {
        assert(dst[i] == src[i]);
        printf("dst[%d] = %lu\n", i, dst[i]);
    }
    printf("PASS\n");
    return 0;
}
```

<details>
<summary>參考解答</summary>

```asm
# memcpy_aligned8.S
    .globl memcpy_aligned8
    .type  memcpy_aligned8, @function

# void *memcpy_aligned8(void *dst, const void *src, size_t n)
# a0 = dst，a1 = src，a2 = n（bytes，必須是 8 的倍數）
# 返回值：a0 = dst（不改變 a0）

memcpy_aligned8:
    mv    t0, a0             # t0 = dst 備份（返回值用）
    beqz  a2, .done          # n == 0，直接返回

.loop:
    ld    t1, 0(a1)          # t1 = *src（64-bit load）
    sd    t1, 0(a0)          # *dst = t1（64-bit store）
    addi  a0, a0, 8          # dst += 8
    addi  a1, a1, 8          # src += 8
    addi  a2, a2, -8         # n -= 8
    bgtz  a2, .loop          # if n > 0, continue

.done:
    mv    a0, t0             # 返回原來的 dst
    ret
```

**進階版（4× unroll）**：

```asm
memcpy_aligned8_unroll:
    mv    t0, a0
    li    t5, 32             # unroll threshold
    blt   a2, t5, .tail      # n < 32，走 tail

.loop4:
    ld    t1, 0(a1)
    ld    t2, 8(a1)
    ld    t3, 16(a1)
    ld    t4, 24(a1)
    sd    t1, 0(a0)
    sd    t2, 8(a0)
    sd    t3, 16(a0)
    sd    t4, 24(a0)
    addi  a0, a0, 32
    addi  a1, a1, 32
    addi  a2, a2, -32
    bge   a2, t5, .loop4

.tail:
    beqz  a2, .done2
.tail_loop:
    ld    t1, 0(a1)
    sd    t1, 0(a0)
    addi  a0, a0, 8
    addi  a1, a1, 8
    addi  a2, a2, -8
    bgtz  a2, .tail_loop
.done2:
    mv    a0, t0
    ret
```

</details>

---

## 提交前自我檢查

- [ ] 題目 1：`sum` 在 `n=0` 時正確返回 0，在有 uint64_t 溢出的情況下正確 wrap
- [ ] 題目 2：能清楚說出 `addiw` 和 `addi` 在 0x7FFFFFFF + 1 時的差別
- [ ] 題目 3：`list_push_front` 在 `*head == NULL` 時也能正確執行（`ld` 讀到 NULL，`sd` 寫 NULL 到 new_node->next）
- [ ] 題目 4：`read_cycle` 的 inline asm 有 `volatile`（避免被 compiler 優化掉）
- [ ] 題目 5：`memcpy_aligned8` 的返回值是原來的 `dst`，不是修改後的指標

→ [練習 D — 手動建立 Sv48 頁表](practice-d-sv48-pagetable.md)
