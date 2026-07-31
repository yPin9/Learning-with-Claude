# 練習 B — debug 五個網路問題

> **目標**：整合 Part 2-4 的所有知識和工具，系統化地 debug 五個真實的網路問題。每個問題模擬一個常見的故障場景，你要用學過的工具（ping/dig/ss/nc/curl/traceroute/tcpdump）找出根因。完成後你具備「系統化 debug 網路問題」的能力——這是 SRE/DevOps 最核心的技能，也是面試最常考的實戰題。

## 背景與動機

學了一堆工具（Part 4），但工具不會自己解決問題——你需要**系統化的 debug 方法**：怎麼從症狀出發、用哪個工具、怎麼縮小範圍、怎麼定位根因。這個練習用五個真實場景訓練這個能力。

這正是真實工作的樣子：有人說「網站連不上」「API 很慢」「服務時好時壞」，你要在沒有更多資訊的情況下，用工具一步步找出問題。好的工程師和新手的差別不在「會用工具」，而在「知道用哪個工具、按什麼順序、怎麼解讀」。這個練習把 Ch 2 的「分層排查」變成可操作的 debug 流程，是整個 Part 4 的能力檢驗。

## 任務規格

對下面五個問題場景，你要：
1. **重現/模擬**問題（每題提供模擬方法）
2. **系統化排查**（用學過的工具，按分層順序）
3. **定位根因**（問題出在哪一層、哪個環節）
4. **說出解法**（怎麼修）

| 問題 | 症狀 | 涉及章節 |
|---|---|---|
| 問題 1 | 域名連不上，但 IP 直連可以 | Ch 9 (DNS) |
| 問題 2 | 服務在本機通，外部連不上 | Ch 13 (監聽位址) |
| 問題 3 | 連線 timeout（vs refused） | Ch 6 (TCP)、Ch 18 (防火牆) |
| 問題 4 | 連線慢，定位慢在哪階段 | Ch 14 (時序) |
| 問題 5 | 時好時壞/間歇丟包 | Ch 16 (mtr) |

**核心要求**：每個問題都要展示「系統化的排查過程」（不是猜，是用工具逐步縮小範圍），並能說出「為什麼這樣排查」。

## 通用 debug 方法（先建立框架）

```
網路問題的系統化排查框架（Ch 2 分層 + Part 4 工具）：

  1. 釐清症狀：完全不通？慢？時好時壞？
        │
  2. 分層排查（自底向上，每層用對應工具）：
     L3 網路層：  ping（能到達主機嗎？）traceroute（路徑哪斷）
     DNS：        dig（域名解析對嗎？）
     L4 傳輸層：  nc -zv（port 通嗎？refused vs timeout）
     L7 應用層：  curl（服務回應對嗎？）
        │
  3. 縮小範圍：問題在我端 / 中間 / 對方端？
        │
  4. 確認根因 + 驗證解法
        │
  → 不要跳步、不要猜。每一步用工具「證明」這層有沒有問題
```

## 五個問題

### 問題 1：域名連不上，但 IP 直連可以（DNS）

**模擬**：
```bash
# 模擬 DNS 解析到錯的 IP（或解析失敗）
# 方法：用 curl --resolve 模擬「DNS 給錯 IP」的情況
curl -sI https://example.com --max-time 5                          # 用域名（假設失敗）
curl -sI https://example.com --resolve example.com:443:93.184.216.34 --max-time 5  # 手動給對 IP
# 如果第二個成功、第一個失敗 → DNS 問題
```

<details>
<summary>排查過程與解法</summary>

