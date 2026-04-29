# Final Project — Cortex-M3 Mini RTOS-lite

> 目標：把整門課所學集大成，自刻一個跑在 Cortex-M3 上的迷你 RTOS。包含 2 個 task、PendSV-based context switch、SVC syscall、SysTick tick、MPU 隔離、ITM trace。寫完後用 GDB + OpenOCD 跑一輪 debug walkthrough，**自己埋一個 bug 自己抓**。

## 任務規格

```
Hardware:    QEMU mps2-an385（Cortex-M3）或 STM32F4 Discovery
Toolchain:   arm-none-eabi-gcc, gdb-multiarch, openocd, qemu-system-arm
Lines:       ~600 (不含 startup)
```

| 功能 | 規格 |
|---|---|
| 2 個 task | `task_a`、`task_b`，分別 1 Hz 與 2 Hz 印 message |
| Context switch | PendSV handler，純 asm naked function |
| SysTick | 1 ms tick，scheduler 在 SysTick 內決定要不要切 |
| SVC syscall | 至少 2 個：`SYS_SLEEP_MS`、`SYS_PRINT` |
| MPU | 每 task 自己一塊 stack region，越界 trap |
| ITM trace | task switch 時印 task ID 到 ITM port 1，print 走 ITM port 0 |
| Debug walkthrough | 寫一份 `debug.md` 文件實況：故意埋一個 bug，記錄 GDB 抓的過程 |

## 期望輸出

UART / ITM 0:

```
boot
sched start
[a] tick 1
[b] tick 1
[b] tick 2
[a] tick 2
[b] tick 3
[b] tick 4
[a] tick 3
...
```

`a` 每秒一次，`b` 每 0.5 秒一次。永久跑下去。

## 實作步驟建議

把任務拆成 5 個 milestones，每個 milestone 結束 commit 一次：

### Milestone 1：startup + main + ITM init

從 Practice A 抄一份骨架。確認能 boot、能 ITM print。**到這裡就有了 Cortex-M baseline**。

### Milestone 2：SysTick scheduler skeleton

```c
typedef struct {
    uint32_t *sp;          /* task 自己的 stack pointer */
    uint32_t state;
    uint32_t wakeup_tick;
    uint8_t  id;
} tcb_t;

tcb_t tasks[2];
volatile int current_task = 0;
volatile uint32_t ticks = 0;

void SysTick_Handler(void) {
    ticks++;
    /* 選下一個 ready task */
    int next = current_task ^ 1;
    if (tasks[next].state == READY && tasks[next].wakeup_tick <= ticks) {
        current_task = next;
        SCB->ICSR = SCB_ICSR_PENDSVSET_Msk;   /* trigger context switch */
    }
}
```

簡單 round-robin，每 tick 看下個 task 是不是 ready。

### Milestone 3：PendSV context switch

這是最 tricky 的部分。寫 asm naked function：

```c
__attribute__((naked))
void PendSV_Handler(void) {
    asm volatile(
        /* 進來時 R0–R3、R12、LR、PC、xPSR 已被 HW push 到 PSP */
        /* 我們要 push R4–R11（callee-saved）到 PSP */
        "mrs   r0, psp           \n"
        "stmdb r0!, {r4-r11}     \n"

        /* 把 PSP 存到當前 task 的 TCB */
        "ldr   r1, =tasks         \n"
        "ldr   r2, =current_task  \n"
        "ldr   r3, [r2]           \n"
        "ldr   r4, [r2]           \n"
        "lsl   r4, r4, #4         \n"   /* tcb_t 大小假設 16，*16 取 offset */
        "add   r1, r1, r4         \n"
        "str   r0, [r1]           \n"   /* tasks[old].sp = PSP */

        /* 切 current_task（其實 SysTick 已經改了，這裡再讀一次）*/
        "ldr   r3, [r2]           \n"
        "ldr   r4, =tasks         \n"
        "lsl   r3, r3, #4         \n"
        "add   r4, r4, r3         \n"
        "ldr   r0, [r4]           \n"   /* 新 task 的 SP */

        /* pop R4-R11 從新 task PSP */
        "ldmia r0!, {r4-r11}      \n"
        "msr   psp, r0            \n"

        /* 用 EXC_RETURN 0xFFFFFFFD 返回 Thread mode + PSP */
        "ldr   lr, =0xFFFFFFFD    \n"
        "bx    lr                 \n"
    );
}
```

