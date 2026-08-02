# Ch 29 — Nyx / kAFL（深挖章）

> **目標：** 從架構層理解 Nyx 和 kAFL 如何把 hypervisor snapshot + Intel PT coverage 組合成一套能打任意目標的 greybox fuzzer——以及它的每個設計選擇解決了什麼具體問題。
>
> **環境：** 本章需要 VT-x + Intel PT 硬體支援，多數 WSL2 / 雲端環境不具備（WSL2 的 nested virtualization 通常不暴露 Intel PT）。架構分析為主，標注「**[本段未實測，為理論預期行為]**」的部分說明有硬體時的驗證方法。能用標準工具觀察的部分一律實測。

---

## 為什麼 Nyx 誕生

2020 年之前，打 hypervisor 和 kernel 的典型方法是：

- **syzkaller 風格**：VM 裡跑 target，crash 就重啟 VM。VM 重啟 ~5–30 秒，exec/s 個位數，打 hypervisor 本身更難（hypervisor 是 VM 的外層，crash 了整個 host 就掛了）
- **AFL + QEMU user mode**：只能打用戶空間 binary，不能打 kernel 或 hypervisor
- **手工 harness**：針對特定 device model 寫 ioctl 序列，沒有 coverage 引導

Nyx（USENIX Security 2021，Schumilo et al.）的目標是：打 QEMU 這類 hypervisor 的 device emulation 程式碼，同時：
1. 有 coverage feedback（greybox）
2. 速度夠快（解決 VM 重啟慢）
3. 能打 closed binary（不需要原始碼插樁）

答案是：把 fuzzer 本身放在 hypervisor 層，用 **VM snapshot** 解決 reset 問題，用 **Intel PT** 解決 coverage 問題。

---

## Nyx 整體架構圖

```
┌─────────────────────────────────────────────────────────────┐
│                      Host (Linux)                           │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐   │
│  │               QEMU/KVM (modified)                   │   │
│  │  ┌────────────────────────────────────────────┐    │   │
│  │  │              Guest VM                      │    │   │
│  │  │                                            │    │   │
│  │  │  ┌─────────────┐   ┌──────────────────┐  │    │   │
│  │  │  │  Target     │   │  Nyx Agent       │  │    │   │
│  │  │  │  (被 fuzz   │   │  （guest 端 lib） │  │    │   │
│  │  │  │   的程序/   │   │  - 接收輸入      │  │    │   │
│  │  │  │   kernel）  │◄──│  - 注入 target   │  │    │   │
│  │  │  └─────────────┘   │  - 回報結果      │  │    │   │
│  │  │                    └──────┬───────────┘  │    │   │
│  │  └───────────────────────────┼──────────────┘    │   │
│  │                              │ hypercall          │   │
│  │  ┌───────────────────────────▼──────────────┐    │   │
│  │  │         Nyx Hypervisor Layer              │    │   │
│  │  │  - snapshot take/restore                  │    │   │
│  │  │  - dirty page tracking                   │    │   │
│  │  │  - Intel PT decode                       │    │   │
│  │  │  - shared memory（輸入/coverage bitmap） │    │   │
│  │  └───────────────────────────────────────────┘    │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                             │
│  ┌─────────────────────┐                                   │
│  │   Nyx Fuzzer        │   ← LibAFL 或 afl++ 為後端        │
│  │  - 變異輸入          │                                   │
│  │  - 讀 coverage bitmap│                                   │
│  │  - 管理 corpus      │                                   │
│  └─────────────────────┘                                   │
└─────────────────────────────────────────────────────────────┘
```

關鍵：**fuzzer 的決策邏輯在 host**，**target 的執行在 guest**，**兩者透過 hypercall + 共享記憶體通信**。這讓 fuzzer 能完全控制 guest，包含：暫停執行、取快照、還原快照、注入任意記憶體內容。

---

## kAFL：Nyx 的前身

