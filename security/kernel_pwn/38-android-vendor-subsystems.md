# Ch 38 — Android / 廠商 kernel 子系統：binder、ashmem、廠商 driver 生態

> **目標**：搞懂為什麼 Android 本地提權（LPE）的主戰場和桌面/伺服器 x86-64 kernel pwn 幾乎是兩個世界——binder 的 IPC 物件模型怎麼運作、為什麼它反覆出 UAF（CVE-2019-2215、CVE-2020-0041）、ashmem/ION/DMA-BUF 這類共享記憶體機制為什麼是另一個高發地（CVE-2021-0929、CVE-2020-11239）、GKI 與廠商 vendor driver 的分工怎麼讓「廠商自己寫的 GPU driver」成為 Android LPE 的主力礦區,以及 SELinux/seccomp/untrusted_app domain 這組縱深防禦怎麼改變攻擊者要先過哪些關卡。

> **環境**：以近期 mainline Linux（6.x）與 Android Common Kernel／GKI（Generic Kernel Image，5.10/5.15/6.1 分支）為參照架構。binder、ashmem、ION 這幾個子系統的程式碼在 AOSP `drivers/android/`、各廠商的 vendor tree 裡,不完全跟著 mainline LTS 版本走,本章談的是機制與歷史 CVE 的 root cause,不綁死某一個精確 kernel 版本。CVE 的 root cause 已用 WebSearch 查證 Google Project Zero、Synacktiv、GitHub Security Lab 等一手分析;查不到逐行細節的部分會明確標注。未實測處會標注（作者環境無 Android 裝置/模擬器可實測）。

## 為什麼 Android 的 kernel pwn 要單獨一章

Part 0–7 建立的心智模型——SLUB、heap spray、SMEP/SMAP、KASLR——在 Android 上**全部適用**，畢竟 Android kernel 就是 Linux kernel 加上一組 Android 專屬子系統。但 Android LPE 的實務面目和桌面 x86-64 差很大，原因有三層：

1. **攻擊面不同**：桌面 kernel pwn 的主戰場是 netfilter、io_uring、eBPF（[Ch 30–32](./30-netfilter-nftables.md)）；Android 裝置上這些子系統很多預設沒開或被限制，取而代之的主戰場是 **binder**（process 間通訊的骨幹）和**廠商自己寫的 driver**（尤其是 GPU）。
2. **信任邊界不同**：Android 有 app sandbox（每個 app 一個 UID + SELinux domain），一般 app（`untrusted_app` domain）能摸到的 syscall/ioctl 面比桌面一般 user 窄得多——但 binder 是個例外，所有 app 都要用它和 system server 通訊，所以它必然暴露在 untrusted_app 面前。
3. **程式碼來源不同**：mainline kernel 的子系統由社群/Google 維護、審查嚴格；廠商（高通、聯發科、三星……）為了支援自己的 GPU/modem/sensor 硬體,會在 vendor tree 加入大量**沒有經過同等審查強度**的程式碼。這是本章要強調的核心觀察：**Android LPE 的真正礦區,不是 Google 寫的 binder，而是廠商自己寫的 driver**。

> 如果你還沒讀過 [Ch 10](./10-vulnerability-taxonomy.md) 的漏洞分類詞彙、[Ch 19](./19-spray-objects.md) 的 spray 概念，這章會直接套用它們，不重新定義。

## 先建立直覺

把一台 Android 手機的 kernel 攻擊面想成一棟大樓的平面圖：Google 負責電梯、走廊、消防系統這些「每棟樓都要有、而且規格統一」的公共設施（GKI 核心部分）；每個廠商（建商）再依自己的需求,在自己的樓層裝修自己的設備間（vendor driver，GPU/modem/sensor）。**電梯（binder）天天被所有住戶使用、磨損檢驗最頻繁**，但真正容易藏汙納垢、沒人仔細檢查的地方,反而是**各家建商自己裝修的設備間**——那裡的管線走法、安全規範,各家不一樣,品質也參差不齊。

```
                     使用者 app（untrusted_app SELinux domain）
                              │
              ┌───────────────┼────────────────────┐
              ▼               ▼                     ▼
          binder          ashmem / ION            GPU / modem / sensor
       （IPC，GKI 核心）   （共享記憶體，GKI       （廠商 vendor driver，
                            核心 + 廠商擴充）        審查強度不一）
              │               │                     │
              ▼               ▼                     ▼
         system server    跨 process 共享頁        廠商自己的 kernel module
       （特權 process）    （native app 常用）      （.ko 或編譯進 vendor kernel）
```

這張圖的重點是**右邊的箭頭越往右、程式碼品質的變異度越大**，這決定了本章的敘事順序：先講 Google 維護的 binder（礦藏已被挖得差不多，但歷史案例教學價值極高），再講共享記憶體機制（GKI 核心 + 廠商擴充的交界），最後講廠商 driver（目前真正的主力礦區）。

## Binder：Android 的 IPC 骨幹

### IPC 模型

