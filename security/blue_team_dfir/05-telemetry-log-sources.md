# Ch 5 — 遙測從哪來：log source 全景

> 目標：盤點防守方可以收到的所有遙測來源，理解每一層的可見性範圍、盲點、與成本，建立「遙測地圖」的心智模型，知道在不同攻擊場景下該看哪裡、不該指望哪裡。

## 為什麼需要先搞清楚 log source？

大多數剛轉藍隊的人直接跳進 SIEM 查詢，卻對「這筆資料從哪來、怎麼產生的、會漏什麼」毫無概念。結果是：

- 查不到不代表沒發生——可能只是沒收那一層
- 查到了但資料不完整——可能來源設定錯，欄位被截掉
- 找到「可疑」事件卻不知道它的可靠性——網路層資料和端點層資料的可信度不同

偵測的品質上限由遙測的品質決定。再好的規則，建在殘缺的 log source 上都是廢的。

從攻擊者視角想：你在靶機執行 `rundll32.exe comsvcs.dll MiniDump`，這個動作在哪一層留下痕跡、在哪一層完全隱形？這章的目標就是把這張地圖畫清楚。

## 一張遙測地圖

```
┌─────────────────────────────────────────────────────────────────┐
│                      遙測層次全景                                │
├─────────────────────────────────────────────────────────────────┤
│  應用層 (Application)                                            │
│  Web access log / DNS query log / Proxy log / 應用程式自訂 log   │
│  ↑ 看到：HTTP 請求、域名查詢、認證行為                           │
│  ✗ 看不到：加密 payload 內容、kernel-level 動作                 │
├─────────────────────────────────────────────────────────────────┤
│  網路層 (Network)                                                │
│  NetFlow/IPFIX / Zeek / Firewall log / IDS (Suricata)           │
│  ↑ 看到：連線 metadata、協議特徵、封包 header                    │
│  ✗ 看不到：TLS 加密內容（除非 TLS inspection）                  │
├─────────────────────────────────────────────────────────────────┤
│  端點層 (Endpoint)                                               │
│  Windows: Event Log / Sysmon / EDR / ETW                        │
│  Linux: auditd / syslog / eBPF / EDR                            │
│  ↑ 看到：process tree、syscall、檔案、registry、記憶體注入       │
│  ✗ 看不到：firmware-level rootkit、側信道                       │
├─────────────────────────────────────────────────────────────────┤
│  雲 / 身份層 (Cloud / Identity)                                  │
│  AWS CloudTrail / Azure Audit Log / GCP Cloud Audit              │
│  VPC Flow Logs / Azure NSG Flow Logs / Entra ID SignIn Log       │
│  ↑ 看到：API 呼叫、IAM 變更、資源操作、登入事件                  │
│  ✗ 看不到：VM 內部行為（除非 endpoint agent 有部署）             │
└─────────────────────────────────────────────────────────────────┘
```

這四層彼此互補，沒有任一層能單獨覆蓋完整的攻擊鏈。SOC 的核心功夫之一就是跨層關聯：從 DNS 查詢（應用層）追到 process（端點層）追到 IAM 呼叫（雲層）。

## Windows 端點遙測

### Windows Security Event Log

Windows 內建，安全事件寫入 `Security` 通道。預設開啟但預設設定粒度很粗：

關鍵 Event ID（原生）：

| Event ID | 意義 | 攻擊相關性 |
|----------|------|------------|
| 4624 | 登入成功 | Lateral movement 來源追蹤 |
| 4625 | 登入失敗 | 暴力破解偵測 |
| 4648 | 使用明確憑證登入（runas） | Pass-the-hash/ticket 行為 |
| 4672 | 特殊權限指派（如 SeDebugPrivilege） | 高權限操作起點 |
| 4688 | 新 process 建立（需開啟 audit policy） | 指令執行追蹤 |
| 4698 | 排程任務建立 | Persistence |
| 4720 | 本機帳戶建立 | Backdoor account |
| 4776 | NTLM 認證 | NTLM relay 偵測 |
| 4769 | Kerberos Service Ticket 請求 | Kerberoasting |

問題：4688 預設不記錄 command line arguments，需額外設定 Group Policy → `Computer Configuration → Administrative Templates → System → Audit Process Creation → Include command line in process creation events`。

