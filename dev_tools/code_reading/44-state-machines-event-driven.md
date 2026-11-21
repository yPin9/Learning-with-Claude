# Ch 44 — state machine 與事件驅動架構

> **目標**：在程式碼裡認出 state machine 的三種寫法（switch on state、轉移表、單一轉移函式），能把它還原成狀態圖；能讀懂事件驅動程式碼，知道「沒有 main loop 的 kernel」和「有 main loop 的 Redis」差在哪裡，並用「事件來源 → handler 對應表」把一段事件驅動程式碼攤平。

> **鴻溝**：本章主要練**間接鴻溝**（事件驅動讓控制流變成「誰在什麼時候呼叫這個 callback」）與**目標鴻溝**（面對兩三百行的 `switch`，不帶問題讀會淹死）。

> **環境**：Linux kernel v6.18 原始碼；Redis 7.2.17 原始碼（`~/code_reading/redis-7.2.17`）；WSL2 Ubuntu 22.04（WSL kernel `6.18.58.1-microsoft-standard-WSL2`）、gcc 11.4、Python 3.10、iproute2 的 `ss`。

## 為什麼需要這個？

打開 `net/ipv4/tcp_input.c` 的 `tcp_rcv_state_process`（第 6910 行），你會看到一個大約 270 行的函式，裡面有三個 `switch (sk->sk_state)`，夾著一堆 `goto discard`、`goto consume`。逐行讀下去，讀到一半就忘了前面在幹嘛。

問題不在 C，在**架構**。這個函式是一個 state machine 的轉移邏輯，而 state machine 的資訊組織方式和程式碼的線性排列天生不合：狀態圖是二維的（狀態 × 事件），程式碼是一維的。Spinellis 在原書 Ch 9.2「Control Models」把系統的控制模型分成幾種：集中式的呼叫／返回、事件驅動（event-driven）、以及以 state transition 描述行為的系統。讀碼時第一件事是**認出是哪一種**，因為每一種都有不同的「正確讀法」：

- 呼叫／返回系統，從入口往下讀呼叫圖（Ch 5）。
- state machine，**先找出狀態集合和轉移點，畫出狀態圖，再回頭讀每條轉移的細節**。
- 事件驅動系統，**先列出所有事件來源和對應的 handler**，再挑一條事件路徑讀。

用錯讀法是讀不懂的常見原因。拿讀呼叫圖的方法讀 state machine，會在 `switch` 的分支裡迷路；拿讀 state machine 的方法讀事件驅動系統，會找不到「下一步」在哪裡，因為下一步取決於外部世界什麼時候發生什麼事。

## 先建立直覺

state machine 是一台自動販賣機：

```
         投幣               選商品
  ┌──────┐ ───────► ┌──────────┐ ───────► ┌──────────┐
  │ IDLE │          │ HAS_COIN │          │ VENDING  │
  └──────┘ ◄─────── └──────────┘          └──────────┘
     ▲      退幣鈕                              │
     └──────────────── 商品掉出 ─────────────────┘
```

它有三個要素：**狀態**（現在在哪）、**事件**（發生了什麼）、**轉移**（在某狀態收到某事件時做什麼、變成什麼狀態）。同一個事件在不同狀態下意義不同：在 IDLE 按退幣鈕什麼都不會發生，在 HAS_COIN 按就會退錢。

事件驅動是販賣機的**外殼**：販賣機不會主動做任何事，它在等事件。誰把事件送進來？硬幣感應器、按鈕、計時器。事件驅動架構關心的是「事件從哪來、怎麼送到正確的 handler」；state machine 關心的是「handler 收到事件後，依目前狀態做什麼」。兩者常常一起出現：**事件驅動決定什麼時候進入程式碼，state machine 決定進來之後走哪條路。** TCP 就是兩者的合體：封包抵達是事件，`sk_state` 決定怎麼處理。

## 從程式碼辨識 state machine

state machine 在 C 程式碼裡有固定的指紋。看到下面任何一個，就切換到「畫狀態圖」模式：

| 指紋 | 例子 | 讀法 |
|---|---|---|
| 一個列舉，成員名字像狀態（`*_STATE_*`、`ST_*`、形容詞或過去分詞） | `TCP_ESTABLISHED`、`TCP_FIN_WAIT1` | 這就是狀態集合，抄下來當狀態圖的節點 |
| struct 裡有一個 `state` 欄位，型別是上面那個列舉 | `sk->sk_state` | 這是「目前狀態」 |
| `switch (x->state)` | `tcp_rcv_state_process` | 每個 `case` 是一個狀態下的事件處理 |
| 一個集中的 `set_state()` 函式 | `tcp_set_state()` | **所有轉移的咽喉點**，從它的呼叫者反推出所有邊 |
| 一個以狀態為索引的陣列 | `new_state[]` | 轉移表，可以直接畫成圖 |
| 一組以位元表示狀態集合的旗標 | `TCPF_ESTABLISHED \| TCPF_CLOSE_WAIT` | 「在這些狀態中的任一個」的判斷 |

### 第一個指紋：狀態列舉

```c
/* include/net/tcp_states.h:12（v6.18） */
enum {
	TCP_ESTABLISHED = 1,
	TCP_SYN_SENT,
	TCP_SYN_RECV,
	TCP_FIN_WAIT1,
	TCP_FIN_WAIT2,
	TCP_TIME_WAIT,
	TCP_CLOSE,
	TCP_CLOSE_WAIT,
	TCP_LAST_ACK,
	TCP_LISTEN,
	TCP_CLOSING,	/* Now a valid state */
	TCP_NEW_SYN_RECV,
	TCP_BOUND_INACTIVE, /* Pseudo-state for inet_diag */

	TCP_MAX_STATES	/* Leave at the end! */
};

#define TCP_STATE_MASK	0xF

#define TCP_ACTION_FIN	(1 << TCP_CLOSE)

enum {
	TCPF_ESTABLISHED = (1 << TCP_ESTABLISHED),
	TCPF_SYN_SENT	 = (1 << TCP_SYN_SENT),
	...
```

