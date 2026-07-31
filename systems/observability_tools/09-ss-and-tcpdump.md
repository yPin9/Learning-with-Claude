# Ch 9 — ss 與 tcpdump

> **目標**：從「觀察程式的網路行為」角度掌握 ss（看連線狀態）和 tcpdump（抓封包）——一個程式開了哪些連線、卡在哪個狀態、實際送收什麼封包。這章不重複 networking 課的協定細節，而是聚焦「用這兩個工具 debug 一個程式的網路問題」：連不上、卡住、慢、送錯資料。把網路觀察整合進本課的「觀察程式行為」框架。

> **環境**：Linux，ss（iproute2）+ tcpdump。tcpdump 需 root/CAP_NET_RAW。

## 為什麼從「觀察程式」角度看網路工具？

ss 和 tcpdump 是強大的網路工具（networking 課深入講過協定細節）。但在本課的脈絡，我們關注的是「**用它們 debug 一個程式的網路行為**」——這個程式開了哪些連線？卡在哪個連線狀態？實際送收什麼封包？和 strace（看 connect/send/recv syscall）、lsof（看網路 fd）配合，組成觀察程式網路行為的完整視角。

當一個程式「連不上某服務」「網路卡住」「送錯資料」，你需要多層觀察：strace 看它呼叫了什麼網路 syscall（connect 到哪、結果如何）、ss 看連線的當前狀態、tcpdump 看實際的封包。這章把 ss/tcpdump 整合進「觀察程式行為」——它們是網路層的觀察工具，補上 strace/lsof 的網路視角。

> networking 課的 Ch 13（ss）、Ch 14（tcpdump）深入講了協定和工具。如果你修過那課，這章是「從 debug 程式的角度」複習應用。如果沒修過，這章夠你 debug 程式的網路問題（協定深度去看 networking 課）。

## 先建立直覺:三層網路觀察

```
觀察程式的網路行為（三層，互補）：

  程式
    │ 網路 syscall（socket/connect/send/recv）
    ▼ ← strace 看「程式做了什麼網路 syscall」（Ch 5）
  ┌──────────────────────┐
  │  socket（fd）         │ ← lsof -i 看「程式開了哪些網路 fd」（Ch 8）
  └──────────┬───────────┘
    │ TCP/IP stack
    ▼ ← ss 看「連線的當前狀態」（ESTAB/TIME-WAIT/...）
  ┌──────────────────────┐
  │  封包進出網卡         │ ← tcpdump 看「實際的封包」
  └──────────────────────┘
        │
  → debug 程式網路問題的多層觀察：
    strace：程式呼叫了什麼（connect 到哪、結果）
    lsof -i：開了哪些連線（fd 視角）
    ss：連線狀態（卡在哪個狀態）
    tcpdump：實際封包（最底層的真相）
```

關鍵心智：觀察程式的網路行為有多層——**strace**（程式呼叫了什麼網路 syscall）、**lsof -i**（程式開了哪些網路 fd）、**ss**（連線的當前狀態）、**tcpdump**（實際的封包）。debug 網路問題時，從上層往下層看（strace 看程式做了什麼 → ss 看連線狀態 → tcpdump 看封包真相）。

## ss:看程式的連線狀態

```bash
# === 看一個程式開了哪些連線、什麼狀態 ===
# 啟動一個有網路的程式
python3 -m http.server 8080 &
PID=$!

# ss 看它的監聽和連線
ss -tlnp | grep 8080
# LISTEN 0 ... 0.0.0.0:8080 ... users:(("python3",pid=...))   ← 在監聽

# 看所有連線狀態（debug 程式的網路）
ss -tanp | grep python3
# 看這個程式的所有 TCP 連線和狀態

# === debug 程式的網路問題（從狀態看） ===
# 連線卡在 SYN-SENT → 連不上（對方沒回，Ch 6 networking）
ss -tanp state syn-sent
# 大量 CLOSE-WAIT → 程式 bug（收到 FIN 但沒 close，fd 洩漏！）
ss -tanp state close-wait
# 大量 TIME-WAIT → 通常正常（高頻短連線）
ss -tanp state time-wait | wc -l

# === ss 的核心選項（debug 用）===
ss -tlnp          # 監聽的 TCP（這程式開了什麼服務）
ss -tanp          # 所有 TCP 連線 + 狀態 + 程式
ss -s             # 連線統計摘要
ss -tip           # TCP 內部資訊（cwnd/rtt，看慢的原因）
kill $PID
```

