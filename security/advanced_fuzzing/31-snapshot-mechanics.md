# Ch 31 — snapshot 機制

> **目標：** 搞清楚「snapshot」在不同層級的實作是什麼——process-level（CRIU）、VM-level（KVM/QEMU）、hypervisor-level（Nyx）——每種機制的 reset 成本、能打的目標、和各自的限制。
>
> **環境：** CRIU 在 WSL2 上**可能可以跑**（部分版本支援，取決於 WSL2 kernel 設定）。真跑 CRIU 快照的步驟和輸出在本章實際執行後貼出；如果執行失敗，標注原因和替代驗證方法。VM-level 和 hypervisor-level 的機制需要 KVM 支援，WSL2 受限，標注「[未實測]」。

---

## 先建立直覺：snapshot 在哪個層面做？

```
層面                  snapshot 的對象                     代表工具
─────────────────────────────────────────────────────────────────────
函數級（in-process）  函數的參數 + 局部狀態                AFL persistent mode
進程級（CRIU）        整個進程的記憶體、fd、暫存器          CRIU, LibAFL fork-based
VM 級（KVM/QEMU）    整個 guest VM（CPU + 記憶體 + 裝置）   QEMU savevm
Hypervisor 級（Nyx） VM + dirty page tracking + Intel PT   kAFL/Nyx
```

層級越高，能打的目標越廣（從 userland function → 整個系統），但 setup 複雜度和 reset overhead 也越高。

---

## Full snapshot vs Incremental snapshot

### Full snapshot

每次 reset 都把所有狀態還原到初始快照：

```
初始快照（T0）
  │
  ├── 輸入 1 執行 → reset（還原到 T0）
  ├── 輸入 2 執行 → reset（還原到 T0）
  └── 輸入 3 執行 → reset（還原到 T0）
```

優點：每個輸入的起點完全相同，確定性最高。
缺點：對 stateful target，每次都從頭走，無法繼承前一個輸入的「有趣狀態」。

### Incremental snapshot

保存多個快照點，可以從任意一個繼續：

```
初始快照（T0）
  │
  ├── 輸入序列走到有趣狀態 → 快照（T1）
  │       │
  │       ├── 從 T1 繼續輸入 A → 有 crash → reset to T1
  │       └── 從 T1 繼續輸入 B → 無 crash → 快照（T2）
  │               │
  │               ├── 從 T2 繼續輸入 C → ...
  │               └── ...
  └── 另一條路 → 快照（T3）...
```

優點：能到達 full snapshot 打不到的深層狀態。
缺點：snapshot 數量增長，管理複雜；確定性稍差（依賴前一步驟的輸入序列）。

Nyx-Net 和某些 LibAFL 的 stateful executor 使用增量快照打協定狀態機的深層路徑。

---

## 機制一：Process-level snapshot（CRIU）

CRIU（Checkpoint/Restore In Userspace）是 Linux 上把進程完整凍結、序列化到磁碟、之後還原的工具。

### CRIU 凍結的狀態

```
進程 P 的完整狀態：
  ├── 虛擬記憶體映射（/proc/P/maps + /proc/P/mem）
  ├── 開啟的 file descriptor（/proc/P/fd + 每個 fd 的狀態）
  ├── 暫存器（ptrace PTRACE_GETREGS）
  ├── signal handler 設定
  ├── namespace 設定（pid/net/mount）
  └── 進程樹（父子關係）
```

CRIU 把這些都序列化到一組檔案（`*.img`），還原時重建完整的進程狀態。

### 在 WSL2 上嘗試 CRIU

```bash
# 安裝 CRIU
sudo apt-get install -y criu

# 確認版本和能力
criu check
# 如果輸出 "Looks good." 代表當前 kernel 支援 CRIU
# WSL2 kernel 對 CRIU 的支援隨版本有差異
```

實際嘗試結果（在 WSL2 Ubuntu 22.04 上執行）：

```bash
$ criu check
Warn  (criu/kerndat.c:791): Mount namespace clone is not supported
Warn  (criu/kerndat.c:861): NS_GET_USERNS isn't supported
Looks good.
```

WSL2 支援基本的 CRIU checkpoint，但有部分功能受限（namespace 操作）。

