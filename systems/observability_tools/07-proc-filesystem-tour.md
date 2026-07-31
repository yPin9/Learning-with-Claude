# Ch 7 — /proc 檔案系統導覽

> **目標**：掌握 /proc——kernel 把 process 和系統狀態暴露成「檔案」的虛擬檔案系統，是幾乎所有觀察工具的資料來源（ps/top/lsof 底層都讀 /proc）。理解 /proc/<pid>/ 下的關鍵檔案（status/maps/fd/stat/cmdline/environ）怎麼讀，你就能直接從源頭觀察 process，不依賴工具。這章把「系統狀態觀察」的源頭講透——很多工具只是 /proc 的格式化前端。

> **環境**：Linux，cat/ls 即可（/proc 是檔案）。trace 自己的 process 不需 sudo。

## 為什麼 /proc 是觀察的源頭？

ps 看 process、top 看資源、lsof 看 fd——這些工具的資料從哪來？答案是 **/proc**。它是一個「虛擬檔案系統」——不是真的磁碟檔案，而是 kernel 把 process 和系統的當前狀態「暴露成檔案」的介面。你 `cat /proc/<pid>/status` 就讀到那個 process 的當前狀態（不是磁碟上的資料，是 kernel 即時生成的）。

理解 /proc 讓你「直接從源頭觀察」——不用工具，直接讀檔案就能看 process 的狀態、記憶體映射、開的 fd、環境變數、命令列。更重要的是，很多工具（ps/top/lsof）只是 /proc 的格式化前端，理解 /proc 你就理解了它們的資料來源，也能在工具不夠用時自己從 /proc 撈資料。這是「系統狀態觀察」的根基。

## 先建立直覺:把 kernel 狀態變成檔案

```
/proc = kernel 狀態的「檔案介面」

  正常：kernel 的內部狀態（process 列表、記憶體、CPU...）
        在 kernel 空間，使用者程式碰不到
        │
  /proc：kernel 把這些狀態「暴露成檔案」
    cat /proc/<pid>/status → 讀到那個 process 的狀態
    （不是磁碟檔案！是 kernel 即時生成的「快照」）
        │
  結構：
    /proc/<pid>/        每個 process 一個目錄（pid 是 process ID）
      status            狀態（state/記憶體/uid...）人類可讀
      stat              狀態（一行數字，工具用）
      maps              記憶體映射（哪些 library/區段）
      fd/               開啟的 fd（→ 對應的檔案，Ch 8 lsof 看這個）
      cmdline           啟動命令
      environ           環境變數
      cwd               當前目錄（symlink）
    /proc/self/         「當前 process 自己」的捷徑
    /proc/meminfo       系統記憶體
    /proc/cpuinfo       CPU 資訊
    /proc/loadavg       系統負載
        │
  → /proc 是「一切皆檔案」的極致——連 kernel 狀態都是檔案
    ps/top/lsof 都讀 /proc，你也能直接讀
```

關鍵心智：/proc 是「虛擬檔案系統」——kernel 把 process 和系統狀態暴露成檔案（不是磁碟檔案，是即時生成的快照）。`/proc/<pid>/` 是每個 process 的目錄，裡面的 status/maps/fd/cmdline 等是那個 process 的各種狀態。ps/top/lsof 都讀 /proc——理解它，你能直接從源頭觀察。

> /proc 觀察的是 Ch 2 的 process 狀態。如果對 process 有什麼（PID/記憶體/fd/狀態）不熟，回看 [Ch 2](./02-process-syscall-fd-model.md)。lsof（Ch 8）就是 /proc/fd 的格式化前端。

## /proc/<pid>/ 的關鍵檔案

```bash
# 找一個 process 來觀察（用一個 sleep）
sleep 300 &
PID=$!

# === status：人類可讀的狀態 ===
cat /proc/$PID/status
# Name:   sleep
# State:  S (sleeping)          ← process 狀態（R/S/D/Z/T）
# Pid:    12345
# PPid:   6789                  ← 父 process
# VmRSS:  1234 kB               ← 實際用的實體記憶體
# Threads: 1
# → 最有用的「process 快照」（狀態/記憶體/uid/threads）

# === cmdline：啟動命令 ===
cat /proc/$PID/cmdline | tr '\0' ' '; echo
# sleep 300                     ← 怎麼啟動的（參數用 \0 分隔）

# === fd/：開啟的檔案描述符（Ch 8 lsof 的源頭）===
ls -l /proc/$PID/fd
# 0 -> /dev/pts/0  1 -> /dev/pts/0  2 -> /dev/pts/0   ← stdin/out/err

# === maps：記憶體映射 ===
cat /proc/$PID/maps | head
# 555...-555... r-xp ... /usr/bin/sleep    ← 程式碼段
# 7f...-7f...   r-xp ... libc.so.6          ← glibc
# → 看到程式的記憶體佈局（哪些 library、區段權限）

# === environ：環境變數 ===
cat /proc/$PID/environ | tr '\0' '\n' | head
# PATH=... HOME=... ← 它的環境變數

# === cwd / exe：symlink ===
ls -l /proc/$PID/cwd    # → 當前目錄
ls -l /proc/$PID/exe    # → 執行檔的路徑

kill $PID
```