Binder 是 Android 的核心 IPC 機制——每一次 app 呼叫 Android Framework API（開相機、讀聯絡人、發通知……），底層幾乎都走 binder transaction 送到某個系統服務（`system_server` 或各個 HAL process）。

```
Process A（caller）                    Process B（callee，例如 system_server）
───────────────────                    ──────────────────────────────────
binder_transaction()                   binder_thread 從 todo list 取出 transaction
  組裝 binder_transaction_data          binder_transaction_buffer_release()
  （含 flat_binder_object、fd、        （處理完後釋放，含 fd 轉譯清理）
   BINDER_TYPE_PTR 等物件類型）
        │
        ▼
  binder_alloc：在雙方共享的 mmap 區域
  配置一塊 transaction buffer
        │
        ▼
  把資料複製進這塊 buffer，順便做
  物件類型的校驗與轉譯（fd 轉譯、
  指標轉譯成 parent/offset 關係）
        │
        └──────────────────────────▶  B 的使用者態程式讀到轉譯後的資料
```

**關鍵設計**：binder 不是單純把 bytes 丟過去,它要在 kernel 裡**理解並轉譯**傳輸資料裡的特殊物件——`BINDER_TYPE_BINDER`（binder 物件參照）、`BINDER_TYPE_HANDLE`、`BINDER_TYPE_FD`（檔案描述符,需要在兩個 process 之間轉譯成各自有效的 fd 號）、`BINDER_TYPE_PTR`/`BINDER_TYPE_FDA`（指標與 fd 陣列,需要做 offset 校驗與重新定位)。**這個「kernel 要解析並重寫 user 傳過來的結構」的設計,正是本章兩個經典 CVE 的共同根源**——這個模式和 [Ch 34](./34-filesystem-attack-surface.md) 的 fs parser「kernel 以 ring 0 權限 parse 不受信任的輸入」是同一種形狀,差別是輸入來源從 disk image 換成另一個 process 傳來的 binder transaction。

### CVE-2019-2215：`binder_thread_release()` 與 epoll 的生命週期不匹配

這是 Google Project Zero（Maddie Stone）在 2019 年分析、當時已被在野利用的一個 CVE。

root cause（Project Zero RCA 原文）：

- `struct binder_thread` 內嵌了一個 `wait_queue_head_t`（成員 `wait`）。
- `binder_poll()` 把這個**內嵌在 binder_thread 裡面**的 wait queue,交給 epoll 機制登記（當 app 用 epoll 監看 binder fd 的事件時）。
- `BINDER_THREAD_EXIT` 這個 ioctl 會呼叫 `binder_thread_release()`，把整個 `binder_thread`（連同內嵌的 `wait`）釋放掉。
- epoll 側（`eppoll_entry->whead`）仍然指著這塊已經釋放的記憶體。
- 之後 epoll 要移除這個監看項目時,呼叫 `remove_wait_queue()`，對著已釋放記憶體做 `spin_lock_irqsave()`——這是 KASAN 報的那次存取,緊接著 `__remove_wait_queue()` 對同一塊記憶體的 `task_list` 做 `list_del()`，給出一個**解鏈結寫入原語**。

```
binder_thread（內嵌 wait queue）              epoll（持有 wait queue 的指標）
─────────────────────────────                ──────────────────────────────
binder_poll() 註冊 thread->wait 給 epoll ───▶ eppoll_entry->whead = &thread->wait

ioctl(BINDER_THREAD_EXIT)
  binder_thread_release()
    free(binder_thread)   ← 連 thread->wait 一起釋放
                                               （epoll 側的指標沒有被通知、仍指著舊位置）

                                              之後移除這個 epoll 監看項目：
                                              remove_wait_queue(whead, ...)
                                                → spin_lock_irqsave(&已釋放記憶體)  【UAF】
                                                → list_del(...)                    【寫入原語】
```

Project Zero 的 RCA 指出：**kernel 本來就有 `POLLFREE` 機制處理「等待佇列比它所屬的物件先被釋放」這種情況**，但 binder 的這條路徑在當時沒有正確使用它——binder 原本「相安無事」是因為 user space 通常會持續地把監看項目移除又加回去，這個行為模式剛好繞開了這個 race 大部分時間的觸發窗口，但不是因為設計上真的安全。這也埋下了第二個問題：Project Zero 指出,修 CVE-2019-2215 時引入的 `POLLFREE` 本身又帶來了新的 race（後續追蹤為 CVE-2020-0030），說明**修補一個生命週期 bug,很容易在修補本身又留下新的時間窗口**。

### CVE-2020-0041：binder 物件轉譯邏輯的運算錯誤

這是另一個影響較廣的 binder CVE，Synacktiv 的分析（本章撰寫時已查證原文）指出 root cause 和表面名稱暗示的「UAF」不同，而是一個**整數運算方向錯誤**：

