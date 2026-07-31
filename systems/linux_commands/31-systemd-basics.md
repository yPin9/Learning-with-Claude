# Ch 31 — systemctl / journalctl 基礎

> **目標**：理解 systemd——現代 Linux 的 init 系統與服務管理器（PID 1）。掌握 systemctl 管理服務（start/stop/enable/status）、unit 檔案的結構、journalctl 查日誌（為什麼日誌變成二進位、怎麼查）、以及 systemd 的設計爭議。這把 Ch 14-18（process）和 Ch 30（timer）提升到「系統如何管理所有服務」的層次。

> **環境**：systemd（現代主流 distro：Ubuntu 16.04+、Debian 8+、RHEL 7+、Arch）。

## 為什麼要懂 systemd？

你每天都在用它，可能不自知：`systemctl restart nginx`、`systemctl status ssh`、開機後各種服務自動啟動——這些都是 systemd。它是現代 Linux 的 **PID 1**（第一個 process，Ch 15）和服務管理器，掌管系統上所有服務的生命週期。

懂 systemd 是現代運維的硬需求：管理服務（啟停、開機自啟、看狀態）、寫自己的服務、查日誌排障、理解開機流程。它也是個有爭議的設計——取代了 30 年的 SysV init，引發 Unix 社群的大辯論。理解它的設計（為什麼用二進位日誌、為什麼管這麼多事）和爭議，你才能有判斷力地用它，而非盲目背命令。

## 先建立直覺:systemd 是系統的「總管」

```
systemd：PID 1，系統所有服務的總管

  開機 → kernel 啟動 → 執行 PID 1（systemd）→ systemd 啟動一切
        │
  systemd 管理「unit」（各種系統資源的抽象）：
    .service  ← 服務（nginx、ssh、你的 app）
    .timer    ← 定時器（Ch 30）
    .mount    ← 掛載點
    .socket   ← socket（按需啟動服務）
    .target   ← 一組 unit（如 multi-user.target = 多使用者模式）
        │
  systemd 對每個服務負責：
    啟動它、監控它（掛了自動重啟）、管它的日誌、
    管它的資源（cgroup 限制 CPU/記憶體）、處理相依性
        │
  → systemd = 服務的生命週期管理器
    你透過 systemctl 對它下令，透過 journalctl 看它收集的日誌
```

關鍵心智：systemd 是 PID 1（開機後第一個 process，Ch 15）和系統總管。它管理各種「unit」（.service/.timer/.mount/.target…），對每個服務負責整個生命週期——啟動、監控（掛了重啟）、收集日誌、限制資源、處理相依順序。你用 `systemctl` 下令、用 `journalctl` 查日誌。

> systemd 是 Ch 15（PID 1）和 Ch 18（job control 的「誰管 process」）的系統級答案。如果你對 PID 1 為什麼特殊、process 的父子關係還不熟，先回看 [Ch 15 — fork/exec/wait](./15-fork-exec-wait.md) 和 [Ch 14 — process 狀態機](./14-process-states.md)。

## systemctl:管理服務

systemctl 是你和 systemd 對話的主要工具：

```bash
# 服務的生命週期操作
sudo systemctl start nginx       # 啟動服務（立即，但不影響開機）
sudo systemctl stop nginx        # 停止服務
sudo systemctl restart nginx     # 重啟（stop + start）
sudo systemctl reload nginx      # 重新載入設定（不中斷服務，如果服務支援）
sudo systemctl enable nginx      # 設定「開機自動啟動」（不立即啟動）
sudo systemctl disable nginx     # 取消開機自啟
sudo systemctl enable --now nginx   # enable + start 一次做（常用）

# 查狀態（最常用！）
systemctl status nginx           # 服務狀態：running/failed、PID、最近日誌、記憶體用量
systemctl is-active nginx        # 只回 active/inactive（腳本裡好用）
systemctl is-enabled nginx       # 是否開機自啟

# 列出 unit
systemctl list-units --type=service          # 所有服務
systemctl list-units --type=service --state=running   # 只看在跑的
systemctl --failed                            # 看失敗的服務（排障第一步！）
systemctl list-unit-files --type=service     # 所有服務 unit 檔（含未啟動的）
```

