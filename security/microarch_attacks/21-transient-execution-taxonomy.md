# Ch 21 — 瞬態執行分類學

> **目標**：建立一套完整的分類框架，讓你面對任何瞬態執行攻擊（包含未來尚未出現的）都能回答三個問題：瞬態窗口怎麼來的、跨越了哪條安全邊界、用什麼 covert channel 帶出秘密。這是 Part 3 的總結章，把 Ch 13–20 的所有攻擊納入同一張地圖。

---

## 為什麼需要分類學

Ch 13–20 我們逐一解剖了 Spectre-v1、Spectre-v2、SpectreRSB、Meltdown、L1TF、MDS 系列、Retbleed、MMIO Stale Data 等攻擊。每個攻擊都有自己的漏洞編號、觸發條件、概念驗證程式碼。如果不加以分類，面對下一個新出現的 CVE，我們只能重頭學一遍。

Canella 等人在 USENIX Security 2019 的論文「A Systematic Evaluation of Transient Execution Attacks and Defenses」提出了一套分類學，讓我們能用統一的框架描述所有已知攻擊，同時推導出尚未被發現的變體。論文附帶的網站 transient.fail 持續更新分類樹。我們這一章以該分類學為核心，把 Ch 13–20 的攻擊全部整合進來。

---

## 1. 兩個根本來源

所有瞬態執行攻擊的瞬態窗口只從兩個地方來：

```
瞬態執行攻擊
│
├── Spectre-type
│   原因：分支預測器走錯路
│   推測窗口：CPU 沿著「錯誤路徑」執行的那段時間
│   關鍵性質：推測路徑讀的記憶體，攻擊者「在正常情況下有權讀」
│              只是讀的位址是攻擊者選的，不是程式原本預期的
│
└── Meltdown-type
    原因：異常（fault）被推遲到 retire 才引發
    推測窗口：CPU 在引發異常前繼續執行的那段時間
    關鍵性質：推測路徑讀的記憶體，攻擊者「在正常情況下無權讀」
              越過了 U/S bit、Present bit、或其他保護機制
```

這個區分比「Spectre 讀用戶記憶體、Meltdown 讀核心記憶體」更精確。實際上 Spectre-v2 可以讓受害者 kernel gadget 讀 kernel 記憶體；而 Meltdown-MDS 讀的不是虛擬記憶體，而是 CPU 內部微架構 buffer。正確的區分是：**產生瞬態窗口的機制**，而非被讀取的記憶體位置。

---

## 2. Spectre-type：依預測器分類

Spectre-type 攻擊的差異在於**哪個預測器被汙染**。Intel/AMD CPU 裡有多個獨立的分支預測結構，每個都是獨立的攻擊面。

### 2.1 Spectre-PHT（Pattern History Table）

PHT 是 CPU 用來預測條件分支 taken/not-taken 的結構。每個核心有一個或多個 PHT，以分支指令的 PC 及分支歷史的 hash 作為索引。

```c
// Spectre-v1 的典型受害者 pattern (Ch 14)
if (x < array1_size) {            // 這個條件分支由 PHT 預測
    y = array2[array1[x] * 4096]; // 當 PHT 預測 taken，x 可以越界
}
```

攻擊流程：
1. 攻擊者用合法的 `x < array1_size` 案例反覆訓練 PHT，讓它學到「taken」
2. 傳入越界的 `x` 值
3. PHT 預測 taken → CPU 推測執行 `array1[x]`（越界讀）
4. 用 `array2[secret * 4096]` 把秘密編碼進 cache
5. Flush+Reload 還原秘密

分類完整資訊：
- 預測器：PHT（per-core，有些實作下跨 SMT 兄弟核心共用）
- CVE：2017-5753
- 防禦：`lfence`、陣列索引遮罩、SLH（Speculative Load Hardening）

### 2.2 Spectre-BTB（Branch Target Buffer）

BTB 快取間接跳轉（`jmp *rax`、`call *rax`）的目標位址。攻擊者能在自己的 process 裡訓練 BTB，使受害者 process 的間接跳轉跳到攻擊者選擇的 gadget。

