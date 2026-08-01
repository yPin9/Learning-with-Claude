# 練習 D — virtio CVE 復刻：從 patch 反推洞、規劃利用路徑

> **目標**：給定一個 virtio CVE 的 patch commit，逆向還原 root cause、畫出觸發時序、規劃完整利用鏈。

> **環境**：QEMU ≤ 6.1.x（分析對象）；QEMU 9.0（對照用）；Linux host；需要能讀 QEMU 原始碼（`git clone` + `git show`）。

---

## 背景與動機

漏洞研究的日常工作有很大一部分是「1-day」——廠商已出 patch，我們要從 diff 倒推 bug 在哪、怎麼觸發。這不是走捷徑，是研究效率：commit message 往往含 root cause 描述，patch 改動範圍直接劃出攻擊面，比盲目審計快十倍。更重要的是，多數 CVE 的修補窗口很短，攻擊者需要在 patch 公開後數小時到數天內完成分析並開發武器化 exploit，這個「從 patch 反推」的能力是 1-day 研究者的核心競爭力。

我們選 **CVE-2021-3748** 作為主任務，理由如下：

1. **對應前序課程**：這是 Ch 26 Bug 2 的教科書案例——VirtQueueElement（虛擬佇列元素）生命週期管理錯誤，正是我們在 Ch 25 virtio 架構章節討論的 virtqueue 流程。讀完 Ch 25-28 後再看這個 CVE，每個細節都有脈絡。

2. **Patch diff 公開**：commit `bedd7e93d01961fcb16a97ae45d93acf357e11f6` 在 QEMU GitLab 公開可查，改動集中——單一函式的幾行修改就能引出整條 UAF（use-after-free，釋放後使用）路徑。diff 本身就是 bug 的路線圖。

3. **利用鏈可接前課**：UAF 後的 heap groom（堆積佈局整理）可接 Ch 17 資訊洩漏、Ch 21 函式指標劫持、Ch 22 ROP 鏈，形成完整的 VM escape 路徑。這個 CVE 是把前三個 Part 串起來的好媒介。

4. **Bug 類型具代表性**：時序問題（time-of-use vs. time-of-free 的順序錯誤）是 QEMU/KVM 漏洞裡非常常見的模式，理解了這個，看其他類似 CVE 會快很多。

**誠實說明**：這個練習以分析為主。完整 exploit 需要精確的舊版 QEMU 環境加上 vIOMMU（虛擬 I/O 記憶體管理單元）支援，加上對目標版本 glibc heap allocator 的 bin 佈局有深入分析，超出教材環境範疇。觸發骨架與利用路徑均標示「未實測，理論預期」。分析能力才是這個練習要訓練的核心。

---

## CVE 快速索引

| 欄位 | 內容 |
|------|------|
| CVE ID | CVE-2021-3748 |
| 影響版本 | QEMU ≤ 6.1.x |
| 影響元件 | `hw/net/virtio-net.c`，`virtio_net_receive_rcu` |
| Bug 類型 | UAF（use-after-free，釋放後使用）— VirtQueueElement bounce buffer |
| 觸發條件 | virtio-net + mrg_rxbuf 協商開啟 + non-direct access region（vIOMMU 或同等） |
| Patch commit | `bedd7e93d01961fcb16a97ae45d93acf357e11f6` |
| 修復邏輯 | 把 `num_buffers` 的 `stw_p` 寫入移到 `address_space_unmap` 之前 |
| 對應 Ch 26 | Bug 2：VirtQueueElement 生命週期管理錯誤 |
| 延伸 CVE | CVE-2024-3446（DMA reentrancy double free，對應 Ch 26 Bug 1） |
| CVSS 分數 | 8.8（高，需 guest 控制），vector：AV:L/AC:H/PR:H/UI:N |

---

## 任務規格

### Task 1：patch diff 分析

取得 patch 並細讀，然後回答下列問題。這些問題的設計讓你必須理解 diff 的語義，而不只是看「哪幾行被移動」。

**取得 patch**：

```bash
git clone https://gitlab.com/qemu-project/qemu.git
cd qemu
# 只看 virtio-net.c 的改動，避免被其他無關改動干擾
git show bedd7e93d01961fcb16a97ae45d93acf357e11f6 -- hw/net/virtio-net.c
```

也可以直接在 QEMU GitLab 的 web 介面讀：
`https://gitlab.com/qemu-project/qemu/-/commit/bedd7e93d01961fcb16a97ae45d93acf357e11f6`

**問題清單**（自己先想，再對照 `<details>` 區塊）：

1. Patch 修改了哪個函式？在哪個源檔（給出相對路徑）？

2. `num_buffers` 的寫入位置：patch 前在 `address_space_unmap` 的哪一側（前還是後）？patch 後呢？

3. 這個順序差別為什麼重要？思考以下問題鏈：
   - `address_space_unmap` 做了什麼？它釋放的是什麼記憶體？
   - 什麼情況下 `address_space_map` 會分配額外的記憶體（bounce buffer）而不是直接回傳 host RAM 指標？
   - 釋放後，原本的指標（`elem->in_sg[0].iov_base`）變成什麼？
   - 對這個指標做寫入，從 allocator 的角度看會發生什麼？

4. Patch commit message 有沒有提到這個 bug 影響了哪幾條路徑？fix 是只改了一條路徑，還是所有有問題的地方都改了？如果只改了一條，其他路徑為什麼沒問題（或者有沒有後續 patch）？

