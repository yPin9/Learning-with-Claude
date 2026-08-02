# Ch 24 — 網路鑑識：Zeek/Suricata/PCAP/C2

> **目標：** 從 PCAP 到 Zeek 的高階 log，掌握網路鑑識的四個層次——原始封包分析、高階連線日誌、IDS 告警、C2 行為指紋——並把你攻擊課學的 C2/exfil 技術反轉成偵測策略。
> **環境：** Linux（任意 distro），Wireshark/tshark，Zeek 3.x+，Suricata 7.x；沒有真實入侵 PCAP 時，以 PCAP 欄位格式和工具語法示範為主，真實工具輸出標（示意，依環境而異）。

---

## 為什麼網路鑑識與主機鑑識要搭配？

你在攻擊課學過：攻擊者在主機上做完了很多清理，但 **網路封包是攻擊者沒辦法在目標端清掉的**——它們在 transit 裡，存在於網路設備、IDS sensor、防火牆的 log 裡。

主機鑑識看「做了什麼」，網路鑑識看「通了哪裡、送了什麼」。好的調查把兩者對齊：主機上的 cron job 在 03:12 啟動，網路 log 裡 03:12 有一筆從這台機器到 185.x.x.x 的 HTTPS 連線，就把攻擊鏈的兩端接起來了。

---

## 先建立直覺：網路鑑識的四個層次

```
Layer 4 — PCAP（原始）
  完整重播，看 payload，最詳細，最大，需要 long-term storage
        │
        ▼
Layer 3 — Zeek logs（高階 log）
  自動解析出 conn/dns/http/ssl/x509 等結構化欄位
  儲存量是 PCAP 的 1/100，適合長期留存
        │
        ▼
Layer 2 — Suricata alert（IDS 告警）
  基於規則比對的即時告警，含 rule ID/sid 可對映 CVE/TTP
        │
        ▼
Layer 1 — Netflow/IPFIX（流量摘要）
  只有 src/dst/port/bytes，無 payload，適合超長期留存
```

實務上，中大型 SOC 會同時保留 Zeek logs + Suricata alerts，PCAP 只留 7-30 天（或用 triggered PCAP：只在告警時留下前後的封包）。

---

## PCAP 分析：tshark 實戰

Wireshark 適合互動式，`tshark` 適合腳本和大檔案。

### 基本過濾語法

```bash
# 看某個 IP 的所有流量
tshark -r capture.pcap -Y 'ip.addr == 192.168.1.100'

# 找非常規 port 的連線（不是 80/443/22）
tshark -r capture.pcap -Y 'tcp.port != 80 and tcp.port != 443 and tcp.port != 22 and tcp.flags.syn == 1 and tcp.flags.ack == 0' -T fields -e ip.src -e ip.dst -e tcp.dstport

# 看 DNS 查詢
tshark -r capture.pcap -Y 'dns.flags.response == 0' -T fields -e frame.time -e ip.src -e dns.qry.name

# 找超大 HTTP POST（可能是 exfil）
tshark -r capture.pcap -Y 'http.request.method == "POST" and http.content_length > 100000' -T fields -e ip.src -e ip.dst -e http.request.uri -e http.content_length

# 解密 TLS（如果有 SSLKEYLOGFILE）
tshark -r capture.pcap -o 'tls.keylog_file:/path/to/keylog.txt' -Y 'http2 or http'
```

### 從 PCAP 提取 artifact

```bash
# 提取所有 HTTP 傳輸的物件（binary、script 等）
tshark -r capture.pcap --export-objects http,/tmp/http_objects/

# 跟著 TCP stream 看完整對話
tshark -r capture.pcap -q -z follow,tcp,ascii,0
# 0 是 stream index，改 1、2... 看不同 session

# 統計所有連線（類似 Zeek conn.log 的摘要）
tshark -r capture.pcap -q -z conv,tcp
```

---

## Zeek：把 PCAP 變成結構化日誌的金礦

