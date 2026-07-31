# 練習 A — 用 Wireshark 解剖一次 HTTPS

> **目標**：整合 Part 2-3 的所有知識，用 Wireshark（或 tcpdump）抓取並完整解剖「打開一個 HTTPS 網站」的全部封包——從 DNS 解析（Ch 9）、TCP 三次握手（Ch 6）、TLS 握手（Ch 11）、到 HTTP 請求（Ch 10）。完成後你能親眼看到 Ch 1「封包旅程」的每一步真實發生，把抽象的協定變成眼睛看到的 bytes。這是把前 12 章串起來的綜合練習。

## 背景與動機

你學完了 TCP/IP 核心（Part 2）和應用層協定（Part 3）。但這些知識如果只停在「讀過」，很容易忘。這個練習讓你**親眼看見**它們在一次真實的網頁訪問中協同運作——DNS 怎麼先解析、TCP 怎麼握手、TLS 怎麼協商、HTTP 怎麼請求。

這正是網路工程師、SRE、資安人員的核心技能：**抓封包並讀懂它**。當「網站連不上」「TLS 握手失敗」「連線很慢」時，抓封包分析是最終極的 debug 手段——它讓你看到「實際發生了什麼」而非「應該發生什麼」。Wireshark 是這個技能的標準工具。完成這個練習，你就具備了「用封包分析 debug」的入門能力，這會貫穿整門課後面的所有 debug（練習 B、VPN 除錯、翻牆分析）。

## 任務規格

抓取並分析一次完整的 HTTPS 訪問（建議用 `example.com` 或 `httpbin.org` 這種單純的站，避免太多資源干擾）：

| 階段 | 要找出的封包 | 對應章節 |
|---|---|---|
| 1. DNS 解析 | DNS query + response | Ch 9 |
| 2. TCP 握手 | SYN, SYN-ACK, ACK | Ch 6 |
| 3. TLS 握手 | ClientHello, ServerHello, Certificate | Ch 11 |
| 4. HTTP 請求 | (加密的) Application Data | Ch 10 |
| 5. TCP 揮手 | FIN, ACK | Ch 6 |

**任務要求**：
- 抓到完整的一次 HTTPS 訪問（從 DNS 到連線關閉）
- 在抓到的封包裡，**標出**上述每個階段的封包（第幾號封包）
- 對每個階段，說出該封包的關鍵欄位（如 TCP 的 seq/flags、TLS 的版本/加密套件）
- 解釋「為什麼 HTTP 內容看不到」（因為 TLS 加密了）
- **進階**：用 `SSLKEYLOGFILE` 解密 TLS，看到加密的 HTTP 內容

## 期望輸出範例

```
完整的封包序列（理想情況）：
No.  Time      Source        Dest          Protocol  Info
1    0.000     192.168.1.100 192.168.1.1   DNS       Standard query A example.com
2    0.015     192.168.1.1   192.168.1.100 DNS       Response A 93.184.216.34
3    0.016     192.168.1.100 93.184.216.34 TCP       54321→443 [SYN] seq=0
4    0.150     93.184.216.34 192.168.1.100 TCP       443→54321 [SYN,ACK] seq=0 ack=1
5    0.151     192.168.1.100 93.184.216.34 TCP       54321→443 [ACK] ack=1
6    0.152     192.168.1.100 93.184.216.34 TLSv1.3   Client Hello
7    0.290     93.184.216.34 192.168.1.100 TLSv1.3   Server Hello, Certificate...
8    0.300     192.168.1.100 93.184.216.34 TLSv1.3   Application Data (加密的 HTTP)
...
N    ...       ...           ...           TCP       [FIN,ACK]
```

## 如果你卡住了

1. 抓封包前先想好「過濾條件」——只抓目標的流量，否則背景雜訊太多（`host example.com or port 53`）
2. DNS 在 port 53，先抓到它才知道目標 IP，後面用那個 IP 過濾
3. TCP 握手是連續的三個小封包（SYN→SYN-ACK→ACK），flags 欄位是關鍵
4. TLS 的 ClientHello 是 TCP 握手後的第一個大封包（含一堆加密套件）
5. 看不到 HTTP 內容是正常的（被 TLS 加密了）——這正是 TLS 在做的事
6. Wireshark 的「Follow TCP Stream」能把一個連線的所有封包串起來看
7. 用 `Statistics > Flow Graph` 看整個連線的時序圖（最直觀）