5. 這個 CVE 影響 QEMU ≤ 6.1.x。QEMU 9.0 的對應函式是否還有類似的「unmap 後存取 elem 成員」的模式？用 `git show` 或 GitLab 確認目前版本是否已完全乾淨。

**讀 diff 的方法論**：遇到「幾行程式碼被移動順序」這類 patch，我們的分析流程是：(1) 找被移動的那個寫入操作的語義（寫什麼、寫到哪）；(2) 找「被移動相對位置的那個操作」的語義（這裡是 `address_space_unmap`，它做了什麼）；(3) 問「原本的順序在什麼情況下有問題」——有 bug 的 case 往往是某個特定路徑（這裡是 bounce buffer 路徑），直接路徑沒問題。這個思路是「patch 驅動的 bug 尋找」的標準流程，值得熟悉。

另外要注意：不是每個 bug fix 都只動一行。有時 patch 同時改了測試、文件、甚至其他相關函式。讀 diff 時要先過濾掉無關改動（測試、格式調整），找到核心邏輯變更。用 `git show --stat` 先看改動了哪些檔案，再針對性讀 `hw/net/virtio-net.c` 的部分。

---

### Task 2：root cause 還原

讀 `hw/net/virtio-net.c` 的 `virtio_net_receive_rcu`，追 VirtQueueElement 生命週期。這個任務的目的是自己走一遍 bug 的邏輯，不要只背 CVE 描述。

**目標 1：找出 non-direct access region 路徑的分叉點**

讀程式碼前先定義「生命週期」的含義：VirtQueueElement 從 `virtqueue_pop` 建立，到 `virtqueue_push` 通知 guest，到 `g_free(elem)` 釋放（在 `virtqueue_pop` 的內部或由呼叫方負責）。bounce buffer 是依附在 elem 上的額外記憶體，理論上應該跟 elem 的使用週期一致，但 patch 前的程式碼沒有做到這一點。

從 `virtio_net_receive_rcu` 開始往內追：

```
virtio_net_receive_rcu
  └─ virtqueue_pop()
       └─ virtqueue_map_desc()
            └─ address_space_map()
                 └─ (核心判斷在這裡：是否直接映射？)
```

關鍵函式在 `softmmu/memory.c`（舊版）或 `system/physmem.c`（新版），搜尋 `is_direct_romd` 或 `memory_access_is_direct`。理解判斷邏輯：

- 若 GPA（guest physical address，客體實體位址）有直接對應的 host RAM→回傳 host 指標，不額外分配。
- 若沒有（IOMMU 轉譯後的位址、ROM region、MMIO-backed region 等）→`g_malloc` 一塊 bounce buffer，`elem->in_sg[0].iov_base` 指向這塊新記憶體。

**目標 2：畫出觸發 UAF 的完整時序圖**

以下是 bug 路徑的骨架，你要在每個箭頭旁邊標注「這一步做了什麼，對 `elem->in_sg[0].iov_base` 的影響是什麼」：

```
[guest]                              [QEMU host process]
填入 descriptor ring（GPA 在 non-direct region）
                                ←    virtqueue_pop()
                                         virtqueue_map_desc()
                                           address_space_map()
                                             g_malloc(bounce_buf)
                                             elem->in_sg[0].iov_base = bounce_buf
                                ←    (QEMU 把封包資料複製到 bounce_buf)
                                ←    virtqueue_push(q->rx_vq, elem, total)
                                         把 elem 標記為已完成，通知 guest
                                ←    address_space_unmap(...)
                                         g_free(bounce_buf)   ← bounce_buf 已釋放
                                ←    [BUG] stw_p(elem->in_sg[0].iov_base + offset,
                                              num_buffers)
                                         ← 此時 iov_base 還是指向已被 g_free 的 bounce_buf
                                         ← 這是 dangling pointer 寫入 = UAF
```

把這個時序圖用你自己的話重新畫一遍——不要直接複製，要能自己重建。

**目標 3：確認觸發前提**

回答這個問題：要讓 desc 落在 non-direct access region，guest 端需要用什麼機制？

- 普通的 `malloc()` 分配的虛擬記憶體，其 GPA 通常有直接 RAM 映射。這樣的 buffer 傳給 virtio-net 會走直接映射路徑，觸發不了 bug。
- 需要什麼樣的 guest 配置，讓 virtio-net 的 DMA buffer 的 GPA 落在 non-direct region？

思考方向：vIOMMU 如何改變 GPA→HPA（host physical address）的映射關係，QEMU 如何看待 IOMMU 轉譯後的 GPA。

**目標 4：理解 `merged rx buffers`（mrg_rxbuf）的作用**

這個 bug 只在 `mrg_rxbuf` 協商開啟的情況下有意義。`num_buffers` 欄位是 merged rx buffer 機制的一部分：當一個封包需要跨多個 RX descriptor 才能放下，QEMU 要在第一個 descriptor 的 header 裡寫入「這個封包用了幾個 buffer」。這個機制讓 guest driver 知道要跳過幾個 descriptor 才到下一個封包的起點。

確認 `mrg_rxbuf` 在 virtio-net 的協商過程：

```bash
# 在 guest 端確認 virtio-net feature bits
cat /sys/bus/virtio/devices/virtio0/features
# 應看到 VIRTIO_NET_F_MRG_RXBUF (15) 這個 bit
```