binder 在處理 `BINDER_TYPE_PTR`/`BINDER_TYPE_FDA` 這類需要相對於某個「parent」物件做 offset 校驗的結構時,要計算 `num_valid`（代表目前已驗證過的物件數量,用來限制 parent 索引的合法範圍)。這個計算在某個版本裡**把本該是乘法的運算寫成了除法**——用 Synacktiv 的具體數字舉例：某個 offset 差值本該乘上 `sizeof(binder_size_t)`，卻被除了，於是一個真實值該是 `0x2` 的數字被算成 `0x80`。

```c
/* 偽碼，還原 Synacktiv 分析描述的方向性錯誤；不是任何版本的原文逐行內容 */
/* 正確方向： */
num_valid = (offset_diff) * sizeof(binder_size_t);
/* 歷史上出問題的版本： */
num_valid = (offset_diff) / sizeof(binder_size_t);   /* 方向錯了，結果被嚴重放大 */
```

這個被放大的 `num_valid` 讓 `binder_validate_ptr()` 的範圍檢查**形同沒有檢查**——原本該被拒絕的、指向範圍外的 parent 索引，因為範圍上限被錯誤放大，通過了檢查。Synacktiv 的分析指出，攻擊者能藉此在驗證過程中操弄 parent 層級的順序,進而讓後續的 `binder_fixup_parent()`（負責把子物件的位置寫回 parent 結構裡）寫入一個被攻擊者控制的值到接收端的 transaction buffer——但 Synacktiv 同時指出 `binder_alloc_do_buffer_copy()` 把這個寫入限制在該 buffer 範圍內，所以這個原語本身不直接等於任意 kernel 記憶體寫入,還需要額外的步驟把它升級。

> 查證備註：這個案例展示了「CVE 的通俗敘述（常被簡化成『UAF』）」和「一手技術分析的實際 root cause」之間的落差——我們以 Synacktiv 的分析為準，不採用未經查證、僅憑標題猜測的分類。

### binder 現在的驗證鏈：多層檢查疊起來防這類 bug

本章撰寫時查證主線 binder 程式碼（`drivers/android/binder.c`），目前這條物件驗證鏈至少包含：`binder_validate_ptr()`（確認 parent 索引落在已驗證範圍內）、`binder_get_object()`（確認物件相對 buffer 的 offset 合法、型別已知）、`binder_validate_fixup()`（要求 fixup 的 offset 必須非遞減、且只能參照「目前已驗證、或其祖先」的物件）、`binder_fixup_parent()`/`binder_translate_fd_array()`（在真正寫入前,對 parent offset、對齊、fd 數量做額外檢查，防 fd-count 溢位），`binder_transaction_buffer_release()` 在清理路徑上重複一部分檢查。這是**同一類 bug（物件轉譯邏輯算錯）被抓到之後，防線逐層加厚**的典型例子——不是靠一次修補解決，而是靠一系列獨立的檢查疊起來降低「某一層漏檢就全盤失守」的機率。

把 `binder_translate_fd_array()` 這道檢查還原成通用的 bug 模式,可以看出它和 [Ch 35](./35-tty-keyring-watchqueue.md) 的 watch_queue filter 範例幾乎是同一個形狀（「宣告的數量」和「實際走訪用的索引」要對得上）：

```c
/* 偽碼，說明 fd 陣列轉譯類檢查該長什麼樣；不是任何版本的原文逐行內容 */
u32 fd_count = parent_fda->num_fds;             /* 來自 transaction data，攻擊者控制 */

/* 正確的檢查順序：先確認這個數量本身合理，再開始走訪 */
if (fd_count > (parent_buffer_size - fda_offset) / sizeof(u32))
        return -EINVAL;                          /* 拒絕「宣告的 fd 數量超出 parent buffer 能裝的範圍」 */

for (i = 0; i < fd_count; i++) {
        u32 fd = fd_array[i];                    /* 逐一轉譯每個 fd */
        /* ... 對應 process 間的 fd 轉譯邏輯 ... */
}
```

這裡的教訓和 [Ch 10](./10-vulnerability-taxonomy.md) 反覆強調的一致：**任何「先宣告一個數量、再依這個數量走訪陣列」的程式碼，數量本身的檢查要獨立存在,不能假設「後面的走訪邏輯自然會因為某個其他原因停下來」**。binder 現在的檢查鏈正是把這類獨立檢查一條一條補齊的結果。

## ashmem、ION、DMA-BUF：共享記憶體的攻擊面

### 為什麼 Android 需要這些機制

Android app 之間（例如 camera app 和它背後的 HAL process）大量需要共享大塊記憶體（相機 preview buffer、GPU 貼圖……），用 binder 一個 transaction 一個 transaction 複製資料效能太差。於是 Android 發展出專門的匿名共享記憶體機制：

- **ashmem**（Anonymous Shared Memory）：較早期的機制，透過一個特殊的字元裝置（`/dev/ashmem`）配置可以在 process 間用 fd 傳遞的共享記憶體區域。
- **ION**：Google 設計的統一記憶體分配器，目的是讓不同子系統（GPU、camera、video codec）透過同一套 API（`ion_alloc`/`ioctl`）配置可被硬體 DMA 存取的記憶體,並能以 fd 的形式跨 process 傳遞。
- **DMA-BUF**：更上游、更通用的核心框架（非 Android 專屬），ION 本身最終是建立在 DMA-BUF 之上。近年 Google 推動把 ION 棄用、改用標準的 DMA-BUF heap，但就我所知許多廠商的 vendor kernel 仍長期保留 ION 相容層。

