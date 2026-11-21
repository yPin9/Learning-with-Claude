# Ch 37 — 其他子系統速覽：ksmbd / GPU-DRM / USB / v4l2 / perf / timerfd

> **目標**：建立一張「還有哪些礦區」的地圖。對 ksmbd、GPU/DRM、USB、v4l2、perf、timerfd 與其他 fd 類子系統，我們各回答三個問題：攻擊面在哪、典型 bug 長什麼樣、為什麼值得看。學完之後，下次在 CVE 通報、syzbot 報告或 kernelCTF 紀錄裡看到這些名字，你能馬上聯想到它的可達性條件（誰能打到）與 bug 模式，並決定要不要深挖。

> **環境**：Linux 6.6 LTS、x86-64 基線；GPU 與 USB 的部分另外涉及 Android / Arm Mali（見 [Ch 38](./38-android-vendor-subsystems.md)）。本章是**地圖章**，不含需要實測的 code；shell 指令（`ls -l /dev/dri`、`sysctl` 等）**未實測**，屬理論預期。CVE 資訊來自 WebSearch，查證程度會逐條標明；若不同來源對同一 CVE 的機制描述不一致，我們照實說「來源不一致」，不選邊。

## 為什麼要一張地圖章？

[Ch 30–35](./30-netfilter-nftables.md) 各用一整章深挖一個子系統，因為它們是 kernelCTF 與 LPE 的主戰場。但現實中的 kernel 有 3000 萬行以上的程式碼（就我所知量級，請以 `cloc` 實測為準），絕大多數子系統不值得每個都寫一章。我們需要的是**分類能力**：看到一個子系統，快速評估它的「價值函數」。

我們用四個維度評估一個子系統值不值得投入：

```
價值 = 可達性 × bug 密度 × primitive 品質 ÷ 利用成本

  可達性      誰能打到它？
              ├─ 遠端無認證（最高）      ← ksmbd
              ├─ 本地 unprivileged        ← perf、timerfd、v4l2（視 device 權限）
              ├─ 本地 + userns            ← netfilter（Ch 36）
              ├─ 本地 + 特定 group        ← DRM render node、/dev/video*
              └─ 物理接觸                 ← USB
  bug 密度    歷史 CVE / syzbot 報告多不多？程式碼是否複雜、新、vendor 寫的？
  primitive   bug 能轉成什麼？（UAF / OOB write / 任意讀寫，見 Ch 10）
  利用成本    要不要 race？要不要特殊硬體？mitigation 擋不擋？
```

本章六個子系統，在這四個維度上各自代表一種極端。

## 先建立直覺：一張攻擊面地圖

```
                           ┌────────────────────────────────┐
   遠端（網路）  ──────────▶│ ksmbd：TCP 445，in-kernel SMB   │  無認證階段就有 parser
                           └────────────────────────────────┘

   本地 unprivileged
     │
     ├── syscall 直接可達 ──▶ perf_event_open、timerfd_*、epoll、signalfd、…
     │                        （受 sysctl 限制：kernel.perf_event_paranoid）
     │
     ├── device node ───────▶ /dev/dri/renderD*（GPU）、/dev/video*（v4l2）、
     │                        /dev/mali0（Android）
     │                        （受檔案權限 / SELinux domain / group 限制）
     │
     └── userns ────────────▶ Ch 36

   物理接觸 ───────────────▶ USB：任何插進來的裝置先被 kernel parse descriptor
                              （Ch 34 的 USB 自動掛載同源）
```

> 如果你對「為什麼 device node 的可達性取決於權限與 group」還不熟，先回看 [Ch 4 — syscall 與 user/kernel boundary](./04-syscall-boundary.md) 的 ioctl 與 file_operations 一節。

## 一、ksmbd：in-kernel SMB server（遠端 + 本地）

### 為什麼值得看

ksmbd 是 Linux 5.15 合入的 **in-kernel SMB3 file server**（就我所知，WebSearch 結果亦指出相關 bug「從 5.15 起」存在）。把網路協定的 server 放進 kernel 的動機是效能（避免 user/kernel 資料拷貝），代價是：**一個遠端、可能無認證的網路 parser 住在 ring 0**。對照 user space 的 Samba，同樣的 parser bug 在 Samba 是 user process 崩潰，在 ksmbd 是 kernel memory corruption。

這是本章**可達性最高**的子系統：只要 `CONFIG_SMB_SERVER` 編進去、module 被載入、`ksmbd.mountd` 服務在跑、TCP 445 對外開放，遠端攻擊者就能打到 SMB2 的 request parser。**這些前提不是預設成立的**：大多數發行版預設不載入 ksmbd，也不跑服務，所以實際受影響面遠小於「5.15 以上所有 kernel」。評估 CVE 時永遠先問「這個子系統在目標上有沒有啟用」。