若 `mrg_rxbuf` 沒有協商成功，`num_buffers` 的寫入會走不同路徑，UAF 的觸發條件也不同。這是分析時要確認的前提之一。

---

### Task 3：利用路徑規劃（理論，未實測）

以下步驟是理論預期，基於 CVE-2021-3748 的 bug 特性和 QEMU 的 heap 佈局，對應特定版本的 glibc allocator。實際利用需要大量調試和版本特定分析。

**Step 1：觸發 UAF——讓 bounce buffer 路徑生效**

目標是讓 `address_space_map` 走到 `g_malloc(bounce_buf)` 這條分支。最直接的方法是啟用 vIOMMU：

```
QEMU 啟動參數加：
  -device intel-iommu,intremap=on,eim=on
  -machine q35

guest kernel cmdline 加：
  intel_iommu=on
```

啟用後，guest 的 virtio DMA 需要經過 IOMMU 轉譯。QEMU 的 `address_space_map` 在遇到 IOMMU 轉譯後的 GPA 時，`is_direct_romd` 回傳 false，走 bounce buffer 路徑。

**Step 2：量測 UAF 視窗大小**

從 `g_free(bounce_buf)` 到 `stw_p(elem->in_sg[0].iov_base + offset, num_buffers)` 之間，執行了幾條 host 端指令？視窗越大，heap spray 成功率越高；視窗越小，需要精確的時序控制（甚至可能需要利用多核心 race condition）。

用 GDB 在 QEMU 9.0（確認邏輯後比對到 6.1.x）設斷點計數：

```
(gdb) b address_space_unmap
(gdb) commands
  record
  continue
end
# 在 stw_p 的對應位置設第二個斷點，計算兩個斷點之間的指令數
```

也可以用 QEMU 的 `-d exec` 加 trace 模式輸出指令計數，或者在 valgrind/DynamoRIO 下執行來計算路徑長度。UAF 視窗分析是 race condition 類漏洞利用的標準前置工作。

**Step 3：Heap spray——選目標物件**

`g_free()` 釋放的 bounce buffer 大小等於 `elem->in_sg` 中所有 iov 的總長度，這個大小由 guest 構造的 descriptor chain 控制。我們可以在 guest 側精確控制 bounce buffer 的大小。

在 UAF 視窗內，需要讓 allocator 把同樣大小的記憶體分配給我們控制的物件：

- **候選 1**：另一個 VirtQueueElement（透過第二個 virtqueue 的 `virtqueue_pop` 分配）。VirtQueueElement 本身在 `hw/virtio/virtio.c` 的 `virtqueue_pop` 裡用 `g_malloc0` 分配，大小固定為 `sizeof(VirtQueueElement) + sizeof(struct iovec) * max`。
- **候選 2**：網路緩衝（`g_malloc` 分配的 `uint8_t *buf`），在另一個 virtio-net 的接收路徑觸發。
- **候選 3**：其他 QEMU 裝置的 DMA buffer（如 virtio-blk 的 request buffer）。

評估標準：大小匹配 bounce buffer；分配時序可控（能在 UAF 視窗內觸發）；物件內有我們關心的欄位（函式指標、指標陣列等）。

glibc tcache 的 bin 大小以 16 bytes 為單位對齊，找到 bounce buffer 大小對應的 tcache bin，確認佔用物件的分配路徑也走同一個 bin。

確認 bounce buffer 大小的方式：在 GDB 中對 `g_malloc` 設斷點，在觸發 bounce buffer 路徑時觀察 `malloc` 的大小參數：`(gdb) b __libc_malloc` 然後在 hit 時印 `$rdi`（x86-64 第一個參數，即 `size`）。

**Step 4：型別混淆後的目標欄位**

成功佔用 freed bounce buffer 後，`stw_p` 把 `num_buffers`（16-bit 值）寫入 `bounce_buf_base + offset`。這個 offset 和 `num_buffers` 的值我們從 guest 端可以控制（透過調整封包大小影響 offset，透過不同封包數量影響 `num_buffers`）。

若佔用物件是 VirtQueueElement：對照 `struct VirtQueueElement` 的欄位佈局，找 16-bit 寫入的 offset 對應到哪個成員。若是 `in_num`（in-direction iov 數量），可以把這個值擴大到一個很大的數，後續讀取 `in_sg` 陣列時就能越界讀出堆積上的指標，達到 infoleak。

若佔用物件有函式指標（例如 VirtQueue 的 `handle_output` 或 VirtIODevice 的 `realize`），用受控的 16-bit 覆寫低位元，讓指標指向我們佈置的 ROP gadget 起點。

**Step 5：接前課利用鏈**

- **Ch 17 infoleak**：透過越界讀（從擴大後的 `in_num` 讀取超出陣列的記憶體）洩漏 QEMU binary 的 `.text` 段或 heap 指標，破解 ASLR（位址空間佈局隨機化）和 PIE（位置無關執行檔）。
- **Ch 21 函式指標劫持**：覆寫 VirtQueue 的 `handle_output` 或 VirtIODevice 的 `handle_legacy_features`。下一次 guest 的 virtqueue kick 時，QEMU 就會 call 到我們指定的位址。
- **Ch 22 ROP 鏈**：在不可執行堆積（NX）的環境下，從 QEMU binary 串接 ROP gadget。目標是呼叫 `system("/bin/sh")` 或直接 `execve`，在 host 上拿到 shell，完成 VM escape。

