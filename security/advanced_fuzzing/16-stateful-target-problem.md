# Ch 16 — Stateful 目標的難題

> **目標**: 理解為什麼傳統 coverage-guided fuzzing 在 stateful 協定目標上會系統性失敗，掌握三個根本問題（輸入是訊息序列、forkserver 模型重置狀態、coverage 永遠被鎖在 handshake 閘前），能用真實 echo+auth server 示範問題所在，並為 Part 3 的 stateful fuzzing 解法建立正確的問題意識。

---

## 為什麼需要這章

你用 afl++ 對一個 FTP server 跑了 24 小時，coverage 幾乎不動。

不是因為 target 太簡單——FTP 有完整的 state machine，有 path traversal、有整數溢位、有 format string，CVE 記錄一頁翻不完。問題是 afl++ 根本沒有把 payload 送到那些程式碼面前。

這章的目的是把這個失敗模式說清楚。不是「stateful fuzzing 比較難」這種廢話，而是具體說明三個失敗原因、每個失敗在什麼地方卡住，以及為什麼這些問題在 afl++ 的架構下無法自行修復。

搞懂這一章，Part 3 的所有技術（AFLNet、StateAFL、snapshot fuzzing）才有意義——它們解決的是這裡描述的具體問題，不是抽象的「stateful 很難」。

---

## 先建立直覺

stateless fuzzer 眼中，target 的樣子：

```
afl++ 的假設:

  一份 input file
       │
       ▼
  ┌─────────────┐
  │   target    │  ← 讀 stdin 或 argv 或 file
  └─────────────┘
       │
       ▼
  crash 或 exit

  下次: 另一份 mutated input file, 重跑
```

協定 server 的實際樣子：

```
真實 FTP server 的 state machine:

  Client                    Server State
  ──────                    ────────────
  (connect)            →    WAIT_USER
  "USER anonymous\r\n" →    WAIT_PASS
  "PASS foo\r\n"       →    AUTHENTICATED
  "LIST /\r\n"         →    ← 列目錄 (這裡才有 parser, 才有 bug)
  "RETR sensitive.txt" →    ← path traversal 在這裡
  "STOR exploit.bin"   →    ← write 漏洞在這裡

  afl++ 看到的:
  ┌─────────────────────────────────────────────────┐
  │  [mutated blob] → server → "530 Not logged in" │
  │                            ↑                   │
  │                     永遠在這裡                  │
  └─────────────────────────────────────────────────┘
```

問題不是 mutation 不夠聰明，是 fuzzer 的輸入模型根本是錯的。

---

## 核心概念

### 問題一：輸入是訊息序列，不是單一 blob

afl++ 的輸入模型：一個 `uint8_t[]`，讀一次，執行完，done。

協定的輸入模型：一個訊息序列 `[msg_0, msg_1, msg_2, ...]`，每個訊息的合法性取決於當前狀態，而狀態由前面所有訊息決定。

```
stateless input model:
  input = bytes[N]

stateful input model:
  input = [(msg_type_0, payload_0),
           (msg_type_1, payload_1),   ← 只有在 state_1 下才合法
           (msg_type_2, payload_2)]   ← 只有在 state_2 下才有趣的 parser
```

afl++ 把整個序列當成一個 blob 去 mutate，會做這些事：

- 把 `USER anonymous\r\n` 的 `n` 改成 `\x00`，server 拒絕，done
- 插入一個 byte 讓 `USER` 變成 `XSER`，server 拒絕，done
- 把兩個訊息的中間剪掉，server 在狀態 0 收到狀態 2 的訊息，拒絕，done

每次 mutation 都以高概率打爛前綴的結構，讓 server 在第一個 message 就拒絕。後面所有的 code path 永遠看不到。

### 問題二：forkserver 把狀態重置了

afl++ 的 forkserver 模型：