**Zeek**（前身 Bro）不是 IDS，是**網路分析框架**。它把原始封包解析成一系列結構化的 log 文件，每個協定一個 log。這些 log 是長期儲存的主力，也是鑑識時的第一個查詢對象。

### 核心 Log 文件

#### conn.log — 連線摘要

每一條 TCP/UDP 連線一行，包含：

```
ts          uid               id.orig_h      id.orig_p  id.resp_h      id.resp_p  proto  duration  orig_bytes  resp_bytes  conn_state  missed_bytes  local_orig  local_resp
1754019164  CXY2dj3pqfmZ9...  192.168.1.100  49231      185.220.101.5  443        tcp    3600.123   2048        15728640    SF          0             true        false
```

**conn_state 欄位**非常有用：

| conn_state | 意義 |
|-----------|------|
| SF | 正常建立並關閉 |
| S0 | 只有 SYN，沒有回應（掃描/失敗連線） |
| REJ | 連線被 RST 拒絕 |
| RSTO | originator 送 RST 關閉 |
| RSTR | responder 送 RST 關閉 |
| OTH | 其他（UDP 等無連線狀態協定） |

C2 beaconing 的特徵在 conn.log 非常明顯：
- 固定時間間隔的 `duration` 非常短（幾秒內的心跳）
- `orig_bytes` 很小（幾十到幾百 byte，只是心跳請求）
- `resp_bytes` 小（只是 C2 回傳的指令，或空回應）
- 連接到同一個 `id.resp_h`

#### dns.log — DNS 查詢

```
ts          uid               id.orig_h      query                    qtype_name  answers           TTL
1754019100  CXY2dj3p...       192.168.1.100  c2.evil-domain.cc        A           185.220.101.5     60
1754019160  CXY2dj3p...       192.168.1.100  c2.evil-domain.cc        A           185.220.101.5     60
```

DNS tunneling 的特徵：
- `query` 非常長，且包含 base64/hex 編碼的前置標籤（`aGVsbG8=.tunnel.evil.cc`）
- `qtype_name` 是 TXT 或 NULL（正常 A/AAAA 查詢不這樣用）
- 同一個 domain 的查詢次數異常多，且每次 subdomain 都不同

#### http.log — HTTP 流量

```
ts          uid      id.orig_h       id.resp_h        method  host              uri               user_agent                         status_code  resp_body_len
1754019200  CXY...   192.168.1.100   185.220.101.5    GET     update.cdn-s.com  /api/v2/status    Mozilla/5.0 (curl/7.81.0)          200          48
```

注意 `user_agent` 欄位：curl 偽裝成瀏覽器、Cobalt Strike 的預設 user-agent、Sliver 的指紋都可以在這裡找到。

#### ssl.log 與 x509.log — TLS 連線

```
ts          uid      id.orig_h       id.resp_h        version  cipher                         server_name       subject
1754019300  CXY...   192.168.1.100   185.220.101.5    TLSv12   TLS_ECDHE_RSA_WITH_AES_256_GCM cdn-delivery.cc   CN=*.cdn-delivery.cc,O=...
```

`server_name`（SNI）是 TLS 裡少數不加密的欄位，即使你看不到 payload，SNI 也告訴你連線的目標 hostname。這是 TLS 流量裡最重要的鑑識欄位。

`x509.log` 記錄了 TLS 證書的完整資訊，包括：
- `certificate.subject`：CN、O、OU
- `certificate.issuer`：自簽（issuer == subject）是可疑信號
- `certificate.not_valid_before`/`not_valid_after`：太新的證書（幾天前才發的）是 C2 基礎設施的常見特徵

---

## Suricata：IDS 告警的 rule 語法與實戰

**Suricata** 是基於規則的 IDS/IPS，可以消費 PCAP 或 live traffic，在 AF_PACKET/DPDK 上做 inline blocking。告警記錄在 `eve.json`（JSON 格式）。

### Rule 語法解剖

```
action proto src_ip src_port direction dst_ip dst_port (rule options)
```

具體範例：