### ashmem 的物件模型與它的歷史問題

`ashmem` 比 ION 更早、更單純：`open("/dev/ashmem")` 拿到一個 fd,透過 `ioctl(fd, ASHMEM_SET_SIZE, size)` 設定大小,再 `mmap()` 就能拿到一塊可以透過 `SCM_RIGHTS`（[Ch 33](./33-socket-skb.md) 講過的機制）或直接傳 fd（binder transaction 裡的 `BINDER_TYPE_FD`）分享給別的 process 的記憶體。它額外提供 `ASHMEM_PIN`/`ASHMEM_UNPIN` 這組操作,讓 kernel 在記憶體壓力大時可以把「目前沒被 pin 住」的 ashmem 區域直接回收（類似一種使用者可控的 LRU 提示機制）。

```
process A                                    process B
──────────                                   ──────────
open("/dev/ashmem")
ioctl(ASHMEM_SET_SIZE, size)
mmap() 拿到一塊可寫記憶體
  │
  │  透過 binder transaction 的 BINDER_TYPE_FD
  └──────────────────────────────────────────▶ 收到同一個 fd（指向同一塊記憶體）
                                                mmap() 自己再映射一次
                                                → A、B 現在共享同一塊實體記憶體
```

`ASHMEM_PIN`/`ASHMEM_UNPIN` 這組「可以被 user 觸發的記憶體回收」機制,歷史上和 ION 的經驗類似,是生命週期管理複雜度的來源之一：一塊被某個 process 標記為 unpinned（可回收）的區域,如果另一個仍持有同一塊記憶體 mapping 的 process 在回收發生的同一時刻存取它,就有機會碰到和 [Ch 18](./18-uaf-double-free.md) 講的 UAF 同一類時序問題。這裡我們不列出具體 CVE 編號——WebSearch 查到的 ashmem 相關歷史 CVE 多半年代久、資訊零散,不足以支撐逐行 root cause 分析,只強調這個「使用者可控回收時機 + 跨 process 共享」的結構性風險和 ION 是同一個家族。

### CVE-2021-0929 與 CVE-2021-39714：ION 自己的 UAF

- **CVE-2021-0929**：`ion.c` 裡 `ion_dma_buf_end_cpu_access()` 一類函式的 use-after-free（CVSS 7.8，Android 2021 年 11 月公告收錄）。WebSearch 查到的摘要描述根因是記憶體生命週期管理，具體的「哪個引用計數分支漏了配對」的逐行細節,公開摘要沒有展開到可以直接還原程式碼的程度,這裡只能描述「屬於哪一類」。
- **CVE-2021-39714**：`ion_buffer_kmap_get()` 的 UAF，根因是一個**整數溢位**（和前面 binder 的運算錯誤同一個大類別：欄位計算錯誤 → 後續邏輯基於錯誤的數字做出錯誤判斷）。

兩者共同點：ION 的設計目標是「讓硬體能直接存取 user 配置的記憶體」，這代表它要管理的生命週期比一般 heap 物件更複雜——一塊 ION buffer 可能同時被 CPU 存取、被 GPU 透過 DMA 存取、被另一個 process 透過 fd 共享存取，三方的「誰還在用」狀態要同步,這正是 UAF 的溫床。

### CVE-2020-11239：Qualcomm KGSL（GPU driver）透過 DMA-BUF 的 UAF

這是展示「GPU driver 吃進 DMA-BUF 物件後自己出錯」的案例（GitHub Security Lab / Project Zero 系列研究，作為 Chrome-to-kernel 完整利用鏈的 kernel 階段使用）。

root cause：Qualcomm 的 KGSL（kernel graphics support layer，Adreno GPU 的 kernel driver）透過 `IOCTL_KGSL_GPUOBJ_IMPORT` 或 `IOCTL_KGSL_MAP_USER_MEM` 把一個 DMA-BUF 物件匯入成 KGSL 自己管理的物件。問題在於匯入之後的生命週期管理和原本 DMA-BUF 的生命週期管理沒有正確同步——後續對這個已經（從 KGSL 的角度）被釋放的物件做 DMA-BUF sync 操作（`DMA_BUF` 相關呼叫）,觸發 UAF。研究指出這個 bug 可以在 kernel 版本 4.14 以上的裝置觸發,覆蓋了 2019 年之後發布的許多中高階手機。

這個案例的教學意義:**bug 不在 ION/DMA-BUF 框架本身，而在「廠商 GPU driver 吃進這個框架提供的物件之後,自己的生命週期管理」**。這正是下一節要展開的核心論點。

## 為什麼廠商 driver 是 Android LPE 的主力礦區

### GKI 與 vendor hook 生態