```bash
# 1. 確認是不是 DNS（域名 vs IP 直連）
curl -sI https://example.com --max-time 5                    # 失敗
dig example.com +short                                        # 看解析到什麼
# 如果沒答案 / 解析到怪 IP → DNS 問題確認

# 2. 對照其他解析器（Ch 15）
dig @8.8.8.8 example.com +short
dig @1.1.1.1 example.com +short
# 如果公共解析器給「對」的 IP，你的解析器給「錯/沒有」→ 你的解析器問題

# 3. 看你用哪個解析器
cat /etc/resolv.conf

# 4. 看 dig 的 status（Ch 15）
dig example.com | grep status
# NXDOMAIN → 域名不存在；SERVFAIL → 解析器錯

# 根因可能：解析器掛了 / DNS 快取舊值 / DNS 污染 / /etc/hosts 有錯誤條目
# 解法：換解析器（如 8.8.8.8）/ 清快取（resolvectl flush-caches）/ 檢查 /etc/hosts
cat /etc/hosts   # 檢查有沒有錯誤的靜態對應
```

**解答說明**：DNS 問題的標誌是「域名失敗、IP 直連成功」。排查順序：用 `--resolve`/IP 確認是 DNS → `dig` 看解析結果 → 對照公共解析器找出是「你的解析器」還是「域名本身」的問題 → 檢查 /etc/hosts（常被忽略的靜態覆蓋）。對應 Ch 9/15。
</details>

### 問題 2：服務在本機通，外部連不上（監聽位址）

**模擬**：
```bash
python3 -m http.server 8080 --bind 127.0.0.1 &   # 故意只聽 127.0.0.1
sleep 1
curl -sI http://127.0.0.1:8080 | head -1          # 本機通
MY_IP=$(ip route get 1.1.1.1 | grep -oP 'src \K\S+')
curl -sI http://$MY_IP:8080 --max-time 3          # 對外 IP 不通
```

<details>
<summary>排查過程與解法</summary>

```bash
# 1. 確認服務在聽什麼位址（Ch 13）
ss -tlnp | grep 8080
# LISTEN 127.0.0.1:8080 ...   ← 找到了！只聽 127.0.0.1（不對外）

# 2. 對照：本機 vs 對外 IP
curl -sI http://127.0.0.1:8080      # 通（聽 127.0.0.1，本機能連）
curl -sI http://$MY_IP:8080         # 不通（外部連不到只聽 loopback 的服務）

# 根因：服務聽 127.0.0.1（只本機），不是 0.0.0.0（對外）
# 解法：改服務設定聽 0.0.0.0
# pkill -f http.server
# python3 -m http.server 8080 --bind 0.0.0.0 &
# 然後 ss -tlnp 確認變成 0.0.0.0:8080，外部就連得上

# 進階：如果聽 0.0.0.0 還是連不上 → 可能是防火牆（Ch 18，問題 3）
```

**解答說明**：「本機通、外部不通」是監聽位址問題的招牌症狀。`ss -tlnp` 看 Local Address——127.0.0.1（只本機）vs 0.0.0.0（對外）。這是部署服務最常見的坑（Ch 13）。如果聽 0.0.0.0 還不通，往防火牆方向查（問題 3）。
</details>

### 問題 3：連線 timeout vs refused（TCP/防火牆）

**模擬**：
```bash
# refused：連一個沒服務的 port（主機在，port 沒開）
nc -zv localhost 12345                            # Connection refused（快）
# timeout：連一個被 DROP 的（防火牆靜默丟棄）—— 用一個不可達的 IP 模擬
nc -zv 192.0.2.1 80 -w 3                          # timeout（慢，等到放棄）
```

<details>
<summary>排查過程與解法</summary>