kAFL（USENIX Security 2017，Schumilo et al.）是 Nyx 的前身，也是「Intel PT + KVM snapshot」這個組合的首次正式發表。主要差異：

| 面向 | kAFL | Nyx |
|------|------|-----|
| 主要目標 | Linux/Windows/macOS kernel | Hypervisor、任意閉源 binary |
| Coverage 取得 | Intel PT（相同） | Intel PT（相同） |
| 輸入注入 | 透過 hypercall + 共享記憶體 | 同，加入 Affine Types 規範 |
| 前端 | 自訂（類 AFL） | LibAFL 或 afl++ |
| Snapshot 範圍 | Guest kernel 狀態 | 整個 VM（可選 partial） |

Nyx 是 kAFL 的架構演進，加入了 Affine Types 系統（後述）和更靈活的目標支援。現代使用通常指 **kAFL/Nyx 框架**，兩者在實作上已合併。

---

## 核心組件一：VM Snapshot（Fast Reset）

**[本段未實測，為理論預期行為。有 KVM host 時的驗證方法見本段末。]**

Nyx 的 snapshot 機制建立在 KVM 的 `KVM_GET_DIRTY_LOG` 和 memslot dirty tracking 上：

```
步驟一：建立初始快照
  1. 讓 guest boot 到「準備接受輸入」的狀態
  2. hypercall NYX_ACQUIRE_SNAPSHOT
  3. host 端：保存所有 vCPU 暫存器狀態（KVM_GET_REGS）
  4. host 端：保存所有 guest 記憶體頁面的雜湊或完整副本
  5. 啟動 dirty page tracking（KVM memslot write-protect）

步驟二：執行一個輸入
  1. host 把輸入寫入 shared memory
  2. agent 讀取、注入 target
  3. Intel PT 開始記錄執行流
  4. target 執行直到 exit/crash/timeout
  5. Intel PT 停止，decode → coverage bitmap

步驟三：快速 reset
  1. 讀取 dirty page log（哪些頁被 guest 寫過）
  2. 只把 dirty pages 從快照還原（mmap + memcpy）
  3. 還原 vCPU 暫存器
  4. 清除 dirty page log
  5. 回到步驟二
```

Reset 時間：取決於 dirty pages 數量。對「每個輸入寫動幾十頁記憶體」的典型 target，reset 在幾毫秒以內。

**有 KVM host 時的驗證方法**（需要裸金屬 Linux，不能是 WSL2）：
```bash
# 確認 KVM 和 VT-x 可用
ls /dev/kvm
cat /proc/cpuinfo | grep vmx   # Intel VT-x
# 確認 Intel PT 可用
cat /proc/cpuinfo | grep intel_pt
# 安裝 kAFL/Nyx（需要打過 patch 的 QEMU）
git clone https://github.com/IntelLabs/kAFL
cd kAFL && python3 kAFL.py --help
```

---

## 核心組件二：Intel PT Coverage

Intel PT（Processor Trace）是 Intel 從 Broadwell（2015）開始加入的硬體 feature，在 CPU 執行期間自動記錄執行流，不需要軟體插樁。

**[本節 Intel PT 解碼細節未實測，為理論預期行為。能用 perf 觀察 PT 的部分見 Ch 30。]**

Nyx 使用 Intel PT 的方式：

```
Intel PT 硬體
  │ 記錄每個分支決定（taken/not taken）
  ▼
壓縮的 PT packet stream（寫入 PT buffer，通常 4MB）
  │
  ▼
libipt（Intel 官方解碼庫）
  │ 把 packet stream + binary image → 執行流 trace
  ▼
(src_addr, dst_addr) 邊列表
  │
  ▼
Edge coverage bitmap（AFL 格式，8KB）
  │  bitmap[(src >> 4 XOR dst >> 4) & 0xFFFF]++
  ▼
Fuzzer 前端讀取 bitmap，計算新 coverage
```

為什麼選 Intel PT 而不是 QEMU TCG（binary translation）插樁？