### 物件模型

```
ksmbd.mountd（user space daemon，處理認證與共享設定）
      ▲  netlink（kernel ↔ user 的 IPC）
      ▼
ksmbd kernel module
  ├── 連線層：struct ksmbd_conn（每條 TCP 連線）
  ├── session：struct ksmbd_session（認證後建立）
  ├── tree connect：struct ksmbd_tree_connect（綁定到某個 share）
  ├── 檔案：struct ksmbd_file（open file 的 handle，有 refcount）
  └── SMB2 命令分派：smb2pdu.c 的各個 smb2_xxx() handler
         NEGOTIATE / SESSION_SETUP / TREE_CONNECT / CREATE / READ / WRITE /
         IOCTL / QUERY_INFO / SET_INFO / TREE_DISCONNECT / LOGOFF …
```

關鍵特徵：**多個物件各自有自己的生命週期**（conn / session / tree connect / file），而且 SMB2 支援 **compound request**（一個封包串多個命令）與**多通道**。這是物件生命週期 bug（UAF、refcount）與 race 的溫床。

### 典型 bug 與公開案例

WebSearch 查到的紀錄（含來源不一致處）：

| 識別 | 描述（來源） | 備註 |
|---|---|---|
| CVE-2022-47939 / ZDI-22-1690 | ZDI 標題為 use-after-free，CVSS 10.0，稱未認證遠端；一個漏洞資料庫描述為 `smb2pdu.c` 中 SMB2 tree disconnect 處理的 UAF，影響 5.15 至 5.19.2 之前；Thalium 的總覽文章的表格列為 kernel-side UAF；修補在 5.15.61 與 5.19.2 之後 | **來源對機制描述不一致**：另一個摘要說是 `smb2_write` 缺長度驗證。我們不選邊，請讀 ZDI 與 Thalium 原文與 fix commit 自行判斷 |
| CVE-2023-32254 / ZDI-23-702 | 有來源稱是 tree connection 的 race condition；Red Hat 寫 SMB2_TREE_DISCONNECT 處理；feedly 稱 session setup 的 TOCTOU race | **來源不一致**，是否為 UAF 也未確認 |

兩個案例共同指向的 bug 類型：**SMB2 命令處理中，物件被另一條請求或另一個命令釋放後又被使用**（UAF / race，對應 [Ch 10](./10-vulnerability-taxonomy.md) 的 refcount 與 race 類），以及**不可信的 offset / length 欄位**（SMB2 header 內大量 `*Offset`、`*Length`，parser 必須逐一驗證，漏驗就是 OOB）。Thalium 的文章（見延伸閱讀）把 2023 年同批報告的多個 ksmbd bug 整理在一起，是看這個子系統 bug 風格最好的入口。

### 為什麼利用門檻不低

遠端 + kernel 聽起來很強，但 **ksmbd 的 bug 往往要在認證之後才能走到深處**（許多 handler 要先有 session），且遠端 heap 利用沒有本地的 spray 控制（沒有 `msg_msg` 可用，spray 物件要走 SMB 協定自己能分配的結構）。這是為什麼 ksmbd 的 CVSS 很高，但公開的穩定 exploit 並不多（就我所知，未做系統性普查）。**評分高 ≠ 好打**。

## 二、GPU / DRM：廠商 driver 多、device node 直接可達

### 為什麼值得看

GPU driver 是 kernel 裡**程式碼量最大的 driver 類別之一**（amdgpu、i915、nouveau 在 desktop；Arm Mali、Qualcomm Adreno、Imagination 在 Android）。它的特徵讓它成為礦區：

- **user space 直接傳複雜結構**：`ioctl` 帶指令 buffer、記憶體 import、物件建立。
- **GPU 要管理記憶體映射**：把 CPU 頁與 GPU 頁表同步，涉及 page 的分配、映射、回收，容易出 page-level 的 UAF（比 slab-level 的 UAF 更強：對應 [Ch 22–25](./22-cross-cache.md) 的 page-level 利用，Dirty Pagetable、USMA 這類技術特別適合）。
- **廠商 driver 品質參差**：Android 的 GPU driver 多是 vendor 的 out-of-tree code，review 與 fuzz 覆蓋遠不如 mainline。
- **可達性高**：Android 的 `untrusted_app`（一般 app 的 SELinux domain）能開 `/dev/mali0` 之類的 node（就我所知，各裝置的 SELinux policy 不同，不要視為通則）；desktop Linux 的 `/dev/dri/renderD128` 通常屬於 `render` 或 `video` group，登入的桌面使用者通常在裡面。

