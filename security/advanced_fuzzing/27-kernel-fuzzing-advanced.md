# Ch 27 Kernel Fuzzing 進階技術

> **目標**: 掌握 syzkaller 的進階子系統 fuzzing（USB/網路/BPF/FS）、description 覆蓋率提升手法、C reproducer 生成流程、crash bisection，以及從 KASAN 報告推導可利用性的完整方法論。

---

## 為什麼需要這些進階技巧

上一章跑起了基本 syzkaller 並拿到第一份 crash。但面對真實漏洞研究，基本配置有幾個明顯瓶頸：

**覆蓋率天花板**：generic syscall set 打的是「全部 syscall 的聯集」，fuzzer 能量分散在幾百個 syscall 上，特定子系統（USB driver、BPF verifier）實際被打到的比例極低。

**crash 無法重現**：syzkaller 的 .prog 是 pseudo-bytecode，沒辦法直接丟給同事或提交 bug report。需要生成 standalone C 程式，在乾淨 VM 上驗證。

**不知道 bug 在哪個 commit**：找到 crash 是起點，不是終點。bisection 告訴你 regression 點，才能去 cc 正確的開發者。

**KASAN 報告看不懂**：一份 slab-out-of-bounds 報告有 20 行 call trace，哪行是真正的寫入點、踩到哪個物件、OOB 距離多少——這些細節決定 bug 是「CVE 等級的 LPE」還是「幾乎不可利用的 DoS」。

---

## 先建立直覺

### 圖一：crash 到 root cause 的完整流程

```
syzkaller 跑 fuzzing
         |
         | crash 觸發
         v
  +------+-------+
  | KASAN / UBSAN|  <-- crash log 存進 workdir/crashes/
  | 報告          |
  +------+-------+
         |
         | syz-repro（自動 minimize）
         v
  +------+-------+
  |  .prog 最小化 |  約 3-5 個 syscall
  +------+-------+
         |
         | syz-prog2c
         v
  +------+-------+
  | standalone C  |  可在裸 VM 上跑
  +------+-------+
         |
         | 驗證 reproduced
         v
  +------+---------+
  | syz-bisect     |  需要可重複 build 環境
  | git log 二分   |  [*未實測]
  +------+---------+
         |
         v
  引入 bug 的 commit
         |
         | git blame + 閱讀 patch
         v
  root cause 分析
  + 可利用性判斷
```

### 圖二：KASAN slab-out-of-bounds 報告解剖

```
=================================================================
BUG: KASAN: slab-out-of-bounds in mydev_ioctl+0x123/0x456
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^  ^^^^^^^^^^^  ^^^^^^^^^^^^^^^^
    bug 類型                   發生函式    函式內 offset / 總大小

Write of size 8 at addr ffff888012345678 by task syz-executor/1234
^^^^^          ^        ^^^^^^^^^^^^^^^^        ^^^^^^^^^^^^^^^^
存取方向(W/R)  寬度     victim 物件地址          觸發的 thread

Object at ffff888012345640, size=64, align=64
          ^^^^^^^^^^^^^^^^       ^^
          物件起始地址           slab 大小 → kmalloc-64

Allocated by task 1234:
 kzalloc                        <-- 分配 call trace
 mydev_open+0x55/0x100

Freed by task 0:                <-- 若有這段 → UAF
 (none)

The buggy address is located 56 bytes inside     <-- OOB 距離
 a 64-byte region [ffff888012345640, ffff888012345680)
 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
 [物件開頭, 物件結尾)   → 寫在 offset 56 → 踩了最後 8 bytes

=================================================================
```

---

## USB Fuzzing：syzkaller + dummy_hcd

### 為什麼 USB 是高價值目標

USB 子系統在 kernel 裡處於特殊地位：任何人插隨身碟都會觸發枚舉流程，攻擊面是「物理接觸就能打」的零信任邊界。USB audio、HID、storage driver 的 parser 程式碼往往 20 年沒人認真 review。