```
forkserver 執行流程:

  target 啟動
       │
  forkserver 初始化 (execve + __AFL_INIT)
       │
  ┌────▼──────────────────────┐
  │  等待 fuzzer 指令          │
  └────────────┬──────────────┘
               │  fork()
               ▼
  ┌─────────────────────────┐
  │  child process          │
  │  讀 input, 執行, exit   │
  └─────────────────────────┘
               │
  回到 parent, 等下一輪 fork
```

對 file-reading target，這個模型是完美的：每次 fork 出一個乾淨的子程序，讀一份 input，執行完死掉，狀態完全隔離，速度快，crash 自動回收。

對 stateful server，這個模型破掉了：

```
問題場景：需要 3 個 TCP exchange 才能到達的 state

  理想情況:
    exchange_0: CLIENT→ "HELLO\n" → server 進入 state_1
    exchange_1: CLIENT→ "AUTH s3cr3t\n" → server 進入 state_2
    exchange_2: CLIENT→ "ECHO <fuzz_payload>\n" → 打到真正的 parser

  forkserver 現實:
    每次 fork → 全新 process → state = 0
    fuzzer 送一個 blob → server 收到 → state 0 的 handler 處理 → exit

  無論 blob 長什麼樣，server 永遠在 state 0 處理它。
  state_2 的 parser 永遠不會被執行。
```

根本原因：forkserver 的 fork 點在程式啟動時，而不是在已建立 session 之後。server 在狀態機深處積累的 state（已通過 auth、session context、已分配的資源）在每次 fork 之後都不存在。

### 問題三：Coverage 永遠被鎖在 handshake 閘前

前兩個問題導致一個直接後果：afl++ 的 coverage map 裡，只有 handshake handler 的 edge 會被觸發。

```
典型協定 server 的 coverage 分佈（afl++ 直打）:

  state_0 handler (WAIT_HELLO)     ████████████████████████ 99%
  state_1 handler (WAIT_AUTH)      ▌                         < 1%
  state_2 handler (AUTHENTICATED)  (完全空白)                 0%

  原因: 幾乎所有 mutated blob 都在 state_0 被拒絕
```

coverage-guided fuzzing 的核心引擎在這裡完全失效：它靠新的 edge 引導 mutation 方向，但如果新 edge 永遠出現在同一個 handler 裡，它就只能在 state_0 的 code 裡探索，永遠到不了 state_2。

這不是 coverage 密度的問題，是 coverage 結構的問題：那些有趣的 edge（反序列化 bug、command parser、業務邏輯）被 handshake gate 擋在後面，而 fuzzer 沒有「先送合法的 HELLO、再送合法的 AUTH、然後才 fuzz ECHO payload」這個概念。

---

## 底層機制

### 用真實 server 示範

以下是一個自包含的 echo+auth server，state machine 清晰，有意埋入一個漏洞在 state_2 的 handler 裡：

