# Ch 18 — StateAFL 與狀態表示

> **目標**: 理解 AFLNet 用 response code 推斷狀態的根本侷限，掌握 StateAFL 如何改用伺服器記憶體快照來定義協定狀態，以及 Locality-Sensitive Hash 在防止狀態爆炸上扮演的角色。讀完這章，你能評估一個目標協定該用哪種狀態表示法，並知道 StateAFL 實際部署時的坑在哪裡。

---

## 為什麼需要這章

Ch 17 介紹了 AFLNet 的核心設計：把 server 回應的 response code 當作狀態標誌，驅動有限狀態機（FSM）的遍歷。這個設計對 SMTP、FTP、RTSP 這類文字協定運作得相當好——它們的狀態轉移剛好編碼在可讀的三位數字裡。

但一旦碰到下面兩種情況，AFLNet 就出問題了：

1. **二進位協定**：QUIC、TLS、MQTT（二進位模式）、SMB……沒有 "220 Service ready" 這種東西，回應是一堆位元組，不存在 response code 可解析。
2. **同碼異狀**：考慮一個登入流程，`AUTH OK (200)` 可能出現在「帳號已驗證但還沒選擇郵箱」或「已完整登入且 session 活躍」兩個截然不同的內部狀態。response code 相同，但繼續發同樣的請求，server 行為完全不同。

StateAFL 的解法是換一個資訊源——不看 server 說什麼，直接看 server **的記憶體是什麼狀態**。

這章深挖：
- StateAFL 記憶體快照機制的設計與侷限
- Locality-Sensitive Hash (LSH) 如何對抗狀態爆炸
- 選哪些記憶體區域 hash（最難的工程決策）
- StateAFL vs AFLNet 的完整取捨對照

---

## 先建立直覺

### AFLNet 的狀態推斷路徑

```
Client sends:  EHLO example.com
Server replies: 250-mail.example.com Hello

AFLNet sees:   "250"  ─────► state_id = hash("250") = 0x1A2B
                              加入 state_corpus["0x1A2B"]

Client sends:  AUTH LOGIN
Server replies: 334 VXNlcm5hbWU6

AFLNet sees:   "334"  ─────► state_id = hash("334") = 0x3C4D
                              狀態轉移 0x1A2B → 0x3C4D，記錄邊
```

問題：如果協定是 TLS，server 回的是：
```
16 03 03 00 7a 02 00 00 76 03 03 ...
```
AFLNet 完全不知道從哪裡截 "response code"。

### StateAFL 的替代路徑

```
                    ┌─────────────────────────────┐
                    │         Target Server        │
Client sends M1 ───►│                             │
                    │  process_message(M1)         │
                    │    ↓                         │
                    │  global_state.auth_phase = 1 │
                    │  session.user = "alice"      │
                    │  conn.tls_established = true │
                    └──────────┬──────────────────┘
                               │  checkpoint 觸發
                               ▼
                    ┌─────────────────────────────┐
                    │     StateAFL Runtime        │
                    │                             │
                    │  snapshot( &global_state,   │
                    │            &session )        │
                    │       ↓                     │
                    │  LSH( raw_bytes )           │
                    │       ↓                     │
                    │  state_id = 0xF3A9...       │
                    └─────────────────────────────┘
```

關鍵差異：資訊從 **server output** 換成了 **server memory**。output 是 server 決定讓外界看到的，memory 是 server 實際的內部狀態。

---

## 核心概念

### 1. Checkpoint 機制

StateAFL 在 server 每處理完一條訊息後設置 checkpoint（检查点）。在 checkpoint 觸發時，StateAFL 的 instrumentation 會：

1. 暫停 server 執行（透過 `SIGSTOP` 或插樁點）
2. 讀取預先選定的記憶體區域
3. 計算這些區域的 hash
4. 把 hash 值寫入 shared memory，讓 fuzzer process 讀取
5. 恢復 server 執行

checkpoint 插樁通常放在：
- 訊息解析函式的返回點
- 主事件迴圈的 iteration 邊界
- `recv()` / `read()` 系統呼叫之後的 dispatch 函式

### 2. 記憶體快照與 Hash

StateAFL 不是 hash 整個 heap——那樣 noise 太大（malloc metadata、cache 行、時間戳記……每次都變）。它只 hash **預先選定的狀態相關記憶體區域**。

Hash 函式選用的是 **Locality-Sensitive Hash (LSH)**，而非 SHA-256 或 FNV 這類密碼學/快速 hash。原因下面說。