syzkaller 用 `dummy_hcd` 模擬一個軟體 USB 控制器，讓 kernel USB stack 以為接上了真實設備，整個枚舉流程（descriptor parse → driver probe → endpoint 通訊）全走一遍。

### 啟用 dummy_hcd

```
# kernel config
CONFIG_USB_DUMMY_HCD=y
CONFIG_USB_GADGET=y
CONFIG_USB_GADGET_TESTING=y
```

### syzkaller USB 偽 syscall

syzkaller 定義了專屬的偽 syscall（在 `executor/common_usb.h`）：

| 偽 syscall | 作用 |
|---|---|
| `syz_usb_connect` | 觸發 USB 枚舉（提供偽 device descriptor） |
| `syz_usb_control_io` | 送 control transfer（EP0） |
| `syz_usb_ep_write` | 寫 bulk/interrupt endpoint |
| `syz_usb_ep_read` | 讀 endpoint 回應 |

description 在 `sys/linux/usb*.txt`，每種 class（HID/audio/CDC/storage）各有自己的 descriptor 格式定義。

### USB fuzzing 的 syzkaller config 調整

要讓 syzkaller 把能量集中在 USB，在 config 裡加：

```json
{
    "enable_syscalls": [
        "syz_usb_connect",
        "syz_usb_control_io",
        "syz_usb_ep_write",
        "syz_usb_ep_read",
        "syz_usb_disconnect"
    ],
    "sandbox": "none"
}
```

`sandbox: none` 很重要：USB 枚舉需要 root，`namespace` sandbox 在某些 kernel config 下拿不到足夠權限操作 dummy_hcd。

### USB descriptor 的 fuzzing 目標

USB 枚舉時 kernel 要 parse 多層 descriptor：

```
Device Descriptor
  └── Configuration Descriptor
        └── Interface Descriptor
              ├── Endpoint Descriptor
              └── Class-Specific Descriptor
                    (HID Report Descriptor / Audio Format / CDC Union)
```

每一層的 `bLength`（長度欄位）如果和實際資料不一致，parser 就可能 OOB。syzkaller 在生成 descriptor 時會刻意製造長度不一致的案例。

### 已知 USB bug 案例

- **CVE-2019-19527**：USB HID `hid_debug_events_read()` UAF，syzkaller + dummy_hcd 找到
- **CVE-2019-15292**：USB audio descriptor parse OOB，透過 `syz_usb_connect` 餵惡意 descriptor 觸發
- **CVE-2021-38208**：USB net driver NFC deref，枚舉時 null-ptr-deref
- **CVE-2020-12464**：USB mass storage `usb_sg_wait()` UAF，concurrent disconnect 競爭

---

## 特定子系統 Fuzzing 策略

### 網路 socket fuzzing

Linux 的 `socket()` 支援數十種 `AF_*` family，每種 family 的 syscall 組合、參數語義完全不同。generic description 會把它們混在一起，產生大量無效組合。

縮窄到特定 family 的做法：

```yaml
# syzkaller config
enable_syscalls:
  - socket$netlink
  - bind$netlink
  - sendmsg$netlink
  - recvmsg$netlink
  - setsockopt$netlink
```

`sys/linux/socket_netlink.txt` 定義了 `NETLINK_ROUTE`、`NETLINK_AUDIT` 等各個 netlink family 的 message 格式。

**AF_PACKET fuzzing** 打的是 raw socket + BPF classic filter 組合，歷史上找到多個 `sk_buff` 的 use-after-free，因為 packet socket 走的 fast path 常跳過某些 refcount 操作。

**常見 socket bug 類型：**
- `sk_buff` 路徑的 UAF：非同步 tx/rx 路徑和 socket close 的競爭
- `setsockopt` 的 OOB：integer overflow 後 `kmalloc` 太小
- netlink policy 驗證邏輯錯誤
- `recvmsg` 的 uninit memory read：某些 family 沒清零 padding bytes 就 copy_to_user