## 實作步驟建議

### Step 1：開始抓封包 + 觸發一次 HTTPS 訪問
### Step 2：找出 DNS 解析階段（query + response）
### Step 3：找出 TCP 三次握手
### Step 4：找出 TLS 握手（ClientHello → ServerHello → Certificate）
### Step 5：找出 HTTP（加密的 Application Data）+ 連線關閉
### Step 6（進階）：解密 TLS 看到 HTTP 明文

## 完整參考解答

**自己抓一次再看！** 親手抓和讀別人的分析，學習效果差很多。

<details>
<summary>完整操作步驟</summary>

### 用 tcpdump 抓（命令列）

```bash
cd ~/netlab/captures

# 1. 先清 DNS 快取（確保會有 DNS 查詢，Ch 9）
sudo resolvectl flush-caches 2>/dev/null || true

# 2. 開始抓（抓到檔案，之後用 Wireshark 開）
#    過濾：目標主機 + DNS（port 53）
sudo tcpdump -i any -w https-trace.pcap \
    'host example.com or port 53' &
TCPDUMP_PID=$!

# 3. 等一下，觸發一次 HTTPS 訪問
sleep 1
curl -sI https://example.com > /dev/null

# 4. 停止抓
sleep 1
sudo kill $TCPDUMP_PID

# 5. 用 Wireshark 開（圖形化分析）
wireshark https-trace.pcap &
#    或命令列快速看：
tcpdump -r https-trace.pcap -n
```

### 在 Wireshark 裡分析

```
1. DNS 階段（過濾 dns）：
   過濾欄輸入：dns
   → 看到 "Standard query A example.com" 和 "Response A 93.184.216.34"
   → 展開 Response，看 Answers 裡的 IP（這就是後面 TCP 連的目標）

2. TCP 握手（過濾 tcp.flags.syn==1）：
   過濾：tcp.flags.syn==1
   → 看到 SYN（[S]）和 SYN-ACK（[S.]）
   → 展開看 Sequence number、Window size、TCP Options（MSS 等，Ch 3/6）

3. TLS 握手（過濾 tls.handshake）：
   過濾：tls.handshake
   → Client Hello：展開看
       - Version（TLS 1.3）
       - Cipher Suites（客戶端支援的加密套件清單，Ch 11）
       - Server Name Indication（SNI，告訴伺服器要哪個域名）
   → Server Hello：選定的加密套件
   → Certificate：伺服器的憑證鏈（Ch 11 的信任鏈）

4. HTTP（過濾 tls.app_data）：
   過濾：tls.record.content_type == 23  （Application Data）
   → 看到 "Application Data" —— 但內容是「加密的」！
   → 這就是 TLS 在做的事：HTTP 內容被加密，看不到明文

5. 連線關閉（過濾 tcp.flags.fin==1）：
   → 看到 FIN，連線進入關閉流程（Ch 6 的四次揮手）
```

### Step 6：解密 TLS 看 HTTP 明文（進階）

```bash
# 用 SSLKEYLOGFILE 讓瀏覽器/curl 記錄 TLS session key
# 然後 Wireshark 用這個 key 解密

export SSLKEYLOGFILE=~/netlab/captures/sslkeys.log

# 重新抓 + 用 curl（curl 會把 session key 寫進 SSLKEYLOGFILE）
sudo tcpdump -i any -w https-decrypt.pcap 'host example.com' &
TCPDUMP_PID=$!
sleep 1
SSLKEYLOGFILE=~/netlab/captures/sslkeys.log curl -sI https://example.com > /dev/null
sleep 1
sudo kill $TCPDUMP_PID

# 在 Wireshark 設定 SSL key log file：
# Edit > Preferences > Protocols > TLS > (Pre)-Master-Secret log filename
#   選 ~/netlab/captures/sslkeys.log
# 然後開 https-decrypt.pcap
# → 現在 Application Data 能被解密！看到明文的 HTTP 請求/回應
#   過濾 http2 或 http，看到 GET / HTTP/2、HTTP/2 200 OK
```

