# Ch 17 — AFLNet：針對網路協定的狀態感知 fuzzer

> **目標**：理解 AFLNet 如何把 afl++ 的覆蓋率反饋延伸到有狀態的網路協定；掌握 M2S 映射的原理與限制；能夠把一個真實的有狀態伺服器接進 AFLNet 並解讀輸出。
>
> **環境**：WSL2 Ubuntu 22.04，gcc 11.4，make。AFLNet 從源碼 build（`~/tools/aflnet/`），不與 afl++ 的 `afl-fuzz` 共用 PATH。本章標記了哪些步驟在本機實測、哪些是理論預期行為。

---

## 為什麼需要這章

Ch 16 把問題說清楚了：對有狀態的伺服器（FTP、SMTP、RTSP），一個隨機的 mutation 輸入幾乎不可能通過前幾條握手訊息，覆蓋率永遠停在協定序列的起點。

afl++ 解決不了這個問題的根本原因不是 mutation 不夠聰明——是它的**輸入模型**不對。afl++ 把整個輸入當成一個 blob，但有狀態協定的輸入是一個**訊息序列**，而且伺服器的狀態在每條訊息之後都會改變。

AFLNet（2020）的貢獻是直接改了輸入模型：seed 不再是一個 blob，而是一段完整的 session 錄製——多條訊息按順序排列。Mutation 可以針對序列中的單一訊息操作，也可以在序列的中間插入或刪除訊息。更關鍵的是，AFLNet 把伺服器的**回應碼**當作狀態的代理，用這個來追蹤哪些協定狀態被覆蓋了、哪些還沒碰到。

這章把 AFLNet 的每個機制拆開來看，並且誠實說它的限制在哪裡。

---

## 先建立直覺

### 訊息序列 vs 單一 blob

```
afl++ 看到的世界（一個 blob）：

┌─────────────────────────────────────────────────────┐
│  USER anonymous\r\nPASS \x00\x41LIST /\r\n...      │
│  ← mutation 在整個 buffer 上隨機打洞 →              │
└─────────────────────────────────────────────────────┘
                      │
                      ▼
       伺服器看到損壞的第一條訊息，直接斷線
       之後所有 mutation 都沒有意義

AFLNet 看到的世界（一個訊息序列）：

┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  訊息 M1     │  │  訊息 M2     │  │  訊息 M3     │
│ USER anon\r\n│→ │ PASS x\r\n  │→ │ LIST /\r\n   │
└──────────────┘  └──────────────┘  └──────────────┘
       │                 │                 │
       ▼                 ▼                 ▼
   回應 R1           回應 R2           回應 R3
   "220 ready"       "331 need pw"     "230 logged"
       │                 │                 │
       ▼                 ▼                 ▼
   狀態 S1           狀態 S2           狀態 S3
   (GREETING)        (AUTH)            (LOGGED_IN)

Mutation 只打在 M3 上 → 伺服器已在 S3，才能觸發
LIST handler 裡的 bug
```

這個直覺是 AFLNet 最核心的思想：**把 mutation 的目標鎖定在特定協定狀態之後的訊息**，不讓隨機打洞破壞前置的握手流程。

### 狀態分區語料庫

```
AFLNet 的 corpus 按狀態分區：

  corpus/
  ├── state_0/    (GREETING 狀態到達的 seed)
  │   ├── seed_001   ← 只發 USER，觀察到狀態 S0→S1
  │   └── seed_002
  │
  ├── state_1/    (AUTH 狀態的 seed)
  │   ├── seed_003   ← 發 USER+PASS，觀察到 S1→S2
  │   └── seed_004
  │
  ├── state_2/    (LOGGED_IN 狀態的 seed)
  │   ├── seed_007   ← 完整登入後發 LIST
  │   └── seed_008
  │
  └── state_3/    (DATA 狀態的 seed，尚未探索)
      (空的)       ← AFLNet 會優先選這個分區的 seed 去 mutate

選種策略：優先選**覆蓋次數少的狀態**的 seed
→ state_3 空的，所以 AFLNet 會把 mutation 引導向產生能到達 state_3 的序列
```

