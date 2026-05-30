# Ch 33 — Core dump 事後分析

> **目標**：掌握 post-mortem debug——從一個已死 process 的 core dump 還原崩潰現場。理解 core 怎麼產生（`core_pattern`、ulimit、systemd-coredump）、怎麼載入分析、core 能做什麼不能做什麼，以及主動產生 core（`gcore`）拍快照。這是線上崩潰分析的核心技能。

> **環境**：GDB 13/14，Linux x86_64。

## 為什麼 core dump 是線上 debug 的命脈

線上服務半夜崩潰，你不可能 attach（它已經死了）。但如果它留下了 **core dump**——崩潰瞬間整個 process 記憶體的快照——你就能在白天用 GDB 把現場完整還原：崩在哪、backtrace、所有變數的值、stack、heap。

core dump 是「凍結的犯罪現場」。學會分析它，你不用重現 bug 就能 debug 線上崩潰——這是生產環境除錯與 attach、live debug 並列的三大支柱之一。

## 先建立直覺：崩潰瞬間的快照

```
   process 正常跑 ──────X 崩潰（SIGSEGV/SIGABRT...）
                        │
                        ▼
              OS 把整個 process 的狀態 dump 成一個檔案
              ┌──────────────────────────────┐
              │ core dump 檔                  │
              │  - 所有記憶體（stack/heap/...）│
              │  - 崩潰時的暫存器             │
              │  - 載入的 library 清單        │
              │  - signal 資訊                │
              └──────────────────────────────┘
                        │
              之後用 gdb 載入 = 把現場「重播」成靜態快照
```

關鍵：core 是**靜態快照**——你能看一切（記憶體、暫存器、backtrace），但**不能繼續執行**（process 已死，沒有活的 inferior）。`continue`、`step`、inferior call 都不能用。它是「驗屍」，不是「急救」。

## 讓程式產生 core

core dump 預設常被關閉，要先打開：

```bash
# 1. ulimit：允許 core 檔（預設常是 0 = 不產生）
ulimit -c unlimited           # 當前 shell；要永久改 /etc/security/limits.conf

# 2. 確認 core 去哪：core_pattern
cat /proc/sys/kernel/core_pattern
```

`core_pattern` 決定 core 檔的命名與位置：

```bash
# 簡單模式：在 cwd 產生 core.<pid>
echo 'core.%e.%p' | sudo tee /proc/sys/kernel/core_pattern
#   %e=執行檔名 %p=pid %t=時間 %s=signal

# 多數現代 distro：管線給 systemd-coredump
# core_pattern = |/usr/lib/systemd/systemd-coredump %P %u %g %s %t %c %h
```

測試：

```c
// crash.c — gcc -g crash.c -o crash
int main(void){ int *p = 0; return *p; }   // 解 NULL → SIGSEGV
```

```bash
$ ulimit -c unlimited
$ ./crash
Segmentation fault (core dumped)         # "(core dumped)" = 成功產生
$ ls core*                                # 找 core 檔（依 core_pattern）
core.crash.12345
```

## systemd-coredump：現代 distro 的 core 管理

多數現代 distro（Ubuntu/Fedora/Arch）把 core 交給 `systemd-coredump`，存到 journal/`/var/lib/systemd/coredump/`，用 `coredumpctl` 管理：

```bash
coredumpctl list                  # 列出所有 core
coredumpctl info crash            # 看某個 core 的摘要（含 backtrace！）
coredumpctl gdb crash             # 直接用 gdb 開最近的 crash 的 core！
coredumpctl dump crash > my.core  # 把 core 倒出成檔案
```

`coredumpctl gdb` 是最方便的——它自動找到 core、對應的執行檔、甚至幫你拉 debug 符號（配 debuginfod，Ch 0），一行進 GDB。

## 載入 core 分析

手動載入（core 檔 + 對應的執行檔）：

```bash
gdb ./crash core.crash.12345         # 執行檔 + core
# 或
gdb -c core.crash.12345 ./crash
# 或進 GDB 後
(gdb) core-file core.crash.12345
```

> 關鍵：**core 檔要配對的執行檔**。core 只存記憶體與暫存器，符號/型別來自執行檔的 DWARF。執行檔不對（改過、重編過），符號對不上，分析會亂。core + 同一份 binary + 它的 debug info，三者要一致（build-id 串連，Ch 0）。

進去之後：

