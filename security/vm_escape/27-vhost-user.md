# Ch 27 — vhost / vhost-user：資料面搬家後的攻擊面轉移

> **目標**：理解 vhost 和 vhost-user 如何把 virtio 資料面搬出 QEMU 行程，攻擊面跟著移向 host kernel 或另一個 userspace backend，以及這對逃逸路徑的意涵。

> **環境**：QEMU 9.0 / x86-64 / Linux host

---

## 為什麼需要這個？

Ch 25 建立了 virtio 架構直覺，Ch 26 示範了 device 信任 guest 值所導致的 bug 模式。但那兩章有一個隱含前提：資料面（data path）跑在 QEMU userspace 裡——封包要進 QEMU、磁碟 I/O 要進 QEMU、QEMU 再把結果轉出去。

這個前提在現代部署裡往往不成立。

**效能瓶頸的根源**：就算 virtio 已經用 shared memory ring 大幅減少 VMEXIT 次數，每次資料路徑仍需要：

1. Guest virtio driver 把 descriptor 寫好，kick QEMU
2. QEMU userspace 處理封包或磁碟 block
3. QEMU 呼叫 callfd 通知 guest 完成

步驟 2 在 QEMU 行程裡跑，意味著每次 I/O 都有 context switch 進出 QEMU 行程的開銷，加上 QEMU 把資料從 guest RAM 複製到 kernel socket buffer 的 memcpy。高頻封包場景下這個開銷是真實瓶頸。

**vhost 的想法**：把 virtio 的 data path 搬進 host kernel。`vhost-net` kernel module 直接存取 guest virtqueue ring（透過 `get_user_pages` pin 住 guest 的實體頁），封包從 guest 記憶體 DMA 直達 kernel network stack，完全繞過 QEMU userspace。QEMU 只保留 control path——協商 feature、分配 virtqueue、處理錯誤。

**vhost-user 的進一步演化**：不需要 kernel module。把 data path 搬到另一個 host userspace 行程（DPDK PMD、SPDK、virtiofsd），QEMU 和這個 backend process 透過 Unix domain socket 交換控制訊息，用 shared memory（`mmap` + `memfd`）傳遞資料。

搬家的代價：**攻擊面跟著搬**。

---

## 先建立直覺

### 三種佈署對比

```
[傳統 virtio]
  guest virtio driver
      │  kick（VMEXIT）
      ▼
  QEMU userspace  ←── 資料面在這裡（封包 copy、磁碟 I/O）
      │  write()/sendmsg()
      ▼
  host kernel network/block stack

[vhost-net]
  guest virtio driver
      │  kick（寫 eventfd，不需 VMEXIT）
      ▼
  host kernel vhost-net module  ←── 資料面在這裡（get_user_pages + skb）
      │  直接讀 guest virtqueue ring（GPA 已映射）
      ▼
  host kernel network stack
  （QEMU 只做初始化 ioctl，資料路徑不經過 QEMU）

[vhost-user]
  guest virtio driver
      │  kick（寫 eventfd）
      ▼
  backend process（DPDK PMD / virtiofsd / SPDK）  ←── 資料面在這裡
      │  mmap 了 guest RAM，直接讀 virtqueue ring
      ▼
  高效能網路 / 本機檔案系統 / 儲存裝置
  （QEMU 透過 Unix socket 告訴 backend：ring 在哪、FD 在哪）
```

**攻擊落點隨部署模式移動**：

| 部署 | 打誰 | 打中後的影響 |
|------|------|------------|
| 傳統 virtio | QEMU userspace 行程 | 取得 QEMU 行程權限（通常 root 或 qemu 使用者） |
| vhost-net | host kernel vhost-net module | 直接 kernel code execution → root |
| vhost-user | backend process | 取得 backend 行程權限（可能是 root、virtiofsd、DPDK PMD） |

攻擊面轉移，不是消失。

---

## 核心概念

### vhost-net 架構

QEMU 透過 `/dev/vhost-net` 的 ioctl 介面把 virtqueue 的設定交給 kernel：

