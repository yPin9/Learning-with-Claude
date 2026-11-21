# Ch 32 — eBPF：verifier / JIT、map 型漏洞、verifier bypass

> **目標**：理解 eBPF 的 verifier 在整個子系統裡扮演的角色——為什麼「verifier 犯一個錯」約等於「整個記憶體安全模型失效」；拆解 verifier bypass 類 bug 的典型成因（範圍追蹤/bounds tracking 錯誤、路徑剪枝/path pruning 錯誤），配兩個公開 CVE（CVE-2021-3490、CVE-2023-2163）的 root cause；理解概念層的利用升級路徑，以及 `unprivileged_bpf_disabled` 這個專門為此而生的防禦機制。

> **環境**：Linux 6.6 LTS、x86-64 基線。verifier 的內部實作（`kernel/bpf/verifier.c`）幾乎每個版本都有調整，本章描述的機制與 CVE 細節以查證到的版本為準；跨版本差異處會標注。

## 從 classic BPF 到 eBPF：為什麼需要一個通用的使用者可程式化 kernel

eBPF 的前身是 1992 年提出的 classic BPF（cBPF）——當時只用來做封包過濾（`tcpdump` 背後的技術），指令集極簡單（只有少量累加器與暫存器），verifier 相對容易證明安全性，因為程式**本來就表達不出太複雜的邏輯**。

隨著 Linux 想讓使用者態能更靈活地客製化 kernel 行為（追蹤、網路加速、安全監控、cgroup 資源控制……），cBPF 的表達力不夠用——於是 3.18（2014）引入 extended BPF（eBPF）：指令集擴充成接近一般通用暫存器機器的規模（11 個 64 位元暫存器、完整的 ALU 運算、呼叫 helper 函式的能力、map 作為持久化資料結構）。這個擴充讓 eBPF 從「只能做簡單封包過濾」變成「幾乎能寫出任意邏輯的通用程式」——而**verifier 必須跟著指令集的表達力一起變複雜**,才能繼續證明「這段更複雜的程式仍然安全」。

這正是本章要講的礦區根源：**cBPF 的簡單換來驗證容易，eBPF 的通用換來驗證困難**。和 [Ch 30](./30-netfilter-nftables.md)、[Ch 31](./31-io-uring.md) 的「表達力換攻擊面」是同一個取捨模式,只是這次的代價不是物件生命週期管理,而是靜態分析問題本身的困難度。

## 為什麼 eBPF 是礦區：一個驗證器失誤 = 整個信任模型崩潰

前兩章的 nf_tables 與 io_uring，bug 都藏在**資料結構生命週期管理**裡——UAF、double-free、refcount 錯誤。eBPF 的礦區成因完全不同：它的核心風險**不是記憶體管理**，而是**一個單一的靜態分析器（verifier）是否正確**。

理解這點要先理解 eBPF 的基本契約：使用者態透過 `bpf(2)` 系統呼叫,把一段 eBPF bytecode 載入 kernel（`BPF_PROG_LOAD`）。這段 bytecode **會被 kernel 直接執行**（經 JIT 編譯成原生機器碼,或用 interpreter 執行）——這等於讓使用者把任意程式碼跑進 ring 0。能讓這件事「安全」的唯一機制,是 verifier：在程式碼真正被接受之前,靜態分析整段 bytecode,證明它不會做任何危險的事——不會存取未初始化的記憶體、不會讓指標跑出合法範圍、不會有無限迴圈、不會洩漏 kernel 位址給使用者態。

**這個模型的信任鏈只有一個環節**：只要 verifier 說「安全」，kernel 就直接執行。如果 verifier 的某個分析邏輯有錯——把一段實際上會造成 OOB 讀寫的程式，誤判為安全——攻擊者得到的不是一個「需要輾轉升級的原語」，而是**直接、確定性的任意記憶體讀寫**。這和前兩章的 UAF/double-free 完全不同：UAF 給你的是一次性的、常常視窗很窄的機會,要靠 spray/reclaim 才能變成可控原語；verifier bypass 給你的是一段**你自己寫的程式，kernel 保證會照你的意思執行**。這是為什麼業界普遍認為 eBPF verifier bug 是 kernel exploitation 裡「性價比最高」的一類——一旦找到，利用過程往往比傳統 heap 利用簡單得多。

這帶來礦區的結構性原因：