**利用鏈整體難度評估**（未實測，理論預期）：

| 步驟 | 難度 | 主要障礙 |
|------|------|----------|
| 觸發 bounce buffer 路徑 | 中 | 需要 vIOMMU 環境設置 |
| UAF 視窗量測 | 低 | GDB 即可 |
| Heap spray（讓正確物件佔用） | 高 | 大小匹配 + 時序控制 |
| 型別混淆目標欄位分析 | 中 | 需要對 QEMU 物件佈局熟悉 |
| 完整利用鏈（infoleak→控制流） | 高 | 多步驟，各步驟有版本相依性 |

這個評估說明了為什麼 CVE-2021-3748 在公開幾個月後仍沒有廣泛流傳的 PoC：觸發條件特殊（需要 vIOMMU），heap spray 難度不低，且影響的 QEMU 版本已普遍被更新。理解這個難度分佈，比「有沒有 exploit」更重要。

**PoC 觸發骨架（guest 端 C 程式骨架，未實測，理論預期）**

以下骨架涵蓋從開啟介面到觸發接收路徑的概念步驟，不含完整的 vIOMMU 操作和 heap spray 實作：

```c
/* 未實測，理論預期 */
/* 需要 QEMU ≤ 6.1.x，且 guest 端有 vIOMMU 支援 */
/* 編譯：gcc -O0 -o trigger trigger.c */

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdint.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <sys/socket.h>
#include <net/if.h>
#include <sys/ioctl.h>
#include <linux/if_tun.h>
#include <arpa/inet.h>

/*
 * 觸發概念：
 *
 * 前提：
 *   - QEMU 啟動參數：-device intel-iommu,intremap=on,eim=on -machine q35
 *   - guest kernel cmdline：intel_iommu=on
 *   - QEMU 版本 ≤ 6.1.x
 *
 * 步驟：
 * 1. 確認 virtio-net 介面已 up（eth0 或 ens3 等）
 * 2. guest 的 virtio-net 驅動把 RX buffer 的 GPA 映射在 vIOMMU 轉譯的區域
 *    → QEMU 的 address_space_map 判斷 is_direct_romd 為 false
 *    → 走 bounce buffer 路徑，g_malloc(bounce_buf)
 * 3. 從 host 端 ping guest（或 host 端的 tap 發 raw packet）觸發 virtio_net_receive_rcu
 * 4. QEMU 執行：
 *    a. virtqueue_pop → bounce_buf 分配
 *    b. 複製封包到 bounce_buf
 *    c. virtqueue_push → 通知 guest
 *    d. address_space_unmap → g_free(bounce_buf)  ← bounce_buf 釋放
 *    e. [BUG] stw_p(elem->in_sg[0].iov_base + offset, num_buffers)
 *             ↑ elem->in_sg[0].iov_base 仍指向已 g_free 的 bounce_buf
 * 5. UAF 視窗內，guest 觸發另一個分配（例如另一個 virtio 操作）
 *    讓 glibc allocator 把 freed bounce_buf 分配給新物件（heap spray）
 * 6. 步驟 4e 的寫入落在佔用物件的受控欄位 → 型別混淆 → 利用鏈
 */

/* 確認 virtio-net 介面存在 */
static int check_if_up(const char *ifname) {
    int sockfd = socket(AF_INET, SOCK_DGRAM, 0);
    if (sockfd < 0) return -1;
    struct ifreq ifr;
    memset(&ifr, 0, sizeof(ifr));
    strncpy(ifr.ifr_name, ifname, IFNAMSIZ - 1);
    int ret = ioctl(sockfd, SIOCGIFFLAGS, &ifr);
    close(sockfd);
    if (ret < 0) return -1;
    return (ifr.ifr_flags & IFF_UP) ? 1 : 0;
}

int main(void) {
    printf("[*] CVE-2021-3748 觸發骨架（分析用，非完整 exploit）\n");
    printf("[*] 需要 QEMU ≤ 6.1.x + vIOMMU 環境\n\n");

    /* Step 1：確認 virtio-net 介面 */
    const char *ifname = "eth0";
    int up = check_if_up(ifname);
    if (up < 0) {
        printf("[-] 介面 %s 不存在（errno=%d）\n", ifname, errno);
        printf("    嘗試 ens3、ens4 或 ip link show 確認介面名稱\n");
        return 1;
    }
    if (!up) {
        printf("[-] 介面 %s 存在但未 up\n", ifname);
        printf("    執行：ip link set %s up && dhclient %s\n", ifname, ifname);
        return 1;
    }
    printf("[+] 介面 %s 已 up\n", ifname);

    /*
     * Step 2：確認 vIOMMU 是否啟用
     * （需要 QEMU 啟動加 -device intel-iommu，且 guest kernel 有 intel_iommu=on）
     */
    printf("[*] 確認 /sys/kernel/iommu_groups/ 是否存在 IOMMU group...\n");
    if (access("/sys/kernel/iommu_groups", F_OK) != 0) {
        printf("[-] 未找到 IOMMU groups，vIOMMU 可能未啟用\n");
        printf("    QEMU 需要加 -device intel-iommu,intremap=on,eim=on -machine q35\n");
        printf("    guest cmdline 需要加 intel_iommu=on\n");
        return 1;
    }
    printf("[+] IOMMU groups 存在，vIOMMU 可能已啟用\n\n");

    /*
     * Step 3：讓 guest 的 virtio-net RX buffer 走 vIOMMU 路徑
     *
     * 具體實作需要：
     * a) 使用 vfio-pci 或修改 virtio-net driver 的 dma_map 呼叫，
     *    讓 DMA buffer 分配在 IOMMU 映射的區域
     * b) 或者使用自訂 kernel module 直接操作 virtio_ring
     *
     * 這部分超出當前分析範圍，需要深入的 virtio driver 改動
     */
    printf("[*] Step 3：配置 vIOMMU-backed virtio buffer（需自訂 driver，超出骨架範圍）\n");

    /*
     * Step 4：從 host 端觸發封包接收
     * 在 host 端執行：ping <guest-ip> 或 hping3 <guest-ip>
     * 這會觸發 QEMU 側的 virtio_net_receive_rcu
     */
    printf("[*] Step 4：請從 host 端 ping guest IP 觸發 virtio_net_receive_rcu\n");
    printf("    host$ ping <guest-ip>\n\n");

    /*
     * Step 5：UAF 視窗內的 heap spray
     * 在 address_space_unmap 釋放 bounce_buf 後、stw_p 寫入前，
     * 觸發另一個 g_malloc 佔用 freed bounce_buf
     *
     * 實作方式（理論）：
     * - 在另一個 CPU 核心上持續做 virtio-blk read 請求
     * - 或者用 /dev/urandom 讀取觸發 virtio-rng 分配
     * - 大小要匹配 bounce_buf（我們控制 desc 長度來控制 bounce_buf 大小）
     */
    printf("[*] Step 5：UAF 視窗內 heap spray（需精確時序，超出骨架範圍）\n");
    printf("    理論：在另一個 thread 持續觸發 g_malloc(same_size)\n\n");

    printf("[*] 骨架結束\n");
    printf("[*] 完整 exploit 需要：\n");
    printf("    1. 對 QEMU ≤ 6.1.x 的 heap 佈局深入分析\n");
    printf("    2. 精確的 bounce_buf 大小控制（控制 desc chain 長度）\n");
    printf("    3. heap spray 的時序控制（可能需要多線程或 USERFAULTFD）\n");
    printf("    4. 找到合適的型別混淆目標物件和可利用欄位\n");
    return 0;
}
```