```c
/* QEMU 側的初始化流程（簡化，參考 QEMU hw/net/vhost_net.c） */

// 1. 打開 vhost-net 裝置
int vhost_fd = open("/dev/vhost-net", O_RDWR);

// 2. 設定 feature bits
ioctl(vhost_fd, VHOST_SET_FEATURES, &features);

// 3. 告訴 kernel：guest RAM 的 GPA→HVA 映射
//    struct vhost_memory { nregions, padding, regions[] }
ioctl(vhost_fd, VHOST_SET_MEM_TABLE, &mem);

// 4. 設定 virtqueue 的 ring 位址（GPA）
//    struct vhost_vring_addr { index, flags, desc_user_addr, used_user_addr, avail_user_addr, log_guest_addr }
ioctl(vhost_fd, VHOST_SET_VRING_ADDR, &addr);

// 5. 設定 kick / call eventfd
ioctl(vhost_fd, VHOST_SET_VRING_KICK, &file);  // guest → kernel 通知
ioctl(vhost_fd, VHOST_SET_VRING_CALL, &file);  // kernel → guest 完成通知
```

從此資料路徑在 kernel 裡跑：`vhost_net` worker thread 等 kick eventfd，收到後呼叫 `vhost_net_handle_rx` / `vhost_net_handle_tx`，直接用 `vhost_map_range`（底層是 `get_user_pages`）把 guest 實體頁 pin 住，搬資料。

**攻擊面含義**：

- QEMU 傳遞給 kernel 的是 ring 的 **HVA（host virtual address）**，kernel 相信這些地址是合法的
- 如果 guest 能控制 ring 的內容（Ch 26 的信任問題），打的目標是 **host kernel** 裡的 `vhost-net` 程式碼
- 真實案例：CVE-2019-14835（V-gHost）——live migration 期間，`vhost_net` 沒有對 `num_buffers` 做 bounds check，guest 傳入構造好的 descriptor 觸發 host kernel 的 OOB write → kernel privilege escalation。技術細節見 Ch 28。

### vhost-user 架構

不需要 kernel module。QEMU 和 backend process 的角色分工：

- **QEMU**：control path——協商 feature、把 guest RAM 的 `memfd` fd 傳給 backend、告訴 backend virtqueue ring 在哪個 offset
- **backend process**：data path——自己 `mmap` guest RAM，直接讀 virtqueue ring，處理封包/檔案 I/O/儲存操作

通訊用 **vhost-user 協議**（定義在 QEMU `docs/interop/vhost-user.rst`），透過 Unix domain socket，格式是固定的 `vhost_user_msg` 結構加上 ancillary fd（用 `SCM_RIGHTS` 傳遞）。

**攻擊面含義**：

- backend process 在收到 `VHOST_USER_SET_MEM_TABLE` 後，用 mmap 把 guest RAM 整個映射到自己的 address space——從此 backend 可以讀寫 guest 任意 GPA
- 如果 backend process 有 bug，逃逸目標就是它：以 backend 的身份在 host 執行程式碼
- backend 的權限因部署不同而異：virtiofsd 通常有 host 檔案系統的讀寫能力，DPDK PMD 可能跑在 root，SPDK 可能有 NVMe 裝置直接存取權

### virtiofsd

`virtiofsd`（virtio filesystem daemon）是把 host 目錄分享給 guest 的 vhost-user backend，實作在 FUSE over virtio 協議之上。

原始 C 版本在 QEMU tree 的 `tools/virtiofsd/`，新版 Rust 重寫在 `gitlab.com/virtio-fs/virtiofsd`。

安全隱患：

- **path traversal**：guest 請求的路徑需要在 host 端驗證，「`../../../etc/shadow`」類的操作如果沒過濾，guest 就能讀 host 任意路徑
- **xattr 繼承**：CVE-2023-3255，virtiofsd 的 `xattrmap` 實作在特定條件下讓 guest 能設定 host 的 `security.capability` xattr，guest 取得 host 上的 capability 提升
- **FUSE opcode 解析 bug**：`FUSE_RENAME2`、`FUSE_SETXATTR` 等 opcode 的 payload 解析若存在邊界問題，可以觸發 virtiofsd 的記憶體錯誤

逃逸路徑：在 guest 裡觸發 virtiofsd 的 bug → 在 host 上以 virtiofsd 的身份執行程式碼 → 取得 virtiofsd 的 host 檔案系統存取權。

### 控制面 vs 資料面的分離

vhost 系列的核心設計原則是：

```
control path：QEMU → (ioctl 或 Unix socket) → kernel/backend
data path：guest → (shared ring + eventfd) → kernel/backend（完全不過 QEMU）
```

這個分離意味著：QEMU 不在資料路徑上。資料路徑的 bug 影響的是 kernel module 或 backend process，QEMU 的防護（如 QEMU 的 address space 隔離、QEMU 的錯誤處理）對資料路徑上的 bug 沒有作用。

---