```
# 偵測 Cobalt Strike 的預設 HTTPS beacon（JA3 指紋）
alert tls any any -> any any (
  msg:"ET MALWARE CobaltStrike Beacon SSL";
  flow:established,to_server;
  ja3.hash; content:"72a589da586844d7f0818ce684948eea";
  classtype:trojan-activity;
  sid:2028831;
  rev:2;
  metadata:affected_product Windows,attack_target Client_Endpoint,
            signature_severity Major, tag CobaltStrike;
)

# 偵測 DNS tunneling（超長的 TXT query）
alert dns any any -> any 53 (
  msg:"Possible DNS Tunneling Long TXT Query";
  dns.query;
  content:".";
  pcre:"/^[a-zA-Z0-9+\/=]{40,}\./";
  threshold:type both, track by_src, count 3, seconds 60;
  classtype:bad-unknown;
  sid:9000001;
  rev:1;
)

# 偵測非常規 port 的 HTTP（C2 用 HTTP over 8080/4444）
alert http any any -> $EXTERNAL_NET ![80,8080,443,8443] (
  msg:"ET POLICY HTTP to non-standard port";
  flow:established,to_server;
  http.method; content:"GET";
  classtype:policy-violation;
  sid:2000001;
  rev:1;
)
```

### Suricata 的 eve.json

```json
{
  "timestamp": "2026-08-01T03:12:44.183421+0000",
  "flow_id": 123456789,
  "in_iface": "eth0",
  "event_type": "alert",
  "src_ip": "192.168.1.100",
  "src_port": 49231,
  "dest_ip": "185.220.101.5",
  "dest_port": 443,
  "proto": "TCP",
  "alert": {
    "action": "allowed",
    "gid": 1,
    "signature_id": 2028831,
    "rev": 2,
    "signature": "ET MALWARE CobaltStrike Beacon SSL",
    "category": "A Network Trojan was Detected",
    "severity": 1
  },
  "tls": {
    "sni": "legit-looking-cdn.com",
    "ja3": {"hash": "72a589da586844d7f0818ce684948eea"},
    "ja3s": {"hash": "b742b407d1f6d4b7b0e71edc8a5e3b34"}
  }
}
```

---

## C2 偵測：從你攻擊的角度看防守

### Beaconing 週期性偵測

**攻擊者視角**：C2 agent 每隔固定時間（例如 60 秒）向 C2 server 發送心跳，以保持連線並拉取指令。為了逃避偵測，加入 jitter（例如 ±20% 的隨機延遲），讓間隔不完全固定。

**防守方看到什麼**：在 Zeek 的 conn.log 裡，同一 src/dst IP pair 在長時間（數小時）內有規律的短連線，每次 duration 幾秒內，orig_bytes 很小。即使有 jitter，統計分析（計算連線間隔的標準差和均值）仍然能找到週期性。

```bash
# 從 conn.log 找可疑的 beaconing pattern
# 計算同一 IP pair 的連線間隔
zeek-cut ts id.orig_h id.resp_h orig_bytes resp_bytes < conn.log | \
  awk '{if ($3 == prev_resp) print $1 - prev_ts, $2, $3; prev_ts=$1; prev_resp=$3}' | \
  awk '{count[$2":"$3]++; sum[$2":"$3]+=$1} END {for (k in count) if (count[k]>10) printf "%.1f avg_interval %s count=%d\n", sum[k]/count[k], k, count[k]}' | \
  sort -n
# 輸出（示意，依環境而異）：間隔接近某個固定值的 IP pair 就是可疑 beacon
```

更精確的 beaconing 偵測通常用 RITA（Real Intelligence Threat Analytics）或 Elastic EQL，它們能計算 beacon score。

### 長連線（Long Connection）偵測

**攻擊者視角**：有些 C2 框架使用一條長時間保持的 TCP 連線（reverse shell 或 persistent tunnel），而不是週期性的短連線。

**防守方看到什麼**：conn.log 裡 duration 超過幾小時的連線，特別是目標 port 不是常見的 443/80。