---

## 如果卡住了

**提示 1**：讀 `virtio_net_receive_rcu` 時，先用下列命令找所有相關呼叫點，確認 `num_buffers` 的賦值相對於 `virtqueue_push` 和 `address_space_unmap` 的位置，patch 前在哪一行，patch 後在哪一行：

```bash
grep -n "virtqueue_push\|address_space_unmap\|num_buffers\|stw_p" hw/net/virtio-net.c
```

對照 patch 的 `+`/`-` 行，確認移動的具體行號。

**提示 2**：`address_space_map` 什麼時候回傳 bounce buffer？讀 `softmmu/memory.c`（舊版 QEMU）或 `system/physmem.c`（QEMU 8.x+），搜尋 `is_direct_romd` 或 `memory_access_is_direct`——這個函式判斷該 MemoryRegion 是否有直接 host 記憶體映射。若回傳 false，`address_space_map` 就要額外 `g_malloc` bounce buffer，並在 `address_space_unmap` 時 `g_free` 它。重點看判斷邏輯的 `else` 分支。

**提示 3**：IOMMU 的 guest 開啟方式——在 QEMU 啟動參數加 `-device intel-iommu,intremap=on,eim=on -machine q35`，並在 guest kernel cmdline 加 `intel_iommu=on`。啟用後，可以用 `dmesg | grep -i iommu` 在 guest 裡確認 IOMMU 初始化訊息。確認後，guest 的 virtio DMA 請求會經過 IOMMU 轉譯，QEMU 側的 `address_space_map` 就會走 bounce buffer 路徑。

---

## 實作步驟

1. **取得 patch**：

   ```bash
   git clone https://gitlab.com/qemu-project/qemu.git
   cd qemu
   # 只看 virtio-net.c 的改動
   git show bedd7e93d01961fcb16a97ae45d93acf357e11f6 -- hw/net/virtio-net.c
   ```

   先讀 commit message 的摘要，再看 diff。commit message 應該含 bug 描述，是分析的起點。

2. **對照讀**：切到 QEMU 9.0 tag（`git checkout v9.0.0`），在 `hw/net/virtio-net.c` 找 `virtio_net_receive_rcu`，確認修正後的邏輯——`num_buffers` 寫入是否已在 `address_space_unmap` 之前完成。同時觀察這個函式在 QEMU 9.0 的結構有沒有大幅重構。

3. **追 caller**：

   ```bash
   grep -rn "virtio_net_receive_rcu\|virtio_net_receive" hw/net/virtio-net.c
   ```

   找所有進入 `virtio_net_receive_rcu` 的路徑，確認哪條路徑會走到 bounce buffer 分支。patch 只改了一條路徑嗎？

4. **追 VirtQueueElement 生命週期**：在開 `virtio-net` 的 QEMU 9.0（確認邏輯後比對 6.1.x）用 GDB attach：

   ```bash
   # 啟動 QEMU（需有 virtio-net）
   qemu-system-x86_64 -s -S \
     -netdev user,id=net0 -device virtio-net-pci,netdev=net0 \
     -hda guest.img -m 512 &

   # 另一個終端
   gdb -p $(pgrep qemu-system-x86_64)
   (gdb) b virtio_net_receive_rcu
   (gdb) c
   ```

   觸發後（host 端 ping guest），在斷點觀察 `elem` 結構：`elem->in_sg[0].iov_base` 在 `virtqueue_push` 呼叫前後的值，確認 `address_space_unmap` 之後指標是否仍指向相同位址（= dangling pointer）。