1. **verifier 要做的是一般化的靜態分析問題，理論上就是困難的**。它要對任意合法 eBPF 程式，追蹤每個暫存器在**所有可能執行路徑**上的數值範圍（value range / bounds tracking），並證明所有記憶體存取都落在合法範圍內。這類問題在完全一般化的情況下是不可決定的（undecidable）——verifier 必須用近似、保守的方法，任何一處近似沒做對，就可能把不安全的程式誤判為安全。
2. **ALU 運算的範圍追蹤特別容易出錯**。eBPF 支援 32 位元與 64 位元的算術邏輯運算（ALU32/ALU64），verifier 要同步維護「這個暫存器的 64 位元範圍」和「低 32 位元範圍」兩套追蹤，兩者之間的同步（尤其是位元運算 AND/OR/XOR、位移運算）歷史上反覆出錯——CVE-2021-3490 就是這一類。
3. **路徑剪枝（path pruning）是效能優化，但也是正確性假設**。verifier 為了不要把每條執行路徑都重新分析一遍（組合數量會爆炸），用一種「如果這個狀態已經和之前驗證過的某個狀態等價，就不用重新驗證」的剪枝策略。判斷「等價」的邏輯如果算錯，就可能把一條實際上不安全的路徑，誤判成「和已驗證過的安全路徑等價」而直接放行——CVE-2023-2163 屬於這一類。
4. **可達性同樣可能被 user namespace 放大**，取決於發行版的 `unprivileged_bpf_disabled` 預設值（見防禦面一節）——這點和 [Ch 30](./30-netfilter-nftables.md)、[Ch 31](./31-io-uring.md) 共有的放大機制一致。

### 一條不完整但有感的時間線

| 年份 | CVE | 大致性質 |
|---|---|---|
| 2021 | CVE-2021-3490 | ALU32 位元運算（AND/OR/XOR）的範圍追蹤不同步（本章案例一） |
| 2023 | CVE-2023-2163 | 路徑剪枝誤判（本章案例二） |

> 我們只列查證過的兩個案例；eBPF verifier 歷史上還有更早期（2017 年前後開始）的範圍追蹤類 bug 報告，但本次 WebSearch 沒有取得足夠細節確認其 root cause 與影響範圍，這裡不列未經查證的編號。即使只看這兩年，跨兩個不同版本都還在出範圍追蹤或路徑分析層級的 bug，說明這類問題不是「修一次就永久解決」的單點缺陷，而是**verifier 複雜度持續增加（為了支援更多合法但複雜的程式模式）所伴隨的結構性風險**。

### 和前兩章的攻擊面放大機制對照

| 子系統 | 攻擊面怎麼打開 | 一旦拿到 bug，原語的性質 |
|---|---|---|
| nf_tables（[Ch 30](./30-netfilter-nftables.md)） | user namespace 放大的 `CAP_NET_ADMIN` | 需要 spray/reclaim 升級的 UAF |
| io_uring（[Ch 31](./31-io-uring.md)） | 預設對所有人開放 | 需要精確控制 race window 的 UAF/OOB |
| eBPF（本章） | 視發行版而定的 `unprivileged_bpf_disabled` | **一次載入即可重複觸發、精確可控**的任意讀寫 |

這張表的最後一列是本章的核心論點：同樣是「bug」，eBPF verifier bypass 給出的原語**品質**遠高於典型的 heap UAF——這也是為什麼許多 kernel exploitation 研究者認為,只要可達（`unprivileged_bpf_disabled=0`），eBPF 永遠是第一個該檢查的方向。

## 先建立直覺：verifier 怎麼「審查」一段程式

把 verifier 想成一個極度謹慎的靜態分析編譯器階段——它不執行你的程式，只是**用符號方式模擬**每一條可能的執行路徑，對每個暫存器維護一個「這個值現在可能是什麼範圍」的抽象：

```
使用者態：bpf(BPF_PROG_LOAD, ...)  遞交一段 eBPF bytecode
      │
      ▼
verifier 開始走訪程式的控制流圖（CFG）
      │
      ├─ 對每條指令，更新每個暫存器的狀態：
      │     type（SCALAR_VALUE / PTR_TO_MAP_VALUE / PTR_TO_CTX / ...）
      │     range（umin/umax/smin/smax，64 位與 32 位分開維護）
      │
      ├─ 遇到分支（JMP）：兩條路徑都要走，各自維護獨立的暫存器狀態快照
      │
      ├─ 遇到記憶體存取（LDX/STX）：
      │     檢查目前暫存器的 range 是否完全落在合法邊界內
      │     （例如存取 map value 時，offset 要落在這個 map 的 value_size 範圍）
      │     ──▶ 任何一條路徑上這個檢查沒過，整個程式被拒絕
      │
      └─ 遇到已經驗證過的等價狀態 → 剪枝，不重複分析（path pruning）
      │
      ▼
全部路徑都通過 ──▶ 程式被接受，JIT 編譯成原生碼（或用 interpreter），真正能被執行
```

這張圖裡每一個「檢查」都是潛在的 bug 點：range 追蹤錯了，邊界檢查用的就是錯的數字；路徑等價判斷錯了，剪枝可能跳過真正該檢查的路徑。verifier 的正確性 = 這整張圖上**所有**檢查都做對,一個環節漏了,後面全部白做。

## 物件模型與關鍵資料結構