> **ss 從「連線狀態」角度 debug 程式網路問題——大量 CLOSE-WAIT 是程式 bug（收到 FIN 沒 close）的招牌**。ss 看程式的連線**狀態**（對應 networking 課 Ch 6 的 TCP 狀態機），不同的狀態堆積指向不同問題：**SYN-SENT 堆積**（程式 connect 了但對方沒回 SYN-ACK）= 連不上（對方服務沒開/防火牆擋/網路問題）；**CLOSE-WAIT 堆積** = **程式 bug**！這是招牌信號——程式收到對方的 FIN（對方要關連線）但**忘了 close** 自己這端，連線卡在 CLOSE-WAIT，fd 洩漏（這是真實的程式 bug，比 TIME-WAIT 嚴重）；**TIME-WAIT 堆積** = 通常正常（高頻短連線的正常現象，networking Ch 6）。所以 debug 程式網路：`ss -tanp | grep <程式>` 看它的連線狀態分布——如果大量 CLOSE-WAIT，去查程式「有沒有忘了 close 連線」。`ss -tlnp`（看程式開了什麼服務、聽在哪個位址——127.0.0.1 vs 0.0.0.0 的問題，networking Ch 13）、`ss -tip`（看 TCP 內部如 cwnd/rtt，debug 慢的連線）。ss 的「狀態視角」補充了 strace（看 syscall）和 lsof（看 fd）——它告訴你「連線現在處於什麼狀態」，這對 debug「連線卡住/洩漏」很關鍵。

## tcpdump:看程式的實際封包

```bash
# === 抓一個程式的網路封包（看實際送收什麼）===
# 抓特定 port 的封包
sudo tcpdump -i any -n 'port 8080' -c 20
# 看到 SYN/SYN-ACK/ACK（握手）、資料封包、FIN（關閉）

# 抓特定主機的封包（debug 程式連某 server）
sudo tcpdump -i any -n 'host example.com' -c 20

# 抓並看內容（-A ASCII，看明文協定如 HTTP）
sudo tcpdump -i any -n -A 'port 80' -c 20
# 能看到 HTTP 請求/回應的明文（HTTPS 是加密的，看不到內容）

# === debug 程式網路問題（封包視角）===
# 程式說連不上 → 抓封包看「SYN 有送出嗎？有回應嗎？」
sudo tcpdump -i any -n 'host <目標> and tcp[tcpflags] & tcp-syn != 0'
# 只看到 SYN（沒 SYN-ACK）→ 對方沒回（連不上的真相）

# 程式慢 → 抓封包看「哪一步慢」（握手？傳輸？重傳？）
sudo tcpdump -i any -n 'host <目標>' -ttt    # -ttt 顯示封包間隔時間
# 看哪兩個封包之間隔很久 = 慢在那一步

# 程式送錯資料 → 抓封包看「實際送了什麼」
sudo tcpdump -i any -n -A 'port <port>'
# 看程式實際送出的內容（對照預期）
```

> **tcpdump 看「實際的封包」——它是網路問題的最底層真相，debug「程式說連不上但不知為什麼」的終極手段**。當 strace 顯示 `connect = -1`（連不上）但你不知道為什麼，tcpdump 看封包揭露真相：**只看到 SYN 沒有 SYN-ACK** = 對方沒回應（服務沒開、防火牆 DROP、網路不通）；**看到 RST** = 對方拒絕（服務沒開回 RST）；**看到 SYN-ACK 但程式沒繼續** = 程式端問題。對「**程式慢**」，`tcpdump -ttt`（顯示封包間隔）看「哪兩個封包之間隔很久」——握手慢（網路延遲）？傳輸中某步慢（對方處理慢）？有重傳（丟包）？對「**程式送錯資料**」，`tcpdump -A`（顯示 ASCII 內容）看程式實際送出什麼（明文協定如 HTTP 看得到，對照預期找出送錯的）。tcpdump 是「實際發生什麼」的最底層真相——當上層工具（strace 看 syscall、ss 看狀態）說不清楚時，封包是最終的證據。這呼應本課的核心——觀察「實際行為」而非「以為的行為」，tcpdump 是網路層的「實際行為」觀察。配合 strace（程式做了什麼網路 syscall）+ ss（連線狀態）+ tcpdump（封包真相），你有了 debug 程式網路問題的完整視角。注意 HTTPS 加密內容看不到（要 SSLKEYLOGFILE 解密，networking 練習 A）。

## 整合:strace + ss + tcpdump debug 網路