5. **設計觸發 PoC（guest 端 C）**：

   a. 確認 QEMU 啟動有 vIOMMU 支援（見提示 3 的啟動參數）。  
   b. 在 guest 確認 IOMMU 已啟用：`dmesg | grep -i iommu`，應有 `Intel-IOMMU: enabled` 類的訊息。  
   c. 從 host 端 ping guest IP（`ping <guest-ip>`），觸發 `virtio_net_receive_rcu`。  
   d. 在 UAF 視窗內，同時對另一個 virtqueue（如 virtio-rng 或 virtio-blk）送請求做 heap spray，讓 QEMU allocator 把 freed bounce buffer 分配給新物件。  
   e. 用 QEMU 側的 GDB 確認 `stw_p` 呼叫時 `elem->in_sg[0].iov_base` 是否已是被佔用的物件位址。

6. **分析 CVE-2024-3446**：讀 RedHat Bugzilla #2274211 與對應 QEMU commit。在 QEMU 原始碼搜尋：

   ```bash
   grep -rn "mem_reentrancy_guard\|reentrancy_guard" hw/virtio/ hw/core/
   ```

   理解 `mem_reentrancy_guard` 的設計——它是 per-device 的 flag，防止單一 device 的 DMA callback 遞迴回到自己的 MMIO handler。但多個不同 virtio device 並行時，各自的 guard 互相獨立，跨 device 的 reentrancy 沒有被攔截。對照 Ch 26 Bug 1，說明 DMA reentrancy double free 的具體觸發路徑。

---

## 常見卡點與除錯

**問題 1：`git clone` 太慢或失敗**

QEMU repo 很大（約 400 MB）。可以用 shallow clone 加速，只取最近的 commits：

```bash
git clone --depth=5000 https://gitlab.com/qemu-project/qemu.git
# 或直接在 GitLab web UI 讀 diff，不需要 full clone
```

也可以用 GitHub 鏡像（部分時期同步，不保證最新）：
`https://github.com/qemu/qemu`

**問題 2：找不到 `virtio_net_receive_rcu`**

不同版本的 QEMU 函式名稱可能略有不同。若在 QEMU 9.0 找不到，用：

```bash
grep -n "receive_rcu\|receive_rcu\|mrg_rxbuf" hw/net/virtio-net.c | head -30
```

**問題 3：`address_space_map` 在不同版本路徑不同**

QEMU 在 7.x 左右做過目錄結構重組，`softmmu/` 被移到 `system/`。找 bounce buffer 邏輯時：

```bash
# 舊版（QEMU ≤ 6.x）
find softmmu/ -name "memory.c" -o -name "physmem.c"

# 新版（QEMU 7.x+）
find system/ -name "physmem.c"
```

搜尋 `address_space_map` 的實作：

```bash
grep -rn "^void \*address_space_map\|^static void \*dma_memory_map" \
     softmmu/ system/ 2>/dev/null | head -10
```

**問題 4：vIOMMU 啟用後 guest 無法 boot**

Q35 machine type 需要 OVMF（UEFI）而非 SeaBIOS。若原本用 SeaBIOS，嘗試：

```bash
qemu-system-x86_64 \
  -machine q35,accel=kvm \
  -device intel-iommu,intremap=on,eim=on \
  -bios /usr/share/OVMF/OVMF_CODE.fd \
  ...
```

或者直接用 `-machine pc`（i440FX）搭配 `-device intel-iommu`（部分 QEMU 版本支援，但效果不如 q35 穩定）。

---

<details>
<summary>參考分析與觸發碼骨架（先自己想再展開）</summary>

### Root Cause 分析

**CVE-2021-3748** 的 root cause 在 `hw/net/virtio-net.c` 的 `virtio_net_receive_rcu`。

問題出在 `num_buffers` 的寫入時機：

**Patch 前（有 bug 的版本，QEMU ≤ 6.1.x）**：

```c
/* patch 前的執行順序（錯誤）*/
virtqueue_push(q->rx_vq, elem, total);
/* ↑ 把 elem 標記完成，通知 guest */

address_space_unmap(&address_space_memory,
                    elem->in_sg[0].iov_base - recv_header_size,
                    total + recv_header_size, true,
                    total + recv_header_size);
/* ↑ 釋放 bounce buffer：g_free(bounce_buf) */
/* 從這行之後，elem->in_sg[0].iov_base 是 dangling pointer */

/* ... 若 n_rx_virtqs > 1，進入 multi-queue 路徑 ... */

/* BUG：在 unmap 之後才寫入 num_buffers */
stw_p(elem->in_sg[0].iov_base + offsetof(struct virtio_net_hdr_mrg_rxbuf,
                                          num_buffers),
      num_buffers);
/* ↑ elem->in_sg[0].iov_base 仍是舊的 bounce_buf 位址，但 bounce_buf 已被 g_free */
/* ↑ 這是 use-after-free：對 freed memory 做寫入 */
```

**Patch 後（修正版本，QEMU 6.2.x+）**：