| 概念 | struct / 介面 | 說明 |
|---|---|---|
| eBPF 程式 | `struct bpf_prog` | 載入後的程式本體，包含 bytecode、JIT 編譯後的原生碼指標、程式類型（`BPF_PROG_TYPE_*`：socket filter、kprobe、XDP、cgroup……決定能呼叫哪些 helper、能存取哪些 context） |
| 驗證期的暫存器狀態 | `struct bpf_reg_state` | verifier 走訪時，每個暫存器的抽象狀態：`type`（純數值、指向 map value 的指標、指向 context 的指標……）、`umin_value`/`umax_value`/`smin_value`/`smax_value`（64 位元範圍）、`u32_min_value`/`u32_max_value`（32 位元範圍，ALU32 操作用） |
| 驗證期的整體狀態 | `struct bpf_verifier_state` | 某個時間點所有暫存器 + stack 的狀態快照，path pruning 比較的就是這個快照是否和之前見過的「等價」 |
| map | `struct bpf_map` + 具體後端（`array`/`hash`/`lru_hash`/`prog_array`/`ringbuf`……） | eBPF 程式和使用者態共享資料的主要機制；也是 eBPF 程式之間共享資料的機制（例如 XDP 程式寫，另一個 tracing 程式讀） |
| helper 函式 | `BPF_CALL_*` 系列（`bpf_map_lookup_elem`、`bpf_probe_read`……） | eBPF 程式唯一合法呼叫外部功能的方式；verifier 對每個 helper 的參數型別、回傳值型別都有專門的檢查規則 |
| JIT | 架構相關的 JIT backend（x86-64：`arch/x86/net/bpf_jit_comp.c`） | 把驗證通過的 bytecode 編譯成原生機器碼，執行時不再經過 interpreter；JIT 本身的 bug（不是 verifier 邏輯錯，而是「verifier 判斷安全，但 JIT 生成的原生碼行為和 bytecode 語意不一致」）是另一個獨立的 bug class，本章聚焦 verifier 本身 |

讀 source 的入口：`kernel/bpf/verifier.c`（整個驗證邏輯，`do_check()` 是主迴圈）、`kernel/bpf/core.c`（interpreter）、`arch/x86/net/bpf_jit_comp.c`（x86-64 JIT）。

### 常見 map 型別與各自的風險特徵

| map 型別 | 用途 | 風險特徵 |
|---|---|---|
| `BPF_MAP_TYPE_ARRAY` | 固定長度陣列，index 存取 | 結構最簡單，bug 史相對少；邊界檢查邏輯直觀 |
| `BPF_MAP_TYPE_HASH` | 一般 hash table | key/value 的記憶體管理較複雜，`update`/`delete` 的並發處理歷史上出過 race 類 bug |
| `BPF_MAP_TYPE_LRU_HASH` | 帶自動淘汰（LRU）的 hash table | 淘汰機制本身的並發邏輯，是 `HASH` 之外額外的風險面 |
| `BPF_MAP_TYPE_PROG_ARRAY` | 存放其他 eBPF 程式的 fd，供 `bpf_tail_call` 用 | 讓程式可以「跳到另一個程式」，這類間接呼叫結構歷史上是驗證邏輯的重點照顧對象（尾呼叫深度限制、型別相容性檢查） |
| `BPF_MAP_TYPE_RINGBUF` | 使用者態與 eBPF 程式共享的環狀 buffer，常用於傳遞 event | 和 io_uring 的 ring 設計理念類似（[Ch 31](./31-io-uring.md)），共享記憶體的生產者/消費者協調邏輯是風險點 |

這張表的用途是:當你讀到一個新的 eBPF CVE，先確認它牽涉哪個 map 型別，再對照這裡的風險特徵,判斷它比較接近「單純的邊界計算錯誤」還是「更複雜的並發/間接呼叫問題」。

## 典型 bug 類型與成因：兩個公開 CVE

### CVE-2021-3490：ALU32 位元運算的範圍追蹤沒跟上

- **背景**：eBPF 的 verifier 對每個暫存器同時維護 64 位元（ALU64）和低 32 位元（ALU32）兩套數值範圍。`3f50f132d840`（"bpf: Verifier, do explicit ALU32 bounds tracking"，5.7-rc1）引入了針對 32 位元運算路徑的獨立範圍追蹤，讓 verifier 能更精確地分析那些明確操作 32 位元子暫存器的程式。
- **root cause**：NVD 的描述指出，這套 ALU32 範圍追蹤在處理**位元運算**（AND、OR，後來發現 XOR 也有相同問題）時沒有正確更新。具體來說，當一段程式對一個暫存器做 32 位元的 AND/OR/XOR，verifier 更新了這個暫存器的「一般」範圍（概念上對應完整的語意），但**沒有同步更新 32 位元子範圍的追蹤值**，導致 verifier 後續對這個暫存器的 32 位元範圍認知,和這個暫存器實際可能的值不一致。AND 和 OR 的問題由前述 5.7-rc1 的改動本身帶入，XOR 的對應問題由另一個稍後的提交（`2921c90d4718`，"bpf: Fix a verifier failure with xor"，5.10-rc1）引入——兩者後來由同一個修補一起處理。
- **後果**：當 verifier 對某個暫存器「以為」它的範圍比實際上窄，後續程式碼若用這個暫存器做記憶體存取的邊界計算（例如 `offset = reg & mask` 這類模式），verifier 用錯誤（過窄）的範圍去驗證邊界檢查通過，但 runtime 執行時這個暫存器的實際值可能超出 verifier 認可的範圍——結果是**可控的 OOB 讀寫**。
- **fix**：上游 `049c4e13714e`（"bpf: Fix alu32 const subreg bound tracking on bitwise operations"）修正了這套範圍更新邏輯，讓 32 位元子範圍在位元運算後正確同步。這個修補被回溯到 5.10.37、5.11.21、5.12.4 等多條 stable 分支。
- **危害評估**：Red Hat 的描述明確指出「a local user with the ability to insert eBPF instructions could use this flaw to crash the system or possibly escalate their privileges」——而「插入 eBPF 指令的能力」是否可達，正是取決於 `unprivileged_bpf_disabled` 的設定（見下文）。