Google 從 Android 11 起推動 **GKI（Generic Kernel Image）**：核心 kernel image 由 Google 統一編譯、提供給所有裝置廠商使用,廠商不能再隨意修改核心部分。廠商自己的驅動（GPU、modem、sensor、相機 ISP）則透過**vendor module（.ko）** 或在 GKI 定義好的**vendor hook**（核心程式碼裡預留的、讓廠商程式碼可以掛進去的擴充點）來實作。

```
                    Google 維護、統一編譯
                 ┌───────────────────────┐
                 │   GKI boot.img         │  ← binder、ashmem/DMA-BUF 核心框架在這裡
                 │   （所有裝置共用同一份） │
                 └───────────┬───────────┘
                              │ vendor hook（預留的掛載點）
                 ┌───────────┴───────────┐
                 │  vendor_boot.img       │  ← 廠商自己的 .ko（GPU/modem/sensor driver）
                 │  （各廠商各自編譯）     │     審查強度、測試覆蓋率因廠商而異
                 └────────────────────────┘
```

這個架構的安全意涵：**GKI 核心部分被 Google 統一審查、被 syzkaller 持續 fuzz、有完整的 CI,品質相對有保證；vendor 部分則完全看各廠商自己的工程文化**。一個中小型廠商的 GPU driver 團隊,人力和審查流程通常比不上 Google 的核心團隊——這直接反映在歷史 CVE 的分布上:近年公開的 Android kernel LPE，相當高比例落在 Mali（ARM GPU）、Adreno（Qualcomm GPU，KGSL driver）這類廠商 GPU driver,而不是 binder/ashmem 這些 GKI 核心部分。

### 為什麼偏偏是 GPU driver

- **攻擊面天生暴露**：一般 app 要畫畫面就要用到 GPU，所以 GPU driver 的 ioctl 介面必然對 `untrusted_app` domain 開放,不像某些廠商 driver（例如 modem 控制）可能限制在更高權限的 domain。
- **ioctl 介面複雜**：GPU driver 的 ioctl 常常直接對映硬體的複雜特性（記憶體管理、同步原語、命令緩衝區），參數結構又大又多,天生就是 bug 密度高的那種介面（類比 [Ch 34](./34-filesystem-attack-surface.md) 講的「輸入空間大、結構複雜」礦區特徵）。
- **跟硬體版本綁定**：同一家廠商不同世代的 GPU,driver 程式碼經常是從舊版本複製修改而來,舊 bug 容易沿著版本演進被一路帶下來。
- **測試資源不均**：Google 自己的 syzkaller 主要 fuzz GKI 核心部分；廠商是否對自己的 vendor driver 跑同等強度的 fuzzing,因廠商而異（就我所知，部分廠商近年已經開始內部導入 syzkaller/KASAN,但覆蓋率沒有公開統一的資料）。

## 為什麼 Android 攻擊面和桌面不同：防禦疊的差異

一個 app 從 Zygote fork 出來,到真正能呼叫到某個廠商 driver 的 ioctl,中間要穿過好幾層檻,每一層都可能擋下一條路：

```
Zygote fork 出新 app process
       │
       ▼
   套用 seccomp-bpf 過濾器 ──── 擋掉一整批 syscall（例如部分架構管理類、
       │                        某些較少用的記憶體/行程控制 syscall）
       ▼
   設定 SELinux domain（untrusted_app）
       │
       ▼
  app 嘗試 open() 某個裝置節點 ─── SELinux policy 檢查這個 domain
       │                           能不能對這個節點的 type 做 open
       ▼ （通過）
  app 對這個 fd 做某個 ioctl ──── SELinux policy 可以再細到
       │                           「這個 domain 能對這個節點的
       │                            type 呼叫哪些 ioctl number」
       ▼ （通過）
  真正進入 kernel driver 的程式碼
```

binder 節點（`/dev/binder`）和多數 GPU 節點（例如 `/dev/kgsl-3d0`）在絕大多數裝置的預設 SELinux policy 裡,對 `untrusted_app` domain 是開放的——這不是政策的漏洞,是因為一般 app **正常運作就需要**用它們做 IPC 和畫面渲染。這正是為什麼它們即使經過層層收緊,仍然是攻擊者視角下「確定能摸到」的少數幾條路。

| 防禦層 | 桌面/伺服器 Linux | Android |
|---|---|---|
| user → kernel 的身分檢查 | 主要靠 `uid`/`gid`/capability | `uid` + **SELinux domain**（每個 app 有自己的 domain，`untrusted_app` 是一般 app 的預設 domain） |
| syscall 可達性 | 一般 user 幾乎能呼叫所有 syscall（除非 seccomp 限制） | app 透過 Zygote fork 出來後,**預設套用 seccomp-bpf 過濾器**,限制能呼叫的 syscall 清單 |
| ioctl 可達性 | 看裝置節點的檔案權限 | 裝置節點權限 + SELinux 的 ioctl allowlist（細到特定 ioctl number 都能單獨管控） |
| mount/namespace 類攻擊面 | user namespace 常是關鍵前提（見 [Ch 36](./36-user-namespace.md)） | 一般 app **不能** `unshare(CLONE_NEWUSER)`（SELinux 政策限制),這整條在桌面上很重要的路徑在 Android 上基本不可達 |
| 攻擊面縮減的效果 | 看發行版/管理員設定,差異大 | 平台層級統一要求（SELinux 政策、seccomp 過濾器隨 Android 版本持續收緊） |

