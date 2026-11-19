# Ch 43 — 0x10 道嵌入式考題 II（0x09–0x10）

> **目標**：完成 Nigel Jones 考題的後八題：位元操作、絕對位址存取、ISR 設計、有號無號混用、字組長度、嵌入式動態配置、`typedef` 與 `#define`、maximal munch。每題都從「常見錯答」走到規格書條款，再延伸到裝置暫存器存取、中斷處理與 Linux 核心的真實漏洞。

> **對應原文**：[你所不知道的 C 語言：深究 0x10 道嵌入式系統考題篇](https://hackmd.io/@sysprog/c-0x10) 的「題目 0x09」到「題目 0x10」等節

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4、clang 14、gdb 12.1、glibc 2.35。Arm64 以 `aarch64-linux-gnu-gcc` + `qemu-aarch64` 執行；RV32 程式碼以 `riscv64-linux-gnu-gcc` 11.4（`-march=rv32i -mabi=ilp32`）編譯後用 `objdump` 檢查；大端序目標以 `clang --target=...` 只產生組合語言。**本機沒有 QEMU 系統模擬器（`qemu-system-riscv32`/`qemu-system-arm`）**，原文在裸機上執行的實驗本章改以「使用者空間模擬」或「只看編譯結果」驗證，會逐處標明。

## 為什麼需要這個？

前八題（[Ch 42](./42-embedded-quiz-part1.md)）考的是語言本身。後八題換了一個視角：**程式和硬體之間的那一層**。位元遮罩對應暫存器的欄位，整數轉指標對應裝置的實體位址，ISR 對應處理器的例外機制，型別寬度對應 CPU 的字組長度。

一般應用程式設計者可以把硬體當成「必要的麻煩」；Jones 說好的嵌入式工程師「critically aware of the underlying hardware and its limitations」。這一章要建立的正是這種意識——同一行 C，在 16 位元 MCU、32 位元 Cortex-M、64 位元 Linux 上可能有三種結果，你要能說出為什麼。

Jones 在題目 0x0D 之後寫道，表現不佳的應試者測驗到此結束，好的才進入最後三道附加題，那三題「看重解題方式而非答案」。所以 0x0E–0x10 我們特別著重推理過程。

## 先建立直覺

把一個 32 位元的裝置暫存器想成一排 32 個電燈開關，而你只能「一次讀整排」或「一次寫整排」：

```
  bit: 31                              8 7 6 5 4 3 2 1 0
      ┌────────────────────────────────┬─┬─┬─┬─┬─┬─┬─┬─┐
  REG │        其他欄位（別碰）          │ │ │ │ │█│ │ │ │   ← 想打開 bit 3
      └────────────────────────────────┴─┴─┴─┴─┴─┴─┴─┴─┘
  步驟：① 讀整排 → ② 在 CPU 裡改 bit 3 → ③ 寫回整排
                    ▲
                    └── ISR 若在 ①③ 之間改了 bit 7，③ 會把它蓋回去
```

這張圖幾乎貫穿本章：題目 0x09 的 `|=`、題目 0x0A 的 MMIO、題目 0x0B 的 ISR、甚至題目 0x09 位元欄位的 data race，全都是「讀、改、寫」這三步被打斷的問題。

| 題號 | 主題 | 回看本課 |
|---|---|---|
| 0x09 | 設定與清除 bit 3、位元欄位 | Ch 23、Ch 24、Ch 2 |
| 0x0A | 把 `0xaa55` 寫到位址 `0x67a9`（MMIO） | Ch 7、Ch 16 |
| 0x0B | 評論一個 ISR | Ch 11、Ch 35 |
| 0x0C | 有號與無號混用 | Ch 21、Ch 22 |
| 0x0D | 零的補數 | Ch 21、Ch 23 |
| 0x0E | 嵌入式動態配置、`malloc(0)` | Ch 17、練習 B |
| 0x0F | `typedef` vs `#define` | Ch 27、Ch 29 |
| 0x10 | `c = a+++b;` | Ch 27、Ch 34、Ch 38 |

---

## 題目 0x09：設定與清除第 3 個位元

> 給定整數變數 `a`，寫兩段程式碼：第一段把 bit 3 設為 1，第二段把 bit 3 清為 0，其餘位元不變。

Jones 把回答分三類：毫無頭緒（沒做過嵌入式）、用位元欄位、用 `#define` 遮罩。他對位元欄位評價極低，和 trigraph 並列為 C 最「brain-dead」的部分——他遇過一份 Infineon 晶片驅動程式用位元欄位，換到他的編譯器上欄位順序整個相反。

### 常見錯答

- `a |= 8; a &= 7;`——魔術數字，而且 `&= 7` 把 bit 3 以上全清了。
- `a ^= (1 << 3)`——這是**反轉**，不是設定。
- `#define BIT31 (1 << 31)`——`1` 是 `int`，把 1 移進符號位元，2^31 無法以 `int` 表示，是未定義行為（C11 6.5.7 §4）。UBSan 會抓到：

```
runtime error: left shift of 1 by 31 places cannot be represented in type 'int'
```

### 正解

```c
#include <stdio.h>

#define BIT3 (1U << 3)

static unsigned int toggle_bit(unsigned int a, unsigned int k) { return a ^ (1U << k); }
static unsigned int test_bit(unsigned int a, unsigned int k) { return (a >> k) & 1U; }
static unsigned int assign_bit(unsigned int a, unsigned int k, unsigned int b)
{
    return (a & ~(1U << k)) | ((b & 1U) << k);
}

int main(void)
{
    unsigned int a = 0x51;           /* 0101 0001 */
    a |= BIT3;                       /* set bit 3 */
    printf("set:    0x%02x\n", a);
    a &= ~BIT3;                      /* clear bit 3 */
    printf("clear:  0x%02x\n", a);
    printf("toggle: 0x%02x, test bit 4: %u, assign bit 0 := 0: 0x%02x\n",
           toggle_bit(a, 7), test_bit(a, 4), assign_bit(a, 0, 0));
    return 0;
}
```

```
set:    0x59
clear:  0x51
toggle: 0xd1, test bit 4: 1, assign bit 0 := 0: 0x50
```

`x | 1 = 1`、`x | 0 = x`：OR 只影響遮罩為 1 的位元。`~BIT3` 是只有 bit 3 為 0 的遮罩，`x & 0 = 0`、`x & 1 = x`：AND 只清 bit 3。

### 規格依據

- 移位量 ≥ 左運算元寬度是 UB（C11 6.5.7 §3），所以上面的函式要求 `k < 32`。
- 有號左移若結果無法表示是 UB（C11 6.5.7 §4）；C23 規定只用二補數，但**這條規則沒變**（C23 6.5.8 §4）。遮罩一律用無號常數產生。

> 位移語意與遮罩技巧回看 [Ch 23 — bitwise 基礎](./23-bitwise-basics.md)。

**遮罩的寬度也要和目標一致**，這是 64 位元暫存器上常見的錯：

```c
uint64_t reg = UINT64_MAX;
reg &= ~(1U << 3);                 /* 遮罩是 32 位元 */
printf("%016" PRIx64 "\n", reg);
reg = UINT64_MAX;
reg &= ~(UINT64_C(1) << 3);        /* 遮罩是 64 位元 */
printf("%016" PRIx64 "\n", reg);
```

```
00000000fffffff7
fffffffffffffff7
```

`~(1U << 3)` 是 32 位元的 `0xfffffff7`，轉成 `uint64_t` 時高位補 0，高 32 位元被一併清掉——而 `-Wall -Wextra` 零警告。Linux 核心 `include/vdso/bits.h` 用 `#define BIT(nr) (UL(1) << (nr))` 產生與 `long` 等寬的遮罩，64 位元暫存器另用 `BIT_ULL()`。

### 延伸：位元欄位為什麼不可移植

位元欄位在儲存單位中從高位或低位開始配置，由實作定義（C11 6.7.2.1 §11），實務上由各平台 ABI 決定：

```c
struct bits {
    unsigned int bit_0 : 1;
    unsigned int bit_1 : 1;
    unsigned int bit_2 : 1;
    unsigned int bit_3 : 1;
    unsigned int others : 28;
};
union reg {
    struct bits b;
    unsigned int word;
};
unsigned int set_bit3(void)
{
    union reg r = {.word = 0};
    r.b.bit_3 = 1;
    return r.word;
}
```

用 clang 14 `-O2` 對四個目標產生組合語言：

```
x86_64-linux-gnu:      movl $8, %eax                 → 8
aarch64-linux-gnu:     mov  w0, #8                   → 8
powerpc-linux-gnu:     lis  3, 4096                  → 4096 << 16 = 0x10000000
aarch64_be-linux-gnu:  mov  w0, #268435456           → 0x10000000
```

小端序 ABI 把第一個欄位放最低位，`bit_3` 就是 bit 3；大端序 ABI 把第一個欄位放最高位，`bit_3` 實際對應 bit 28。這正是 Linux 核心 `struct iphdr` 要用 `#if defined(__LITTLE_ENDIAN_BITFIELD)` 分兩種順序排欄位的原因（題目 0x03 的 `#error` 守在 `#else`）。這裡透過 `union` 讀另一個成員，C11 6.5.2.3 註腳 95 說明結果是「以新型別重新解讀同一段位元組」，合法；若改用指標轉型存取，就要另外檢查有效型別規則（C11 6.5 §7），見 [Ch 34](./34-undefined-behavior.md)。

位元欄位還有兩個坑。**其一**，宣告為 `int` 的位元欄位是否有號由實作定義（C11 6.7.2 §5），1 位元有號欄位只能表示 0 與 −1：

```c
struct flags { signed int s : 1; int i : 1; unsigned int u : 1; };
struct flags f = {.s = -1, .i = -1, .u = 1};
printf("s = %d, i = %d, u = %u\n", f.s, f.i, f.u);
```

gcc、clang、aarch64 gcc 都印 `s = -1, i = -1, u = 1`——`int` 位元欄位被當有號。旗標要宣告成 `unsigned int` 或 `bool`。

**其二**，相鄰的位元欄位共用同一個**記憶體位置**（memory location，C11 3.14）。兩個執行緒各改一個欄位，在 C11 中是 data race；換成獨立成員就是不同的記憶體位置：

```c
#include <pthread.h>
#include <stdio.h>

#define N 30000

#ifdef BITFIELD
static struct { unsigned int a : 15, b : 15; } s;   /* one memory location */
#else
static struct { unsigned short a, b; } s;           /* two memory locations */
#endif

static pthread_barrier_t go;

static void *inc_a(void *arg)
{
    pthread_barrier_wait(&go);  /* start both threads at the same time */
    for (int i = 0; i < N; i++)
        s.a++;
    return arg;
}

static void *inc_b(void *arg)
{
    pthread_barrier_wait(&go);
    for (int i = 0; i < N; i++)
        s.b++;
    return arg;
}

int main(void)
{
    pthread_t t1, t2;
    pthread_barrier_init(&go, NULL, 2);
    pthread_create(&t1, NULL, inc_a, NULL);
    pthread_create(&t2, NULL, inc_b, NULL);
    pthread_join(t1, NULL);
    pthread_join(t2, NULL);
    printf("a = %u, b = %u\n", (unsigned) s.a, (unsigned) s.b);
    return 0;
}
```

```
$ gcc -O0 -pthread bfrace.c               # 三次執行
a = 30000, b = 30000 | a = 30000, b = 30000 | a = 30000, b = 30000
$ gcc -O0 -pthread -DBITFIELD bfrace.c
a = 25723, b = 30014 | a = 29312, b = 30003 | a = 30000, b = 29998
$ clang -O0 -pthread -DBITFIELD bfrace.c
a = 18539, b = 24294 | a = 28301, b = 29558 | a = 29967, b = 30000
```

獨立成員永遠是 30000；位元欄位版本遺失更新。仔細看 gcc 那行：`b = 30014`，**比執行次數還多**！看組合語言就懂了：

```
inc_a 的寫回（gcc -O0）:              inc_b 的寫回:
  movzwl s(%rip), %eax                  movl  s(%rip), %eax
  andw   $-32768, %ax                   andl  $-1073709057, %eax
  orl    %edx, %eax                     orl   %edx, %eax
  movw   %ax, s(%rip)    ← 16 位元寫入   movl  %eax, s(%rip)   ← 32 位元寫入
```

`a` 佔 bit 0–14，gcc 用 16 位元的 `movw` 寫回 bit 0–15——而 **bit 15 是 `b` 的最低位**。執行緒 A 讀到舊的 `b` 最低位、再寫回去，可能把 `b` 從偶數「改回」奇數，`b` 就多了 1。兩個欄位共用一個記憶體位置，編譯器可以用任何寬度存取它；這段程式本身就是 data race，任何結果都合乎標準。單核 MCU 上主程式與 ISR 各改同一字組裡的不同旗標，也是同一個問題。

### 延伸：位元欄位仍有它的用途

Jones 的批評針對「用位元欄位描述**外部規定**的位元配置」。程式內部的緊湊資料結構（配置不離開程式，ABI 差異無關緊要）、鎖定單一 ABI 的暫存器定義，位元欄位反而比手寫遮罩不易出錯；Linux 核心 `struct sk_buff` 就有一長串 `__u8 cloned:1, ...`。另一個巧妙用途是**編譯時期檢查**：寬度必須是非負常數，負寬度會編譯失敗：

```c
#define BUILD_BUG_ON_ZERO(e) ((int) sizeof(struct { int : (-!!(e)); }))
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]) + BUILD_BUG_ON_ZERO(0))

static int table[8];
int n = ARRAY_SIZE(table);
#ifdef BAD
int m = BUILD_BUG_ON_ZERO(sizeof(int) == 4);
#endif
```

```
gcc -DBAD:   error: negative width in bit-field ‘<anonymous>’
clang -DBAD: error: anonymous bit-field has negative width (-1)
```

`-!!(e)`：`!!` 把任何非零值變 1，再取負得 −1。條件不成立時這個結構只有一個寬度 0 的匿名欄位，在 ISO C 中是 UB（C11 6.7.2.1 §8，沒有具名成員），GCC/Clang 以擴充把大小定為 0——所以這招依賴 GNU C。它的優點是能放進運算式（`_Static_assert` 是宣告，放不進去）。2025 年核心改用 `sizeof(struct { _Static_assert(!(e), msg); })`，錯誤訊息變成可讀字串。

---

## 題目 0x0A：把整數 `0xaa55` 寫到絕對位址 `0x67a9`

> 某專案要求把絕對位址 `0x67a9` 的整數變數設為 `0xaa55`，編譯器是純 ANSI 編譯器。寫出程式碼。

題目刻意限定「pure ANSI compiler」，排除 IAR 的 `@` 運算子、Keil C51 的 `_at_` 這類位址擴充，要求只用標準 C。

### 常見錯答

- 「標準 C 不能存取絕對位址」——錯，整數可以轉成指標。
- 宣告一個全域變數再用連結器腳本擺到 `0x67a9`——那是連結器的事，不是「純 C」。

### Jones 的答案與它的問題

```c
int *ptr;
ptr = (int *) 0x67a9;
*ptr = 0xaa55;
/* 或 */
*(int * const)(0x67a9) = 0xaa55;
```

gcc 11 `-O2` 把兩種寫法編成同一道指令：

```
jones1:
	mov	DWORD PTR ds:26537, 43605
	ret
jones2:
	mov	DWORD PTR ds:26537, 43605
	ret
```

（26537 = 0x67a9，43605 = 0xaa55。）這個答案有五處可議：

1. **整數轉指標的結果由實作定義**（C11 6.3.2.3 §5）。嵌入式編譯器通常直接對映到實體位址，那是平台的承諾，不是標準的保證。
2. **`0x67a9` 是奇數**，對 `int` 沒有對齊。Cortex-M0 這類不支援非對齊存取的處理器會 HardFault（回看 [Ch 16](./16-alignment-and-padding.md)）。
3. **16 位元 `int` 上 `0xaa55`（43605）大於 `INT_MAX`**，它的型別是 `unsigned int`，存進 `int` 超出範圍，結果由實作定義或引發實作定義的訊號（C11 6.3.1.3 §3）。
4. **第二種寫法的 `const` 沒作用**。轉型產生的不是左值，轉型為限定型別等同轉型為非限定版本（C11 6.5.4 註腳 104），上面的組合語言完全一樣就是證據。
5. **沒有 `volatile`**。如果那是裝置暫存器，寫入可能被合併或刪除。

第 5 點最致命，實測一下：

```c
#define REG  (*(unsigned int *) 0x40001000u)
#define VREG (*(volatile unsigned int *) 0x40001000u)

void plain_twice(void)    { REG = 1;  REG = 2; }
void volatile_twice(void) { VREG = 1; VREG = 2; }
```

```
plain_twice:
	mov	DWORD PTR ds:1073745920, 2       ← 第一次寫入被刪了
	ret
volatile_twice:
	mov	DWORD PTR ds:1073745920, 1
	mov	DWORD PTR ds:1073745920, 2
	ret
```

對一般記憶體，`REG = 1` 是死存（dead store），刪掉完全正確。但如果這是 UART 的資料暫存器，你要送的兩個位元組只剩一個。

### 正解

標出寬度與 `volatile`，用巨集集中定義位址——CMSIS 等廠商標頭檔都是這種形式：

```c
#include <stdint.h>
#define UART_STATUS (*(volatile uint32_t *) 0x40001000u)
```

回到題目本身，較嚴謹的寫法是 `*(volatile uint16_t *) 0x67a8 = 0xaa55u;`（改成對齊的位址、寬度明確），並在面試時說明為什麼這樣改。

一個新版 GCC 才有的坑：GCC 12 起把位址小於 4096 的常數指標當成「指向大小為 0 的物件」：

```
$ gcc-11 -O2 -Wall -c low.c            # *(volatile int *) 0x100 = 1;
（無警告）
$ docker run gcc:14 ... gcc -O2 -Wall -c low.c
warning: array subscript 0 is outside array bounds of 'volatile int[0]' [-Warray-bounds=]
$ ... gcc -O2 -Wall --param=min-pagesize=0 -c low.c
（無警告）
```

周邊位址落在低位的平台（AVR、MSP430）要加 `--param=min-pagesize=0`；`0x67a9` 大於 4096，不受影響。

### 延伸：在使用者空間模擬一個裝置

本機沒有 QEMU 系統模擬器，但我們可以在使用者空間重現 MMIO 的本質：把一頁記憶體 `mmap` 到固定位址當「暫存器區塊」，再用另一個執行緒扮演「硬體」，100 ms 後填資料並設 READY 位元：

```c
/* Simulate a memory-mapped device in user space:
 * a page mapped at a fixed address plays the role of the register block,
 * and a second thread plays the role of the hardware. */
#define _GNU_SOURCE
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/mman.h>
#include <unistd.h>

#define DEV_BASE   0x10000000u          /* "physical" address of the device */
#define REG_STATUS 0x0                   /* offset of the status register */
#define REG_DATA   0x4                   /* offset of the data register */
#define ST_READY   (1u << 0)             /* bit 0: data ready */

#ifdef NO_VOLATILE
#define REG32(off) (*(uint32_t *) (uintptr_t) (DEV_BASE + (off)))
#else
#define REG32(off) (*(volatile uint32_t *) (uintptr_t) (DEV_BASE + (off)))
#endif

static void *hardware(void *arg)
{
    volatile uint32_t *regs = (volatile uint32_t *) (uintptr_t) DEV_BASE;
    usleep(100 * 1000);                  /* the device needs 100 ms */
    regs[REG_DATA / 4] = 0xaa55;
    __atomic_thread_fence(__ATOMIC_RELEASE);
    regs[REG_STATUS / 4] |= ST_READY;
    return arg;
}

int main(void)
{
    void *p = mmap((void *) (uintptr_t) DEV_BASE, 4096, PROT_READ | PROT_WRITE,
                   MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
    if (p == MAP_FAILED) {
        perror("mmap");
        return 1;
    }
    pthread_t hw;
    pthread_create(&hw, NULL, hardware, NULL);
    alarm(2);                            /* give up after 2 s */
    unsigned long polls = 0;
    while (!(REG32(REG_STATUS) & ST_READY))
        polls++;
    printf("ready after %lu polls, data = 0x%04x\n", polls,
           (unsigned) REG32(REG_DATA));
    pthread_join(hw, NULL);
    return 0;
}
```

```
$ gcc -O2 -Wall -Wextra -pthread fakedev.c -o fakedev && ./fakedev
ready after 411302004 polls, data = 0xaa55
$ gcc -O2 -pthread -DNO_VOLATILE fakedev.c -o fakedev && ./fakedev
Alarm clock                                   (rc=142，2 秒後被 SIGALRM 終止)
```

沒有 `volatile` 的版本，迴圈被編成題目 0x04 見過的樣子（讀一次，然後 `.L8: jmp .L8`），硬體設了 READY 也看不到。輪詢次數（約四億次）每次執行不同。這個模擬的限制要說清楚：真正的 MMIO 存取有副作用（讀狀態暫存器可能清除旗標），而這裡是普通記憶體；兩個執行緒共享非 atomic 物件嚴格說是 data race。它示範的是**編譯器層次**的效果，不是裝置語意。

### 延伸：真實驅動程式長什麼樣（OS in 1,000 Lines）

Seiya Nuta 的〈Operating System in 1,000 Lines〉在 QEMU RISC-V `virt` 機器上寫 virtio-blk 驅動，把 Jones 的轉型包成存取函式：

```c
uint32_t virtio_reg_read32(unsigned offset) {
    return *((volatile uint32_t *) (VIRTIO_BLK_PADDR + offset));
}
void virtio_reg_write32(unsigned offset, uint32_t value) {
    *((volatile uint32_t *) (VIRTIO_BLK_PADDR + offset)) = value;
}
void virtio_reg_fetch_and_or32(unsigned offset, uint32_t value) {
    virtio_reg_write32(offset, virtio_reg_read32(offset) | value);
}
```

（節錄自該書 Disk I/O 一章。）和考題比，它補上了：寬度與 `volatile` 明確；初始化時先檢查 magic `0x74726976`（小端序的 "virt"）、版本、device ID，不符就 `PANIC`；`fetch_and_or32` 是讀改寫，單執行緒初始化沒問題，有 ISR 就要保護；放進 virtqueue 後先 `__sync_synchronize()` 再寫 `QUEUE_NOTIFY`——Ch 42 題目 0x08 說的裝置層次排序；請求結構用 `__attribute__((packed))`，因為配置由 virtio 規格決定。

我們無法執行原文的裸機程式，但可以驗證關鍵的一點——**存取寬度**。用 RV32 交叉編譯原文的 UART/virtio 存取函式：

```c
static inline uint8_t mmio_read8(uintptr_t a) { return *(volatile uint8_t *) a; }
static inline void mmio_write8(uintptr_t a, uint8_t v) { *(volatile uint8_t *) a = v; }
static inline uint32_t mmio_read32(uintptr_t a) { return *(volatile uint32_t *) a; }

void uart_putc(char c)
{
    while (!(mmio_read8(UART0 + UART_LSR) & LSR_THRE))
        ;
    mmio_write8(UART0, (uint8_t) c);
}
uint32_t virtio_magic(void) { return mmio_read32(VIRTIO0 + 0x00); }
```

```
$ riscv64-linux-gnu-gcc -march=rv32i -mabi=ilp32 -O2 -ffreestanding -nostdlib -c mmio_rv.c
$ riscv64-linux-gnu-objdump -d --no-show-raw-insn mmio_rv.o
00000000 <uart_putc>:
   0:	lui	a4,0x10000
   4:	lbu	a5,5(a4)        ← 8 位元讀 LSR (0x10000005)
   8:	andi	a5,a5,32      ← 0x20 = THRE
   c:	beqz	a5,4          ← 每圈都重讀
  10:	sb	a0,0(a4)        ← 8 位元寫 THR
  14:	ret
00000018 <virtio_magic>:
  18:	lui	a5,0x10001
  1c:	lw	a0,0(a5)        ← 32 位元讀 magic
  20:	ret
```

每個存取都是一道寬度相符的載入/儲存，輪詢迴圈每圈重讀。原文在 QEMU 上執行得到 `magic = 0x74726976`、`version = 0x00000001`、未掛裝置時 `device id = 0x00000000`（**本段執行結果引用原文，本機未實測**；要驗證需安裝 `qemu-system-misc` 與 `clang`/`lld` 的 RISC-V 目標）。

考題假設程式直接用實體位址，這在沒有 MMU 的 MCU 上成立；作業系統開啟分頁後必須先建立映射——該書用 `map_page()` 把這頁恆等映射，Linux 核心用 `ioremap()` 再透過 `readl()`/`writel()` 存取。

---

## 題目 0x0B：評論這個 ISR

> 許多編譯器廠商為標準 C 加入中斷擴充，常見關鍵字是 `__interrupt`。評論以下 ISR：
> ```c
> __interrupt double compute_area(double radius)
> {
>     double area = PI * radius * radius;
>     printf("\nArea = %f", area);
>     return area;
> }
> ```

Jones：不知道 ISR 不能傳回值、不能接受參數，「you aren't hired」；浮點運算與 `printf()` 兩點答不出不苛責，答得出大大加分。

### 常見錯答

- 只挑 `PI` 沒定義——那不是重點。
- 「把 `printf` 改成 `puts` 就好」——仍然不可重入。

### 正解：四個問題

1. **不能傳回值**：ISR 由硬體觸發，沒有呼叫端接收傳回值。
2. **不能接受參數**：也沒有呼叫端傳參數。TI 編譯器手冊明定 `__interrupt` 只能用於傳回 `void` 且無參數的函式。
3. **浮點運算**：許多處理器的浮點單元不可重入，或要求 ISR 額外保存浮點暫存器，有些平台根本禁止。Linux 核心 x86 預設不得用 FPU，必須包在 `kernel_fpu_begin()`/`kernel_fpu_end()` 中，中斷中使用前還要 `irq_fpu_usable()` 確認。
4. **`printf()`**：執行時間長，內部可能取 lock、用共享緩衝區，不可重入；POSIX 也沒有把它列入 async-signal-safe 函式。

### 規格依據：ISR 的形式由架構決定

標準 C 沒有「中斷」這個概念，ISR 的形式完全由架構與編譯器擴充決定。三個例子：

- **Arm Cortex-M**：進入例外時**硬體**把 r0–r3、r12、lr、pc、xPSR 推入堆疊，lr 填特殊的 `EXC_RETURN` 值，函式用一般的 `bx lr` 返回就觸發硬體還原。所以 Cortex-M 的 ISR 是普通 C 函式，不需擴充。
- **x86**：GCC 的 `interrupt` 屬性**要求**一個指向 `struct interrupt_frame` 的參數，和「ISR 沒有參數」正好相反：

```c
struct interrupt_frame;
volatile unsigned int ticks;

__attribute__((interrupt)) void timer_isr(struct interrupt_frame *frame)
{
    (void) frame;
    ticks++;
}
```

```
$ gcc -O2 -mgeneral-regs-only -S isr_x86.c
timer_isr:
	pushq	%rax              ← 只保存用到的暫存器
	movl	ticks(%rip), %eax
	addl	$1, %eax
	movl	%eax, ticks(%rip)
	popq	%rax
	iretq                   ← 中斷返回，不是 ret
```

寫成無參數版本，gcc 直接拒絕：`error: interrupt service routine can only have a pointer argument and an optional integer argument`。`-mgeneral-regs-only` 禁止使用 SSE/x87 暫存器——正是第 3 點「ISR 裡別碰浮點」的編譯器層次落實。

- **RISC-V**：GCC 的 `interrupt("machine")` 屬性讓編譯器產生保存暫存器的程式碼與 `mret`。

### 延伸：ISR 呼叫外部函式的代價

編譯器只保存 ISR **實際用到**的暫存器——前提是 ISR 不呼叫其他函式。用 RV32 驗證：

```c
volatile uint32_t ticks;
static uint32_t deadline;
void log_tick(void);

__attribute__((interrupt("machine"), aligned(4))) void trap_handler(void)
{
    ticks++;
    deadline += TICK_CYCLES;
    *(volatile uint32_t *) CLINT_MTIMECMP = deadline;
#ifdef CALL_EXTERN
    log_tick();
#endif
}
```

```
$ riscv64-linux-gnu-gcc -march=rv32im_zicsr -mabi=ilp32 -O2 -ffreestanding -nostdlib -c isr_rv.c
== 不呼叫外部函式：保存 4 個暫存器，結尾 mret
== -DCALL_EXTERN：  保存 16 個暫存器，結尾 mret
```

加一個外部呼叫，被呼叫端可能用到任何 caller-saved 暫存器，ISR 只好全部保存（原文用 GCC 13 / Clang 20 得到 6 與 17 個，數字因版本而異，趨勢相同）。這是 ISR 裡不宜呼叫 `printf()` 的**另一個理由**：每次中斷都要付保存全部 caller-saved 暫存器的代價，延遲跟著增加。`aligned(4)` 是因為 `mtvec` 的最低兩個位元是模式欄位。

### 延伸：上半部與下半部

ISR 的設計原則是**短、快、不阻塞**，耗時的處理延後執行：

```
   周邊裝置發出中斷
          │ 硬體觸發
          ▼
   ┌──────────────────────────┐
   │ 上半部 (ISR)              │  讀並清除狀態暫存器、保存少量資料
   └──────────────────────────┘
          │ raise_softirq()                │ queue_work() / 喚醒執行緒 /
          ▼                                ▼ xSemaphoreGiveFromISR()
   ┌──────────────────────┐        ┌───────────────────────────────┐
   │ softirq / tasklet     │        │ workqueue、threaded IRQ、RTOS task │
   │ 不可睡眠              │        │ 可被排程、可等 lock、做耗時處理     │
   └──────────────────────┘        └───────────────────────────────┘
```

ISR 不能等 mutex：它不是可以被排程器暫停的 task。FreeRTOS 的 `xSemaphoreGiveFromISR()` 只釋放不等待，用來喚醒處理後續工作的 task（官方稱 deferred interrupt handling）。Linux v2.6.30 的 `request_threaded_irq()` 讓中斷處理主體在可排程的核心執行緒中執行，這也是 PREEMPT_RT 降低延遲的基礎。

原文用 RISC-V CLINT 計時器每 10 ms 中斷一次、主迴圈 `wfi` 等待、印出 `tick 1` 到 `tick 5`（本機無 `qemu-system-riscv32`，**未實測**）。它的結構可以在使用者空間用 signal 重現——`SIGALRM` 扮演計時器中斷、`pause()` 扮演 `wfi`、handler 只遞增計數器，印字留給主迴圈：

```c
/* User-space analogue of a timer ISR + deferred work:
 * SIGALRM plays the timer interrupt, pause() plays wfi. */
#define _POSIX_C_SOURCE 200809L
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/time.h>
#include <unistd.h>

static volatile sig_atomic_t ticks;     /* written only by the "ISR" */

static void on_alarm(int sig)
{
    (void) sig;
    ticks++;                             /* short: no printf, no malloc */
}

int main(void)
{
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_alarm;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGALRM, &sa, NULL);

    struct itimerval it = {.it_interval = {0, 10000}, .it_value = {0, 10000}};
    setitimer(ITIMER_REAL, &it, NULL);   /* every 10 ms */

    sig_atomic_t seen = 0;
    while (seen < 5) {
        pause();                         /* sleep until the next "interrupt" */
        if (ticks != seen) {
            seen = ticks;
            printf("tick %d\n", (int) seen);  /* deferred work, outside the handler */
        }
    }
    return 0;
}
```

```
$ gcc -Wall -Wextra -O2 tick_sig.c -o tick_sig && ./tick_sig
tick 1
tick 2
tick 3
tick 4
tick 5
```

`volatile sig_atomic_t` 是 C 標準唯一保證 signal handler 可以安全寫入的型別（C11 7.14.1.1 §5）。這段程式藏著 Ch 42 題目 0x04 提過的**遺失喚醒**：signal 若在 `if (ticks != seen)` 檢查之後、`pause()` 之前到達，`pause()` 會多睡一個週期。週期性計時器下只是晚 10 ms，一次性事件就可能永遠睡下去。正確做法是先用 `sigprocmask` 擋住 signal、檢查條件，再用 `sigsuspend()` 原子地「解除遮蔽並睡眠」——和 Cortex-M「關中斷檢查再 `__WFI()`」是同一個模式。

> signal 與 async-signal-safe，回看 [Ch 35 — Stream I/O、EOF 與 signal](./35-stream-io-and-signals.md)。

---

## 題目 0x0C：有號與無號混用

> 以下程式碼的輸出為何？
> ```c
> void foo(void)
> {
>     unsigned int a = 6;
>     int b = -20;
>     (a + b > 6) ? puts("> 6") : puts("<= 6");
> }
> ```

Jones 說答錯的人「perilously close to not getting the job」。

### 常見錯答

「6 + (−20) = −14，印 `<= 6`」。

### 正解

印 `> 6`。

```
> 6
a + b = 4294967282
```

### 規格依據

Jones 稱這題考「integer promotion rules」，嚴格說觸發的是**一般算術轉換**（usual arithmetic conversions，C11 6.3.1.8），整數提升只是第一步：

```
  兩個運算元各自做整數提升（char、short、位元欄位 → int 或 unsigned int）
          │
     型別相同？ ──是──► 不再轉換
          │否
  同為有號或同為無號？ ──是──► 轉成 rank 較高者
          │否
  無號型別的 rank ≥ 有號型別？ ──是──► 有號 → 無號      ← 本題：int 與 unsigned int 同 rank
          │否                                       -20 → 4294967276
  有號型別能表示無號型別的所有值？ ──是──► 無號 → 有號（LP64：unsigned int + long → long）
          │否
  兩者轉成「有號型別對應的無號型別」
```

`unsigned int` 與 `int` 同 rank，有號轉無號：−20 依模 2^32 取值得 4294967276（C11 6.3.1.3 §2），加 6 遠大於 6。

**同一行程式碼的結果取決於型別寬度**——這才是 Jones 說它「對嵌入式非常重要」的原因。把 `a` 改成 `unsigned short`：

```
ushort: <= 6
```

32 位元 `int` 能表示所有 `unsigned short` 值，`a` 先提升成 `int`，全程有號，結果 −14。但在 `int` 與 `short` 同為 16 位元的 MCU 上，`unsigned short` 提升成 `unsigned int`（C11 6.3.1.1 §2），結果又回到 `> 6`（依規格推得，本機無 16 位元目標）。

比較運算也一樣，而且常和 `sizeof` 一起出現：

```c
int my_arr[] = {21, 22, 23, 24, 25, 26};
#define MY_ARRAY_SIZE (sizeof(my_arr) / sizeof(my_arr[0]))
int d = -1;
puts(d <= MY_ARRAY_SIZE ? "d <= size" : "d > size");   /* 印 d > size */
```

`sizeof` 的型別是無號的 `size_t`，`d` 轉成 `SIZE_MAX`。gcc/clang 在 C 模式要 `-Wextra` 才有 `-Wsign-compare`。遞減迴圈是常見形式：

```c
unsigned int a = 0;
for (int i = 9; i >= a; i--)
    b[i] = 0;
```

加上保護計數後執行：

```
would write b[-1]
would write b[-2]
still running, i = -3
```

`i >= a` 中 `i` 轉無號，遞減到 −1 成為 `UINT_MAX`，條件永遠成立，迴圈寫出陣列邊界。

> 二補數與轉換的位元層次，回看 [Ch 21](./21-number-systems.md) 與 [Ch 22](./22-integer-overflow.md)。

### 延伸：混用的代價是記憶體安全漏洞

同一條規則用在長度上就是漏洞。最常見的形式：有號長度通過只檢查上限的比較，再被轉成 `size_t` 傳給 `memcpy`：

```c
#include <stdio.h>
#include <string.h>

#define MAX_PAYLOAD 64

static char packet[256];

/* len comes from the wire, e.g. a 16-bit field sign-extended by mistake */
static int copy_payload(char *dst, const char *src, int len)
{
    if (len > MAX_PAYLOAD) /* rejects 65..INT_MAX, but not negatives */
        return -1;
    memcpy(dst, src, len); /* len converted to size_t */
    return 0;
}

int main(void)
{
    char buf[MAX_PAYLOAD];
    short wire_len = (short) 0xFFFF; /* attacker-controlled field */
    printf("len = %d, as size_t = %zu\n", wire_len, (size_t) (int) wire_len);
    return copy_payload(buf, packet, wire_len);
}
```

```
gcc  -Wall -Wextra: 0 warnings   → Segmentation fault (rc=139)
clang -Wall -Wextra: 0 warnings  → Segmentation fault (rc=139)
gcc -Wsign-conversion:
  warning: conversion to ‘size_t’ {aka ‘long unsigned int’} from ‘int’ may change the sign of the result
gcc -fsanitize=address -U_FORTIFY_SOURCE:
  ERROR: AddressSanitizer: negative-size-param: (size=-1)
clang -fsanitize=implicit-conversion:
  runtime error: implicit conversion from type 'int' of value -1 (32-bit, signed) to type
  'unsigned long' changed the value to 18446744073709551615 (64-bit, unsigned)
```

（終端機上會先看到 `len = -1, as size_t = 18446744073709551615`；輸出被導向管線時 stdout 是全緩衝，程式崩潰前來不及 flush，那一行就不見了——又一個 [Ch 35](./35-stream-io-and-signals.md) 的細節。）

`0xFFFF` 被當成有號讀入變 −1，通過 `len > 64` 的檢查，`memcpy` 收到 18446744073709551615。`-Wall -Wextra` 全部沉默。Linux 核心有兩個真實案例：

- **CVE-2013-2094**：`perf_swevent_init()` 用 `int event_id = event->attr.config;` 接收 64 位元的 `config`，截斷後可能為負，有號比較 `event_id >= PERF_COUNT_SW_MAX` 擋不住負值，接著以負索引遞增陣列前方的記憶體——本機提權。修正只是把 `int` 改成 `u64`（Linux v3.8.9）。
- **CVE-2016-9793**：`sock_setsockopt()` 寫 `max_t(u32, val * 2, SOCK_MIN_SNDBUF)`，負的 `val` 在 `u32` 比較中變極大值而勝出，存回 `int` 後又變負的緩衝區大小。`max_t` 本意是避免型別混用，這裡卻指定了錯的型別，所以**沒有任何警告**。修正改成 `max_t(int, ...)`（Linux v4.8.14）。

共同點：**檢查時以一種型別解讀，使用時以另一種型別解讀**。防禦：長度與索引一開始就用 `size_t` 或固定寬度無號型別，在信任邊界同時檢查上下限；開 `-Wsign-compare -Wsign-conversion`；測試時開 ASan 與 `-fsanitize=implicit-conversion`。Ch 42 題目 0x02 核心的 `min()` 用 `static_assert` 拒絕混用，就是把這一課寫進巨集。

### 延伸：其他混型陷阱

**`char` 是否有號由實作定義**（C11 6.2.5 §15）：

```c
char b = 0x80;
puts(b == 0x80 ? "b == 0x80" : "b != 0x80");
```

```
x86-64 gcc:          b != 0x80      （char 有號，b = -128）
clang 14:            warning: result of comparison of constant 128 with expression of type 'char' is always false
aarch64 (qemu):      b == 0x80      （Arm64 Linux ABI 規定 char 無號）
```

同一份原始碼，x86 和 Arm 結果相反。處理原始位元組一律用 `unsigned char` 或 `uint8_t`。

**`labs(LONG_MIN)`**：二補數下 `LONG_MIN` 的絕對值無法表示，是 UB：

```
sizeof(long) = 8, labs(LONG_MIN) = -9223372036854775808
sizeof(-2147483648) = 8
-fsanitize=undefined: runtime error: negation of -9223372036854775808 cannot be represented in type 'long int'
```

第二行點出另一個陷阱：**C 沒有負的整數常數**。`-2147483648` 是對 `2147483648` 套用單元負號，而 2147483648 超出 32 位元 `int`/`long`，依整數常數規則它的型別變成 `long long`——用 `-m32` 驗證（`_Static_assert(sizeof(-2147483648) == 8)` 與 `_Static_assert(sizeof(long) == 4)` 在 `gcc -m32 -c` 下都通過）。所以 glibc 把 `INT_MIN` 定義為 `(-INT_MAX - 1)`。

---

## 題目 0x0D：零的補數

> 評論以下程式碼：
> ```c
> unsigned int zero = 0;
> unsigned int compzero = 0xFFFF; /* 1's complement of zero */
> ```

Jones：這題檢驗應試者是否理解字組長度的重要。寫死 `0xFFFF` 的程式在 16 位元 MCU 上正確，產品換到 32 位元處理器才出錯——嵌入式產品換代最常見的移植缺陷。

### 常見錯答

- 「沒問題，`0xFFFF` 就是全 1」——只在 `unsigned int` 恰為 16 位元時成立。
- 「寫 `~0` 最好」——Jones 的答案，在二補數機器上對，但有更好的寫法（見下）。

### 正解

```c
unsigned int compzero = 0xFFFF;
unsigned int c1 = ~0U;
unsigned long c2 = ~0U;
unsigned long c3 = -1;
printf("%x %x %lx %lx\n", compzero, c1, c2, c3);
```

```
ffff ffffffff ffffffff ffffffffffffffff
```

- `0xFFFF` 在 32 位元平台只是 `0x0000FFFF`。
- `~0U` 在無號型別上反轉，得到 `UINT_MAX`；但**寬度固定是 `unsigned int`**，存進 `unsigned long` 時 `c2` 只有低 32 位元為 1。
- `-1` 轉成任何無號型別都得到該型別的最大值（C11 6.3.1.3 §2），不用知道寬度；代價是 `-Wsign-conversion` 會警告 `unsigned conversion from ‘int’ to ‘long unsigned int’ changes value from ‘-1’ to ‘18446744073709551615’`。
- **意圖最清楚的是 `<limits.h>` 的 `UINT_MAX`、`ULONG_MAX`**。

### 規格依據：為什麼 `~0` 不如 `~0U`

`~0` 是 `int` 的反轉。C23 之前的標準允許一補數與「符號與值」表示法；一補數下全 1 是**負零**：實作支援負零時 `~0` 得到負零、轉無號變 0；不支援時，產生負零的 `~` 運算本身就是 UB（C11 6.2.6.2 §3–4）。`~0U` 直接在無號型別上運算，沒有這個問題。C23 只允許二補數（C23 6.2.6.2），這段歷史包袱才卸下。

### 延伸：全 1 遮罩與 bitwise 的三個陷阱

```c
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>

/* same shape as Linux GENMASK_TYPE(): never shifts by the full width */
#define GENMASK32(h, l) ((UINT32_MAX << (l)) & (UINT32_MAX >> (31 - (h))))

static uint32_t low_mask_naive(unsigned n)
{
    return (1U << n) - 1; /* undefined when n == 32 */
}

static uint32_t low_mask(unsigned n)
{
    return n ? UINT32_MAX >> (32 - n) : 0;
}

int main(void)
{
    uint8_t z = 0;
    printf("~z = %d, (uint8_t) ~z = %u, ~z == 0xFF is %d\n", ~z,
           (unsigned) (uint8_t) ~z, ~z == 0xFF);
    printf("GENMASK32(7, 4) = 0x%08" PRIx32 ", GENMASK32(31, 0) = 0x%08" PRIx32
           "\n", GENMASK32(7, 4), GENMASK32(31, 0));
    volatile unsigned n = 32;
    printf("low_mask(32) = 0x%08" PRIx32 "\n", low_mask(n));
    printf("low_mask_naive(32) = 0x%08" PRIx32 "\n", low_mask_naive(n));
    return 0;
}
```

```
$ gcc -std=c11 -O2 mask.c && ./a.out           # clang 14 輸出相同
~z = -1, (uint8_t) ~z = 255, ~z == 0xFF is 0
GENMASK32(7, 4) = 0x000000f0, GENMASK32(31, 0) = 0xffffffff
low_mask(32) = 0xffffffff
low_mask_naive(32) = 0x00000000
```

1. **`~` 作用在窄型別前先做整數提升**。`uint8_t` 的 0 提升為 `int` 後反轉得 −1，不是 `0xFF`，`~z == 0xFF` 為偽。8 位元暫存器上用 `if (~reg == 0xFF)` 檢查「全為 0」永遠不成立。gcc `-Wextra` 警告 `comparison of promoted bitwise complement of an unsigned value with constant`，clang 14 沒有。
2. **移位量等於寬度是 UB**。`(1U << 32) - 1` 實際得到 0 而非全 1：x86-64 的 `shl` 只取移位量低 5 位元，`1U << 32` 跑起來像 `1U << 0`。`-fsanitize=shift` 回報 `shift exponent 32 is too large for 32-bit type 'unsigned int'`。修正版從全 1 往右移，並單獨處理 `n == 0`。
3. **Linux 核心的 `GENMASK(h, l)`** 用 `type_max << l & type_max >> (BITS - 1 - h)`，兩個移位量都不會達到型別寬度，`GENMASK(31, 0)` 合法；另以 `BUILD_BUG_ON_ZERO()` 在編譯時期拒絕 `l > h`。

順帶一提浮點數的「全 1」問題：`0.1 + 0.2 == 0.3` 印出 `not equal, 0.30000000000000004`——二進位無法有限表示這三個數。見 [Ch 25](./25-ieee754.md)。

---

## 題目 0x0E：嵌入式系統中的動態配置

> 嵌入式系統中以 heap 動態配置記憶體，有哪些問題要注意？

這是附加題第一題。Jones 期待應試者列出碎片化、垃圾回收、執行時間不定等議題，「看重解題方式而非答案」。

### 常見錯答

- 「`malloc` 失敗了再說」——在沒有虛擬記憶體與 swap 的 MCU 上，失敗通常沒有退路。
- 「Linux 有 overcommit，`malloc` 永遠不會傳回 NULL」——不完整，下面實測。

### 正解：四類問題

| 問題 | 說明 | 對策 |
|---|---|---|
| 碎片化 | 可用總量夠，卻找不到夠大的連續區塊 | 合併相鄰閒置區塊、固定大小的 pool、只在初始化時配置 |
| 洩漏 | 每小時洩漏幾個位元組，運作數月終究耗盡 | 靜態配置、arena 一次釋放 |
| 執行時間 | 搜尋可用區塊的時間取決於 heap 當下狀態 | 即時系統要求可預期的上界：pool / TLSF |
| 配置失敗 | RAM 有限，`NULL` 必須處理且難以復原 | 開機時配好所有物件 |

MISRA C:2012 Rule 21.3 因此禁止 `<stdlib.h>` 的配置函式。FreeRTOS 提供五種 heap 實作讓應用選擇：`heap_1` 只配置不釋放；`heap_2` 可釋放、best fit、**不合併**相鄰閒置區塊；`heap_3` 包裝標準 `malloc`；`heap_4` 釋放時合併（coalescing）；`heap_5` 同 `heap_4` 但可跨多段不連續記憶體。

> malloc 的內部結構回看 [Ch 17](./17-malloc-internals.md)；自己寫一個合併相鄰區塊的配置器是 [練習 B](./practice-b-my-malloc.md) 的內容。

### 延伸：在主機上觀察 heap_2 與 heap_4 的差別

我們抓 FreeRTOS-Kernel（本機用 V11.1.0 標籤，原文用 V11.3.1）的 POSIX 移植層標頭編譯 `heap_2.c` 與 `heap_4.c`，把排程器相關函式換成空函式：

```c
/* cfg/FreeRTOSConfig.h：FreeRTOS.h 要求的最小設定 */
#define configUSE_PREEMPTION        1
#define configUSE_IDLE_HOOK         0
#define configUSE_TICK_HOOK         0
#define configMAX_PRIORITIES        5
#define configMINIMAL_STACK_SIZE    128
#define configUSE_16_BIT_TICKS      0
#define configTOTAL_HEAP_SIZE       1024
```

```c
#include <stdio.h>
#include "FreeRTOS.h"
#include "task.h"

void vTaskSuspendAll(void) {}
BaseType_t xTaskResumeAll(void) { return pdFALSE; }
void vPortEnterCritical(void) {}
void vPortExitCritical(void) {}

int main(void)
{
    void *a = pvPortMalloc(64), *b = pvPortMalloc(64);
    void *rest = pvPortMalloc(xPortGetFreeHeapSize() - 16); /* header */
    printf("a, b, rest: %s, free = %zu\n",
           (a && b && rest) ? "ok" : "NULL", xPortGetFreeHeapSize());
    vPortFree(a);
    vPortFree(b);
    printf("after free(a), free(b): free = %zu\n", xPortGetFreeHeapSize());
    void *c = pvPortMalloc(128);
    printf("malloc(128): %s\n", c ? "ok" : "NULL");
    return 0;
}
```

```
$ git clone --depth 1 --branch V11.1.0 https://github.com/FreeRTOS/FreeRTOS-Kernel.git
$ K=FreeRTOS-Kernel
$ gcc -std=c11 -O2 -Icfg -I$K/include -I$K/portable/ThirdParty/GCC/Posix \
      -I$K/portable/ThirdParty/GCC/Posix/utils heaptest.c $K/portable/MemMang/heap_2.c
== heap_2
a, b, rest: ok, free = 0
after free(a), free(b): free = 160
malloc(128): NULL
== heap_4
a, b, rest: ok, free = 0
after free(a), free(b): free = 160
malloc(128): ok
```

64 位元主機上每個區塊前有 16 位元組的 `BlockLink_t` 標頭，所以 64 位元組的請求佔 80。釋放 `a`、`b` 後兩種實作都回報 160 位元組可用——這是**閒置區塊總和**，不是最大連續區塊。`pvPortMalloc(128)` 連標頭需要 144：

```
  釋放前:   [ a 使用中 80 ][ b 使用中 80 ][ rest 使用中 ... ]
  heap_2:   [ 閒置 80     ][ 閒置 80     ][ rest ]   → 要 144，沒有夠大的：NULL
  heap_4:   [ 閒置 160（合併）           ][ rest ]   → 成功
```

可用總量相同、能否配置不同，這就是碎片化。`heap_2.c` 插入閒置區塊時以 `pxIterator->pxNextFreeBlock->xBlockSize < xBlockSize` 走訪，串列由小到大排序，取第一個夠大的就是 best fit（原始碼第 144 行）。安全關鍵系統多半用 `configSUPPORT_STATIC_ALLOCATION` 與 `xTaskCreateStatic()` 完全避開 heap。

### 延伸：`malloc(0)` 與 overcommit

附加題的第二部分：

> ```c
> char *ptr;
> if ((ptr = (char *) malloc(0)) == NULL)
>     puts("Got a null pointer");
> else
>     puts("Got a valid pointer");
> ```

Jones 的答案是 `Got a valid pointer`。但標準規定請求 0 位元組時，傳回空指標或一個不得用來存取物件的非空指標，**二擇一由實作定義**（C11 7.22.3 §1）。這題沒有唯一答案，只有「在某個 C 函式庫上的答案」：

```c
char *p = malloc(0);
printf("%s, usable = %zu\n", p ? "non-null" : "null", p ? malloc_usable_size(p) : 0);
free(p);
void *q = malloc((size_t) 1 << 40);
printf("malloc(1 TiB): %s\n", q ? "non-null" : "null");
```

```
gcc -O0 / gcc -O2 / clang -O0 / clang -O2（四者相同）:
non-null, usable = 24
malloc(1 TiB): non-null
```

glibc 傳回非空指標，背後是 24 位元組的最小區塊；經由它存取物件仍是 UB，但可以傳給 `free()`。（題目裡的 `(char *)` 在 C 中多餘，`void *` 可隱含轉成任何物件指標。）C23 在相近問題上收緊：`realloc(p, 0)` 從實作定義改為**未定義行為**（C23 7.24.3.7）。

第二行要誠實說明環境：**本機 WSL 的 `vm.overcommit_memory` 是 1**（`cat /proc/sys/vm/overcommit_memory` 印 1，「永遠 overcommit」），所以 1 TiB 也成功。原文的兩台主機是預設的 0（啟發式模式），只拒絕明顯超出「實體記憶體 + swap」的請求，1 TiB 在那裡得到 `null`（**引用原文，本機未在 mode 0 實測**；改設定需要 root）。要在本機看到失敗，得要求超出整個使用者定址空間：

```c
void *q = malloc((size_t) 1 << 47);   /* 128 TiB */
printf("malloc(128 TiB): %s\n", q ? "non-null" : "null");
```

```
gcc -O0:   malloc(128 TiB): null
gcc -O2:   malloc(128 TiB): null
clang -O0: malloc(128 TiB): null
clang -O2: malloc(128 TiB): non-null     ← !
```

clang `-O2` 的組合語言裡**根本沒有 `malloc` 呼叫**，只剩 `printf`：配置結果除了和 `NULL` 比較沒有其他用途，LLVM 把整次配置刪掉並假設成功（C 標準允許，因為沒有可觀察的差別）。「`malloc` 會不會傳回 NULL」這個問題，答案取決於 C 函式庫、核心設定與編譯器最佳化三層。

### 延伸：堆疊也不是無限的

```c
int main(void)
{
    int array[10000000];         /* 40 MB */
    for (int i = 0; i < 10000000; i++)
        array[i] = i;
    printf("%d\n", array[9999999]);
    return 0;
}
```

```
$ ./big
Segmentation fault (core dumped)      (rc=139，ulimit -s = 8192 KiB)
$ gcc -fstack-usage -c big.c && cat big.su
big.c:3:5:main	40000048	static
$ gcc -Wstack-usage=8192 -c big.c
warning: stack usage is 40000048 bytes [-Wstack-usage=]
```

主執行緒的堆疊上限由 `RLIMIT_STACK` 決定，不是編譯器。嵌入式系統的堆疊常只有幾 KiB，`-fstack-usage` 與 `-Wstack-usage=N` 是韌體專案的標準配備。VLA 的大小沒有上限檢查，過大即壓垮堆疊，所以 Linux 核心在 v4.20 清除所有 VLA 後全面開 `-Wvla`。

---

## 題目 0x0F：`typedef` 與 `#define`

> 以下兩者都宣告指向 `struct s` 的指標型別，哪一個較好？
> ```c
> #define dPS struct s *
> typedef struct s *tPS;
> ```

Jones 說答對而且理由正確的人「is to be congratulated or condemned ('get a life' springs to mind)」。

### 常見錯答

「兩者一樣，只是風格」。

### 正解

`typedef` 較好。巨集只是語彙單元替換，`typedef` 定義完整的型別（C11 6.7.8）。差異在多個宣告符與 `const` 上現形：

```c
struct s { int v; };
#define dPS struct s *
typedef struct s *tPS;

void f(struct s *q)
{
    dPS p1, p2;      /* p2 is NOT a pointer */
    tPS p3, p4;      /* both pointers */
    p1 = q; p2 = q; p3 = q; p4 = q;
    (void) p1; (void) p2; (void) p3; (void) p4;
}

void g(struct s *q)
{
    const dPS p1 = q;
    const tPS p2 = q;
    p1->v = 1;
    p1 = 0;
    p2->v = 1;
    p2 = 0;
}
```

```
td1.c:11:18: error: incompatible types when assigning to type ‘struct s’ from type ‘struct s *’
td1.c:19:11: error: assignment of member ‘v’ in read-only object
td1.c:22:8: error: assignment of read-only variable ‘p2’
```

- `dPS p1, p2;` 展開成 `struct s *p1, p2;`，`p2` 是結構不是指標（第 11 行）。
- `const dPS p1` 展開成 `const struct s *p1`：指向唯讀結構的指標，所以 `p1->v = 1` 錯（第 19 行）、`p1 = 0` 可以。
- `const tPS p2` 是 `struct s *const p2`：**指標本身**唯讀，所以 `p2 = 0` 錯（第 22 行）、`p2->v = 1` 可以。

錯誤落在不同的行，語意完全相反。

### 延伸：編譯器與除錯器看得見 `typedef`，看不見巨集

```c
dPS p1 = 1;
tPS p2 = 2;
```

```
gcc 11:   warning: initialization of ‘struct s *’ from ‘int’ makes pointer from integer without a cast
          warning: initialization of ‘tPS’ {aka ‘struct s *’} from ‘int’ makes pointer from integer ...
clang 14: ... initializing 'tPS' (aka 'struct s *') with an expression of type 'int'
```

（gcc 11 這是警告；GCC 14 起 `-Wint-conversion` 預設為錯誤。）診斷訊息保留了 `tPS` 這個名字，巨集在前置處理後就消失。gdb 也一樣：

```
$ gcc -g td3.c -o td3
(gdb) ptype tPS
type = struct s {
    int v;
} *
(gdb) whatis p2
type = tPS
(gdb) ptype dPS
No symbol "dPS" in current context.
(gdb) whatis p1
type = struct s *
```

巨集資訊要 `-g3` 才寫進除錯資訊，而且要停在中斷點後 `info macro` 才查得到。本機一個環境細節：gcc 11 的 `-g3` 預設產生 DWARF 5，gdb 12 讀不到其中的巨集表（`readelf --debug-dump=macro` 看得到 `DW_MACRO_define_strp ... dPS struct s *`，gdb 卻回報「no definition」）；改用 `-g3 -gdwarf-4` 才正常：

```
$ gcc -g3 -gdwarf-4 td3.c -o td3g4
(gdb) break main
(gdb) run
(gdb) info macro dPS
Defined at .../td3.c:4
#define dPS struct s *
```

> gdb 的用法回看 [Ch 5](./05-gdb-in-practice.md)。

### 延伸：要不要 `typedef` 結構與指標，是風格問題

`#define` vs `typedef` 有正確答案；要不要用 `typedef` 替結構或指標取別名則是風格。Linux 核心 coding style 第 5 章明言「It's a mistake to use typedef for structures and pointers」——看到 `vps_t a;` 不知道 `a` 是什麼；只保留給完全不透明的型別（如 `pte_t`）與明確寬度的整數。其他大型專案多半選擇用：SQLite 的 `typedef struct sqlite3 sqlite3;`（不透明）、FreeRTOS 的 `typedef struct tskTaskControlBlock * TaskHandle_t;`、libcurl 的 `typedef void CURL;`。函式庫要對外維持穩定 ABI，把結構藏在不透明型別後面，內部配置可以改而不影響應用（回看 [Ch 29 — 物件導向 C](./29-oop-in-c.md)）。

但不透明的寫法本身影響編譯器能做的檢查：

```c
#ifdef VOID_HANDLES
typedef void EASY;  /* like libcurl's CURL */
typedef void SHARE; /* like libcurl's CURLSH */
#else
typedef struct easy EASY;   /* like sqlite3 */
typedef struct share SHARE;
#endif

int easy_perform(EASY *e);

int run(SHARE *sh)
{
    return easy_perform(sh); /* wrong handle type */
}
```

```
struct handles: warning: passing argument 1 of ‘easy_perform’ from incompatible pointer type [-Wincompatible-pointer-types]
void handles:   （-Wall -Wextra 下 0 個診斷）
```

以不同 `struct` 宣告的 handle 是不相容的指標型別，傳錯會被抓到（GCC 14 起是錯誤）；`void` handle 全都是 `void *`，傳錯編譯器一聲不吭。**`typedef` 只是別名，型別檢查的強弱取決於別名背後的型別。**

---

## 題目 0x10：`c = a+++b;`

> C 允許一些糟糕的寫法。以下寫法合法嗎？若合法，做什麼？

Jones 把這題當輕鬆收尾，答不出來也不算問題；他看重的是它引出的程式碼風格、code review 與 lint 討論。

### 常見錯答

「`a + (++b)`」，或「不合法」。

### 正解

合法，等同 `c = (a++) + b;`：

```c
int a = 5, b = 7, c;
c = a+++b;
printf("a = %d, b = %d, c = %d\n", a, b, c);   /* a = 6, b = 7, c = 12 */
```

### 規格依據：maximal munch

切分語彙單元時，下一個語彙單元總是取**能構成語彙單元的最長字元序列**（C11 6.4 §4），俗稱 maximal munch。`a+++b` 切成 `a`、`++`、`+`、`b`。這條規則**只看字元、不看語法**：

```c
int f(int a, int b) { return a+++++b; }
```

```
munch5.c:1:33: error: lvalue required as increment operand
```

`a+++++b` 切成 `a ++ ++ + b`，對非左值的 `a++` 再做 `++`，編譯失敗——即使 `a++ + ++b` 是合法的解讀。C11 6.4 §6 的 EXAMPLE 2 用 `x+++++y` 示範同一規則。實務上用空白寫明意圖：`c = a++ + b;`。

> 詞法分析器如何實作 maximal munch，回看 [Ch 38 — 編譯器實作](./38-compiler-construction.md)。

### 延伸：巨集展開之後不會重新 munch

前置處理在切分語彙單元**之後**進行，替換結果仍是原本的語彙單元，不會重新套用 maximal munch（唯一例外是 `##`，C11 6.10.3.3 §3）：

```c
#include <stdio.h>

#define ADD(x, y)   x + y
#define SQUARE(x)   ((x) * (x))
#define PLUS        +

int main(void)
{
    int a = 5, b = 7, c;
    c = ADD(a++, b);
    printf("a = %d, b = %d, c = %d\n", a, b, c);
    c = a PLUS++b;
    printf("a = %d, b = %d, c = %d\n", a, b, c);
    c = SQUARE(a++);
    printf("a = %d, c = %d\n", a, c);
    return 0;
}
```

```
$ gcc -E -P macro.c | grep "c = "
    c = a++ + b;
    c = a + ++b;
    c = ((a++) * (a++));
$ gcc macro.c && ./a.out
a = 6, b = 7, c = 12
a = 6, b = 8, c = 14
a = 8, c = 42
```

前兩行若不加空白排在一起都寫成 `a+++b`，語意卻相反：`PLUS` 已經是獨立的 `+` 語彙單元，緊接的 `++` 屬於 `b`。前置處理器輸出文字時在必要處補空白，避免相鄰語彙單元被重新切分。

```
  原始碼              語彙單元              替換後                剖析
  ─────────────────  ───────────────────  ───────────────────  ──────────────────────────
  ADD(a++, b)        ADD ( a ++ , b )     a ++ + b             (a++) + b        c = 12
  a PLUS++b          a PLUS ++ b          a + ++ b             a + (++b)        c = 14
  SQUARE(a++)        SQUARE ( a ++ )      ((a++) * (a++))      兩個 unsequenced 副作用：UB
```

第三行是**雙重求值**：`SQUARE` 的參數在替換清單出現兩次，乘號兩側沒有 sequence point，未定義行為。gcc/clang 在 `-O0`/`-O2` 都印 `a = 8, c = 42`，但那只是本次實測，會隨版本而變。clang 不加旗標就警告 `multiple unsequenced modifications to 'a' [-Wunsequenced]` 並附 `expanded from macro 'SQUARE'`；gcc 需要 `-Wall` 才有 `operation on ‘a’ may be undefined [-Wsequence-point]`。

### 延伸：真實世界的雙重求值

**Research UNIX V7 的 `getc`**：`<stdio.h>` 把 `getc` 定義為 `(--(p)->_cnt>=0? *(p)->_ptr++&0377:_filbuf(p))`，參數 `p` 在條件與分支各求值一次。我們複製這個結構重現問題：

```c
#include <stdio.h>

/* A stripped-down copy of the Research UNIX V7 <stdio.h> stream and getc() */
struct v7file {
    int _cnt;            /* bytes left in the buffer */
    char *_ptr;          /* next byte to read */
};

static int v7_filbuf(struct v7file *p) { (void) p; return -1; }

#define v7getc(p) (--(p)->_cnt >= 0 ? *(p)->_ptr++ & 0377 : v7_filbuf(p))

int main(void)
{
    char buf_a[] = "AAAA", buf_b[] = "BBBB";
    struct v7file fa = {4, buf_a}, fb = {4, buf_b};
    struct v7file *streams[] = {&fa, &fb};
    struct v7file **fpp = streams;

    int ch = v7getc(*fpp++);   /* intends: read one byte from fa */
    printf("ch = '%c', fpp advanced by %td\n", ch, fpp - streams);
    printf("fa._cnt = %d, fb._cnt = %d, fb._ptr offset = %td\n",
           fa._cnt, fb._cnt, fb._ptr - buf_b);
    return 0;
}
```

```
ch = 'B', fpp advanced by 2
fa._cnt = 3, fb._cnt = 4, fb._ptr offset = 1
```

本意是從第一個串流讀一個位元組，結果遞減的是 `fa` 的計數、讀的卻是 `fb` 的緩衝區，`fpp` 前進兩格——兩個串流的狀態同時被破壞。C 標準後來要求以巨集實作的函式庫函式對每個引數**恰好求值一次**（C11 7.1.4 §1），只對 `getc`、`putc` 及寬字元版保留例外：它們「may evaluate stream more than once」，所以 `stream` 引數不得帶副作用（C11 7.21.7.5 §2）。必要時寫 `(getc)(fp)`，函式名稱後不是左括號，就不會展開巨集。

**Linux AppArmor 的 `match_char()`（CVE-2026-23406）**：參數在替換清單中只出現一次也不保證只求值一次。巨集主體是 `do { ... } while (1)`，狀態採 differential encoding 時以 `continue` 重試，參數 `C` 隨之再次求值；呼叫端傳的是 `(u8) *str++`，每次重試讓 `str` 多前進一格，最後讀出緩衝區之外。Linux v7.0 的修正先取出 `*str`，把 `str++` 移到巨集外。

**hwmon lm87 的 TOCTOU**：`FAN_FROM_REG(reg, div)` 展開後多次讀取 `reg`，而 `reg` 是 `data->fan[nr]` 這類被其他執行緒更新的共用資料。檢查 `reg == 0` 時非零，作為除數時可能已是 0。Linux v6.19 把這類巨集改成函式，引數以傳值方式只讀一次。

三個案例的共同點：**呼叫端看似只求值一次的引數被巨集求值多次**。審查程式碼時，傳給巨集的引數避免 `++`、`--`、指定與函式呼叫；沒持有 lock 的共用資料先讀進區域變數。

最後收一個 sequence point 的經典：`int j = ++i + i++;`（`i` 初值 1）在 gcc 得 `j = 5`、clang 得 `j = 4`，兩者都警告——同一物件兩個 unsequenced 副作用（C11 6.5 §2）。相對地，`--a || ++a`、逗號運算子 `(++i, i++)` 在運算子之間有 sequence point，行為確定；但函式引數之間的逗號只是分隔符號，`foo(i++, i++)` 仍是 UB。

> 未定義行為的分類與編譯器推論，回看 [Ch 34](./34-undefined-behavior.md)；`c_interview` 課的 [前置處理器陷阱](../c_interview/19-preprocessor-traps.md) 與 [經典陷阱 40 題](../c_interview/28-classic-traps-40.md) 可做面試前的快速複習。

---

## 對比與取捨

| 題目 | 1990 年代標準答案 | 今天要補上的 | 工具 |
|---|---|---|---|
| 0x09 位元 | `|=`、`&= ~` + `#define` | 無號遮罩、遮罩寬度 = 目標寬度、讀改寫非 atomic | `-fsanitize=shift`、BSRR 類暫存器 |
| 0x0A 絕對位址 | `*(int *)0x67a9 = 0xaa55` | `volatile`、明確寬度、對齊、16 位元 `int` 範圍 | CMSIS 式巨集、`--param=min-pagesize=0` |
| 0x0B ISR | 無參數、無傳回、無浮點、無 printf | 架構差異、上下半部、呼叫外部函式的保存成本 | `interrupt` 屬性、`-mgeneral-regs-only` |
| 0x0C 混型 | `> 6` | 結果取決於型別寬度；長度混型就是 CVE | `-Wsign-conversion`、ASan、`implicit-conversion` |
| 0x0D 全 1 | `~0` | `~0U` 寬度固定；`UINT_MAX` 最清楚 | `GENMASK` |
| 0x0E 配置 | 碎片化、執行時間 | `malloc(0)` 實作定義；overcommit 與編譯器刪除配置 | FreeRTOS heap_4/static、`-Wstack-usage` |
| 0x0F 型別別名 | `typedef` | 診斷與除錯資訊保留名稱；不透明 handle 用不同 `struct` | gdb `-g3 -gdwarf-4` |
| 0x10 munch | `(a++) + b` | 巨集展開不重新 munch；雙重求值的真實 CVE | `-Wsequence-point`、`-Wunsequenced` |

## 踩雷集錦

1. **以為 `1 << 31` 沒事**：`1` 是有號 `int`，移進符號位元是 UB。遮罩一律 `1U`、`UINT64_C(1)`、或核心的 `BIT()`/`BIT_ULL()`。
2. **以為 `const` 能讓 MMIO 寫入被保留**：`*(int * const)0x67a9` 的 `const` 毫無作用，能保留存取的是 `volatile`。
3. **以為位元欄位是「各自的變數」**：相鄰位元欄位是同一個記憶體位置，編譯器可以用任意寬度存取，連鄰居都會被寫壞（`b = 30014` 那個實驗）。
4. **以為警告全開就能抓到混型漏洞**：`copy_payload` 在 `-Wall -Wextra` 下零警告，要 `-Wsign-conversion` 或 sanitizer。
5. **以為「`malloc` 會不會失敗」有唯一答案**：本機 overcommit=1 時 1 TiB 也成功；clang `-O2` 甚至把 128 TiB 的配置刪掉假設成功。檢查 NULL 依然必要，但別把它當成記憶體不足的唯一訊號。
6. **以為參數只出現一次就只求值一次**：AppArmor 的 `match_char()` 用迴圈重試讓參數多次求值。

## 進階：再往深一層

- **讀改寫的硬體解法**：STM32 GPIO 的 BSRR（寫 1 設定/清除，不必先讀）、Cortex-M3/M4 的 bit-banding（每個位元有自己的別名位址）、RISC-V 的 `amoor.w`/`amoand.w` atomic 指令。面試追問「`REG |= BIT3` 如何做到 ISR-safe」時，這三種比「關中斷」更有分量。
- **DMA 與快取一致性**：MMIO 存取之外，DMA 緩衝區在非一致性（non-coherent）平台上要用 `dma_sync_single_for_device()` 一類的 API 清/無效化快取；`volatile` 對此完全無能為力。
- **型別轉換的形式化**：〈基於 C 語言標準研究與系統程式安全議題〉（jserv）把整數轉換等級、提升、一般算術轉換與零擴充/符號擴充指令一一對應，是題目 0x0C 的完整版本。

## 動手練習

1. 把 `bfrace.c` 的兩個欄位改成各 16 位元（`a : 16, b : 16`），分別用 gcc 與 clang `-O0` 跑十次，觀察誰會遺失更新，再用 `objdump -d` 說明寫回指令的寬度差異。
2. 修改 `fakedev.c`：讓「硬體」執行緒在讀取 `REG_DATA` 後自動清除 READY 位元（模擬「讀即清除」的暫存器），然後思考：主程式先讀 STATUS 再讀 DATA，順序能不能被編譯器調換？為什麼 `volatile` 存取之間的順序有保證？
3. 把 `tick_sig.c` 改成 `sigprocmask` + `sigsuspend()` 版本，消除遺失喚醒的窗口。
4. 寫一個 `copy_payload` 的修正版：參數改為 `size_t len`，並在**信任邊界**（解析封包的那一層）把 16 位元欄位以 `uint16_t` 讀入。用 `-Wsign-conversion` 確認零警告，用 ASan 確認 `0xFFFF` 被正確拒絕。

## 本章重點整理

- 位元操作的三個要點：無號遮罩、遮罩寬度與目標一致、對暫存器的 `|=` 是不可分割性沒有保證的讀改寫。
- 整數轉指標由實作定義；MMIO 存取要 `volatile` + 明確寬度 + 對齊；`const` 在轉型裡沒有作用。
- ISR 沒有呼叫端，所以不能有參數與傳回值（x86 的 `interrupt` 屬性是例外）；短、快、不阻塞，耗時工作交給下半部；呼叫外部函式會增加暫存器保存成本。
- 一般算術轉換讓有號值在比較與運算中變無號；結果取決於型別寬度；在長度與索引上就是記憶體安全漏洞。
- `malloc(0)` 的結果由實作定義；`malloc` 是否傳回 NULL 取決於函式庫、核心設定、編譯器三層。
- `typedef` 是型別，巨集是文字；maximal munch 只看字元；巨集參數可能被求值多次。

## 自我檢核

- [ ] 不看筆記，能寫出把第 `k` 位元設為 `b`（0 或 1）的單行運算式，並說出 `k` 與 `b` 的前提條件
- [ ] 能解釋 `bfrace.c` 裡 `b` 為什麼可能超過 30000
- [ ] 如果面試官問「`*(int * const)0x67a9 = 0xaa55;` 有什麼問題」，你能說出至少四點
- [ ] 能畫出一般算術轉換的決策流程，並舉一個「換到 16 位元 MCU 結果就變」的例子
- [ ] 能說明 CVE-2013-2094 與 CVE-2016-9793 的共同模式，以及各自的修正為什麼只改了一個型別
- [ ] 能解釋為什麼同一個 `malloc((size_t)1 << 47)` 在 clang `-O0` 與 `-O2` 結果不同
- [ ] 能說出 `const dPS p` 與 `const tPS p` 分別讓什麼東西唯讀
- [ ] 想一個你看過的巨集：它的參數會不會被求值多次？如果會，改成什麼？

## 延伸閱讀

### 論文

- **[Towards Optimization-Safe Systems: Analyzing the Impact of Undefined Behavior](https://dl.acm.org/doi/10.1145/2517349.2522728)** — Xi Wang et al.，SOSP（2013）
  - **核心貢獻**：用工具 STACK 系統性找出被編譯器依 UB 刪掉的安全檢查，在 Linux 核心、PostgreSQL 等專案中找到上百個真實案例。
  - **讀哪裡**：Section 2（UB 如何讓檢查消失的實例）與 Section 6（案例研究）；Section 4 的求解器細節可跳過。
  - **和本章的關聯**：題目 0x0C 的有號溢位、題目 0x0E 的「配置被刪掉」，都是編譯器依規格推論的結果；前提是讀過 [Ch 34](./34-undefined-behavior.md)。

### 部落格 / 技術文章

- **[Operating System in 1,000 Lines](https://operating-system-in-1000-lines.vercel.app/en/)**（[繁體中文版](https://operating-system-in-1000-lines.vercel.app/tw/)）— Seiya Nuta
  - **這篇說什麼**：在 QEMU RISC-V 上用約一千行 C 寫出作業系統。
  - **讀哪裡**：Exception（題目 0x0B 的進入點與 trap frame）、Page Table（分頁後的 MMIO 映射）、Disk I/O（題目 0x0A 的 virtio-blk 驅動）三章。
  - **為什麼值得讀**：本章因環境限制沒能執行的裸機實驗，這本書提供完整可跑的程式碼；前提是會一點 RISC-V 組合語言。
- **[基於 C 語言標準研究與系統程式安全議題](https://hackmd.io/@sysprog/c-std-security)** — 黃敬群
  - **這篇說什麼**：整數轉換等級、提升、一般算術轉換的完整條文，對照編譯器產生的零擴充與符號擴充指令，並以 Heartbleed 等案例說明。
  - **讀哪裡**：整數轉換的章節。
  - **為什麼值得讀**：題目 0x0C 只展示了現象，這篇把規則一條條推完。

### 官方文件 / Spec

- **[ISO/IEC 9899:201x 草案 N1570](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)**
  - **讀哪裡**：6.3.1.1（整數提升）、6.3.1.8（一般算術轉換）、6.5.7（移位）、6.7.2.1 §11（位元欄位配置）、6.3.2.3 §5（整數轉指標）、6.4 §4（maximal munch）、7.1.4 §1（巨集實作的函式庫函式）、7.22.3 §1（`malloc(0)`）。
- **[FreeRTOS Memory Management](https://freertos.org/a00111.html)**
  - **讀哪裡**：heap_1 到 heap_5 的比較表，以及 static allocation 一節。
  - **前提**：做過題目 0x0E 的實驗，對「合併相鄰閒置區塊」有直覺。
- **[Linux kernel coding style 第 5 章 Typedefs](https://www.kernel.org/doc/html/latest/process/coding-style.html#typedefs)**
  - **讀哪裡**：第 5 章全文，列出核心允許 `typedef` 的五種情況。

### 書籍

- **《Making Embedded Systems》（2nd ed.）** — Elecia White（O'Reilly，2024）
  - **這本書的定位**：從軟體工程師角度講嵌入式：暫存器、中斷、記憶體、低功耗。
  - **讀哪幾章**：中斷與輸入輸出的章節對應題目 0x0A、0x0B；記憶體的章節對應 0x0E。

十六道題目做完，前 43 章的零件都檢查過了。最後一步是把它們組成一個能跑的系統：一個用 X macro 定義指令、用 computed goto 分派、用 arena 管記憶體、能 `dlopen` 外掛的小型虛擬機器。

→ [Final Project：迷你 stack VM 直譯器](./final-project-stack-vm.md)