用偽碼示意這個模式（不是任何版本的原文，用來說明「兩套範圍追蹤不同步」這個抽象問題）：

```c
/* 偽碼：示意 ALU32 bounds tracking 的同步問題 */
static void adjust_reg_bounds_on_and(struct bpf_reg_state *reg, u64 mask)
{
        /* 64 位元範圍有正確更新 */
        reg->umax_value &= mask;

        /* 漏更新：32 位元子範圍（u32_min_value/u32_max_value）理應
         * 也跟著這次 AND 運算收斂，但程式碼在某條路徑上忘了同步，
         * 讓 verifier 後續用的 32 位元範圍比實際寬，邊界檢查因此算錯 */
}
```

### CVE-2023-2163：path pruning 把不安全的路徑誤判為安全

- **背景**：verifier 為了避免每條分支組合都重新從頭分析（分支一多，路徑數量會指數級成長），用路徑剪枝：如果目前要分析的狀態，和之前已經驗證過的某個狀態「足夠等價」（通常指暫存器的型別與範圍是之前狀態的子集），就直接判定「這條路徑也安全」，不重新完整分析。
- **root cause**：Google 的公開 PoC 說明標題是「Path pruning gone wrong」——verifier 的分支剪枝邏輯在某種情況下，把兩個**實際上不等價**的狀態誤判為等價，導致一條實際上會造成不安全記憶體存取的路徑，被直接標記為「已驗證過、安全」而跳過完整分析。**本次 WebSearch 沒有取得這個等價判斷具體在哪個函式、哪個條件上出錯的細節**（Google 的深度分析部落格文章應該有完整機制，請讀者自行查閱該文章取得精確機制，見延伸閱讀）。我們能確定的是：這屬於「剪枝優化的正確性假設被打破」這一類,和 CVE-2021-3490「範圍追蹤的更新邏輯沒做對」是同一個大類（verifier 內部狀態追蹤錯誤）下的**不同子類型**——一個錯在「追蹤的值本身算錯」，一個錯在「判斷兩個追蹤值是否等價算錯」。
- **影響範圍**：OSV 記錄受影響版本從 5.3 起，修補版本包括 5.4.242、5.10.179、5.15.109 等多條 stable 分支;上游修補 commit 為 `71b547f561247897a0a14f3082730156c0533fed`。NVD 給出的 CVSS 3.1 分數是 8.8，多個來源一致描述其後果為「可達成任意 kernel 記憶體讀寫，進而本地提權或容器逃逸」。

### 一個更直覺的範圍追蹤例子

用一段貼近 C 語意的偽碼（不是真實 bytecode，只是幫助建立直覺）示意「為什麼 ALU32/ALU64 不同步會造成邊界檢查失效」：

```c
/* 概念示意：一段 eBPF 程式對映射到的邏輯 */
u64 reg;                      /* 64 位暫存器 */
reg = bpf_get_some_value();   /* verifier 推斷：reg 的 64 位範圍是 [0, 0xffffffff] */

reg32 = (u32)reg & 0xf;       /* 做一次 32 位元的 AND：
                                * 正確結果：reg 的低 32 位元範圍應收斂成 [0, 0xf]
                                * 但如果 verifier 的 32 位元範圍追蹤沒跟上這次 AND,
                                * 它仍然認為 reg32 的範圍是舊的、更寬的值 */

array[reg32] = value;         /* 邊界檢查用的是 verifier「以為」的範圍（可能是舊的、更寬的）
                                * 如果這個範圍比陣列大小還寬，檢查就會錯誤地放行,
                                * 但 runtime 執行時 reg32 的實際值如果能被攻擊者間接影響到
                                * 超出陣列大小的值，就是可控的 OOB 存取 */
```

這個例子的重點不是「這段程式實際上長怎樣」（真實觸發 CVE-2021-3490 的 bytecode 構造遠比這複雜），而是建立一個心智模型：**verifier 的每一次範圍更新都要跟上程式實際做的每一次運算，少跟上一次，後面所有基於這個範圍的邊界檢查都可能是錯的**。