### BPF verifier fuzzing

BPF verifier 是 kernel 裡攻擊密度最高的程式碼之一。它要在 O(instructions) 時間內靜態驗證任意 BPF 程式的安全性，任何漏算都可能讓 unprivileged user 拿到任意讀寫。

- **CVE-2021-3490**：verifier 錯誤追蹤 32-bit 運算的 value range → 繞過 pointer arithmetic 限制 → root
- **CVE-2021-34866**：verifier 允許 speculation gadget → Spectre 變形
- **CVE-2022-23222**：`ALU32` 指令的 scalar value 追蹤缺陷 → 越界寫

syzkaller 的 BPF descriptions 在 `sys/linux/bpf*.txt`，其複雜度在整個 description set 裡名列前茅：

```
# bpf() 的 description 片段
bpf_prog_load(fd bpf_prog, attrs ptr[in, bpf_attr_prog], size len[attrs]) fd[bpf_prog]

bpf_attr_prog {
    prog_type    flags[bpf_prog_type, int32]
    insn_cnt     len[insns, int32]
    insns        ptr[in, array[bpf_insn]]
    license      ptr[in, string[bpf_licenses]]
    log_level    int32
    log_size     int32
    log_buf      ptr[out, array[int8], opt]
    ...
}
```

`insns` 是個 BPF 指令陣列，syzkaller 會生成合法結構的指令序列（包含正確的 offset、register 範圍），再在邊界條件上做 mutation。

重點：BPF fuzzing 需要 `CAP_BPF` 或 `unprivileged_bpf_disabled=0`。在 VM 裡跑 `root` 就沒這問題。

### 檔案系統 image mounting fuzzing

策略：生成 malformed 的 fs image bytes，然後 `mount(2)` 讓 kernel parse。kernel 的 fs image parser（superblock 讀取、extent tree 遍歷）是手寫 C code，幾乎沒有任何 fuzzing 歷史。

syzkaller 有 `syz_mount_image` 偽 syscall：

```
syz_mount_image(fs ptr[in, string[fs_types]], dir ptr[in, filename],
                flags flags[mount_flags], opts ptr[in, fs_options],
                chdir bool8, size int32, img ptr[in, array[int8, size]])
```

fuzzer 直接把 `img` 當作任意 bytes 餵給 mount，再配合各種 mount option 組合。

**適合打的 fs：**
- `ext4`：最多人用，parser 最複雜，`ext4_find_entry()` / `ext4_ext_walk()` 都出過洞
- `btrfs`：tree-of-trees 結構，`btrfs_read_chunk_tree()` 的邏輯路徑非常深
- `f2fs`：Flash 友好 fs，segment bitmap parse 出過多個 OOB
- `ntfs3`（新版 kernel 加入）：程式碼相對新，描述複雜，CVE-2023-xxxx 系列

---

## Description 覆蓋率提升技巧

### 用 syz-cover 找 description 缺口

```bash
# 從 coverage.db 生成 HTML report
syz-cover -corpus workdir/corpus.db \
          -kernel vmlinux \
          -html cover.html
```

HTML report 會顯示每個函式被打到的比例。找出 coverage < 20% 的 driver 函式，就是 description 缺口所在。

### kcov_remote：擴展到 workqueue / IRQ handler

標準 kcov 只追蹤直接 syscall 執行路徑，`workqueue` 裡非同步跑的程式碼不在範圍內。

`kcov_remote` 讓你在 workqueue task 裡手動標記：

```c
// kernel driver 程式碼
kcov_remote_start(kcov_handle);
/* ... async work ... */
kcov_remote_stop();
```

syzkaller 支援 `kcov_remote` annotation，在 description 裡指定 handle，fuzzer 就能收集到 IRQ handler 和 softirq 的覆蓋率。

### 打新 driver 前：系統性列出 ioctl