---

## 核心概念

### M2S 映射（Message-to-State）

AFLNet 的狀態推斷不依賴對協定語義的深度理解——它直接讀伺服器的**回應碼**，把回應碼序列對應到一個狀態 ID。

以 FTP 為例：

```
訊息序列送出後，AFLNet 讀取每條回應的前三個字元：

M1: USER anonymous\r\n
R1: 220 FTP server ready\r\n       ← 回應碼 "220"

M2: PASS \r\n
R2: 331 Password required\r\n      ← 回應碼 "331"

M3: PASS anything\r\n
R3: 230 User logged in\r\n         ← 回應碼 "230"

M4: LIST /\r\n
R4: 150 Opening data connection\r\n ← 回應碼 "150"

M4 回應後：
R5: 226 Transfer complete\r\n       ← 回應碼 "226"

M2S 映射：
  回應碼序列 ["220"]              → 狀態 ID 0
  回應碼序列 ["220","331"]        → 狀態 ID 1
  回應碼序列 ["220","331","230"]  → 狀態 ID 2
  回應碼序列 ["220","331","230","150","226"] → 狀態 ID 3
```

實作上，AFLNet 用一個 hash 把「到目前為止看到的所有回應碼」對應到一個數字 ID。這個設計的優點是不需要預先定義協定狀態機——只要協定有文字可解析的回應碼，AFLNet 就能自動建構狀態圖。

### 從 seed PCAP 建立初始序列

AFLNet 的 seed 格式是**純文字的訊息序列**，每條訊息之間用 AFLNet 理解的分隔符隔開。實際上最簡單的 seed 是直接把一段合法的 session 原始位元組存成檔案：

```bash
# 方法一：用 netcat 錄製一段合法 FTP session
# （需要一個真實的 FTP server 在跑）
nc -q 2 127.0.0.1 21 < ftp_commands.txt > /dev/null

# ftp_commands.txt 的內容（每行一條指令）：
# USER anonymous
# PASS
# LIST /
# QUIT

# 方法二：直接手寫原始位元組
printf 'USER anonymous\r\nPASS \r\nLIST /\r\nQUIT\r\n' > seeds/ftp_session_001
```

AFLNet 讀取 seed 時，會依據協定的 request/response 分界來切割訊息邊界——這個分界是用 `-P` 旗標指定的協定類型決定的。

### 完整執行流程

以一個自製的簡單 AUTH 伺服器為目標（延續 Ch 16 的設計概念）：

```c
/* auth_server.c — 一個最小的 AUTH 協定伺服器，用作 AFLNet 演示目標
 *
 * 協定：
 *   客戶端送 "USER <name>\r\n"   → 伺服器回 "331 Password required\r\n"
 *   客戶端送 "PASS <pw>\r\n"     → 密碼正確："230 Login ok\r\n"
 *                                   密碼錯誤："530 Login failed\r\n"
 *   登入後送 "CMD <data>\r\n"    → 伺服器處理 data（這裡故意放一個 bug）
 *   任何時候送 "QUIT\r\n"        → "221 Bye\r\n" + 斷線
 *
 * 編譯：gcc -g -o auth_server auth_server.c
 * 跑：  ./auth_server 9999
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <signal.h>

#define BUF_SIZE 4096
#define SECRET_PW "s3cr3t"

static void handle_client(int fd) {
    char buf[BUF_SIZE];
    char cmd_buf[64];     /* 故意小，用來觸發 stack overflow */
    int logged_in = 0;
    ssize_t n;

    dprintf(fd, "220 AuthServer ready\r\n");

    while ((n = recv(fd, buf, sizeof(buf) - 1, 0)) > 0) {
        buf[n] = '\0';

        if (strncmp(buf, "USER ", 5) == 0) {
            dprintf(fd, "331 Password required\r\n");

        } else if (strncmp(buf, "PASS ", 5) == 0) {
            char *pw = buf + 5;
            /* 去掉結尾的 \r\n */
            pw[strcspn(pw, "\r\n")] = '\0';
            if (strcmp(pw, SECRET_PW) == 0) {
                logged_in = 1;
                dprintf(fd, "230 Login ok\r\n");
            } else {
                dprintf(fd, "530 Login failed\r\n");
            }

        } else if (strncmp(buf, "CMD ", 4) == 0 && logged_in) {
            char *data = buf + 4;
            data[strcspn(data, "\r\n")] = '\0';
            /* BUG: 無邊界 strcpy → stack overflow */
            strcpy(cmd_buf, data);
            dprintf(fd, "200 Executed: %s\r\n", cmd_buf);

        } else if (strncmp(buf, "QUIT", 4) == 0) {
            dprintf(fd, "221 Bye\r\n");
            break;

        } else {
            dprintf(fd, "500 Unknown command\r\n");
        }
    }

    close(fd);
    exit(0);  /* fork-based server：子行程直接 exit */
}

int main(int argc, char *argv[]) {
    if (argc < 2) { fprintf(stderr, "usage: %s <port>\n", argv[0]); return 1; }
    int port = atoi(argv[1]);

    signal(SIGCHLD, SIG_IGN);

    int srv = socket(AF_INET, SOCK_STREAM, 0);
    int opt = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_addr.s_addr = INADDR_ANY,
        .sin_port = htons(port),
    };
    bind(srv, (struct sockaddr *)&addr, sizeof(addr));
    listen(srv, 5);

    while (1) {
        int cli = accept(srv, NULL, NULL);
        if (cli < 0) continue;
        if (fork() == 0) {
            close(srv);
            handle_client(cli);
        }
        close(cli);
    }
}
```