### 兩案例對照

| CVE | 錯誤發生的層次 | 根本原因類型 | 對 Ch 10 分類 |
|---|---|---|---|
| CVE-2021-3490 | 單一暫存器的範圍追蹤更新 | ALU32/ALU64 兩套追蹤不同步 | integer（verifier 層級的範圍計算錯誤） |
| CVE-2023-2163 | 路徑之間的等價判斷（剪枝） | 剪枝優化的正確性假設被打破 | 邏輯 bug（verifier 層級，非傳統記憶體操作錯誤） |

共同模式：**這兩個 bug 都不是「程式碼忘記做某個檢查」，而是「verifier 內部用來代表『這段程式安全』的抽象模型，和程式實際的 runtime 語意不一致」**。這是 eBPF 和前兩章最根本的差異——nf_tables、io_uring 的 bug 是「物件的真實生命週期管理錯了」，eBPF 的 bug 是「對程式行為的**預測**錯了」，而這個預測一旦錯，kernel 會基於這個錯誤預測直接執行使用者給的任意程式碼。

## 利用思路（概念，不寫 turnkey）

verifier bypass 類 bug 的利用路徑和前兩章的 UAF/OOB 升級路徑有本質差異，概念上分三步：

1. **構造觸發 bug 的 eBPF 程式**：目標是寫一段 bytecode，讓 verifier 的範圍追蹤或路徑等價判斷在某個特定運算序列上出錯，使 verifier 相信某個暫存器的範圍比實際可能的值窄（或某條路徑比實際更安全）。這一步通常需要對著 verifier 原始碼逐步構造觸發條件，不是靠黑箱 fuzz 就能輕易找到——雖然 Syzkaller 配合專門的 eBPF bytecode 生成器也確實挖出過不少這類 bug（見 [Ch 43](./43-syzkaller.md)）。
2. **把範圍誤判轉成可控的記憶體存取**：一旦 verifier 誤判通過，這段程式載入後就能真正執行——執行時,用那個「被誤判範圍」的暫存器做 map 存取的 offset,就能存取到 verifier 原本該擋住、但實際上超出 map value 邊界的記憶體。這一步得到的是一個**可以自己控制偏移量、讀寫範圍相對精確**的原語,比典型的 heap UAF 更接近「程式自己定義的任意讀寫」,不需要像 [Ch 19](./19-spray-objects.md) 那樣去 spray 別的物件佔位——因為你操作的就是你自己註冊的 `bpf_map`,它的記憶體本來就在你的控制範圍附近。
3. **升級為任意讀寫 + 提權**：有了相對精確的 OOB 讀寫之後，概念上的做法是先用讀取能力做 KASLR leak（讀出 map 物件附近的 kernel 指標），再用寫入能力覆寫 `cred` 或等價的 data-only 目標（[Ch 16](./16-classic-primitives.md)、[Ch 29](./29-data-only-attack.md)）。相較於前兩章需要「觸發 → reclaim → 再觸發」的多輪升級，verifier bypass 給的原語通常一次載入就能重複呼叫、精確控制偏移,穩定性與可重複性往往優於典型的 heap UAF——這也是為什麼業界長期認為 eBPF verifier bug「性價比最高」。

概念骨架：

```c
/* 概念骨架，不是任何特定 CVE 的完整 exploit */
int main(void) {
        /* 1. 建立一個 bpf_map，作為後續 OOB 的操作載體 */
        int map_fd = bpf_create_map(BPF_MAP_TYPE_ARRAY, 4, 8, 1, 0);

        /* 2. 載入一段構造好的 bytecode，觸發特定 CVE 的 verifier 誤判
         *    （bytecode 本身需要針對目標 CVE 精確構造，這裡用 load_crafted_bytecode()
         *     代表這個高度特化、不通用的步驟） */
        struct bpf_insn insns[] = { /* ... 針對目標 verifier bug 構造的指令序列 ... */ };
        int prog_fd = load_crafted_bytecode(insns, map_fd);

        /* 3. 透過某個觸發點（socket filter / tracepoint / XDP，依 prog type 而定）
         *    真正執行這段程式，利用被誤判的範圍做越界讀寫 */
        unsigned long leaked_ptr = trigger_oob_read(prog_fd, map_fd);

        /* 4. 用 leak 到的 kernel 指標計算 KASLR base，定位 data-only 目標 */
        unsigned long kbase = leaked_ptr - KNOWN_OFFSET;

        /* 5. 用 OOB 寫覆寫 cred 或等價目標 */
        trigger_oob_write(prog_fd, map_fd, kbase);

        if (getuid() == 0)
                system("/bin/sh");
        return 0;
}
```

`load_crafted_bytecode()`、`trigger_oob_read()`/`trigger_oob_write()` 的具體內容完全依賴你選定的 CVE 與目標 kernel 版本——這正是 verifier bypass 類利用「觸發條件極度特化」的特徵，不存在通用骨架能填的「中間步驟」。