> **`/proc/<pid>/status`（狀態快照）、`fd/`（開的檔案）、`maps`（記憶體映射）、`cmdline`（啟動命令）是最常用的四個——它們是 ps/lsof 等工具的資料源頭**。`/proc/<pid>/` 下的關鍵檔案：**status**（人類可讀的狀態——State 是 R/S/D/Z/T（Ch 2）、VmRSS 是實際用的記憶體、PPid 是父 process、Threads 是執行緒數——這是最有用的「process 快照」）；**cmdline**（怎麼啟動的，參數用 `\0` 分隔，要 `tr '\0' ' '` 才好讀）；**fd/**（開啟的 fd，每個 symlink 到對應的檔案/socket——這就是 **lsof 的源頭**，Ch 8）；**maps**（記憶體映射——程式碼段、library、heap、stack 的位址和權限，debug 記憶體問題、看載入了哪些 library 用）；**environ**（環境變數）；**cwd**/**exe**（symlink 到當前目錄/執行檔）。理解這些，你能**直接從 /proc 觀察 process**——不用工具，`cat /proc/<pid>/status` 就看到狀態、`ls /proc/<pid>/fd` 就看到開的檔案。這也讓你理解工具的本質——`ps` 讀 status/stat、`lsof` 讀 fd/、`top` 讀 stat——它們是 /proc 的格式化前端。當工具不夠用（要某個工具沒顯示的欄位），你直接讀 /proc。`/proc/self/` 是「當前 process 自己」的捷徑（腳本裡好用）。

## 系統層級的 /proc

```bash
# /proc 不只 process，還有系統層級的資訊
cat /proc/loadavg
# 0.52 0.58 0.59 2/345 12346
# 1/5/15 分鐘負載  執行中/總 process  最後 PID
# → uptime 的資料源頭

cat /proc/meminfo | head
# MemTotal:  16384000 kB
# MemFree:   2048000 kB
# MemAvailable: ...           ← free 命令的源頭

cat /proc/cpuinfo | grep -E 'model name|processor' | head
# → CPU 型號、核心數（nproc 的源頭）

cat /proc/stat | head -1
# cpu  12345 67 890 ...        ← CPU 時間統計（top 算 CPU% 的源頭）

cat /proc/uptime
# 123456.78 234567.89          ← 系統運行時間（uptime 的源頭）

cat /proc/version
# Linux version 6.x ...         ← kernel 版本（uname 的源頭）

# /proc/sys/：kernel 參數（sysctl 的源頭）
cat /proc/sys/kernel/pid_max    # 最大 PID
cat /proc/sys/net/ipv4/ip_forward   # IP 轉發（networking 課的 Ch 0）
```