```bash
# 編譯目標（加 ASan 讓 stack overflow 更容易被 AFLNet 觀察到）
gcc -g -fsanitize=address -o auth_server auth_server.c

# 確認伺服器正常運作
./auth_server 9999 &
printf 'USER test\r\nPASS s3cr3t\r\nCMD hello\r\nQUIT\r\n' | nc 127.0.0.1 9999
# 預期輸出：
# 220 AuthServer ready
# 331 Password required
# 230 Login ok
# 200 Executed: hello
# 221 Bye
kill %1
```

建立 seed 目錄和最小 seed：

```bash
mkdir -p aflnet_seeds aflnet_out

# seed 1：完整的合法 session（能到達 CMD 狀態）
printf 'USER test\r\nPASS s3cr3t\r\nCMD hello\r\nQUIT\r\n' > aflnet_seeds/seed_001

# seed 2：登入失敗的路徑
printf 'USER test\r\nPASS wrong\r\nQUIT\r\n' > aflnet_seeds/seed_002
```

### AFLNet 執行指令

**本段未實測，為理論預期行為。** 以下指令基於 AFLNet 官方文件和源碼，在本機未對這個具體 target 執行。驗證方法：按上述步驟 build auth_server，再跑下列指令，觀察 AFLNet 的狀態圖輸出。

```bash
# 先確認 AFLNet 的 afl-fuzz 路徑，不與 afl++ 衝突
AFLNET=~/tools/aflnet/afl-fuzz

$AFLNET \
  -i aflnet_seeds \          # 輸入 seed 目錄
  -o aflnet_out \            # 輸出目錄
  -N tcp://127.0.0.1/9999 \ # 目標網路位址（-N = network target）
  -P FTP \                   # 協定名稱，決定 M2S 的回應碼解析方式
  -D 1000 \                  # 每條訊息送出後等待 1000 μs 讓伺服器處理
  -q 3 \                     # 狀態選擇策略：3 = 隨機選狀態
  -s 3 \                     # 每個狀態最多花 3 秒
  -E \                       # 啟用 state-aware mode
  -R \                       # 啟用 region-level mutation
  -m none \                  # 不限制記憶體（ASan 本身很吃記憶體）
  -t 5000 \                  # 每次 exec 的 timeout（ms）
  -- ./auth_server 9999      # 目標程式和參數
```

各旗標的意義：