```c
/* server.c — stateful echo server (教育用途，CVE hunting 示範)
 *
 * State machine:
 *   WAIT_HELLO (0) → 收到 "HELLO\n"       → WAIT_AUTH (1)
 *   WAIT_AUTH  (1) → 收到 "AUTH s3cr3t\n" → AUTHENTICATED (2)
 *   AUTHENTICATED  → 收到 "ECHO <msg>\n"  → 回應 msg (含故意漏洞)
 *
 * 編譯:
 *   gcc -g -fsanitize=address server.c -o server
 *
 * 執行:
 *   ./server 12345
 *
 * 測試 (另一個 terminal):
 *   echo -e "HELLO\nAUTH s3cr3t\nECHO hello world" | nc 127.0.0.1 12345
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <netinet/in.h>
#include <sys/socket.h>

#define STATE_WAIT_HELLO    0
#define STATE_WAIT_AUTH     1
#define STATE_AUTHENTICATED 2

#define BUF_SIZE  256
#define ECHO_SIZE 64    /* intentionally small: 故意設小觸發漏洞 */

static void handle_client(int fd) {
    int state = STATE_WAIT_HELLO;
    char line[BUF_SIZE];
    char echo_buf[ECHO_SIZE];  /* 漏洞在這裡 */
    FILE *fp = fdopen(fd, "r+");
    if (!fp) { close(fd); return; }

    while (fgets(line, sizeof(line), fp)) {
        /* 去掉 \n */
        size_t len = strlen(line);
        if (len > 0 && line[len-1] == '\n') line[--len] = '\0';

        switch (state) {

        case STATE_WAIT_HELLO:
            if (strcmp(line, "HELLO") == 0) {
                fputs("200 OK\n", fp);
                fflush(fp);
                state = STATE_WAIT_AUTH;
            } else {
                fputs("500 Expected HELLO\n", fp);
                fflush(fp);
            }
            break;

        case STATE_WAIT_AUTH:
            /* 格式: "AUTH <password>" */
            if (strncmp(line, "AUTH ", 5) == 0) {
                const char *pw = line + 5;
                if (strcmp(pw, "s3cr3t") == 0) {
                    fputs("200 Authenticated\n", fp);
                    fflush(fp);
                    state = STATE_AUTHENTICATED;
                } else {
                    fputs("401 Wrong password\n", fp);
                    fflush(fp);
                }
            } else {
                fputs("500 Expected AUTH\n", fp);
                fflush(fp);
            }
            break;

        case STATE_AUTHENTICATED:
            /* 格式: "ECHO <message>"
             * 漏洞: memcpy 沒有檢查 msg 長度對 echo_buf (64 bytes) 的溢位
             * 攻擊: ECHO + 超過 64 bytes 的 payload → stack overflow
             */
            if (strncmp(line, "ECHO ", 5) == 0) {
                const char *msg = line + 5;
                size_t msg_len = strlen(msg);
                memcpy(echo_buf, msg, msg_len);  /* 漏洞: 未檢查 msg_len */
                echo_buf[msg_len] = '\0';
                fprintf(fp, "ECHO %s\n", echo_buf);
                fflush(fp);
            } else {
                fputs("500 Unknown command\n", fp);
                fflush(fp);
            }
            break;
        }
    }

    fclose(fp);
}

int main(int argc, char *argv[]) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s <port>\n", argv[0]);
        return 1;
    }
    int port = atoi(argv[1]);

    int srv = socket(AF_INET, SOCK_STREAM, 0);
    int opt = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    struct sockaddr_in addr = {
        .sin_family      = AF_INET,
        .sin_addr.s_addr = INADDR_ANY,
        .sin_port        = htons(port),
    };
    bind(srv, (struct sockaddr *)&addr, sizeof(addr));
    listen(srv, 5);
    fprintf(stderr, "listening on port %d\n", port);

    for (;;) {
        int cfd = accept(srv, NULL, NULL);
        if (cfd < 0) continue;
        handle_client(cfd);
    }
}
```

編譯並驗證漏洞在正確位置：

```bash
# 編譯（ASAN 追蹤 memcpy 溢位）
gcc -g -fsanitize=address server.c -o server

# terminal 1: 啟動 server
./server 12345

# terminal 2: 正常路徑（抵達 state_2）
printf "HELLO\nAUTH s3cr3t\nECHO hello world\n" | nc 127.0.0.1 12345
# 預期輸出:
# 200 OK
# 200 Authenticated
# ECHO hello world

# terminal 2: 直接送垃圾（被 state_0 擋住）
printf "XXXXXXXXXXXXXXXXXXXXXXXXXXX\n" | nc 127.0.0.1 12345
# 預期輸出:
# 500 Expected HELLO

# terminal 2: 觸發漏洞（需要先過 state_0 和 state_1）
python3 -c "print('HELLO'); print('AUTH s3cr3t'); print('ECHO ' + 'A'*200)" \
    | nc 127.0.0.1 12345
# 預期: ASAN 噴出 heap-buffer-overflow (echo_buf 是 stack，
#        ASAN 會報 stack-buffer-overflow，帶完整 call stack)
```

