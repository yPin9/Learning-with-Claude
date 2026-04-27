# Ch 15 — arch/x86/boot/ 從 setup 到 start_kernel

> 目標：實際翻 Linux source `arch/x86/boot/`，跟著 code 走一遍 kernel 早期啟動，知道每個檔案在做什麼。

## 我們在哪裡

第 4 階段 (Kernel) 中段。bootloader 已經把 bzImage 載入、跳到 setup code 了，看 kernel 自己怎麼從這裡走到 `start_kernel()`。

## 目錄速覽

```
arch/x86/boot/
├── header.S            # bzImage 第一個 instruction
├── boot.h              # boot 階段的 header
├── main.c              # real-mode setup 的 C 入口
├── memory.c            # E820 抓 memory map
├── video.c             # VGA mode 設定
├── pm.c                # 切到 protected mode
├── pmjump.S            # jmp far 到 32-bit
├── compressed/
│   ├── head_64.S       # 64-bit entry，做最後切換到 long mode
│   ├── misc.c          # 解壓器
│   ├── eboot.c         # EFI stub
│   └── ...
└── ...
```

兩條 path：

- **BIOS 路徑**：header.S → main.c → pm.c → pmjump.S → compressed/head_64.S → start_kernel
- **EFI 路徑**：compressed/eboot.c → compressed/head_64.S → start_kernel

我們走 BIOS path，遇到 EFI 差別會標出來。

## header.S — 第一個 instruction

`arch/x86/boot/header.S` 是 bzImage 的開頭。

開頭 code：

```asm
; 簡化版
SECTION .bstext

bootsect_start:
#ifdef CONFIG_EFI_STUB
    # MZ header for UEFI (Ch 14 講過)
    .ascii "MZ"
    .skip 6
    ...
#else
    .byte 0xeb, 0x3c        # jmp short 0x3c (real-mode entry skip 過 header)
#endif
```

兩種模式：

- BIOS 載這個 binary 到 `0x07C0:0000`（segment 形式），跳第一條 byte 就是 `jmp short`
- UEFI 載到任意位址，從 PE entry 進，那邊是 `efi_pe_entry`

我們走 BIOS 線。`jmp 0x3c` 跳過去就是 setup header 之後的 real-mode setup code。

real-mode setup 跑完跳到 `start_of_setup` (`main.c`)。

## main.c — real-mode setup 的 C 入口

`arch/x86/boot/main.c` 的 `main()`：

```c
void main(void)
{
    /* First, copy the boot header into the "zeropage" */
    copy_boot_params();

    /* Initialize the early-boot console */
    console_init();

    init_heap();

    /* Make sure we have all the proper CPU support */
    if (validate_cpu()) {
        ...
    }

    /* Tell the BIOS what CPU mode we intend to run in. */
    set_bios_mode();

    /* Detect memory layout */
    detect_memory();

    /* Set keyboard repeat rate (why?) and query the lock flags */
    keyboard_init();

    /* Query Intel SpeedStep (IST) information */
    query_ist();

    /* Query APM information */
    query_apm_bios();

    /* Query EDD information */
    query_edd();

    /* Set the video mode */
    set_video();

    /* Do the last things and invoke protected mode */
    go_to_protected_mode();
}
```

這個 `main` 在 real mode 跑！能呼叫 BIOS INT。每個 helper 對應一個 BIOS 服務：

- `detect_memory()` → INT 15h, AX=E820h（拿 e820 map）
- `query_apm_bios()` → INT 15h, AH=53h
- `query_edd()` → INT 13h, AX=4800h（Enhanced Disk Drive）
- `set_video()` → INT 10h, AH=4Fh (VBE)

這些資訊全寫到 `boot_params` (zeropage)。

最後 `go_to_protected_mode()` — 對，跟 Ch 7 學的一樣，設 GDT、開 A20、CR0.PE = 1、jmp far。

## pm.c + pmjump.S — 切 protected mode

```c
// arch/x86/boot/pm.c
void go_to_protected_mode(void)
{
    /* Hook before leaving real mode */
    if (boot_params.hdr.realmode_swtch) {
        ...
    }

    realmode_switch_hook();

    /* Enable the A20 gate */
    if (enable_a20()) {
        puts("A20 gate not responding, unable to boot...\n");
        die();
    }

    /* Reset coprocessor (IGNNE#) */
    reset_coprocessor();

    /* Mask all interrupts in the PIC */
    mask_all_interrupts();

    /* Actual transition to protected mode... */
    setup_idt();
    setup_gdt();
    protected_mode_jump(boot_params.hdr.code32_start,
                        (u32)&boot_params + (ds() << 4));
}
```