幾個值得注意的細節，每一個都是 [Ch 2](./02-program-comprehension-models.md) 說的 beacon：

- **從 1 開始**：`TCP_ESTABLISHED = 1`。0 被留給「無效狀態」，`tcp.c` 的轉移表第 0 格就註明 `(Invalid)`。
- **比 RFC 多出來的狀態**：RFC 793／9293 定義了 11 個狀態，這裡多了 `TCP_NEW_SYN_RECV`（Linux 用輕量的 request sock 處理半開連線時的狀態）與 `TCP_BOUND_INACTIVE`（註解說是給 `inet_diag` 用的假狀態）。實作的狀態集合**不等於**規格的狀態集合，讀碼時不要假設一一對應。
- **`TCP_STATE_MASK 0xF`**：所有狀態值小於 16，可以塞進 4 個 bit。這讓轉移表可以把「下一個狀態」和「附帶動作」打包進同一個 byte（下面會看到）。
- **`TCPF_*` 位元版本**：`(1 << state)`，讓程式可以寫 `if ((1 << sk->sk_state) & (TCPF_CLOSE | TCPF_LISTEN))` 一次判斷多個狀態。這在 `net/ipv4/` 裡到處都是，認得它就不會被長長的位元運算嚇到。

### 第二個指紋：咽喉點 `tcp_set_state`

找出所有轉移最快的方法，是找**唯一負責改狀態的函式**：

```c
/* net/ipv4/tcp.c:2945（v6.18，節錄） */
void tcp_set_state(struct sock *sk, int state)
{
	int oldstate = sk->sk_state;
	...
	BUILD_BUG_ON((int)BPF_TCP_ESTABLISHED != (int)TCP_ESTABLISHED);
	...
	if (BPF_SOCK_OPS_TEST_FLAG(tcp_sk(sk), BPF_SOCK_OPS_STATE_CB_FLAG))
		tcp_call_bpf_2arg(sk, BPF_SOCK_OPS_STATE_CB, oldstate, state);

	switch (state) {
	case TCP_ESTABLISHED:
		if (oldstate != TCP_ESTABLISHED)
			TCP_INC_STATS(sock_net(sk), TCP_MIB_CURRESTAB);
		break;
	...
	inet_sk_state_store(sk, state);
```

集中的轉移函式是好的 state machine 設計，理由在這段程式碼裡都看得到：BPF hook、統計計數、以及最後的 `inet_sk_state_store`（`net/ipv4/af_inet.c:1352`）會觸發 `trace_inet_sock_set_state` tracepoint。**所有轉移都經過這裡，所以所有轉移都可觀察。** 那串 `BUILD_BUG_ON` 確保 kernel 內部的狀態編號和 BPF UAPI 匯出的編號永遠一致——狀態值一旦匯出給 user space，就成了 ABI，不能再改。

讀碼含意：要畫 TCP 狀態圖，不用讀完 `tcp_input.c`，只要找出 `tcp_set_state` 的所有呼叫點，記下「呼叫時在哪個 case 裡」和「設成什麼」。

### 用工具半自動抽出轉移

這種機械性工作適合寫個小工具。第一版（刻意寫得天真）：

```awk
# transitions.awk — 粗略抽出「在哪個 case 裡把狀態改成什麼」
/^[a-z].*\(/              { fn = $0; sub(/\(.*/, "", fn); sub(/.* \**/, "", fn) }
/case TCP_[A-Z0-9_]+:/    { match($0, /TCP_[A-Z0-9_]+/); cur = substr($0, RSTART, RLENGTH) }
/tcp_set_state\(sk, TCP_/ { match($0, /TCP_[A-Z0-9_]+/)
                            printf "%-26s %-14s -> %-14s (line %d)\n", fn, cur, substr($0, RSTART, RLENGTH), NR }
/tcp_time_wait\(sk, TCP_/ { match($0, /TCP_[A-Z0-9_]+/)
                            printf "%-26s %-14s -> %-14s (line %d, via tcp_time_wait)\n", fn, cur, substr($0, RSTART, RLENGTH), NR }
/[^_]tcp_done\(sk\)/      { printf "%-26s %-14s -> %-14s (line %d, via tcp_done)\n", fn, cur, "TCP_CLOSE", NR }
```

```
$ cd ~/code_reading/linux && awk -f transitions.awk net/ipv4/tcp_input.c
tcp_done_with_error        TCP_CA_CWR     -> TCP_CLOSE      (line 4625, via tcp_done)
tcp_fin                    TCP_ESTABLISHED -> TCP_CLOSE_WAIT (line 4688)
tcp_fin                    TCP_FIN_WAIT1  -> TCP_CLOSING    (line 4708)
tcp_fin                    TCP_FIN_WAIT2  -> TCP_TIME_WAIT  (line 4713, via tcp_time_wait)
tcp_finish_connect         TCP_FIN_WAIT2  -> TCP_ESTABLISHED (line 6499)
tcp_rcv_synsent_state_process TCP_FIN_WAIT2  -> TCP_SYN_RECV   (line 6798)
tcp_rcv_state_process      TCP_SYN_RECV   -> TCP_ESTABLISHED (line 7028)
...
```

前五行就有三行是錯的，而且錯得很有教育意義：

- `TCP_CA_CWR` 不是連線狀態，是**另一個 state machine**（壅塞控制的 `tcp_ca_state`）的狀態。同一個檔案裡有兩台 state machine，名字前綴都是 `TCP_`。
- `tcp_finish_connect` 裡沒有任何 `case`，`TCP_FIN_WAIT2` 是從前一個函式殘留下來的。`case` 的作用範圍是函式，工具卻沒有在換函式時清掉。
- 同理 `tcp_rcv_synsent_state_process` 那行。