### 驗證 afl++ 的 coverage 被鎖

**本段未實測，為理論預期行為。** 實際驗證步驟：

1. 用 `afl-clang-fast` 重編 server（加 AFL instrumentation）
2. 準備 seed：一個包含 `HELLO\n` 的單一 text 檔案
3. 用 `afl-fuzz` 的 persistent mode 對 server 跑 30 分鐘
4. 執行後看 `afl-whatsup` 輸出，比較 `total edges found` 與手動走完三個 exchange 後 `afl-showmap` 的 edge 數

預期差距：

```
手動走完 HELLO → AUTH → ECHO 的完整路徑:
  afl-showmap 輸出 ≈ 120-180 edges
  （包含 state_0、state_1、state_2 所有分支）

afl++ 直接 fuzz 30 分鐘:
  total edges found ≈ 30-50 edges
  （只有 state_0 handler 的各個分支，state_2 的 0 edges）

差距: state_2 裡的漏洞 memcpy 那條 edge, afl++ 永遠找不到
```

---

## 形式化分析：為什麼單一 input 到不了深層狀態

### State Machine 的形式化描述

協定 server 的狀態轉移可以形式化：

```
State Machine M = (S, Σ, δ, s₀, F)

  S  = 狀態集合    {WAIT_HELLO, WAIT_AUTH, AUTHENTICATED}
  Σ  = 輸入字母    所有合法/非法的 message
  δ  = 轉移函數    δ(state, msg) → next_state
  s₀ = 初始狀態    WAIT_HELLO
  F  = 接受狀態    {AUTHENTICATED}

真正有趣的 code 只在 F (接受狀態) 中執行。

要打到 F 中的 code，必須走一條有效路徑:
  s₀ →[HELLO]→ WAIT_AUTH →[AUTH s3cr3t]→ AUTHENTICATED

Coverage of AUTHENTICATED state 需要:
  coverage(AUTHENTICATED) = coverage(path: s₀ →* AUTHENTICATED)
  不是 coverage(single_msg), 是 coverage(msg_sequence)
```

這個形式化說明了為什麼單一 blob fuzzing 在結構上無法解決問題：

- fuzzer 需要找到一條到達 AUTHENTICATED 的「合法前綴序列」
- 這條序列的每個 message 必須被前一個 state 接受
- 純 mutation 在統計意義上極難生成一個符合多個 state 條件的序列

### 為什麼 forkserver 在 file target 工作，在 server 失敗

```
File-reading target (正確使用 forkserver):

  program start
       │
  ┌────▼────────────────┐   ← fork 點（forkserver hook 在這裡）
  │  global init         │
  └────────────────────┘
       │
  fork()
       │
  ┌────▼────────────────┐
  │  open(argv[1])       │   ← 每次讀不同的 input file
  │  parse(content)      │
  │  exit()              │
  └────────────────────┘

  fork 點之前: 幾乎沒有可變狀態（只有 global init）
  每個 fork child 看到的初始狀態: 完全一致


Stateful server (forkserver 失效):

  server start
       │
  ┌────▼────────────────┐   ← forkserver hook 在這裡（程式啟動時）
  │  listen() 等待連線   │
  └────────────────────┘
       │  accept()
       │  ←── client 連進來
       │
  ┌────▼────────────────┐
  │  recv "HELLO\n"      │   ← state 轉移發生在這裡
  │  state = WAIT_AUTH   │
  │  recv "AUTH ...\n"   │   ← 再次轉移
  │  state = AUTH        │
  └────────────────────┘
       │
  問題: forkserver 在程式啟動時 fork，
        server 的 state 建立在 accept() 之後
        → fork 出的每個 child 都是 state_0
        → 3 個 exchange 積累的 session 狀態不存在
```

