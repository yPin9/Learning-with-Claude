# 練習 A — 手寫最小 KVM hypervisor + 攔截 MMIO exit

> **目標**：從零寫一個 C 語言 KVM hypervisor，讓 guest 對未映射的 GPA（Guest Physical Address）發出 MMIO write/read，host 攔截並模擬一個假 device，最後親眼看見「長度未檢查」如何讓 buffer 溢位。

> **環境**：Linux x86-64, `/dev/kvm`, gcc（需要 `CAP_SYS_ADMIN` 或 `/dev/kvm` group 權限）

---

## 背景與動機

Ch 8 介紹了 KVM 的 EPT（Extended Page Table）機制與 VM exit 的分類。MMIO exit 是 VM escape 研究裡最重要的一條路：guest 寫到一個沒有對應 HPA（Host Physical Address）的 GPA，EPT violation 觸發，KVM 回到 host userspace，把控制權交給 QEMU（或我們自己的 hypervisor loop）。

真實的 QEMU device 就是這樣工作的——MemoryRegion callback、`mr->ops->read/write`，全部都在處理這條 exit path。本練習讓我們親手建立這個路徑，不靠 QEMU 抽象層，直接操作 `/dev/kvm` ioctl。

這對後面的章節至關重要：
- **Ch 9**：QEMU 的 main loop 就是一個更複雜的「等 KVM_RUN 回來，判斷 exit reason，dispatch 給 device」循環。
- **Ch 11**：virtio-net、e1000 的 MMIO handler 都有歷史上的邊界問題，本練習的 Task 3 是它們漏洞的最小原型。
- **Ch 15**：跨 device buffer overflow 的 VM escape PoC，根源就在未驗證 guest 控制的長度。

練習分三個任務，遞進加深：Task 1 攔截 MMIO exit，Task 2 讓 device 有狀態（可讀可寫的暫存器），Task 3 故意做出邊界漏洞再修復它。

---

## 任務規格

### Task 1 — 攔截自訂 MMIO 範圍

**條件**：
- 用 `KVM_SET_USER_MEMORY_REGION` 只映射 GPA `0x0000`–`0x1FFF`（guest 的程式碼在這裡）。
- GPA `0x4000`–`0x4FFF` 刻意不映射，使其成為 MMIO 空洞。

**Guest 程式碼**（16-bit real mode）：
```
mov ax, 0xDEAD
mov [0x4000], ax   ; 觸發 MMIO write
hlt
```
對應位元組：`0xB8, 0xAD, 0xDE, 0xA3, 0x00, 0x40, 0xF4`

**Host 行為**：偵測到 `KVM_EXIT_MMIO` 且 `is_write == 1`，印出：
```
[host] MMIO write: addr=0x4000, len=2, data=0xdead
```
然後繼續 `KVM_RUN`，直到 `KVM_EXIT_HLT`。

**驗收標準**：
- hypervisor 正常啟動、不 segfault。
- 印出正確的 addr/len/data。
- guest 在 HLT 時程式正常退出。

---

### Task 2 — 模擬「只有一個暫存器的假 device」

**假 device 規格**：
- GPA `0x4000`：唯一暫存器 `fake_reg`（host 端 `uint16_t` 變數）。
- MMIO write → `fake_reg = data`
- MMIO read → 把 `fake_reg` 填入 `kvm_run->mmio.data[]`，讓 guest 讀到正確值。

**Guest 程式碼**：
1. 寫 `0x1234` 到 `0x4000`
2. 讀回 `0x4000` 到 `bx`
3. 比較是否等於 `0x1234`（可用 `cmp bx, 0x1234` + 條件 HLT）

**驗收標準**：
- host 印出 write log，緊接著印出 read log。
- guest 讀回值等於寫入值（無需在 guest 端做驗證，host log 可見即可）。

---

### Task 3（加分）— 假 device 有「長度欄位 + buffer」，展示未檢查長度時的邊界問題

**假 device 擴充規格**：
- GPA `0x4000`（offset 0）：`len` 欄位（guest 寫入想複製的長度）
- GPA `0x4004`（offset 4）：`buf[16]`（guest 寫入資料，host 執行 `memcpy`）

**有漏洞的 host 處理**：
```c
memcpy(dev.buf, kvm_run->mmio.data, dev.len);  // dev.len 未上界
```