這張表解釋了為什麼本書前面 Part 6 講的「透過 `unshare(CLONE_NEWUSER)` 放大攻擊面」這條路徑,在 Android 上多數情況走不通——`untrusted_app` domain 的 SELinux 政策通常直接禁止建立新的 user namespace。這也是為什麼 Android LPE 反而更依賴 **binder（所有 app 都必然能摸到）** 和 **廠商 driver 的 ioctl（只要 SELinux policy 允許 `untrusted_app` 存取這個裝置節點）** 這兩條路徑——它們是在層層收緊的 SELinux/seccomp 之下,少數幾條確定對一般 app 開放的通道。

## 利用思路（概念層級）

1. **binder 物件轉譯類 bug 的升級路徑**：CVE-2020-0041 這類「校驗邏輯計算錯誤,讓本該被拒絕的索引通過」的 bug,升級路徑是找出「校驗錯誤能讓你控制到什麼範圍」，再看這個範圍能不能碰到有價值的目標（例如另一個 transaction buffer 的內容，或透過反覆操作把這個寫入原語升級成更通用的任意寫）。這和 [Ch 10](./10-vulnerability-taxonomy.md) 「bug → 原語 → 原語升級」的三段式完全一致。
2. **生命週期不匹配類 bug（CVE-2019-2215、ION 系列）**：核心問題都是「兩個子系統對同一塊記憶體的生命週期假設不一致」。利用思路是先確認「誰先釋放、誰的指標還活著」，再決定要走哪種升級——UAF 讀寫原語到手之後,走 [Ch 18](./18-uaf-double-free.md)/[Ch 21](./21-heap-to-rip.md) 的標準流程。
3. **GPU driver ioctl 的 fuzzing 導向思路**：面對一個不熟的廠商 driver,實務上的做法通常是先列出它暴露的 ioctl 清單（`/sys/kernel/debug/`、反編譯 HAL library 呼叫這些 ioctl 的方式），再比照 [Ch 43 — Syzkaller](./43-syzkaller.md) 的方法論寫 syscall 描述檔去 fuzz，而不是從零手讀整個 driver 原始碼（廠商 driver 很多沒有公開原始碼,只能拿到二進位的 .ko）。

## 對比取捨表

| 子系統 | 誰維護 | 審查/fuzz 強度 | 歷史 bug 類型 | 對一般 app 的可達性 |
|---|---|---|---|---|
| binder | Google（GKI 核心） | 高（長期被 syzkaller/Project Zero 重點關注） | UAF（生命週期）、物件轉譯邏輯錯誤 | 高（所有 app 必然使用） |
| ashmem/ION/DMA-BUF | Google 設計框架 + 廠商擴充 | 中（框架本身審查較嚴,廠商擴充部分不一） | UAF、整數溢位 | 中（看 SELinux policy 開放程度） |
| 廠商 GPU driver（Mali/Adreno） | 各廠商自行維護 | 低到中，因廠商而異 | 各種（UAF、OOB、邊界檢查缺失） | 高（畫面渲染必然使用，untrusted_app 通常能摸到） |
| 廠商 modem/sensor driver | 各廠商自行維護 | 低到中 | 各種 | 視 SELinux policy，常比 GPU 更受限 |

## 踩雷集錦

1. **「binder 的 bug 已經挖得差不多，不值得再看」**：binder 本身新 CVE 的出現頻率確實比 2015–2020 年代低,但它的驗證鏈模式（物件轉譯、offset 校驗）是理解其他「kernel 解析不受信任結構化輸入」子系統的最佳教材,價值不只在找新 bug,也在建立分析方法。
2. **「CVE 名稱裡有『use-after-free』就一定是 UAF」**：CVE-2020-0041 常被通俗地描述成 UAF,但 Synacktiv 的一手分析指出根因其實是整數運算方向錯誤導致的 OOB。永遠去讀一手分析,不要只看二手摘要裡的分類標籤。
3. **「GKI 讓所有 Android kernel 都一樣安全」**：GKI 統一的只是**核心框架**部分;廠商的 vendor driver（尤其 GPU）完全在 GKI 的審查範圍之外,不同廠商、不同型號的實際攻擊面差異可能很大。
4. **「untrusted_app 什麼都摸不到」**：SELinux 政策確實限制很多,但 binder、以及被 policy 明確允許存取的裝置節點（常包含 GPU）仍然在可達範圍內——縮小攻擊面不代表攻擊面變成零。
5. **「ION 已經被 DMA-BUF heap 取代,不用再管」**：就我所知,棄用是演進方向,但許多現役裝置的 vendor kernel（尤其是較舊的 GKI 分支）仍保留 ION 相容層;評估一台具體裝置的風險前,先確認它實際跑的是哪一套,不要套用「新版一定已經換掉」的假設。
6. **「GPU driver 的 CVE 都可以直接套用到所有裝置」**：廠商 GPU driver 的程式碼經常因 SoC 世代、甚至同一世代不同廠商客製化版本而有差異,一個特定型號上驗證過的 CVE,不能直接假設在另一台用同款 GPU、不同廠商客製韌體的裝置上一樣可觸發——實務上要先確認目標裝置的 kernel 版本與廠商 patch 狀態。
7. **「SELinux 擋掉 ioctl 就代表這個 driver 的 bug 完全沒用」**：即使 `untrusted_app` domain 不能直接呼叫某個 ioctl,一旦攻擊者先透過另一條路徑拿到某個權限較高的 domain（例如先利用一個權限較低但可達的 bug 提升到 `system_app` 或某個 HAL domain),原本摸不到的 ioctl 可能就進入可達範圍——這是多階段利用鏈常見的「先換身分、再換攻擊面」模式。