```
start/enable 的關鍵區別（新手必懂）：

  start：  「現在」啟動服務（這次開機有效，重開機後不會自動啟動）
  enable： 設定「開機時自動啟動」（但不會「現在」啟動它）
        │
  常見錯誤：
    只 start 沒 enable → 服務在跑，但重開機後消失（沒自啟）
    只 enable 沒 start → 設了自啟，但「現在」還沒跑（要等下次開機或手動 start）
        │
  → 要「現在跑 + 以後開機也跑」：enable --now（兩個一起）
```

> **`start` 和 `enable` 是兩件不同的事——混淆它們是最常見的 systemd 錯誤**。`systemctl start nginx` 是「**現在**啟動」（這次開機有效，重開機後不會自動起來）。`systemctl enable nginx` 是「設定**開機自啟**」（但不會立刻啟動它）。新手常犯：部署服務時只 `start` 沒 `enable`——服務當下在跑，看起來沒問題，但伺服器重開機後服務消失了（因為沒設自啟）。或只 `enable` 沒 `start`——以為服務跑了，其實要等下次開機。要「現在跑 + 以後開機也跑」用 `enable --now`（一次做兩件事）。排障第一個命令是 `systemctl status <服務>`（看 running/failed + 最近日誌）和 `systemctl --failed`（列出所有掛掉的服務）。

## unit 檔案:定義一個服務

unit 檔案是 systemd 的設定——理解它你能寫自己的服務：

```bash
# 看一個現有服務的 unit 檔
systemctl cat nginx              # 顯示 nginx 的 unit 檔內容
cat /etc/systemd/system/nginx.service   # 或直接看

# 一個典型的 .service 檔結構
# /etc/systemd/system/myapp.service
```

```ini
[Unit]
Description=My Application          # 服務描述
After=network.target               # 在 network 就緒「之後」才啟動（相依順序）
Requires=postgresql.service        # 強相依（postgres 沒起來就不啟動）

[Service]
Type=simple                        # 服務類型（simple/forking/oneshot/notify）
ExecStart=/usr/bin/myapp --config /etc/myapp.conf   # 啟動命令（絕對路徑！）
ExecReload=/bin/kill -HUP $MAINPID # reload 時做什麼
Restart=on-failure                 # 掛了自動重啟（systemd 的殺手鐧）
RestartSec=5                       # 重啟前等 5 秒
User=myapp                         # 以哪個使用者跑（Ch 28，最小權限！別用 root）
Group=myapp
Environment=NODE_ENV=production    # 環境變數（Ch 29）
WorkingDirectory=/opt/myapp        # 工作目錄
MemoryMax=512M                     # 記憶體上限（cgroup 限制）

[Install]
WantedBy=multi-user.target         # enable 時掛到哪個 target（開機啟動）
```

```bash
# 寫了 unit 檔後
sudo systemctl daemon-reload     # 讓 systemd 重新讀 unit 檔（改了 unit 檔必做！）
sudo systemctl enable --now myapp.service
journalctl -u myapp -f           # 即時看它的日誌
```

> **unit 檔的 `Restart=on-failure` 是 systemd 取代「自己寫監控腳本」的關鍵能力**。傳統上要讓「服務掛了自動重啟」得自己寫監控腳本（像練習 B 的 procmon）或裝 monit。systemd 內建：`Restart=on-failure`（失敗時重啟）、`RestartSec=5`（重啟間隔）——systemd 持續監控服務，掛了自動拉起來。配合 `User=`（以低權限使用者跑，Ch 28 最小權限）、`MemoryMax=`（cgroup 資源限制）、`After=`/`Requires=`（相依順序——等資料庫就緒才啟動）、`Environment=`（環境變數，Ch 29），一個 unit 檔就定義了服務的完整運行規格。**鐵律：改了 unit 檔一定要 `systemctl daemon-reload`**（讓 systemd 重讀），否則改動不生效——這是寫 unit 檔最常忘的一步。`ExecStart` 一定用**絕對路徑**（systemd 的環境和 cron 一樣貧瘠，Ch 30）。

