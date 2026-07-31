# Ch 6 — TCP 深入：握手、狀態機、流量控制

> **目標**：把 TCP 講到面試任何問題都答得出來——三次握手（為什麼三次不是兩次）、四次揮手、TCP 狀態機（TIME_WAIT 為什麼存在）、序號與 ACK 怎麼保證可靠、流量控制（滑動視窗）、壅塞控制（慢啟動）。TCP 是網際網路最重要的協定，也是「看起來懂其實一知半解」的重災區。這章用 tcpdump 把每個機制抓出來看。

> **環境**：Linux（tcpdump / ss）。TCP 狀態用 `ss -tan` 觀察。

## 為什麼 TCP 值得最深的一章？

你用的幾乎所有東西都跑在 TCP 上——網頁、API、SSH、資料庫連線。TCP 提供「**可靠的位元組串流**」：你從一端寫入的資料，另一端會**完整、有序、不重複**地收到，即使底下的 IP 是「盡力而為」（會丟包、亂序、重複，Ch 4）。TCP 怎麼在不可靠的 IP 上做出可靠？這是電腦科學的經典問題。

理解 TCP 回答了無數實際問題：連線為什麼 timeout？大量 TIME_WAIT 是什麼、要不要怕？為什麼有時連線「卡住」？為什麼下載速度是這樣爬升的（慢啟動）？這些是後端/SRE 的日常。TCP 也是面試高頻題——「講一下三次握手」「為什麼要四次揮手」「TIME_WAIT 的作用」。這章把這些從「背答案」變成「理解機制」。

## 先建立直覺:可靠傳輸像掛號信對話

```
TCP 在不可靠的 IP 上做出可靠傳輸，靠「編號 + 確認 + 重傳」：

  寄掛號信的對話：
    你：寄信 #1「你好」
    對方：「收到 #1，請寄 #2」（ACK）
    你：寄信 #2「最近好嗎」
    （信 #2 寄丟了，對方沒收到）
    你：等不到「收到 #2」→ 重寄信 #2
    對方：「收到 #2」
        │
  TCP 的對應：
    每個 byte 都有「序號」（seq）
    收到就回「確認號」（ACK，告知「我收到到這裡了」）
    送出後等 ACK，超時沒等到 → 重傳
        │
  → 可靠傳輸 = 編號(seq) + 確認(ACK) + 超時重傳
    這讓「在會丟包的 IP 上」做出「保證送達」
```

關鍵心智：TCP 在不可靠的 IP（會丟包/亂序/重複）上做出**可靠的位元組串流**，靠三個機制：**序號**（每個 byte 編號）、**確認**（ACK 告知收到哪裡）、**超時重傳**（沒收到 ACK 就重送）。加上**滑動視窗**（流量控制）和**壅塞控制**，就是完整的 TCP。

> TCP 是傳輸層（Ch 2），跑在 IP（Ch 4）之上。它用 port 區分同一台機器的不同連線（Ch 2 的傳輸層定址）。如果對分層和 IP 不熟，回看 [Ch 2](./02-osi-tcpip-models.md) 和 [Ch 4](./04-network-layer-ip-icmp.md)。

## 三次握手:建立連線

```
TCP 三次握手（建立連線）：

  客戶端                              伺服器
    │                                   │
    │── SYN (seq=x) ──────────────────▶│  「我想連線，我的起始序號是 x」
    │                                   │
    │◀──── SYN-ACK (seq=y, ack=x+1) ───│  「好，我的起始序號是 y，
    │                                   │   確認收到你的 x（期待 x+1）」
    │                                   │
    │── ACK (ack=y+1) ────────────────▶│  「確認收到你的 y（期待 y+1）」
    │                                   │
    │═══════ 連線建立，開始傳資料 ═══════│
        │
  為什麼三次（不是兩次）？
    要「雙向」都確認彼此能收能發：
    - 第 1 次：客戶端證明「我能發」
    - 第 2 次：伺服器證明「我能收also能發」
    - 第 3 次：客戶端證明「我能收」
    → 兩次不夠（伺服器不知道客戶端能不能收）
```