```bash
# 從 kernel source 找所有 ioctl number 定義
grep -rn '_IOW\|_IOR\|_IOWR\|_IO\b' drivers/mydriver/ | \
  grep '#define.*0x' | \
  awk '{print $2, $3}'

# 或找 unlocked_ioctl handler
grep -rn 'unlocked_ioctl' drivers/mydriver/
```

從 handler 反推：每個 `cmd` case 對應一個 ioctl，`arg` 指向的 struct 是 description 的輸入類型。把所有 `switch(cmd)` 的 case 列出來，逐一寫 description。

### Resource 鏈設計

syzkaller 的 resource 系統讓 fuzzer 知道 fd 的生命週期：

```
# 正確的 resource 鏈
resource fd_mydev[fd]

openat$mydev(fd const[AT_FDCWD], file ptr[in, string["/dev/mydev"]],
             flags flags[open_flags]) fd_mydev

ioctl$MYDEV_CMD1(fd fd_mydev, cmd const[MYDEV_CMD1], arg ptr[in, mydev_cmd1_arg])

close(fd fd_mydev)
```

`fd_mydev` 是 typed resource，fuzzer 知道 `ioctl$MYDEV_CMD1` 的 fd 必須是 `openat$mydev` 的回傳值，不會亂配其他 fd。

---

## C Reproducer 生成

### 自動 minimize 流程

syzkaller 抓到 crash 後會自動跑 `syz-repro`：

1. **最小化 .prog**：從觸發 crash 的 syscall 序列中移除不必要的 syscall，直到最小集合
2. **最小化參數**：把每個 syscall 的參數 bytes 逐步清零，保留觸發所需的最小 payload
3. **生成 C reproducer**：把最小化後的 .prog 翻譯成 standalone C 程式

```bash
# 手動跑（通常 syzkaller 自動觸發）
syz-repro -config syzkaller.cfg workdir/crashes/xxx/log0
```

### syz-prog2c：手動轉換

```bash
syz-prog2c -prog crash.prog -enable sandbox_none > repro.c
```

### C reproducer 結構解讀

```c
// 典型 C reproducer 結構

#include <...>

// sandbox 設置（通常 none 或 namespace）
static void sandbox_common() { ... }

// 每個 thread 的 syscall 序列
static void *thr0(void *arg) {
    // syscall 1
    syscall(SYS_openat, ...);
    // syscall 2
    syscall(SYS_ioctl, ...);
    return NULL;
}

// 主要邏輯：可能有多個 thread 模擬競爭條件
int main() {
    sandbox_common();
    pthread_t th;
    pthread_create(&th, NULL, thr0, NULL);
    // 重複跑幾次提高成功率
    for (int i = 0; i < 100; i++) {
        syscall(SYS_ioctl, ...);  // trigger
    }
}
```

注意 `for (int i = 0; i < 100; i++)` 這個迴圈：代表 syzkaller 認為這個 bug 有 race condition，需要重複嘗試才能觸發。

### 在乾淨 VM 上驗證

```bash
# 準備好的 repro.c
gcc -o repro repro.c -pthread -static

# 複製到 VM
scp repro user@vm-ip:/tmp/

# 在 VM 上跑
ssh user@vm-ip '/tmp/repro'

# 觀察 kernel log
ssh user@vm-ip 'dmesg | tail -50'
```

若 reproducer 無法在乾淨 VM 上觸發：
- 確認 kernel config 相同（尤其是 `CONFIG_KASAN=y`）
- 確認 kernel 版本相同（bisection 結果對版本敏感）
- 若是 race condition，多跑幾次或加 `stress`

---

## Crash Bisection

**[以下流程需要能重複 build 的 kernel 環境，本節為概念說明，未實際測試]**

### syz-bisect 工作原理

```
git log HEAD~1000..HEAD
        |
        | 每個 commit 都 build + 跑 reproducer
        | 二分搜尋
        v
  找出第一個讓 reproducer 成功的 commit
        |
        v
  輸出：Introduced by commit abc1234
        Author: Some Developer <dev@example.com>
        Subject: "mydriver: add new ioctl for feature X"
```