### 公開案例（WebSearch 查證）

| 識別 | 內容 | 來源 |
|---|---|---|
| CVE-2022-22706 | Arm Mali driver：Project Zero 的「Mind the Gap」（Ian Beer）所述，driver 讓 process 把 read-only 頁映射成可寫；同批報告還有 kernel memory corruption、physical address 洩漏、physical page UAF | Project Zero blog |
| CVE-2023-4211 | Arm Mali kernel driver 的 UAF；影響 Midgard r4p0–r32p0、Bifrost r0p0–r38p0、Valhall r19p0–r38p0（以及 r39p0 在 r38p1 前）；2023-10 修補，被列入 CISA Known Exploited | Project Zero 0day in-the-wild RCA（Maddie Stone & Jann Horn） |
| CVE-2022-46395 | Mali driver 對 imported user memory 的 UAF，GitHub Security Lab 通報（GHSL-2022-127）；有第三方 writeup 稱它是 Project Zero issue 2327（CVE-2022-36449）的 variant | GHSL；variant 說法**僅來自第三方**，Project Zero 自己的 write-up 我們沒查到 |

綜合這些：**Mali 的 bug 重複出現在「GPU 記憶體物件的生命週期」**——import user memory、`mmap` GPU buffer、物理頁的 refcount。Project Zero 這批報告有被商業 spyware 實際使用的紀錄（WebSearch 的聚合頁面稱 CVE-2022-22706、CVE-2022-38181、CVE-2023-4211 被武器化，用於針對 Android 裝置；這是二手說法，請以 Project Zero 與 Google TAG 原文為準）。這是 GPU 子系統 **in-the-wild 價值**的最佳證據。細節延續到 [Ch 38](./38-android-vendor-subsystems.md)。

### desktop 側

desktop 的 DRM（amdgpu / i915 / nouveau / vmwgfx / virtio-gpu）同樣是 ioctl + GEM / TTM 記憶體管理的組合。**本次查證沒有取得 desktop DRM 的具體 CVE 編號**，我們不編造；讀 syzbot 的 `drm` 類別報告是建立感覺的捷徑。`vmwgfx` 與 `virtio-gpu` 是虛擬化環境（VM guest）內特別值得注意的：它們讓 guest 的 unprivileged user 摸到 hypervisor 介面的 guest 端。

## 三、USB：物理攻擊面與 Syzkaller USB fuzzing

### 為什麼值得看

USB 是**物理接觸型攻擊面**的代表：插進一個惡意裝置，kernel 立刻去讀 descriptor（device、configuration、interface、endpoint、HID report descriptor……），然後根據內容載入對應 driver。驅動程式的 probe 函式會信任這些描述符裡的 length、interface 編號、endpoint 數量。

```
惡意 USB 裝置（可以是改過韌體的隨身碟、Rubber Ducky 類裝置、Raspberry Pi gadget）
   │ 回應 GET_DESCRIPTOR
   ▼
usbcore 解析 → 依 class / VID:PID 選 driver
   │
   ▼
driver probe()：usbhid、snd-usb-audio、usb-serial、cdc_*、uvcvideo……
   │   讀 descriptor 欄位、配置 buffer、解析 report descriptor / mixer unit
   ▼
欄位不一致 → OOB read/write、UAF、NULL deref
```

好用的觀察：**USB 的攻擊者不是 user process，而是「硬體」**。所以威脅模型是物理接觸（lost laptop、公共充電站、evil maid），與 [Ch 34](./34-filesystem-attack-surface.md) 的 USB 儲存裝置自動掛載相連：同一個插入動作，可以同時打 usb driver 的 probe 與 fs 的 parser。

### Syzkaller USB fuzzing

這是 Syzkaller 的一個經典成果。WebSearch 查到：2017 年 11 月，Andrey Konovalov 在 oss-security 公佈 14 個用 syzkaller 在 USB 子系統找到、可由惡意 USB 裝置觸發的漏洞（相關報導稱同一波共 22 個安全缺陷，一個 Sophos 報導的標題為 79 個，**數字在不同來源有出入**，我們不採單一數字）。其中公開 CVE 例如 CVE-2017-16527（`snd_usb_mixer` 的 UAF）與 CVE-2017-16533（`usbhid_parse` 的 OOB read，`drivers/hid/usbhid/hid-core.c`）。2017 這批以 OOB read、NULL deref、UAF 為主，不是典型 heap overflow。

方法論（就我所知，請讀 syzkaller 的 `docs/linux/external_fuzzing_usb.md` 驗證）：