```
攻擊者 process                    受害者 process (kernel)
┌─────────────────────────┐       ┌──────────────────────────────┐
│ jmp *rax  (rax=gadget)  │──────>│ BTB 被汙染                   │
│ jmp *rax  (rax=gadget)  │  反覆 │                              │
│ jmp *rax  (rax=gadget)  │  訓練 │ call *rbx                    │
└─────────────────────────┘       │   ↓ BTB 預測 → gadget        │
                                  │   gadget: mov rax,[secret]    │
                                  │          array2[rax*4096]     │
                                  └──────────────────────────────┘
```

分類完整資訊：
- 預測器：BTB（跨 process 共用，SMT 下跨 HT 共用）
- CVE：2017-5715
- 防禦：retpoline（把 indirect branch 替換成 ret 到已知地點）、IBRS（Intel）、eIBRS

### 2.3 Spectre-RSB（Return Stack Buffer）

RSB 是 CPU 的 call/ret 預測結構：`call` 把返回地址 push 進 RSB，`ret` 從 RSB 讀預測目標。RSB 容量有限（Intel 通常 16 個 entry）。

攻擊向量有兩種：

**RSB overflow（underflow）**：深呼叫後 RSB 被 push 滿，再彈出時回到更舊的「預測值」，這些舊值可能指向攻擊者控制的位址。

**RSB poisoning（SpectreRSB / ret2spec）**：攻擊者刻意讓自己的 call 推入偽造的返回地址，之後受害者的 ret 從 RSB 拿到攻擊者填的目標。

分類完整資訊：
- 預測器：RSB（per-core，Intel 16 entry ring buffer）
- CVE：無官方統一編號
- 防禦：RSB stuffing（context switch 時用 `call`/`pause`/`lfence` 把 RSB 填滿）、eIBRS（阻止跨 privilege level 的 RSB 使用）

### 2.4 Spectre-STL（Store-to-Load Forwarding）

Store-to-Load Forwarding 是 CPU 的最佳化：當 load 和 store 存取同一位址時，CPU 不等 store 寫入記憶體，直接把值轉發給 load。Memory Disambiguation Unit（MDU）負責預測哪些 load/store 有別名關係。

Spectre-v4 利用 MDU 的誤預測：MDU 預測某個 load 和任何 pending store 無別名關係，直接取舊的值 → 推測路徑拿到過時的（可能是攻擊者控制的）舊值。

分類完整資訊：
- 預測器：MDU（Memory Disambiguation Unit）
- CVE：2018-3639
- 防禦：SSBD（Speculative Store Bypass Disable），透過 `prctl(PR_SET_SPECULATION_CTRL, ...)` 啟用；效能損失約 2–8%

---

## 3. Meltdown-type：依越過的保護分類

Canella 的記法：Meltdown-X，X 表示被「推遲引發」的 fault 類型。

### 3.1 Meltdown-US（User/Supervisor）

U/S bit 在 PTE 裡控制 user mode 能否存取該頁。當 user mode 嘗試 load kernel 頁，應該引發 #PF；但在 fault retire 之前，推測窗口中已經把 kernel 資料讀進暫存器。

```
user-mode load 從 kernel VA
  → TLB hit，U/S bit = 0，觸發 #PF
  → 但在 pipeline 裡 #PF 尚未 commit
  → 推測窗口：mov rax, [kernel_addr]  // 資料真的被讀了
             mov rbx, probe[rax*64]  // 秘密編碼進 cache
  → #PF 生效，架構可見狀態回滾
  → Flush+Reload 從 cache 側通道取出秘密
```

分類完整資訊：
- 被繞過的保護：PTE U/S bit
- CVE：2017-5754
- 受影響：Intel 大部分 Skylake 前後世代；AMD 一般不受影響（L1 cache 存取在權限檢查後）
- 防禦：KPTI（Kernel Page Table Isolation）——user mode 和 kernel mode 使用不同 CR3，user mode 頁表只保留 syscall 入口的最小 kernel mapping

### 3.2 Meltdown-P（Present bit）

PTE 的 Present bit = 0 代表頁面不在記憶體中（已 swap 出、或刻意設為 not-present）。存取這樣的頁面應引發 #PF，OS 處理 page fault。但 Intel CPU 在這個 fault retire 之前，**會把 PTE 裡的實體位址（即使 Present=0）拿去查 L1D cache**。