- **速度**：PT 是硬體，overhead ~5%；TCG 插樁 overhead ~30–50%
- **無插樁**：不需要修改 target binary，可打 closed source
- **完整追蹤**：包含 kernel mode 的執行（如果啟用），能同時追蹤 userland + kernel

---

## 核心組件三：Nyx Agent

Agent 是執行在 **guest 內部**的小型 library，負責：

1. **接收輸入**：透過 hypercall 告知 host「我準備好了」，host 把輸入寫入 shared memory
2. **注入 target**：根據 target 類型，以適當方式把輸入送進 target（呼叫函數、寫入 fd、模擬設備請求）
3. **回報結果**：觸發 crash 時透過 hypercall 通知 host；正常完成時也通知

Agent 的存在讓 Nyx 能打各種 target 而不需要修改 fuzzer 核心——你只需要為新目標寫一個 Agent。

對 kernel fuzzing，Agent 直接呼叫 syscall；對 device fuzzing，Agent 執行 MMIO 讀寫序列；對用戶空間 binary，Agent 呼叫 target 函數。

---

## Affine Types：規範 input 注入

這是 Nyx 論文最有趣的設計之一，也最常被忽略。

問題：fuzzer 產生的 input 是一個 byte sequence，但 target 接受的可能是「一系列 MMIO 操作」或「一個有結構的系統呼叫序列」。你需要一個方式描述「如何把 bytes 轉換成 target 的操作序列」，而且這個轉換必須：

- 有確定性（相同 bytes → 相同操作序列）
- 讓 fuzzer 能理性地變異（位元翻轉要有意義）
- 不能讓 target 收到格式錯誤的操作

Nyx 的解法是 **Affine Types spec**：用一個描述語言定義 input 的結構，以及如何把 input 的各個 field 對映到 target 的操作。「Affine」的意思是每個 input 欄位只被消費一次（類似 Rust 的所有權語意），防止雙重注入之類的非預期行為。

**[Affine Types spec 的具體語法未實測，依 Nyx 論文 Section 4.2 的描述。]**

```
# Nyx spec 範例（概念性，非真實語法）
input_spec {
  header: u32,          # 讀 4 bytes，作為操作數量
  ops: header * {       # 重複 header 次
    op_type: u8,        # 操作類型（READ/WRITE/IOCTL）
    addr:    u32,       # 目標地址
    size:    u8,        # 資料長度
    data:    size * u8  # 實際資料
  }
}

# 注入函數：把 bytes 按 spec 解析，執行對應的 MMIO 操作
```

這讓變異引擎能在「有結構的 input 空間」裡操作，而不是盲目翻轉 bytes。

---

## Nyx 的 exec/s：為什麼快

Nyx 論文的 Table 1（打 QEMU 的 e1000 網卡 device model）：

| 方法 | exec/s |
|------|--------|
| 手工 harness + AFL（無 snapshot） | ~50 |
| syzkaller 風格（VM restart） | ~2 |
| Nyx（snapshot + Intel PT） | ~700–2000 |

數量級的差距來自：
1. **Reset 快**：dirty page 還原 < 1ms，而 VM restart ~5s
2. **Coverage 採集快**：Intel PT 硬體 overhead 幾乎可忽略
3. **不需要 target 重啟**：target 的初始化成本只付一次

---

## kAFL/Nyx 的限制

**硬體依賴（最大限制）**：

- 需要 **Intel CPU**，支援 VT-x 和 Intel PT
- 需要 **裸金屬 Linux host** 或支援 nested virtualization 的雲端實例（大多數雲端關閉了 nested virt 或不暴露 PT）
- WSL2 和大部分 VM 環境**無法跑 kAFL/Nyx**

這就是為什麼本章大量標注「未實測」——這不是理解障礙，是硬體現實。

**AMD 支援**：AMD 有類似的 Processor Trace feature（AMD IBS、AMDuProf），但 kAFL/Nyx 主要設計給 Intel PT，AMD 支援有限。