1. 在 fuzz VM 裡用 pseudo-syscall（`syz_usb_connect` 一類）**模擬一個 USB 裝置**，由 fuzzer 控制它對 host 的每次回應（descriptor 內容、control transfer 回應）。
2. 底層靠 `dummy_hcd` 與 `raw-gadget`（Konovalov 開發，就我所知）把 user space 變成「裝置」，讓 fuzzer 完全不需要實體硬體。
3. 把 KASAN 打開，收 crash。

這是一個通用 idea：**把 kernel 對外界的信任邊界（USB 描述符）變成 fuzzer 的輸入**。同樣思路後來延伸到藍牙、WiFi（`mac80211_hwsim`）、NFC 等。看 [Ch 43](./43-syzkaller.md) 的 syzkaller 架構，你會看到 pseudo-syscall 的設計就是為這種「模擬外部世界」而生。

### 為什麼對 LPE 價值有限

物理接觸，加上多數 USB bug 是 OOB read 或 NULL deref，所以對 kernelCTF 風格的 unprivileged LPE 很少成為主角；它在 **Android 的物理取證／鎖屏繞過**、**筆電 evil-maid** 情境下才有價值。

## 四、v4l2（Video4Linux2）：多媒體 driver 與 device node 權限

### 為什麼值得看

v4l2 提供攝影機、capture card、codec 的統一 API。`/dev/video*` 通常屬於 `video` group，桌面使用者通常能開。它的 ioctl 面很大（`VIDIOC_REQBUFS`、`VIDIOC_QBUF`、`VIDIOC_STREAMON`……），並且配合 **videobuf2（vb2）** 管理 buffer 與 mmap，涉及「buffer 的 alloc / free / streaming 狀態」之間的狀態機。這是**狀態機 + 鎖 + 生命週期**的組合，bug 常出現在**並發操作 ioctl 時的 race**。

### 公開案例（WebSearch 查證）

| 識別 | 內容 |
|---|---|
| CVE-2019-18683 | `vivid`（v4l2 的虛擬測試 driver）：停止 streaming 時 mutex 鎖定不正確，數個 race 之一導致 UAF；Alexander Popov 發現，Linux 5.4 修補。**限制**：只在 `vivid` module 已載入時可達，且 local user 要能開 `/dev/video0` |
| CVE-2019-9458 | Red Hat 條目稱 video driver 的 race 導致 UAF（細節我們沒查，不展開） |
| CVE-2025-40302 | `videobuf2`：`vb2_ioctl_remove_bufs()` 在 legacy fileio 啟用時被呼叫會破壞內部 buffer 指標，後續 I/O 時可能 UAF；**未查到提權評級** |

這幾個案例有一個一致點：**v4l2 的 bug 是「同一個 fd 上、不同 ioctl 的並發或交錯呼叫」**，root cause 是鎖範圍沒覆蓋整個狀態轉換。對照 [Ch 40](./40-race-conditions.md) 的 race condition 分類，這是 lock scope bug 的教科書例子。

### 為什麼 vivid 的案例重要

CVE-2019-18683 本身有一個非常教學意義的屬性：**它只在 `vivid` 載入時才可達**。vivid 是測試用虛擬 driver，多數發行版不預設載入。這類「需要特定 module 載入」的 bug 在可達性評估時要**打折**，但又**不能忽略**，因為 module 可以被自動載入（見 [Ch 36](./36-user-namespace.md) 的 autoload 討論；對 `/dev/video*` 這類 node，udev 或 `request_module` 會依裝置觸發）。結論：可達性是**環境相依**的，不是 CVE 本身的屬性。

## 五、perf subsystem：syscall 直接可達，受 sysctl 控制

### 為什麼值得看

`perf_event_open(2)` 是一個**一般 user 可以直接呼叫的 syscall**（受 `kernel.perf_event_paranoid` 限制）。它的內部極度複雜：event 型別（hardware、software、tracepoint、kprobe、uprobe、breakpoint）、group / sibling 結構、per-cpu 與 per-task context、mmap ring buffer、`read`、`ioctl`、`fork` 時的 inherit。**同一個 API 背後是一整個大型 state machine，且多個 event 可以互相關聯**，這是 bug 的溫床。

### 公開案例（WebSearch 查證）

| 識別 | root cause | 備註 |
|---|---|---|
| CVE-2013-2094 | `perf_swevent_init()`（`kernel/events/core.c`）用了錯誤的整數型別：kernel 沒檢查 user space 傳入 `attr.config` 的全部 64 bit，造成對 `perf_swevent_enabled` 陣列的 OOB 存取；3.8.9 前受影響；引入 commit 為 `b0a873eb`，上游修補 commit 為 `8176cced`（皆為搜尋結果所述）；CISA Known Exploited | 教科書級 **integer 型別寬度** bug；歷史上被廣泛用於本地提權 |
| CVE-2023-5717 | `perf_read_group()` 在某 event 的 `sibling_list` 比 child 的 `sibling_list` 小時，對配置好的 buffer 之外寫入（heap OOB write）；修補 commit `32671e37`（搜尋結果所述） | Red Hat 註明預設 `kernel.perf_event_paranoid=2` 時無法觸發；Ubuntu 的 `SECURITY_PERF_EVENTS_RESTRICT` 讓預設阻止 unprivileged 使用 |