**修復版**：
```c
size_t safe_len = dev.len > sizeof(dev.buf) ? sizeof(dev.buf) : dev.len;
memcpy(dev.buf, kvm_run->mmio.data, safe_len);
```

**驗收標準**：
- 程式碼清楚標示漏洞路徑（加 `// BUG:` 注解）。
- 修復版有對應注解（`// FIX:`）。
- 不需要真的觸發溢位崩潰，能解釋「guest 寫 `len=0xFF` 時會發生什麼」即可。

---

## 期望輸出範例

Task 1 執行後終端輸出：
```
[host] KVM fd=3, VM fd=4, vCPU fd=5
[host] guest_mem mapped at host 0x7f3a12000000
[host] vCPU registers set (real mode, CS:IP=0x0000:0x0000)
[host] KVM_RUN...
[host] MMIO write: addr=0x4000, len=2, data=0xdead
[host] KVM_RUN...
[host] KVM_EXIT_HLT — guest halted, exit normally
```

Task 2 額外輸出（在 HLT 之前）：
```
[host] MMIO write: addr=0x4000, len=2, data=0x1234  -> fake_reg=0x1234
[host] MMIO read:  addr=0x4000, len=2               -> returning fake_reg=0x1234
```

---

## 實作步驟

### Step 1 — 建立 KVM 基礎框架

打開 `/dev/kvm`，建立 VM，建立 vCPU：

```c
int kvm_fd  = open("/dev/kvm", O_RDWR | O_CLOEXEC);
int vm_fd   = ioctl(kvm_fd, KVM_CREATE_VM, 0);
int vcpu_fd = ioctl(vm_fd, KVM_CREATE_VCPU, 0);
```

取得 `kvm_run` 的 mmap 大小，然後 mmap vcpu_fd：

```c
int kvm_run_size = ioctl(kvm_fd, KVM_GET_VCPU_MMAP_SIZE, 0);
struct kvm_run *run = mmap(NULL, kvm_run_size,
                           PROT_READ | PROT_WRITE, MAP_SHARED, vcpu_fd, 0);
```

這個 `run` 結構在每次 `KVM_RUN` 返回後告訴我們 exit reason。

---

### Step 2 — 分配 guest 記憶體，刻意留 MMIO 空洞

```c
#define GUEST_MEM_SIZE  0x2000   // 只映射 0x0000-0x1FFF
void *guest_mem = mmap(NULL, GUEST_MEM_SIZE,
                       PROT_READ | PROT_WRITE,
                       MAP_SHARED | MAP_ANONYMOUS, -1, 0);

struct kvm_userspace_memory_region region = {
    .slot            = 0,
    .flags           = 0,
    .guest_phys_addr = 0x0000,
    .memory_size     = GUEST_MEM_SIZE,
    .userspace_addr  = (uint64_t)guest_mem,
};
ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, &region);
```

注意：我們只映射到 `0x1FFF`。GPA `0x4000` 完全不在任何 region 裡，所以 guest 存取它時 EPT violation 發生，KVM 回傳 `KVM_EXIT_MMIO`。

把 guest 位元組複製進 `guest_mem`：

```c
uint8_t guest_code[] = { 0xB8, 0xAD, 0xDE,  // mov ax, 0xDEAD
                          0xA3, 0x00, 0x40,  // mov [0x4000], ax
                          0xF4 };            // hlt
memcpy(guest_mem, guest_code, sizeof(guest_code));
```

---

### Step 3 — 設定 vCPU 暫存器（16-bit real mode）

16-bit real mode 是 KVM vCPU 預設狀態最接近的模式。CS:IP = 0:0，DS base = 0，這樣 `mov [0x4000], ax` 的線性位址就是 `DS.base + 0x4000 = 0x4000`，直接對應 GPA `0x4000`。

```c
struct kvm_sregs sregs;
ioctl(vcpu_fd, KVM_GET_SREGS, &sregs);
// real mode: CS selector=0, base=0, limit=0xFFFF, CS.db=0 (16-bit)
sregs.cs.selector = 0;
sregs.cs.base     = 0;
ioctl(vcpu_fd, KVM_SET_SREGS, &sregs);

struct kvm_regs regs = { .rip = 0, .rflags = 0x2 };
ioctl(vcpu_fd, KVM_SET_REGS, &regs);
```