| 旗標 | 必要性 | 作用 |
|------|--------|------|
| `-N tcp://host/port` | 必要 | 指定目標 IP:port；AFLNet 替換掉 afl++ 的 stdin 輸入，改成 TCP 連線 |
| `-P PROTOCOL` | 必要 | 決定 M2S 解析器；支援 FTP/HTTP/SMTP/SIP/RTSP；自訂協定需改源碼 |
| `-D usec` | 強烈建議 | 送完訊息後等 server 回應的 buffer time；太小會讀不到回應 |
| `-q strategy` | 建議 | 1=round-robin、2=覆蓋少的優先、3=隨機；實測上 2 對深狀態效果好 |
| `-s seconds` | 建議 | 每個狀態的 budget；防止在已飽和的狀態浪費時間 |
| `-E` | 必要（狀態感知）| 啟用狀態分區 corpus 和狀態感知 seed 選擇 |
| `-R` | 建議 | 啟用 region mutation（訊息級別而非 byte 級別）|
| `-m none` | 視情況 | ASan 的 shadow memory 很大，不限制避免 OOM kill |
| `-t ms` | 建議調高 | 每次 exec 要建立 TCP 連線，比 in-process fuzzing 慢十倍以上 |

### 預期輸出解讀

AFLNet 跑起來後，`aflnet_out/` 目錄結構：

```
aflnet_out/
├── queue/           ← 主語料庫（按狀態分區存放）
│   ├── id:000000,state:0,...
│   ├── id:000001,state:1,...
│   └── id:000002,state:2,...
├── crashes/         ← AFLNet 找到的 crash（含觸發的完整訊息序列）
├── hangs/           ← 超時的 case
├── replayable-crashes/  ← 可重放的 crash（AFLNet 特有）
└── plot_data        ← 狀態圖演化的時間序列

# 重放一個 crash（AFLNet 特有功能）：
$AFLNET -i aflnet_out/replayable-crashes -o /tmp/replay \
        -N tcp://127.0.0.1/9999 -P FTP -t 5000 \
        -- ./auth_server 9999
```

狀態覆蓋統計（在跑的時候 AFLNet 輸出類似以下的行）：

```
state: 0 [freq: 412]  state: 1 [freq: 203]  state: 2 [freq: 87]
→ state 2（登入後的 CMD 狀態）被觸及 87 次，相比 state 0 的 412 次少很多
→ AFLNet 的 -q 2 策略會把更多 budget 分配給 state 2
```

---

## 底層機制

### AFLNet 完整架構

```
AFLNet 執行循環

┌─────────────────────────────────────────────────────────┐
│                    AFLNet 主循環                         │
│                                                         │
│  ┌──────────────┐                                       │
│  │  Seed 選擇   │←──── 狀態感知策略（-q）              │
│  │  （按狀態）   │      優先選低覆蓋狀態的 seed           │
│  └──────┬───────┘                                       │
│         │                                               │
│         ▼                                               │
│  ┌──────────────────────────────────────┐              │
│  │        Region Mutator（-R）           │              │
│  │                                      │              │
│  │  seed 的訊息序列：[M1][M2][M3]        │              │
│  │                    │                 │              │
│  │  選定一個 region   ↓                 │              │
│  │  對 M3 做 mutation：                  │              │
│  │   - bit flip / byte flip             │              │
│  │   - havoc（隨機打洞）                │              │
│  │   - 插入新訊息 M3'                   │              │
│  │   - 刪除 M2，讓 M1 直接接 M3         │              │
│  └──────────────┬───────────────────────┘              │
│                 │  mutated 訊息序列                      │
│                 ▼                                       │
│  ┌──────────────────────────────────────┐              │
│  │   fork() + exec() 啟動新的 server    │              │
│  │                                      │              │
│  │   TCP 連線                           │              │
│  │   ┌────────┐  M1   ┌─────────────┐  │              │
│  │   │AFLNet  │──────▶│   server    │  │              │
│  │   │        │◀──────│  (child)    │  │              │
│  │   │        │  R1   └─────────────┘  │              │
│  │   │        │  M2   ┌─────────────┐  │              │
│  │   │        │──────▶│   server    │  │              │
│  │   │        │◀──────│  (same)     │  │              │
│  │   │        │  R2   └─────────────┘  │              │
│  │   │  ...   │                        │              │
│  └───┤        ├───────────────────────┘              │
│      └────┬───┘                                       │
│           │                                           │
│           ▼                                           │
│  ┌─────────────────────────────────────┐             │
│  │        M2S 映射                     │             │
│  │                                     │             │
│  │  回應碼序列 [R1.code, R2.code, ...] │             │
│  │       │                             │             │
│  │       ▼  hash                       │             │
│  │  state_id = hash(response_codes)    │             │
│  └──────────┬──────────────────────────┘             │
│             │                                         │
│             ▼                                         │
│  ┌──────────────────────────────────────┐            │
│  │   Coverage bitmap（繼承自 afl++）    │            │
│  │   + State bitmap（AFLNet 新增）      │            │
│  │                                      │            │
│  │   若有新 edge 或新 state → 加進 corpus│            │
│  │   若 crash → 存到 replayable-crashes │            │
│  └──────────────────────────────────────┘            │
└─────────────────────────────────────────────────────────┘
```