```c
/* 概念性實作，非 StateAFL 原始碼 */
typedef struct {
    uint8_t  *regions[MAX_STATE_REGIONS];
    size_t    region_sizes[MAX_STATE_REGIONS];
    int       n_regions;
} state_config_t;

uint64_t compute_state_id(state_config_t *cfg) {
    /* 把所有選定區域的位元組串接起來 */
    uint8_t buf[MAX_SNAPSHOT_SIZE];
    size_t  off = 0;
    for (int i = 0; i < cfg->n_regions; i++) {
        memcpy(buf + off, cfg->regions[i], cfg->region_sizes[i]);
        off += cfg->region_sizes[i];
    }
    return lsh_minhash(buf, off);  /* LSH，不是普通 hash */
}
```

### 3. Locality-Sensitive Hash (LSH) 的作用

普通 hash（MD5、FNV）的性質：**輸入差一個 bit，輸出完全不同**。

LSH 的性質：**輸入相似，輸出相似**。更精確地說，對於 Jaccard 距離下的 MinHash：

```
Pr[ MinHash(A) == MinHash(B) ] = Jaccard(A, B)
                                = |A ∩ B| / |A ∪ B|
```

這對 StateAFL 意味著什麼？

```
狀態 S1: auth_phase=1, user_id=0x0042, buffer="EHLO"
狀態 S2: auth_phase=1, user_id=0x0042, buffer="EHLO\r\n"  ← 僅差兩個位元組

普通 hash:
  hash(S1) = 0xDEADBEEF12345678
  hash(S2) = 0x7A3F92B4C1E80011  ← 完全不同，看起來是兩個獨立狀態

LSH (MinHash):
  lsh(S1) ≈ lsh(S2)             ← 相似，會被合併為同一狀態
```

在協定 fuzzing 的場景，buffer 末尾的 `\r\n` 差異並不代表 server 進入了不同的邏輯狀態。普通 hash 會把它當成新狀態，導致狀態爆炸；LSH 把它合併，讓 fuzzer 的算力花在真正不同的狀態上。

### 4. LSH 的實現選項

StateAFL 論文使用的是 **MinHash** 的變體：

```
MinHash 流程（概念）：
1. 把記憶體視為 n-gram 的集合（e.g., 4-byte chunks）
2. 用 k 個不同的 hash function h_1...h_k 分別對集合取最小值
3. signature = [min(h_1(S)), min(h_2(S)), ..., min(h_k(S))]
4. 兩個 signature 的 Hamming 距離近 → 原始狀態相似

具體參數（StateAFL 預設）：
  k = 64（64 個 hash function）
  n-gram size = 4 bytes
  相似度閾值 = 0.8（80% MinHash 值相同 → 視為同一狀態）
```

---

## 底層機制

### StateAFL 系統架構

```
┌─────────────────────────────────────────────────────────────┐
│                     afl-fuzz (修改版)                        │
│                                                             │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │  Seed Queue  │    │ State-aware  │    │  Coverage    │  │
│  │  (per-state  │    │  Scheduler   │    │  Bitmap      │  │
│  │   corpus)    │    │              │    │  (afl 原有)  │  │
│  └──────┬───────┘    └──────┬───────┘    └──────────────┘  │
│         │                   │                               │
│         └─────────────┬─────┘                               │
│                       ▼                                     │
│              ┌─────────────────┐                            │
│              │  Shared Memory  │                            │
│              │  [coverage_map] │◄──────── afl 標準共享記憶體│
│              │  [state_hash]   │◄──────── StateAFL 新增    │
│              └────────┬────────┘                            │
└───────────────────────│─────────────────────────────────────┘
                        │
          fork() + exec │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│                  Target Server Process                       │
│                                                             │
│  recv(M1) ──► dispatch() ──► handler_A()                    │
│                                    │                        │
│                                    ▼                        │
│                         [checkpoint instrumentation]        │
│                              │                              │
│                    read selected globals                     │
│                    compute LSH                              │
│                    write to shm[state_hash]                 │
│                              │                              │
│  recv(M2) ──► dispatch() ──► handler_B() ...               │
└─────────────────────────────────────────────────────────────┘
```

### 狀態轉移圖的建立