```bash
# 抓三次握手（本課核心：親眼看 TCP 機制）
sudo tcpdump -i any -n -S 'tcp port 443 and host example.com' &
curl -sI https://example.com > /dev/null
# IP ...54321 > ...443: Flags [S], seq 1000          ← SYN
# IP ...443 > ...54321: Flags [S.], seq 2000, ack 1001  ← SYN-ACK (S. = SYN+ACK)
# IP ...54321 > ...443: Flags [.], ack 2001           ← ACK (. = 純 ACK)
sudo pkill tcpdump
#   Flags: S=SYN, .=ACK, P=PUSH, F=FIN, R=RST
```

> **三次握手是「雙向確認彼此能收能發」——兩次不夠的原因是伺服器無法確認客戶端能收**。握手的本質是雙方同步「起始序號」（ISN）並確認雙向通道暢通。第 1 次（SYN）客戶端說「我要連，序號從 x 開始」——證明客戶端能發。第 2 次（SYN-ACK）伺服器說「好，我序號從 y 開始，確認你的 x」——證明伺服器能收也能發。第 3 次（ACK）客戶端說「確認你的 y」——證明客戶端能收。**為什麼不能兩次？** 如果只有兩次（SYN、SYN-ACK），伺服器發完 SYN-ACK 就認為連線建立，但它**不知道客戶端有沒有收到**——萬一 SYN-ACK 丟了，客戶端不知道連線建立，但伺服器以為建立了，狀態不一致。第三次 ACK 讓伺服器確認「客戶端收到了我的 SYN-ACK」。另外**起始序號是隨機的**（不從 0 開始）——這是安全考量（防止序號預測攻擊、防止舊連線的封包亂入新連線）。面試必考：能畫出三次握手 + 解釋為什麼三次。

## 四次揮手與 TIME_WAIT

```
TCP 四次揮手（關閉連線）：

  主動關閉方                          被動關閉方
    │── FIN ──────────────────────────▶│  「我沒資料要送了」
    │◀──── ACK ────────────────────────│  「知道了」
    │            （被動方可能還有資料要送）
    │◀──── FIN ────────────────────────│  「我也沒資料要送了」
    │── ACK ──────────────────────────▶│  「知道了」
    │                                   │
    │ [TIME_WAIT 狀態，等 2×MSL]         │
    │ （等一段時間才真正關閉）            │
        │
  為什麼四次（比握手多一次）？
    關閉是「雙向各自關」：
    一方說「我沒資料了」(FIN)，但另一方可能還有資料要送
    → 所以 ACK 和對方的 FIN 分開（不像握手能合併成 SYN-ACK）
        │
  為什麼有 TIME_WAIT（主動關閉方等 2×MSL）？
    1. 確保最後的 ACK 對方收到（沒收到對方會重送 FIN，你還能回 ACK）
    2. 讓舊連線的「迷途封包」在網路上消失，不污染新連線
```

```bash
# 觀察 TCP 連線狀態（ss -tan）
ss -tan
# State      Recv-Q Send-Q  Local Address:Port  Peer Address:Port
# ESTAB      0      0       192.168.1.100:22    192.168.1.5:54321   ← 已建立
# TIME-WAIT  0      0       192.168.1.100:443   1.2.3.4:33445       ← 等待中
# LISTEN     0      128     0.0.0.0:80          0.0.0.0:*           ← 監聽中

# 統計各狀態的連線數（debug「大量 TIME_WAIT」）
ss -tan | awk 'NR>1 {print $1}' | sort | uniq -c
#   找出有多少 ESTAB / TIME-WAIT / CLOSE-WAIT
```