修正版：換函式時重設、`case` 只認真正的連線狀態，並把 `tcp_time_wait` 的語意講清楚（它把 socket 轉成一個輕量的 timewait sock，第二個參數是它的子狀態）：

```awk
# transitions2.awk — 修正版：換函式時清掉 cur；case 只認真正的 TCP 狀態
BEGIN { st = "TCP_(ESTABLISHED|SYN_SENT|SYN_RECV|FIN_WAIT1|FIN_WAIT2|TIME_WAIT|CLOSE|CLOSE_WAIT|LAST_ACK|LISTEN|CLOSING|NEW_SYN_RECV)" }
/^[a-z].*\(/              { cur = "(any)"; fn = $0; sub(/\(.*/, "", fn); sub(/.* \**/, "", fn) }
$0 ~ ("case " st ":")     { match($0, st); cur = substr($0, RSTART, RLENGTH) }
/tcp_set_state\(sk, TCP_/ { match($0, st)
                            printf "%-30s %-15s -> %-14s (line %d)\n", fn, cur, substr($0, RSTART, RLENGTH), NR }
/tcp_time_wait\(sk, TCP_/ { match($0, st)
                            printf "%-30s %-15s -> TIME_WAIT sock, substate %s (line %d)\n", fn, cur, substr($0, RSTART, RLENGTH), NR }
/[^_]tcp_done\(sk\)/      { printf "%-30s %-15s -> TCP_CLOSE      (line %d, via tcp_done)\n", fn, cur, NR }
```

```
$ awk -f transitions2.awk net/ipv4/tcp_input.c
tcp_done_with_error            (any)           -> TCP_CLOSE      (line 4625, via tcp_done)
tcp_fin                        TCP_ESTABLISHED -> TCP_CLOSE_WAIT (line 4688)
tcp_fin                        TCP_FIN_WAIT1   -> TCP_CLOSING    (line 4708)
tcp_fin                        TCP_FIN_WAIT2   -> TIME_WAIT sock, substate TCP_TIME_WAIT (line 4713)
tcp_finish_connect             (any)           -> TCP_ESTABLISHED (line 6499)
tcp_rcv_synsent_state_process  (any)           -> TCP_SYN_RECV   (line 6798)
tcp_rcv_state_process          TCP_SYN_RECV    -> TCP_ESTABLISHED (line 7028)
tcp_rcv_state_process          TCP_FIN_WAIT1   -> TCP_FIN_WAIT2  (line 7066)
tcp_rcv_state_process          TCP_FIN_WAIT1   -> TCP_CLOSE      (line 7078, via tcp_done)
tcp_rcv_state_process          TCP_FIN_WAIT1   -> TCP_CLOSE      (line 7087, via tcp_done)
tcp_rcv_state_process          TCP_FIN_WAIT1   -> TIME_WAIT sock, substate TCP_FIN_WAIT2 (line 7104)
tcp_rcv_state_process          TCP_CLOSING     -> TIME_WAIT sock, substate TCP_TIME_WAIT (line 7112)
tcp_rcv_state_process          TCP_LAST_ACK    -> TCP_CLOSE      (line 7120, via tcp_done)
```

`(any)` 表示轉移不在 `switch` 裡，「從哪個狀態出發」要讀呼叫脈絡才知道：`tcp_finish_connect` 是 SYN_SENT 收到 SYN-ACK 後被呼叫的，`tcp_rcv_synsent_state_process` 第 6798 行是 simultaneous open（雙方同時送 SYN）的情況。

這個練習的重點不是 awk，而是兩個讀碼態度：**用工具做機械工作，但工具的輸出也是要驗證的假說**（Ch 3）；**一個檔案裡可能有不只一台 state machine**。完整的工具化方法在 Part 8 的 [Ch 47](./47-grep-and-regex-search.md)。

### 第三個指紋：轉移表

`tcp_input.c` 處理的是「收到封包」這個事件。「使用者呼叫 `close()`」是另一個事件，它的轉移寫成一張表：

```c
/* net/ipv4/tcp.c:3023（v6.18） */
static const unsigned char new_state[16] = {
  /* current state:        new state:      action:	*/
  [0 /* (Invalid) */]	= TCP_CLOSE,
  [TCP_ESTABLISHED]	= TCP_FIN_WAIT1 | TCP_ACTION_FIN,
  [TCP_SYN_SENT]	= TCP_CLOSE,
  [TCP_SYN_RECV]	= TCP_FIN_WAIT1 | TCP_ACTION_FIN,
  [TCP_FIN_WAIT1]	= TCP_FIN_WAIT1,
  [TCP_FIN_WAIT2]	= TCP_FIN_WAIT2,
  [TCP_TIME_WAIT]	= TCP_CLOSE,
  [TCP_CLOSE]		= TCP_CLOSE,
  [TCP_CLOSE_WAIT]	= TCP_LAST_ACK  | TCP_ACTION_FIN,
  [TCP_LAST_ACK]	= TCP_LAST_ACK,
  [TCP_LISTEN]		= TCP_CLOSE,
  [TCP_CLOSING]		= TCP_CLOSING,
  [TCP_NEW_SYN_RECV]	= TCP_CLOSE,	/* should not happen ! */
};

static int tcp_close_state(struct sock *sk)
{
	int next = (int)new_state[sk->sk_state];
	int ns = next & TCP_STATE_MASK;

	tcp_set_state(sk, ns);

	return next & TCP_ACTION_FIN;
}
```

現在 `TCP_STATE_MASK` 和 `TCP_ACTION_FIN` 的用意清楚了：低 4 bit 是下一個狀態，`1 << TCP_CLOSE`（`TCP_CLOSE` 是 7，所以是第 7 bit，0x80）這一個 bit 表示「轉移時要送 FIN」。因為所有狀態值都小於 16，0x80 不會和狀態值衝突。整張表一眼可以讀完：ESTABLISHED 或 CLOSE_WAIT 時 `close()`，要送 FIN；已經在關閉流程中的狀態，`close()` 不改變狀態。