**解答說明**：

- **過濾的重要性**：抓封包時背景雜訊很多，用 `host` + `port 53` 過濾只抓目標流量，分析時再用 Wireshark 的顯示過濾（`dns`/`tls.handshake` 等）聚焦
- **DNS 先行**：DNS（Ch 9）一定在最前面——沒有 IP 就不能建 TCP 連線。先抓 DNS 才知道後面 TCP 連的目標 IP
- **TCP 握手的三個封包**：SYN/SYN-ACK/ACK（Ch 6），flags 欄位是 `[S]`/`[S.]`/`[.]`。展開能看到 seq、window、MSS（Ch 3 的 MTU 相關）
- **TLS ClientHello 的 SNI**：注意 ClientHello 裡的 SNI（Server Name Indication）——它**明文**告訴伺服器要哪個域名（即使後面加密，SNI 還是洩漏了你訪問哪個網站，這是隱私問題，ECH/加密 SNI 想解決，Ch 31 翻牆相關）
- **HTTP 被加密**：Application Data 看不到明文——這正是 TLS 的價值（Ch 11）。中間人抓到也是亂碼
- **SSLKEYLOGFILE 解密**：這是合法的「解密自己流量」（你有 session key）——用於 debug TLS 應用。它能看到加密的 HTTP 內容，是分析 HTTPS 應用的利器

</details>

## 測試用案例

| 觀察項 | 預期看到 | 驗證的概念 |
|---|---|---|
| DNS query/response | example.com → IP | Ch 9 解析 |
| TCP SYN/SYN-ACK/ACK | 三個握手封包 | Ch 6 握手 |
| TLS ClientHello | 加密套件清單 + SNI | Ch 11 協商 |
| TLS Certificate | 憑證鏈 | Ch 11 信任鏈 |
| Application Data | 加密的（看不到明文）| Ch 11 加密 |
| TCP FIN | 連線關閉 | Ch 6 揮手 |
| (進階) 解密後 | GET / HTTP/2 明文 | TLS 解密 |

## 延伸挑戰（加分）

- **挑戰一**：比較 TLS 1.2 和 1.3 的握手——用 `curl --tlsv1.2` 強制 1.2，抓封包對比 1.3，數握手的往返次數（RTT），驗證 Ch 11 說的「1.3 更快」

- **挑戰二**：比較 HTTP/1.1、HTTP/2、HTTP/3——抓三種版本的訪問，看 HTTP/3 是 UDP（QUIC）而非 TCP，對照 Ch 10 的演進

- **挑戰三**：分析 TCP 的視窗與重傳——下載一個大檔案，在 Wireshark 看 TCP 的 window size 變化、有沒有重傳（Statistics > TCP Stream Graphs），驗證 Ch 6 的流量/壅塞控制

- **挑戰四**：抓一次失敗的連線——連一個沒開的 port（看 RST）或 TLS 憑證錯的站（badssl.com），分析失敗封包，對照 Ch 6（RST）和 Ch 11（憑證）

- **挑戰五**：用 Wireshark 的 Flow Graph（Statistics > Flow Graph）畫出整個連線的時序圖，對照 Ch 1 的「封包旅程」圖

## 自我檢核

- [ ] 能抓取一次完整的 HTTPS 訪問，並在封包裡標出 DNS/TCP/TLS/HTTP 各階段
- [ ] 能說出每個階段關鍵封包的重要欄位（TCP flags/seq、TLS 版本/套件）
- [ ] 理解為什麼 HTTP 內容看不到（TLS 加密），以及怎麼用 SSLKEYLOGFILE 解密
- [ ] 能用 Wireshark 的過濾、Follow Stream、Flow Graph 分析封包
- [ ] 體會到「抓封包看實際發生什麼」是 debug 網路的終極手段

這個練習把 Part 2-3 的協定知識綜合成了「親眼看見的封包」。接下來 Part 4 深入網路工具——這些工具讓你在不同層次「看見」和「操作」網路，是 debug 的武器庫。

→ [Ch 13 ip / ss / route](./13-ip-ss-route.md)