```bash
# 完整的「程式網路問題」debug 流程（多層觀察）
cd ~/obslab
cat > netbug.c <<'EOF'
#include <stdio.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
int main() {
    int s = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(9999);              // 連 port 9999
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);
    if (connect(s, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
        perror("connect");                     // 沒人聽 9999 → 失敗
        return 1;
    }
    printf("Connected\n"); close(s); return 0;
}
EOF
gcc -o netbug netbug.c

# 層 1：strace 看程式做了什麼網路 syscall
strace -e trace=network ./netbug
# socket(AF_INET, SOCK_STREAM, ...) = 3
# connect(3, {...sin_port=htons(9999), sin_addr="127.0.0.1"...}) = -1 ECONNREFUSED
# → strace 直接顯示「connect 到 127.0.0.1:9999 失敗，ECONNREFUSED」
#   ECONNREFUSED = 對方主動拒絕（port 9999 沒人聽）

# 層 2：ss 確認「9999 真的沒人聽」
ss -tlnp | grep 9999    # （空的，確認沒人聽 9999）

# 層 3：tcpdump 看封包（看到 SYN 然後 RST）
# sudo tcpdump -i lo -n 'port 9999' &
# ./netbug
# → SYN 送出，立刻收到 RST（拒絕）= ECONNREFUSED 的封包真相

# → 三層觀察一致地指出：連 9999 失敗，因為沒人聽（ECONNREFUSED）
#   修法：先啟動聽 9999 的服務，或連對的 port
```

> **strace + ss + tcpdump 三層觀察一致地定位網路問題——strace 顯示 ECONNREFUSED、ss 確認沒人聽、tcpdump 看到 RST**。這個整合範例展示多層觀察怎麼協同：程式 connect 到沒人聽的 port 9999，**strace -e network** 直接顯示 `connect(...9999...) = -1 ECONNREFUSED`（最快——syscall 層直接告訴你連哪、失敗原因）；**ss** 確認「9999 真的沒人聽」（從監聽狀態確認）；**tcpdump** 看到「SYN 送出立刻收到 RST」（封包層的真相——ECONNREFUSED 對應的就是收到 RST，networking Ch 6）。三層一致指向「連 9999 失敗，因為沒人聽」。這展示了本課的「分層觀察」威力——同一個問題在不同層（syscall/狀態/封包）觀察，互相印證，定位根因。實務中通常 strace 就夠（直接看到 connect 的結果和 errno），但複雜問題（如連得上但行為怪、慢、間歇失敗）需要 ss（狀態）和 tcpdump（封包）補充。記住 debug 程式網路的順序：**strace 先看程式做了什麼網路 syscall（最快定位）→ ss 看連線狀態 → tcpdump 看封包真相**。這把網路觀察整合進了本課的「觀察程式行為」框架——網路問題也是「程式的行為」，用分層觀察解。

## 故意弄壞:CLOSE-WAIT 洩漏

```bash
# 製造並偵測 CLOSE-WAIT 洩漏（程式忘了 close 連線的 bug）
cd ~/obslab
# 一個 server，收到連線但「故意不 close」（模擬 bug）
cat > leaky_server.c <<'EOF'
#include <stdio.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <unistd.h>
int main() {
    int s = socket(AF_INET, SOCK_STREAM, 0);
    int opt = 1; setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET; addr.sin_port = htons(7777);
    addr.sin_addr.s_addr = INADDR_ANY;
    bind(s, (struct sockaddr*)&addr, sizeof(addr));
    listen(s, 10);
    printf("listening on 7777\n");
    while (1) {
        int c = accept(s, NULL, NULL);   // 接受連線
        // 故意不 close(c)！→ client 斷線後，連線卡在 CLOSE-WAIT
        char buf[10]; read(c, buf, 10);  // 讀一下
        // 忘了 close(c) → CLOSE-WAIT 累積（fd 洩漏）
    }
    return 0;
}
EOF
gcc -o leaky_server leaky_server.c
./leaky_server &
SERVER_PID=$!
sleep 1

# 連線然後斷開（製造 CLOSE-WAIT）
for i in 1 2 3; do
    (exec 3<>/dev/tcp/127.0.0.1/7777; echo "hi" >&3; exec 3<&-) 2>/dev/null
done
sleep 1

# 用 ss 偵測 CLOSE-WAIT 累積（bug 的證據）
ss -tanp state close-wait | grep 7777
# 看到 CLOSE-WAIT 連線累積！→ server 收到 FIN 但沒 close
# 用 lsof / /proc 看 fd 也在累積
ls /proc/$SERVER_PID/fd | wc -l    # fd 數量（漲 = 洩漏）
kill $SERVER_PID
# → CLOSE-WAIT 累積 = 程式忘了 close 連線的 bug（修法：accept 後處理完要 close）
```