## 防禦面：`unprivileged_bpf_disabled`

- **`kernel.unprivileged_bpf_disabled` sysctl**：控制非特權使用者（沒有 `CAP_BPF`/`CAP_SYS_ADMIN`）是否能呼叫 `bpf(2)`。三個值：`0` 允許、`1` 禁止且無法再開回來（one-way）、`2` 禁止但可由管理員重新開啟。
- **`CONFIG_BPF_UNPRIV_DEFAULT_OFF`**：Daniel Borkmann 在 2021 年提出的 Kconfig 選項，編譯進這個選項的 kernel，`unprivileged_bpf_disabled` 的**初始值就是 `2`**（而不是預設的 `0`）——也就是從開機那一刻起，非特權使用者就不能用 eBPF，除非管理員主動改回 `0`。
- **各發行版的實際狀況**：Ubuntu 的 kernel config 啟用了這個選項（設為 `2`，保留管理員重新開啟的彈性）；查證時能確認的 Red Hat 說明也提到「RHEL 的預設 kernel 設定會阻止非特權使用者使用 eBPF」。但這**不是全部發行版的通用預設**——評估一個 eBPF CVE 的實際危害前，務必先確認目標環境的 `sysctl kernel.unprivileged_bpf_disabled` 實際數值，不要假設。
- **和 kernelCTF Hardened target 的對照**（見 [Ch 49](./49-kernelctf-overview.md)）：查證時的 Hardened target 預設 `BPF_UNPRIV_DEFAULT_OFF=y`，和本章這個機制一致——這是「關閉非特權可達性」而不是「移除 eBPF 整個功能」,因為 eBPF 對合法的系統監控、網路加速（XDP）、安全工具（Falco、Cilium）有核心價值,不能像 io_uring 那樣直接整個關掉。

| 防禦層 | 具體做法 | 擋得住什麼 | 擋不住什麼 |
|---|---|---|---|
| `unprivileged_bpf_disabled=2`（或 `CONFIG_BPF_UNPRIV_DEFAULT_OFF`） | 禁止非特權使用者載入 eBPF 程式 | 一般使用者層級的 verifier bypass 攻擊 | 已有 `CAP_BPF`/`CAP_SYS_ADMIN` 的服務（容器內某些監控 agent 常需要）；這些服務若被攻破，攻擊者仍能摸到 eBPF |
| LSM 層收斂特定 `CAP_BPF` 授予範圍 | SELinux/AppArmor 規則限制哪些 binary 能用 eBPF | 把可達性收斂到白名單 | 設定維護成本，容易隨系統演進出現漏洞 |
| 持續 fuzz verifier 本身 | Syzkaller 配合 eBPF 專用生成器（見 [Ch 43](./43-syzkaller.md)） | 找出下一個 bug，縮短曝險窗口 | 不是預防性防禦，只是加速發現 |
| KCFI/完整性檢查對 JIT 生成碼 | 確保 JIT 生成的原生碼沒有被額外篡改 | JIT 輸出被竄改的風險 | 不解決 verifier 邏輯本身的誤判問題 |

## 對比取捨表

| bug 類型 | 利用穩定性 | 觸發難度 | 現狀 |
|---|---|---|---|
| ALU32/ALU64 範圍追蹤不同步（CVE-2021-3490 類型） | 高（一旦找到觸發序列，原語精確可重複） | 中—需要理解 verifier 的範圍追蹤機制，但不需要複雜的 race | 持續有新變種被發現 |
| 路徑剪枝誤判（CVE-2023-2163 類型） | 高 | 高—需要理解剪枝的等價判斷邏輯，構造難度更大 | 相對少見但影響範圍大 |
| JIT 輸出與 bytecode 語意不一致 | 視具體情況 | 高—需要理解特定架構的 JIT 實作細節 | 另一個獨立的 bug class，本章未深入 |
| 傳統 UAF/heap 類（對照 Ch 30/31） | 中—需要 spray/reclaim 升級 | 中—但不需要理解靜態分析理論 | 更常見，但原語精確度通常較低 |

## 踩雷集錦