### Region 的定義

AFLNet 把 seed 的位元組流切割成「regions」。每個 region 對應一條請求訊息：

```
seed 位元組流：
  USER test\r\nPASS s3cr3t\r\nCMD hello\r\nQUIT\r\n
  │←── region 0 ──│←── region 1 ──│←── region 2 ──│← region 3 →│

AFLNet 根據協定的 delimiter（\r\n）自動切割。
Region mutation 的三種操作：

  1. 修改（mutate region r）：
     [R0][R1][*R2*][R3]  → 只對 R2 的位元組做 afl 風格的 mutation

  2. 插入（inject after region r）：
     [R0][R1][R2][NEW][R3]  → 在 R2 和 R3 之間插入一條新訊息

  3. 刪除（delete region r）：
     [R0][R1][R3]  → 刪掉 R2，看伺服器對少了一條訊息怎麼反應
```

---

## 進階用法

### 自訂 M2S 函數（binary 協定）

AFLNet 的 M2S 預設只支援文字協定（FTP/HTTP/SMTP/SIP/RTSP）。對 binary 協定，需要修改 AFLNet 源碼中的 `extract_response_codes()` 函數：

```c
/* aflnet.c 裡的 extract_response_codes()，預設版本 */
klist_t(llu) *extract_response_codes(unsigned char *buf, unsigned int buf_size,
                                     char *response_buf, unsigned int *state_count_list) {
    /* ... 解析 FTP/HTTP 等回應碼 ... */
}

/* 自訂 binary 協定版本（例如一個自訂的 4-byte status header）：
 *
 *  每條回應開頭是 4 bytes：[0x52][0x45][status_hi][status_lo]
 *  "RE" magic + 2-byte status code
 *
 * 把 status_hi << 8 | status_lo 當作狀態識別碼
 */
klist_t(llu) *extract_response_codes_custom(
        unsigned char *buf, unsigned int buf_size, ...) {
    klist_t(llu) *kl = kl_init(llu);
    unsigned int i = 0;
    while (i + 4 <= buf_size) {
        if (buf[i] == 0x52 && buf[i+1] == 0x45) {
            unsigned long long code = ((unsigned long long)buf[i+2] << 8) | buf[i+3];
            *kl_pushp(llu, kl) = code;
            i += 4;
        } else {
            i++;
        }
    }
    return kl;
}
```

修改完重新 build，然後用 `-P CUSTOM`（或直接 hardcode 呼叫自訂函數）執行。

### 狀態選擇策略選擇

`-q` 旗標控制 AFLNet 如何在狀態間分配 fuzzing budget：

```
-q 1（round-robin）：
  輪流選每個狀態的 seed。適合初期探索，不浪費 budget 在已飽和的狀態。

-q 2（exploration-first）：
  優先選 state_count 少的狀態的 seed。對深層協定狀態效果最好，
  因為深層狀態很少被觸及，集中打更容易突破。

-q 3（random）：
  隨機選狀態。通常不如 q2，但在狀態數量多、狀態分布不均時
  可以避免 q2 過度集中在某一個邊緣狀態。
```