## journalctl:查日誌

systemd 用 journal（二進位日誌）取代傳統文字 log，journalctl 是查詢工具：

```bash
# 基本查詢
journalctl                       # 所有日誌（從舊到新，用 less 翻）
journalctl -e                    # 跳到最新（end）
journalctl -f                    # 即時跟蹤（follow，像 tail -f）
journalctl -r                    # 反向（最新在上）

# 按服務過濾（最常用）
journalctl -u nginx              # 只看 nginx 的日誌
journalctl -u nginx -f           # 即時跟蹤 nginx
journalctl -u nginx --since today          # 今天的
journalctl -u nginx --since "1 hour ago"   # 過去一小時
journalctl -u nginx --since "2024-01-01" --until "2024-01-02"

# 按時間
journalctl --since "10 min ago"
journalctl --since yesterday --until now

# 按優先級（嚴重程度）
journalctl -p err                # 只看 error 以上（emerg/alert/crit/err）
journalctl -p warning -u nginx   # nginx 的 warning 以上

# 開機相關
journalctl -b                    # 本次開機的日誌
journalctl -b -1                 # 上次開機的（查「為什麼上次當機」）
journalctl --list-boots          # 列出所有開機紀錄

# 其他
journalctl -k                    # kernel 訊息（等於 dmesg）
journalctl -u nginx -o json      # JSON 格式輸出（給程式處理）
journalctl --disk-usage          # journal 佔多少磁碟
sudo journalctl --vacuum-time=7d # 只保留 7 天的日誌（清理）
```

```
為什麼 systemd 用二進位 journal（爭議點）：

  傳統 syslog：純文字 log（/var/log/syslog、/var/log/messages）
    優點：用 grep/awk/tail 直接處理（Ch 24-27 全套能用）
    缺點：無結構（要 parse）、無索引（大檔案查詢慢）、易偽造
        │
  systemd journal：二進位、結構化、有索引
    優點：結構化欄位（按服務/優先級/時間快速查）、有 metadata
          （每條 log 自動帶 PID/UID/服務名/開機 ID）、防竄改
    缺點：不能直接 grep（要用 journalctl）、二進位不透明
        │
  → 這是 Unix「一切皆文字」哲學（Ch 21）vs 結構化的衝突
    journal 賭「結構化查詢」勝過「文字可 grep」
    （journalctl 能輸出文字給 grep，但原生不是文字）
```

> **journal 的二進位格式是 systemd 最具爭議的設計——它違背了「一切皆文字」（Ch 21）**。傳統 syslog 是純文字（`/var/log/syslog`），你能直接 `grep`/`awk`/`tail`（Ch 24-27）。systemd 改用**二進位、結構化、有索引**的 journal——優點是強大的結構化查詢（`journalctl -u nginx -p err --since today` 按服務+優先級+時間秒查，每條 log 自動帶 PID/UID/服務名等 metadata）、防竄改。代價是**不能直接 grep**（要透過 journalctl）、格式不透明。這是 Unix 哲學（文字可組合，Ch 21）和現代結構化日誌的正面衝突，引發社群激烈辯論。實務上：journalctl 能 `-o json` 或純文字輸出給下游 grep/awk，所以沒完全失去可組合性。記住關鍵命令：`journalctl -u <服務>`（看某服務）、`-f`（即時）、`-p err`（只看錯誤）、`-b`（本次開機）、`-b -1`（上次開機，查當機）、`--since`（時間範圍）。這幾個覆蓋 90% 的排障需求。

## 對比:systemd vs SysV init

systemd 取代了 SysV init，理解差異看到設計的演進：