```c
/* patch 後的執行順序（正確）*/

/* 先寫 num_buffers（bounce buffer 仍有效）*/
stw_p(elem->in_sg[0].iov_base + offsetof(struct virtio_net_hdr_mrg_rxbuf,
                                          num_buffers),
      num_buffers);

virtqueue_push(q->rx_vq, elem, total);
/* 再 push */

address_space_unmap(&address_space_memory, ...);
/* 最後 unmap，釋放 bounce buffer */
/* 此時 stw_p 已完成，dangling pointer 不再被使用 */
```

修正邏輯很簡單：把 `num_buffers` 的寫入移到 `address_space_unmap` 之前，確保寫入時 bounce buffer 仍然有效。

**為什麼只有 bounce buffer 路徑受影響？**

當 GPA 有直接 RAM 映射時，`address_space_map` 回傳的是 host RAM 的指標（`qemu_map_ram_ptr` 之類的函式），不涉及 `g_malloc`/`g_free`。`address_space_unmap` 在這條路徑下是 no-op 或只做 cache sync，不釋放記憶體。因此 `stw_p` 呼叫時指標仍然有效，沒有 UAF。只有在走 bounce buffer 路徑時，`g_free` 才會真的釋放記憶體。

**`stw_p` 的寫入原語（write primitive）評估**：

`stw_p`（store word，platform byte order）往 `bounce_buf_base + offsetof(struct virtio_net_hdr_mrg_rxbuf, num_buffers)` 寫入 16-bit 的 `num_buffers` 值。這給我們以下原語：

- **寫入大小**：16-bit（2 bytes）
- **寫入偏移**：由 `offsetof(struct virtio_net_hdr_mrg_rxbuf, num_buffers)` 決定，固定偏移，不直接受 guest 控制
- **寫入值**：`num_buffers`，等於「這個封包用了幾個 RX descriptor」，guest 能透過構造封包大小和 descriptor 大小來影響這個值
- **寫入目標位址**：freed bounce buffer 的起點 + 固定偏移，間接受 guest 控制（透過控制 descriptor 的 GPA）

這個原語的限制比完全任意寫（arbitrary write）小很多，但對於 heap 型別混淆而言已經足夠——只要找到一個在該偏移有重要欄位的佔用物件。

**觸發條件小結**：

- QEMU ≤ 6.1.x
- 使用 `virtio-net` 且啟用 merged rx buffers（`mrg_rxbuf` 協商開啟，這是現代 virtio-net 的預設）
- guest 的 virtio-net RX buffer GPA 落在 non-direct access region（需要 vIOMMU 或其他 non-RAM MemoryRegion）
- 觸發方向：host→guest（host 端發封包到 guest），而非 guest→host

### 觸發骨架說明

下方骨架已包含在 Task 3 的 PoC 觸發碼中。這裡補充說明幾個關鍵的實作決策：

**bounce buffer 大小的控制**：

bounce buffer 大小由 `address_space_map` 的 `plen` 參數決定，這個值最終來自 descriptor chain 描述的 buffer 大小。在 guest 端，我們可以透過控制 virtio-net RX buffer 的大小（設定 `VIRTIO_NET_F_GUEST_CSUM`、調整 RX buffer size）來精確控制 bounce buffer 的大小，進而控制它落在 glibc tcache 的哪個 bin。

**heap spray 的 race window**：

UAF 視窗（`g_free` 到 `stw_p`）在程式碼層面看起來很短，但涉及的函式呼叫鏈（`virtqueue_push` → `address_space_unmap`）加上可能的 lock 競爭，實際 CPU 指令數可能比表面看起來多。可以用 USERFAULTFD（使用者層面缺頁錯誤處理機制）在 guest 端製造 page fault，讓 QEMU 在 UAF 視窗內 block，給 heap spray 更多時間。

**這個骨架只展示概念框架**，不是完整 exploit。精確的 heap groom 和時序控制需要對特定 QEMU 版本的 heap 佈局（glibc 版本、tcache 參數、chunk header 格式）做完整分析，遠超出這個練習的範疇。

```c
/* 未實測，理論預期 */
/* 需要 QEMU ≤ 6.1.x，且 guest 有 vIOMMU 支援 */
#include <stdio.h>
#include <string.h>
#include <sys/types.h>
#include <net/if.h>
#include <sys/ioctl.h>
#include <linux/if_tun.h>
#include <fcntl.h>
#include <unistd.h>

/* 觸發概念：
 * 1. 在 guest 開啟 virtio-net 介面
 * 2. 從 host 端發送封包到 guest（觸發 virtio_net_receive_rcu）
 * 3. guest 的 virtio RX buffer 的 GPA 在 vIOMMU 映射區域
 *    → QEMU 走 bounce buffer 路徑（is_direct_romd 回傳 false）
 * 4. UAF 視窗內，用另一個 virtio 操作分配同大小物件佔用 freed bounce buffer
 */

/* 實際觸發需要：
 * a) QEMU 啟動加 -device intel-iommu,intremap=on,eim=on -machine q35
 * b) guest kernel 加 intel_iommu=on
 * c) guest 驅動把 virtio-net RX buffer 分配在 IOMMU 映射的 GPA 區段
 * d) 精確的時序控制（視窗約數十到數百條 host 側指令）
 */

int main(void) {
    /* 分配在特殊 GPA 的 buffer（需 vIOMMU support）*/
    /* 具體實作需要 vfio-pci 或自訂 virtio driver，超出當前分析範圍 */
    printf("需要 vIOMMU 環境，見延伸挑戰\n");
    return 0;
}
```