實務上先用 `-q 2` 跑幾個小時，觀察狀態覆蓋分布，如果某個深層狀態始終無法突破再換策略。

### 重放 crash

AFLNet 把 crash 的訊息序列存成可重放格式，能直接驗證 bug：

```bash
# 用 GDB 重放一個 crash
gdb ./auth_server
(gdb) run 9999 &

# 另一個 terminal：
cat aflnet_out/replayable-crashes/id:000000,... | nc 127.0.0.1 9999
# → ASan 報告 stack-buffer-overflow in handle_client
```

這個重放功能是 AFLNet 比直接用 afl++ 方便的地方——afl++ 找到 crash 後你還要自己還原完整的 session 序列，AFLNet 直接幫你存好了。

### 協定狀態圖視覺化

AFLNet 在 `aflnet_out/` 目錄下會產生 DOT 格式的狀態圖，用 graphviz 渲染：

```bash
dot -Tpng aflnet_out/state_ipsm.dot -o state_graph.png
```

這個圖直接顯示 AFLNet 推斷出的協定狀態機，包括哪些狀態被觸及多少次、哪些轉換是發現 crash 的路徑。對不熟悉目標協定的情況下，這個圖也有逆向參考價值。

---

## 對比取捨

| 比較面向 | afl++（plain） | AFLNet |
|---------|---------------|--------|
| **輸入模型** | 單一 blob | 訊息序列（多條 message） |
| **狀態感知** | 無 | 有（M2S 回應碼映射） |
| **mutation 粒度** | byte-level | region-level（訊息級別）+ byte-level |
| **Corpus 組織** | 單一佇列 | 按協定狀態分區 |
| **目標類型** | 任何接受 stdin/file 的程式 | TCP/UDP 有狀態伺服器 |
| **協定覆蓋** | 廣（無限制） | 有限（需 M2S 支援，或自己實作）|
| **執行速度** | 快（in-process 可達 10k exec/s）| 慢（每次 TCP 連線 + fork，通常 1–100 exec/s）|
| **Setup 難度** | 低（寫 harness + 編譯）| 中（需要 seed PCAP + 協定知識）|
| **文字協定（FTP/HTTP）** | 差（序列起點就失敗） | 優（M2S 內建支援）|
| **binary 協定** | 差（同上）| 可以，但要自己改源碼 |
| **何時選 AFLNet** | — | 目標是有狀態的 TCP/UDP 伺服器，且協定有文字回應碼 |
| **何時不選 AFLNet** | — | 協定全 binary、需要高速 fuzzing、或目標已可 harness 成 in-process |

---

## 踩雷

- **錯誤直覺**：「PCAP seed 越多越好，錄一整段完整 session 塞進去」→ **正確認知**：大 seed 讓 region mutation 的空間爆炸——一個 200 條訊息的 session，AFLNet 要在 200 個 region 之間選目標，mutation 效率極低。能觸達目標狀態的**最小序列**遠優於完整的 session dump。做法：先手動找出到達每個關鍵狀態所需的最少訊息數，每條路徑存一個 seed，不要把所有路徑塞進同一個 seed。

- **錯誤直覺**：「Binary 協定就不能用 AFLNet，要換工具」→ **正確認知**：AFLNet 完全可以處理 binary 協定，但需要在 `aflnet.c` 裡實作自訂的 `extract_response_codes()` 函數。如果 binary 協定有固定的 status header（例如 4-byte magic + 2-byte status code），寫起來不複雜。另一個選擇是用 StateAFL（Ch 18），它不依賴回應碼，改用記憶體狀態 hash，對 binary 協定更友善。

- **錯誤直覺**：「AFLNet 跑很慢是 build 有問題或參數設錯」→ **正確認知**：AFLNet 固有地慢，因為每次 exec 都要 fork + exec 啟動新的 server 行程，再建立 TCP 連線，再送訊息序列，再讀回應，再 kill server。這個循環在本機能到 50–200 exec/s 就已經算快了。相比之下 afl++ in-process fuzzing 可以達到數萬 exec/s。解決速度問題的正確方法是 Ch 19 的 harness 化——把 server 改成 in-process 函數後再用 afl++ 打，AFLNet 的 TCP 連線開銷消失了。

