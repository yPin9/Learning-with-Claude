# Ch 30 — cron 與 systemd timer

> **目標**：掌握排程（scheduling）——cron 的語法和運作機制（為什麼 cron job 常因環境變數不同而失敗）、crontab 的管理、systemd timer 的現代替代（為什麼它更強）、以及 at（一次性排程）。這是「讓任務自動定時執行」的核心，是 SysOps 自動化的基礎，也呼應 Ch 29（cron 環境問題本質是環境變數繼承）。

> **環境**：Linux，cron（cronie/vixie-cron）+ systemd（現代 distro 都有）。

## 為什麼需要排程？

運維工作有大量「定時要做的事」：每晚備份、每小時清理暫存、每分鐘檢查服務、每月產報表。手動做不可能（你不可能半夜三點起來跑備份）。排程系統讓這些自動化——你定義「什麼時候做什麼」，系統到時間自動執行。

cron 是 Unix 四十年的排程標準，systemd timer 是現代的更強替代。理解它們回答了運維最常見的痛點之一：「為什麼我的腳本手動跑沒問題，放進 cron 就失敗？」——答案幾乎總是環境變數（Ch 29）：cron 跑腳本時的環境和你的 terminal 不同。搞懂這個，你能省下無數小時的 debug。

## 先建立直覺:cron 是個鬧鐘守護程式

```
cron：一個一直在背景跑的守護程式（daemon），盯著時鐘

  crond（守護程式）每分鐘醒來一次：
    「現在是 03:00，有哪些 job 該在這時跑？」
    → 查所有 crontab，找符合當前時間的 job
    → fork/exec 執行它們（Ch 15）
    → 繼續睡，下一分鐘再檢查
        │
  你的 crontab（排程表）：
    0 3 * * *  /home/alice/backup.sh
    分 時 日 月 週   要執行的命令
    （每天 03:00 跑 backup.sh）
        │
  → cron = 「定時鬧鐘」：到點就 fork/exec 你的命令
    它在背景一直跑，不需要你登入
```

關鍵心智：cron 是一個一直在背景跑的守護程式（crond），每分鐘檢查一次「有哪些 job 該在這個時間跑」，符合的就 fork/exec 執行。你透過 crontab（排程表）告訴它「什麼時間跑什麼命令」。它不需要你登入就會跑——這是它能做「半夜備份」的原因。

## cron 語法:五個時間欄位

cron 的時間語法是必須記住的：

```
crontab 一行的格式：

  ┌───────── 分鐘 (0-59)
  │ ┌─────── 小時 (0-23)
  │ │ ┌───── 日 (1-31)
  │ │ │ ┌─── 月 (1-12)
  │ │ │ │ ┌─ 星期 (0-7，0 和 7 都是週日)
  │ │ │ │ │
  * * * * *  command
        │
  特殊符號：
    *       任意值（每分/每時/每天...）
    */5     每 5 個單位（*/5 在分鐘 = 每 5 分鐘）
    1,15,30 列舉（第 1、15、30）
    1-5     範圍（週一到週五）
    0-23/2  範圍 + 步進（每 2 小時）
```

```bash
# 常見排程範例
0 3 * * *      backup.sh        # 每天 03:00
*/5 * * * *    check.sh         # 每 5 分鐘
0 * * * *      hourly.sh        # 每小時整點
0 0 * * 0      weekly.sh        # 每週日 00:00
0 0 1 * *      monthly.sh       # 每月 1 號 00:00
30 8 * * 1-5   workday.sh       # 週一到週五 08:30
0 */2 * * *    every2h.sh       # 每 2 小時

# 特殊字串（更易讀，cron 支援）
@reboot        startup.sh       # 開機時跑一次
@daily         daily.sh         # 等於 0 0 * * *
@hourly        job.sh           # 等於 0 * * * *
@weekly @monthly @yearly        # 對應的快捷

# 管理 crontab
crontab -e                      # 編輯自己的 crontab（用 $EDITOR，Ch 29）
crontab -l                      # 列出自己的 crontab
crontab -r                      # 刪除整個 crontab（小心！）
sudo crontab -e -u www-data     # 編輯別人的 crontab（需 root）
```