```
(gdb) bt                              # 崩在哪、怎麼來的——第一招
#0  0x... in main () at crash.c:1
(gdb) frame 0
(gdb) print p                         # 看崩潰時的變數值
$1 = (int *) 0x0                       # NULL！找到原因
(gdb) info registers                  # 崩潰時的暫存器
(gdb) bt full                         # 所有層的所有變數（完整現場）
(gdb) print $_siginfo                 # 是什麼 signal 殺的、出錯位址
(gdb) info proc mappings              # （core 有的話）記憶體佈局
```

core 分析的標準流程和 live crash 一樣（Ch 10）：`bt` 找呼叫鏈 → `frame` 切層 → `print` 看變數。差別只在不能繼續執行。

## `gcore`：主動拍快照

不只崩潰才有 core——你可以對**活著的** process 主動產生 core（拍快照），事後慢慢分析，而不長時間打斷它：

```bash
# 命令列：對 pid 產生 core
gcore -o snapshot 12345           # 產生 snapshot.12345

# 或在 GDB 裡（attach 後）
(gdb) generate-core-file mysnap.core
(gdb) detach                       # 拍完快照就放掉，process 繼續服務
```

用途：

- 線上服務卡住但沒崩，想看狀態又不想長時間 attach（Ch 3 提過 attach 會凍結）——`gcore` 拍個快照，detach 讓它繼續，回頭慢慢分析快照。
- 週期性快照比對（記憶體洩漏：隔一段拍一次，比 heap 成長）。

## core 能做什麼、不能做什麼

```
   能做（靜態檢視）              不能做（需要活 inferior）
   ─────────────────            ──────────────────────
   bt / frame / 切層             continue / step / next
   print 變數（崩潰時的值）       inferior call（print func()）
   info registers               watchpoint / breakpoint 後繼續
   x 看記憶體                    改記憶體後跑
   bt full                      reverse（除非 rr 的 core）
   thread apply all bt          
   分析 heap / stack            
```

記住：core 是**死的快照**。所有「看」都行，所有「跑」都不行。

## 常見問題：core 分析失敗

1. **`bt` 全是 `??`**：執行檔對不上 core（改過/重編過），或 strip 了。確認 build-id 一致（`readelf -n` core 裡的 vs 執行檔）。
2. **`Cannot access memory`**：core 可能不完整（被 ulimit 截斷、或某些記憶體沒 dump）。`coredumpctl info` 看是否 truncated。
3. **找不到 library 符號**：core 記錄了載入的 `.so`，但分析機器上版本不同。需要同版本 library + debug info（debuginfod 救援）。
4. **core 太大**：含整個記憶體可能幾 GB。`coredumpctl` 有大小限制；可調 `core_pattern` 用 `%c` 或 `coredump.conf`。

## 一個完整的線上崩潰分析

```bash
# 收到崩潰報告，core 在 systemd
$ coredumpctl list | grep myservice
$ coredumpctl gdb myservice
...
(gdb) bt                              # 崩在哪
#0  0x... in handle_request (req=0x...) at server.c:204
#1  ...
(gdb) frame 0
(gdb) print *req                      # 看 request 物件
$1 = {url = 0x0, ...}                  # url 是 NULL！
(gdb) bt full                         # 完整現場存報告
(gdb) print $_siginfo._sifields._sigfault.si_addr   # SIGSEGV 存取的位址
$2 = 0x0
# 結論：handle_request 收到 url=NULL 的 request 沒檢查就解參
```

不用重現、不用上線，白天喝著咖啡就把半夜的崩潰查清楚——這就是 core dump 的價值。

## 踩雷集錦

1. **忘了 `ulimit -c unlimited`**：core 根本沒產生。`(core dumped)` 沒出現就是沒設。
2. **執行檔和 core 不匹配**：分析全亂。core 一定要配**產生它的那份** binary。CI 部署要保留每個版本的 binary + debug info。
3. **以為 core 能 continue**：不能。core 是死快照。要能「重播執行」用 rr（Ch 35）。
4. **container 裡的 core**：容器的 `core_pattern` 是 host 的（kernel 全域），core 可能跑到 host。容器崩潰分析要注意這點。
5. **core 含敏感資料**：core 是整個記憶體，含密碼、金鑰、用戶資料。傳輸/保存 core 有資安/隱私風險，別亂上傳。
6. **debug info 不在**：core + binary 但沒 debug info，只能看組語。配 debuginfod 或保留 `.debug` 檔。
7. **PIE 的位址**：core 記錄了實際載入位址，GDB 會自動對齊，但手動算 offset 時記得 PIE 載入基址（Ch 40）。