關鍵：`sregs.cs.base = 0` 確保 IP 偏移直接等於實體 GPA。`rflags` 的 bit 1 永遠要是 1（x86 保留位）。

---

### Step 4 — 主迴圈：處理 MMIO exit

```c
uint16_t fake_reg = 0;

while (1) {
    ioctl(vcpu_fd, KVM_RUN, 0);

    switch (run->exit_reason) {
    case KVM_EXIT_MMIO: {
        uint64_t addr = run->mmio.phys_addr;
        uint32_t len  = run->mmio.len;

        if (run->mmio.is_write) {
            uint64_t data = 0;
            memcpy(&data, run->mmio.data, len);
            printf("[host] MMIO write: addr=0x%lx, len=%u, data=0x%lx\n",
                   addr, len, data);
            if (addr == 0x4000 && len == 2)
                fake_reg = (uint16_t)data;
        } else {
            printf("[host] MMIO read:  addr=0x%lx, len=%u"
                   " -> returning 0x%04x\n", addr, len, fake_reg);
            memcpy(run->mmio.data, &fake_reg, len);
        }
        break;
    }
    case KVM_EXIT_HLT:
        printf("[host] KVM_EXIT_HLT — exit normally\n");
        goto done;
    default:
        fprintf(stderr, "[host] unexpected exit: %d\n", run->exit_reason);
        goto done;
    }
}
done:
```

MMIO read 的關鍵：在 `is_write == 0` 時，你必須把回傳值寫進 `run->mmio.data[]`，KVM 才會把這個值注射回 guest 暫存器。

---

### Step 5 — Task 3：加入 len + buf device，展示邊界漏洞

在 host dispatch 裡加一個小型 device 結構：

```c
struct fake_dev {
    uint8_t  len;
    uint8_t  buf[16];
} dev = {0};

// GPA 0x4000: len register
if (addr == 0x4000 && run->mmio.is_write) {
    dev.len = (uint8_t)data;
    printf("[host] dev.len set to %u\n", dev.len);
}

// GPA 0x4004: buf register
if (addr == 0x4004 && run->mmio.is_write) {
    // BUG: dev.len 未上界，guest 可控
    memcpy(dev.buf, run->mmio.data, dev.len);
    // FIX: size_t safe = dev.len > sizeof(dev.buf) ? sizeof(dev.buf) : dev.len;
    //      memcpy(dev.buf, run->mmio.data, safe);
}
```

當 guest 先寫 `len=0xFF`（255），再寫 buf，`memcpy(dev.buf, ..., 255)` 超出 `buf[16]`，覆蓋 `fake_dev` 後方的 host stack 或 heap 記憶體——這就是真實 QEMU device 漏洞的最小模型。

---

## 如果卡住了

**提示 1**：GPA 範圍必須完全不在任何 `KVM_SET_USER_MEMORY_REGION` 裡面，才會觸發 `KVM_EXIT_MMIO`。如果你的 `guest_mem` size 設太大、覆蓋了 `0x4000`，EPT 會正常映射，MMIO exit 就不會發生，你會看到 guest 寫到一塊真實的 host 記憶體裡，然後什麼 log 都沒有。

**提示 2**：`kvm_run->mmio.is_write` 為 `1` 是 guest 在寫，為 `0` 是 guest 在讀。讀的時候你要把資料填進 `kvm_run->mmio.data[]` 再繼續跑 `KVM_RUN`——不填的話，guest 讀到的是未初始化的零，更糟的是某些 KVM 版本會讓 guest 拿到垃圾值，造成難以追蹤的 bug。

**提示 3**：16-bit real mode 的記憶體位址定址受 CS:IP 與 DS segment base 影響。寫 `mov [0x4000], ax` 時，guest 存取的線性位址是 `DS.base + 0x4000`。我們在 Step 3 把 `sregs.cs.base` 設為 0，但也要確認 DS segment 的 base 同樣是 0（`KVM_GET_SREGS` 回來的 DS 預設 base 通常是 0，但不同 KVM 版本行為不同，最好顯式設定 `sregs.ds.base = 0`）。

---

## 完整參考解答