> **TIME_WAIT 不是 bug，是 TCP 正確性的保證——但「大量 TIME_WAIT」常被誤解為問題**。主動關閉連線的一方（先送 FIN 的）會進入 **TIME_WAIT** 狀態，等 `2×MSL`（MSL = 最大封包存活時間，Linux 預設讓 TIME_WAIT 持續 60 秒）。為什麼要等？(1) **確保最後的 ACK 送達**——如果你的 ACK 丟了，對方會重送 FIN，你還在 TIME_WAIT 就能再回 ACK（如果立刻關閉，重來的 FIN 會收到 RST，對方困惑）；(2) **讓舊連線的迷途封包消失**——避免延遲的舊封包亂入「相同 IP+port 的新連線」造成資料污染。常見困惑：高流量伺服器有**大量 TIME_WAIT**——這通常**正常**（每個關閉的連線都會有），不是問題。但如果 TIME_WAIT 耗盡了本地 port（client 端大量短連線）才需要處理（用連線池、`SO_REUSEADDR`、調 `tcp_tw_reuse`）。另一個要警惕的是 **CLOSE_WAIT** 堆積——那才常是 bug（你的程式收到對方 FIN 但忘了 close，連線卡在 CLOSE_WAIT，洩漏 fd）。面試常問「TIME_WAIT 的作用」和「TIME_WAIT vs CLOSE_WAIT」——前者是主動關閉方的正常等待，後者堆積是程式沒關連線的 bug。

## TCP 狀態機

```
TCP 連線的完整狀態機（簡化）：

  CLOSED
    │ 主動連線(送SYN)        被動監聽(LISTEN)
    ▼                              ▼
  SYN_SENT                      LISTEN
    │ 收SYN-ACK,送ACK          │ 收SYN,送SYN-ACK
    ▼                              ▼
  ESTABLISHED ◀──────────────  SYN_RCVD
    │（資料傳輸）                  │
    │ 主動關閉(送FIN)           被動關閉(收FIN)
    ▼                              ▼
  FIN_WAIT_1                    CLOSE_WAIT
    │                              │ (送FIN)
    ▼                              ▼
  FIN_WAIT_2                    LAST_ACK
    │ 收FIN,送ACK                 │ 收ACK
    ▼                              ▼
  TIME_WAIT ──(等2MSL)──▶CLOSED  CLOSED
        │
  → 每個連線都在這個狀態機裡移動
    ss -tan 看到的 State 就是這些狀態
    debug 時：卡在某狀態 = 某一步沒完成
```

> **TCP 狀態機是 debug 連線問題的地圖——卡在某狀態就代表某一步沒完成**。每個 TCP 連線都在這個狀態機裡移動，`ss -tan` 的 State 欄就是當前狀態。debug 時，異常的狀態堆積指出問題：大量 **SYN_SENT**（client 送了 SYN 但收不到 SYN-ACK）= 對方沒回應或 SYN-ACK 被擋（防火牆/對方服務沒開）；大量 **SYN_RECV**（server 收到 SYN 回了 SYN-ACK 但等不到 ACK）= 可能 SYN flood 攻擊或網路問題；大量 **CLOSE_WAIT** = 你的程式收到 FIN 但沒 close（bug，fd 洩漏）；大量 **TIME_WAIT** = 通常正常（高頻短連線）。理解狀態機，`ss -tan` 的輸出就從「一堆字」變成「連線健康的診斷」。這是 SRE debug 連線問題的核心技能——先看狀態分布，定位問題在握手、傳輸、還是關閉階段。

## 序號、ACK 與可靠傳輸