```
Fuzzer 執行軌跡：M1 → M2 → M3

  start ─M1─► state_0xF3A9 ─M2─► state_0x7B21 ─M3─► state_0xC4DE
                   ↑                   ↑                   ↑
              auth_none           auth_partial          auth_done
              (記憶體快照)         (記憶體快照)           (記憶體快照)

StateAFL 維護的狀態圖：
  節點 = 不同的 LSH 狀態 ID
  邊   = 觸發轉移的訊息序列
  標籤 = 抵達該狀態的 seed 集合

目標：對每個狀態節點，確保 fuzzer 能定向地反覆到達該狀態並在此 mutate
```

### StateAFL vs AFLNet 的 FSM 建立對比

```
AFLNet：
  輸入軌跡 → 解析 response codes → 建立 FSM
  FSM 的狀態 = response code 的序列
  問題：二進位協定無 response code

StateAFL：
  輸入軌跡 → checkpoint 觸發 → 記憶體快照 → LSH → 建立 FSM
  FSM 的狀態 = LSH(選定記憶體區域)
  問題：需要知道 hash 哪些記憶體
```

---

## 進階用法

### 記憶體區域選擇策略

這是 StateAFL 實際部署最難的決策。StateAFL 論文提出三種方式，按精確度遞增（工作量也遞增）：

**方式 A：自動靜態分析**

StateAFL 使用 LLVM 分析 pass，找出滿足以下條件的全域變數：
- 在多個 message handler 函式中都有讀寫
- 不是純粹的計數器或時間戳記
- 型態不是 `pthread_mutex_t` 等同步原語

```bash
# StateAFL 的靜態分析步驟（概念）
clang -emit-llvm -c target_server.c -o target.bc
opt -load StateAFL-analysis.so -statevar-detect \
    -annotate-output state_regions.txt \
    target.bc -o target_instrumented.bc
```

**方式 B：Heuristic 過濾**

不做完整靜態分析，改用簡單 heuristic：
- 在 `recv()` 之後被寫入的全域變數列為候選
- 在 `send()` 之前被讀取的全域變數列為候選
- 兩者交集 → 高機率是真正的狀態變數

**方式 C：人工標注**

對目標協定熟悉的情況下，直接在 target source 加 annotation：

```c
/* 在 target server source 加入 StateAFL annotation */
#include "stateafl_ann.h"

struct session_state {
    int    auth_phase;      /* STATEAFL_TRACK */
    char   username[64];    /* STATEAFL_TRACK */
    time_t last_seen;       /* STATEAFL_IGNORE — 太 noisy */
    int    recv_count;      /* STATEAFL_IGNORE — 純計數器 */
} g_session;
```

### 狀態表示法的完整分類樹

```
狀態表示法分類
│
├── 基於 Server 輸出 (Response-based)
│   └── AFLNet
│         訊息回應 → response code → state ID
│         優點：零開銷、不需碰 server 記憶體
│         缺點：只適用文字協定，同碼異狀問題
│
├── 基於 Server 記憶體 (Memory-based)
│   ├── 全 heap hash
│   │     hash 整個 heap 的每個 allocation
│   │     缺點：noise 太大，每次執行幾乎都不同
│   │
│   ├── 選定全域變數 hash（StateAFL 主要模式）
│   │     只 hash 預選的 state 相關全域/堆積物件
│   │     使用 LSH 合併相似狀態
│   │     缺點：需要事先知道 hash 哪些變數
│   │
│   └── 程序計數器集合 (PC sets)
│         把執行過的 basic block 集合視為狀態代理
│         類似 afl 的 coverage bitmap，但以集合而非 bitmap 計算
│
├── 基於覆蓋率 (Coverage-based)
│   └── 把 coverage bitmap 直接當狀態
│         優點：afl 生態天然支援
│         缺點：覆蓋率相同但記憶體狀態不同 → 被誤判為同狀態
│
└── 基於人工標注 (Manual Annotations)
      開發者直接指定「這個變數是狀態變數」
      精確度最高，但需要 source code 且工作量大
```

### 與 afl++ 整合的建置流程

**本段未實測，為理論預期行為**。驗證步驟：在 WSL2 Ubuntu 22.04，按照 `https://github.com/stateafl/stateafl` 的 README，先確認 afl++ 2.68c 或相容版本已安裝，再執行以下步驟。