### Sysmon（System Monitor）

微軟 Sysinternals 出品，安裝後以 kernel driver 方式運行，大幅補強原生 Event Log。詳細見 Ch 6。

### EDR（Endpoint Detection and Response）

CrowdStrike Falcon、SentinelOne、Microsoft Defender for Endpoint 等。

EDR 的資料來源通常是 ETW（Event Tracing for Windows）加上自有 kernel driver，涵蓋：
- 進程樹（含完整 command line）
- 記憶體注入行為
- 網路連線（含 TLS SNI，某些可解密）
- 憑證存取（LSASS 觸碰偵測）
- 沙盒行為分析

EDR 的可見性通常優於原生 Event Log + Sysmon，但：
1. 各家資料欄位不標準，遷移成本高
2. 代理商鎖定風險
3. 資料留在廠商雲端，法遵議題

## Linux 端點遙測

### auditd

Linux 核心的 Audit 子系統，透過 `auditctl` 設規則，記錄 syscall。

典型規則（`/etc/audit/rules.d/`）：
```bash
# 記錄 execve (程式執行)
-a always,exit -F arch=b64 -S execve -k exec_commands

# 記錄 /etc/passwd 修改
-w /etc/passwd -p wa -k passwd_changes

# 記錄 ptrace (可能是調試或注入)
-a always,exit -F arch=b64 -S ptrace -k ptrace_usage
```

auditd 輸出到 `/var/log/audit/audit.log`，格式原始，通常搭配 `auditbeat`（Elastic）或 `laurel`（JSON 轉換器）送 SIEM。

問題：高負載系統若規則太細，CPU overhead 顯著。

### syslog / journald

系統服務、daemon、核心訊息的通用管道。`/var/log/auth.log`（Debian 系）或 `/var/log/secure`（RHEL 系）記錄 SSH、su、sudo 等認證。

可見性比 auditd 低，但幾乎每台機器都有、幾乎不需設定。

### eBPF 遙測

現代做法：透過 eBPF 程式 attach 到 tracepoint/kprobe，無需修改核心模組。

代表工具：
- `Falco`：Sysdig 出品，基於 eBPF/kernel module，以 YAML 規則偵測執行時威脅
- `Tetragon`：Cilium 專案，支援 process/network/syscall 的細粒度事件，輸出 JSON
- `auditbeat`（Elastic）：封裝 auditd 資料

eBPF 的優勢：攻擊者較難 tamper（與 userspace 工具不同），且 overhead 比 kernel module 低。

## 網路遙測

### NetFlow / IPFIX

路由器、防火牆、交換機產生的流量摘要。每一條 TCP/UDP 流記錄：

```
src_ip | src_port | dst_ip | dst_port | protocol | bytes | packets | duration
10.0.1.5 | 54321 | 185.1.2.3 | 443 | TCP | 2048000 | 1500 | 3600
```

NetFlow **不包含 payload**，只有 metadata。對 C2 beacon 偵測非常有用（固定週期、固定 byte size pattern）。保留成本低，通常可以存 30–90 天以上。

### Zeek（原名 Bro）

在網路 tap / SPAN port 上跑的流量分析引擎，產出結構化 log：

- `conn.log`：TCP/UDP 連線摘要
- `http.log`：HTTP 請求（User-Agent、URI、response code）
- `dns.log`：DNS 查詢與回答
- `ssl.log`：TLS handshake（SNI、cipher suite、cert）
- `files.log`：傳輸的檔案（MD5/SHA1）

Zeek 是威脅狩獵的利器：你可以用 Zeek 的 `notice` 框架寫自訂偵測邏輯，也可以把 log 送 Elastic/Splunk 做關聯查詢。

### 防火牆 / NGFW Log

Palo Alto NGFW、Cisco ASA、pfSense 等輸出 session log，通常包含：
- 5-tuple（src/dst IP + port + protocol）
- 應用層識別（App-ID：「TOR」「BitTorrent」）
- 允許/拒絕決策
- 地理位置標籤

NGFW 的應用識別層是額外情報：如果防火牆標注流量為 `unknown-tcp` 卻走 443，值得進一步看。

