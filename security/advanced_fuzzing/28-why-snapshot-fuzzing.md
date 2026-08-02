# Ch 28 — 為什麼需要 snapshot fuzzing

> **目標：** 理解 fork server 的根本限制，以及 snapshot fuzzing 如何打破這些限制——從第一原理說清楚，不是背工具名稱。

## 先從 fork server 的邏輯說起

afl++ 的 fork server 模型有個很單純的假設：

```
target 啟動 → 跑到「初始化完畢」的點
         │
         ▼
   fork server loop
         │
         ├──── fork() ────► 子進程（注入輸入、執行、exit/crash）
         │                          │
         └──────────────────────────┘ 繼續下一輪
```

核心假設是：**parent 的記憶體狀態就是每次輸入的乾淨起點**。fork 的 copy-on-write 保證每個子進程從完全相同的狀態出發。

這個假設在四種情境全部破裂。

---

## 四種 fork server 打不到的情境

### 情境一：副作用無法隔離

target 在處理輸入的過程中製造副作用，而這些副作用 fork() 隔離不了：

- 寫入 `/tmp/state.db`——下一個子進程讀到的是被前一輸入污染的檔案
- 呼叫 `ioctl` 改變 kernel 全域狀態——kernel-side 的 fd table、socket 狀態、inode 不屬於 fork 能複製的記憶體
- 修改共享記憶體（SHM）
- 佔用 TCP port

結果：你以為在測試「輸入 B」，實際上在測試「輸入 A 的殘留 + 輸入 B」。Coverage 被污染，oracle 失效。

### 情境二：kernel 和 hypervisor 本身

fork server 是用戶空間機制。當 fuzz 目標本身是 kernel：

```
你想 fuzz 的 ← kernel
你的 fuzzer ← 只能在 kernel 上面跑
```

你沒辦法在 kernel 裡呼叫 fork()，也沒辦法從 kernel 上方注入 fork server。syzkaller 的解法是在 VM 裡跑，crash 就重啟 VM——但 VM 重啟要幾秒，exec/s 掉到個位數。

### 情境三：closed-source、無插樁點

fork server 需要把 shim 注入 target。有原始碼或 ELF 可以改 `__start` 前的初始化——但：

- Commercial firmware blob（無 OS、無 ELF loader）
- BootROM、UEFI runtime
- Hypervisor 本身
- Kernel module（你不想跑整個 kernel 重啟）

這些目標沒有讓你注入 fork server 的位置。

### 情境四：「跑到某個中間狀態再開始 fuzz」

協定狀態機跑三次握手才到有趣的狀態；JWT 驗證走過簽名驗證才進業務邏輯。你想從這個中間點開始大量變異輸入，而不是每次都重走前置流程。fork server 本身就是這個概念——但它假設「初始化完畢的點」在進程生命週期的最前端，沒辦法讓你任意暫停在中間點、存下狀態、從那裡反覆繼續。

---

## 直覺：存檔、變異、讀檔

Snapshot fuzzing 的核心直覺非常直接：

```
                ┌─────────────────────────────────────┐
                │     snapshot fuzzing 的循環          │
                │                                     │
  target 執行   │   注入輸入 #1 → 執行 → crash/exit   │
  到達「準備    │              ↓                       │
  接受輸入」    │         從快照 reset                 │
  的狀態        │              ↓                       │
      │         │   注入輸入 #2 → 執行 → crash/exit   │
      ▼         │              ↓                       │
   拍快照 ──────►         從快照 reset                 │
  （記憶體、   │              ↓  ...                  │
   暫存器、    └─────────────────────────────────────┘
   裝置狀態）
```

這個模型：
- 不依賴 `fork()`
- 不需要目標有原始碼
- 不需要目標跑在有 OS 的環境
- 可以在「任意執行點」設下快照

只要你能在某個執行層級讀寫記憶體、控制執行，你就能做 snapshot fuzzing。

---

## fork vs snapshot：正面對比

```
指標               fork server              snapshot fuzzing
─────────────────────────────────────────────────────────────
需要 OS 支援        是（fork()）              否（hypervisor 層）
需要原始碼/插樁     通常是                    否
reset 機制          fork() + CoW              dirty page 還原
reset 成本          固定（~fork 開銷）        正比於 dirty pages 數
能處理 kernel-side  否                        是
副作用
能 fuzz kernel      否                        是（guest 是整個系統）
能 fuzz hypervisor  否                        是（嵌套 VM）
能 fuzz closed bin  有限（LD_PRELOAD shim）   是
覆蓋率來源          需要插樁                  Intel PT（無插樁）
確定性              副作用時破裂              極高（完整狀態 reset）
任意中間點快照      否                        是
```