### `perf_event_paranoid` 的各級

就我所知（請查 `Documentation/admin-guide/sysctl/kernel.rst` 驗證）：

| 值 | 意義 |
|---|---|
| `-1` | 不限制 |
| `0` | 允許 user 取 CPU event，不限制 tracepoint（視版本） |
| `1` | 允許 user space 與 kernel 量測 |
| `2` | 只允許 user space 量測（mainline 的預設值，與 Red Hat 的說法一致） |
| `3` | 部分發行版（Debian / Android 的 patch，就我所知）禁止 unprivileged 使用 `perf_event_open` |

這是 userns 之外另一個典型的「**sysctl 決定可達性**」例子。CVE-2023-5717 的 Red Hat 註記清楚說明了這點：同一個 bug 在 paranoid=2 時不可達。**對 CVE 做 triage，第一件事看 sysctl 預設。**

## 六、timerfd 與其他 fd 類

### 為什麼值得看

Linux 的「everything is a file」讓一堆內核物件以 fd 形式暴露：`timerfd`、`eventfd`、`signalfd`、`epoll`、`pidfd`、`memfd`、`userfaultfd`（[Ch 41](./41-widening-race-window.md)）、`fanotify`、`inotify`、`io_uring`（[Ch 31](./31-io-uring.md)）。這類子系統的共通特性：

1. **syscall 直接可達，不需要特權**（部分受 sysctl，如 `userfaultfd`）。
2. **每個 fd 對應一個 kernel 物件，有 release 路徑**，而 `close()`、`dup`、`fork`、在另一個 thread 的 `ioctl`/`read` 可以並發。
3. **和別的子系統有交互**：timerfd 與 hrtimer、系統時間調整；epoll 與被監聽的 file 的 wait queue；signalfd 與 signal 處理。**交互點最容易出生命週期 bug**。

### 公開案例：CVE-2017-10661（timerfd）

WebSearch：`fs/timerfd.c` 的 race condition，「simultaneous file-descriptor operations」導致 list corruption 或 UAF，4.10.15 修補，4.9.27 有 stable 回補（來源所述）。就我所知，機制與 `TFD_TIMER_CANCEL_ON_SET`、系統時間改變時 kernel 對 timerfd 的 `cancel_list` 的處理有關，也就是 timerfd 物件同時被「user 的 settime/close」與「kernel 時間調整的 cancel 路徑」存取，缺乏一致的鎖保護；**這段機制是我們的推測，WebSearch 只確認了「race → list corruption / UAF」這一層**，請讀 fix commit 確認。

教學重點：這是一個**經典的「老 bug 但模式永不過時」**：任何 fd 類物件，只要被兩條彼此不知情的路徑存取，就要懷疑 race。

### 其他 fd 類的檢查清單

看到一個沒見過的 fd 類子系統，我們問：

- `release()` 與 `read/write/ioctl/poll` 有沒有同一把鎖保護？
- 有沒有 **callback 被註冊到別的子系統**（wait queue、timer、notifier chain）？unregister 與 callback 之間有沒有 race？
- 物件有沒有 refcount？ `get`/`put` 是否成對？error path 有沒有漏 `put`？
- 是否有 `fork` / `dup` 造成的**多個 file 指向同一個內部物件**？

## 演練：拿到一個 CVE 通報，怎麼 30 分鐘內做完 triage

我們用 CVE-2023-5717（perf）當範本，把前面的四維度變成可重複的流程。以下步驟的前三步來自 WebSearch 查到的資料，第四步起是我們建議的做法，**未針對任何真實目標實測**。