| 寫法 | 長相 | 優點 | 讀碼方式 |
|---|---|---|---|
| switch on state | `switch (sk->sk_state) { case ...: }` | 每條轉移可以有任意複雜的動作 | 依 case 逐一讀，配合轉移函式的呼叫點 |
| 轉移表 | `new_state[state]` | 一眼看完全部轉移，不會漏 | 直接把表畫成圖 |
| 單一轉移函式 | `tcp_set_state(sk, s)` | 所有轉移可觀察、可 hook | 列出所有呼叫點，反推邊 |

TCP 三種都用了。真實系統裡這很常見：轉移表處理單純的事件，switch 處理複雜的事件，兩者最後都經過咽喉點。

## 把它畫成狀態圖，並用實驗驗證

把工具的輸出、`new_state[]`、`tcp_v4_connect` 裡的 `tcp_set_state(sk, TCP_SYN_SENT)`（`net/ipv4/tcp_ipv4.c:308`）合起來，關閉流程的狀態圖：

```
                           close() / 送 FIN
              ESTABLISHED ─────────────────► FIN_WAIT1 ──收到 ACK──► FIN_WAIT2
                   │       (new_state[])        │  (tcp_input.c:7066)    │
        收到 FIN   │                    收到 FIN │                       │ 收到 FIN
 (tcp_input.c:4688)▼                (4708)      ▼                       ▼ (4713)
              CLOSE_WAIT                     CLOSING ──收到 ACK──►  TIME_WAIT
                   │ close() / 送 FIN            (7112)                  │ 2MSL 計時器
                   ▼ (new_state[])                                       ▼
               LAST_ACK ──────收到 ACK (7120, tcp_done)─────────────► CLOSE
```

不要只相信圖，用 loopback 走一遍。下面的程式在每一步用 `ss` 觀察兩端的狀態，不需要 root：

```python
# tcpstates.py — 在 loopback 上走一遍 TCP 狀態機，每一步用 ss 觀察
import socket, subprocess, time

PORT = 47011

def show(step):
    out = subprocess.run(["ss", "-tanH", f"( sport = :{PORT} or dport = :{PORT} )"],
                         capture_output=True, text=True).stdout
    print(f"--- {step}")
    for line in sorted(out.splitlines()):
        f = line.split()
        print(f"    {f[0]:<11} {f[3]:<17} -> {f[4]}")

srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", PORT)); srv.listen()
show("listen()")

cli = socket.create_connection(("127.0.0.1", PORT))
conn, _ = srv.accept()
show("connect() + accept()")

cli.close(); time.sleep(0.1)          # 主動關閉的一方
show("client close()")

conn.close(); time.sleep(0.1)         # 被動關閉的一方也關
show("server close()")
srv.close()
```

```
$ python3 tcpstates.py
--- listen()
    LISTEN      127.0.0.1:47011   -> 0.0.0.0:*
--- connect() + accept()
    ESTAB       127.0.0.1:45152   -> 127.0.0.1:47011
    ESTAB       127.0.0.1:47011   -> 127.0.0.1:45152
    LISTEN      127.0.0.1:47011   -> 0.0.0.0:*
--- client close()
    CLOSE-WAIT  127.0.0.1:47011   -> 127.0.0.1:45152
    FIN-WAIT-2  127.0.0.1:45152   -> 127.0.0.1:47011
    LISTEN      127.0.0.1:47011   -> 0.0.0.0:*
--- server close()
    LISTEN      127.0.0.1:47011   -> 0.0.0.0:*
    TIME-WAIT   127.0.0.1:45152   -> 127.0.0.1:47011
```

每一步都和圖一致：client `close()` 後，client 經 FIN_WAIT1 很快收到 ACK 進入 FIN_WAIT2（loopback 太快，看不到 FIN_WAIT1），server 收到 FIN 進入 CLOSE_WAIT；server 也 `close()` 後，server 經 LAST_ACK 收到 ACK 進入 CLOSE（從列表消失），client 收到 FIN 進入 TIME_WAIT。

邊界情況：`SO_LINGER` 設為 `{on, 0}` 時，`close()` 送 RST 而不是 FIN，整條關閉流程被跳過：

```python
# tcprst.py（節錄；建立連線與 show() 同上，PORT = 47012）
cli.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
cli.close(); time.sleep(0.1)
show("client close() with linger=0")
try:
    conn.recv(10)
except OSError as e:
    print("    server recv():", e)
```

```
$ python3 tcprst.py
--- established
    ESTAB       127.0.0.1:47012   -> 127.0.0.1:56208
    ESTAB       127.0.0.1:56208   -> 127.0.0.1:47012
    LISTEN      127.0.0.1:47012   -> 0.0.0.0:*
--- client close() with linger=0
    LISTEN      127.0.0.1:47012   -> 0.0.0.0:*
    server recv(): [Errno 104] Connection reset by peer
```

兩端直接消失，沒有 TIME_WAIT。這條路徑在 `__tcp_close()`（`net/ipv4/tcp.c:3123`）裡：第 3173 行判斷 `SOCK_LINGER && !sk->sk_lingertime` 後改走 `sk->sk_prot->disconnect(sk, 0)`（即 `tcp_disconnect`，它送出 RST 並把狀態設為 CLOSE），根本不會走到下一個分支的 `tcp_close_state()` 與 `new_state[]`。上面一個分支還有另一條旁路：關閉時接收佇列裡仍有未讀資料，也會直接 `tcp_set_state(sk, TCP_CLOSE)` 並送 RST。它提醒我們：**狀態圖是從「正常事件」畫出來的，錯誤與例外事件常常走旁路**，讀碼時要另外找。

（想在 kernel 端直接看每次轉移，可以用 `inet_sock_set_state` tracepoint：`bpftrace -e 'tracepoint:sock:inet_sock_set_state { printf("%d -> %d\n", args->oldstate, args->newstate); }'`。這需要 root 與 bpftrace，WSL 無 sudo 環境下**本段未實測**，詳見 [Ch 51](./51-dynamic-tracing.md)。）