複雜的部分：tcb 大小、offset 計算要對。建議 **第一次寫用 size-aware C 包一層**，asm 最少：

```c
extern uint32_t *current_psp;     // 全域，C 算

__attribute__((naked))
void PendSV_Handler(void) {
    asm volatile(
        "cpsid i\n"
        "mrs r0, psp\n"
        "stmdb r0!, {r4-r11}\n"
        "bl pendsv_pick_next\n"   // C 函式：把 r0 存進舊 TCB、回傳新 PSP
        "ldmia r0!, {r4-r11}\n"
        "msr psp, r0\n"
        "cpsie i\n"
        "bx lr\n"
    );
}
```

C 端：

```c
uint32_t *pendsv_pick_next(uint32_t *current_psp) {
    tasks[current_task].sp = current_psp;
    /* current_task 已被 SysTick 更新到下一個 */
    return tasks[current_task].sp;
}
```

更易 debug。

### Milestone 4：SVC syscall

```c
typedef enum {
    SYS_SLEEP_MS = 0,
    SYS_PRINT    = 1,
} syscall_t;

static inline void sys_sleep_ms(uint32_t ms) {
    asm volatile("svc #0" :: "r"(ms) : "memory");
}

static inline void sys_print(const char *s) {
    register const char *r0 __asm__("r0") = s;
    register int r1 __asm__("r1") = 1;        /* SYS_PRINT */
    asm volatile("svc #0" :: "r"(r0), "r"(r1));
}
```

SVC handler（naked，要從 stack 取 R0–R1）：

```c
__attribute__((naked))
void SVC_Handler(void) {
    asm volatile(
        "tst lr, #4\n"
        "ite eq\n"
        "mrseq r0, msp\n"
        "mrsne r0, psp\n"
        "b svc_dispatch\n"
    );
}

void svc_dispatch(uint32_t *stack) {
    /* stack 指向 hardware push 的 frame，stack[0..7] = R0..R3, R12, LR, PC, xPSR */
    uint32_t arg0 = stack[0];
    uint32_t arg1 = stack[1];
    /* SVC 指令的下面 8 bit 是 syscall 號，PC 指 SVC 後一條 */
    uint32_t pc = stack[6];
    uint8_t  num = ((uint8_t *)pc)[-2];

    switch (num) {
        case SYS_SLEEP_MS:
            tasks[current_task].state = SLEEPING;
            tasks[current_task].wakeup_tick = ticks + arg0;
            current_task ^= 1;
            SCB->ICSR = SCB_ICSR_PENDSVSET_Msk;
            break;
        case SYS_PRINT:
            itm_puts((const char *)arg0);
            break;
    }
}
```

注意 `tst lr, #4` 是檢查 EXC_RETURN — bit[2] = 1 表示 Thread mode 用 PSP。SVC 從 user task 來都是 PSP。

### Milestone 5：MPU 設定

每 task 一塊 4 KB stack region：

```c
void mpu_setup_task(int task_id) {
    /* Region 1: task[id] 的 stack RW */
    MPU->RNR  = 1;
    MPU->RBAR = task_stack[task_id];        /* 4 KB align */
    MPU->RASR = (1 << 0)
              | ((11) << 1)                  /* SIZE = 4 KB (2^12) */
              | (3 << 24)                    /* AP = full RW */
              | (1 << 28);                   /* XN */

    /* Region 2: 別的 task stack 不可達 */
    MPU->RNR  = 2;
    MPU->RBAR = task_stack[task_id ^ 1];
    MPU->RASR = (1 << 0)
              | ((11) << 1)
              | (0 << 24)                    /* AP = no access */
              | (1 << 28);
}
```