```bash
# 關鍵：區分 refused 和 timeout（Ch 6 的 RST vs 無回應）

# refused（收到 RST，快）→ 能到達主機，但 port 沒服務（或防火牆 REJECT）
nc -zv localhost 12345
# Connection refused
# → 排查：服務有啟動嗎？（ss -tlnp）port 對嗎？

# timeout（無回應，慢）→ 到不了主機，或防火牆 DROP（靜默丟棄）
nc -zv 192.0.2.1 80 -w 3
# timeout
# → 排查：主機通嗎？（ping，但 ICMP 可能也被擋）路由對嗎？（traceroute）
#         防火牆 DROP？（Ch 18）

# 系統化區分流程：
target_host="服務器"
target_port=443
ping -c2 "$target_host"            # 主機通嗎？（記得 ICMP 可能被擋）
nc -zv "$target_host" "$target_port" -w 3
# succeeded → 通
# refused → 主機在但 port 沒服務 → 查服務（ss -tlnp on server）
# timeout → 主機不通或被 DROP → 查路由（traceroute）和防火牆

# 根因與解法：
# refused → 啟動服務 / 改防火牆 REJECT→ACCEPT / 確認 port
# timeout → 修路由 / 改防火牆 DROP→ACCEPT / 確認主機在線
```

**解答說明**：refused（快，收到 RST）和 timeout（慢，無回應）指向完全不同的問題（Ch 6）。refused = 到得了主機但服務沒開（查服務）；timeout = 到不了或被 DROP（查路由/防火牆）。防火牆的 DROP（造成 timeout）vs REJECT（造成 refused）是刻意選擇（Ch 18）。這個區分能省下大量盲目排查。
</details>

### 問題 4：連線慢，定位慢在哪階段（時序分析）

**模擬與排查**：
```bash
# 用 curl -w 分解連線時序，定位慢在哪階段
curl -w '@-' -o /dev/null -s https://example.com <<'EOF'
DNS lookup:   %{time_namelookup}s
TCP connect:  %{time_connect}s
TLS handshake:%{time_appconnect}s
Start xfer:   %{time_starttransfer}s
Total:        %{time_total}s
EOF
```

<details>
<summary>排查過程與解法</summary>

```bash
# curl -w 的時序分解，看慢在哪（Ch 14）：
#   time_namelookup 大 → DNS 慢（Ch 9，換解析器/查 DNS）
#   time_connect - time_namelookup 大 → TCP 握手慢（網路延遲/RTT 高，traceroute 看路徑）
#   time_appconnect - time_connect 大 → TLS 握手慢（Ch 11，伺服器 TLS 配置/CPU）
#   time_starttransfer - time_appconnect 大 → 伺服器處理慢（後端慢，不是網路！）
#   下載階段慢 → 頻寬/壅塞

# 進一步定位：
# 如果是 DNS 慢：
dig example.com | grep 'Query time'
dig @1.1.1.1 example.com           # 換解析器對照

# 如果是 TCP/網路慢：
ping -c4 example.com               # RTT 高嗎？
mtr -c 20 example.com              # 哪一段延遲大？

# 如果是伺服器處理慢（time_starttransfer 大但 TCP/TLS 快）：
# → 不是網路問題！是伺服器後端慢（查伺服器負載/應用 log）

# 根因可能：DNS 慢 / 跨國 RTT 高 / 伺服器過載
# 解法：換 DNS / 用 CDN（縮短距離）/ 優化伺服器
```

**解答說明**：「慢」要先定位「慢在哪階段」——`curl -w` 的時序分解直接告訴你 DNS/TCP/TLS/伺服器處理各花多久。關鍵洞察：如果 `time_starttransfer` 大但 TCP/TLS 快，是**伺服器處理慢**（不是網路問題，別在網路上瞎找）。這對應 Ch 14 的「定位延遲在哪階段」，是 debug「慢」的核心方法。
</details>

### 問題 5：時好時壞/間歇丟包（mtr）

**模擬與排查**：
```bash
# 時好時壞通常是「中間某段間歇丟包」—— 用 mtr 持續監測
mtr -c 100 example.com             # 跑 100 次，看每跳的 Loss%
# 或報告模式
mtr -r -c 100 example.com
```

<details>
<summary>排查過程與解法</summary>