### 讀 `tcp_rcv_state_process` 的正確姿勢

有了狀態圖，回頭讀那個 270 行的函式就有地圖了。它的結構（行號為 v6.18）：

```
6919  switch (sk->sk_state)          ← 第一段：三個「特殊」狀態先處理掉
        case TCP_CLOSE:   丟棄
        case TCP_LISTEN:  收到 SYN → icsk_af_ops->conn_request()（建立半開連線）
        case TCP_SYN_SENT: 交給 tcp_rcv_synsent_state_process()
      （其餘狀態往下）
6988  /* step 5: check the ACK field */
7008  switch (sk->sk_state)          ← 第二段：依狀態處理 ACK 帶來的轉移
        case TCP_SYN_RECV:  → ESTABLISHED
        case TCP_FIN_WAIT1: → FIN_WAIT2 或 timewait
        case TCP_CLOSING:   → TIME_WAIT
        case TCP_LAST_ACK:  → CLOSE
7126  /* step 6: check the URG bit */
7129  /* step 7: process the segment text */
7130  switch (sk->sk_state)          ← 第三段：依狀態決定資料要不要收
```

那些 `step 5`、`step 6`、`step 7` 註解是最有價值的 beacon：函式開頭第 6903 行的註解寫著它實作的是 **RFC 793 的接收程序**，RFC 793 第 3.9 節「Event Processing」的 SEGMENT ARRIVES 段落就是用 first、second……eighth 這種步驟編號寫的。**這個函式的結構是照規格的結構排的。** 讀不懂某一段時，打開 RFC 對應的步驟，常常比讀 code 還快。這是領域鴻溝的標準解法：規格就是領域知識的源頭。（今天 TCP 的現行規格是 RFC 9293，它整合了 793 與後續修正，段落結構相近。）

## 事件驅動：誰把事件送進來？

state machine 回答「收到事件後怎麼辦」，事件驅動架構回答「事件怎麼來」。先看一個有 main loop 的系統，再看 kernel。

### user space：Redis 的 `ae.c`

Redis 是單執行緒事件驅動伺服器的教科書範例。整個 server 的主迴圈只有 8 行：

```c
/* redis-7.2.17/src/ae.c:493 */
void aeMain(aeEventLoop *eventLoop) {
    eventLoop->stop = 0;
    while (!eventLoop->stop) {
        aeProcessEvents(eventLoop, AE_ALL_EVENTS|
                                   AE_CALL_BEFORE_SLEEP|
                                   AE_CALL_AFTER_SLEEP);
    }
}
```

事件與 handler 的對應存在一張以 fd 為索引的表：

```c
/* redis-7.2.17/src/ae.h:72 */
typedef struct aeFileEvent {
    int mask; /* one of AE_(READABLE|WRITABLE|BARRIER) */
    aeFileProc *rfileProc;
    aeFileProc *wfileProc;
    void *clientData;
} aeFileEvent;
```

```c
/* redis-7.2.17/src/ae.c:162（節錄） */
int aeCreateFileEvent(aeEventLoop *eventLoop, int fd, int mask,
        aeFileProc *proc, void *clientData)
{
    ...
    aeFileEvent *fe = &eventLoop->events[fd];

    if (aeApiAddEvent(eventLoop, fd, mask) == -1)
        return AE_ERR;
    fe->mask |= mask;
    if (mask & AE_READABLE) fe->rfileProc = proc;
    if (mask & AE_WRITABLE) fe->wfileProc = proc;
    fe->clientData = clientData;
```

`aeProcessEvents`（`ae.c:361`）的骨架是：呼叫 `beforesleep` → `aeApiPoll()`（底層是 epoll／kqueue／select，第 399 行，**整個程式唯一會睡覺的地方**）→ 對每個就緒的 fd 查表呼叫 `rfileProc`／`wfileProc`（第 410–456 行）→ `processTimeEvents()` 處理計時器（第 466 行）。

為了確認這個結構，把它縮成 60 行的 C 程式，用 epoll + timerfd + pipe 實作同樣的「表 + 迴圈 + 分派」：

```c
/* miniloop.c — 仿 Redis ae.c 的最小 event loop：epoll + 每個 fd 一個 handler */
#include <stdio.h>
#include <stdint.h>
#include <unistd.h>
#include <sys/epoll.h>
#include <sys/timerfd.h>

typedef void handler_fn(int fd, void *data);
struct event { handler_fn *proc; void *data; };   /* 像 aeFileEvent */

static struct event events[64];                  /* 以 fd 為索引，像 eventLoop->events[fd] */
static int epfd, stop;

static void add_event(int fd, handler_fn *proc, void *data)   /* 像 aeCreateFileEvent */
{
    struct epoll_event ev = { .events = EPOLLIN, .data.fd = fd };
    events[fd] = (struct event){ proc, data };
    epoll_ctl(epfd, EPOLL_CTL_ADD, fd, &ev);
}

static void on_timer(int fd, void *data)
{
    uint64_t ticks;
    int *count = data;
    if (read(fd, &ticks, sizeof(ticks)) != sizeof(ticks)) return;
    printf("timer tick #%d\n", ++*count);
    if (*count == 3) stop = 1;
}

static void on_pipe(int fd, void *data)
{
    char buf[64];
    ssize_t n = read(fd, buf, sizeof(buf) - 1);
    (void)data;
    buf[n > 0 ? n : 0] = '\0';
    printf("pipe readable: \"%s\"\n", buf);
}

int main(void)
{
    int p[2], ticks = 0;
    struct itimerspec its = { .it_interval = { 0, 100000000 }, .it_value = { 0, 100000000 } };
    int tfd = timerfd_create(CLOCK_MONOTONIC, 0);

    epfd = epoll_create1(0);
    if (pipe(p) < 0) return 1;
    timerfd_settime(tfd, 0, &its, NULL);            /* 每 100 ms 觸發一次 */
    add_event(tfd, on_timer, &ticks);
    add_event(p[0], on_pipe, NULL);
    if (write(p[1], "hello", 5) != 5) return 1;     /* 事先塞一筆資料 */

    while (!stop) {                                 /* 像 aeMain */
        struct epoll_event fired[8];
        int n = epoll_wait(epfd, fired, 8, -1);     /* 像 aeApiPoll：唯一會睡的地方 */
        for (int i = 0; i < n; i++) {
            struct event *e = &events[fired[i].data.fd];
            e->proc(fired[i].data.fd, e->data);     /* 分派：查表 → 呼叫 handler */
        }
    }
    return 0;
}
```