```bash
# 概念性用法（[未實測]）
syz-bisect -config syzkaller.cfg \
           -kernel-repo /path/to/linux.git \
           -crash workdir/crashes/xxx
```

需要：
- 完整 linux git 歷史（`git clone --full`）
- 能在幾分鐘內 build kernel 的機器（不然 bisect 要跑幾小時）
- reproducer 觸發率要高（否則 false negative 讓 bisect 跑歪）

### 手動 git bisect 當 syz-bisect 不可用

如果沒有自動化 build 環境，可以手動跑：

```bash
# 標記 known good（bug 不存在的版本）
git bisect start
git bisect bad HEAD          # 當前版本有 bug
git bisect good v6.1         # v6.1 沒有 bug

# git 自動跳到中間點
# 你 build + 跑 reproducer
make -j$(nproc) bzImage && qemu-boot && ./repro

# 有 crash
git bisect bad

# 沒 crash
git bisect good

# 重複直到 git 輸出 first bad commit
git bisect log    # 看 bisect 歷程
git bisect reset  # 結束
```

手動 bisect 的挑戰：每次要 build kernel（快的話 5 分鐘，慢的話 20 分鐘），10-12 次迭代才能在 1000 個 commit 裡定位到一個。整體要 1-4 小時。

### bisect 找到 commit 後的動作

```bash
# 看這個 commit 改了什麼
git show abc1234

# 找 patch 的 mail thread（Fixes: 欄位）
git log --oneline --follow -p drivers/mydriver/myfile.c | head -100

# 看誰 review 了這個 patch（可以 cc 的人）
git log --format="%an <%ae>" abc1234~1..abc1234
```

如果你有完整 patch 分析，可以寄給 security@kernel.org 或在 syzbot issue 上留言。

---

## 從 KASAN 報告推導可利用性

### 完整報告解讀

```
BUG: KASAN: slab-out-of-bounds in mydev_ioctl+0x123/0x456
Write of size 8 at addr ffff888012345678 by task syz-executor/1234
```

逐欄解讀：

**`slab-out-of-bounds`**：踩到了同一個 slab 分配的鄰近物件，或踩到 slab 的 redzone。若是 write，通常可利用。

**`Write of size 8`**：8-byte write → 可以覆蓋一個 pointer（64-bit）。比 1-byte write 好太多。

**`at addr ffff888012345678`**：計算 `addr - object_start = 0x678 - 0x640 = 0x38 = 56`，在 64-byte 物件的 offset 56 寫 8 bytes，剛好踩到物件最後的欄位。

### 各類 bug 的可利用性

| bug 類型 | 可利用性 | 說明 |
|---|---|---|
| `slab-out-of-bounds` write | 高 | 覆蓋鄰近物件 function pointer，cross-cache 打法成熟 |
| `use-after-free` write | 高 | 控制 UAF window 大小可決定打法 |
| `use-after-free` read | 中 | 可 leak pointer，需要配合其他 primitive |
| `slab-out-of-bounds` read | 低-中 | 純 info leak，獨立不夠用 |
| `null-ptr-deref` | 低 | SMEP/SMAP 後 mmap null page 受限；kernel 4.x 後更難 |
| `stack-out-of-bounds` | 中 | 視 offset 決定，近 canary 則難；遠可覆蓋 ra |

### slab-out-of-bounds 可利用性判斷流程

```
1. 確認 slab 大小（kmalloc-64 / kmalloc-128 / etc.）
   → 從報告的 "size=64" 或 "kmalloc-64" 讀出

2. OOB offset 是多少？
   → addr - object_start

3. 往後多少 bytes？
   → 下一個物件從哪裡開始？（通常就是 object_end）

4. 那個位置在下一個物件的哪個欄位？
   → 需要知道「通常放在相同 slab 的物件是什麼」

5. 能控制那個欄位的結果嗎？
   → function pointer / cred pointer / file pointer → 可打
   → data buffer → 看情況
```