### IDS/IPS（Suricata）

Suricata 做深封包檢測（Deep Packet Inspection, DPI），對封包內容比對規則，輸出 `eve.json`：

```json
{
  "event_type": "alert",
  "alert": {
    "signature": "ET MALWARE CobaltStrike Beacon",
    "signature_id": 2016476,
    "severity": 1
  },
  "src_ip": "10.0.1.5",
  "dest_ip": "185.1.2.3",
  "dest_port": 443
}
```

Suricata 規則（`.rules` 格式）類似 Snort，社群規則庫來自 Emerging Threats。

## 雲端遙測

### AWS CloudTrail

記錄所有 AWS API 呼叫。每一條 CloudTrail 事件包含：
- `eventName`：呼叫的 API（如 `AssumeRole`、`GetSecretValue`）
- `userIdentity`：呼叫者身份（IAM User / Role / 服務）
- `sourceIPAddress`：來源 IP
- `requestParameters`：API 參數
- `responseElements`：回應內容（部分）

對攻擊者而言：取得 credential 後所有動作都在 CloudTrail 留下。對防守方：`AssumeRole` 跨帳號、`GetSecretValue` 大量存取、`CreateUser` 是常見告警觸發點。

CloudTrail 分 Management events（控制面，預設開啟）和 Data events（S3 物件讀寫、Lambda 執行，需額外開啟，費用高）。

### VPC Flow Logs

等同 NetFlow 的雲端版本，記錄 VPC 內的流量 metadata。可以送到 CloudWatch Logs 或 S3。格式：

```
2 123456789012 eni-0abcdef 10.0.1.5 52.1.2.3 54321 443 6 10 1000 1623000000 1623000060 ACCEPT OK
```

### Azure / GCP 對應

| 功能 | AWS | Azure | GCP |
|------|-----|-------|-----|
| API 稽核 | CloudTrail | Activity Log / Defender for Cloud | Cloud Audit Logs |
| 網路流量 | VPC Flow Logs | NSG Flow Logs | VPC Flow Logs |
| 身份認證 | CloudTrail（IAM） | Entra ID Sign-In Log | Cloud Identity Audit |
| 威脅偵測 | GuardDuty | Microsoft Defender for Cloud | Security Command Center |

## 應用層遙測

### Web 存取 Log

Apache/Nginx 的 access log、WAF（Web Application Firewall）log。關鍵欄位：

```
192.168.1.10 - - [01/Aug/2026:12:34:56 +0000] "POST /upload.php HTTP/1.1" 200 4096 "-" "curl/7.68.0"
```

User-Agent 欄位攻擊者可以偽造，但常被忽略的是：`curl` 打 web 應用、回應 200 且 body 很大，這組合值得關注。

### DNS Log

DNS 是被低估的遙測來源。攻擊者使用 DNS 做 C2（DNS tunneling）、資料外洩、domain generation algorithm（DGA）。

收集方式：
- 架設 Zeek 在出口路由器前，Zeek 的 `dns.log` 自動記錄
- Windows DNS Server 開啟 debug log 或用 `Microsoft-Windows-DNS-Client/Operational` ETW
- 部署 Pi-hole / 商業 DNS 過濾器（Cisco Umbrella、Cloudflare Gateway），這些工具本身會記錄查詢

### Proxy Log

HTTP proxy（Squid、Zscaler、Netskope）記錄使用者的 HTTP/HTTPS 請求（TLS inspection 後可見 URL）。比 NetFlow 多了 URL 路徑和 User-Agent，比 Zeek 少了 TCP 層細節。

## 可見性缺口（Visibility Gap）

缺口的根本原因：**你沒部署感應器的地方就是盲區**。

常見缺口：

| 缺口 | 攻擊者利用方式 | 填補方法 |
|------|---------------|----------|
| 加密網路流量 | TLS C2、DNS-over-HTTPS | TLS inspection（代價：隱私與效能）|
| 端點無 agent | Living-off-the-land 攻擊在未覆蓋機器 | EDR 覆蓋率追蹤、Sysmon 強制部署 |
| 容器內部 | 容器逃逸前的橫向移動 | Falco/Tetragon、Pod Security |
| 韌體層 | BootKit、UEFI implant | TPM 量測、Secure Boot 驗證 |
| 影子 IT 雲帳號 | 攻擊者在非管理帳號建資源 | CSPM、Cloud Security Posture 掃描 |
| OT / IoT 網路 | 攻擊者從 IT 跳 OT | 獨立 IDS、被動式流量分析 |