**Coverage 精度**：Intel PT 記錄的是「分支決定」，而不是完整的指令追蹤。在跑非常快的緊密迴圈時，PT buffer 可能溢出，造成 coverage 遺漏。實作上需要定期 flush PT buffer。

**Guest kernel 的除錯支援**：打 kernel 時，你可能需要在 guest 裡安裝自訂 kernel（加入 KASAN、KCOV 等），這增加了 setup 複雜度。

---

## Nyx-Net：網路目標的延伸

Nyx-Net（CCS 2022）是 Nyx 框架的延伸，專門針對網路服務：

- 在 guest 內部模擬網路 client，向 server 發送 fuzz 輸入
- 同樣使用 snapshot reset——在「連線建立後」的狀態存快照，從那裡反覆 fuzz 一個 session 內的請求
- 解決了 stateful 網路服務（Ch 16–20）的 reset 問題

這讓 snapshot 框架能打「需要三次握手才能到達的」服務邏輯，而不是每次都重走握手。

---

## 實際可做的觀察（不需要 Intel PT 硬體）

雖然無法跑完整的 Nyx，有幾個觀察可以在 WSL2 做：

```bash
# 觀察 KVM 的能力（如果 WSL2 有開 nested virt）
ls /dev/kvm 2>/dev/null && echo "KVM available" || echo "no KVM"

# 確認 CPU 有沒有 Intel PT
grep -c intel_pt /proc/cpuinfo 2>/dev/null || echo "no /proc/cpuinfo or no intel_pt"

# 用 perf 看 Intel PT 是否可用（需要裸金屬或 PT-capable VM）
# perf list | grep intel_pt
# 如果輸出 intel_pt，代表硬體可用

# 觀察 kAFL repo 的架構
git clone --depth=1 https://github.com/IntelLabs/kAFL /tmp/kafl-inspect 2>/dev/null
ls /tmp/kafl-inspect/
```

**有 Intel PT 硬體時的完整驗證方法**（非 WSL2，裸金屬 Linux）：

```bash
# 確認 PT 可用
dmesg | grep intel_pt

# 安裝 kAFL 依賴
cd kAFL
python3 -m pip install -r requirements.txt
# 啟動 kAFL 打一個 sample target
python3 kAFL.py fuzz --kernel /path/to/bzImage --target sample_target --input seeds/
# 觀察 exec/s 和 coverage
```

---

## 踩雷

**錯誤直覺一：「Nyx 和 kAFL 是兩個不同的工具，要分開學」**

正確理解：kAFL 是 2017 年的論文工具，Nyx 是 2021 年的演進。IntelLabs 的 kAFL 儲存庫已經整合了 Nyx 的設計，現在對外呈現為同一個框架。區分兩者的名字主要是引用論文時需要，實際使用就是同一套程式碼。

**錯誤直覺二：「Intel PT 給的 coverage 和 afl++ 的 edge bitmap 一樣準確」**

正確理解：Intel PT 給的是「分支記錄」，解碼出來的 edge 集合在 PT buffer 不溢出的情況下是完整的，但 buffer 溢出就會遺漏。afl++ 用軟體插樁在每個 edge 上更新 bitmap，是精確的。Intel PT 的 coverage 在實務上比插樁稍差，但換來的是「不需要插樁、可打 closed binary」的能力——這個 trade-off 在目標有原始碼時不值得，在 closed binary 時沒有替代方案。

**錯誤直覺三：「Nyx 的 Affine Types 是為了 type safety，跟 Rust 的 ownership 無關」**

正確理解：Affine Types 在邏輯上確實來自型別理論的「affine type」（每個值最多使用一次）。Nyx 引入它是為了防止 fuzzer 生成的 input 被 agent 以「消費多次」的方式注入——這確保注入的確定性和安全性，跟 Rust 的所有權語意在數學上同源。

---

## 進階延伸