正確的 fork 點應該在 server 已經完成 N 個合法 exchange、session 建立之後。這正是 snapshot fuzzing（Ch 28-31）要做的事：在已知 session 狀態下建立 snapshot，fuzzer 從這個 checkpoint 繼續執行，不需要重放整個前綴序列。

---

## 進階用法

### 手動構造前綴 + 只 fuzz 最後一個訊息

最粗暴的解法，在 AFLNet 等工具出現之前常用：

```python
# fuzz_wrapper.py
# 對 ECHO command 做 fuzzing，但先手動送合法的前綴

import socket, sys, subprocess, random, struct

TARGET_HOST = "127.0.0.1"
TARGET_PORT = 12345

def fuzz_one(payload: bytes):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((TARGET_HOST, TARGET_PORT))
    s.sendall(b"HELLO\n")
    resp = s.recv(256)
    if b"200" not in resp:
        s.close()
        return "failed_hello"
    s.sendall(b"AUTH s3cr3t\n")
    resp = s.recv(256)
    if b"200" not in resp:
        s.close()
        return "failed_auth"
    # 只 fuzz 這個 message
    s.sendall(b"ECHO " + payload + b"\n")
    try:
        resp = s.recv(256)
    except:
        return "crash"
    s.close()
    return resp

# 簡單隨機 mutation
seed = b"hello world"
for i in range(1000):
    payload = bytearray(seed)
    # 隨機插入 byte
    pos = random.randint(0, len(payload))
    payload.insert(pos, random.randint(0, 255))
    result = fuzz_one(bytes(payload))
    if result == "crash":
        print(f"[!] crash with payload: {bytes(payload)[:64]}")
        break
```

這個方法的問題：沒有 coverage guidance、速度受 TCP 開銷限制（每輪都要 connect/handshake）、無法探索跨多個訊息的狀態轉移組合。這只是「能跑」的 baseline，不是正確解法。

### 前綴插入 + Coverage 引導（AFLNet 的思路）

AFLNet（Ch 17）的核心改進：把輸入格式改成「訊息序列」，mutation 在序列層面操作（插入、刪除、替換整個 message），同時追蹤每個 response code 對應的 state，用 state coverage 作為引導訊號，而不只是 code coverage。

---

## 對比取捨

| 方法 | 能到達的 State | Coverage 引導 | 速度 | 適合場景 |
|------|--------------|--------------|------|---------|
| afl++ 直打 | state_0 only | code coverage | 快 | 無 stateful 目標 |
| 手動前綴腳本 | 指定 state | 無 | 慢（TCP 開銷） | 快速 PoC 驗證 |
| AFLNet | 所有 state | state + code | 中 | TCP/UDP 協定 |
| Snapshot fuzzing | 任意 checkpoint | code coverage | 快 | 需要 hypervisor/ptrace |
| StateAFL | 所有 state | network state | 中 | 需要 state inference |

---

## 踩雷

**踩雷 1：「seed 裡放完整的合法 session，afl++ 就能 fuzz 到深層 state」**

錯誤直覺：seed 是 `HELLO\nAUTH s3cr3t\nECHO hello\n`，afl++ 一定會 mutate ECHO 部分然後觸發漏洞。

正確認知：afl++ 不知道這個 blob 是「三個訊息」。它的 mutation 是 byte-level 的：插一個 byte 進 `HELLO` → `HELxLO` → state_0 handler 拒絕，整個 session 失敗。大多數 mutation 都會打爛前綴，機率上只有極少數 mutation 恰好只修改了 `ECHO` 後面的 payload 且保持前綴完整。這種機率不是零，但低到讓 coverage progress 幾乎停止。

**踩雷 2：「forkserver 加在 accept() 之後就能解決問題」**

錯誤直覺：如果把 `__AFL_INIT()` 放在 server 接受第一個連線之後，fork 出的 child 就帶著已建立的連線狀態，可以繼續讀 fuzz input。