---

## 速度：snapshot 為什麼可以比 fork 快

fork server 的速度瓶頸：

```
fork() syscall            ~50–200μs
  │
CoW 頁面錯誤（child 第一次寫才複製）
  │  大型進程大量寫入 → 大量 page fault → 累積很重
  ▼
exec/s 上限：通常 1,000–10,000/s（視目標記憶體大小）
```

Snapshot reset（VM-level，KVM）：

```
讀 dirty page log         （KVM_GET_DIRTY_LOG，幾微秒）
  │
只還原被改過的頁面         （mmap + memcpy，比例極小）
  │
重設 vCPU 暫存器          （幾微秒）
  ▼
reset 成本 ∝ dirty pages 數，與進程總大小無關
```

Nyx 論文（USENIX Security 2021）：打同一個目標，VM restart（syzkaller 風格）vs snapshot reset（Nyx），exec/s 差距 10–50 倍。

另一個數字：afl++ 的 persistent mode（`__AFL_LOOP()`，in-process 不 fork）可以到 100,000–500,000 exec/s，但需要手動管理狀態 reset，且只能打有原始碼的 target。Nyx 的 snapshot（~10,000–50,000 exec/s，視 dirty pages 數）比 persistent mode 慢，但能打 persistent mode 打不了的 closed binary 和 kernel——這是速度換通用性的正確 trade-off。

---

## 底層機制：dirty page tracking

VM 層的快速 reset 依賴 hypervisor 追蹤哪些記憶體頁被 guest 修改：

```
初始快照
 ┌────┬────┬────┬────┐
 │ A  │ B  │ C  │ D  │  所有頁：clean
 └────┴────┴────┴────┘

guest 執行輸入 N（寫入 A、C）
 ┌────┬────┬────┬────┐
 │ A★ │ B  │ C★ │ D  │  A, C: dirty
 └────┴────┴────┴────┘

reset：只把 A★、C★ 從快照還原
 ┌────┬────┬────┬────┐
 │ A  │ B  │ C  │ D  │  回到快照狀態
 └────┴────┴────┴────┘
```

KVM 用 memslot dirty tracking（或 `KVM_GET_DIRTY_LOG`）實作這個功能。Nyx 在此基礎上加 Intel PT 做 coverage 採集，形成完整的 snapshot fuzzer——Ch 29 的主題。

---

## snapshot fuzzing 的三個核心價值

**速度**：reset 成本跟 dirty pages 成正比，不跟進程大小成正比。對「每個輸入只改動少量記憶體」的目標，速度可以超過 fork。

**確定性**：每次輸入從完全相同的狀態出發。crash 必定可重現，corpus 最小化有意義，差分測試可以精確 diff。

**通用性**：同一套 snapshot 基礎設施，換 harness 就能打 kernel、hypervisor、UEFI、closed firmware、協定狀態機的任意中間點——這是 fork server 給不了的。

---

## 踩雷

**錯誤直覺：「snapshot 一定比 fork 慢，因為要複製更多狀態」**

正確理解：fork 的 CoW 是「寫了才複製」；snapshot reset 是「只還原被寫過的頁」。方向相反，成本函數不同。對記憶體寫入量小的 fuzzing session，snapshot reset 比 fork 快，因為省掉了 fork() syscall 本身和子進程初始化的固定開銷。

**錯誤直覺：「只要 target 沒有 global state，fork server 就夠了」**

正確理解：global state 不只在進程記憶體裡。kernel-side 的 fd、socket、inode、pipe buffer、futex 狀態——這些都不被 fork() 複製，在子進程裡是繼承的，不是重置的。一個 target 呼叫 open() 然後 close()，從 kernel 看留下了 inode 存取時間、可能的鎖——這些對下一個輸入可見。

**錯誤直覺：「設 snapshot 點需要原始碼」**

正確理解：你可以用動態分析（`strace`、GDB、QEMU monitor）找到目標的「準備接受輸入」點，然後把 snapshot 設在那個 guest physical address 上，完全不需要原始碼。

---

## 進階延伸

**Snapshot + stateful fuzzing**：最強的用法不是「從頭 reset」，而是「在狀態機的任意中間點存檔、從那點繼續」。這讓你能高效採樣深層狀態，而不是每次都重走握手流程。Nyx-Net（CCS 2022）把這個思路用在網路服務上——在「三次握手完成後」存快照，從那裡反覆 fuzz 一個 session 內的請求，解決了 stateful target（Ch 16–20）裡的核心問題。