```bash
# 建立一個簡單的持續執行進程作為 checkpoint 目標
cat > /tmp/counter.sh << 'EOF'
#!/bin/bash
i=0
while true; do
    echo "count: $i"
    sleep 1
    i=$((i+1))
done
EOF
chmod +x /tmp/counter.sh
/tmp/counter.sh &
TARGET_PID=$!
echo "Target PID: $TARGET_PID"
sleep 3  # 讓它跑幾秒

# Checkpoint（凍結並儲存狀態到 /tmp/criu-dump/）
mkdir -p /tmp/criu-dump
sudo criu dump -t $TARGET_PID -D /tmp/criu-dump --shell-job
echo "Checkpoint done. Files:"
ls -la /tmp/criu-dump/

# 查看 CRIU 儲存了什麼
ls /tmp/criu-dump/*.img | head -10
```

**[如果 WSL2 上 `criu dump` 失敗，通常是因為 `--shell-job` 需要的 pty 支援或 `/proc` 某些功能受限。失敗訊息通常明確說明缺哪個 kernel feature。替代方案：在裸金屬 Linux 或標準 Ubuntu VM 裡執行上述步驟。]**

```bash
# 如果 checkpoint 成功，還原進程
sudo criu restore -D /tmp/criu-dump --shell-job &
# 觀察 count 是否從 checkpoint 時的值繼續
```

### CRIU 的 fuzzing 應用

CRIU 可以用來做「進程級 snapshot fuzzing」：

```
1. target 啟動，初始化完畢
2. CRIU checkpoint（儲存整個進程狀態）
3. 注入一個輸入，target 執行
4. 輸入結束後：CRIU restore（從 checkpoint 還原）
5. 回到步驟 3
```

**問題**：CRIU restore 需要幾百毫秒到幾秒（需要讀磁碟、重建記憶體映射），比 fork 或 VM snapshot 慢。在 fuzzing 場景裡通常用 in-memory 的 fork-based snapshot 代替 CRIU，除非 target 有 CRIU 沒有辦法用 fork 模擬的複雜狀態（例如打開了特殊 fd）。

---

## 機制二：VM-level snapshot（QEMU savevm）

QEMU 的 `savevm` / `loadvm` 功能把整個 VM 的狀態存到一個快照：

```bash
# 在 QEMU monitor 裡（Ctrl+Alt+2 進入 monitor）
(qemu) savevm my_snapshot
# 儲存 VM 狀態（CPU、記憶體、所有虛擬裝置）

(qemu) loadvm my_snapshot
# 還原到快照狀態（會暫停 VM 幾秒）
```

**[以下 QEMU savevm 步驟在 WSL2 上未實測完整流程，為理論預期行為。有 KVM 的環境可依步驟驗證。]**

### VM snapshot 的成本來源

```
savevm：
  儲存 vCPU 暫存器（快）
  儲存整個 guest 記憶體（慢！4GB RAM → 4GB 資料）
  儲存裝置狀態（取決於裝置數量）

loadvm：
  還原 vCPU 暫存器（快）
  還原 guest 記憶體（慢！或用 CoW 優化）
  還原裝置狀態（快）
```

純 `loadvm` 沒有 dirty page tracking 的加速，每次 reset 都要讀寫大量資料。這就是為什麼 Nyx 要在 hypervisor 層加 dirty page tracking——只還原被改過的頁。

### QEMU savevm 的使用場景

對於**不需要極高 exec/s** 的目標（比如你想「打到某個 state 然後大量 fuzz」，但每個輸入本身執行很慢），QEMU savevm 就夠了：

```bash
# 啟動 QEMU，boot target
qemu-system-x86_64 -m 2G -hda target.qcow2 -enable-kvm \
    -monitor telnet:127.0.0.1:4444,server,nowait

# 透過 telnet 操作 QEMU monitor
telnet 127.0.0.1 4444
(qemu) savevm state1
(qemu) loadvm state1  # 幾秒內完成
```

---

## 機制三：Nyx 的 Hypervisor-level snapshot（dirty page 加速）

**[本節為理論預期行為，基於 Nyx 論文和 KVM API 文件。需要裸金屬 Linux + KVM 才能實測。]**

Nyx 在 KVM 之上加入了精確的 dirty page tracking，讓 reset 只還原被 guest 寫過的頁面：

### KVM dirty page tracking API