```bash
# 取得 StateAFL
git clone https://github.com/stateafl/stateafl.git
cd stateafl

# 建置（需要 afl++ 相依）
make

# 用 StateAFL 的 clang wrapper 編譯 target server
# （instrumentation 會被插入 checkpoint 位置）
CC=./afl-clang-fast \
  CFLAGS="-DSTATEAFL" \
  ./configure --prefix=/tmp/target-inst
make install

# 執行 fuzzing
./afl-fuzz \
  -i corpus/          \   # 初始 seed
  -o findings/        \   # 輸出目錄
  -N tcp://127.0.0.1/21 \ # 目標位址（FTP 範例）
  -- /tmp/target-inst/bin/ftpd @@
```

StateAFL 在 `afl-fuzz` 執行期間會：
1. 啟動 target server（fork-server 模式）
2. 發送 seed 訊息序列
3. 在每條訊息後讀取 `shm[state_hash]`
4. 根據狀態 ID 決定下一個 mutation 方向

### 針對 Binary Protocol 的場景範例

假設目標是一個自定義二進位通訊協定，封包格式：
```
[4B magic][1B type][2B length][nB payload]
```

AFLNet 完全無法自動推斷狀態（沒有文字 response code）。

StateAFL 的做法：
1. 識別 server 中的 `session->state` 全域變數（型態 `enum`）
2. 把這個 `enum` 加入 hash 區域
3. StateAFL 自動追蹤 `HANDSHAKE → AUTH → ESTABLISHED → CLOSING` 的狀態轉移

這樣即使封包是純二進位，fuzzer 也能知道「現在 server 在 AUTH 狀態，應該優先 mutate 認證相關的 payload 欄位」。

---

## 對比取捨

| 維度 | AFLNet | StateAFL（記憶體快照）|
|---|---|---|
| **狀態資訊來源** | Server 的 response output | Server 的記憶體內容 |
| **二進位協定支援** | 不支援（需要 response code） | 支援 |
| **每次執行額外開銷** | 極低（parse response） | 中等（記憶體讀取 + LSH 計算）|
| **設定複雜度** | 低（指定 response code 格式即可）| 中到高（需要識別 state 相關記憶體）|
| **狀態準確度** | 取決於協定設計，有同碼異狀問題 | 取決於選擇了正確的記憶體區域 |
| **狀態爆炸風險** | 低（response code 種類有限）| 中（需靠 LSH 控制）|
| **對 source code 的依賴** | 不需要（黑盒可用）| 靜態分析方式需要；人工標注需要 source |
| **記憶體使用（fuzzer 端）** | 低 | 中（需存 state 特徵向量）|
| **適用典型場景** | SMTP、FTP、RTSP、HTTP/1.1 | TLS、QUIC、SMB、自定義二進位協定 |

---

## 踩雷

- **錯誤直覺**：「記憶體快照就等於完整的 server 狀態。」
  **正確認知**：StateAFL 只 hash **預先選定的記憶體區域**，不是整個 process 記憶體空間。如果選錯了（選了雜訊大的區域、或漏掉真正的狀態變數），兩個完全不同的邏輯狀態可能得到相同的 hash（假陰性），或同一個邏輯狀態的不同 noise 被判定為不同狀態（假陽性）。記憶體快照準不準，完全取決於你選了什麼東西來 hash。

- **錯誤直覺**：「狀態越多越好——狀態粒度越細，fuzzer 對協定的理解越精確。」
  **正確認知**：狀態爆炸（state explosion）是有限狀態機 fuzzing 的核心問題。如果每一個微小的記憶體差異都被視為新狀態，fuzzer 的 corpus 會暴增，排程器的算力被分散到無數個幾乎相同的狀態上，有效 throughput 反而下降。LSH 的設計目的就是**有損壓縮**：犧牲一部分精確度，換取狀態空間的可控規模。相似度閾值（e.g., 80%）是個超參數，調得太高（合併太多）→ 漏掉真實轉移；調得太低（幾乎不合併）→ 狀態爆炸。

- **錯誤直覺**：「StateAFL 出來了，AFLNet 就過時了，應該無腦用 StateAFL。」
  **正確認知**：對有清晰 response code 的文字協定（SMTP、FTP、SIP），AFLNet 的設定更簡單、overhead 更低、狀態語義更直觀（response code 本身就是協定設計者定義的狀態）。StateAFL 的價值在於**二進位協定**和**需要 server 內部精確狀態**的場景。工具選擇應該先看目標協定特性，而非新舊。