對照 Ch 7 我們手寫的版本，多了：

- `realmode_switch_hook()`：bootloader 可以在這註冊 callback
- `reset_coprocessor()`：reset x87 浮點處理器
- `mask_all_interrupts()`：關 PIC 所有中斷

`protected_mode_jump` 在 `pmjump.S`：

```asm
GLOBAL(protected_mode_jump)
    movl    %edx, %esi      # Pointer to boot_params table
    xorl    %ebx, %ebx
    movw    %cs, %bx
    shll    $4, %ebx
    addl    %ebx, 2f
    ...
    movl    %cr0, %edx
    orb     $1, %dl         # CR0.PE = 1
    movl    %edx, %cr0

    # jmp far
    .byte   0x66, 0xea      # ljmpl opcode
2:  .long   in_pm32         # offset
    .word   __BOOT_CS       # segment selector
ENDPROC(protected_mode_jump)
```

跟 Ch 7 寫的幾乎一模一樣。Ch 7 不是教學示範，是實際 Linux 在做的事。

跳完進入 `in_pm32`，從那裡 `jmp *%eax` (eax = `code32_start`) 跳到 protected-mode kernel — 也就是 **decompressor**。

## compressed/head_64.S — 解壓器入口

`arch/x86/boot/compressed/head_64.S` 是 protected-mode kernel 的入口。它做：

1. 確認在 protected mode、設好 segment
2. 找一個記憶體位址放解壓後的 vmlinux
3. 切到 long mode（如果還沒）
4. 呼叫 `extract_kernel` 解壓
5. 跳到解壓後 vmlinux 的 entry

簡化版：

```asm
ENTRY(startup_64)
    /* 確認 long mode */
    /* 設 stack */
    leaq    boot_stack_end(%rip), %rsp

    /* 算放 kernel 的位址 */
    movq    %rsi, %rdi               # rdi = boot_params
    leaq    (_bss-8)(%rip), %rsi
    leaq    (_bss-8)(%rbx), %rdi
    movq    $_bss, %rcx
    ...

    /* 呼叫解壓器 */
    pushq   %rsi
    movq    %rsi, %rdi               # boot_params
    leaq    boot_heap(%rip), %rsi    # heap area
    leaq    input_data(%rip), %rdx   # compressed input
    movl    input_len(%rip), %ecx
    movq    %rbp, %r8                # output address
    call    extract_kernel
    popq    %rsi

    /* 跳到 vmlinux entry */
    jmp     *%rax
ENDPROC(startup_64)
```

`extract_kernel` 在 `misc.c`，它呼叫合適的解壓函式（gunzip / unxz / unzstd）把 vmlinux 解到 `output_address`。

## extract_kernel 與 KASLR

```c
asmlinkage __visible void *extract_kernel(...)
{
    /* 抓 e820 / memory map */

    /* KASLR：選一個隨機位址放 kernel */
    if (!cmdline_find_option_bool("nokaslr")) {
        choose_random_location(...);
    }

    /* 解壓 */
    debug_putstr("\nDecompressing Linux... ");
    __decompress(input_data, input_len, NULL, NULL,
                 output, output_len, NULL, error);

    /* relocate ELF */
    parse_elf(output);
    handle_relocations(output, output_len, virt_addr);

    return output;
}
```

KASLR (Kernel Address Space Layout Randomization) 在這發生 — 解壓前隨機選位址、解壓後 patch 所有 absolute reference。`/proc/kallsyms` 顯示的位址每次開機都不同就是這個。

關掉 KASLR：cmdline 加 `nokaslr`。debug 時很有用。

## 跳到 vmlinux：start_kernel

解壓 + relocate 完，jmp 到 vmlinux 的 entry。最終呼叫 `start_kernel()` （在 `init/main.c`）。

`start_kernel` 大致流程：

```c
asmlinkage __visible void __init start_kernel(void)
{
    char *command_line;

    set_task_stack_end_magic(&init_task);
    smp_setup_processor_id();
    debug_objects_early_init();

    cgroup_init_early();
    local_irq_disable();
    early_boot_irqs_disabled = true;

    boot_cpu_init();
    page_address_init();
    pr_notice("%s", linux_banner);

    setup_arch(&command_line);              // 機器特定初始化
    add_latent_entropy();
    add_device_randomness(command_line, strlen(command_line));
    setup_per_cpu_areas();

    parse_early_param();
    parse_args(...);                         // parse cmdline

    setup_log_buf(0);
    vfs_caches_init_early();
    sort_main_extable();
    trap_init();
    mm_init();                               // memory management
    ftrace_init();

    sched_init();                            // scheduler
    radix_tree_init();
    workqueue_init_early();

    rcu_init();                              // RCU
    trace_init();

    early_irq_init();
    init_IRQ();
    tick_init();
    rcu_init_nohz();
    init_timers();

    time_init();
    perf_event_init();
    profile_init();

    local_irq_enable();
    lockdep_info();

    setup_per_cpu_pageset();
    numa_policy_init();
    sched_clock_init();

    fs_init();
    ...
    rest_init();                             // 啟動 init thread (PID 1)
}
```