> **cron 的「日」和「星期」欄位是 OR 關係，不是 AND——常見誤解**。如果你寫 `0 0 13 * 5`（13 號**和**週五），cron 會在「每月 13 號」**或**「每個週五」執行——不是「13 號剛好是週五時」。當日（第 3 欄）和星期（第 5 欄）**都不是** `*` 時，cron 用 OR 邏輯（符合任一就跑）。要表達「13 號且週五」要在命令裡自己檢查日期。這是 cron 語法最反直覺的地方。另外 `*/5`（每 5 分鐘）是步進語法——`*/5` 在分鐘欄是 0,5,10,...,55。記住五欄順序（分時日月週）和這些符號，cron 語法就掌握了。線上工具 crontab.guru 能即時翻譯 cron 表達式。

## 底層機制:為什麼 cron job 常常失敗

這是 cron 最重要、最多人踩坑的部分——cron 的執行環境和你的 terminal **完全不同**：

```
為什麼「手動跑成功，cron 跑失敗」：

  你的 terminal：
    PATH=/usr/local/bin:/usr/bin:/bin:/home/alice/bin:...（豐富）
    HOME=/home/alice，一堆 export 的變數
    當前目錄 = 你 cd 到的地方
        │
  cron 執行 job 時的環境（極簡！）：
    PATH=/usr/bin:/bin（很短！可能找不到你的命令）
    HOME=使用者家目錄（但沒讀 .bashrc/.profile！）
    當前目錄 = 使用者家目錄
    沒有你 terminal 裡 export 的任何變數
        │
  → cron 不讀 .bashrc/.profile（它不是互動 shell）
    所以你 terminal 裡的環境，cron 一概沒有
        │
  常見失敗：
    腳本用了 PATH 裡的工具 → cron 的 PATH 短 → command not found
    腳本依賴某個 export 的變數 → cron 沒有 → 行為異常
    腳本用相對路徑 → cron 的當前目錄不同 → 檔案找不到
```

```bash
# 驗證 cron 的環境有多貧瘠
# 在 crontab 加一行，把環境 dump 出來
* * * * * env > /tmp/cron-env.txt
# 等一分鐘後比較
diff <(env | sort) <(sort /tmp/cron-env.txt)
# 會看到 cron 環境少了一大堆變數（你 terminal 有的，cron 沒有）

# 解決方法 1：腳本裡用「絕對路徑」（最可靠）
# 壞：  backup.sh 裡寫 mysqldump ...（cron 可能找不到 mysqldump）
# 好：  /usr/bin/mysqldump ...（絕對路徑，不依賴 PATH）

# 解決方法 2：在 crontab 或腳本開頭設好 PATH
# crontab 頂部可以設環境變數：
PATH=/usr/local/bin:/usr/bin:/bin
0 3 * * * backup.sh

# 解決方法 3：腳本開頭 source 需要的環境
# backup.sh 開頭：
#   source /home/alice/.profile   # 載入需要的環境變數

# 解決方法 4：明確設定所有依賴
0 3 * * * cd /home/alice/project && /usr/bin/env PATH=/usr/bin:/bin ./run.sh
```

> **「手動跑成功、cron 跑失敗」99% 是環境問題（Ch 29）**。cron 執行 job 時**不是互動 shell**——它**不讀** .bashrc/.profile，所以你 terminal 裡的豐富環境（長 PATH、所有 export 的變數）cron 一概沒有。cron 的 PATH 通常只有 `/usr/bin:/bin`（很短），HOME 設了但沒載入 shell 設定，當前目錄是家目錄。後果：腳本裡 `mysqldump`（依賴 PATH）在 cron 裡 "command not found"、相對路徑檔案找不到、依賴的環境變數是空的。**解法**：(1) 腳本裡用**絕對路徑**（`/usr/bin/mysqldump`）；(2) 在 crontab 頂部或腳本開頭設好 PATH；(3) 腳本開頭 `cd` 到正確目錄。debug 技巧：`* * * * * env > /tmp/cron-env.txt` 把 cron 的環境 dump 出來和你的 `env` 比較。這是運維最常見的坑，理解 Ch 29 的環境繼承就懂了根因。