```bash
# 1. 確認是丟包（不是穩定的慢）
ping -c 50 example.com             # 看 packet loss%（持續丟包率）
# 如果 loss > 0% 且時間波動大 → 間歇丟包

# 2. 用 mtr 定位「哪一跳開始丟」（Ch 16）
mtr -c 100 example.com
# 看每跳 Loss%，但記住（Ch 16 的關鍵）：
#   中間跳 Loss% 高但「目標跳」0% → 中間路由器限速 ICMP（假象，封包都到了）
#   從第 N 跳「一路丟到目標」→ 真的丟包，第 N 跳那段有問題

# 3. 判斷責任段：
mtr -r -c 100 example.com          # 留報告當證據
#   第 1 跳就丟 → 你的本機/區網（換網線/重啟路由器/WiFi 訊號）
#   中間 ISP 段一路丟到目標 → ISP 或跨國線路問題（回報 ISP，附 mtr 報告）
#   只有目標附近丟 → 目標伺服器問題

# 4. 區分有線/無線（如果是本地）
#   WiFi 容易間歇丟包（干擾/訊號）→ 換有線測試對照

# 根因可能：WiFi 干擾 / ISP 線路品質 / 跨國段尖峰壅塞 / 目標過載
# 解法：依責任段——換有線 / 回報 ISP / 用 CDN 或換路徑 / 連絡服務方
```

**解答說明**：「時好時壞」是最惱人的問題，因為它間歇出現。`mtr` 持續監測能抓到間歇丟包，但要正確解讀（Ch 16）——看「丟包是否一路延續到目標」，別被中間跳的 ICMP 限速假象騙。定位責任段（你/ISP/目標）決定你能不能解和該找誰。這是 Ch 16 的核心能力。
</details>

## 測試用案例

| 問題 | 關鍵工具 | 定位點 |
|---|---|---|
| 域名連不上 | dig, curl --resolve | DNS（解析器/快取/hosts）|
| 外部連不上 | ss -tlnp | 監聽 127.0.0.1 vs 0.0.0.0 |
| timeout vs refused | nc -zv | TCP（服務）vs 防火牆（DROP）|
| 慢 | curl -w | 哪階段（DNS/TCP/TLS/伺服器）|
| 時好時壞 | mtr | 哪一跳丟包（責任段）|

## 延伸挑戰（加分）

- **挑戰一**：寫一個 `netdebug.sh` 腳本——給一個目標（域名或 IP），自動跑完整的分層排查（dig→ping→nc→curl→mtr），輸出每層的狀態，幫你快速定位問題（整合 linux_commands 課的 scripting）

- **挑戰二**：用 tcpdump（Ch 14）抓問題 3（timeout）的封包，看 SYN 送出去但沒有 SYN-ACK（對照 Ch 6 的握手），親眼看「無回應」

- **挑戰三**：模擬 MTU 黑洞（Ch 4）——用 `tc` 或調 MTU 造出「小封包通、大封包不通」，用 ping -M do 診斷

- **挑戰四**：用 netns（Ch 0/20）建一個有「故意的問題」的拓樸（如閘道路由錯、防火牆擋某 port），讓別人 debug

- **挑戰五**：把五個問題做成一個「故障注入」實驗——隨機注入一個問題，盲測自己能不能用系統化流程找出來

## 自我檢核

- [ ] 能對一個「連不上」的問題，按分層順序系統化排查（不是猜）
- [ ] 知道每個工具驗證哪一層（ping=L3、nc=L4、curl=L7、dig=DNS）
- [ ] 能區分 refused（服務問題）和 timeout（路由/防火牆問題）
- [ ] 會用 curl -w 定位「慢在哪階段」
- [ ] 會用 mtr 定位丟包的責任段，並正確解讀（避開 ICMP 限速假象）

這個練習把 Part 4 的工具串成了系統化的 debug 能力。接下來 Part 5 進入 Linux 的網路機制——防火牆（iptables/nftables）和虛擬網路（netns/tun-tap/bridge），這是 VPN 和容器網路的根基。

→ [Ch 18 iptables 完整](./18-iptables-complete.md)