## 底層機制：vhost-user 協議

### QEMU ↔ backend 訊息流

```
QEMU（master 端）                    backend process（slave 端）
     │                                        │
     │── VHOST_USER_GET_FEATURES ────────────▶│  backend 回報支援的 feature bits
     │◀─ reply: features ─────────────────────│
     │                                        │
     │── VHOST_USER_SET_FEATURES ────────────▶│  QEMU 告知雙方協商好的 features
     │                                        │
     │── VHOST_USER_GET_PROTOCOL_FEATURES ───▶│  協商 protocol extension
     │◀─ reply: protocol_features ────────────│
     │                                        │
     │── VHOST_USER_SET_MEM_TABLE ───────────▶│  ← 關鍵：傳遞 guest RAM 的 memfd
     │   [SCM_RIGHTS: memfd fds]              │    backend 用這些 fd mmap guest RAM
     │                                        │    從此 backend 有 guest RAM 的映射
     │                                        │
     │── VHOST_USER_SET_VRING_NUM ───────────▶│  各 virtqueue 的 ring size
     │── VHOST_USER_SET_VRING_ADDR ──────────▶│  virtqueue ring 在 guest RAM 的 offset
     │── VHOST_USER_SET_VRING_BASE ──────────▶│  avail index 起始值
     │                                        │
     │── VHOST_USER_SET_VRING_KICK ──────────▶│  [SCM_RIGHTS: kickfd]
     │── VHOST_USER_SET_VRING_CALL ──────────▶│  [SCM_RIGHTS: callfd]
     │                                        │
     │── VHOST_USER_SET_VRING_ENABLE ────────▶│  啟動 virtqueue
     │                                        │
     ╔════════════════════════════════════════╗
     ║  以下是資料路徑，不經過 QEMU           ║
     ╚════════════════════════════════════════╝
     │                                        │
  [guest kick eventfd] ──────────────────────▶│  backend 收到 kick
                                              │  backend 直接讀 guest virtqueue（已 mmap）
                                              │  處理 I/O
                                              │  寫 used ring（直接寫 guest RAM）
                                              ▼
                                     [callfd 通知 guest 完成]
```

### vhost-user 訊息結構（簡化版）

```c
/* 參考 QEMU include/hw/virtio/vhost-user.h（QEMU 9.0） */

typedef struct VhostUserMsg {
    VhostUserRequest request;   /* uint32_t：message type，如 VHOST_USER_SET_MEM_TABLE = 5 */
    uint32_t flags;             /* bit 0: reply，bit 1: version（固定 0x1），bit 2: need_reply */
    uint32_t size;              /* payload 長度（bytes） */
    union {                     /* payload，依 request type 不同 */
        uint64_t u64;
        struct vhost_vring_state state;     /* ring index + num */
        struct vhost_vring_addr addr;       /* desc/avail/used 的 HVA */
        VhostUserMemory memory;             /* nregions + padding + VhostUserMemoryRegion[] */
        VhostUserLog log;
        struct vhost_iotlb_msg iotlb;
        VhostUserConfig config;
        VhostUserCryptoSession crypto_session;
        VhostUserVringArea area;
        VhostUserInflight inflight;
    } payload;
} QEMU_PACKED VhostUserMsg;

/* SET_MEM_TABLE 的 payload */
typedef struct VhostUserMemory {
    uint32_t nregions;          /* guest RAM region 數量，通常 1～8 */
    uint32_t padding;
    VhostUserMemoryRegion regions[VHOST_USER_MAX_RAM_SLOTS];
} VhostUserMemory;

typedef struct VhostUserMemoryRegion {
    uint64_t guest_phys_addr;   /* GPA 起始 */
    uint64_t memory_size;       /* region 大小 */
    uint64_t userspace_addr;    /* QEMU 那邊的 HVA（backend mmap 後自己的地址會不同） */
    uint64_t mmap_offset;       /* memfd 內的 offset */
} VhostUserMemoryRegion;
```

**關鍵流程**：backend 收到 `SET_MEM_TABLE` 後，對每個 region 做：

```c
/* backend 側的處理（未實測，理論預期，參考 virtiofsd/DPDK vhost library 實作） */
void *addr = mmap(NULL,
                  region->memory_size,
                  PROT_READ | PROT_WRITE,
                  MAP_SHARED,
                  received_memfd,          /* 透過 SCM_RIGHTS 收到的 fd */
                  region->mmap_offset);
/* 從此 addr + (GPA - guest_phys_addr) 可以直接讀寫 guest 任意實體地址 */
```