1. **一手資料**：找 fix commit（`32671e37`，搜尋結果所述）與 NVD / 發行版 advisory。二手聚合網站只拿來找線索；本次搜尋就遇到某個聚合摘要把 CVE-2023-5717 錯寫成網卡 driver 的 buffer overflow，與 CVE 記錄和廠商資料衝突，我們直接捨棄。
2. **定位 root cause**：從 commit message 與 diff 得到「`perf_read_group()` 在 sibling_list 大小不一致時越界寫」，對照 [Ch 10](./10-vulnerability-taxonomy.md) 歸類為 heap OOB write。
3. **可達性**：Red Hat 說 paranoid=2 不可達，Ubuntu 預設也不可達。所以對預設設定的伺服器，這是「需要 root 或被調鬆的設定才可達」的 bug。
4. **環境盤點**：目標機器的 `kernel.perf_event_paranoid` 是多少？有沒有程式（效能工具、監控 agent）要求把它調成 `-1` 或 `0`？這種「為了好用而調鬆」的設定，在開發機與 CI 機器上並不罕見，風險集中在那裡。
5. **primitive 評估**：OOB write 的大小與內容可不可控？落在哪個 kmalloc cache（[Ch 8](./08-slub-allocator.md)）？有沒有可用的 victim object（[Ch 19](./19-spray-objects.md)）？這一步決定它是「DoS 級」還是「LPE 級」。
6. **結論格式**：一行寫清楚「在 X 設定下，Y 身分可達，primitive 為 Z」。例如「paranoid ≤ 1 時一般 user 可達，heap OOB write，能否穩定利用未評估」。

這個流程的價值在於**把「可達性」從直覺變成一個必填欄位**。很多對外溝通的混亂，來自只寫 root cause 而沒寫前提。

### 同一套流程套在 ksmbd 上

| 步驟 | CVE-2022-47939 的情況 |
|---|---|
| 一手資料 | ZDI-22-1690、Thalium 文章、fix commit（需自己找） |
| root cause | 來源對機制描述不一致，讀 fix commit 判斷 |
| 可達性 | 需要：kernel 5.15 至 5.19.2 之前（含 5.15.x 在 5.15.61 前）、`CONFIG_SMB_SERVER` 且 module 載入、`ksmbd.mountd` 在跑、445 對攻擊者可達 |
| 環境盤點 | `lsmod | grep ksmbd`、`ss -ltnp | grep 445`、`systemctl status ksmbd` 一類（未實測） |
| primitive | UAF；遠端 heap 控制弱 |
| 結論 | 「啟用 ksmbd 的受影響版本上，網路可達者可觸發 UAF；多數預設環境不受影響」 |

## 對比取捨表：六個子系統的價值函數

| 子系統 | 可達性 | bug 密度 | primitive | 利用成本 | 值得投入的情境 |
|---|---|---|---|---|---|
| ksmbd | 遠端；前提：module 啟用、服務在跑 | 中（新、物件多） | UAF、OOB（heap） | 高：遠端 heap 控制弱、多數 handler 要先認證 | 找遠端 kernel bug；評估 SMB server 部署風險 |
| GPU / DRM | 本地，device node 權限（Android app 可達） | 高（vendor code） | page-level UAF、任意物理頁映射 | 中：page-level 技巧要熟 | Android 研究；對應 Ch 22–25 |
| USB | 物理接觸 | 高（driver 多、信任 descriptor） | OOB read、UAF | 高：要控制「裝置」 | 物理威脅模型、syzkaller 方法論學習 |
| v4l2 | 本地，`video` group | 中 | UAF（race 型） | 中高：要贏 race | 學 lock scope bug；視 module 是否載入 |
| perf | 本地 syscall（sysctl 控制） | 中（歷史上不少） | OOB write、integer | 中 | 學 integer / OOB；看 paranoid 設定 |
| timerfd 與其他 fd | 本地 syscall | 低到中（成熟但 race 模式常見） | UAF（race 型） | 高：race | 學 fd 生命週期 race 模式 |

## 踩雷集錦

1. **「CVSS 10 就是好打」**：錯。ksmbd 的 CVE-2022-47939 在 ZDI 評為 CVSS 10.0，但實際利用要面對「前提（module 啟用、服務在跑）」「遠端 heap 控制弱」「認證階段」等。CVSS 衡量最壞情況的影響，不衡量利用難度。
2. **「有 CVE 就代表我的機器受影響」**：要先查子系統是否啟用。ksmbd 預設不載入；vivid 是測試 driver；CVE-2023-5717 在預設 paranoid 下不可達。**可達性取決於環境，不是 CVE 本身**。
3. **「USB bug 都是物理攻擊，不用管」**：對 server 是對的；對筆電與手機不是。USB 描述符 parsing 與 [Ch 34](./34-filesystem-attack-surface.md) 的自動掛載是同一個插入事件打兩個面。
4. **「GPU driver 是 user space 的事」**：GPU driver 的 kernel 半邊（KMD）是 ring 0 code，bug 就是 kernel bug。Android 上 app 可以直接打到它，是 in-the-wild exploit 的熱門目標（Project Zero 的 0day RCA 為證）。
5. **「老 CVE 不用看」**：CVE-2013-2094（perf）與 CVE-2017-10661（timerfd）是教學價值很高的老案例：前者教 integer 寬度、後者教 fd 生命週期 race。**bug 模式比 bug 本身長壽**。
6. **「來源說的都對」**：本章就遇到兩次來源不一致（CVE-2022-47939 與 CVE-2023-32254 的機制描述）。聚合型 CVE 網站會把摘要寫錯或混淆。**一手來源是 fix commit 與廠商 advisory**，二手網站只拿來找線索。
7. **「sysctl 預設在所有發行版一致」**：`perf_event_paranoid` 的預設在 mainline 是 2，Debian / Android 等有自己的 patch；userns 的 knob 更分歧（[Ch 36](./36-user-namespace.md)）。不要用你自己機器的設定去推論別人的環境。