## 進階：再往深一層

- **從 Chrome 到 kernel 的完整利用鏈**：GitHub Security Lab 的 "One day short of a full chain" 系列展示了從瀏覽器 render process 一路打到 kernel 的完整過程,KGSL 的 UAF（CVE-2020-11239）是其中的 kernel 階段。讀這類完整鏈的價值在於理解**單一子系統的 bug 在真實攻擊鏈裡扮演什麼角色**,不是孤立的技術展示。
- **ARM64 特有的 mitigation 如何影響 Android exploit**：Android 裝置是 ARM64 架構為主,PAC（Pointer Authentication）、MTE（Memory Tagging Extension）這些硬體級 mitigation 在新裝置上逐步普及,大幅改變了「拿到一個寫入原語之後能做什麼」的計算——[Ch 46](./46-arm64-kernel-pwn.md) 會展開這部分,binder/ION 的 bug 本身不變,但後續利用鏈要重新設計。
- **廠商安全公告（如高通、三星的月度公告）是另一個資料源**：不是所有廠商 driver 的 CVE 都走標準 Android Security Bulletin,部分僅出現在廠商自己的公告頁。長期追蹤某個廠商生態的攻擊面,需要同時關注兩邊。
- **vendor hook 本身的攻擊面**：GKI 的 vendor hook 機制本身（讓廠商程式碼掛進核心路徑的那組介面）是否可能被設計不當而成為獨立攻擊面,是近年學術與工業研究社群持續關注的方向,屬於這個生態系統本身演進中的議題。

## 動手練習

> 以下描述**未實測**，為理論預期行為（作者環境無 Android 裝置/AOSP 編譯環境可驗證）。以下操作一律預設在**你自己擁有的裝置、或 Android 模擬器/AOSP 原始碼樹**內進行。

1. 下載 AOSP 原始碼（或單獨 clone `android/kernel/common` 的 GKI 分支），讀 `drivers/android/binder.c` 的 `binder_validate_ptr()`、`binder_get_object()`、`binder_validate_fixup()` 三個函式,畫出它們之間的呼叫關係與各自負責的檢查項目。
2. 在 AOSP 原始碼裡搜尋 `num_valid` 的歷史修補 commit（`git log --all --grep='num_valid'` 或類似搜尋）,核對本章對 CVE-2020-0041 的描述是否與實際 diff 一致。
3. 用 `adb shell getenforce` 確認你手上的 Android 裝置（或模擬器）的 SELinux 模式,並用 `adb shell ls -laZ /dev/` 觀察哪些裝置節點對 `untrusted_app` domain 開放（搭配 `sesearch` 或 `seinfo` 工具分析 policy，如果你的環境有裝）。
4. 找一份公開的 Android GPU driver 原始碼（例如某些廠商有開源部分 Mali driver 的使用者態元件，或讀 mainline 的 `drivers/gpu/drm/` 作對照），列出它暴露的 ioctl 清單,練習評估「如果我要 fuzz 這個 driver,第一個該關注的 ioctl 是哪個，為什麼」。
5. 讀 GitHub Security Lab 的 "One day short of a full chain" 系列文章，畫出從瀏覽器 render process 到 kernel 提權的完整攻擊鏈示意圖，標出 CVE-2020-11239 在這條鏈裡的位置與作用。
6. 用 `adb shell dumpsys package <某個 app>` 或讀 AOSP 的 `system/sepolicy/` 原始碼,找出 `untrusted_app` domain 的 SELinux policy 定義,列出它被允許存取的裝置節點清單,和你在本章學到的 binder/GPU 節點對照,確認它們確實在清單內。

## 本章重點整理