```c
// [未實測，為 KVM API 概念範例]

// 啟動 dirty page logging
struct kvm_dirty_log dirty_log = {
    .slot = 0,
    .dirty_bitmap = malloc(bitmap_size),
};

// 設置 memslot 為 log_dirty_pages 模式
// （讓 EPT 把 guest 寫入標記為 dirty）
struct kvm_userspace_memory_region region = {
    .slot = 0,
    .flags = KVM_MEM_LOG_DIRTY_PAGES,  // 關鍵：啟用 dirty tracking
    .guest_phys_addr = 0,
    .memory_size = guest_mem_size,
    .userspace_addr = (uint64_t)guest_mem_ptr,
};
ioctl(kvm_fd, KVM_SET_USER_MEMORY_REGION, &region);

// 在每次輸入結束後，讀 dirty bitmap
ioctl(kvm_fd, KVM_GET_DIRTY_LOG, &dirty_log);

// 只把 dirty 的頁從快照還原
for (int i = 0; i < num_pages; i++) {
    if (bitmap_isset(dirty_log.dirty_bitmap, i)) {
        memcpy(guest_mem + i * PAGE_SIZE,
               snapshot_mem + i * PAGE_SIZE,
               PAGE_SIZE);
    }
}

// 清除 dirty log，準備下一輪
ioctl(kvm_fd, KVM_CLEAR_DIRTY_LOG, &dirty_log);
```

### EPT 的角色

```
Guest 寫入某個 GPA（Guest Physical Address）：
         │
         ▼
  EPT（Extended Page Tables）
         │  如果 EPT 項目的 "write" bit 是 0（被 KVM 清除以追蹤 dirty）
         │  → EPT violation（VM exit）
         ▼
  KVM 處理 VM exit：
    1. 把這個 GPA 標記在 dirty bitmap
    2. 設定 EPT 項目的 write bit = 1（允許後續寫入）
    3. 繼續執行 guest
```

這個機制的代價：每個新 page 的第一次寫入會觸發一次 VM exit（EPT violation）。對「只寫少量頁面的輸入」，這是可接受的。

---

## CoW Page 與 snapshot reset 的關係

Copy-on-Write 和 snapshot reset 在概念上相似但方向相反：

```
CoW（fork 的機制）：
  fork() 時不複製頁面
  child 第一次寫某頁 → page fault → 複製那頁
  → 「寫的時候才複製」

Dirty page reset（Nyx 的機制）：
  snapshot 時記錄所有頁面的內容
  輸入結束後，查哪些頁被寫過（dirty bitmap）
  只把那些頁還原
  → 「還原被寫過的頁」
```

兩者都只處理「實際被修改的頁」，但 CoW 是懶複製、Nyx reset 是選擇性還原。

---

## 裝置狀態的 snapshot：最複雜的部分

記憶體和 CPU 暫存器容易 snapshot，麻煩的是裝置狀態：

```
需要 snapshot 的裝置狀態：
  ├── 虛擬網卡（e.g., e1000）的暫存器和 DMA buffer
  ├── 虛擬磁碟（virtio-blk）的 queue 狀態
  ├── QEMU 的 PCI 配置空間
  └── timer / interrupt controller 狀態
```

QEMU 的 `savevm` 透過每個裝置的 `vmstate` 結構序列化裝置狀態。Nyx 的方案是在「target 開始接受輸入之前」設定快照——這個時間點裝置狀態通常是穩定的（初始化完成），reset 後裝置回到同樣的穩定狀態。

如果 target 本身在執行過程中修改裝置狀態（比如發 DMA request、改中斷 mask），就需要把裝置狀態也納入 dirty tracking。Nyx 論文的 Section 5.3 討論了這個問題。**[未實測驗證 Nyx 的裝置狀態 reset 細節。]**

---

## 踩雷

**錯誤直覺一：「CRIU 和 VM snapshot 是同樣的東西，只是顆粒度不同」**

正確理解：CRIU 是用戶空間工具，透過 `ptrace` 和 `/proc` 序列化進程狀態——它知道 fd、signal、記憶體映射，但**不知道** kernel-side 的狀態（scheduler queue、RCU state、open file 的 kernel struct）。VM snapshot 是 hypervisor 在 CPU 層面凍結整個 VM，包含 kernel state 在內。如果你的 target 需要 kernel 狀態一起 reset（比如 kernel fuzzing），CRIU 沒用，只能用 VM-level snapshot。

**錯誤直覺二：「Dirty page tracking 對所有 fuzzing 目標都有效」**

正確理解：Dirty page tracking 的假設是「每個輸入只寫動少量頁面」。如果你的 target 每個輸入都寫遍整個記憶體（比如 memset 整個 heap），dirty page log 就是「全部都 dirty」，reset 成本等同於還原整個記憶體——速度退化到 QEMU savevm 的水準。這種情況要考慮「壓縮快照」或選擇更靠前的 snapshot 點。

**錯誤直覺三：「Full snapshot 和 incremental snapshot 的選擇只是效能問題」**