**Process-level vs VM-level snapshot**：CRIU 做 process-level snapshot（只凍結一個進程），適合打 userland stateful target 但不能打 kernel——kernel-side 的 fd、inode、socket 狀態不屬於 CRIU 能序列化的範圍。VM-level snapshot 凍結整個系統，能打 kernel 但 overhead 更高。Ch 31 詳細比較四個層面（in-process、CRIU、KVM、Nyx）。

**Intel PT 的角色**：snapshot 解決「如何 reset」；Intel PT 解決「如何在不插樁的情況下拿 coverage」。兩者合體才是完整的 greybox snapshot fuzzer。沒有 Intel PT，你就只能用 QEMU TCG 插樁（30–50% overhead）或 QEMU user mode（只打 userland）——速度更慢、覆蓋面更窄。Ch 29–30 展開這兩者。

**AFL persistent mode 是什麼**：AFL++ 的 persistent mode（`__AFL_LOOP()`）是 snapshot 的最簡單近似——在同一個進程裡反覆呼叫同一個函數，省掉 fork 的開銷。代價是全域狀態不 reset（你要手動清），而且 target 必須有原始碼、必須能改。它比 VM-level snapshot 限制多，但比普通 fork server 快 5–10 倍——理解它的限制，正好說明 VM-level snapshot 解了哪些 persistent mode 解不了的問題。

---

## 動手練習

1. 跑 `strace -f -e trace=fork,clone,exit_group afl-fuzz -i seeds -o out -- ./target @@`（用一個小型 target），數 1 秒內發生多少次 fork，感受 fork server 的節奏。
2. 寫一個有副作用的 target（每次執行讀寫 `/tmp/fuzz_state`），用 afl++ 跑，看 coverage 是否出現意外成長（副作用累積讓後續輸入走到新路徑）。
3. 閱讀 Nyx 論文 Section 2（Motivation），找出他們用 fork server 打哪個目標時遇到什麼具體問題。

---

## 本章重點

- Fork server 的根本限制：副作用無法隔離（kernel-side 狀態）、不能打 kernel/hypervisor/closed binary、沒辦法在任意中間點存快照
- Snapshot fuzzing 的三個核心價值：速度（dirty page reset）、確定性（完整狀態 reset）、通用性（打任意目標）
- Reset 成本 ∝ dirty pages 數，不是進程總大小——這是 snapshot 速度優勢的根本來源
- Snapshot 點不需要原始碼，只需要能觀察 guest 執行

---

## 自我檢核

- [ ] 能說出 fork server 在哪四種情境下失效，並給每種情境一個具體例子？
- [ ] 能解釋為什麼 snapshot reset 有時比 fork 快？
- [ ] Dirty page tracking 是什麼？KVM 用什麼機制實作？
- [ ] 為什麼 kernel fuzzing 不能用 fork server？snapshot 解的是哪個層面的問題？

---

## 延伸閱讀

1. **Nyx: Greybox Hypervisor Fuzzing using Fast Snapshots and Affine Types**（Schumilo et al., USENIX Security 2021）
   - 讀 Section 2（Motivation）和 Section 3（Design Overview）——本章論點的一手來源，清楚說明為什麼 fork server 打不了 hypervisor，以及 snapshot 怎麼從 hypervisor 層解決
   - https://www.usenix.org/conference/usenixsecurity21/presentation/schumilo

2. **Brandon Falk（gamozolabs）：Snapshots: a force multiplier for fuzzing**
   - 讀正文全篇——從效能工程角度解釋 snapshot 為什麼是 fuzzing 加速最重要的槓桿，包含真實 exec/s 測量方法，視角與 Nyx 論文互補
   - https://gamozolabs.github.io/fuzzing/2019/12/05/vectorized_emulation.html

3. **AFL++ persistent mode 文件（`docs/fuzzing_in_depth.md`）**
   - 讀 persistent mode 章節——persistent mode 是 snapshot 的 in-process 近似（不 fork、同進程內 reset 函數局部狀態），理解它的限制正好說明為什麼 VM-level snapshot 才能打更廣的目標
   - https://github.com/AFLplusplus/AFLplusplus/blob/stable/docs/fuzzing_in_depth.md

---

→ [Ch 29 Nyx / kAFL](./29-nyx-kafl.md)