- Android kernel pwn 的攻擊面和桌面不同，核心原因是信任邊界（SELinux domain + seccomp）與程式碼來源（GKI 核心 vs 廠商 vendor driver）的雙重差異。
- binder 是所有 app 必然使用的 IPC 骨幹，它的物件轉譯邏輯（處理 `BINDER_TYPE_PTR`/`FDA` 等）是歷史上多個 CVE 的根源；CVE-2019-2215 是生命週期不匹配（epoll 與 binder_thread 的 wait queue），CVE-2020-0041 是校驗邏輯的整數運算方向錯誤。
- ashmem/ION/DMA-BUF 作為跨 process 共享記憶體機制，生命週期要在 CPU/GPU/多 process 之間同步，這個複雜度本身就是 UAF 溫床（CVE-2021-0929、CVE-2021-39714）。
- 真正的 Android LPE 主力礦區是**廠商 GPU driver**（CVE-2020-11239 為代表），因為它天生暴露給 untrusted_app、審查強度因廠商而異、ioctl 介面複雜。
- GKI 把核心框架統一、交給 Google 審查,但廠商 vendor driver 仍在審查範圍外——這個分工本身就是理解現代 Android 攻擊面分布的關鍵。

## 自我檢核

- [ ] 不看筆記，畫出一次 binder transaction 從 caller 到 callee 的流程,並指出哪個環節涉及「kernel 解析不受信任的結構化輸入」。
- [ ] 能解釋 CVE-2019-2215 裡,`binder_thread` 和 epoll 之間的生命週期不匹配具體是什麼意思,畫出時間序列圖。
- [ ] 能說出為什麼 CVE-2020-0041 的根因不是字面上聽起來的 UAF,以及這對你讀新 CVE 摘要時的習慣有什麼提醒。
- [ ] 能用 SELinux domain + seccomp 的框架,解釋為什麼 user namespace 放大攻擊面這條路在桌面上重要、在 Android 上多數時候走不通。
- [ ] 面試官問「為什麼 Android LPE 現在主要打 GPU driver 而不是 binder」，你能從審查強度、攻擊面暴露程度兩個角度回答。

## 延伸閱讀

### 官方文件 / 原始碼

- **[Android Common Kernel（AOSP `drivers/android/binder.c`）](https://android.googlesource.com/kernel/common/+/refs/heads/android-mainline/drivers/android/binder.c)**
  - **讀哪裡**：`binder_transaction()`、`binder_validate_ptr()`、`binder_get_object()` 幾個函式。
  - **為什麼值得讀**：這是本章 binder 物件模型與驗證鏈的一手來源,也是 Android IPC 子系統最核心的程式碼。
- **[Android Developers — GKI 架構文件](https://source.android.com/docs/core/architecture/kernel/generic-kernel-image)**
  - **讀哪裡**：GKI 與 vendor module 的分工說明。
  - **學什麼**：本章「核心 vs 廠商」分工論述的官方依據。

### 漏洞資料 / 一手分析

- **[Google Project Zero — CVE-2019-2215 Root Cause Analysis](https://googleprojectzero.blogspot.com/p/rca-cve-2019-2215.html)**
  - **讀哪裡**：全文，重點是 `binder_poll`/epoll 生命週期不匹配的描述,以及文末提到修補本身又引入 CVE-2020-0030 的部分。
  - **和本章的關聯**：本章 binder UAF 案例的主要依據，也是「修補可能引入新 race」這個教訓的直接例證。
- **[Synacktiv — Binder: Analysis and Exploitation of CVE-2020-0041](https://synacktiv.com/publications/binder-analysis-and-exploitation-of-cve-2020-0041)**
  - **讀哪裡**：root cause 段落（`num_valid` 計算方向錯誤）與後續利用鏈分析。
  - **為什麼值得讀**：目前查到對這個 CVE 最完整的一手技術分析,糾正了「這是 UAF」的常見誤解。
- **[GitHub Security Lab — One day short of a full chain: Part 1](https://github.blog/security/vulnerability-research/one-day-short-of-a-full-chain-part-1-android-kernel-arbitrary-code-execution/)**
  - **讀哪裡**：KGSL/CVE-2020-11239 的 root cause 與完整利用鏈部分。
  - **和本章的關聯**：本章「廠商 GPU driver 是主力礦區」論述的具體案例，也示範了單一 kernel bug 在完整攻擊鏈裡的位置。

### 書籍 / 系列

- **《Android Security Internals》** — Nikolay Elenkov（No Starch Press, 2014）
  - **這本書的定位**：涵蓋 Android 4.x 時代的安全架構,年代較久但 SELinux/權限模型/binder 的基礎概念到今天仍大致適用,適合建立整體架構的心智模型。
  - **讀哪幾章**：Binder IPC 章節、權限模型章節；app sandbox 與較新版本（Android 10+）的 SELinux 收緊措施需另外查最新文件補充。
  - **前提知識**：基本 Linux 權限模型、本書 Part 0 的 kernel 基礎。

Android/廠商子系統展示了「同一套 kernel exploitation 技巧,放進不同的信任邊界和程式碼生態,會長出完全不同的主力礦區」。下一個 Part 要回到一個更基礎、橫跨所有平台的主題——kernel 的並發模型,這是看懂與製造 race condition 漏洞的機制前置。

→ [Ch 39 — kernel 並發模型：lock 家族、RCU、per-cpu、記憶體屏障、race window](./39-concurrency-model.md)