## 進階：再往深一層

- **三種「可達性放大器」**：userns（[Ch 36](./36-user-namespace.md)）、module 自動載入、device node 的 group 與 SELinux domain。看一個 CVE，我們依序問：syscall 直接可達嗎？要不要 userns？要不要某個 module 被載入？要不要某個 device node 的權限？
- **Android 的差異**：Android 用 SELinux 把「一般 app 能開哪些 device node」收得很緊，GPU 是少數 app 一定要開的 node，所以成為重點目標；詳見 [Ch 38](./38-android-vendor-subsystems.md) 與 [Ch 46](./46-arm64-kernel-pwn.md)。
- **用 syzbot 找新礦區**：開 syzbot 上游 dashboard，依子系統名稱（`drm`、`media`、`usb`、`perf`、`ksmbd`）看 open bug 數與年齡。open 很久且 reproducer 穩定的，值得照 [Ch 42](./42-patch-to-rootcause.md) 的方法學習。
- **看 `MAINTAINERS`**：`MAINTAINERS` 檔裡 `S:`（Status）欄為 `Orphan` 或 `Odd Fixes` 的子系統，維護者少、review 弱，歷史上 bug 修補慢。這是挑冷門礦區的啟發式（未做統計驗證）。
- **其他值得補充的子系統**（本章不展開）：`vsock`、`sound/ALSA`、`bluetooth`、`nfc`、`sctp`、`tipc`、`rds`、`netrom` 這類少用網路協定。就我所知，kernel 的網路協定 module 常有「幾乎沒人用但預設可自動載入」的問題，這是各發行版黑名單它們的原因。

## 動手練習

> 以下在**你自己擁有的 Linux VM**進行；所有指令**未實測**。

1. **盤點可達性**：在 VM 上執行
   ```bash
   ls -l /dev/dri /dev/video* 2>/dev/null          # device node 權限與 group
   lsmod | grep -E 'ksmbd|vivid|vmwgfx|virtio_gpu' # 這些 module 是否載入
   sysctl kernel.perf_event_paranoid
   id                                               # 你屬於哪些 group
   ```
   寫下：以一般 user 身分，你能直接碰到本章哪幾個子系統？
2. **讀 CVE-2013-2094 的 fix**：找 `8176cced` 對 `kernel/events/core.c` 的修改（`git show 8176cced`，若 hash 不符用 `git log --grep=perf_swevent`），確認 root cause 是「`attr.config` 的型別寬度」，並解釋為什麼用 `u64` 比較與用 `int` 比較結果不同。
3. **讀 CVE-2023-5717 的 fix**：`git show 32671e37`，描述 `perf_read_group` 修補前缺的約束（sibling_list 大小）。
4. **讀 ksmbd 的 tree disconnect**：在 `fs/smb/server/smb2pdu.c`（6.6 位置，就我所知 ksmbd 於 6.x 搬到 `fs/smb/server/`，請確認）找 `smb2_tree_disconnect()`，標出它對 `ksmbd_tree_connect` 物件做了什麼、有沒有鎖，對照 Thalium 文章與 ZDI 描述，判斷來源的機制說法哪個較符合 source。
5. **跑 syzbot 瀏覽**：在 syzbot 上游頁面找 `usb`、`drm`、`media` 類別各一個有 reproducer 的 `KASAN: use-after-free`，把 call trace 的最頂層函式寫下來，標它屬於本章哪個子系統、可達性條件是什麼。
6. **（進階）架 USB fuzz 環境**：照 syzkaller 的 USB fuzzing 文件在 QEMU 內啟用 `dummy_hcd` 與 raw-gadget，跑一小時，記錄 crash 數；這需要你自己編 kernel（[Ch 0](./00-environment-setup.md) 的流程），**未實測**。

## 本章重點整理