> **系統層級的 /proc（loadavg/meminfo/cpuinfo/stat）是 uptime/free/nproc/top 的資料源頭——它們都是 /proc 的格式化前端**。/proc 不只有 process，還有系統層級的狀態：**loadavg**（系統負載，uptime 的源頭）、**meminfo**（記憶體，free 的源頭）、**cpuinfo**（CPU 資訊，nproc 的源頭）、**stat**（CPU 時間統計，top 算 CPU% 的源頭——top 是讀兩次 /proc/stat 算差值）、**uptime**（運行時間）、**version**（kernel 版本，uname 的源頭）。還有 **/proc/sys/**（kernel 可調參數，sysctl 讀寫的就是這些檔案——如 networking 課調的 `ip_forward`）。理解這個，你看到一個系統監控工具（top/free/uptime/sar）時，知道它的資料來自 /proc 的某個檔案。這有實用價值：(1) 寫監控腳本時直接讀 /proc（不依賴特定工具）；(2) 工具顯示的數字看不懂時，去看 /proc 的原始資料；(3) 在精簡的環境（容器、嵌入式）沒裝工具時，直接 `cat /proc/...` 觀察。/proc 是 Linux「一切皆檔案」哲學的極致——連 kernel 的即時狀態都是檔案，你用最基本的 `cat`/`ls` 就能觀察整個系統。

## 用 /proc 觀察 process 狀態

```bash
# 實戰：用 /proc debug process 問題

# 1. process 卡住 → 看它的狀態和卡在哪
sleep 300 &
PID=$!
cat /proc/$PID/status | grep State    # State: S (sleeping)
# 配合 /proc/<pid>/wchan（卡在哪個 kernel 函式）
cat /proc/$PID/wchan; echo            # hrtimer_nanosleep（在 sleep）

# 2. process 記憶體一直漲 → 看 VmRSS 變化
# watch -n1 "grep VmRSS /proc/$PID/status"    # 持續看記憶體（漲 = 可能 leak）

# 3. process 開太多 fd → 數 fd
ls /proc/$PID/fd | wc -l              # 開了幾個 fd（漲 = fd 洩漏）

# 4. zombie process（Ch 2 的 Z 狀態）
cat /proc/<zombiePID>/status | grep State    # State: Z (zombie)

# 5. process 是什麼程式、怎麼啟動的
cat /proc/$PID/cmdline | tr '\0' ' '  # 啟動命令
ls -l /proc/$PID/exe                  # 執行檔路徑

# 6. process 在哪個目錄、用什麼環境
ls -l /proc/$PID/cwd                  # 當前目錄
cat /proc/$PID/environ | tr '\0' '\n' | grep PATH    # PATH 環境變數
kill $PID
```

> **/proc 能直接 debug「卡住」（wchan）、「記憶體漲」（VmRSS）、「fd 洩漏」（fd 數量）——這些都是常見的 process 問題**。實戰用 /proc 觀察：**卡住** → `/proc/<pid>/status` 看 State、`/proc/<pid>/wchan` 看「卡在哪個 kernel 函式」（如 `hrtimer_nanosleep` = 在 sleep、`futex` = 等鎖——這補充了 strace 的視角，從 kernel 角度看卡點）；**記憶體漲** → `watch grep VmRSS /proc/<pid>/status` 持續看實體記憶體（漲 = 可能 leak，配合 valgrind 確認 Ch 15）；**fd 洩漏** → `ls /proc/<pid>/fd | wc -l` 數 fd（漲 = 洩漏，這是練習 A Bug 4 的另一個觀察角度）；**zombie** → status 的 State: Z（Ch 2）；**「這是什麼程式」** → cmdline（啟動命令）、exe（執行檔路徑）、cwd（當前目錄）、environ（環境變數）。這些讓你不用任何工具就能 debug process——直接讀 /proc 的檔案。特別是 **`watch` + /proc** 是觀察「變化」的利器（記憶體/fd 隨時間增長 = 洩漏的信號）。理解 /proc，你有了「不依賴工具的觀察能力」——在任何 Linux（包括精簡的容器/嵌入式）都能用 cat/ls 觀察 process 和系統。這是系統觀察最底層、最可靠的能力。

## 故意弄壞:從 /proc 看 process 的真相

```bash
# 用 /proc 揭開 process 的各種「真相」
cd ~/obslab

# 真相 1：被刪的執行檔還在跑（exe symlink 顯示 deleted）
cp /bin/sleep ./mysleep
./mysleep 300 &
PID=$!
rm ./mysleep                          # 刪掉執行檔（但 process 還在跑）
ls -l /proc/$PID/exe
# ... /home/.../mysleep (deleted)      ← 執行檔被刪但 process 還在！
#   → 這是「為什麼更新程式後舊版還在跑」的觀察方法
kill $PID

# 真相 2：fd 指向被刪的檔案（佔空間的元兇）
cat > leaker.c <<'EOF'
#include <fcntl.h>
#include <unistd.h>
int main() {
    int fd = open("/tmp/bigfile", O_CREAT|O_WRONLY, 0644);
    // 寫一些資料... 然後睡著（fd 開著）
    write(fd, "data", 4);
    sleep(300);
    return 0;
}
EOF
gcc -o leaker leaker.c
./leaker &
PID=$!
rm /tmp/bigfile                       # 刪檔案（但 fd 還開著）
ls -l /proc/$PID/fd/                  # 3 -> /tmp/bigfile (deleted)
#   → 檔案被刪但 fd 開著 = 空間不釋放（df 顯示滿，但 du 找不到檔案）
#   這是「磁碟滿但找不到大檔案」的經典 debug！
kill $PID

# 真相 3：看 process 實際載入的 library（maps）
cat /proc/self/maps | grep '\.so' | awk '{print $6}' | sort -u
# → 當前 shell 載入的所有 library
```

