# Ch 35 — Context Switch 實作：arch/riscv 的 switch_to() 是怎麼工作的

> 目標：理解 RV64 context switch 需要保存哪些暫存器；能讀懂 `__switch_to()` 的 assembly；能用 GDB + QEMU 追蹤一次完整的 context switch。

---

## 35.1 Context Switch 的定義

Context switch 發生在 scheduler 決定暫停當前 task，改跑另一個 task 的時候。從 CPU 的角度，「保存狀態」等於把所有 CPU 狀態（暫存器值）存到記憶體，然後把下一個 task 的狀態從記憶體載入。

```
Task A（正在跑）
  |
  | schedule() 被呼叫（timer interrupt 或主動 yield）
  v
switch_to(A, B)：
  保存 A 的 callee-saved 暫存器 → A->thread
  載入 B 的 callee-saved 暫存器 ← B->thread
  ret（跳到 B 的 ra 指向的地方）
  |
  v
Task B（繼續跑）
```

---

## 35.2 需要保存哪些暫存器

不是所有暫存器都需要保存：

**不需要保存（caller-saved）**：
- t0–t6（臨時暫存器）
- a0–a7（argument 暫存器）

原因：`switch_to` 是一個 C 函式呼叫（透過 `schedule()`），caller 必須在呼叫 `schedule()` 前保存自己用到的 caller-saved 暫存器。Scheduler 不用操心。

**需要保存（callee-saved）**：

```
整數暫存器：
  ra（x1）    return address——回到 schedule() 之後要執行什麼
  sp（x2）    stack pointer——切回來時要用正確的 stack
  s0–s11      saved registers

FP 暫存器（若 task 使用 FPU）：
  fs0–fs11    FP saved registers
```

---

## 35.3 struct thread_struct

```c
// arch/riscv/include/asm/processor.h（簡化）
struct thread_struct {
    /* Callee-saved registers */
    unsigned long ra;
    unsigned long sp;    /* kernel stack pointer */
    unsigned long s[12]; /* s0–s11 */

    /* FPU state */
    struct __riscv_d_ext_state fstate;
    unsigned long bad_cause;
};
```

`task_struct->thread` 就是 `struct thread_struct`，存放了 task 不在 CPU 上跑時的 CPU 狀態。

---

## 35.4 switch_to() 和 __switch_to()

```c
// arch/riscv/include/asm/switch_to.h（簡化）
#define switch_to(prev, next, last)  \
    do {                              \
        struct task_struct *_prev = (prev);  \
        struct task_struct *_next = (next);  \
        if (has_fpu())               \
            __switch_to_fpu(_prev, _next);  \
        (last) = __switch_to(_prev, _next); \
    } while (0)
```

真正的切換在 `__switch_to()`，這是一個 assembly 函式：

```asm
# arch/riscv/kernel/entry.S（簡化）
# __switch_to(prev, next)
# a0 = prev task_struct 指標
# a1 = next task_struct 指標
# 返回值 a0 = prev（讓 last = prev）

__switch_to:
    # 把 offset 計算出來（TASK_THREAD_RA = offsetof(task_struct, thread.ra)）
    li    a4, TASK_THREAD_RA    # = offsetof(task_struct, thread.ra)

    # 保存 prev 的 callee-saved 暫存器
    add   a3, a0, a4            # a3 = &prev->thread.ra
    sd    ra,  TASK_THREAD_RA_RA(a3)     # 保存 ra
    sd    sp,  TASK_THREAD_SP(a3)        # 保存 sp
    sd    s0,  TASK_THREAD_S0(a3)        # 保存 s0
    sd    s1,  TASK_THREAD_S1(a3)
    sd    s2,  TASK_THREAD_S2(a3)
    sd    s3,  TASK_THREAD_S3(a3)
    sd    s4,  TASK_THREAD_S4(a3)
    sd    s5,  TASK_THREAD_S5(a3)
    sd    s6,  TASK_THREAD_S6(a3)
    sd    s7,  TASK_THREAD_S7(a3)
    sd    s8,  TASK_THREAD_S8(a3)
    sd    s9,  TASK_THREAD_S9(a3)
    sd    s10, TASK_THREAD_S10(a3)
    sd    s11, TASK_THREAD_S11(a3)

    # 載入 next 的 callee-saved 暫存器
    add   a3, a1, a4            # a3 = &next->thread.ra
    ld    ra,  TASK_THREAD_RA_RA(a3)
    ld    sp,  TASK_THREAD_SP(a3)
    ld    s0,  TASK_THREAD_S0(a3)
    ld    s1,  TASK_THREAD_S1(a3)
    ld    s2,  TASK_THREAD_S2(a3)
    # ... （s3–s11）

    # 更新 current task pointer（tp 暫存器在 RISC-V Linux 指向 current task）
    move  tp, a1                # tp = next task_struct

    # a0 = prev（return value，讓 last = prev）
    move  a0, a0                # 其實不用動，a0 本來就是 prev

    ret                         # 跳到 next 的 ra（next 上次被切走時保存的 ra）
```