這讓攻擊者能：把一個 not-present PTE 的 PA 欄位設為目標記憶體的實體位址 → CPU 推測期間查 L1 → 目標資料若在 L1 cache 就被讀出。

Foreshadow（L1TF）的三個 CVE：
- CVE-2018-3615：針對 SGX enclave 頁（enclave 頁 PTE 設為 not-present）
- CVE-2018-3620：針對 OS kernel（SMM、hypervisor 頁）
- CVE-2018-3646：針對 VMM，L1TF in virtual machines

分類完整資訊：
- 被繞過的保護：PTE Present bit
- 防禦：L1D flush on VM entry（`VMENTER` 時清空 L1D）；SGX 需要 microcode 更新

### 3.3 Meltdown-MDS（Microarchitectural Data Sampling）

MDS 系列的特殊之處：它們不繞過虛擬記憶體保護，而是從 CPU 內部微架構 buffer 採樣殘留資料。這些 buffer 包含來自不同安全域的舊資料。

```
CPU 內部 buffer 殘留資料的來源：
┌──────────────────────────────────────────────────────┐
│  Line Fill Buffer (LFB) — 從 L2/記憶體 fetching 的中繼  │
│  Store Buffer       — 已 commit 但尚未寫出的 store       │
│  Load Port          — load 執行完等待寫入暫存器的資料     │
│  (Intel Atom: 更多 buffer 暴露)                         │
└──────────────────────────────────────────────────────┘
```

四個主要變體：
- MSBDS/Fallout（CVE-2018-12126）：採樣 Store Buffer
- MFBDS/ZombieLoad（CVE-2018-12130）：採樣 Line Fill Buffer（本機系統顯示 VULNERABLE）
- MLPDS/RIDL（CVE-2018-12127）：採樣 Load Port
- MDSUM（CVE-2019-11091）：採樣 Uncacheable Memory 路徑

防禦：`VERW` 指令（清空微架構 buffer）在 kernel→user 轉換和 VM entry 時執行；微碼更新提供 MDS_NO capability bit。

### 3.4 Meltdown-GP（General Protection fault）

CVE-2019-1125，SWAPGS Attack。`swapgs` 指令在 syscall entry 交換 GS base 暫存器，用來切換到 kernel percpu 結構。如果在 non-maskable interrupt（NMI）或 #DF 等中斷路徑中 `swapgs` 的執行被推測跳過或多執行，GS 會指向錯誤的地址，後續讀取 `[gs:offset]` 在推測路徑中讀到錯誤值。

分類完整資訊：
- 被繞過的保護：#GP（General Protection fault）
- 防禦：在 NMI 等路徑前後加 `lfence`

---

## 4. 完整分類樹

```
瞬態執行攻擊家族
│
├─ Spectre-type (分支預測走錯路)
│  特徵：讀「有讀取權限」的記憶體，洩漏在於推測路徑選擇了攻擊者期望的位址
│
│  ├─ Spectre-PHT (條件分支誤預測，PHT 被汙染)
│  │   ├─ In-place: 同 process 內訓練與觸發 (Spectre-v1, CVE-2017-5753)
│  │   └─ Out-of-place: 跨 process/HT 訓練，victim 觸發
│  │
│  ├─ Spectre-BTB (間接跳轉目標誤預測，BTB 被汙染)
│  │   ├─ Same-process BTI
│  │   └─ Cross-process BTI (Spectre-v2, CVE-2017-5715)
│  │
│  ├─ Spectre-RSB (ret 目標誤預測，RSB stale/poisoned)
│  │   ├─ RSB overflow / underflow
│  │   ├─ SpectreRSB / ret2spec (stale RSB entry)
│  │   └─ Retbleed (CVE-2022-29900/29901): ret 在特定條件回退用 BTB)
│  │
│  └─ Spectre-STL (Store-to-Load Forwarding 誤預測)
│      └─ Spectre-v4 / SSB (CVE-2018-3639)
│
└─ Meltdown-type (異常被推遲，推測路徑越過保護邊界)
   特徵：讀「無讀取權限」的記憶體，洩漏在於 fault commit 前的推測存取
   記法：Meltdown-X，X = 被推遲的 fault 種類
   │
   ├─ Meltdown-US (User/Supervisor bit 被繞過)
   │   └─ Classic Meltdown / RDCL (CVE-2017-5754, #PF 因 U/S 違規)
   │
   ├─ Meltdown-P (Present=0 PTE 被繞過)
   │   ├─ Foreshadow-SGX (CVE-2018-3615)
   │   ├─ Foreshadow-NG OS (CVE-2018-3620)
   │   └─ Foreshadow-NG VMM (CVE-2018-3646)
   │
   ├─ Meltdown-MDS (微架構 buffer 殘留資料採樣)
   │   ├─ MSBDS / Fallout (CVE-2018-12126, Store Buffer)
   │   ├─ MFBDS / ZombieLoad (CVE-2018-12130, Line Fill Buffer)
   │   ├─ MLPDS / RIDL (CVE-2018-12127, Load Port)
   │   ├─ MDSUM (CVE-2019-11091, Uncacheable memory)
   │   └─ MMIO Stale Data (CVE-2022-21123 et al., uncore buffer via MMIO)
   │       ← 本機 /sys/devices/system/cpu/vulnerabilities/mmio_stale_data
   │          顯示 Vulnerable
   │
   └─ Meltdown-GP (General Protection fault 被繞過)
       └─ SWAPGS Attack (CVE-2019-1125)
```