1. **「eBPF 的風險等於 map 型漏洞」**：不準確。map 的實作本身（各種 map type 的 insert/lookup/delete）確實也有獨立的 bug 史，但**verifier bypass 的影響遠大於單一 map 型漏洞**——後者通常還是落在傳統記憶體安全 bug 的範疇（UAF/OOB），前者直接讓攻擊者的任意程式碼被信任執行。
2. **「只要 CAP_BPF 存在，就等於開了後門」**：不完全對。`CAP_BPF`（較新版本把一部分權限從 `CAP_SYS_ADMIN` 拆出來的獨立 capability）讓程式能呼叫 `bpf(2)`，但**仍然要經過 verifier**——`CAP_BPF` 本身不等於繞過驗證，它只是「可達性」的門檻，真正的風險來自 verifier 本身是否有 bug。
3. **「verifier 一旦通過，程式就絕對安全」**：這正是整章的反例——verifier 是近似的靜態分析，不是形式化證明。業界對「verifier 永遠正確」沒有共識，持續的 fuzz 與修補正是承認這點的實踐。
4. **「JIT 和 verifier 是同一回事」**：錯。verifier 決定「這段 bytecode 安全嗎」，JIT 決定「怎麼把這段 bytecode 翻譯成原生機器碼」。兩者都可能有獨立的 bug——「verifier 誤判」和「JIT 翻譯錯誤導致行為偏離 bytecode 語意」是兩個不同的 bug class,不要混在一起討論。
5. **「`unprivileged_bpf_disabled` 在所有發行版都預設關閉非特權存取」**：錯。這點因發行版而異，有些發行版（尤其較舊的或桌面導向的）仍預設 `0`（允許）。評估風險前務必查證目標系統的實際值。
6. **「verifier bug 一定比 heap UAF 好打」**：方向對但不是絕對。verifier bypass 的**觸發條件構造**往往需要深入理解 verifier 的內部演算法,門檻比找一個典型的 UAF 高得多——「原語品質更好」和「找到 bug 的難度更低」是兩個獨立的維度,不要混為一談。
7. **「JIT spray 和 verifier bypass 是同一招」**：不是。JIT spray 是另一條獨立技巧——利用 JIT 把 eBPF bytecode 編譯成原生碼時,原生碼裡可能包含可被利用的 immediate value 序列（可以拼成 shellcode 的 byte pattern），這是「利用 JIT 輸出的副作用」,和「verifier 誤判通過一段惡意邏輯」是不同的攻擊面,雖然兩者都牽涉 eBPF 子系統。

## 進階：再往深一層

- **讀 Linux Foundation 委託的 eBPF Verifier 安全稽核報告**：這份報告系統性檢視了 verifier 的已知風險面與歷史 bug 模式，是比單篇 CVE 分析更全面的視角，見延伸閱讀。
- **Spectre 類側通道與 eBPF 的交集**：eBPF 程式能被用來構造精確的計時側通道攻擊（讀取 cache 命中/未命中的時間差），這和本章講的 verifier 邏輯 bug 是獨立的另一條風險線——verifier 額外有一套針對 speculative execution 的防護（如插入 speculation barrier），值得在學完側通道攻擊的背景知識後回來研究。
- **eBPF 在不同 `BPF_PROG_TYPE` 下的攻擊面差異**：XDP、tracing（kprobe/tracepoint）、cgroup、socket filter 各自能存取的 context、能呼叫的 helper 集合不同，風險面也不同——tracing 類程式因為能存取更多 kernel 內部資料（`bpf_probe_read` 之類），historically 是 info leak 的重點。
- **從防禦角度量化風險**：如果你要寫一份 hardening 評估報告，先確認目標系統的 `unprivileged_bpf_disabled` 實際值，再評估「有 `CAP_BPF` 的服務清單」這個更小、更可控的攻擊面,而不是籠統地說「eBPF 有風險」。
- **JIT spray 作為獨立的進階方向**：如果你對「利用 JIT 輸出的副作用」這條線感興趣（和本章 verifier bypass 是不同的攻擊面，見踩雷集錦第 7 條），可以從 x86-64 的 JIT 編譯輸出開始研究，理解哪些 eBPF 指令會被編譯成包含攻擊者可控 immediate value 的機器碼序列。
- **形式化驗證方向**：部分研究嘗試用形式化方法（SMT solver 輔助）重新驗證 verifier 本身的正確性，試圖從根源解決「近似分析可能錯」的問題——這是比持續 fuzz 更徹底，但工程成本也高得多的方向，值得作為這整個主題的終點延伸。

## 動手練習

> 以下操作在**你自己擁有的 Linux VM** 內進行，涉及 kernel 原始碼閱讀的部分**未實測**，為理論預期行為；驗證方式見各步驟說明。

1. **讀修補 CVE-2021-3490 的 commit**：找 `049c4e13714e`（"bpf: Fix alu32 const subreg bound tracking on bitwise operations"），逐行比對修補前後的 `kernel/bpf/verifier.c`，確認本章的偽碼描述是否與實際邏輯一致。
2. **讀 Google 對 CVE-2023-2163 的深度分析部落格文章**（NVD 條目連結的那篇），記下 path pruning 誤判的精確機制，補上本章承認「沒有查到細節」的那一段。
3. **驗證 `unprivileged_bpf_disabled` 的行為**：在 VM 裡用非特權使用者嘗試 `bpf(BPF_PROG_LOAD, ...)`（可以用 `bpftool` 或最小 C 程式呼叫），記錄你的 kernel 預設值,再用 `sysctl kernel.unprivileged_bpf_disabled=2` 切換後重測。
4. **用 `bpftool prog dump xlated` 觀察一段簡單程式的驗證結果**：寫一個最小的 eBPF 程式（可以用 `clang -target bpf` 編譯一段 C），載入後用 `bpftool` 檢視 verifier 標註的暫存器型別與範圍，建立「verifier 實際在追蹤什麼」的具體印象。