## cron 的輸出與日誌

cron job 的輸出去哪了？這影響你怎麼知道 job 有沒有成功：

```bash
# cron 預設把 job 的 stdout/stderr「email」給使用者（如果系統有設 mail）
# 多數系統沒設 mail → 輸出就「消失」了（你不知道成功失敗）

# 解法：自己重導向輸出到 log 檔（Ch 19）
0 3 * * * /home/alice/backup.sh >> /var/log/backup.log 2>&1
#   >> 追加 stdout，2>&1 連 stderr 一起（Ch 19）
#   → 所有輸出進 log 檔，你能查

# 只在「出錯」時才收到通知（成功時安靜）
0 3 * * * /home/alice/backup.sh > /dev/null 2>> /var/log/backup-error.log
#   stdout 丟棄（成功不囉嗦），stderr 進 error log

# 設定 MAILTO（讓 cron 輸出寄給你）
MAILTO=alice@example.com
0 3 * * * backup.sh         # 有輸出時寄信給 alice

# 看 cron 自己的日誌（cron 有沒有「嘗試」執行 job）
sudo journalctl -u cron      # systemd 系統（Ch 31）
sudo grep CRON /var/log/syslog   # 傳統 syslog
# 能看到 cron 在什麼時間執行了什麼 job（但看不到 job 的輸出）
```

> **cron job 的輸出預設「消失」——你必須自己重導向到 log，否則無從得知成功失敗**。cron 預設把 job 的 stdout/stderr email 給使用者，但多數系統沒設定 mail，輸出就丟失了。後果：你的備份 job 可能已經失敗好幾週，你完全不知道。**鐵律：每個 cron job 都重導向輸出到 log**——`>> /var/log/job.log 2>&1`（Ch 19，stdout+stderr 都進 log）。或更聰明：成功時安靜（`> /dev/null`）、只記錄錯誤（`2>> error.log`），避免 log 被成功訊息淹沒。要 debug「cron 到底有沒有跑我的 job」，看 cron **自己的**日誌（`journalctl -u cron` 或 `/var/log/syslog` 的 CRON 行）——它記錄 cron 嘗試執行了什麼（但不含 job 的輸出，那要靠你自己的重導向）。這是「為什麼我的 cron job 好像沒跑」的排查起點。

## systemd timer:現代的替代

systemd timer 是 cron 的現代替代，在很多方面更強：

```bash
# systemd timer 需要兩個檔案：一個 .service（做什麼）+ 一個 .timer（何時做）

# /etc/systemd/system/backup.service（要執行的任務）
# [Unit]
# Description=Daily backup
# [Service]
# Type=oneshot
# ExecStart=/home/alice/backup.sh

# /etc/systemd/system/backup.timer（何時執行）
# [Unit]
# Description=Run backup daily
# [Timer]
# OnCalendar=*-*-* 03:00:00      # 每天 03:00（比 cron 語法更明確）
# Persistent=true                # 錯過了（關機）開機後補跑！
# [Install]
# WantedBy=timers.target

# 啟用 timer
sudo systemctl enable --now backup.timer
systemctl list-timers            # 看所有 timer 和「下次執行時間」（cron 沒有這個！）
sudo systemctl status backup.timer
journalctl -u backup.service     # 看 job 的完整輸出（systemd 自動收集！）
```

| 面向 | cron | systemd timer |
|---|---|---|
| 設定複雜度 | 簡單（一行）| 複雜（兩個檔案）|
| 輸出/日誌 | 自己重導向，否則丟失 | 自動進 journal（journalctl 查）|
| 錯過補跑 | ✗（關機錯過就錯過）| ✓ Persistent=true |
| 相依性 | ✗ | ✓（等網路/某服務就緒才跑）|
| 資源限制 | ✗ | ✓（CPU/記憶體限制、cgroup）|
| 看下次執行 | ✗ | ✓ list-timers |
| 隨機延遲 | ✗ | ✓ RandomizedDelaySec（避免同時驚群）|