---

## 5. 攻擊三要素框架

任何瞬態執行攻擊都可以用三個維度完全描述：

```
攻擊 = (Trigger) + (Secret Access) + (Covert Channel)

Trigger — 怎麼製造瞬態窗口：
  Spectre-type:
    - 訓練條件分支預測器 (PHT)
    - 訓練間接跳轉預測器 (BTB)
    - 汙染/耗盡返回地址堆疊 (RSB)
    - 觸發 store-to-load forwarding 誤預測 (MDU)
  Meltdown-type:
    - 存取 U/S 保護的頁面 → 推遲 #PF
    - 存取 not-present 頁面 → 推遲 #PF，但 CPU 查 L1
    - 觸發微架構 buffer 採樣路徑 (MDS/MMIO)
    - 觸發 #GP 的推遲 (SWAPGS)

Secret Access — 在瞬態窗口內用什麼方式讀秘密：
  - 直接越界讀 (Spectre-v1: array[attacker_idx])
  - 跳到 victim gadget 讀 (Spectre-v2: gadget in kernel)
  - 直接越權 load (Meltdown: mov rax, [kernel_addr])
  - L1D PA 查找 (L1TF: not-present PTE with crafted PA)
  - 微架構 buffer 採樣 (MDS/MMIO: fault replay or MMIO path)

Covert Channel — 怎麼把瞬態讀到的秘密帶出架構可見狀態：
  - Flush+Reload (最普遍：需 shared memory with victim)
  - Prime+Probe (不需 shared memory，需 LLC 控制)
  - Flush+Flush (比 F+R 更隱蔽，利用 clflush 延遲差異)
  - Port contention (更難偵測：利用 execution port 競爭)
  - Translation-based (TLB、Page Table Walk 的 side channel)
```

---

## 6. 面對新 CVE 的分類方法

這是這一章最實用的技能。每次新攻擊出來，走這五步：

```
Step 1: 什麼觸發了瞬態執行？
  問：是某個預測器走錯路？
    → 是 → Spectre-type，繼續 Step 2a
  問：是某種 fault 的 check 被延遲到 retire？
    → 是 → Meltdown-type，繼續 Step 2b

Step 2a: 哪個預測器被汙染/走錯？
  → 條件分支 → Spectre-PHT
  → 間接跳轉 → Spectre-BTB
  → ret 指令  → Spectre-RSB
  → load/store 別名預測 → Spectre-STL

Step 2b: 哪個保護被推遲繞過？
  → U/S bit → Meltdown-US
  → Present bit → Meltdown-P
  → 微架構 buffer 殘留 → Meltdown-MDS
  → General Protection → Meltdown-GP

Step 3: 跨越了哪條安全邊界？
  → User → Kernel（Meltdown, Spectre-v2 via kernel gadget）
  → Guest → Host（Foreshadow-NG VMM, MMIO Stale Data）
  → Enclave → OS（Foreshadow-SGX）
  → Cross-thread（MDS via SMT）
  → Cross-process（Spectre-BTB via shared BTB）

Step 4: Covert channel 是什麼？
  → 絕大多數用 cache timing（F+R 或 P+P）
  → 少數用 port contention（更難偵測）

Step 5: 現有防禦能擋哪些？
  → KPTI：擋 Meltdown-US，對其他 Meltdown-type 無效
  → retpoline：擋 Spectre-BTB，不擋 PHT/RSB/STL
  → RSB stuffing：擋 RSB attacks，不擋 BTB
  → VERW：擋 MDS（LFB/SB/LP），不擋 MMIO Stale Data！
  → VERW + microcode（MMIO variant）：擋 MMIO Stale Data
  → eIBRS：擋 Spectre-v2 跨 privilege level，不擋 PHT
  → lfence/masking：擋 Spectre-PHT，不擋 BTB/RSB
  → L1D flush：擋 L1TF/Foreshadow，對 MDS 效果有限
```