- **額外陷阱**：**checkpoint 位置選錯會破壞整個狀態推斷**。如果 checkpoint 插在訊息處理的中途（而非完成後），記憶體快照反映的是 partial 狀態，LSH 輸出的 state ID 毫無意義。checkpoint 必須插在 server 完全消化一條訊息、更新完所有狀態變數之後的點。對事件驅動的 server（libuv、libevent）這個點不一定顯而易見。

---

## 進階延伸

**SGFuzz（Stateful Greybox Fuzzing）**：SGFuzz 不依賴記憶體快照，而是直接追蹤程式中宣告為狀態機相關的變數（透過靜態分析識別 `enum` 型態的全域變數），並在 fuzzer 的種子選擇和 mutation 策略上做狀態感知的調整。相比 StateAFL，SGFuzz 的 overhead 更低，但對「哪個 enum 是狀態機」的識別精確度有限。

**污點分析自動識別狀態變數**：把 taint tracking 整合進 StateAFL 的工作流可以自動化「選哪些記憶體來 hash」的決策——追蹤哪些記憶體區域的值由 `recv()` 的輸入決定，並且在後續的 `send()` 或 `write()` 之前被讀取，這些區域高機率是狀態相關的。工具鏈：LibAFL（有 taint tracking 支援）+ StateAFL 的 LSH 模組。

**協定逆向工程作為前置補強**：如果目標 server 沒有 source code（黑盒），可以先用協定逆向工具（如 **Polyglot**、**Netzob**、或商業的 Peach Pit）從流量中推斷出協定的 message format 和 FSM，再用推斷出的狀態資訊指導 StateAFL 的記憶體區域選擇。StateAFL 本身是 white-box（需要 instrumentation），但協定結構推斷可以在 black-box 階段完成。

**與 Ch 40 混合模糊測試的結合**：Ch 40 討論的符號執行 + fuzzing 混合方法，在 StateAFL 的脈絡下有一個明確的應用：用符號執行（例如 angr）對 server 的狀態機做靜態分析，自動找出哪些條件分支對應狀態轉移，進而精確識別應該 hash 的記憶體區域。這比 StateAFL 目前的 heuristic 靜態分析更準確，代價是符號執行本身的 scalability 問題。

---

## 動手練習

1. **分析一個現有文字協定 server 的狀態變數**
   取得 `vsftpd 3.0.5` 的 source code。找出 `struct vsf_session` 結構體中哪些欄位是 FTP 狀態機的真正狀態（如 `logged_in`、`data_conn_fd` 等），哪些是噪音（如連線計數器、時間戳記）。如果要手動標注給 StateAFL，你會選哪些欄位？說明理由。

2. **LSH 相似度的直覺驗證**
   用 Python 實作一個簡單的 4-gram MinHash：
   ```python
   from datasketch import MinHash

   def memory_lsh(data: bytes, num_perm=64) -> MinHash:
       m = MinHash(num_perm=num_perm)
       for i in range(len(data) - 3):
           m.update(data[i:i+4])
       return m

   # 測試：兩個幾乎相同的記憶體快照
   s1 = b"AUTH_DONE\x00user=alice\x00session_id=0x0042"
   s2 = b"AUTH_DONE\x00user=alice\x00session_id=0x0043"  # 只差 1 byte

   m1 = memory_lsh(s1)
   m2 = memory_lsh(s2)
   print(f"Jaccard 估計: {m1.jaccard(m2):.3f}")  # 預期 > 0.8
   ```
   調整 `num_perm`（16, 32, 64, 128），觀察相似度估計的方差如何變化。

3. **checkpoint 位置分析**
   閱讀 `lighttpd` 或 `pure-ftpd` 的主事件迴圈（通常在 `server.c` 或 `ftpd.c` 的 `main_loop()` 函式）。畫出一個事件迴圈迭代的流程圖，標出哪個位置是插入 StateAFL checkpoint 的最佳點，並解釋為什麼不能插在訊息解析的中途。

4. **StateAFL vs AFLNet 協定適用性判斷**
   針對以下協定，判斷應使用 AFLNet 還是 StateAFL，並說明原因：
   - (a) Redis RESP 協定（文字，但狀態複雜）
   - (b) DTLS（UDP + 二進位 + 握手狀態機）
   - (c) SSH（二進位，多層狀態）
   - (d) HTTP/1.1（文字 response code 清晰）

---

## 本章重點