正確認知：這個思路有部分對，但需要 persistent mode 而不是普通 forkserver，而且 TCP socket 在 fork 之後的行為很微妙（fd 被 child 繼承，但 server 端的 socket state 包含 TCP sequence number、nagle buffer 等 kernel-side 狀態，不是 fork 能完全複製的）。正確的做法是使用 Unix domain socket 或 pipe 重構 server 的 IO 層，讓 fuzzer 繞過 TCP，直接對 parser 函數做 in-process fuzzing——這就是 harnessing（Ch 19）的主題。

**踩雷 3：「提高 mutation 次數就能覆蓋到 state_2」**

錯誤直覺：跑足夠久，隨機 mutation 總能生成一個「恰好合法」的前綴。

正確認知：對 `AUTH s3cr3t\n` 這個 15 個 byte 的完全匹配來說，隨機生成的機率是 (1/256)^15，大約 10^-36。就算 1 GHz 的 fuzzing throughput，宇宙年齡也不夠。Coverage-guided fuzzing 會試著接近這個 sequence（因為 `AU` 比 `XY` 能到達更深的分支），但字串比較本身對 coverage 不友好——`strcmp` 要麼完全通過要麼完全失敗，中間沒有 edge 可以引導。這是 AFL++ 的比較覆蓋（cmplog）要解決的問題，但在 stateful context 裡 cmplog 的效果也很有限，因為能打到 `strcmp("AUTH s3cr3t")` 的前提是已經在 state_1。

---

## 進階延伸

**Snapshot 作為根本解法**

正確的 fork 點應該在 server 完成所有前綴 exchange、進入目標 state 之後。這就是 snapshot fuzzing（Ch 28）的核心思路：用 hypervisor 或 ptrace 在任意時刻對程序拍快照，之後每次 fuzzing 從快照 restore，不重放前綴。對 echo+auth server 示範：snapshot 在 `state == STATE_AUTHENTICATED` 之後，fuzzer 從這個點開始，只 fuzz `ECHO` 後面的 payload。

**Protocol State Inference（StateAFL 的思路）**

不需要人工標記 state machine，從 server 的 response 推斷狀態：`200 OK`、`401 Wrong password`、`500 Expected HELLO` 這些 response code 天然是 state 的觀測視窗。StateAFL 用 response 的 hash 作為「推斷 state」，coverage 追蹤改為「(code_edge, inferred_state) 的 pair」，讓 mutation 能往未覆蓋的 state 走。

**與 cmplog 的交互**

afl++ 的 cmplog（比較日誌）可以抓到 `strcmp(input, "s3cr3t")` 這種比較，從 input 推斷應該輸入什麼。在 stateful context 裡，cmplog 能幫忙猜對 password，但前提是 fuzzer 的輸入有機會到達 `strcmp` 那一行——也就是已經在 state_1。cmplog 解決的是「知道要過，但不知道密碼是什麼」的問題，不解決「根本到不了那行 code」的問題。這兩個問題必須分開處理。

---

## 動手練習

1. **編譯並手動驗證 state machine**：把本章的 `server.c` 用 `gcc -g -fsanitize=address server.c -o server` 編譯，用 `nc` 走完三個 exchange，確認能觸發 ASAN 的 stack-buffer-overflow。記錄 ASAN 輸出裡的 crash address 和 call stack。

2. **驗證 coverage 被鎖**：用 `afl-clang-fast` 重編 server（`afl-clang-fast -g server.c -o server_afl`），用 `afl-showmap -o /dev/null -- ./server_afl 12345` 配合 `echo "HELLO" | nc` 記錄只送 HELLO 的 edge 數，再配合完整三步 exchange 記錄 edge 數，比較差距。