連接到 kernel_pwn 課程的技巧：cross-cache attack（讓目標 struct 和可控物件放進同一個 slab page）、dirty pagetable（覆蓋 page table entry），以及 USMA（User Space Mapping Attack）都適用於 slab-OOB 場景。

### UAF 可利用性的額外判斷：window 大小

use-after-free 的可利用性取決於「UAF window」有多大：

```
Thread A                        Thread B
  alloc obj                       |
  set obj->ptr = something        |
  free obj       ← 這裡是 free    |
  ...                             |    ← UAF window
  ...                           spray kmalloc-N（同大小）
  ...                           填進剛 free 的 slot
  deref obj->ptr ← use         ← 如果 B 先填進去，A 這裡讀/寫的是 B 的內容
```

window 越大（free 和 use 之間的指令越多），就越容易在 window 期間做 heap spray。window 很小（< 10 指令）的 UAF 通常要靠 `userfaultfd` 或 `FUSE` 來人工暫停 kernel thread，讓 window 變大——但這類技巧在 kernel 5.11 後被逐步限制。

詳細利用技術見 `security/kernel_pwn/` 課程的 Part 3（heap 利用）。

---

## syzbot 生態：挑 open bug 練手

### syzbot 是什麼

`syzbot.appspot.com` 是 Google 持續跑的公開 syzkaller dashboard，24/7 對著 mainline / stable 分支打，找到的 bug 公開列出，維護者修完才關閉。

dashboard 上有幾百個 open bug，每個 bug 有：
- crash 標題
- kernel 版本
- C reproducer（不一定，自動生成失敗的就沒有）
- crash log
- 上報日期

### 如何挑適合新手分析的 bug

篩選條件（由易到難）：

1. **有 C reproducer**：沒有就先不管，複現是第一步
2. **`KASAN: slab-out-of-bounds`**：報告最清楚，告訴你物件、offset、大小
3. **`未分配（No assignee）`**：有人在看的 bug 不適合練手（別打擾人家）
4. **bug 上報時間 < 3 個月**：太老的 bug 可能已有非正式 patch

### 練習流程建議

```
1. 進 syzbot，Filter: "open" + "KASAN" + "has repro"

2. 點開一個 bug，讀 crash log
   → 哪個 driver？哪個 syscall path？
   → OOB 大小？物件類型？

3. 下載 C reproducer，在本地 VM 跑
   → 能觸發嗎？KASAN 報告一致嗎？

4. 閱讀 kernel source 中的對應函式
   → crash 在哪行？為什麼會 OOB？
   → 缺少哪個 bounds check？

5. 嘗試寫 minimal patch（bounds check / early return）
   → 驗證 patch 後 reproducer 不再 crash

6. （進階）判斷可利用性
   → 套用上面的判斷框架
```

這個流程不需要提交 patch（除非你真的寫出好 patch），純分析就夠練 root cause 能力。

---

## 對比取捨表

| 面向 | 選擇 A | 選擇 B | 說明 |
|---|---|---|---|
| USB fuzzing | dummy_hcd（純軟體） | 真實 USB host + malicious device | dummy_hcd 快、可重現；真實 USB 覆蓋物理層 |
| BPF fuzzing | syzkaller descriptions | 自訂 BPF 生成器（如 bf2019） | syzkaller 整合好但 insn 生成不夠 semantic-aware |
| FS fuzzing | `syz_mount_image`（純 bytes） | 結構感知 fuzzer（如 Hydra） | 純 bytes 快但 hit rate 低；結構感知能打到深層 path |
| C reproducer | `syz-prog2c` 自動生成 | 手寫 minimal repro | 自動生成有 sandbox/thread 框架可能干擾；手寫更乾淨但費時 |
| bisection | `syz-bisect` 自動 | 手動 `git bisect` | 自動需 build 環境；手動更靈活但要自己跑 reproducer |