```
TCP 怎麼保證「完整、有序、不重複」：

  序號(seq)：每個 byte 有編號
    送 "HELLO"（seq從1000）→ H=1000,E=1001,L=1002,L=1003,O=1004
        │
  確認號(ACK)：「我收到到這裡了，下一個期待這個」
    收到 1000-1004 → 回 ACK=1005（「期待 1005」）
        │
  有序：收到亂序的封包，靠 seq 重排
    先收到 seq=2000 再收到 seq=1000 → 按序號排好
        │
  不重複：重複的 seq → 丟棄（已經收過了）
        │
  重傳：送出後等 ACK，超時(RTO)沒等到 → 重傳
    或收到 3 個重複 ACK（duplicate ACK）→ 快速重傳（不等超時）
        │
  → seq + ACK + 重排 + 去重 + 重傳 = 可靠的位元組串流
```

```bash
# 看序號和 ACK 的流動
sudo tcpdump -i any -n -S 'tcp port 22' &
# （SSH 連線或傳資料時觀察）
# ...22 > ...: Flags [P.], seq 1000:1050, ack 500   ← 送 50 bytes (1000-1049)，確認對方到 500
# ...> ...22: Flags [.], ack 1050                     ← 確認收到（期待 1050）
sudo pkill tcpdump
```

> **TCP 用「序號 + 累積確認」做可靠傳輸——ACK 號是「我下一個期待的 byte」，不是「我收到的最後一個」**。每個 byte 有序號，接收方回的 **ACK 號是「期待的下一個 byte」**（累積確認——ACK=1050 表示「1050 之前的我都收到了」）。這個設計很巧妙：即使某個 ACK 丟了，後面的 ACK 也能涵蓋（ACK=1050 隱含確認了 1000-1049 全部）。**重傳機制**有兩種觸發：**超時重傳**（送出後等 RTO 時間沒收到 ACK 就重送，RTO 根據 RTT 動態計算）和**快速重傳**（收到 3 個重複的 ACK——如連續收到 ACK=1000 三次，表示對方一直沒收到 1000 那段，不等超時就立刻重送）。快速重傳讓 TCP 對丟包反應更快。亂序的封包靠序號重排、重複的靠序號去重。理解 seq/ACK 的累積語意，你看 tcpdump 的 TCP 流就能讀懂「哪段資料送到哪、有沒有重傳」——這是 debug 傳輸問題（卡住、慢）的關鍵。

## 流量控制與壅塞控制

TCP 有兩套「控制送多快」的機制，常被混淆：

```
流量控制 vs 壅塞控制（兩個不同的「踩煞車」）：

  流量控制（flow control）：別淹死「接收方」
    接收方在 ACK 裡告知「我的接收緩衝還剩多少」（window size）
    發送方不送超過接收方能收的量（滑動視窗）
    → 保護「接收方」不被淹沒
        │
  壅塞控制（congestion control）：別淹死「網路」
    發送方維護「壅塞視窗」(cwnd)，估計網路能承受多少
    慢啟動：從小開始（如 10 個封包），每次 RTT 翻倍探測
    遇到丟包（網路壅塞信號）→ 大幅縮小 cwnd
    → 保護「網路」不被塞爆
        │
  實際能送的量 = min(接收方視窗, 壅塞視窗)
    兩個煞車取較嚴格的那個
```

```
慢啟動（slow start）—— 為什麼下載速度是「爬升」的：

  連線剛建立：cwnd 很小（保守，不知道網路能承受多少）
    第 1 個 RTT：送 10 個封包
    第 2 個 RTT：送 20 個（翻倍）
    第 3 個 RTT：送 40 個...（指數成長，「探測」網路容量）
        │
  直到：遇到丟包（網路滿了）或達到接收方視窗上限
    → 縮小 cwnd，進入「壅塞避免」（線性緩增）
        │
  → 這就是為什麼下載速度不是瞬間滿速
    而是「爬升」上去（慢啟動探測網路容量）
    短連線（如小檔案）可能還沒爬到滿速就結束了
    → 這也是為什麼 HTTP/2 多路複用、連線重用重要（Ch 10）
```