<details>
<summary>展開參考解答</summary>

> ⚠️ **未實測：以下為理論預期，請在真實 Linux + `/dev/kvm` 環境驗證。**

```c
/* minimal_kvm.c — Task 1 + Task 2 + Task 3 demo
 * 編譯: gcc -O0 -o minimal_kvm minimal_kvm.c
 * 執行: sudo ./minimal_kvm   (需要 /dev/kvm 讀寫權限)
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <linux/kvm.h>

/* Task 1 guest code: 16-bit real mode
 *   mov ax, 0xDEAD    ; B8 AD DE
 *   mov [0x4000], ax  ; A3 00 40
 *   hlt               ; F4
 */
static const uint8_t GUEST_CODE_T1[] = {
    0xB8, 0xAD, 0xDE,
    0xA3, 0x00, 0x40,
    0xF4
};

/* Task 2 guest code: write 0x1234, then read back
 *   mov ax, 0x1234    ; B8 34 12
 *   mov [0x4000], ax  ; A3 00 40  (MMIO write)
 *   mov ax, [0x4000]  ; A1 00 40  (MMIO read)
 *   hlt               ; F4
 */
static const uint8_t GUEST_CODE_T2[] = {
    0xB8, 0x34, 0x12,
    0xA3, 0x00, 0x40,
    0xA1, 0x00, 0x40,
    0xF4
};

#define GUEST_MEM_SIZE 0x2000  /* 映射 GPA 0x0000-0x1FFF; 0x4000 故意留空 */

/* 假 device 狀態 (Task 2 + Task 3) */
struct fake_dev {
    uint16_t reg;        /* Task 2: 單一暫存器 */
    uint8_t  len;        /* Task 3: guest 寫入的長度 */
    uint8_t  buf[16];    /* Task 3: 目標 buffer */
};

static int setup_vm(int kvm_fd, int vm_fd, void *guest_mem)
{
    struct kvm_userspace_memory_region region = {
        .slot            = 0,
        .flags           = 0,
        .guest_phys_addr = 0x0000ULL,
        .memory_size     = GUEST_MEM_SIZE,
        .userspace_addr  = (uint64_t)(uintptr_t)guest_mem,
    };
    return ioctl(vm_fd, KVM_SET_USER_MEMORY_REGION, &region);
}

static int setup_vcpu(int vcpu_fd)
{
    struct kvm_sregs sregs;
    if (ioctl(vcpu_fd, KVM_GET_SREGS, &sregs) < 0) return -1;

    /* 16-bit real mode: CS/DS base = 0, limit = 0xFFFF */
    sregs.cs.selector = 0;  sregs.cs.base = 0;
    sregs.ds.selector = 0;  sregs.ds.base = 0;
    if (ioctl(vcpu_fd, KVM_SET_SREGS, &sregs) < 0) return -1;

    struct kvm_regs regs;
    memset(&regs, 0, sizeof(regs));
    regs.rip    = 0;
    regs.rflags = 0x2;  /* bit 1 永遠為 1 */
    return ioctl(vcpu_fd, KVM_SET_REGS, &regs);
}

static void handle_mmio(struct kvm_run *run, struct fake_dev *dev, int task)
{
    uint64_t addr = run->mmio.phys_addr;
    uint32_t len  = run->mmio.len;

    if (run->mmio.is_write) {
        uint64_t data = 0;
        memcpy(&data, run->mmio.data, len < 8 ? len : 8);
        printf("[host] MMIO write: addr=0x%lx, len=%u, data=0x%lx\n",
               addr, len, data);

        if (task >= 2 && addr == 0x4000 && len == 2) {
            dev->reg = (uint16_t)data;
            printf("[host]   -> fake_reg = 0x%04x\n", dev->reg);
        }

        /* Task 3: len register */
        if (task >= 3 && addr == 0x4000 && len == 1) {
            dev->len = (uint8_t)data;
            printf("[host] Task3: dev.len set to %u\n", dev->len);
        }

        /* Task 3: buf register — 展示有漏洞的版本 */
        if (task >= 3 && addr == 0x4004) {
            // BUG: dev->len 由 guest 控制，未上界
            // memcpy(dev->buf, run->mmio.data, dev->len);
            // FIX: 加上界限檢查
            size_t safe_len = dev->len > sizeof(dev->buf)
                              ? sizeof(dev->buf)
                              : dev->len;
            printf("[host] Task3: memcpy buf, len=%u (clamped from %u)\n",
                   (unsigned)safe_len, dev->len);
            memcpy(dev->buf, run->mmio.data, safe_len);  /* FIX applied */
        }

    } else {
        /* MMIO read: 把 fake_reg 注射回 guest */
        printf("[host] MMIO read:  addr=0x%lx, len=%u -> returning 0x%04x\n",
               addr, len, dev->reg);
        uint16_t ret = dev->reg;
        memcpy(run->mmio.data, &ret, len < 2 ? len : 2);
    }
}

static int run_vm(int vcpu_fd, struct kvm_run *run, struct fake_dev *dev,
                  int task)
{
    printf("[host] starting KVM_RUN loop (task %d)\n", task);
    for (;;) {
        if (ioctl(vcpu_fd, KVM_RUN, 0) < 0) {
            perror("KVM_RUN");
            return -1;
        }
        switch (run->exit_reason) {
        case KVM_EXIT_MMIO:
            handle_mmio(run, dev, task);
            break;
        case KVM_EXIT_HLT:
            printf("[host] KVM_EXIT_HLT — guest halted, exit normally\n");
            return 0;
        case KVM_EXIT_IO:
            /* real mode 可能觸發一些 IO，忽略 */
            break;
        default:
            fprintf(stderr, "[host] unexpected exit reason: %u\n",
                    run->exit_reason);
            return -1;
        }
    }
}

int main(int argc, char **argv)
{
    int task = (argc > 1) ? atoi(argv[1]) : 1;
    printf("[host] running Task %d\n", task);

    int kvm_fd = open("/dev/kvm", O_RDWR | O_CLOEXEC);
    if (kvm_fd < 0) { perror("open /dev/kvm"); return 1; }

    int vm_fd = ioctl(kvm_fd, KVM_CREATE_VM, 0);
    if (vm_fd < 0) { perror("KVM_CREATE_VM"); return 1; }

    int vcpu_fd = ioctl(vm_fd, KVM_CREATE_VCPU, 0);
    if (vcpu_fd < 0) { perror("KVM_CREATE_VCPU"); return 1; }

    void *guest_mem = mmap(NULL, GUEST_MEM_SIZE,
                           PROT_READ | PROT_WRITE,
                           MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    if (guest_mem == MAP_FAILED) { perror("mmap guest_mem"); return 1; }

    if (setup_vm(kvm_fd, vm_fd, guest_mem) < 0) {
        perror("KVM_SET_USER_MEMORY_REGION"); return 1;
    }

    /* 選擇 guest 程式碼 */
    const uint8_t *code;
    size_t code_len;
    if (task == 1) {
        code = GUEST_CODE_T1; code_len = sizeof(GUEST_CODE_T1);
    } else {
        code = GUEST_CODE_T2; code_len = sizeof(GUEST_CODE_T2);
    }
    memcpy(guest_mem, code, code_len);

    if (setup_vcpu(vcpu_fd) < 0) { perror("setup_vcpu"); return 1; }

    int kvm_run_size = ioctl(kvm_fd, KVM_GET_VCPU_MMAP_SIZE, 0);
    struct kvm_run *run = mmap(NULL, kvm_run_size,
                               PROT_READ | PROT_WRITE,
                               MAP_SHARED, vcpu_fd, 0);
    if (run == MAP_FAILED) { perror("mmap kvm_run"); return 1; }

    struct fake_dev dev = {0};
    int ret = run_vm(vcpu_fd, run, &dev, task);

    munmap(run, kvm_run_size);
    munmap(guest_mem, GUEST_MEM_SIZE);
    close(vcpu_fd); close(vm_fd); close(kvm_fd);
    return ret;
}
```