---

## 7. Ch 13–20 全攻擊對照表

| 攻擊名稱 | 章節 | CVE | 分類 | Trigger | 秘密讀取方式 | 防禦 |
|---------|------|-----|------|---------|------------|------|
| Spectre-v1 | Ch 14 | CVE-2017-5753 | Spectre-PHT | 條件分支誤預測 | gadget OOB load | lfence、陣列遮罩 |
| Spectre-v2 | Ch 16 | CVE-2017-5715 | Spectre-BTB | 間接跳轉誤預測 | victim kernel gadget | retpoline、IBRS、eIBRS |
| SpectreRSB | Ch 17 | — | Spectre-RSB | RSB underflow/poison | stale RSB entry | RSB stuffing、eIBRS |
| ret2spec | Ch 17 | — | Spectre-RSB | stack/RSB 不同步 | stale RSB target | eIBRS |
| Spectre-v4/SSB | — | CVE-2018-3639 | Spectre-STL | MDU 誤預測 | stale load value | SSBD |
| Meltdown/RDCL | Ch 18 | CVE-2017-5754 | Meltdown-US | #PF (U/S bit) | 直接讀 kernel VA | KPTI |
| Foreshadow-SGX | Ch 19 | CVE-2018-3615 | Meltdown-P | Present=0 PTE | L1D PA lookup | microcode + L1D flush |
| L1TF-OS | Ch 19 | CVE-2018-3620 | Meltdown-P | Present=0 PTE | L1D PA lookup | L1D flush |
| L1TF-VMM | Ch 19 | CVE-2018-3646 | Meltdown-P | Present=0 PTE | L1D PA lookup | L1D flush on VM entry |
| Fallout/MSBDS | Ch 19 | CVE-2018-12126 | Meltdown-MDS | fault replay | Store Buffer 殘留 | VERW + microcode |
| ZombieLoad/MFBDS | Ch 19 | CVE-2018-12130 | Meltdown-MDS | fault replay | LFB 殘留 | VERW + microcode |
| RIDL/MLPDS | Ch 19 | CVE-2018-12127 | Meltdown-MDS | fault | Load Port 殘留 | VERW + microcode |
| MDSUM | Ch 19 | CVE-2019-11091 | Meltdown-MDS | uncacheable | UC memory path | VERW + microcode |
| SWAPGS Attack | — | CVE-2019-1125 | Meltdown-GP | #GP 推遲 | stale GS read | lfence in NMI |
| Retbleed | Ch 20 | CVE-2022-29900/29901 | Spectre-BTB (via ret) | ret→BTB fallback | kernel gadget | eIBRS、RSB stuffing |
| Downfall/GDS | Ch 20 | CVE-2022-40982 | Meltdown-MDS-like | gather op | Gather buffer 殘留 | microcode (perf -50%) |
| Zenbleed | Ch 20 | CVE-2023-20593 | 暫存器檔案 bug | vzeroupper 競態 | YMM 暫存器殘留 | microcode |
| Inception/SRSO | Ch 20 | CVE-2023-20569 | Spectre-RSB (phantom) | synthetic call 注入 | RSB poisoning | microcode、Zen 5 |
| MMIO Stale Data | Ch 20 | CVE-2022-21123 et al. | Meltdown-MDS | MMIO path | uncore buffer 殘留 | VERW + MMIO microcode |

---

## 8. 為什麼分類學有實際意義