</details>

---

## 驗收表

- [ ] 能說出 CVE-2021-3748 的 root cause，不超過 3 句話（不是背 CVE 描述，是自己的理解）
- [ ] 能找到 patch commit `bedd7e93` 並說明它修改的核心邏輯（`num_buffers` 寫入順序）
- [ ] 能畫出 UAF 觸發的時序圖（從 `virtqueue_pop` 到 dangling pointer 寫入，每步說明 `iov_base` 的狀態）
- [ ] 能說明 non-direct access region 路徑如何在 guest 端觸發（需要什麼 QEMU 啟動參數，guest 端確認方式）
- [ ] 能把這個 bug 對應到 Ch 26 的哪種 bug 模式（元素生命週期管理錯誤），並說明它和 Ch 26 Bug 1（DMA reentrancy）的本質差異
- [ ] 能描述 heap spray 的目標物件選擇策略（大小匹配原則、allocator bin、可利用欄位）

---

## 延伸挑戰

**挑戰 1：分析 CVE-2024-3446（DMA reentrancy double free）**

CVE-2024-3446 影響 virtio-gpu、virtio-serial、virtio-crypto。在 QEMU 原始碼搜尋 `mem_reentrancy_guard` 的設置位置：

```bash
grep -rn "mem_reentrancy_guard\|reentrancy_guard" hw/virtio/ hw/core/
```

說明為什麼多個 virtio device 並行時 guard 會失效：guard 是 per-device flag，防止單一 device 的 DMA callback 遞迴回到自己的 MMIO handler，但跨 device 的 reentrancy（Device A 的 DMA callback 觸發 Device B 的 MMIO 操作，Device B 的 MMIO 操作再觸發 Device A 的 DMA）沒有被攔截。對照 Ch 26 Bug 1，說明 double free 的具體觸發路徑，以及 QEMU 9.0 的修復思路是什麼。

**挑戰 2：在 QEMU 9.0 找其他 VirtQueueElement 生命週期問題**

在 QEMU 9.0 的 `hw/net/virtio-net.c` 搜尋所有在 `virtqueue_push` 之後存取 `elem` 成員的位置：

```bash
grep -n "virtqueue_push\|elem->" hw/net/virtio-net.c | less
```

手動對照：每個 `virtqueue_push(... elem ...)` 呼叫之後，是否還有 `elem->xxx` 的存取？若有，那個欄位是否可能是 bounce buffer 的 dangling pointer？還是只是存取了 elem 結構本身（elem 本身在 `virtqueue_push` 之後被 `g_free`，也是另一個潛在問題）？

評估你找到的模式是否真的構成漏洞，還是有其他機制保護。

**挑戰 3：不用 vIOMMU 的替代觸發方式**

研究 QEMU 的 MemoryRegion 類型（`include/exec/memory.h`），找 non-RAM 類型的 MemoryRegion——例如 MMIO region、ROM region、IOMEMTYPE_DEVICE。這些 region 的 `address_space_map` 是否也需要走 bounce buffer 路徑？

```bash
grep -n "IOMEMTYPE\|is_direct_romd\|memory_access_is_direct" \
     include/exec/memory.h softmmu/memory.c
```

若有非 IOMMU 的 non-direct region 類型，能否構造一個 guest 配置，讓 virtio-net 的 RX buffer GPA 落在這類 region，在不啟用 vIOMMU 的情況下觸發 bounce buffer 路徑？這個路徑如果存在，exploit 條件會比 vIOMMU 路徑寬鬆很多。

---

## 自我檢核

- [ ] 我能獨立找到 patch commit 並讀懂 diff，不需要別人解釋 `+`/`-` 代表什麼
- [ ] 我能解釋 `address_space_map` 和 bounce buffer 的關係，不只是背 CVE 描述——我理解為什麼只有 non-direct access region 路徑才有 UAF
- [ ] 我能說明為什麼「`num_buffers` 寫入在 `unmap` 之前」就是正確的，而不是記憶「patch 把它移到前面」——我理解的是 free 和 use 的相對順序
- [ ] 我理解這個練習的分析和完整 exploit 之間的差距在哪裡（vIOMMU 環境建置、heap groom 的大小控制、UAF 視窗的時序控制），且能說出為什麼「觸發條件特殊」本身就是 CVE-2021-3748 沒有廣泛 PoC 的主要原因
- [ ] 我能把 CVE-2021-3748 和 CVE-2024-3446 對應到 Ch 26 的不同 bug 模式，說明兩者的觸發機制有何本質差異——前者是 free/use 順序錯誤，後者是 reentrancy guard 設計不完整

---

Part 4 virtio 深挖到這裡結束——從架構（Ch 25）、bug 模式（Ch 26）、vhost-user（Ch 27）、CVE 詳解（Ch 28）到這個練習的 patch diff 逆向分析，完整走了一遍 virtio 攻擊面。這五個章節加上這個練習，是 QEMU/KVM virtio 漏洞研究的完整入門路線。

Part 5 轉換目標：VirtualBox 是另一套完全不同的架構，有自己的裝置模型、COM 物件系統和漏洞模式。把 QEMU 學到的「裝置模型→DMA→生命週期管理」框架帶進去，有助於快速定位 VirtualBox 的相似模式。

→ [Ch 29](./29-virtualbox-architecture.md)