```bash
# 找持續超過 1 小時的連線
zeek-cut ts duration id.orig_h id.resp_h id.resp_p proto < conn.log | \
  awk '$2 > 3600 {print}' | sort -k2 -rn | head -20
```

### DNS Tunneling 偵測

**攻擊者視角**：把 C2 通訊塞進 DNS query/response 裡（例如用 iodine、dnscat2）。DNS 查詢被大多數防火牆放行，是一個理論上的 covert channel。

**防守方看到什麼**：在 dns.log 裡，query 的子網域很長、含 base64/hex 字元、且同一個 domain 被頻繁查詢，但每次子網域不同：

```bash
# 找超長的 DNS query（超過 50 字元的子網域）
zeek-cut ts id.orig_h query < dns.log | \
  awk '{n=split($3,a,"."); if (length(a[1]) > 50) print $1, $2, $3}' | head -20

# 統計每個 domain 的查詢次數（排除 .local 和 known-good）
zeek-cut query < dns.log | \
  awk '{n=split($0,a,"."); domain=a[n-1]"."a[n]; count[domain]++} END {for (d in count) if (count[d]>100) print count[d], d}' | \
  sort -rn | head -20
```

### TLS/JA3/JA4 指紋偵測

**JA3** 是 TLS Client Hello 的指紋，基於 TLS 版本、cipher suite 清單、extension 清單計算出來的 MD5 hash。JA3 值識別的是**TLS 客戶端的 library 和版本**，而不是通訊的 content，即使 payload 是加密的也能做指紋比對。

**JA4** 是 JA3 的改進版（2023 年推出），格式更易讀，也更難偽造。

Zeek 的 ssl.log 如果裝了 JA3 plugin，會有 `ja3`/`ja3s` 欄位。Suricata 7.x 原生支援 `ja3.hash` 和 `ja4` 關鍵字。