```
$ gcc -Wall -Wextra -O2 -o miniloop miniloop.c && ./miniloop
pipe readable: "hello"
timer tick #1
timer tick #2
timer tick #3
```

（`it_interval` 的 `100000000` 是奈秒，即 100 ms；`events[64]` 的 64 只是這個玩具允許的最大 fd 編號，Redis 用 `setsize` 參數決定。）

這類系統的讀法：**main loop 本身沒什麼好讀，重點是那張表在哪裡被填**。在 Redis 裡 `rg 'aeCreateFileEvent\(|aeCreateTimeEvent\('`，會得到全部事件來源：`server.c:2421` 註冊 listening socket 的 accept handler，`server.c:2714` 註冊每毫秒觸發的 `serverCron`，`networking.c:130` 透過 `connSetReadHandler(conn, readQueryFromClient)` 為每個新連線註冊讀取 handler……

### kernel：沒有 main loop 的事件驅動

kernel 也是事件驅動的，但它**沒有一個你可以指著說「這是 main loop」的地方**。事件從四面八方直接打進來：

```
  硬體中斷 ──► IRQ handler ──► softirq（NET_RX）──► tcp_v4_rcv ──► tcp_rcv_state_process
                                                                       │
                                                       sk->sk_data_ready(sk)  ← callback
                                                                       │
  user syscall ──► recvmsg ──► 睡在 sk_wq 上 ◄──── wake_up ─────────────┘
  計時器到期 ──► timer softirq ──► tcp_write_timer / tcp_keepalive_timer
```

Redis 的 handler 是被**自己的** main loop 呼叫；kernel 的 handler 是被**事件發生的那個執行脈絡**直接呼叫：封包處理跑在 softirq 裡（Ch 32），系統呼叫跑在 process context 裡，兩者用 wait queue（Ch 33）銜接。Ch 42 看過的 `sk->sk_data_ready`、`sk->sk_state_change` 就是這種 callback：

```c
/* net/core/sock.c:3661（v6.18），sock_init_data_uid() 內 */
	sk->sk_state_change	=	sock_def_wakeup;
	sk->sk_data_ready	=	sock_def_readable;
	sk->sk_write_space	=	sock_def_write_space;
	sk->sk_error_report	=	sock_def_error_report;
```

TCP 在收到資料後呼叫 `sk->sk_data_ready(sk)`（例如 `tcp_input.c:5145`），在狀態轉移後呼叫 `sk->sk_state_change(sk)`（例如 `tcp_input.c:7029`，SYN_RECV → ESTABLISHED 之後）。預設的 `sock_def_readable`（`sock.c:3542`）做的事就是喚醒睡在這個 socket 上的 process 和 epoll。**state machine 轉移之後發出事件，事件喚醒等待者**——這是 state machine 與事件驅動接合的那個點。

| 面向 | Redis（有 main loop） | kernel（callback 驅動） |
|---|---|---|
| 事件從哪裡進入 | 唯一的 `aeApiPoll()` | 中斷、softirq、syscall、timer、其他子系統的呼叫 |
| handler 跑在哪個脈絡 | 永遠是 main thread | 取決於事件來源：hardirq、softirq、process context |
| 併發 | 單執行緒，handler 之間不會同時跑 | 多 CPU 同時跑不同 handler，需要鎖或 RCU（Ch 30、Ch 31） |
| handler 可以睡嗎 | 不應該（會卡住整個 server） | 依脈絡：中斷與 softirq 裡絕對不行 |
| 找 handler 的方法 | 找 `aeCreateFileEvent` 的呼叫點 | 找 callback 欄位的賦值點、`request_irq`、`timer_setup` |

## 讀事件驅動程式碼的方法：建一張對應表

面對事件驅動程式碼，不要從某個 handler 開始往下讀，先建一張**事件來源 → 註冊點 → handler → 跑在哪個脈絡**的表。以 TCP socket 為例：

| 事件來源 | 註冊／分派點 | handler | 執行脈絡 |
|---|---|---|---|
| 封包抵達 | IP 層依 protocol 號分派 | `tcp_v4_rcv`（`tcp_ipv4.c:2202`）→ `tcp_rcv_state_process` | softirq（NET_RX） |
| user `connect()` | `inet_stream_ops.connect` | `inet_stream_connect` → `tcp_v4_connect` | process |
| user `close()` | `tcp_prot.close` | `tcp_close` → `tcp_close_state`（查 `new_state[]`） | process |
| 重傳計時器到期 | `tcp_init_xmit_timers`（`tcp_timer.c:895`） | `tcp_write_timer`（`tcp_timer.c:726`） | timer softirq |
| keepalive／FIN_WAIT2 計時器 | 同上 | `tcp_keepalive_timer`（`tcp_timer.c:779`） | timer softirq |
| 延遲 ACK 計時器 | 同上 | `tcp_delack_timer`（`tcp_timer.c:359`） | timer softirq |
| 有資料可讀（往上通知） | `sock_init_data_uid` 設預設值，可被覆寫 | `sk->sk_data_ready` | 呼叫者的脈絡（通常是 softirq） |

這張表建好之後，任何「為什麼會這樣」的問題都可以先在表上定位：「FIN_WAIT2 怎麼結束的？」→ 看 keepalive timer 那一列；「為什麼 recv 會醒來？」→ 看 `sk_data_ready` 那一列。這正是 Ch 3 提問驅動的應用：表上的每一列都是一個可以單獨回答的問題。