**LibAFL + Nyx**：LibAFL 0.11+ 有原生的 Nyx executor，讓你用 LibAFL 的 Rust 生態系（自訂 mutator、scheduler）配上 Nyx 的 snapshot engine。這是目前最靈活的組合——用 Ch 4–10 學的 LibAFL 知識，直接銜接 snapshot fuzzing。

**Nyx-Net**（CCS 2022）：把 snapshot 用到網路服務——在「session 建立後」存快照，從那裡 fuzz 一個 session 的多個請求。對 stateful daemon（Ch 16–20 的問題）提供 snapshot 等級的速度。

**WinAFL + snapshot**：WinAFL 有 in-process snapshot 模式（`--drpersist`），概念類似但不用 Intel PT 和 VM，是 Windows 版本的近似解。比 Nyx 慢但不需要硬體支援。

---

## 動手練習

1. 閱讀 kAFL GitHub（https://github.com/IntelLabs/kAFL），找到 `targets/` 目錄，看看現有的 target 是怎麼寫 agent 的（選一個 Linux user-space target，讀 agent 的 hypercall 使用方式）。
2. 閱讀 Nyx 論文 Section 4（Design），對照本章的架構圖，找出每個架構組件對應論文的哪一節。
3. 如果有 Intel PT 硬體（非 WSL2），嘗試用 `perf record -e intel_pt// -- ls` 記錄 PT trace，再用 `perf script` 解碼，感受原始 PT 資料的形式。

---

## 本章重點

- Nyx/kAFL 把 fuzzer 放在 hypervisor 層，用 VM snapshot 解決 reset 問題，用 Intel PT 解決 coverage 問題
- 三個核心組件：VM snapshot（dirty page 還原）、Intel PT（硬體無插樁 coverage）、Nyx agent（guest 端注入）
- Affine Types spec 規範「如何把 bytes 轉換成 target 操作序列」，確保注入的確定性
- 硬體依賴是最大限制：需要 Intel CPU + VT-x + Intel PT，WSL2 和多數雲端環境不支援
- exec/s 比 VM restart 快 10–50 倍，比 fork server 在 kernel/hypervisor 目標上有根本優勢

---

## 自我檢核

- [ ] 能說出 Nyx 的三個核心組件各自解決什麼問題？
- [ ] Intel PT 為什麼能在不插樁的情況下拿 coverage？代價是什麼？
- [ ] Nyx agent 住在哪一層？它的職責是什麼？
- [ ] Affine Types 解決了什麼問題？跟一般的 structured fuzzing 有什麼不同？
- [ ] 為什麼 Nyx 在 WSL2 跑不起來？什麼環境才能跑？

---

## 延伸閱讀

1. **Nyx: Greybox Hypervisor Fuzzing using Fast Snapshots and Affine Types**（Schumilo et al., USENIX Security 2021）
   - 讀 Section 3（Overview）、Section 4（Design）、Section 5（Implementation）——本章的直接來源，Section 4 的 Affine Types 設計是論文最獨特的貢獻
   - https://www.usenix.org/conference/usenixsecurity21/presentation/schumilo

2. **kAFL: Hardware-Assisted Feedback Fuzzing for OS Kernels**（Schumilo et al., USENIX Security 2017）
   - 讀 Section 3（System Design）和 Section 4（Implementation）——理解 Intel PT 怎麼被用在 kernel fuzzing 的原始論文，Nyx 的架構直接從這裡演進
   - https://www.usenix.org/conference/usenixsecurity17/technical-sessions/presentation/schumilo

3. **Nyx-Net: Network System Fuzzing with Incremental Snapshots**（Schumilo et al., EuroSys 2022 / CCS 2022）
   - 讀 Section 2（Background and Motivation）和 Section 3（Design）——把 snapshot 用到網路服務，解決 stateful target 的 reset 問題，與 Ch 16–20 的 stateful fuzzing 形成橋樑
   - https://arxiv.org/abs/2111.03013

---

→ [Ch 30 Intel PT 當 coverage source](./30-intel-pt-coverage.md)