backend process 的整個 address space 裡，有一塊映射就是 guest RAM。任何能讓 backend 做出超範圍讀寫的 bug，都可以讀取 guest 的任意記憶體——或者反過來，如果攻擊者控制 guest，可以操控 virtqueue 的 descriptor 讓 backend 根據惡意的地址做 I/O。

---

## 對比與取捨

| 面向 | 傳統 virtio-net | vhost-net | vhost-user |
|------|----------------|-----------|------------|
| **效能** | 低（每次 I/O 經 QEMU） | 高（kernel bypass QEMU） | 最高（user-kernel bypass 皆可，DPDK zero-copy） |
| **架構複雜度** | 低 | 中（需 vhost-net kernel module） | 高（額外 backend process + socket 協議） |
| **攻擊目標** | QEMU userspace 行程 | host kernel（vhost-net module） | backend process（virtiofsd/DPDK PMD/SPDK） |
| **到達 host 的難度** | 低（只要打 QEMU） | 高（需要 kernel exploit） | 中（視 backend 複雜度而定） |
| **成功後影響** | QEMU 行程權限（qemu/root） | kernel code execution → root | backend 行程權限（可能是 root 或受限 uid） |
| **QEMU 是否在資料路徑** | 是 | 否 | 否 |
| **典型 CVE** | CVE-2015-3456 VENOM（floppy DMA） | CVE-2019-14835 V-gHost（migration OOB） | CVE-2023-3255（virtiofsd xattr bypass） |

---

## 踩雷集錦

**「vhost 把攻擊面從 QEMU 移到 kernel，所以更安全」**（錯）

攻擊面轉移，不是消失。打 kernel 比打 QEMU userspace 難，這點是真的——kernel exploit 通常需要 KASLR bypass、heap spray、競態視窗等更多條件。但成功的後果更嚴重：直接 kernel code execution，不需要再從 QEMU 行程 pivot。vhost-net 讓 guest 的惡意 descriptor 有機會直接打 host kernel，拿到 ring 0。

**「vhost-user backend 跑在隔離 namespace 裡，沒問題」**（錯）

virtiofsd 預設就有 host 目標目錄的讀寫權限，這本來就是它的設計目的。就算跑在獨立 mount namespace，只要 virtiofsd 有 bug，攻擊者在 guest 裡觸發它，就能以 virtiofsd 的 host 身份讀寫 host 檔案系統、繼承 host 的 capability。打進 virtiofsd 不比打 QEMU 輕——前者甚至可能直接拿到 host 目錄存取，而後者還需要從 QEMU 行程 pivot 到 host。

**「QEMU 把 vhost 的 bug 擋掉了」**（錯）

vhost 資料面完全繞過 QEMU，QEMU 不在那條路徑上。QEMU 的所有防護機制（AddressSanitizer、edge case 檢查、錯誤處理）對 `vhost-net` kernel module 或 virtiofsd 的 data path bug 完全無效。QEMU 只處理 control path 的訊息（feature 協商、ring 設定），資料搬運發生的地方 QEMU 看不到。

**「vhost-user socket 只有本機才連得到，沒有遠端攻擊面」**（不完全對）

socket 路徑本身確實是本機的，遠端攻擊者不能直接連 Unix domain socket。但已取得 guest 控制權的攻擊者可以在 guest 裡操作 virtio driver——寫 descriptor ring、構造惡意 buffer 指標、觸發 virtqueue kick。這會透過 eventfd 傳到 backend process，backend 根據 descriptor 的指令存取 guest RAM（或被誘導做 OOB 操作）。所以「guest 裡的攻擊者可以間接觸達 backend 的 data path」這條路線是存在的。

**「virtiofsd 只是檔案系統，不涉及 pwn」**（錯）

virtiofsd 實作了完整的 FUSE 協議，需要解析幾十種 opcode（`FUSE_OPEN`、`FUSE_READ`、`FUSE_RENAME2`、`FUSE_SETXATTR` 等）。每個 opcode 都有對應的 payload 解析和 host 操作。路徑合法性驗證、xattr name mapping、symlink 解析都是真實的漏洞路徑。CVE-2023-3255 就是 xattr mapping 的邏輯 bug，CVE-2024 系列繼續在 virtiofsd 裡找到 path traversal 相關問題。FUSE 協議的攻擊面和任何複雜系統程式一樣大。

---

## 進階：再往深一層

### vhost-user reconnect 機制的 TOCTOU 問題