200+ 行，每行都是一個子系統的 init。重點 milestone：

- `setup_arch()`：解析 boot_params、設 page tables、認 CPU
- `mm_init()`：memory management 上線（slab allocator、buddy allocator）
- `sched_init()`：scheduler 可用
- `init_IRQ()`：中斷可以接收
- `time_init()`：timer
- `local_irq_enable()`：開中斷（這之前都是關著的）
- `fs_init()`：VFS 起來
- `rest_init()`：建立 PID 1 (kernel thread)、然後 idle

## rest_init 與 PID 1

```c
noinline void __ref rest_init(void)
{
    struct task_struct *tsk;
    int pid;

    /* 建立 init 這個 kernel thread */
    pid = kernel_thread(kernel_init, NULL, CLONE_FS);

    /* 建立 kthreadd */
    pid = kernel_thread(kthreadd, NULL, CLONE_FS | CLONE_FILES);

    /* 變成 idle thread */
    cpu_startup_entry(CPUHP_ONLINE);
}
```

`kernel_init`（PID 1，但還是 kernel thread）：

```c
static int __ref kernel_init(void *unused)
{
    kernel_init_freeable();          // 等更多東西 ready
    free_initmem();                  // 釋放 __init code 佔用的 page

    /* 試著找 init binary */
    if (execute_command) {
        ret = run_init_process(execute_command);
        ...
    }

    if (!try_to_run_init_process("/sbin/init") ||
        !try_to_run_init_process("/etc/init") ||
        !try_to_run_init_process("/bin/init") ||
        !try_to_run_init_process("/bin/sh"))
        return 0;

    panic("No working init found.");
}
```

`run_init_process` → `kernel_execve` → exec 那個 binary。**這一刻 PID 1 從 kernel thread 變成 userspace process**。

如果在 initramfs 裡，找的是 `/init`（不是 `/sbin/init`）。

## 一個常見誤解：「kernel 一啟動就有 driver」

不是。`start_kernel` 只起核心子系統。Driver 大多在 `do_initcalls()`（`kernel_init_freeable` 裡）跑：

```c
static void __init do_initcalls(void)
{
    int level;
    for (level = 0; level < ARRAY_SIZE(initcall_levels) - 1; level++)
        do_initcall_level(level);
}
```

每個 driver 用 `module_init()` / `subsys_initcall()` 註冊 callback，按 level 順序跑。整個 driver init 在 `kernel_init` 裡 sequential 跑完，才 exec /sbin/init。

## 動手練習

**1. 翻 source 對照**

```bash
# clone Linux source（很大，淺 clone）
git clone --depth 1 https://github.com/torvalds/linux.git
cd linux/arch/x86/boot

# 看 main.c
less main.c

# 看 header.S 找 setup_header 結構
less header.S

# 看 head_64.S
less compressed/head_64.S
```

**2. dmesg 對照 start_kernel 順序**

```bash
sudo dmesg | head -50
```

你會看到很多 `Initializing ...`、`Setting up ...`，順序對應到 `start_kernel` 的呼叫順序。

**3. 看 KASLR 隨機**

```bash
# 兩次開機後比對
sudo grep "_text" /proc/kallsyms
# 每次的 _text 位址不同
```

**4. 試 nokaslr**

在 GRUB 裡按 `e` 編輯 entry，cmdline 加 `nokaslr`，按 `Ctrl-X` 開機。再跑上面 grep，這次 _text 位址固定。

## 自我檢核

- [ ] 講得出 BIOS 路徑：header.S → main.c → pm.c → head_64.S → start_kernel
- [ ] 知道 main.c 在 real mode 跑、可以呼叫 BIOS INT
- [ ] 講得出解壓器在 `compressed/` 下、KASLR 在這發生
- [ ] 知道 `start_kernel` → `rest_init` → `kernel_init` → exec `/init` 或 `/sbin/init`
- [ ] 翻過 Linux source，對照 dmesg

下一章看 cmdline — 怎麼傳、怎麼影響 kernel、有哪些常用參數。

→ [Ch 16 kernel cmdline](./16-kernel-cmdline.md)