> **systemd timer 比 cron 強，但 cron 更簡單——按場景選**。systemd timer 的關鍵優勢：(1) **自動日誌**——job 的輸出自動進 journal（`journalctl -u backup.service` 直接看，不用自己重導向，解決了 cron 最大的痛點）；(2) **錯過補跑**（`Persistent=true`——如果到執行時間時機器關著，開機後補跑，cron 做不到）；(3) **相依性**（等網路就緒、等某服務啟動才跑）；(4) **資源控制**（限制 job 的 CPU/記憶體，用 cgroup）；(5) `list-timers` 看每個 timer 的下次執行時間。代價是設定較繁瑣（要寫 .service + .timer 兩個檔案）。**選擇**：簡單的個人定時任務、快速腳本用 cron（一行搞定）；生產環境、需要可靠日誌和錯過補跑的關鍵任務用 systemd timer。現代 distro 的系統維護任務（如 logrotate、apt 更新檢查）多已改用 systemd timer。下一章（Ch 31）深入 systemd。

## at:一次性排程

cron 是「重複」排程，at 是「一次性」排程：

```bash
# at：在某個未來時間「執行一次」
echo "backup.sh" | at 03:00              # 今天（或明天）03:00 跑一次
echo "reboot" | sudo at now + 1 hour     # 一小時後
at 14:30 tomorrow                         # 互動式輸入命令，明天 14:30 跑
at> /home/alice/task.sh
at> <Ctrl-D>                              # Ctrl-D 結束輸入

# 管理 at 任務
atq                                       # 列出排隊的 at 任務（queue）
atrm 3                                    # 刪除任務編號 3

# at 的時間格式很靈活
at now + 30 minutes
at 4pm + 3 days
at midnight
at 10:00 AM next week

# at 同樣有 cron 的環境問題（不讀 .bashrc）—— 用絕對路徑
```

> **at 是「一次性」排程，補足 cron 的「重複」排程**。cron 適合「每天/每小時重複」的任務，但有時你只要「30 分鐘後跑一次」「明天下午做一件事」——這是 at 的領域。`echo "command" | at 14:30` 或互動式 `at 14:30`（Ctrl-D 結束）。at 的時間格式很人性化（`at now + 1 hour`、`at 4pm tomorrow`、`at midnight`）。`atq` 看排隊的任務、`atrm` 刪除。at 同樣有 cron 的環境陷阱（不讀 .bashrc，PATH 簡陋），所以一樣**用絕對路徑**。systemd 的對應是 `systemd-run --on-active=30min`（一次性 transient timer）。實務上 at 用得比 cron 少，但「臨時排一個未來任務」時很方便（如「下班前自動關閉某服務」）。

## 故意弄壞:cron 環境陷阱實作

```bash
cd ~/cmdlab
# 寫一個「在 terminal 能跑、cron 會失敗」的腳本來體會環境問題
cat > cron-test.sh <<'EOF'
#!/bin/bash
# 故意依賴環境的腳本
echo "PATH is: $PATH"
echo "HOME is: $HOME"
echo "PWD is: $PWD"
echo "MY_VAR is: $MY_VAR"
which python3 || echo "python3 not found!"
EOF
chmod +x cron-test.sh

# 在 terminal 跑（環境豐富）
export MY_VAR="set in terminal"
./cron-test.sh               # PATH 長、MY_VAR 有值、python3 找得到

# 放進 cron 跑（環境貧瘠），把輸出存檔比較
# crontab -e 加一行（每分鐘跑，測完刪掉）：
# * * * * * /home/你/cmdlab/cron-test.sh >> /tmp/cron-test.log 2>&1
# 等一分鐘後：
cat /tmp/cron-test.log
#   PATH is: /usr/bin:/bin      ← 短！
#   MY_VAR is:                  ← 空！（cron 沒有你 export 的）
#   PWD is: /home/你            ← 家目錄（不是你 cd 的地方）
# 對比兩者，親眼看到 cron 環境的貧瘠
# 測完記得 crontab -e 刪掉那行
```