正確理解：Full snapshot 和 incremental snapshot 有根本的覆蓋率差異。Full snapshot 讓 fuzzer 只能探索從同一個起點出發的路徑；incremental snapshot 讓 fuzzer 能累積「有意義的執行歷史」再繼續 fuzz，能打到 full snapshot 進不去的深層狀態。對 stateful target，这不只是效能問題，而是能不能打到那層狀態的問題。

---

## 進階延伸

**LibAFL 的 snapshot executor**：LibAFL 提供了 `SnapshotExecutor`（用 in-process CoW 實作），和 `QemuExecutor`（包含 QEMU 整合，可搭配 snapshot）。Ch 4–10 學的 LibAFL 元件可以直接接上這些 executor。

**Incremental snapshot 的管理問題**：當 fuzzer 建立大量 incremental snapshot，snapshot 樹會很快佔滿記憶體。實際系統（如 Nyx-Net）會限制 snapshot 深度，或定期修剪不活躍的 snapshot 分支。

**非揮發性記憶體（NVM）上的 snapshot**：Intel Optane DCPMM 等 NVM 設備能以接近記憶體的速度讀寫，但像磁碟一樣持久化。把 snapshot 存在 NVM 上可以實現「跨重啟的 fuzzing 狀態恢復」——crash 後不需要重新跑到 snapshot 點。

---

## 動手練習

1. 在 WSL2 上執行 `criu check`，記錄輸出，確認哪些功能支援、哪些不支援。

2. 嘗試對一個簡單的 shell 進程做 CRIU checkpoint（見本章的指令），觀察 CRIU 儲存了哪些 `.img` 檔案，用 `criu-stat` 或 `ls -la` 看各個 img 的大小。

3. 閱讀 KVM API 文件（`Documentation/virt/kvm/api.rst` 或 https://docs.kernel.org/virt/kvm/api.html），找到 `KVM_GET_DIRTY_LOG` 和 `KVM_MEM_LOG_DIRTY_PAGES` 的描述，理解 dirty page tracking 的 API 界面。

4. 閱讀 Nyx 論文 Section 5（Implementation），找出他們如何處理「裝置狀態的 snapshot 和 reset」。

---

## 本章重點

- Snapshot 有四個層面：in-process（AFL persistent）、進程級（CRIU）、VM 級（QEMU savevm）、hypervisor 級（Nyx dirty-page-tracking）
- Nyx 的速度優勢來自 dirty page tracking：只還原被寫過的頁，而不是整個 VM 狀態
- CRIU 可做進程級 snapshot，WSL2 上部分支援，但不能 reset kernel-side 狀態
- 裝置狀態是 snapshot 最複雜的部分，通常靠「在穩定狀態存快照」迴避這個問題
- Incremental snapshot 能打到 full snapshot 打不到的深層 stateful 目標

---

## 自我檢核

- [ ] 四個 snapshot 層面各自能打什麼目標，打不了什麼？
- [ ] KVM dirty page tracking 怎麼工作？EPT 在這裡扮演什麼角色？
- [ ] CoW 和 dirty page reset 有什麼本質差異？
- [ ] 為什麼 CRIU 不能用來做 kernel fuzzing 的 snapshot？
- [ ] Full snapshot vs incremental snapshot 的 trade-off 在哪裡？

---

## 延伸閱讀

1. **Nyx: Greybox Hypervisor Fuzzing using Fast Snapshots and Affine Types**（Schumilo et al., USENIX Security 2021）
   - 讀 Section 5.1（Memory Snapshotting）和 Section 5.3（Device State）——最完整的 hypervisor-level snapshot 實作說明，dirty page tracking 和裝置狀態處理都在這裡
   - https://www.usenix.org/conference/usenixsecurity21/presentation/schumilo

2. **CRIU 官方文件：How CRIU works**
   - 讀「Checkpoint」和「Restore」兩節——詳細說明 CRIU 如何透過 ptrace + /proc 序列化進程狀態，理解進程級 snapshot 的實作細節和限制
   - https://criu.org/How_CRIU_works

3. **KVM API documentation（`Documentation/virt/kvm/api.rst`）**
   - 讀 `KVM_SET_USER_MEMORY_REGION`（flags 部分）和 `KVM_GET_DIRTY_LOG`——KVM dirty page tracking 的 authoritative API 文件，理解 Nyx 快速 reset 的底層機制
   - https://docs.kernel.org/virt/kvm/api.html

---

→ [Ch 32 全系統 target](./32-whole-system-targets.md)
