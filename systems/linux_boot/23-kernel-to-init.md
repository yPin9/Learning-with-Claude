# Ch 23 — 從 kernel 到第一個 process

> **目標**：理解 kernel 如何從「純 kernel 態」過渡到「執行第一個 userspace process」——rest_init、kernel_init、PID 1 的誕生、`execve` 第一個 init、以及著名的 "Unable to mount root fs" panic 的成因。這是 kernel 態到 userspace 的關鍵跳躍。

> **環境**：Linux kernel 6.x。承接 Ch 22（start_kernel）。

## 為什麼「第一個 process」這麼特別？

`start_kernel`（Ch 22）初始化完所有子系統後，kernel 面臨一個哲學性的轉折：它是 kernel，但 kernel 不能憑空跑使用者程式——使用者程式（process）需要被「執行」，而執行第一個 process 之前，系統裡一個 process 都沒有。

這是個雞生蛋問題：process 由 fork 現有 process 產生，但第一個 process 從哪來？kernel 必須「手工」製造第一個 process（PID 1），然後 `execve` 它變成 init。這個過程是「kernel 態 → userspace」的關鍵跳躍。理解它，你就懂整個開機接力的倒數第二棒（kernel → init）。

## 先建立直覺：kernel 親手生下第一個 process

```
雞生蛋問題：process 從 fork 來，第一個 process 從哪來？

  start_kernel 完成所有子系統初始化
        │
  rest_init()：
    kernel 親手建立第一個 process（用 kernel_thread）
    這個 thread 跑 kernel_init 函式
        │
  kernel_init（在新建的 PID 1 context 裡跑）：
    1. 掛載 root 檔案系統
    2. 找到 init 程式（/sbin/init 等）
    3. execve 它 → PID 1 從「kernel thread」變成「userspace init」
        │
  → 第一個 userspace process（PID 1, init）誕生
    之後所有 process 都是它的後代（fork）
```

kernel 用 `kernel_thread` 親手造出第一個 process，這個 process 一開始跑 kernel code（kernel_init），最後 `execve` 變成 userspace 的 init。這是「kernel 製造 userspace 的起點」。

## rest_init：建立 PID 1 和 PID 2

`start_kernel` 的最後呼叫 `rest_init`，它建立系統最初的兩個 thread：

```c
// init/main.c — rest_init（簡化）
static noinline void __ref rest_init(void)
{
    // 建立 PID 1：未來的 init（先跑 kernel_init）
    pid = user_mode_thread(kernel_init, NULL, CLONE_FS);
    //    ↑ 這會成為 PID 1

    // 建立 PID 2：kthreadd（管理所有 kernel thread）
    pid = kernel_thread(kthreadd, NULL, CLONE_FS | CLONE_FILES);
    //    ↑ 這會成為 PID 2，管理 kernel 內部的 threads

    // 原本的開機路徑變成 idle thread（PID 0）
    cpu_startup_entry(CPUHP_ONLINE);
    // → 變成 idle，沒事做時跑這個（CPU 空閒時的迴圈）
}
```

```
最初的三個特殊 thread：
  PID 0：idle thread（原本的開機路徑變成的，CPU 空閒時跑）
  PID 1：init（先跑 kernel_init，最後 execve 成 userspace init）
  PID 2：kthreadd（kernel thread 的祖先，管理 kworker 等）
        │
  之後：
    所有 userspace process 是 PID 1 的後代
    所有 kernel thread 是 PID 2 的後代
```

> 三個特殊 PID 的分工很優雅：PID 0（idle，CPU 沒事做時跑）、PID 1（init，所有 userspace 的祖先）、PID 2（kthreadd，所有 kernel thread 的祖先）。`ps` 看到的 `[kthreadd]`（PID 2）和它的 kernel thread 後代（`[kworker/...]`），以及 init/systemd（PID 1）和它的 userspace 後代，就是這個結構。

## kernel_init：掛 root + execve init

PID 1 一開始跑 `kernel_init`（kernel code），它做兩件大事：掛載 root、執行 init：

```c
// init/main.c — kernel_init（簡化）
static int __ref kernel_init(void *unused)
{
    // 1. 完成剩餘初始化（驅動等）
    kernel_init_freeable();

    // 2. 釋放 __init 記憶體（Ch 22）
    free_initmem();

    // 3. 嘗試執行 init 程式
    //    （此時 root 已掛載——initramfs 或真正的 root）
    if (execute_command) {
        // 如果 cmdline 有 init=xxx，用指定的
        ret = run_init_process(execute_command);
    }

    // 否則依序嘗試標準路徑
    if (!run_init_process("/sbin/init") ||
        !run_init_process("/etc/init") ||
        !run_init_process("/bin/init") ||
        !run_init_process("/bin/sh"))      // 最後退路：shell
        return 0;

    // 全部失敗 → panic
    panic("No working init found.  Try passing init= option to kernel. ...");
}
```