3. **撰寫手動前綴 fuzzer**：參考本章的 `fuzz_wrapper.py`，對 `ECHO` payload 實作一個「每次插入一個隨機 byte」的 mutation loop，觀察第幾次能觸發 ASAN 報出 crash。記錄觸發 crash 的最短 payload 長度（答案：超過 64 bytes — `ECHO_SIZE` 的值）。

4. **思考 forkserver 改造**：如果你要把 `handle_client()` 改造成 AFL persistent mode harness（讓 fuzzer 直接對 `handle_client` 的已認證 session 做 in-process fuzzing），需要修改哪幾行？寫出你的計劃（不需要實際跑通，下一章 AFLNet 提供正確解法）。

---

## 本章重點

- afl++ 的輸入模型是單一 blob，協定 server 的輸入模型是訊息序列；這個不匹配是結構性的，不是 mutation 策略的問題
- forkserver 在程式啟動時 fork，server 的 session state 建立在之後的多個 exchange；fork 點錯誤導致每次 fuzzing 都從 state_0 開始
- coverage 永遠被 handshake gate 鎖住：深層 state 的 edge 在 afl++ 的 coverage map 裡是零
- 「seed 裡放合法 session 序列」不能解決問題，因為 byte-level mutation 以高概率打爛前綴
- 正確解法方向：序列層級的 mutation（AFLNet）、snapshot（Ch 28）、in-process harness 繞過 TCP 層（Ch 19）——三條路解決的是同一個問題的不同面向

---

## 自我檢核

- [ ] 你能用一句話說清楚「stateless input model」和「stateful input model」的差異嗎？
- [ ] forkserver 為什麼在 file-reading target 上工作正確，在 stateful server 上失敗？fork 點的問題在哪？
- [ ] 在本章的 echo+auth server 中，漏洞在哪一行？為什麼 afl++ 直打找不到它？
- [ ] 「seed 裡放合法的完整 session 序列，afl++ 就能 fuzz 到深層」——這個想法錯在哪裡？
- [ ] coverage 被鎖在 state_0 這件事，用 `afl-showmap` 要怎麼驗證？
- [ ] snapshot fuzzing 的「正確 fork 點」是哪裡？和 forkserver 的 fork 點差在哪？

---

## 延伸閱讀

1. **"AFLNet: A Greybox Fuzzer for Network Protocols"** — Pham et al., ICST 2020
   優先讀 §2 Background 和 §3 Design。這篇論文把本章描述的三個失敗模式（序列 vs blob、state reachability、coverage capped at handshake）用形式語言精確定義，然後提出對應的解法。讀完本章再讀這篇，你會看到每個設計決定背後的問題意識。AFLNet 是 Part 3 的核心工具，論文是理解它為什麼這樣設計的必要背景。

2. **"SNAPFUZZ: High-Throughput Fuzzing of Network Applications"** — Andronidis & Cadar, ISSTA 2022
   優先讀 §3 Motivation 的 Figure 1-3。這篇量化了 TCP 開銷對 stateful server fuzzing 吞吐量的影響，並用 snapshot 繞過 TCP handshake。對理解「snapshot 解決的是哪個層次的問題」有直接幫助，和 Ch 28-31 的 snapshot fuzzing Part 直接呼應。

3. **"StateAFL: Greybox Fuzzing for Stateful Network Servers"** — Natella, EMSE 2022
   優先讀 §4 Approach 的 state inference 部分。StateAFL 不需要人工標記 state machine，從 network message 的結構特徵自動推斷 server 狀態。這篇展示了「不假設任何 protocol knowledge」的 stateful fuzzing 是可行的，是本章問題的另一條解法路線，和 AFLNet 的「需要 protocol-aware mutation」形成對比。

---

stateless fuzzer 在 stateful target 上的失敗是確定性的，不是機率問題。本章建立的三個失敗模式（序列 vs blob、fork 點錯誤、coverage 鎖死）是 Part 3 所有工具的設計基礎——理解問題是選對工具的前提。

→ [下一章](./17-aflnet.md)