**EDR 覆蓋率**是 SOC 最常忽略的指標。你有 1000 台機器，EDR 只裝了 900 台，那 100 台的攻擊動作在 SIEM 裡完全不存在。

## Log 保留與成本取捨

儲存 log 有實際的金錢成本，企業不可能無限期保留所有東西。

典型保留策略：

| 來源 | 建議保留時間 | 理由 |
|------|-------------|------|
| 安全 Event Log（端點） | 1 年以上 | 事後溯源需要長時間窗口；APT 停留期中位數約 21 天，但有些更長 |
| Sysmon / EDR 詳細事件 | 90–180 天 | 高量、高噪音，90 天通常夠做 IR |
| NetFlow | 90–365 天 | 量小，成本低，對 C2 偵測很有用 |
| Zeek / PCAP full | 7–30 天（Zeek）；PCAP 更短 | PCAP 量巨大，成本高；Zeek metadata 可較長 |
| CloudTrail | 1 年以上（需符合法規） | GDPR、SOC2 等要求稽核追蹤 |
| Web access log | 90 天–1 年 | 取決於法遵需求 |

**什麼該收、什麼別收**的判斷原則：

1. **先問「這筆資料能偵測什麼」**：如果收了但沒有對應的偵測規則或分析流程，只是在花儲存費用。
2. **噪音比高的 log 要先過濾**：DNS log 若直接全量送 SIEM，查詢量可能壓垮索引。考慮在 Zeek 層做初步過濾（排除已知良好域名）。
3. **保留 raw 還是 parsed**：SIEM 對 raw log 索引費用高；考慮把 raw 冷儲存（S3/Glacier），SIEM 只存 parsed、enriched 版本。
4. **Data events vs Management events（雲）**：S3 Data events 量是 Management events 的 10-100 倍，按需開啟特定 bucket，不要全域開。

## 踩雷：錯誤直覺 → 正確認識

**1. 「我有 SIEM，所以我有可見性」**
→ SIEM 只知道你送進去的資料。如果端點沒有 Sysmon、雲帳號沒有開 CloudTrail、網路沒有 Zeek，SIEM 裡查不到不等於沒發生。可見性取決於感應器部署，不取決於 SIEM 的存在。

**2. 「防火牆 log 有連線記錄，代表我看得到攻擊者的流量」**
→ 防火牆 log 記錄允許和拒絕的連線，但對於允許的連線，你只知道 5-tuple，不知道 payload。攻擊者在 443 上的 C2 流量，防火牆 log 顯示 `ACCEPT`，什麼都看不到。

**3. 「Event ID 4688 有了，我就能追蹤所有程式執行」**
→ 4688 需要額外設定才記錄 command line；WMI 產生的 process 父程序可能指向 `WmiPrvSE.exe`，而非真正的呼叫鏈；process injection 執行的 shellcode 完全不產生 4688。

**4. 「DNS log 不重要，攻擊者不用 DNS」**
→ DNS 是攻擊者愛用的 C2 管道，因為它幾乎不被過濾。DNS tunneling（如 `iodine`）可以在 DNS 查詢裡藏 exfiltration 資料，很多環境的 SIEM 完全沒有 DNS 遙測。

**5. 「我的 log 太多，SIEM 收不完，所以只收重要的」**
→ 「重要的」這個判斷在事後 IR 時常常是錯的——你不知道攻擊者走哪條路，所以你不知道哪筆 log 是關鍵的。解法不是少收，是分層：全量冷儲存，SIEM 只收高優先，IR 時按需倒入冷儲存的資料。

## 進階延伸

### eBPF 作為下一代端點遙測

eBPF 的出現讓 Linux 端點的遙測粒度逼近 Windows Sysmon 等級。`Tetragon` 可以做到進程樹、網路連線、syscall 三層的關聯，且以 Kubernetes 原生方式輸出，是容器環境首選。