- **錯誤直覺**：「`-P FTP` 旗標只能用在真正的 FTP server」→ **正確認知**：`-P FTP` 只是告訴 AFLNet 用 FTP 的回應碼格式（3 位數字開頭的文字行）來解析回應。任何回應格式和 FTP 相似的協定都可以用 `-P FTP`，包括自製的類 FTP 協定。本章的 auth_server 就是這樣——它的回應碼格式（`220 ...`、`331 ...`、`230 ...`）和 FTP 完全一樣，所以 `-P FTP` 能正確解析。

---

## 進階延伸

### AFLNet + ASAN + AddressSanitizer crash 分類

AFLNet 本身不做 crash 分類，但可以結合外部工具：

```bash
# 在 replayable-crashes 上跑 exploitable 分類
for crash in aflnet_out/replayable-crashes/id:*; do
    ./auth_server 9999 &
    cat "$crash" | nc -q 1 127.0.0.1 9999 2>&1 | grep -E 'ASAN|signal'
    kill %1
done
```

### 針對 live555 RTSP server（AFLNet 官方 demo）

AFLNet 論文的 demo target 是 live555，RTSP 協定比 FTP 複雜（有 DESCRIBE/SETUP/PLAY 狀態機）。Build 步驟：

```bash
# 下載 live555（特定版本，AFLNet 論文用的）
wget http://www.live555.com/liveMedia/public/live555-latest.tar.gz
tar xzf live555-latest.tar.gz && cd live

# 用 AFL_CC 編譯（讓 AFL instrumentation 插入）
CC=~/tools/aflnet/afl-clang-fast \
CXX=~/tools/aflnet/afl-clang-fast++ \
./genMakefiles linux && make -j$(nproc)

# seed：一個合法的 RTSP session
printf 'OPTIONS rtsp://127.0.0.1:8554/ RTSP/1.0\r\nCSeq: 1\r\n\r\n' \
       > rtsp_seeds/options_only
```

live555 的 build 和 run 步驟請以 AFLNet GitHub wiki 為準（下方延伸閱讀），本機未實測。

### 覆蓋 UDP 協定

AFLNet 也支援 UDP：`-N udp://127.0.0.1/PORT`。UDP 沒有連線狀態，M2S 的訊息/回應邊界更難界定，通常需要 `-D` 設得更大（10,000 μs 以上）給 server 足夠的處理時間。

---

## 動手練習

1. Build `auth_server.c` 並確認它能正確回應 `USER/PASS/CMD/QUIT`。用 `nc` 手動走一遍合法 session，記錄每條回應碼。

2. 把合法 session 存成 seed，觀察 seed 的位元組內容：哪些部分是 AFLNet 的 region boundary？用 `hexdump -C seed_001` 確認 `\r\n` 分隔符在位。

3. 如果你有裝 AFLNet，執行上面的指令，等待 10 分鐘後：
   - 觀察 `aflnet_out/queue/` 裡的 seed 按什麼命名分區
   - 觀察 terminal 上的狀態覆蓋統計，哪個狀態被觸及最少？
   - 如果找到 crash，用 `nc` 重放 `replayable-crashes/` 裡的 seed 確認可重現

4. 修改 `auth_server.c`，把 bug 從 `strcpy(cmd_buf, data)` 改成 `strncpy(cmd_buf, data, sizeof(cmd_buf) - 1)`——這樣 CMD 的 bug 就修掉了。重跑 AFLNet，觀察 crash 消失後，AFLNet 是否仍然繼續探索新的覆蓋路徑。

5. 思考題：如果 `auth_server` 的密碼不是 hardcode 的 `s3cr3t`，而是從外部檔案讀取，AFLNet 用這套 seed 能找到 PASS 正確的路徑嗎？為什麼？（提示：想想 seed 裡的 `s3cr3t` 在 mutation 時會發生什麼。）

---

## 本章重點