已知的惡意 JA3 hash 可以從 threat intel feed 取得（例如 [sslbl.abuse.ch](https://sslbl.abuse.ch/blacklist/)）：

```bash
# 用 Zeek ssl.log 查已知惡意 JA3
zeek-cut ts id.orig_h id.resp_h server_name ja3 < ssl.log | \
  grep -Ff known_bad_ja3_hashes.txt
```

**JA3 的侷限**：攻擊者知道 JA3 之後，可以在 C2 框架裡偽造 cipher suite 清單（malleable C2 profile），讓 JA3 看起來像 Firefox 或 Chrome。這就是為什麼 JA3 是輔助指標，不是決定性指標。

---

## Exfiltration 偵測

### 大量上傳偵測

```bash
# 從 conn.log 找 resp_bytes 極少但 orig_bytes 極大的連線（上傳）
zeek-cut ts id.orig_h id.resp_h orig_bytes resp_bytes < conn.log | \
  awk '$4 > 10000000 {print $0, $4/$5}' | sort -k5 -rn | head -20
# orig_bytes/resp_bytes 比值很高的連線 = 上傳多、下載少 = 可疑 exfil

# 找 http.log 裡的大 POST
zeek-cut ts id.orig_h id.resp_h method request_body_len < http.log | \
  awk '$5 > 1000000 && $4 == "POST" {print}' | sort -k5 -rn | head -20
```

### 非常規 Port 偵測

```bash
# 找連到外部 IP 的非標準 port（排除 80/443/22/53）
zeek-cut id.orig_h id.resp_h id.resp_p proto local_orig local_resp < conn.log | \
  awk '$5=="true" && $6=="false" && $3!=80 && $3!=443 && $3!=22 && $3!=53 {print}' | \
  sort -k3 -n | uniq -c | sort -rn | head -20
```

---

## 範例：從網路 log 重建 C2 攻擊鏈

假設 host `192.168.1.100` 在 03:12 觸發了 Suricata 的 CobaltStrike beacon 告警，完整的網路側調查流程：

```bash
# 1. 確認連線的時間範圍和規模
grep '"src_ip":"192.168.1.100"' /var/log/suricata/eve.json | jq '.timestamp' | head

# 2. 在 Zeek conn.log 裡看這台機器的所有對外連線
zeek-cut ts id.orig_h id.resp_h id.resp_p duration orig_bytes resp_bytes < conn.log | \
  grep '192.168.1.100' | sort -k1

# 3. 在 dns.log 找 C2 domain 的解析
zeek-cut ts id.orig_h query answers < dns.log | grep '192.168.1.100'

# 4. 在 ssl.log 找 TLS SNI（即使 payload 加密）
zeek-cut ts id.orig_h id.resp_h server_name ja3 < ssl.log | grep '192.168.1.100'

# 5. 在 http.log 找 staging/dropper 的下載（如果有 HTTP 階段）
zeek-cut ts id.orig_h id.resp_h method host uri user_agent < http.log | grep '192.168.1.100'

# 6. 把 C2 IP 拿去交叉比對主機 log
grep '185.220.101.5' /var/log/audit/audit.log   # 看有沒有主機側的對應事件
ss -tnap | grep '185.220.101.5'                  # live 狀態還在不在
```

---

## 對比：Zeek vs Suricata vs tshark

| 面向 | tshark/Wireshark | Zeek | Suricata |
|------|-----------------|------|----------|
| 輸出格式 | PCAP/dissection | 結構化 log（TSV/JSON） | eve.json（JSON） |
| 儲存效率 | 最差（完整封包） | 好（1/100 of PCAP） | 好（只有告警） |
| 協定解析深度 | 最深（幾百種） | 深（主要協定+scripting） | 深（IDS focus） |
| 即時告警 | 否 | 需要 Zeek script | 是（rule-based） |
| 自訂靈活性 | 有限 | 極高（Zeek scripting language） | 中（rule syntax） |
| 惡意 TLS 偵測 | 需要 key，否則看不到 | JA3/SNI（不需 key） | JA3/JA4 rule |
| 適合場景 | 深度封包分析、取證重播 | 長期 log 留存、鑑識查詢 | 即時偵測、IPS blocking |
| 學習曲線 | 低—中 | 中—高（scripting） | 中（rule syntax） |

---

## 踩雷

1. **Zeek 的 uid（Connection ID）是連線的唯一識別碼**，不同 log 裡（conn.log、http.log、ssl.log）同一條連線的 uid 一樣，這是關聯不同 log 的主鍵。很多人忘記用 uid join，結果做出不對的時間關聯。

2. **JA3 很容易被偽造**：C2 框架的 malleable profile（Cobalt Strike 著名）可以把 TLS cipher suite 設定成 Chrome 的 fingerprint。不要把 JA3 == 正常 browser hash 當成「這條連線沒問題」的理由。

3. **Zeek 的 conn_state = SF 不代表連線正常**：SF 只代表 TCP 正常建立和關閉，不代表內容是合法的。加密的惡意 C2 beacon 在 conn.log 裡跟正常的 HTTPS 看起來完全一樣（都是 SF），只有 duration、bytes 模式和 JA3 能區分。

4. **DNS 的 TTL 可以揭露 CDN fronting**：攻擊者用 domain fronting（透過 CDN 隱藏 C2），SNI 看到的是 CDN 的 hostname，但真實 C2 在 HTTP Host header 裡。Zeek 的 http.log 能看到 Host header，跟 ssl.log 的 SNI 比較，不一致就是 fronting 的信號。

5. **大量告警不等於更安全**：Suricata 開太多規則、閾值設太低，會把 SOC 的人淹死在 false positive 裡，導致真正的告警被忽略。規則要 tune，定期看 false positive 率，這是 Detection Engineering 的基本功（接本課 Part 1 的章節）。

---

## 進階延伸

- **RITA（Real Intelligence Threat Analytics）**：把 Zeek log 餵進去，自動計算 beacon score、blacklist 比對、DNS 統計，是 beaconing 偵測的利器；比手寫 awk 更精確。
- **Suricata 的 Dataset 功能**：可以載入大型 IOC 列表（IP/domain/JA3 hash），不用為每個 IOC 寫一條 rule，適合把 threat intel feed 整合進 Suricata。
- **Zeek Intelligence Framework**：同理，把 threat intel 以 `Intel::ADDR`、`Intel::DOMAIN` 格式餵進 Zeek，自動比對所有 log，hit 就告警。
- **NetworkMiner**（Windows GUI）：從 PCAP 自動提取傳輸的檔案、憑證、圖片，適合快速 artifact 提取，不用手動 export-objects。
- **Wireshark 的 Conversations 統計**（Statistics → Conversations）：快速看各 TCP/UDP 連線的 byte 量，找 outlier，適合初步 triage 一個大 PCAP。

---

## 本章重點整理

- **網路鑑識四層**：PCAP（最詳細）→ Zeek log（長期留存主力）→ Suricata alert（即時偵測）→ Netflow（超長期摘要）。
- **Zeek conn.log** 的 `duration`/`orig_bytes`/`resp_bytes`/`conn_state` 是 beaconing 和 exfil 的核心偵測欄位；`uid` 是跨 log 關聯的主鍵。
- **dns.log** 裡的超長子網域和高頻查詢是 DNS tunneling 的特徵；**ssl.log + x509.log** 裡的 SNI 和新發證書是加密 C2 的指紋。
- **Suricata rule** 的 `ja3.hash` 和 `ja4` 欄位可以在不解密的情況下做 TLS client 指紋比對；JA3 可以被偽造，是輔助指標。
- **Beaconing 偵測**關鍵：找同一 IP pair 的高頻短連線，計算連線間隔的週期性；RITA 比手寫 awk 準確。
- 網路 log 是攻擊者不能在受害端清除的痕跡，和主機 log 對齊時間線才能完整重建攻擊鏈。

## 自我檢核

1. Zeek 的 `conn_state = S0` 代表什麼？在大規模出現時，最可能的解讀是什麼？
2. 你在 ssl.log 看到 `server_name = cdn-updates.microsoft.com`，但 `id.resp_h` 是 185.x.x.x（不是微軟的 IP），這代表什麼攻擊手法？
3. JA3 hash 可以被攻擊者偽造成 Chrome 的指紋，你還有什麼其他欄位或方法可以識別異常的 TLS 連線？
4. 在 conn.log 裡，一個 IP pair 每隔 58-62 秒有一條短連線，持續 8 小時，每次 orig_bytes 約 150，resp_bytes 約 50。這是什麼攻擊模式？jitter 在哪裡？
5. Suricata 的 `threshold` 選項是做什麼的？在 DNS tunneling rule 裡為什麼需要它？

## 延伸閱讀

1. **[Zeek 官方文件 — Log Formats](https://docs.zeek.org/en/master/logs/index.html)**：每個 log 的欄位定義、型別、語義；conn_state 的完整狀態機說明就在這裡，一次讀完。
2. **[Suricata 官方文件 — Rule Writing](https://docs.suricata.io/en/latest/rules/index.html)**：rule 語法完整參考，特別是 TLS keyword（`tls.sni`、`ja3.hash`、`ja4`）和 threshold；寫任何 rule 前先讀。
3. **SANS FOR572（Advanced Network Forensics）**：以網路鑑識為核心的完整課程；Zeek 深度分析、Suricata tuning、C2 beaconing 偵測實驗，是本章最直接的延伸。
4. **[The DFIR Report — Cobalt Strike tag](https://thedfirreport.com/tag/cobalt-strike/)**：真實 CobaltStrike 入侵案例的完整 write-up，每篇都包含網路側證據（Zeek log、PCAP 特徵、JA3），看真實防守方怎麼從網路還原攻擊鏈。
5. **RITA（Real Intelligence Threat Analytics）文件**：beaconing 分析的演算法說明，以及如何從 Zeek log 算出 beacon score；接本章 beaconing 偵測部分，是把直覺轉化成可重複工具的好範例。

---

→ [Ch 25 雲 IR：CloudTrail/GuardDuty](./25-cloud-ir.md)