---

## 踩雷

**1. dummy_hcd 沒有 load 就跑 USB fuzzing**

`CONFIG_USB_DUMMY_HCD` 要設 `y`（內建），不能是 `m`（module）或 `n`。很多 distribution kernel 預設關掉，自己 build 才有。`syz_usb_connect` 在沒有 dummy_hcd 的 kernel 上會直接失敗，但錯誤不明顯，只會看到所有 USB syscall 回傳 `ENODEV`，覆蓋率掉到零。

**2. BPF fuzzing 忘記開 unprivileged BPF**

現代 kernel（5.10+）預設 `unprivileged_bpf_disabled=1`，非 root 無法跑 `BPF_PROG_LOAD`。syzkaller 在 VM 裡跑 root 沒問題，但如果你的 sandbox 設成 `setuid` 或 `namespace`，要確認沙箱內還有 `CAP_BPF`。症狀：BPF 相關 syscall 全回 `EPERM`，log 裡看不到 BPF 覆蓋率。

**3. C reproducer 在自己 VM 跑不起來**

最常見原因：kernel config 不同。你的 VM 可能沒開 `CONFIG_KASAN=y`，導致記憶體踩壞但沒有報告，或是踩壞了 heap 結構之後 segfault 在奇怪地方。驗證 reproducer 一定要用和 syzkaller 相同 config build 出來的 kernel。第二常見原因：race condition reproducer 需要特定的 CPU 數量或 scheduler 狀態，在不同 VM 上成功率差異很大。

**4. 把 syzbot 報告的 crash log 當 root cause**

KASAN 報告告訴你「在哪裡被抓到」，不是「在哪裡被引入」。`Write of size 8 at addr X in func_B+0x123` 可能只是 func_B 把一個壞的 pointer 往下傳到這裡才被 KASAN 抓到，真正的 bug 在更上層的 func_A 沒做 bounds check。一定要順著 call trace 往上找 root cause。

---

## 進階延伸

**Syzkaller 的 coverage-guided mutation**：理解 `syz-fuzzer` 如何根據 coverage feedback 選擇 mutation 策略（port/signal-based prioritization），對調整 fuzzing 效率有幫助。

**Kernel sanitizer 組合拳**：KASAN + KMSAN（uninitialized memory）+ KCSAN（data race）同時開，可以抓到三種不同類型的 bug，但 overhead 很高，通常不在同一個 VM 上全開。

**Grammar-based BPF fuzzing**：Buzzer（Google 的工具）和 bpfuzz 做法是生成語義正確的 BPF 程式，繞過 verifier 找到 JIT 層的 bug，與 syzkaller 互補。

**VirtIO fuzzing**：類似 USB 的思路，用 fake virtio 設備欺騙 guest kernel 的 virtio driver，尤其在雲端環境的 VM escape 研究中很有用（見 `security/vm_escape/` 課程）。

**KMSAN（Kernel Memory Sanitizer）**：專門抓 uninitialized memory read，很多 netlink / socket `recvmsg` 的 info leak 是 KMSAN 找到的。與 KASAN 互補，但 overhead 更高（約 3-5x），通常單獨開一個 fuzzing VM 跑。

**slab_nomerge kernel 參數**：開啟後 kernel 不合併相同大小的 slab cache，讓 cross-cache attack 更難（防禦面），但也讓 fuzzing 時的 heap layout 更可預測（研究面）。在分析可利用性時值得測試兩種情況的行為差異。

---

## 動手練習

**練習 1：縮窄 syzkaller 到 AF_NETLINK**

修改 syzkaller config，`enable_syscalls` 只留 `socket$netlink`、`bind$netlink`、`sendmsg$netlink` 系列。跑 10 分鐘，比較和 generic config 的 coverage 分佈差異（`syz-cover` 看 `net/netlink/` 目錄的覆蓋率）。