### Deception 技術作為遙測補充

蜜罐（Honeypot）和蜜令牌（Honeytoken）不是主動偵測，而是被動等待攻擊者踩到。一個 S3 bucket 的 honeytoken 被存取時，CloudTrail 會有記錄——誤報率接近零，因為沒有正常業務流程會用到這個 token。

### 遙測品質評估框架

MITRE ATT&CK 的 [Data Sources](https://attack.mitre.org/datasources/) 模型把每個 TTP 對應到它會觸發的 data source（如 `Process: Process Creation`、`Network Traffic: Network Connection Creation`），可以用來衡量你的遙測覆蓋了哪些 TTP。

## 本章重點整理

- 遙測分四層：應用、網路、端點、雲/身份。每層覆蓋不同攻擊面，需要交叉關聯。
- Windows 端點：原生 Event Log 粒度粗（4688 需額外設定），Sysmon 補強，EDR 最完整。
- Linux 端點：auditd 是傳統路線，eBPF（Falco/Tetragon）是現代路線。
- 網路層：NetFlow 看連線模式，Zeek 看協議內容，Suricata 做 DPI 告警。
- 雲層：CloudTrail（Management events）是基本，Data events 要按需開啟。
- 可見性缺口由感應器覆蓋率決定，不是由 SIEM 決定。
- Log 保留策略需要平衡成本與溯源需求：冷熱分層是常見解法。

## 自我檢核

1. 攻擊者在 Windows 機器執行了 `cmd.exe /c whoami`，這個動作在哪些 log source 會留下記錄？哪些不會？
2. NetFlow 和 Zeek 的本質差異是什麼？各自擅長偵測哪類行為？
3. 你的環境有 500 台 Linux 機器，沒有任何端點 agent，能從網路遙測推斷什麼？推斷不了什麼？
4. CloudTrail Management events 和 Data events 的差異？不開 Data events 會漏掉什麼攻擊場景？
5. 為什麼「有防火牆 log」不等同於「能看到攻擊者的 C2 流量」？

## 延伸閱讀

1. **MITRE ATT&CK Data Sources** — [https://attack.mitre.org/datasources/](https://attack.mitre.org/datasources/)
   讀哪：Data Sources 頁面，選幾個你熟悉的 TTP 看它對應的 data source
   學什麼：建立 TTP → 遙測需求的直覺對映
   關聯：Ch 7、Ch 10 的偵測涵蓋度評估

2. **SANS Windows Forensic Artifact Poster** — [https://www.sans.org/posters/](https://www.sans.org/posters/)
   讀哪：Windows Forensic Analysis Poster
   學什麼：Windows 各 artifact 的位置、格式、含義速查
   關聯：Ch 15–18 的 Windows 鑑識章節

3. **Zeek 官方文件** — [https://docs.zeek.org/](https://docs.zeek.org/)
   讀哪：Script Reference → Log Files（conn.log、http.log、dns.log、ssl.log）
   學什麼：每個 log 的欄位定義與語意，這是 Ch 24 網路鑑識的基礎
   關聯：Ch 24

4. **Florian Roth, "Detectionability"** — [https://github.com/Neo23x0/sigma/wiki](https://github.com/Neo23x0/sigma/wiki)
   讀哪：Sigma wiki 的 Targets 與 Backends 說明
   學什麼：不同 log source 對 Sigma 規則的支援程度，決定你能寫哪些偵測
   關聯：Ch 8 Sigma 規則工程

5. **AWS CloudTrail 官方文件：Logging Data Events** — [https://docs.aws.amazon.com/awscloudtrail/latest/userguide/logging-data-events-with-cloudtrail.html](https://docs.aws.amazon.com/awscloudtrail/latest/userguide/logging-data-events-with-cloudtrail.html)
   讀哪：Data events 的 event type 清單與費用說明
   學什麼：哪些 AWS 操作被 Data events 覆蓋、哪些只有 Management events，成本怎麼估
   關聯：Ch 25 雲 IR

---

遙測地圖建好了，下一步進入最值得深挖的端點層——Windows 機器的遙測細節。

→ [Ch 6 Windows 遙測地基：Sysmon 與 ETW](./06-windows-telemetry-sysmon-etw.md)