backend 崩潰後 QEMU 會重新連線。重連流程中，QEMU 重新執行一遍 feature 協商和 ring 設定。問題是：在 `VHOST_USER_SET_MEM_TABLE` 和 `VHOST_USER_SET_VRING_ENABLE` 之間，guest 可能已經開始寫 virtqueue ring（因為 guest 驅動不知道 backend 崩了）。重連完成後 backend mmap 的是一份已被 guest 修改過的 ring——這是一個潛在的 TOCTOU 視窗。（未實測，理論預期，需要在目標部署環境驗證是否可觸及。）

### DPDK vhost 的 multi-queue 競態

DPDK vhost library 對每條 virtqueue 獨立起一個 thread 處理。多個 queue 之間的共享狀態（如 memory table）用 rwlock 保護，但 fast path 裡為了效能有些操作不加鎖。在高頻 I/O 壓力下，multi-queue 並發存取同一份 guest RAM 映射可能產生競態條件。這個方向在 DPDK vhost 的歷史 patch 裡有出現，但公開利用案例不多。

### vhost-net zerocopy 與 kernel memory pin

`vhost-net` 有一個 zerocopy 模式（透過 `MSG_ZEROCOPY` + `get_user_pages`），把 guest 實體頁 pin 住，直接讓 kernel 的 DMA 存取——不做記憶體複製。被 pin 住的頁不會被 swap out，也不會被重新分配。大量 pin 頁在高壓場景下可能成為 host 記憶體的 DoS 向量，或在特定 kernel 版本製造 page reference counting 的 bug 機會。

### Kata Containers 場景

Kata Containers 用輕量 VM（QEMU micro VM）提供 container 隔離，container 裡的 agent（`kata-agent`）透過 vhost-user 和 host 的 backend 通訊——包括用 virtiofsd 掛載 container 的 rootfs。這意味著 virtiofsd 的 bug 不是「打穿一台 VM」，而是「打穿 Kata 的輕量 VM 隔離邊界，在 host 以 virtiofsd 身份執行」。雲端環境裡一個共享 host 上跑著幾十個 Kata container，共用同一份 virtiofsd instance 的場景下，單個 bug 的橫向影響面積大幅擴大。

---

## 動手練習

**練習 1：追 `vhost_user_set_mem_table` 的 mmap 呼叫路徑**

閱讀 QEMU 9.0 原始碼 `hw/virtio/vhost-user.c`，找到處理 `VHOST_USER_SET_MEM_TABLE` 的函式（`vhost_user_set_mem_table`），追蹤 QEMU 這一側如何把 `memfd` fd 透過 `SCM_RIGHTS` 傳送出去。接著閱讀 virtiofsd 或 DPDK vhost library 的 slave 側，找到收到 `SET_MEM_TABLE` 後做 `mmap` 的位置，確認 `mmap_offset`、`memory_size`、`prot` 標誌分別是什麼。畫出 QEMU → socket → backend 的 fd 傳遞流程圖。

**練習 2：閱讀 CVE-2019-14835 公告並重述 root cause**

閱讀 oss-security 公告：https://seclists.org/oss-sec/2019/q3/233

用自己的話回答：

- 哪個 struct 的哪個欄位沒有 bounds check？
- live migration 期間的哪個步驟讓 guest 有機會構造惡意值？
- 攻擊者在 guest 裡需要什麼權限才能觸發這個 bug？
- 這個 bug 屬於 Ch 26 的哪一類 bug 模式（device 信任 guest 值）？

**練習 3：在 virtiofsd 源碼裡找路徑合法性驗證**

Clone virtiofsd Rust 版本（https://gitlab.com/virtio-fs/virtiofsd），搜尋 FUSE opcode 的 dispatch 邏輯（通常在 `src/passthrough/` 或 `src/fuse.rs`）。

找出以下幾點：

- `FUSE_LOOKUP`、`FUSE_OPEN`、`FUSE_RENAME2` 各在哪個函式處理？
- path 驗證（防止 `..` traversal）在哪裡做？有沒有統一入口？
- `FUSE_SETXATTR` 的 xattr name 在傳給 host 的 `setxattr(2)` 之前，有沒有過濾 `security.` 前綴？

不需要找到漏洞，只要建立對 virtiofsd 攻擊面的結構性認識。

---

## 本章重點整理