## 對比與取捨

| 架構 | 優點 | 缺點 | 何時看到它 |
|---|---|---|---|
| 明確的 state machine（enum + 轉移函式） | 合法狀態一目了然，轉移可觀察、可測試 | 狀態多時程式碼爆炸；狀態之外的隱含狀態（計時器、旗標）容易被忽略 | 協定實作、裝置生命週期、parser |
| 隱含的 state machine（用一堆 bool 旗標） | 寫起來快 | 合法組合不明，讀者要自己推 | 演化很久的 driver，常需要重構 |
| 單執行緒 event loop | 不需要鎖，行為可預測 | 一個慢 handler 拖垮全部；無法利用多核 | Redis、Node.js、nginx 的每個 worker |
| 執行緒 + 阻塞呼叫 | 程式碼是線性的，好讀 | 每個連線一個 stack，切換成本高；需要鎖 | 傳統 Apache prefork、多數 kernel syscall 路徑本身 |
| callback 驅動（kernel） | 事件來源可以直接呼叫 handler，延遲低 | 控制流分散，handler 的執行脈絡限制多 | kernel 網路、driver、timer |

Lauer 與 Needham 在 1979 年提出一個著名論點：以訊息／事件為中心的系統和以程序／執行緒為中心的系統在某種意義上是**對偶**的，可以機械地互相轉換，效能本質上相同。這個論點提醒讀碼者：同一個邏輯可以寫成兩種長相，看到其中一種時，在腦中轉成另一種常常更好理解。TCP 的 `tcp_rcv_state_process` 若寫成執行緒版本，大概就是「`wait_for_syn(); send_synack(); wait_for_ack(); ...`」的線性程式碼。

## 踩雷集錦

1. **以為規格的狀態集合等於實作的狀態集合**：RFC 有 11 個狀態，v6.18 的 enum 有 13 個加一個上限值。`TCP_NEW_SYN_RECV` 存在於 request sock 而不是完整的 `tcp_sock`，`TIME_WAIT` 時完整的 socket 已經被換成輕量的 `inet_timewait_sock`。讀碼時以 enum 為準，用規格理解語意。
2. **只看 `sk_state` 就以為掌握了全部狀態**：TCP 至少還有壅塞控制狀態（`icsk_ca_state`，`TCP_CA_*`）、計時器是否啟動、各種旗標。上面天真版工具把 `TCP_CA_CWR` 當成連線狀態，就是這個錯誤的實例。
3. **狀態圖只畫正常路徑**：RST、`SO_LINGER=0`、逾時、記憶體不足都會繞過正常轉移。`tcprst.py` 的實驗顯示連線可以從 ESTABLISHED 直接消失。畫圖時要另外標出例外邊。
4. **在 event loop 的 handler 裡做阻塞操作**：Redis 的 handler 若呼叫阻塞 I/O，整個 server 停住；kernel 的 softirq handler 若呼叫可能睡眠的函式（例如帶 `GFP_KERNEL` 的配置、`mutex_lock`），會觸發 "scheduling while atomic" 錯誤。讀 handler 時第一件事是確認它跑在哪個脈絡。
5. **從 handler 往下讀，找不到「之後」發生什麼**：事件驅動程式碼的「之後」是下一個事件，不在 handler 的呼叫圖裡。用對應表從事件來源往下讀，而不是從 handler 往下讀。

## 進階：再往深一層

- **TCP 的 request sock 與 timewait sock**：`TCP_NEW_SYN_RECV` 與 `TIME_WAIT` 時，完整的 `tcp_sock` 不存在或已被替換，狀態轉移發生在不同型別的物件之間。讀 `net/ipv4/tcp_minisocks.c` 的 `tcp_check_req` 與 `tcp_timewait_state_process`，會看到「state machine 跨越物件型別」的設計，這是為了在大量半開連線（SYN flood）或大量 TIME_WAIT 時節省記憶體。
- **壅塞控制是另一台 state machine**：`tcp_ca_state`（Open、Disorder、CWR、Recovery、Loss）和連線狀態正交。它的轉移與 `tcp_congestion_ops` 的 `set_state` callback 相連，Ch 45 會把 `tcp_congestion_ops` 當 strategy 模式的例子。
- **其他值得讀的 kernel state machine**：網路裝置的 operstate（RFC 2863 定義，`include/uapi/linux/if.h` 的 `IF_OPER_*`）、USB 裝置狀態（`enum usb_device_state`）、`struct device` 的 probe 狀態。每一個都可以用本章的指紋表去找。
- **事件驅動的歷史論戰**：Ousterhout 的 "Why Threads Are A Bad Idea (for most purposes)"（1996 USENIX 邀請演講）主張事件優先；von Behren 等人的 "Why Events Are A Bad Idea (for high-concurrency servers)"（HotOS 2003）反駁；Welsh 等人的 SEDA（SOSP 2001）提出兩者混合的 staged 架構。讀這三篇能理解 Redis 為什麼選 event loop，而 kernel 為什麼兩種都用。

## 動手練習