**編譯與執行**：
```bash
gcc -O0 -o minimal_kvm minimal_kvm.c
sudo ./minimal_kvm 1   # Task 1
sudo ./minimal_kvm 2   # Task 2
sudo ./minimal_kvm 3   # Task 3 (用 Task 2 guest code + Task 3 device logic)
```

**Task 3 漏洞說明**（不需要真的觸發）：

假設 guest 先寫 `0xFF` 到 GPA `0x4000`（設定 `dev.len = 255`），再寫 8 bytes 資料到 GPA `0x4004`，有漏洞的 `memcpy(dev.buf, data, 255)` 會從 `dev.buf[0]` 開始寫 255 bytes，但 `buf` 只有 16 bytes。在 QEMU 這種 heap-allocated device 結構裡，這 255 bytes 會覆蓋相鄰的 heap object，開啟 arbitrary write 路徑——CVE-2015-5165 (rtl8139) 和 CVE-2019-6778 (slirp) 的根因都是這個模式。

</details>

---

## 測試用例表

| 測試案例 | guest 操作 | 預期 host 輸出 | 任務 |
|----------|-----------|----------------|------|
| 寫入 `0xDEAD` 到 `0x4000` | MMIO write, len=2 | `MMIO write: addr=0x4000, data=0xdead` | 1 |
| 讀取 `0x4000`（初始值） | MMIO read | `MMIO read: addr=0x4000, returning 0x0000` | 2 |
| 寫 `0x1234` 再讀回 | write then read | 讀回 `0x1234`，log 顯示兩行 | 2 |
| `len=0xFF`，寫 buf（有漏洞版） | MMIO write to `0x4004` | `memcpy(buf, data, 255)` 超出 `buf[16]`，溢位 | 3 |
| `len=0xFF`，寫 buf（修復版） | MMIO write to `0x4004` | 截斷為 `min(0xFF, 16) = 16 bytes` | 3 |