- AFLNet 的核心是把輸入模型從 blob 改成**訊息序列**（message sequence），mutation 在訊息粒度上操作，而不是整個 buffer。
- **M2S 映射**用伺服器的回應碼作為協定狀態的代理——不需要預先定義狀態機，只要協定有文字回應碼就能自動推斷。
- Corpus 按**狀態分區**，配合 `-q` 策略把 budget 優先分配給低覆蓋的深層狀態。
- AFLNet 固有地慢（每次 TCP + fork），接受這個開銷或改用 Ch 19 的 in-process harness。
- Binary 協定需要自訂 M2S 函數；或換 StateAFL（Ch 18）用記憶體狀態 hash 繞過回應碼解析的限制。
- 最小 seed 優於塞滿的 session dump——mutation 空間小才打得進去。

---

## 自我檢核

- [ ] M2S 映射的輸入是什麼、輸出是什麼？它依賴什麼假設？
- [ ] AFLNet 的 region mutation 和 afl++ 的 havoc mutation 有什麼本質差異？
- [ ] 為什麼一個 200 條訊息的 PCAP seed 比一個 5 條訊息的 seed 更難 mutate？
- [ ] `-P FTP` 旗標的實際作用是什麼？對非 FTP 的伺服器能用嗎？
- [ ] AFLNet 執行速度慢的根本原因是什麼？如何根本解決？
- [ ] Binary 協定有哪兩種接進 AFLNet 的方法？各自的代價是什麼？
- [ ] AFLNet 的 `replayable-crashes/` 比 afl++ 的 `crashes/` 多了什麼資訊？

---

## 延伸閱讀

1. **Van-Thuan Pham, Marcel Böhme, Andrew E. Santosa, Alexandru Razvan Caciulescu, Abhik Roychoudhury. "AFLNet: A Greybox Fuzzer for Network Protocols." ICST 2021.**
   — §3 "Design" 完整說明 M2S 映射的推導方式和 seed corpus 的狀態分區邏輯；§4 "Evaluation" 對 live555/LightFTP/TinyDTLS 的實測數據直接回答「AFLNet 比 afl++ 多找多少 bug」的問題；§5 的 limitations 段落誠實說明了回應碼代理的限制，是理解本章踩雷 2 的原始資料。

2. **Roberto Copik, Giorgi Matiashvili, Flavio Toffalini, Andrea Giallanza, Mathias Payer, Edouard Bugnion. "StateAFL: Greybox Fuzzing for Stateful Network Servers." arXiv 2022.**
   — §2 說明 M2S 回應碼方式的根本限制（binary 協定、同碼多狀態問題）；§3 提出用記憶體快照 hash 替代回應碼來做狀態推斷——這是 Ch 18 的核心，在讀 Ch 18 前先掃這篇的 §2–§3 能讓你對比兩套方法的設計取捨。

3. **AFLNet GitHub Wiki（https://github.com/aflnet/aflnet/wiki）**
   — "Tutorial: Fuzzing Live555 media server" 章節給出完整的 RTSP target build 步驟，包括 AFL instrumentation 的編譯旗標和 RTSP seed 的準備方式；"Extending AFLNet" 章節說明如何在 `aflnet.c` 裡新增自訂的 `extract_response_codes()` 函數——對 binary 協定和本章踩雷 2 的解法直接對應。

4. **Marcel Böhme, Van-Thuan Pham, Manh-Dung Nguyen, Abhik Roychoudhury. "Directed Greybox Fuzzing." CCS 2017.**
   — 不直接關於 AFLNet，但 AFLNet 的狀態感知 seed 選擇策略（優先打覆蓋少的狀態）在設計上和有向 greybox fuzzing 的「distance-based power schedule」共享同一個思路。Ch 43 的 AFLGo 章會回來看這篇；現在先掃 §2 的 power schedule 定義，理解 AFLNet 的 `-q 2` 策略為什麼比 round-robin 更有效。

---

AFLNet 把 afl++ 的覆蓋率引擎延伸到了網路協定，但回應碼代理的假設對 binary 協定來說太脆弱。Ch 18 的 StateAFL 用記憶體快照 hash 徹底繞開這個假設。

→ [下一章](./18-stateafl-state-representation.md)