- vhost 系列把 virtio 資料面（data path）搬出 QEMU：vhost-net 搬進 host kernel，vhost-user 搬進另一個 userspace backend process。QEMU 只保留 control path。
- 攻擊面隨資料面搬移：傳統 virtio 打 QEMU，vhost-net 打 host kernel，vhost-user 打 backend process（virtiofsd/DPDK/SPDK）。
- vhost-user 協議透過 Unix socket 傳遞 `memfd` fd，backend 用 `mmap` 取得 guest RAM 的完整映射，之後資料路徑完全不經過 QEMU。
- virtiofsd 實作 FUSE over virtio，攻擊面包括 path traversal、xattr 繼承（CVE-2023-3255）、FUSE opcode 解析 bug，打進去等於以 virtiofsd 身份存取 host 檔案系統。
- vhost-net 的代表性 CVE 是 CVE-2019-14835（V-gHost）：live migration 期間 `vhost_net` 沒有 bounds check，guest 構造惡意 descriptor 打 host kernel。
- Kata Containers 等雲端場景讓 vhost-user backend bug 的影響面積倍增：單個 virtiofsd instance 服務多個 VM，打穿一個等於影響所有共用的 container。

---

## 自我檢核

- [ ] 我能畫出傳統 virtio、vhost-net、vhost-user 三種架構的資料路徑，並標出每種的攻擊落點
- [ ] 我能說明 `VHOST_USER_SET_MEM_TABLE` 的作用，以及 backend 收到它之後做了什麼
- [ ] 我能解釋 CVE-2019-14835 的 root cause，以及它打的是哪層（QEMU / kernel / backend）
- [ ] 我能列出 virtiofsd 至少三個攻擊面方向，並說明 CVE-2023-3255 屬於哪一類
- [ ] 我能說明為什麼「vhost 攻擊面轉移到 kernel 等於更安全」這個直覺是錯的

---

## 延伸閱讀

**vhost-user 協議規範：QEMU `docs/interop/vhost-user.rst`**
讀什麼：完整的 message type 列表、每種 message 的 payload 格式、fd 傳遞規則、backend feature bits。
學到什麼：協議層面的攻擊面——哪些 message 帶 fd、哪些涉及地址傳遞、哪些改變 ring 狀態。
相關性：理解 vhost-user 協議是分析任何 backend bug 的基礎。

**Tencent Blade Team V-gHost 公告（CVE-2019-14835）**
讀什麼：搜尋「V-gHost QEMU KVM escape」，找 Tencent Blade Team 的技術部落格和 oss-security 公告（https://seclists.org/oss-sec/2019/q3/233）。
學到什麼：vhost-net kernel module 的 bug 觸發路徑，live migration 作為攻擊向量，guest-to-host kernel exploit 的完整思路。
相關性：Ch 28 將深入剖析此 CVE，本章先建立背景。

**virtiofsd 原始碼（Rust 版）**
讀什麼：https://gitlab.com/virtio-fs/virtiofsd，重點看 `src/passthrough/` 的 VFS 操作實作和 xattr 處理。
學到什麼：現代 virtiofsd 的架構，sandbox 模式（`--sandbox=chroot/namespace`）的保護範圍，以及 FUSE opcode dispatch 的程式碼組織。
相關性：練習 3 的基礎材料，也是理解 virtiofsd 攻擊面的一手資料。

**DPDK vhost library 文件（dpdk.org）**
讀什麼：https://doc.dpdk.org/guides/prog_guide/vhost_lib.html，重點看 multi-queue、zero-copy、memory mapping 的說明。
學到什麼：高效能場景下 vhost-user backend 的實作取捨，以及 DPDK 選擇犧牲哪些安全性換效能。
相關性：了解 vhost-user 在雲端網路裡的主流部署形式（SR-IOV + DPDK 是 OVS-DPDK 的核心）。

**Kata Containers 架構文件**
讀什麼：https://github.com/kata-containers/kata-containers/tree/main/docs/design，重點看 `architecture.md` 和 virtiofsd 整合說明。
學到什麼：vhost-user 在輕量 VM 容器場景裡的角色，以及一個 virtiofsd bug 在多租戶場景下的影響範圍。
相關性：把本章的技術細節連結到現實雲端部署的威脅模型。

---

vhost 和 vhost-user 讓我們意識到：分析 virtio 系列漏洞時，第一個問題不是「bug 在哪」，而是「這個部署裡資料面在哪」。攻擊面會跟著資料面走，而落點決定了利用難度和成功後的影響。下一章把這個框架套到真實 CVE 上——逐行拆解 virtio 相關漏洞的觸發和利用鏈。

→ [Ch 28](./28-cve-virtio.md)