1. **畫出建立連線的狀態圖**：仿照本章的關閉流程圖，畫出 LISTEN → SYN_RECV → ESTABLISHED 與 CLOSE → SYN_SENT → ESTABLISHED 兩條路徑，每條邊標出 v6.18 的 `file:line`。提示：被動端的 SYN_RECV 涉及 request sock，從 `tcp_rcv_state_process` 第 6942 行的 `conn_request` 開始追。
2. **找另一台 state machine**：在 `drivers/usb/core/` 或 `net/core/` 找一個 `*_set_state` 或 `enum *_state`，用本章的指紋表確認它是 state machine，並用 `rg` 列出所有轉移點。
3. **擴充 miniloop**：加入一個 listening socket，accept 新連線後為它註冊讀取 handler，讓它變成一個 echo server。這就是 Redis `acceptTcpHandler → readQueryFromClient` 那條鏈的縮小版。
4. **建 Redis 的事件對應表**：用 `rg -n 'aeCreateFileEvent\(|aeCreateTimeEvent\(|connSetReadHandler\(|connSetWriteHandler\(' ~/code_reading/redis-7.2.17/src/*.c`，整理成本章那種四欄表格（Redis 只有一種執行脈絡，所以第四欄可以改成「多久觸發一次」）。
5. **（需要 root 與 bpftrace，本段未實測）** 在 QEMU guest 用 `tracepoint:sock:inet_sock_set_state` 重跑 `tcpstates.py`，確認是否觀察到 FIN_WAIT1 與 LAST_ACK 這兩個在 `ss` 輸出裡一閃而過的狀態。

## 本章重點整理

- state machine 的指紋：狀態列舉、`state` 欄位、`switch (x->state)`、集中的 `set_state()`、以狀態為索引的轉移表。看到它就先畫圖再讀細節。
- 找出所有轉移最快的方法是找咽喉點（`tcp_set_state`）的所有呼叫者；工具可以加速，但輸出要當假說驗證。
- `tcp_rcv_state_process` 的結構照 RFC 793 的事件處理步驟排列，規格是讀它的地圖。
- Redis 是「main loop + fd 索引的 handler 表」；kernel 沒有 main loop，事件從中斷、softirq、syscall、timer 直接呼叫 callback。
- 讀事件驅動程式碼：先建「事件來源 → 註冊點 → handler → 執行脈絡」對應表，再逐列提問。

## 自我檢核

- [ ] 不看筆記，畫出 TCP 主動關閉與被動關閉兩方的狀態序列，並說出 `tcpstates.py` 為什麼看不到 FIN_WAIT1 和 LAST_ACK。
- [ ] 解釋 `TCP_STATE_MASK` 和 `TCP_ACTION_FIN` 如何讓 `new_state[]` 用一個 byte 同時編碼「下一個狀態」與「要不要送 FIN」，以及為什麼這個技巧依賴「所有狀態值小於 16」。
- [ ] 如果面試官問「Redis 為什麼是單執行緒還能這麼快？它的 event loop 和 Linux kernel 處理網路事件的方式有什麼不同？」，你能說出至少三個差異嗎？
- [ ] 拿到一個你沒讀過、有幾百行 `switch` 的函式，你會用什麼步驟判斷它是不是 state machine、並在半小時內畫出它的狀態圖？
- [ ] 想一個你寫過的程式裡用 bool 旗標組合表示狀態的地方，把它改寫成明確的 state machine 會有什麼好處與成本？

## 延伸閱讀

### 官方文件 / RFC

- **[RFC 9293: Transmission Control Protocol (TCP)](https://www.rfc-editor.org/rfc/rfc9293)** — W. Eddy, Ed.（IETF, 2022）
  - **讀哪裡**：Section 3.3.2「State Machine Overview」的狀態圖，與 Section 3.10「Event Processing」中 SEGMENT ARRIVES 的步驟。後者就是 `tcp_rcv_state_process` 註解裡 step 5、6、7 的出處（kernel 註解引用的是舊版 RFC 793 的 3.9 節，結構相同）。
  - **前提知識**：TCP 三向交握與序號的基本概念。
- **Redis 原始碼 `src/ae.c` 與 `src/ae_epoll.c`**（本課 `~/code_reading/redis-7.2.17/src/`）
  - **讀哪裡**：`aeProcessEvents`（第 361 行起）整個函式，約 130 行，加上 `ae_epoll.c` 全檔不到 140 行；這是讀懂一個生產等級 event loop 最短的路徑。

### 論文

- **On the Duality of Operating System Structures** — H. C. Lauer, R. M. Needham，ACM SIGOPS Operating Systems Review（1979）
  - **核心貢獻**：論證以訊息傳遞為中心與以程序呼叫為中心的系統可以一一對應互轉，兩者沒有本質上的效能差異，差別在於實作環境。
  - **和本章的關聯**：讀事件驅動程式碼時，把它在腦中轉成線性的執行緒版本（或反過來），是理解它的有效技巧；這篇是這個技巧的理論依據。
- **SEDA: An Architecture for Well-Conditioned, Scalable Internet Services** — M. Welsh, D. Culler, E. Brewer，SOSP（2001）
  - **核心貢獻**：把伺服器拆成以事件佇列相連的多個 stage，每個 stage 內部用小型執行緒池，結合事件驅動與執行緒的優點。
  - **讀哪裡**：Section 2 對 thread-based 與 event-driven 兩種設計的比較是本章「對比與取捨」的延伸；Section 3 之後的系統細節可以略讀。

### 書籍

- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪幾章**：Ch 9.2「Control Models」，原書討論事件驅動系統與 state transition 的辨識，並建議用圖把狀態轉移外部化，正是本章「先畫圖」的出處。
- **《TCP/IP Illustrated, Volume 2: The Implementation》** — Gary R. Wright, W. Richard Stevens（Addison-Wesley, 1995）
  - **這本書的定位**：逐行導讀 4.4BSD-Lite 的 TCP/IP 實作，是「讀協定 state machine 程式碼」的經典示範。
  - **讀哪幾章**：從 Ch 24 開始的 TCP 部分，尤其講 `tcp_input` 的那幾章。程式碼是 BSD 而非 Linux，但 state machine 的組織方式與 Linux 的 `tcp_rcv_state_process` 同源，對照著讀能看出兩者共同承襲的 RFC 結構。

state machine、事件驅動、分層、物件模型——這些都是可以命名、可以辨識的結構。下一章把這種「命名」推到極致：kernel 裡反覆出現的設計模式，一旦認出來，其餘部分會長什麼樣就能預測，你就能放心跳讀。

→ [Ch 45 辨識 kernel 裡的設計模式](./45-design-patterns-in-kernel.md)