> **流量控制保護接收方、壅塞控制保護網路——兩個不同的煞車，常被混淆**。**流量控制**：接收方在每個 ACK 裡告知「我緩衝還剩多少」（window size 欄位），發送方不送超過這個量——防止快的發送方淹死慢的接收方（如手機收 server 的資料）。**壅塞控制**：發送方自己估計「網路能承受多少」（壅塞視窗 cwnd），透過慢啟動（從小開始指數探測）和遇到丟包就縮小來避免塞爆網路——這是 TCP 對「公網是共享資源」的自律。實際送速取兩者較小值。**慢啟動**解釋了一個日常現象：下載速度為什麼是「爬升」的而非瞬間滿速——連線剛開始 cwnd 小（保守），每個 RTT 翻倍探測網路容量，直到丟包或達到上限。這對短連線（小檔案、API 請求）影響大——可能還沒爬到滿速就傳完了，所以**連線重用**（HTTP/2 多路複用、keep-alive，Ch 10）很重要（重用已經「熱身」過的連線，不用每次重新慢啟動）。壅塞控制演算法有很多（Reno、CUBIC、BBR），是活躍的研究領域，但本課不深入（記住「慢啟動 + 遇丟包縮小」的核心思想即可）。

## 故意弄壞:用 RST 看連線被拒絕

```bash
# 看「連線被拒絕」的真相：RST 封包
sudo tcpdump -i any -n 'tcp port 9999' &
curl -s --max-time 3 http://127.0.0.1:9999    # 連一個沒開的 port
# IP ...> 127.0.0.1.9999: Flags [S], seq ...        ← SYN（想連）
# IP 127.0.0.1.9999 > ...: Flags [R.], ...          ← RST！（拒絕，「這 port 沒服務」）
# curl: (7) Failed to connect ... Connection refused
sudo pkill tcpdump
#   → "Connection refused" 的真相 = 收到 RST
#   RST = 「重置」，立刻終止連線（不像 FIN 是優雅關閉）

# 對比「連線 timeout」（沒有 RST，封包石沉大海）
curl -s --max-time 3 http://192.0.2.1:80       # 連一個不存在/被防火牆 drop 的
# （沒有任何回應，等到 timeout）
# curl: (28) Connection timed out
#   → timeout = SYN 送出去沒任何回應（被 drop 或主機不存在）

# RST vs timeout 的 debug 意義：
#   Connection refused (RST)：能到達主機，但 port 沒服務（或防火牆 REJECT）
#   Connection timeout (無回應)：到不了主機，或防火牆 DROP（靜默丟棄）
```

> **「Connection refused」（收到 RST）和「Connection timeout」（無回應）是兩種不同的失敗，指向不同的問題**。**RST（Reset）** 是 TCP 的「立刻終止」信號（不像 FIN 的優雅關閉）。當你連一個「主機在、但 port 沒服務」的地方，主機回 RST——curl 顯示 "Connection refused"。當你連一個「到不了的主機」或「防火牆 DROP（靜默丟棄）的 port」，SYN 石沉大海，等到 timeout——curl 顯示 "Connection timed out"。這個區別是 debug 的金鑰：**refused = 能到達主機但服務沒開**（或防火牆設 REJECT，Ch 18）——查服務有沒有啟動、port 對不對；**timeout = 到不了主機或被靜默丟棄**（路由問題、主機關機、或防火牆設 DROP）——查路由、主機狀態、防火牆。防火牆的 DROP（不回應，造成 timeout）vs REJECT（回 RST/ICMP，造成 refused）是刻意的選擇（Ch 18）——DROP 讓掃描者不知道 port 存不存在（更隱蔽）。記住：**refused 快（有回應），timeout 慢（等到放棄）**，兩者指向完全不同的排查方向。

## 動手練習

1. 抓三次握手：`tcpdump -S` 抓一次 curl/SSH 連線，找出 SYN、SYN-ACK、ACK，看序號怎麼對應