---

## 延伸挑戰

1. **多個 MMIO slot**：新增 GPA `0x5000` 作為第二個假 device（中斷狀態暫存器），guest 寫 `0x4000` 後 host 設定 `0x5000` 的 pending bit，guest 再讀 `0x5000` 確認。這模擬了真實 device 的「command register + status register」模式。

2. **32-bit protected mode**：把 guest 程式碼改寫成 32-bit protected mode（需要設定 GDT、CR0.PE = 1）。觀察 `mov DWORD PTR [0x4000], 0xDEADBEEF` 觸發的 MMIO exit 的 `len` 是否變成 4。

3. **統計 exit 次數**：在主迴圈裡計算 `KVM_EXIT_MMIO` 的次數，和 `KVM_EXIT_HLT` 之間的其他 exit（如 `KVM_EXIT_IO`）。理解 real mode 下哪些指令會意外觸發其他 exit reason。

4. **移植到 Rust**：用 `kvm-ioctls` crate 重寫 Task 1，對比 C 版本的 ioctl 呼叫與 Rust binding 的差異。這是 cloud-hypervisor 和 Firecracker 的底層。

5. **計時**：用 `clock_gettime` 測量從 `KVM_RUN` 呼叫到 `KVM_EXIT_MMIO` 回來的 roundtrip 時間。通常是 1–5 µs，這是 virtio 比直接 MMIO passthrough 快的核心原因（批次處理減少 exit 次數）。

---

## 自我檢核

- [ ] Task 1：hypervisor 跑起來，終端印出 `MMIO write: addr=0x4000, len=2, data=0xdead`
- [ ] Task 1：程式在 `KVM_EXIT_HLT` 後正常 return 0，不 segfault
- [ ] Task 2：MMIO read 的 `kvm_run->mmio.data[]` 有正確填值（不填會讓 guest 讀到垃圾）
- [ ] Task 2：write 後再 read，log 顯示讀回值等於寫入值
- [ ] Task 3：程式碼裡有 `// BUG:` 和 `// FIX:` 兩條路徑，能解釋差異
- [ ] Task 3：理解 `guest 控制 len → host memcpy → 超出 buf` 是 VM escape 漏洞的最小原型
- [ ] 能用文字說明為什麼「GPA 不在任何 KVM_SET_USER_MEMORY_REGION」是 MMIO exit 的前提條件

---

完成這個練習後，你已經親手建立了 QEMU 每個 device handler 底下的核心機制。接下來我們要看 QEMU 如何在這個基礎上架出完整的 device 模型、MemoryRegion 樹、以及 main loop 的 dispatch 結構。

→ [Ch 9 QEMU 架構全圖：main loop、memory API、QOM](./09-qemu-architecture.md)