`ret` 之後，CPU 跳到 `ra`——這是 next task 上次呼叫 `schedule()` 然後進入 `__switch_to()` 時保存的 ra，也就是 `schedule()` 的 call site 的下一條指令。從 next task 的角度，`schedule()` 正常返回了。

---

## 35.5 ra 指向哪裡：兩種情況

**情況 1：被 context switch 過的 task（曾經跑過）**

ra 保存的是 `schedule()` 內部呼叫 `switch_to` 時的返回地址：`schedule_tail()` 或 `schedule()` 的下一步。task 醒來後繼續在 `schedule()` 裡面跑，然後正常返回到 caller。

**情況 2：剛 fork 的 new task（第一次跑）**

`copy_thread()`（arch/riscv/kernel/process.c）在 fork 時初始化 new task 的 thread_struct：

```c
// copy_thread 的關鍵部分（簡化）
childregs = task_pt_regs(p);
// 設定 task 第一次被 schedule 時的 entry point
p->thread.ra = (unsigned long)ret_from_fork;
p->thread.sp = (unsigned long)childregs;
```

`ret_from_fork` 是一個 assembly 函式，它：
1. 呼叫 `schedule_tail()`（完成 fork 的清理）
2. 如果是 kernel thread：呼叫 thread function
3. 如果是 user task：呼叫 `ret_from_exception()`，最終 sret 回到 user mode

---

## 35.6 FPU Context 的 Lazy Save

FPU 暫存器（f0–f31）很佔資源，每次 context switch 都保存 32 × 8 = 256 bytes。Linux 用 **lazy save** 策略：

```
sstatus.FS（Floating-Point Status）欄位：
  00（Off）   : FPU 不可用，任何 FP 指令都 trap
  01（Initial）: FPU clean，暫存器是初始值
  10（Clean） : FPU clean，暫存器已保存/恢復過
  11（Dirty） : FPU dirty，暫存器被修改過但還沒保存

策略：
  task 剛建立：sstatus.FS = Off（FPU 關閉）
  task 執行 FP 指令：觸發 Illegal instruction exception
  Handler：把 FS 設成 Initial，重新執行 FP 指令
  FP 指令執行後：FS 自動設成 Dirty
  Context switch：
    if (prev->FS == Dirty) → 保存 FP 暫存器
    if (next->FS == Dirty) → 恢復 FP 暫存器
    else → 把 sstatus.FS 設成 Off，讓下次 FP 指令觸發 trap
```

大多數 task 不用 FPU（純計算型 kernel thread），lazy save 省去了大量不必要的 FP 暫存器儲存。

---

## 35.7 用 GDB + QEMU 追蹤 Context Switch

```bash
# 啟動 QEMU，等待 GDB 連線
qemu-system-riscv64 -M virt -bios fw_dynamic.bin -kernel vmlinux \
  -append "console=ttyS0 nokaslr" -nographic -S -gdb tcp::1234

# 另一個終端，啟動 GDB
riscv64-linux-gnu-gdb vmlinux
(gdb) target remote :1234
(gdb) hbreak __switch_to          # hardware breakpoint
(gdb) continue

# 觸發 breakpoint 後：
(gdb) info registers              # 看 a0（prev）, a1（next）
(gdb) p/x ((struct task_struct*)$a0)->comm   # 看 prev 的 name
(gdb) p/x ((struct task_struct*)$a1)->comm   # 看 next 的 name
(gdb) stepi                       # 單步追蹤 assembly
```

---

## 自我檢核

- [ ] 能說出 context switch 需要保存哪些暫存器（callee-saved：ra, sp, s0–s11）
- [ ] 知道 caller-saved 暫存器為什麼不用 context switch 保存
- [ ] 能說出 new task（剛 fork）的 ra 初始值指向哪裡（ret_from_fork）
- [ ] 理解 FPU lazy save 的機制（FS bit，Off/Dirty 狀態）
- [ ] 能用 GDB + QEMU 在 __switch_to 設 breakpoint

→ [Ch 36 — QEMU virt 跑 Linux 實作](36-qemu-linux-practice.md)