2. 看狀態機：`ss -tan` 看當前連線狀態，`ss -tan | awk '{print $1}' | sort | uniq -c` 統計各狀態數量

3. 觀察 TIME_WAIT：跑幾個 `curl` 後立刻 `ss -tan | grep TIME-WAIT`，看 TIME_WAIT 連線，理解為什麼正常

4. RST vs timeout：`curl` 一個沒開的 port（refused/RST）vs 一個不存在的 IP（timeout），抓封包看差別

5. 看慢啟動：下載一個大檔案時用 `ss -ti`（顯示 cwnd/rtt）觀察 cwnd 怎麼成長

## 本章重點整理

- TCP 在不可靠 IP 上做出可靠位元組串流：序號（編號）+ ACK（累積確認）+ 超時/快速重傳 + 重排去重
- 三次握手雙向確認彼此能收能發（兩次不夠，伺服器無法確認 client 能收）；起始序號隨機（安全）
- 四次揮手雙向各自關閉；TIME_WAIT 是主動關閉方的正常等待（保證最後 ACK + 清迷途封包），大量 TIME_WAIT 通常正常，CLOSE_WAIT 堆積才是 bug
- 流量控制（保護接收方，滑動視窗）vs 壅塞控制（保護網路，慢啟動）；慢啟動解釋「下載速度爬升」
- RST（Connection refused，主機在但服務沒開）vs timeout（到不了或被 DROP）——debug 的關鍵區別

## 自我檢核

- [ ] 能畫出三次握手並解釋「為什麼三次不是兩次」
- [ ] 能解釋 TIME_WAIT 的作用，以及為什麼大量 TIME_WAIT 通常不是問題
- [ ] 知道 ACK 號是「期待的下一個 byte」（累積確認）
- [ ] 能區分流量控制和壅塞控制，解釋慢啟動為什麼造成「速度爬升」
- [ ] 知道 Connection refused（RST）和 timeout 的差別，各指向什麼問題

## 延伸閱讀

### 書籍

- **《TCP/IP Illustrated, Volume 1》— Ch 12-17** — Stevens & Fall
  - **讀哪幾章**：Ch 13（連線建立與終止）、Ch 15（流量控制與視窗）、Ch 16（壅塞控制）
  - **這本書的定位**：TCP 機制的權威，每個機制配真實封包；本章的完整版
  - **前提**：Ch 4-5

### 文章

- **[The TIME_WAIT state in TCP](https://vincent.bernat.ch/en/blog/2014-tcp-time-wait-state-linux)** — Vincent Bernat
  - **核心貢獻**：徹底解釋 TIME_WAIT、為什麼它存在、何時是問題、怎麼正確處理（破除「tcp_tw_recycle」的迷思）
  - **讀哪裡**：整篇（長但值得）
  - **和本章的關聯**：本章 TIME_WAIT 那節的權威深入版

- **[How TCP works](https://www.brendangregg.com/blog/2022-01-09/tcp-tracing.html)** — Brendan Gregg
  - **這篇說什麼**：用 tracing 工具觀察 TCP 內部機制
  - **為什麼值得讀**：把 TCP 機制和「怎麼觀測它」連起來（接 bpf 課）

### 官方文件

- **[RFC 9293 — Transmission Control Protocol](https://www.rfc-editor.org/rfc/rfc9293)** — IETF（2022，TCP 的最新整合版）
  - **讀哪裡**：Section 3.5（連線建立/關閉）、3.7（資料傳輸）、狀態機圖
  - **為什麼值得讀**：TCP 的權威定義（2022 整合了數十年的 RFC）；狀態機圖對照本章

下一章對比 TCP 和 UDP——什麼時候該用不需要可靠保證但更快的 UDP，以及為什麼 DNS、影音、QUIC 選 UDP。

→ [Ch 7 UDP vs TCP](./07-udp-vs-tcp.md)