**練習 2：讀一份 syzbot bug**

進 `syzbot.appspot.com`，filter `KASAN: slab-out-of-bounds`，找一個有 C reproducer 的 open bug。

要交出：
- bug title 和上報日期
- OOB 的物件大小和 offset
- 推測的 root cause（哪個函式沒做什麼檢查）
- 可利用性等級（高/中/低）和理由

**練習 3：syz-prog2c 轉換**

把上一章跑出的任意 crash 的 .prog 用 `syz-prog2c` 轉成 C，在乾淨 VM 上驗證能觸發。觀察生成的 C 程式：有幾個 thread？有沒有迴圈重試？sandbox 模式是什麼？

---

## 本章重點

- `dummy_hcd` 讓 syzkaller 在純軟體環境做 USB fuzzing，不需要實體設備
- BPF verifier 是高密度攻擊面，syzkaller 的 BPF descriptions 是最複雜的 description 之一
- `syz_mount_image` 對 fs image parser 做黑盒 mutation，ext4/btrfs/f2fs/ntfs3 都是目標
- `syz-cover` 找 description 缺口；`kcov_remote` 把覆蓋率擴展到非同步路徑
- C reproducer = `.prog` 最小化 + `syz-prog2c`；驗證要在相同 config 的乾淨 VM 上做
- KASAN 報告的解讀重點：bug 類型、OOB offset、物件大小、slab 類型
- slab-OOB write 通常可利用；null-ptr-deref 在現代 kernel 很難；race UAF 視 window 而定
- syzbot 是最好的練習來源：找有 C reproducer、未分配的 slab-OOB bug 做 root cause 分析

---

## 自我檢核

- [ ] 能說出 dummy_hcd 的作用，以及啟用需要哪些 kernel config
- [ ] 知道 BPF fuzzing 為什麼需要特別的 capability，以及在 VM 裡如何繞過
- [ ] 看到一份 KASAN slab-out-of-bounds 報告，能計算 OOB offset 和找出物件大小
- [ ] 能用 syz-prog2c 把 .prog 轉成 C 並在 VM 上驗證
- [ ] 能從 syzbot 挑出適合新手分析的 bug，並說明選擇標準
- [ ] 能根據 bug 類型（slab-OOB/UAF/null-deref）初步判斷可利用性等級

---

## 延伸閱讀

1. **syzkaller USB fuzzing 文件**（`docs/usb.md` in syzkaller repo）：官方說明 dummy_hcd 的設置和偽 syscall 的設計思路，比任何教程都準確。

2. **syzbot 公開 dashboard**（`syzbot.appspot.com`）：最直接的實戰材料，每個 bug 都有完整的 crash log 和（通常有）C reproducer。

3. **"My Kernel Exploitation Journey"（Google Project Zero blog）**：多篇文章從 syzkaller crash 出發，完整走完 root cause → exploit 的流程，是目前公開文章裡最有深度的範例。

4. **CVE-2021-3490 分析（Nguyen Hoang Thach）**：從 syzbot 的 BPF verifier bug 出發，詳細解釋 ALU32 range tracking 的錯誤和如何建立任意讀寫 primitive，適合對 BPF fuzzing 有興趣的讀者。

5. **Linux kernel KASAN 文件**（`Documentation/dev-tools/kasan.rst`）：解釋各種 bug 類型（slab-out-of-bounds / use-after-free / global-out-of-bounds）的偵測機制和報告格式，看懂報告的必讀。

---

Part 4 的 kernel fuzzing 到此結束。前三章建立了 syzkaller 環境（Ch 25），上一章跑起來並讀 crash（Ch 26），本章深挖了進階子系統和 crash 分析。

接下來 Part 5 轉向完全不同的架構：snapshot fuzzing。

→ [下一章：為什麼需要 Snapshot Fuzzing](./28-why-snapshot-fuzzing.md)