分類不是學術遊戲，它直接影響工程決策：

**防禦選擇**：知道攻擊是 Meltdown-US，才知道 KPTI 能擋；知道是 Meltdown-MDS，才知道需要 VERW 而 KPTI 無效。混淆這兩類會讓防禦方案打錯靶。

**廠商 triage**：新 CVE 出來，廠商第一個問題是「現有 microcode/軟體補丁能否覆蓋？」分類學提供答案。Retbleed 出來時，廠商能快速確認：這是 Spectre-BTB via ret，eIBRS 有部分覆蓋，但需要額外的 RSB stuffing。

**完整性驗證**：分類學可以用來系統搜尋尚未發現的變體。Canella 的論文用這個方法找出了多個先前未報告的變體。如果 PHT/BTB/RSB 都有對應攻擊，STL 是否也有？（有，就是 Spectre-v4。）如果 Meltdown-US/P 存在，Present=0 但有其他 bit 問題的 PTE 是否也有問題？

**補丁驗證**：`/sys/devices/system/cpu/vulnerabilities/` 下的每個 entry 都對應分類樹上的一個節點。看到 `mds: Vulnerable` 代表 VERW 未啟用；看到 `mmio_stale_data: Vulnerable` 代表 MMIO microcode 未套用，即使 `mds: Mitigation: Clear CPU buffers` 顯示 MDS 已修。這兩者對應分類樹上不同的節點，防禦不可互換。

---

## 對比與取捨

**Spectre-type vs Meltdown-type 的防禦成本比較**

Meltdown-type 的修補通常更乾淨：KPTI 一次解決 Meltdown-US；L1D flush 解決 L1TF。代價是效能（KPTI 在系統呼叫密集的工作負載損失 5–30%），但補丁邏輯清晰。

Spectre-type 更難根治，因為它根本上是「CPU 幫你讀了你有權讀的記憶體，只是你不想讀那個位址」。你沒有辦法在硬體層面完全禁止這件事，只能：要求所有軟體在危險點前插入 `lfence`（CFI/SLH），或讓 gadget 消失（retpoline），或讓預測器隔離（eIBRS）。任何方案都不能保證完整覆蓋。

**「關掉 covert channel」vs「關掉 trigger」**

理論上，如果 covert channel 消失，所有瞬態執行攻擊都失效。強制所有記憶體存取都經過恆定時間路徑（no-cache）能做到這點，但效能損失無法接受。因此每個攻擊都有自己的窄版防禦，針對 trigger 或 secret access。這也說明為什麼每出一個新攻擊就需要新補丁。

**「Canella 分類」vs「Intel/AMD 官方分類」**

Intel 和 AMD 的官方分類偏向以自家硬體受影響的 CVE 群組為單位，和 Canella 的機制分類並不完全對齊。兩者都需要參考：Canella 幫助理解機制，廠商分類幫助確認哪個 microcode 修了哪個問題。

---

## 踩雷集錦

**坑一：把「需要訓練」當 Spectre-type 的充分條件**

PHT、BTB、RSB 攻擊都需要某種「訓練」或「汙染」預測器的動作，但所需的存取權限差很多。PHT 訓練不需要特殊權限，同一個 process 的正常迴圈就能完成。BTB 訓練在有些 CPU 上需要 SMT 同核（攻擊者要和受害者在同一個實體核心的不同 HT）；在另一些 CPU 上共用整顆 die 的 BTB 就可以跨核訓練。把所有 Spectre-type 攻擊視為「需要相同難度前提條件」會得出錯誤的威脅評估。

**坑二：以為 Meltdown 比 Spectre 更危險，因為它直接讀核心記憶體**

這在 2018 年初確實成立——原始 Meltdown 讓任意 user process 讀完整核心記憶體，感覺更嚴重。但從修補角度看，Meltdown-US 的修補（KPTI）是一次性的，之後的 Intel CPU 硬體層就修掉了。Spectre-PHT 因為沒有通用硬體修補，理論上至今仍然存在，只是需要的 gadget 被逐一消除。長遠來看，「能修的」不代表「不嚴重」，「難修的」更值得持續關注。

**坑三：以為 VERW 能修所有 MDS 攻擊包含 MMIO**