- AFLNet 用 response code 推斷狀態，對二進位協定無效，對同碼異狀的文字協定也不準確。
- StateAFL 改用 server 記憶體快照：在 checkpoint 時讀取選定的記憶體區域，計算 LSH，得到狀態 ID。
- LSH（尤其是 MinHash）的核心性質是「輸入相似則輸出相似」，用於對抗狀態爆炸——將記憶體微小差異引起的狀態變化合併，讓 fuzzer 的算力集中在真正不同的狀態上。
- 選哪些記憶體區域 hash 是最難的工程決策。StateAFL 提供靜態分析、heuristic、人工標注三條路，精確度與工作量成正比。
- StateAFL 適合二進位協定和需要 server 精確內部狀態的場景；AFLNet 在有清晰 response code 的文字協定上更簡單且 overhead 更低。工具選擇取決於目標協定特性，不是新舊。

---

## 自我檢核

- [ ] 我能說出 AFLNet 在二進位協定上失效的根本原因，以及同碼異狀問題的具體例子。
- [ ] 我理解 StateAFL 的 checkpoint 機制：在哪個時機點觸發、讀什麼資料、把結果送到哪裡。
- [ ] 我能解釋為什麼 StateAFL 用 LSH 而非普通 hash（SHA-256 / FNV），以及 LSH 的哪個性質使它適合這個場景。
- [ ] 我知道「選哪些記憶體 hash」的三種策略（靜態分析、heuristic、人工標注）各自的優缺點。
- [ ] 我能填寫 StateAFL vs AFLNet 對比表的每一個格子，包括「二進位協定支援」和「狀態準確度」行。
- [ ] 我理解狀態爆炸是什麼問題，以及 LSH 的相似度閾值過高和過低各自會造成什麼後果。
- [ ] 對一個給定的目標協定，我能判斷應該選 AFLNet 還是 StateAFL 並說出理由。

---

## 延伸閱讀

1. **StateAFL 原始論文**
   Natella, R. (2022). *StateAFL: Reusable State Coverage-guided Fuzzing for Network Protocols*. Empirical Software Engineering, Springer.
   **讀什麼**：第 3 節說明 LSH 的選型決策（為什麼是 MinHash 而非 SimHash），第 4 節詳述靜態分析如何識別 state 變數，第 6 節的 evaluation 量化了「記憶體選擇正確性」對 bug 發現率的影響。
   **相關性**：本章所有核心機制的原始出處，讀論文比讀二手摘要準確得多。

2. **Andoni & Indyk (2008). *Near-Optimal Hashing Algorithms for Approximate Nearest Neighbor in High Dimensions.* Communications of the ACM.**
   **讀什麼**：LSH 的理論基礎——為什麼 MinHash 能以可控的錯誤率估計 Jaccard 相似度，`num_perm` 參數如何影響估計方差。雖然是數學導向的論文，Section 2–3 對工程師來說是最有用的部分。
   **相關性**：理解 StateAFL 用 LSH 做狀態合併的理論保證，以及為什麼這個保證在實踐中可能不成立（高維度問題、選錯 n-gram size）。

3. **Van-Thuan Pham et al. (2020). *AFLNet: A Greybox Fuzzer for Network Protocols*. ICST 2020.**
   **讀什麼**：Section 3 描述 AFLNet 的 FSM 推斷機制，Section 5 的 evaluation 展示 AFLNet 在哪些協定上有效、在哪些協定上碰壁——這些「碰壁的案例」正是 StateAFL 動機的具體化。
   **相關性**：本章是在 AFLNet（Ch 17）基礎上推進的。重讀 AFLNet 論文時帶著「這個設計哪裡做不到 StateAFL 能做的事」的問題，會讀出不同的東西。

4. **Böhme et al. (2022). *SGFuzz: Stateful Greybox Fuzzing of Event-Driven Programs.* USENIX Security 2022.**
   **讀什麼**：Section 4 解釋 SGFuzz 如何用靜態分析找 `enum` 型態的狀態變數（比 StateAFL 的 heuristic 更精確），Section 6 直接與 AFLNet 和 StateAFL 做比較實驗，是難得的三者並列評測。
   **相關性**：了解狀態感知 fuzzing 的下一步演進方向，以及「狀態變數識別」這個問題的不同解法。

---

StateAFL 把「我們能不能看到 server 的內部狀態」這個問題從協定層拉到記憶體層，解開了 AFLNet 在二進位世界的死結——代價是你得告訴它看哪裡。這個取捨沒有免費的午餐：從 response code 到記憶體快照，本質上是把協定設計者的知識換成 server 實作者的知識。

→ [下一章](./19-harnessing-servers.md)