`kernel_init` 嘗試 `execve` 一系列標準的 init 路徑（`/sbin/init`、`/etc/init`、`/bin/init`、`/bin/sh`）。`execve` 成功後，PID 1 的記憶體被 init 程式取代——它從「跑 kernel code 的 kernel thread」變成「跑 userspace init 的 process」。這是 kernel 態到 userspace 的跳躍。

## root 檔案系統：execve 之前的前提

`kernel_init` 要 `execve("/sbin/init")`，但 `/sbin/init` 在 root 檔案系統上——所以 root 必須**先掛載好**。掛 root 的來源：

```
kernel 掛 root 的兩種情況：

  情況 A：有 initramfs（現代標準，Ch 24）
    kernel 把 initramfs 解開成 rootfs（在記憶體）
    execve initramfs 的 /init
    （initramfs 的 /init 之後再掛真正的 root，switch_root，Ch 24-25）

  情況 B：直接掛真正的 root（無 initramfs，少見）
    kernel 用 cmdline 的 root= 參數（Ch 20）找到 root 裝置
    掛載它
    execve 真正 root 上的 /sbin/init
        │
  現代發行版幾乎都用情況 A（initramfs）
```

`root=` 參數（bootloader 傳的，Ch 20）告訴 kernel root 在哪。但要掛載 root 可能需要驅動（磁碟控制器、檔案系統、LVM/LUKS）——這些驅動如果不在 kernel 內，就需要 initramfs 提供（Ch 24 的核心動機）。

## 著名的 panic："Unable to mount root fs"

kernel 開機最著名的失敗：

```
[    X.XXXXXX] VFS: Cannot open root device "sda2" or unknown-block(0,0): error -6
[    X.XXXXXX] Please append a correct "root=" boot option; here are the available partitions:
[    X.XXXXXX] Kernel panic - not syncing: VFS: Unable to mount root fs on unknown-block(0,0)
```

成因：

```
"Unable to mount root fs" 的常見成因：
  1. root= 參數錯（指向不存在的裝置）
  2. 掛 root 需要的驅動不在 kernel 也不在 initramfs
     （如 NVMe 驅動缺失，找不到 NVMe 磁碟）
  3. initramfs 損壞或缺失
  4. root 檔案系統的驅動缺失（如 root 在 btrfs 但沒 btrfs 驅動）
        │
  kernel 找不到/掛不上 root → 沒有 /sbin/init 可 execve → panic
```

> 這個 panic 是開機 debug 的經典。它的本質是「kernel 沒有 root 就沒有 init 可跑」。看到它，檢查：root= 參數對不對（Ch 20）、initramfs 有沒有需要的驅動（Ch 24）、root 的檔案系統/儲存驅動齊不齊。這直接連到 initramfs 存在的理由（Ch 24）——initramfs 就是為了確保「掛 root 需要的驅動都在」。

## 故意對照：有無 initramfs 的開機

```
無 initramfs（要 root 的驅動都編進 kernel）：
  kernel → 直接掛 root（root= 指定）→ execve /sbin/init
  問題：所有可能的 root 驅動都要編進 kernel → kernel 肥大
        且無法處理 LVM/LUKS（需要 userspace 工具組裝）

有 initramfs（現代標準）：
  kernel → 掛 initramfs（記憶體 rootfs）→ execve initramfs 的 /init
  → initramfs 的 /init 載入驅動、組 LVM/解密 LUKS、掛真正 root
  → switch_root 到真正 root → execve 真正的 /sbin/init
        │
  initramfs 讓「掛 root 的複雜邏輯」在 userspace 做，kernel 保持精簡
```

這個對照是 Ch 24（initramfs）的引子：initramfs 解決「掛 root 需要驅動，但驅動太多不能全編進 kernel，且 LVM/LUKS 需要 userspace 工具」的問題。kernel → init 的這一棒，現代幾乎都經過 initramfs 中轉。

## 踩雷集錦

1. **以為 PID 1 一開始就是 userspace**：PID 1 先跑 kernel code（kernel_init），execve 後才變 userspace init。它是「kernel 親手造的，後來變 userspace」

2. **混淆 PID 0/1/2**：PID 0 = idle、PID 1 = init、PID 2 = kthreadd。三個特殊 thread 各有分工

3. **"Unable to mount root fs" 只看 root= 參數**：可能是 root= 錯，但也可能是缺驅動（initramfs 沒有 root 的儲存/檔案系統驅動）。兩個方向都要查

4. **以為無 initramfs 不能開機**：可以（情況 B），但要把所有 root 驅動編進 kernel，且不能用 LVM/LUKS。現代用 initramfs 是為了靈活和精簡