`cat /sys/devices/system/cpu/vulnerabilities/mds` 顯示 `Mitigation: Clear CPU buffers` 不代表 MMIO Stale Data 也被修了。兩者需要不同的 microcode 更新。VERW 在 MDS 的 LFB/SB/LP 路徑有效，但 MMIO 路徑的 uncore buffer 在 2022 年才被發現，需要額外 microcode。分類學上它們是 Meltdown-MDS 樹下的不同分支，防禦不可互換。本機系統 `mmio_stale_data: Vulnerable` 顯示這個坑仍然存在。

**坑四：把 Canella 2019 分類學當成完整不變的最終答案**

Canella 的論文是截至 2019 年最完整的整理。Retbleed（2022）、Downfall（2022）、Zenbleed（2023）、Inception（2023）、MMIO Stale Data（2022）都在論文之後出現，其中 Zenbleed 根本不符合原始分類學的任何 node（它是暫存器檔案的競態條件，不是預測器汙染也不是 fault 推遲）。分類學是工具，不是教條；新攻擊出來時它可能需要擴展。

---

## 進階：再往深一層

### 可組合性（Composability）

不同的 trigger 和 covert channel 可以任意組合。Canella 的論文明確指出，一個 trigger 配多個 covert channel，以及多個 trigger 合用，都能創造新的攻擊面。這是「有沒有被看到」和「有沒有被發現」的差距。

### Phantom Speculation（幻象推測）

Inception（CVE-2023-20569）引入了一個新概念：攻擊者能在 AMD CPU 上製造「不存在的 call 指令」的推測效果，讓 RSB 被 push 一個假的返回地址。這不屬於 Canella 原始分類的任何一個 trigger，催生了 Phantom Speculation 這個術語，代表「沒有對應架構指令的推測行為」。

### Transient Execution Cross-Privilege-Level Considerations

Spectre-BTB 的防禦在不同 privilege level 轉換時有不對稱問題。eIBRS 保證跨 privilege level（user→kernel、guest→host）的間接分支不共用 BTB 條目，但同 privilege level 內的間接分支仍然暴露。理解這個邊界是分析 Retbleed 的關鍵：Retbleed 利用 `ret` 指令在特定條件下降級使用 BTB 而非 RSB，突破了 eIBRS 的假設。

### 分類學指導的模糊測試

transient.fail 網站提供了一個「空洞分析」：把所有已知 trigger × 所有已知保護邊界做矩陣，看哪些 cell 還沒有已知攻擊或已知「不受影響」的分析。這些空格是模糊測試的優先目標。Canella 的論文本身就用這個方法找到了數個未報告變體。

---

## 動手練習

**練習 A：攻擊辨別**

拿到 CVE-2022-21166（MMIO Stale Data 的 Device Register Partial Write 變體），回答：
1. 瞬態窗口由什麼產生？（MMIO write 的 fault 路徑 or 正常路徑的 uncore buffer？）
2. 分類是 Meltdown-type 還是 Spectre-type？依據是什麼？
3. 所需的攻擊者權限是什麼？（是否需要直接 MMIO 存取？還是任何 unprivileged process 都能觸發？）

**練習 B：防禦矩陣**

製作一個 4×4 矩陣，row = 四類 Spectre-type（PHT/BTB/RSB/STL），col = 四種防禦（KPTI/retpoline/eIBRS/RSB stuffing）。每格填「能擋 / 不能擋 / 部分」，並寫出一句理由。

**練習 C：分類新變體**

用 step 1–5 的流程分析 Zenbleed（CVE-2023-20593）：
1. `vzeroupper` 指令競態是 prediction failure 還是 fault 推遲？
2. 被洩漏的 YMM 暫存器殘留是否符合 Meltdown-MDS 的定義（微架構 buffer 殘留）？
3. 如果不符合，你會如何擴展 Canella 分類學來容納它？

**練習 D：本機驗證**

```bash
# 在 Linux 系統上執行
for f in /sys/devices/system/cpu/vulnerabilities/*; do
    printf "%-30s: %s\n" "$(basename $f)" "$(cat $f)"
done
```

把輸出對照本章分類樹，找出哪些 node 顯示 Vulnerable，並解釋：
(a) 缺少的是 microcode 還是 kernel 補丁？
(b) 這台機器在哪個使用場景下風險最高？（雲端 hypervisor / 多租戶 / 個人桌機？）