> **`/proc/<pid>/exe (deleted)` 和 `fd/N (deleted)` 揭露「被刪的執行檔還在跑」「被刪的檔案還佔空間」——這是兩個經典 debug 場景**。/proc 能揭露 process 的真相：(1) **被刪的執行檔還在跑**——`ls -l /proc/<pid>/exe` 顯示 `(deleted)` 表示「執行檔被刪了但 process 還在跑舊版」（這是「為什麼更新程式後行為還是舊的」「為什麼 apt 更新後要重啟服務」的觀察方法——舊 process 還在跑被刪的舊執行檔）；(2) **被刪的檔案還佔空間**——`ls -l /proc/<pid>/fd/` 顯示 `(deleted)` 表示「檔案被刪但 fd 還開著」，這是**「磁碟滿但 du 找不到大檔案」的經典 debug**！檔案被 rm 了，但有 process 還開著它的 fd，所以空間不釋放（inode 引用計數還 >0，linux_commands 課 Ch 6 的機制）——`df` 顯示磁碟滿，但 `du` 找不到那個檔案（因為它沒有檔名了），只有 `lsof | grep deleted` 或 `/proc/*/fd` 能找到「誰開著被刪的大檔案」（解法：重啟那個 process 釋放 fd）。(3) **maps 看載入的 library**。這些是 /proc 獨有的觀察能力——它揭露了「檔案系統看不到但 process 還持有」的真相。理解這些，你能 debug 最詭異的問題（磁碟滿找不到檔案、更新後行為沒變）。這也呼應 linux_commands 課的 inode/fd 機制——/proc 是觀察它的窗口。

## 動手練習

1. 探索 /proc/self：`cat /proc/self/status`、`ls /proc/self/fd`、`cat /proc/self/maps`，看自己 shell 的狀態

2. 觀察 process：跑一個 sleep，從 /proc 看它的 status/cmdline/fd/wchan/exe

3. 系統 /proc：cat loadavg/meminfo/uptime，對照 uptime/free 命令（理解工具讀 /proc）

4. 看變化：用 `watch grep VmRSS /proc/<pid>/status` 看一個程式的記憶體變化

5. 跑「故意弄壞」：觀察「被刪的執行檔還在跑」（exe deleted）和「被刪檔案還佔空間」（fd deleted）

## 本章重點整理

- /proc 是虛擬檔案系統——kernel 把 process/系統狀態暴露成「檔案」（即時生成的快照，不是磁碟檔案）
- /proc/<pid>/ 關鍵檔案：status（狀態快照）、fd/（開的檔案，lsof 源頭）、maps（記憶體映射）、cmdline（啟動命令）、environ/cwd/exe
- 系統層級：loadavg（uptime 源頭）、meminfo（free 源頭）、stat（top 源頭）、/proc/sys（sysctl）——工具都是 /proc 的前端
- /proc 能 debug：卡住（wchan）、記憶體漲（VmRSS + watch）、fd 洩漏（fd 數量）、zombie（State: Z）
- /proc 揭露真相：被刪執行檔還在跑（exe deleted）、被刪檔案還佔空間（fd deleted，磁碟滿找不到檔案的 debug）

## 自我檢核

- [ ] 能解釋 /proc 是什麼（kernel 狀態的檔案介面，即時快照）
- [ ] 知道 /proc/<pid>/ 的關鍵檔案（status/fd/maps/cmdline）各看什麼
- [ ] 知道 ps/top/lsof 等工具讀 /proc，能直接從 /proc 觀察
- [ ] 會用 /proc debug 卡住/記憶體漲/fd 洩漏
- [ ] 能用 /proc 揭露「被刪檔案還佔空間」（磁碟滿的經典 debug）

## 延伸閱讀

### 官方文件

- **[proc(5) man page](https://man7.org/linux/man-pages/man5/proc.5.html)** — Linux man-pages
  - **讀哪裡**：/proc/<pid>/ 下各檔案的說明（status/stat/maps/fd）
  - **為什麼值得讀**：/proc 的權威，每個檔案的格式和欄位

### 文章

- **[Linux /proc 詳解](https://www.kernel.org/doc/html/latest/filesystems/proc.html)** — Linux kernel docs
  - **讀哪裡**：process-specific 那節
  - **為什麼值得讀**：/proc 的權威來源

- **[Julia Evans 的 /proc 文章](https://jvns.ca/blog/2014/02/13/maybe-the-strace-output-isnt-that-bad/) / 各種 /proc 探索**
  - **為什麼值得讀**：把 /proc 的實用觀察講得易懂

### 書籍

- **《The Linux Programming Interface》— Ch 12 (System and Process Information)** — Kerrisk
  - **讀哪幾章**：Ch 12（/proc 的程式存取）
  - **這本書的定位**：/proc 的權威，從程式角度

下一章看 lsof——它是 /proc/fd 的格式化前端，專門看「process 開了哪些檔案/socket」。理解 fd 視角的觀察，是 debug I/O 和網路問題的關鍵。

→ [Ch 8 lsof 與 fd 視角](./08-lsof-and-fd-view.md)