| 面向 | SysV init（舊）| systemd（新）|
|---|---|---|
| 啟動方式 | 循序執行 /etc/init.d/ 腳本 | 平行啟動（有相依圖）|
| 開機速度 | 慢（一個個來）| 快（能平行的就平行）|
| 服務定義 | shell 腳本（每個自己寫）| 宣告式 unit 檔 |
| 服務監控 | ✗（要自己/額外工具）| ✓ 內建（Restart=）|
| 日誌 | syslog（文字）| journal（二進位+結構化）|
| 相依管理 | 靠執行順序編號 | 明確的 After/Requires |
| 按需啟動 | ✗ | ✓（socket/path 觸發）|
| 資源控制 | ✗ | ✓（cgroup）|
| 爭議 | 簡單但功能少 | 強大但「管太多」「太複雜」|

> **systemd vs SysV init 的爭論是 Linux 史上最大的技術論戰之一**。SysV init（30 年標準）用 shell 腳本循序啟動服務——簡單、透明、符合 Unix 哲學（每個服務一個 shell 腳本），但慢（循序）、功能少（沒監控、沒相依管理、沒資源控制）。systemd 用宣告式 unit 檔 + 平行啟動 + 相依圖——開機快很多、內建服務監控/重啟/資源限制/日誌。**反對者**批評 systemd 「管太多」（init + 日誌 + 網路 + DNS + 登入… 違背「做一件事」哲學 Ch 21）、太複雜、二進位日誌不透明、PID 1 太肥（bug 影響大）。**支持者**認為現代系統需要這些整合能力，SysV 的簡單是「把複雜性推給每個服務的作者」。結果：主流 distro（Ubuntu/Debian/RHEL/Arch）幾乎全採用 systemd，但有 Devuan、Artix 等「無 systemd」的反抗 distro。理解這個爭議，你會明白技術選擇從來不只是技術——還有哲學和權衡。無論立場，systemd 是現代運維的現實，必須掌握。

## 故意弄壞:讓一個服務失敗看排障

```bash
# 寫一個會失敗的服務，練習排障流程
sudo tee /etc/systemd/system/broken.service > /dev/null <<'EOF'
[Unit]
Description=A deliberately broken service
[Service]
Type=simple
ExecStart=/usr/bin/nonexistent-command --foo
EOF

sudo systemctl daemon-reload     # 別忘了！（改/加 unit 檔必做）
sudo systemctl start broken.service
# Job failed... （啟動失敗）

# 排障流程（這是真實運維每天做的）
systemctl status broken.service  # 看狀態：failed，含失敗原因摘要
#   Active: failed (Result: exit-code)
#   Process: ... ExecStart=/usr/bin/nonexistent-command ... (code=exited, status=203/EXEC)
#   status=203/EXEC = 找不到執行檔！

journalctl -u broken.service     # 看完整日誌
#   ... Failed to locate executable /usr/bin/nonexistent-command: No such file or directory

# 修正：改成存在的命令
sudo sed -i 's|/usr/bin/nonexistent-command --foo|/bin/echo hello|' /etc/systemd/system/broken.service
sudo systemctl daemon-reload     # 又要 reload（改了 unit 檔）
sudo systemctl start broken.service
journalctl -u broken.service     # 看到 "hello"（成功了）

# 清理
sudo systemctl stop broken.service 2>/dev/null
sudo rm /etc/systemd/system/broken.service
sudo systemctl daemon-reload
```

> 這個排障流程是運維日常：**`systemctl status`（看摘要和失敗碼）→ `journalctl -u`（看完整日誌）→ 修 unit 檔 → `daemon-reload` → 重試**。注意 `status=203/EXEC`（找不到執行檔）這類退出碼——systemd 的退出碼能告訴你失敗類型（203/EXEC 路徑錯、200/CHDIR 工作目錄錯、權限問題等）。每次改 unit 檔都要 `daemon-reload`（這個實驗故意做了兩次提醒你）。學會這個循環，你就能排查絕大多數服務啟動問題。

## 動手練習

1. 服務操作：對一個服務（如 ssh）`systemctl status`、`is-active`、`is-enabled`，理解 start vs enable