---

## 本章重點整理

- 所有瞬態執行攻擊的窗口來自兩個根本來源：分支預測走錯路（Spectre-type）或異常被推遲（Meltdown-type）。
- Spectre-type 按預測器分類：PHT（條件分支）、BTB（間接跳轉）、RSB（ret）、STL（store-to-load forwarding）。
- Meltdown-type 按被繞過的保護分類：Meltdown-X，X = 被推遲的 fault（US/P/MDS/GP）。
- 任何攻擊 = Trigger + Secret Access + Covert Channel，三個維度分別可以有不同選擇。
- 分類學讓你在拿到新 CVE 時能快速回答：哪個防禦能擋、哪個不行、影響哪些場景。
- Canella 2019 分類是截至 2019 年的快照；之後的攻擊（Retbleed/Downfall/Zenbleed/Inception）需要延伸分類。
- 防禦不可互換：VERW 擋 MDS 但不擋 MMIO Stale Data；KPTI 擋 Meltdown-US 但不擋任何 Spectre-type；retpoline 擋 BTB 但不擋 RSB。

---

## 自我檢核

1. Spectre-PHT 和 Spectre-BTB 都是 Spectre-type，根本差異是什麼？訓練難度和所需攻擊者權限有什麼不同？

2. 為什麼 Meltdown-type 攻擊中 CPU 能讀取「無讀取權限」的記憶體？fault 在哪個 pipeline 階段被發現，又在哪個階段才真正生效？

3. Meltdown-P（L1TF）和 Meltdown-MDS（ZombieLoad）同屬 Meltdown-type，但機制完全不同。用一句話解釋兩者的差異。

4. 為什麼 VERW 能清掉 LFB/Store Buffer/Load Port 的殘留資料，但同樣標示為 Meltdown-MDS 的 MMIO Stale Data 卻需要額外的 microcode？

5. Retbleed 被分類為 Spectre-BTB（via ret），不是 Spectre-RSB。原因是什麼？（提示：`ret` 指令的預測器選擇條件）

6. 給定一個新論文的摘要：「當 CPU 執行 prefetch 指令時，推測路徑存取了 Present=0 的頁面，洩漏了其他行程的敏感資料。」依照本章五步驟框架分類這個攻擊。

---

## 延伸閱讀

- Canella, C. et al. "A Systematic Evaluation of Transient Execution Attacks and Defenses." *USENIX Security 2019*. https://www.usenix.org/conference/usenixsecurity19/presentation/canella — 本章核心參考，Figure 1 包含完整分類樹，Section 6 包含未來攻擊面的空洞分析。

- https://transient.fail/ — Canella 等人維護的活文件，持續更新分類樹，包含 2019 年後所有新攻擊的分類位置。每次新 CVE 出來是第一個確認分類的地方。

- Kocher, P. et al. "Spectre Attacks: Exploiting Speculative Execution." *IEEE S&P 2019*. — 定義了 Variant 1（PHT）、Variant 2（BTB）的原始論文，包含攻擊者訓練流程的詳細描述。

- Lipp, M. et al. "Meltdown: Reading Kernel Memory from User Space." *USENIX Security 2018*. — Meltdown-US 的原始論文，包含異常推遲機制的微架構說明。

- Intel Corporation. "MMIO Stale Data Advisory: Deep Dive." https://www.intel.com/content/www/us/en/developer/articles/technical/software-security-guidance/technical-documentation/processor-mmio-stale-data-vulnerabilities.html — 解釋 MMIO Stale Data 系列與 VERW 的關係，確認哪些 processor 需要 microcode 更新。

---

Part 3 到此收束——你手上已有整套瞬態執行攻擊的分類地圖：兩個根本來源、四種 Spectre 預測器、四類 Meltdown fault、三要素框架、以及面對新 CVE 的五步驟分析流程。Ch 13–20 的每個攻擊都在這張地圖上找到了自己的位置。Part 4 換個戰場：Rowhammer 不靠推測執行，而是直接翻轉 DRAM 裡的 bit，攻擊的是記憶體硬體物理性質，而非 CPU pipeline 的推測行為。

→ [下一章](22-rowhammer-basics.md)