5. **panic 後以為 kernel 壞了**："Unable to mount root fs" 通常不是 kernel bug，是 root/initramfs 配置問題。檢查 root=、initramfs、驅動

## 進階：init= 參數與 PID 1 的替換

你能用 `init=` kernel 參數指定 PID 1 跑什麼，這是強大的救援和 debug 工具：

```bash
# 在 GRUB 編輯開機項（按 e），在 linux 行加：
init=/bin/bash
# → PID 1 不是 systemd，而是直接一個 bash shell
# → 跳過所有 init 系統，直接進 root shell（救援用）

# 其他用途：
init=/bin/sh        # 最小 shell（systemd 壞掉時救援）
rd.break            # systemd/dracut 的：在 initramfs 階段停下（更早的救援）
```

```
init= 的救援場景：
  系統開機失敗（init/systemd 壞了、忘記 root 密碼）
        │
  在 GRUB 加 init=/bin/bash
        │
  → 跳過正常開機，直接 PID 1 = bash
  → 一個 root shell（可以改密碼、修設定、reinstall init）
        │
  注意：此時 root 可能是唯讀的，要 mount -o remount,rw / 才能改
```

> `init=/bin/bash` 是 Linux 救援的瑞士刀。系統開不了機、忘記 root 密碼，在 GRUB 加這個參數，PID 1 直接變 bash，繞過所有 init 邏輯給你 root shell。這展示了「PID 1 是 kernel execve 的任意程式」這個本質——kernel 不在乎 PID 1 是 systemd 還是 bash，它只是 execve cmdline 指定的東西。理解 kernel → init 的機制，這個救援技巧就很自然。

## 動手練習

1. 看三個特殊 PID：`ps -p 1`（init/systemd）、`ps -p 2`（kthreadd）、`ps aux | grep '\[' | head`（kernel threads，PID 2 的後代）。確認 PID 1 的祖先結構

2. 看 init 是什麼：`ls -l /sbin/init`（通常 symlink 到 systemd）、`cat /proc/1/comm`（PID 1 的名字）

3. 救援練習（VM）：在 GRUB 開機時按 e 編輯，linux 行加 `init=/bin/bash`，開機進入 root shell。`mount -o remount,rw /` 後可以改東西。重開回正常

4. 製造 root 問題（VM）：故意改 GRUB 的 root= 參數成不存在的裝置，看 "Unable to mount root fs" panic。改回修復

## 本章重點整理

- kernel 用 kernel_thread 親手造第一個 process（PID 1），解決「process 從 fork 來，第一個從哪來」的雞生蛋
- rest_init 建立 PID 0（idle）、PID 1（init，先跑 kernel_init）、PID 2（kthreadd，kernel thread 祖先）
- kernel_init 掛載 root 後 execve init（/sbin/init 等），PID 1 從 kernel thread 變 userspace init
- "Unable to mount root fs" panic：kernel 掛不上 root（root= 錯 / 缺驅動 / initramfs 問題）→ 沒 init 可跑
- init= 參數能指定 PID 1 跑什麼（如 init=/bin/bash 救援，繞過正常開機給 root shell）

## 自我檢核

- [ ] 能解釋 kernel 怎麼解決「第一個 process 從哪來」的雞生蛋問題
- [ ] 知道 PID 0/1/2 各是什麼、分工為何
- [ ] 知道 PID 1 怎麼從 kernel thread（kernel_init）變成 userspace init（execve）
- [ ] 能說出 "Unable to mount root fs" panic 的幾種成因
- [ ] 知道 init=/bin/bash 怎麼用於救援，以及它為什麼有效

## 延伸閱讀

### 官方文件

- **[Linux kernel: init/main.c (rest_init, kernel_init)](https://elixir.bootlin.com/linux/latest/source/init/main.c)**
  - **讀哪裡**：`rest_init`、`kernel_init`、`run_init_process` 函式
  - **學什麼**：第一個 process 誕生的權威原始碼
  - **前提**：本章 + C

### 部落格 / 文章

- **[Linux Inside: First steps after start_kernel](https://0xax.gitbooks.io/linux-insides/content/)** — 0xax
  - **讀哪裡**：rest_init 和第一個 process 那部分
  - **學什麼**：原始碼層級的 kernel → init 過程
  - **前提**：本章

### 書籍

- **《How Linux Works, 3rd ed.》— Ch 6 (How User Space Starts)** — Brian Ward
  - **這本書的定位**：用平易方式講 kernel → init → 系統服務
  - **讀哪幾章**：Ch 6，和本章 + Ch 26（init/systemd）直接對應
  - **前提**：無

→ [Ch 24 initramfs / initrd 機制](./24-initramfs.md)