## 進階：再往深一層

- **core 檔格式**：core 是 ELF 格式（`readelf -h core` 看 Type: CORE），裡面是一堆 `PT_LOAD`（記憶體段）和 `PT_NOTE`（暫存器、signal、檔案映射）。`readelf -n core` 看 notes。
- **`/proc/<pid>/coredump_filter`**：控制 dump 哪些記憶體（私有/共享/huge page…）。預設不 dump file-backed 唯讀段（省空間，反正在 binary 裡）。
- **minidump / breakpad**：Chrome/Firefox 用的精簡 core 格式，只存關鍵資訊，較小。GDB 不直接讀，但概念相同。
- **core + debuginfod**：`coredumpctl gdb` + debuginfod = 自動拉所有需要的 debug 符號，零手動準備。
- **`gcore` 自動化**：監控腳本偵測到服務異常時自動 `gcore` 拍快照——事後分析「為什麼卡住」。
- **kdump（kernel core）**：kernel panic 的 core，用 `crash` 工具分析（不是 GDB），呼應 kernel 課程。
- **core 配 rr**：rr（Ch 35）的 recording 比 core 強——它能「重播執行」，等於可繼續/可 reverse 的 core。

## 動手練習

1. `ulimit -c unlimited`，跑本章的 `crash.c`，確認產生 core，`gdb ./crash core.*` 載入、`bt`、`print p` 還原 NULL 解參。
2. 用 `coredumpctl list` / `coredumpctl gdb` 分析最近的崩潰（如果你的 distro 用 systemd-coredump）。
3. 對一個活著的 `sleep 999`，`gcore -o snap <pid>` 拍快照，載入它 `bt` 看它卡在哪——體會 gcore。
4. 在 core 裡試 `continue`，確認它報錯（死快照不能跑）。
5. 故意改一行 `crash.c` 重編但用**舊的** core 分析，看符號怎麼對不上——理解 binary 必須匹配。
6. `readelf -n core.*` 看 core 的 NOTE（暫存器、signal、檔案映射），理解 core 的內容。

## 本章重點整理

- core dump = 崩潰瞬間的 process 記憶體+暫存器快照；post-mortem debug 的基礎。
- 產生：`ulimit -c unlimited` + `core_pattern`；現代 distro 用 systemd-coredump（`coredumpctl gdb` 一行分析）。
- 載入：`gdb ./binary core`，**binary 必須匹配**（符號來自它的 DWARF）。
- core 是**靜態快照**：能 bt/frame/print/x（看），不能 continue/step/inferior call（跑）。
- `gcore`/`generate-core-file` 主動拍活 process 的快照，detach 後慢慢分析。

## 自我檢核

- [ ] core dump 沒產生，你會檢查哪兩件事？
- [ ] 為什麼分析 core 一定要配「產生它的那份」binary？
- [ ] core 能做什麼、不能做什麼？為什麼不能 continue？
- [ ] 線上服務卡住但沒崩，怎麼拍快照又不長時間打斷它？
- [ ] `coredumpctl gdb` 幫你自動做了哪些事？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Core Files](https://sourceware.org/gdb/current/onlinedocs/gdb/Files.html)** 與 **[generate-core-file](https://sourceware.org/gdb/current/onlinedocs/gdb/Core-File-Generation.html)**
  - **讀哪裡**：core-file、generate-core-file 指令。
  - **和本章的關聯**：本章核心指令的權威。

- **[man core(5)](https://man7.org/linux/man-pages/man5/core.5.html)** 與 **[coredumpctl(1)](https://www.freedesktop.org/software/systemd/man/coredumpctl.html)**
  - **讀哪裡**：core(5) 的 core_pattern 與 coredump_filter；coredumpctl 的子命令。
  - **和本章的關聯**：core 產生機制與 systemd 管理的權威。

### 部落格 / 文章

- **[Analyzing core dumps with GDB](https://developers.redhat.com/articles/2021/04/14/analyze-core-dumps-gdb)** — Red Hat
  - **這篇說什麼**：core 分析的完整實戰流程，含 debuginfod。
  - **為什麼值得讀**：把本章流程放進真實線上崩潰場景。

下一章是 core 的「動態版」——reverse debugging：不只看快照，還能讓程式**往回走**，重看 bug 發生之前發生了什麼。

→ [Ch 34 Reverse debugging](./34-reverse-debugging.md)