> **CLOSE-WAIT 累積是「程式忘了 close 連線」bug 的招牌——ss 一眼看出，這是真實的伺服器 bug**。這個實驗製造了真實的伺服器 bug——server `accept` 連線後**忘了 close**，所以 client 斷線（送 FIN）後，連線卡在 **CLOSE-WAIT**（networking Ch 6：收到對方 FIN，自己這端還沒 close）。`ss -tanp state close-wait` 一眼看出累積的 CLOSE-WAIT——這是「程式有 fd/連線洩漏 bug」的招牌信號。配合 `/proc/<pid>/fd` 數量增長（每個沒 close 的連線是一個洩漏的 fd），確認洩漏。這在生產環境是嚴重 bug——長期運行的 server 如果每個連線都忘了 close，CLOSE-WAIT 和 fd 會一直累積，最終「too many open files」（fd 耗盡）讓 server 無法接受新連線。**修法**：server 處理完每個連線後 `close(c)`。debug 這類問題的關鍵是 ss 的狀態視角——`state close-wait` 直接顯示「有連線卡在這個狀態」，指出「程式沒正確關閉連線」。這呼應 Ch 8 的 fd 洩漏（lsof）和練習 A 的 Bug 4——fd/連線洩漏是常見的伺服器 bug，ss（連線狀態）和 lsof/proc（fd 數量）是偵測它的工具。掌握 CLOSE-WAIT 的意義，你能 debug 最常見的伺服器資源洩漏問題。

## 動手練習

1. ss 看程式：跑一個有網路的程式，用 `ss -tanp | grep <程式>` 看它的連線狀態

2. tcpdump 看封包：抓一個程式的網路封包（`tcpdump port <port>`），看握手/資料/關閉

3. 三層觀察：對 netbug.c（連不存在的 port）用 strace/ss/tcpdump，看三層怎麼一致指向 ECONNREFUSED

4. 看慢：用 `tcpdump -ttt` 看封包間隔，找「慢在哪一步」

5. 跑「故意弄壞」：製造 CLOSE-WAIT 洩漏，用 `ss state close-wait` 偵測，理解這個 bug

## 本章重點整理

- 觀察程式網路行為分層：strace（網路 syscall）、lsof -i（網路 fd）、ss（連線狀態）、tcpdump（封包真相）
- ss 看連線狀態 debug：SYN-SENT 堆積=連不上、CLOSE-WAIT 堆積=程式忘了 close（bug）、TIME-WAIT=通常正常
- tcpdump 看實際封包：連不上看 SYN 有沒有回應（SYN-ACK/RST/無）、慢看封包間隔（-ttt）、送錯看內容（-A）
- 整合 debug：strace 先看程式做了什麼網路 syscall（最快）→ ss 看狀態 → tcpdump 看封包真相
- CLOSE-WAIT 累積是「程式忘了 close 連線」bug 的招牌（fd/連線洩漏），ss 一眼看出

## 自我檢核

- [ ] 知道觀察程式網路行為的多層（strace/lsof/ss/tcpdump）和各自的角度
- [ ] 會用 ss 看程式的連線狀態，理解 CLOSE-WAIT 堆積是 bug
- [ ] 會用 tcpdump 看程式的實際封包，debug 連不上/慢/送錯
- [ ] 能整合三層觀察定位網路問題（如 ECONNREFUSED）
- [ ] 能偵測 CLOSE-WAIT 洩漏（程式忘了 close 連線）

## 延伸閱讀

### 本課相關

- **networking 課 Ch 13（ss）、Ch 14（tcpdump）**
  - **為什麼值得讀**：協定細節和工具完整用法的深入版；本章聚焦「debug 程式」，協定深度看那裡

### 文章

- **[Debugging network issues with ss and tcpdump](https://www.brendangregg.com/blog/2021-09-26/tcp-tracing.html)** — Brendan Gregg
  - **這篇說什麼**：用工具觀察 TCP 行為 debug
  - **為什麼值得讀**：把網路觀察放進 debug 脈絡

### 官方文件

- **[ss(8)](https://man7.org/linux/man-pages/man8/ss.8.html)** + **[tcpdump(1)](https://www.tcpdump.org/manpages/tcpdump.1.html)**
  - **為什麼值得讀**：兩個工具的權威；ss 的狀態過濾、tcpdump 的 filter 語法

下一章看 sysstat 家族（vmstat/iostat/pidstat/sar）——系統資源的統計觀察。從「單一 process」擴展到「系統整體資源」（CPU/記憶體/IO），debug 效能和資源問題。

→ [Ch 10 sysstat 家族（vmstat/iostat/pidstat/sar）](./10-sysstat-family.md)