2. 寫 unit 檔：寫一個簡單的 .service（跑一個你的腳本），enable --now，用 journalctl -u 看輸出，練 daemon-reload

3. journalctl 查詢：`journalctl -u ssh --since today`、`journalctl -p err -b`、`journalctl -b -1`（上次開機），熟悉過濾

4. 看失敗服務：`systemctl --failed` 看系統有沒有失敗的服務，對失敗的用 status + journalctl 排查

5. 跑「故意弄壞」：建一個會失敗的服務，走完整排障流程（status → journalctl → 修 → reload → 重試）

## 本章重點整理

- systemd 是 PID 1 和系統總管，管理各種 unit（.service/.timer/.mount/.target），負責服務的完整生命週期
- start（現在啟動）vs enable（開機自啟）是兩件事——要兩者用 `enable --now`；混淆是最常見錯誤
- unit 檔宣告式定義服務：ExecStart（絕對路徑）、Restart=（自動重啟）、User=（最小權限）、After/Requires（相依）；改了必 daemon-reload
- journalctl 查二進位 journal：`-u`（服務）、`-f`（即時）、`-p err`（錯誤）、`-b`（開機）、`--since`（時間）
- journal 二進位格式違背「一切皆文字」是 systemd 最大爭議；systemd vs SysV init 是 Unix 哲學 vs 整合能力之爭

## 自我檢核

- [ ] 能用 systemctl 管理服務，清楚 start 和 enable 的差別
- [ ] 能讀懂並寫一個基本的 .service unit 檔，知道為什麼改了要 daemon-reload
- [ ] 會用 journalctl 查特定服務、特定時間、特定優先級的日誌
- [ ] 能走完「服務啟動失敗」的排障流程（status → journalctl → 修 → reload）
- [ ] 能說出 systemd 的設計爭議（二進位日誌、管太多）和 vs SysV init 的取捨

## 延伸閱讀

### 官方文件

- **[systemd.service(5)](https://www.freedesktop.org/software/systemd/man/systemd.service.html)** + **[systemd.unit(5)](https://www.freedesktop.org/software/systemd/man/systemd.unit.html)** — freedesktop
  - **讀哪裡**：service(5) 的 [Service] 區段選項（Type/ExecStart/Restart）、unit(5) 的 [Unit] 相依選項（After/Requires/Wants）
  - **為什麼值得讀**：寫 unit 檔的權威參考；Type= 的各種值（simple/forking/oneshot/notify）這裡講得最清楚

- **[journalctl(1)](https://www.freedesktop.org/software/systemd/man/journalctl.html)** — freedesktop
  - **讀哪裡**：所有過濾選項（-u/-p/-b/--since）
  - **為什麼值得讀**：journalctl 所有查詢能力的權威

### 文章

- **[systemd for Administrators](http://0pointer.de/blog/projects/systemd-for-admins-1.html)** — Lennart Poettering（systemd 作者）
  - **這篇說什麼**：systemd 作者親自寫的系列教學，從管理員角度講 systemd 的設計和用法
  - **讀哪裡**：Part 1-3（基本概念）、socket activation 那篇
  - **為什麼值得讀**：第一手的設計理念，理解 systemd「為什麼這樣設計」

- **[The systemd controversy / Broken by design](http://ewontfix.com/14/)** — 各方觀點
  - **這篇說什麼**：systemd 爭議的批判觀點（PID 1 太肥、違背 Unix 哲學）
  - **為什麼值得讀**：聽反對方的論點，才能對 systemd 有平衡的判斷（呼應 Ch 21 哲學）

### 書籍

- **《systemd 權威指南》/ 《How Linux Works》— Ch 6 (How User Space Starts)** — Brian Ward（No Starch, 3rd ed）
  - **讀哪幾章**：Ch 6（systemd 的開機流程、unit、相依）
  - **這本書的定位**：把 systemd 放進「整個 Linux 怎麼運作」的脈絡，承接 Ch 14-18 的 process 知識

→ [Ch 32 shell 語法與 quoting](./32-shell-quoting.md)