這個實驗讓你**親眼看到** cron 環境和 terminal 的差異——同一個腳本，terminal 跑 PATH 長、變數齊全，cron 跑 PATH 短、export 的變數全空。這就是「為什麼 cron job 失敗」的根因，理解後你寫 cron 腳本會自動用絕對路徑、自己設好環境。

## 動手練習

1. cron 語法：用 crontab.guru 把幾個需求（每 15 分、每週一早上、每月最後一天）翻成 cron 表達式，理解五欄位

2. 環境實驗：跑「故意弄壞」的 cron-test.sh，比較 terminal 和 cron 的環境差異（親眼看 PATH/變數的不同）

3. 輸出重導向：寫一個 cron job 加 `>> log 2>&1`，確認輸出進 log（而非消失）

4. systemd timer：寫一個簡單的 .service + .timer，`systemctl list-timers` 看下次執行時間，`journalctl -u` 看輸出

5. at 一次性：`echo "date >> /tmp/at-test" | at now + 2 minutes`，兩分鐘後看檔案，理解一次性排程

## 本章重點整理

- cron 是背景守護程式，每分鐘檢查哪些 job 該跑就 fork/exec；crontab 五欄位：分 時 日 月 週
- 日和星期欄都非 * 時是 OR 關係（13 號「或」週五，不是「且」）；*/N 是步進語法
- cron job 失敗 99% 是環境問題：cron 不讀 .bashrc，PATH 短、沒有 export 的變數——用絕對路徑解決
- cron 輸出預設消失（email 沒設）——必須自己 `>> log 2>&1`，否則不知成功失敗
- systemd timer 更強（自動日誌、錯過補跑、相依性、資源控制）但較繁瑣；at 做一次性排程

## 自我檢核

- [ ] 能寫出常見需求的 cron 表達式（每天/每小時/每週/每 N 分鐘）
- [ ] 知道為什麼 cron job 常失敗（環境不同），以及怎麼解決
- [ ] 知道 cron job 的輸出去哪，為什麼要重導向到 log
- [ ] 能說出 systemd timer 相對 cron 的優勢
- [ ] 知道 at 和 cron 的差別（一次性 vs 重複）

## 延伸閱讀

### 官方文件

- **[crontab(5) man page](https://man7.org/linux/man-pages/man5/crontab.5.html)** — Linux man-pages
  - **讀哪裡**：整篇，特別是時間欄位語法和「day of month / day of week」的 OR 規則
  - **為什麼值得讀**：cron 語法的權威，本章「日和星期是 OR」那個坑的官方說明

- **[systemd.timer(5)](https://www.freedesktop.org/software/systemd/man/systemd.timer.html)** + **[systemd.time(7)](https://www.freedesktop.org/software/systemd/man/systemd.time.html)** — freedesktop
  - **讀哪裡**：timer 的 OnCalendar/Persistent 選項，time(7) 的 OnCalendar 時間格式
  - **為什麼值得讀**：systemd timer 的權威，OnCalendar 語法比 cron 更明確的證明

### 工具

- **[crontab.guru](https://crontab.guru/)** — 線上 cron 表達式翻譯器
  - **讀哪裡**：輸入任何 cron 表達式，即時看它的人話翻譯和下次執行時間
  - **為什麼值得讀**：學 cron 語法和驗證表達式的最佳互動工具

### 文章

- **[Why your cron job isn't running](https://www.baeldung.com/linux/cron-job-not-running)** — Baeldung
  - **這篇說什麼**：系統性列舉 cron job 失敗的各種原因（環境、權限、路徑、輸出）和排查方法
  - **為什麼值得讀**：本章「為什麼 cron 失敗」的實戰排查清單，運維必備

→ [Ch 31 systemctl/journalctl 基礎](./31-systemd-basics.md)