- 評估子系統用四個維度：可達性、bug 密度、primitive 品質、利用成本。
- ksmbd：遠端 in-kernel parser，可達性最高但有啟用前提，利用門檻不低；CVE-2022-47939 等案例的機制描述來源不一致，要讀一手來源。
- GPU/DRM：vendor code 多、device node 直接可達、page-level 利用強；Mali 的 CVE-2022-22706、CVE-2023-4211 有 in-the-wild 紀錄。
- USB：物理威脅模型，Syzkaller 用 `dummy_hcd` / raw-gadget 模擬裝置，把 descriptor 變成 fuzz 輸入。
- v4l2、perf、timerfd：syscall / device node 直接可達，bug 模式分別是 lock scope race、integer 寬度、fd 生命週期 race；可達性由 sysctl、module 載入、群組權限決定。
- 做 CVE triage，先問可達性，再問 root cause。

## 自我檢核

- [ ] 不看筆記，能說出評估子系統價值的四個維度，並用 ksmbd 與 perf 各舉一個極端。
- [ ] 能解釋為什麼 CVSS 10.0 的 ksmbd bug 不一定好利用。
- [ ] 能說出 CVE-2013-2094 的 root cause 屬於哪一類 bug，為什麼「型別寬度」會造成 OOB。
- [ ] 給你一個「vivid UAF」的 CVE，你能說出它在你的機器上是否可達、依據哪三個條件。
- [ ] 能說出 Syzkaller 怎麼在沒有實體硬體的情況下 fuzz USB，用到哪兩個核心元件。
- [ ] 面試問「為什麼 Android 的 GPU driver 是 LPE 熱門目標」，你能從可達性、vendor code、page-level 利用三個角度回答。
- [ ] 想一個你自己機器上有裝 `/dev/video*` 或 GPU 的情境，評估本章哪些 bug 對你可達。

## 延伸閱讀

### 部落格 / 技術文章

- **[kSMBd: a quick overview — Thalium](https://thalium.github.io/posts/ksmbd-trailer/)**
  - **讀哪裡**：ksmbd 架構概述與發現的 bug 整理表（含 CVE-2022-47939 等）。
  - **為什麼值得讀**：ksmbd 最好的外部入門，能看出這個子系統 bug 的整體風格；前提：知道 SMB2 協定的基本概念。
- **[Mind the Gap — Ian Beer, Project Zero（2022-11）](https://projectzero.google/2022/11/mind-the-gap.html)**
  - **讀哪裡**：Mali driver 的記憶體映射與 physical page UAF 的部分。
  - **和本章的關聯**：GPU driver 為什麼會有 page-level 的 primitive；前提：讀過 [Ch 6](./06-kernel-memory-layout.md) 與 [Ch 25](./25-usma-page-spray.md)。
- **[CVE-2023-4211 root cause analysis — Project Zero 0days in the wild](https://googleprojectzero.github.io/0days-in-the-wild/0day-RCAs/2023/CVE-2023-4211.html)**
  - **讀哪裡**：整篇 RCA，特別是 bug 發生在哪個 driver 結構與修補差異。
  - **學什麼**：一份「實際被 in-the-wild 使用」的 GPU driver bug 的完整 root cause，是讀 RCA 的範本。

### 官方文件 / 資料

- **[Syzkaller — external USB fuzzing 文件（`docs/linux/external_fuzzing_usb.md`）](https://github.com/google/syzkaller/blob/master/docs/linux/external_fuzzing_usb.md)**
  - **讀哪裡**：全篇，注意 `syz_usb_connect` 與 `dummy_hcd` 的設置步驟。
  - **為什麼值得讀**：了解「如何 fuzz 外部世界」的設計；前提：讀過 [Ch 43](./43-syzkaller.md) 的基本架構。
- **[Linux `Documentation/admin-guide/sysctl/kernel.rst`](https://www.kernel.org/doc/html/latest/admin-guide/sysctl/kernel.html)**
  - **讀哪裡**：`perf_event_paranoid` 一節。
  - **學什麼**：每一級的精確語意，核對本章的表。
- **[Alexander Popov — CVE-2019-18683 vivid 的 write-up（Habr 與其個人部落格）](https://a13xp0p0v.github.io/)**
  - **讀哪裡**：找「CVE-2019-18683」條目。
  - **和本章的關聯**：一個 v4l2 race 的 root cause 到 exploit 的完整旅程；前提：讀過 [Ch 40](./40-race-conditions.md)。

地圖畫完了：mainline Linux 的主要礦區我們都走過一遍。接下來看另一個世界：Android 與廠商 kernel，那裡 binder、ashmem 與數量龐大的廠商 driver 讓攻擊面完全不同。

→ [Ch 38 Android / 廠商 kernel 子系統：binder、ashmem、廠商 driver 生態](./38-android-vendor-subsystems.md)