PendSV 切 task 時呼叫 `mpu_setup_task(new_id)`。

context switch 後 task A 試圖寫 task B 的 stack → MemManage trap → kill task。

### Milestone 6：debug walkthrough 文件

寫一份 `debug.md`，內容：

1. **故意埋一個 bug**：例如 PendSV 中 `stmdb` 寫成 `stmia`、或忘記設 PSP
2. **跑一次，記錄症狀**：可能 hang、可能 fault、可能輸出亂跳
3. **用 GDB + OpenOCD debug**：
   - 設 breakpoint 在 PendSV_Handler 入口
   - 用 GDB Python 印 PSP / tcb 狀態
   - 用 watchpoint 抓 task[i].sp 何時被改
   - 用 ITM trace 看 task switch 順序
4. **找到並修復**
5. **驗證**：bug 不再復現

這份文件本身是 deliverable — 證明你會 debug，不只會寫。

## 完整參考解答

完整可跑專案放在 `solutions/arm/final-project/`（給予 reader）。寫完前**不要看**。

主要檔案：

```
final-project/
├── Makefile
├── link.ld
├── startup.c
├── main.c
├── scheduler.c
├── pendsv.S
├── svc.c
├── mpu.c
├── itm.c
├── tasks.c
└── debug.md
```

## 測試用例

1. **正常運行 60 秒**：兩個 task 都該維持輪流，task A 印 60 次，task B 印 120 次
2. **Task A 故意 stack overflow**：設 stack overrun，應觸發 MemManage，trap 到 handler，`itm_puts("task A killed\n")`
3. **連續 sleep**：兩個 task 都 sleep，CPU 應進 WFI，IDLE，下一個 tick 醒來
4. **GDB 連接 debug**：能看到 `tasks[0]`、`tasks[1]` 的 SP、state 變化

## 可選擴展（如果還有興趣）

- 加更多 syscall：`SYS_MUTEX_LOCK`、`SYS_QUEUE_PUSH`、`SYS_YIELD`
- 加 priority-based scheduling
- 加 task list（tasks[N] 動態註冊）
- 加 systick-based profiling（每 task CPU 用量）
- 從 Cortex-M3 移植到 Cortex-A（用 EL0/EL1 + MMU 取代 MPU）— 這個會大幅升級

## 自我檢核（最終 boss）

- [ ] 我寫了一個 < 800 行能跑的 mini RTOS-lite
- [ ] 我用 PendSV asm 寫了 context switch
- [ ] 我用 SVC 實作了至少 2 個 syscall
- [ ] 我用 MPU 隔離了 task stack
- [ ] 我用 ITM trace 印了 task switch 軌跡
- [ ] 我寫了 debug.md 紀錄一次完整的 GDB debug 過程
- [ ] 我能向別人解釋每一行為什麼這樣寫

完成後請考慮把 firmware push 到自己 GitHub repo，標記為「我學完 ARM」的最終證明。

## 後話

整門 ARM 課到此結束。你從一條 `mov` 指令認 ARM 開始，到自刻一個 RTOS 收尾。**這之中你寫過的每一行 code、debug 過的每一次 hang，都是 ARM 工程師職涯的縮影**。

接下來如果你想繼續：

- 讀 Linux ARM64 boot：`arch/arm64/kernel/head.S`，現在你看得懂
- 讀 OP-TEE：感受 TrustZone 的真實 OS
- 讀 ARM Trusted Firmware-A：看 BL31 是怎麼做的
- 讀 KVM-arm64：看 hypervisor 的真實實作
- 移植 mini RTOS 到 Cortex-A：升級到 MMU + EL 系統

學 ARM 不是學一個 ISA，是學「**怎麼從硬體層思考軟體**」。希望這門課值得你這幾週的時間。

— 課程結束 —