## 本章重點整理

- eBPF 的礦區本質和前兩章完全不同：不是資料結構生命週期管理，而是**單一驗證器（verifier）的正確性直接等於整個子系統的安全性**。
- 兩個代表 CVE 分屬 verifier 內部狀態追蹤的兩個子類型：範圍追蹤本身算錯（CVE-2021-3490，ALU32/ALU64 不同步）、路徑等價判斷算錯（CVE-2023-2163，剪枝誤判）。
- verifier bypass 給出的原語通常比傳統 UAF 更精確、更穩定、更不需要 spray/reclaim——這是業界認為它「性價比最高」的原因。
- 防禦的結構性解法同樣是攻擊面縮減（`unprivileged_bpf_disabled`），但因為 eBPF 對合法用途（監控、XDP、安全工具）的核心價值,不能像 io_uring 一樣直接整個關掉,只能收斂到「誰能用」。

## 自我檢核

- [ ] 不看筆記，能解釋「為什麼 verifier bug 比典型 heap UAF 給出的原語更強」。
- [ ] 能畫出 verifier 走訪一段程式的流程，標出範圍追蹤與路徑剪枝各自在哪個環節可能出錯。
- [ ] 能用一句話分別講出 CVE-2021-3490 與 CVE-2023-2163 的根本原因，並說出兩者同屬「verifier 內部狀態追蹤錯誤」但是不同子類型。
- [ ] 面試官問「為什麼 eBPF 不能像 io_uring 一樣直接整個關掉」，你能從合法用途的核心價值角度回答。
- [ ] 能解釋 `CAP_BPF` 存在本身不等於繞過了 verifier。

## 延伸閱讀

### 漏洞分析（第一手或接近第一手來源）

- **[Red Hat Bugzilla 1959556 — CVE-2021-3490](https://bugzilla.redhat.com/show_bug.cgi?id=1959556)**
  - **讀哪裡**：描述欄位與 "Fixes" 引用的 commit 列表。
  - **和本章的關聯**：本章 CVE-2021-3490 root cause 分析的主要來源。
- **[Google 深度分析 — A deep dive into CVE-2023-2163](https://security.googleblog.com/)**（從 NVD CVE-2023-2163 條目連結）
  - **讀哪裡**：全篇，重點在路徑剪枝誤判的精確機制。
  - **和本章的關聯**：補上本章承認未能查到細節的部分；是本章「進階延伸」第一項的對象。
  - **前提**：需要先讀過 `kernel/bpf/verifier.c` 的 path pruning 相關函式（`states_equal()` 之類）才能完全跟上。
- **[google.github.io PoC — CVE-2023-2163](https://google.github.io/security-research/pocs/linux/cve-2023-2163)**
  - **讀哪裡**：PoC 的構造說明（標題「Path pruning gone wrong」本身就是對這個 bug 最精簡的概括）。
  - **學什麼**：Google 內部安全團隊挖掘與驗證 verifier bug 的流程。

### 官方文件 / 原始碼

- **[`kernel/bpf/verifier.c`（Bootlin Elixir, Linux 6.6）](https://elixir.bootlin.com/linux/v6.6/source/kernel/bpf/verifier.c)**
  - **讀哪裡**：`do_check()`（主驗證迴圈）、`adjust_reg_min_max_vals()`（ALU 運算的範圍更新）、`states_equal()`（路徑剪枝的等價判斷）。
  - **學什麼**：親眼看範圍追蹤與路徑剪枝的實際程式碼結構，建立「這類 bug 長什麼樣」的直覺。
- **[eBPF Verifier Security Audit（Linux Foundation / Trail of Bits）](https://www.linuxfoundation.org/hubfs/eBPF/eBPF%20Verifier%20Security%20Audit.pdf)**
  - **讀哪裡**：全篇，特別是對 bounds tracking 與 path pruning 這兩個子系統的風險評估章節。
  - **為什麼值得讀**：這是一份系統性的第三方安全稽核報告，比單篇 CVE 分析更能建立「verifier 整體風險面長什麼樣」的全貌,適合在讀完本章兩個案例後，用它驗證本章的論點是否和專業稽核的結論一致。
  - **前提**：需要先懂基本的靜態分析概念（抽象解釋、範圍分析）會更容易讀懂。

eBPF 展示了第三種礦區成因——單一驗證器的正確性風險。Part 5 剩下的章節會繼續看其他子系統的攻擊面，但本書三大礦區（nf_tables、io_uring、eBPF）到此講完；下一章回到更底層、更基礎的 socket/skb 家族，看看 AF_PACKET、AF_UNIX 這些「所有網路子系統共用的底層物件」本身又是怎麼一回事。

→ [Ch 33 socket / skb 家族：AF_PACKET、AF_UNIX、pgv、各種 skb](./33-socket-skb.md)
