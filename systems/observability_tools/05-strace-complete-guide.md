# Ch 5 — strace 完整指南

> **目標**：把真 strace 用到精通——核心選項（-e 過濾、-f 追子 process、-p attach、-c 統計、-T 計時、-s 字串長度、-y 顯示 fd 對應）、怎麼讀 strace 輸出、以及用 strace 解各類問題（卡住/慢/找不到檔案/權限/網路）。你帶著 Ch 4「知道它怎麼運作」的優勢來學用法。strace 是本課的主力工具——這章讓你能用它解決真實問題。

> **環境**：Linux，strace。trace 自己的程式不需 sudo（Ch 0）。

## 為什麼 strace 是主力工具？

Ch 4 你親手寫了 mini-strace，理解了它的原理。現在學真 strace 的完整用法——它是 debug 的主力，因為「程式的真實行為都是 syscall」（Ch 2），strace 讓你看到這些行為。

strace 能回答無數問題：程式卡在哪（哪個 syscall）？為什麼找不到檔案（open 哪個路徑失敗）？為什麼權限錯（哪個操作 EACCES）？為什麼慢（哪個 syscall 花時間）？網路為什麼連不上（connect 失敗）？這些問題，讀原始碼或加 printf 都很慢，strace 直接讓你看到「實際發生什麼」。這章把 strace 的選項和實戰用法講透，讓你能熟練地用它 debug。

## strace 的核心選項

```bash
# === 基本 ===
strace ./prog                    # trace 程式，印所有 syscall
strace -p 1234                   # attach 到正在跑的 process（需權限，Ch 0）
strace -o out.txt ./prog         # 輸出到檔案（不混在程式輸出裡）

# === 過濾（-e，最重要）===
strace -e trace=open,openat ./prog       # 只看 open 相關
strace -e trace=file ./prog              # 只看「檔案相關」的 syscall（一組）
strace -e trace=network ./prog           # 只看網路相關（socket/connect/...）
strace -e trace=read,write ./prog        # 只看 I/O
strace -e trace=%file ./prog             # %file = 檔案類（同 file）
strace -e signal=all ./prog              # 顯示 signal

# === 追蹤子 process（-f，常忘！Ch 2）===
strace -f ./prog                 # 追蹤 fork/clone 出的子 process
strace -ff -o out ./prog         # 每個 process 一個輸出檔（out.PID）

# === 統計與計時 ===
strace -c ./prog                 # 統計：各 syscall 次數/時間（總覽）
strace -T ./prog                 # 每個 syscall 花多久（找慢的）
strace -r ./prog                 # 相對時間戳（syscall 之間的間隔）
strace -tt ./prog                # 絕對時間戳（微秒）

# === 顯示細節 ===
strace -s 1024 ./prog            # 字串顯示長度（預設 32，常截斷）
strace -y ./prog                 # fd 旁顯示它對應的檔案（超有用！）
strace -yy ./prog                # 連 socket 的 IP 也顯示
strace -v ./prog                 # 詳細（struct 完整展開，不省略）
```

```
最常用的 strace 組合：
  strace -f -e trace=file ./prog     ← 看程式（含子process）開了哪些檔案
  strace -p <PID> -e trace=read,write  ← 看卡住的 process 在讀寫什麼
  strace -c ./prog                    ← 程式行為總覽（哪些 syscall 最多）
  strace -T -e trace=... ./prog       ← 找慢的 syscall
  strace -f -e trace=network ./prog   ← debug 網路問題
```

> **`-f`（追子 process）、`-e`（過濾）、`-y`（顯示 fd 對應）、`-c`（統計）是 strace 最該記住的選項**。**`-f`**（追蹤 fork 出的子 process，Ch 2）是最常忘卻最重要的——trace 一個會 fork 的程式（shell script、會開子 process 的服務）不加 `-f` 就漏掉子 process 的所有行為（問題往往在子 process）。**`-e trace=`**（過濾）讓輸出聚焦——`file`（檔案類）、`network`（網路類）、`read,write`（I/O），不過濾的話 syscall 太多淹沒你。**`-y`**（在 fd 旁顯示它對應的檔案）超有用——`read(3, ...)` 變成 `read(3</tmp/foo.txt>, ...)`，不用自己對照 fd 是哪個檔案。**`-c`**（統計各 syscall 次數和時間）給程式行為的總覽（哪些 syscall 最多、最花時間——快速看出「程式在幹嘛」）。**`-T`**（每個 syscall 花多久）找慢的 syscall。**`-s N`**（字串顯示長度，預設 32 會截斷，看完整內容要加大）。**`-o file`**（輸出到檔案，避免和程式輸出混在一起，trace 互動程式時必用）。記住這幾個，你能應付大部分 debug。配合 Ch 4 的理解（你知道 strace 怎麼用 ptrace 看到這些），用起來更有把握。

## 用 strace 解各類問題

```bash
# === 問題 1：程式卡住/沒反應 ===
# attach 到卡住的 process，看它在等什麼
strace -p <卡住的PID>
# 如果停在：
#   read(0, ...) → 在等 stdin（沒輸入）
#   futex(...) → 在等鎖（可能 deadlock）
#   poll/epoll/select → 在等 I/O 事件
#   connect(...) → 在等連線（可能對方沒回）
#   wait4(...) → 在等子 process
# → strace 直接告訴你「卡在哪個 syscall」= 卡在等什麼

# === 問題 2：找不到檔案/設定 ===
strace -f -e trace=openat ./prog 2>&1 | grep -E 'ENOENT'
# openat(... "/etc/myapp/config" ...) = -1 ENOENT
# → 看到程式試圖開哪個檔案但失敗（ENOENT = 不存在）
#   常用來找「程式到底讀哪個設定檔」「為什麼說找不到」

# === 問題 3：權限錯誤 ===
strace -f ./prog 2>&1 | grep -E 'EACCES|EPERM'
# openat(... "/var/log/app.log" ...) = -1 EACCES (Permission denied)
# → 看到哪個操作權限不足

# === 問題 4：為什麼慢 ===
strace -c ./prog                 # 先看哪類 syscall 花最多時間
strace -T -e trace=<那類> ./prog # 再看具體哪個慢
# 如果某個 read/connect 花了好幾秒 → 那就是慢的根源

# === 問題 5：網路問題 ===
strace -f -e trace=network ./prog
# socket(...) = 3
# connect(3, {sa_family=AF_INET, sin_port=htons(443), sin_addr=...}) = -1 ECONNREFUSED
# → 直接看到連哪個 IP:port、失敗原因（refused/timeout）
```

> **strace 最強的用途是「看卡住的 process 在等什麼 syscall」——`strace -p <PID>` 立刻定位卡點**。當 process 卡住（沒反應），`strace -p <PID>` attach 上去，它停在哪個 syscall 就告訴你「在等什麼」：停在 `read(0, ...)` = 等 stdin（沒輸入）、停在 `futex` = 等鎖（可能 deadlock，Ch 16 深入）、停在 `poll`/`epoll` = 等 I/O 事件、停在 `connect` = 等連線（對方沒回，可能網路問題）、停在 `wait4` = 等子 process。這是 debug「卡住」的最快方法——不用猜，直接看它卡在哪個 syscall。其他常見用途：**找檔案**（`-e openat | grep ENOENT` 看程式試圖開哪個檔案失敗——「為什麼說找不到設定」「到底讀哪個檔案」的最佳工具）、**權限**（`grep EACCES` 看哪個操作權限不足）、**慢**（`-c` 看哪類 syscall 花時間，`-T` 看具體哪個慢）、**網路**（`-e network` 看連哪個 IP:port、失敗原因）。這些「症狀 → strace 怎麼用」的對應（呼應 Ch 1 的決策框架）是 strace 實戰的核心。記住：**syscall 回 -1 + errno（ENOENT/EACCES/ECONNREFUSED）就是問題所在**——strace 讓這些錯誤無所遁形。

## 讀 strace 輸出的技巧

```bash
# strace 輸出可能很長，這些技巧幫你聚焦

# 1. 看「失敗的」syscall（回 -1）
strace ./prog 2>&1 | grep '= -1'
# → 所有失敗的操作，往往就是問題

# 2. 看最後幾行（崩潰/卡住前在做什麼）
strace ./prog 2>&1 | tail -20
# → 程式死前的最後行為

# 3. 統計先行（-c 看總覽再深入）
strace -c ./prog
# → 先知道「程式主要在做什麼」（哪些 syscall 多），再聚焦

# 4. 計時找慢點
strace -T ./prog 2>&1 | grep -E '<[0-9]+\.[0-9]+>' | sort -t'<' -k2 -rn | head
# → 找出花最久的 syscall

# 5. 用 -y 省去對照 fd
strace -y -e trace=read,write ./prog
# read(3</etc/passwd>, ...) → 直接知道 fd 3 是 /etc/passwd
```

```
strace 輸出的解讀重點：
  syscall(參數...) = 回傳值 [errno]
        │
  關注：
    = -1 ENOENT/EACCES/...  ← 失敗！（debug 的線索）
    很大的回傳值/很多次     ← 可能是熱點
    卡在某個 syscall 不動    ← 卡點
    重複的失敗+重試          ← 可能 busy loop
        │
  → strace 是「程式的行為日誌」
    讀它就像讀程式的「實際做了什麼」的逐行記錄
```

> **讀 strace 輸出的第一招是 `grep '= -1'`——失敗的 syscall 往往就是問題**。strace 輸出可能上千行，聚焦的技巧：(1) **`grep '= -1'`** 看所有失敗的 syscall（回 -1 + errno）——這些往往就是 bug 的線索（找不到檔案、權限不足、連線失敗）；(2) **`tail`** 看最後幾行——程式崩潰/卡住前的最後行為；(3) **`-c` 統計先行**——先看總覽（哪些 syscall 最多/最花時間）再聚焦；(4) **`-T` + 排序**找最慢的 syscall；(5) **`-y`** 省去手動對照 fd（直接顯示 fd 對應的檔案）。把 strace 輸出當成「程式的行為日誌」——它是程式「實際做了什麼」的逐行記錄。讀的時候關注異常：失敗的 syscall（-1）、重複的失敗+重試（可能 busy loop 燒 CPU）、卡在某個 syscall 不動（卡點）、某個 syscall 異常多次或慢（熱點）。這些異常模式指向問題。strace 的價值在於它顯示「真實」——不是你**以為**程式做什麼（讀碼），而是它**實際**做了什麼。當你帶著「症狀」（卡/慢/錯）去讀 strace 輸出，這些技巧幫你快速從上千行裡找到關鍵的那幾行。

## 故意弄壞:strace 抓真實 bug

```bash
# 用 strace 抓一個「config 讀錯路徑」的 bug
cd ~/obslab
cat > app.c <<'EOF'
#include <stdio.h>
int main() {
    FILE *f = fopen("config.ini", "r");   // 相對路徑！
    if (!f) { printf("Config not found, using defaults\n"); return 1; }
    printf("Config loaded\n");
    fclose(f);
    return 0;
}
EOF
gcc -o app app.c
echo "key=value" > /tmp/config.ini       # config 放在 /tmp

cd /tmp && ~/obslab/app                    # 在 /tmp 跑 → 找得到（相對路徑剛好對）
# Config loaded
cd ~ && ~/obslab/app                        # 在別處跑 → 找不到！
# Config not found, using defaults

# 用 strace 找出「它到底在哪裡找 config」
strace -e trace=openat ~/obslab/app 2>&1 | grep config
# openat(AT_FDCWD, "config.ini", O_RDONLY) = -1 ENOENT (No such file or directory)
# → 看到了！它用「相對路徑」找 config.ini（相對於當前目錄）
#   所以在不同目錄跑結果不同 → bug 是「該用絕對路徑或明確的設定路徑」

# 這就是 strace 的威力：「為什麼有時找得到有時找不到」
# 讀碼可能看不出（fopen("config.ini") 看起來沒問題）
# strace 直接顯示「它在當前目錄找」→ 立刻理解問題
```

> **strace 抓出「相對路徑找 config」這種讀碼看不出的 bug——它顯示「程式實際在哪裡找檔案」**。這個 bug 很經典——程式 `fopen("config.ini")` 用**相對路徑**，所以在不同目錄跑結果不同（在 config 所在目錄跑找得到、在別處跑找不到）。讀原始碼**看不出問題**（`fopen("config.ini")` 看起來沒毛病）。但 strace 直接顯示 `openat(AT_FDCWD, "config.ini", ...) = -1 ENOENT`——`AT_FDCWD` 表示「相對於當前目錄」，立刻理解「它在當前目錄找 config，所以換目錄就找不到」。這就是 strace 的核心威力：**它顯示「程式實際做了什麼」，揭開讀碼看不出的真相**。「為什麼有時 work 有時不 work」「為什麼在我機器上 OK 在伺服器上失敗」這類環境相關的詭異問題，strace 往往一眼看穿（因為它顯示實際的路徑、實際的環境互動）。這是 strace 比「加 printf」「讀碼」高效的地方——你不用猜，直接看實際行為。練習 A 會用 strace 抓更多這類 bug。掌握 strace，你 debug 時多了一雙「看見真實」的眼睛——這是本課最核心的工具，也是 debug 能力的分水嶺。

## 動手練習

1. 核心選項：對一個程式用 `-f`、`-e trace=file`、`-c`、`-T`、`-y`，理解每個選項的作用

2. 抓卡住：寫一個 `read(0)` 等輸入的程式，`strace -p` attach 它，看它停在 read（在等輸入）

3. 找檔案：用 `strace -e openat | grep ENOENT` 找一個程式試圖開但失敗的檔案

4. 統計總覽：`strace -c` 一個程式，看它主要做哪些 syscall（行為總覽）

5. 跑「故意弄壞」：用 strace 抓 app.c 的相對路徑 bug，理解「strace 顯示實際在哪找檔案」

## 本章重點整理

- strace 核心選項：-f（追子 process，常忘）、-e trace=（過濾：file/network/read,write）、-y（顯示 fd 對應）、-c（統計）、-T（計時）、-s（字串長度）、-o（輸出到檔案）
- 最強用途：`strace -p <PID>` 看卡住的 process 在等什麼 syscall（read/futex/connect/wait）
- 各類問題：找檔案（openat+ENOENT）、權限（EACCES）、慢（-c/-T）、網路（-e network）
- 讀輸出技巧：grep '= -1'（失敗的 syscall）、tail（死前行為）、-c 先總覽、-T 找慢
- strace 顯示「程式實際做了什麼」，抓出讀碼看不出的 bug（相對路徑、環境互動）

## 自我檢核

- [ ] 熟練 strace 核心選項，知道何時用 -f/-e/-y/-c/-T
- [ ] 會用 `strace -p` 看卡住的 process 在等什麼
- [ ] 會用 strace 找「找不到的檔案」「權限錯誤」「網路失敗」
- [ ] 會讀 strace 輸出，用 grep '= -1' 等技巧聚焦
- [ ] 理解 strace 為什麼能抓讀碼看不出的 bug（顯示實際行為）

## 延伸閱讀

### 文章

- **[strace 完整教學](https://blog.packagecloud.io/the-definitive-guide-to-linux-system-calls/)** + **[Julia Evans 的 strace 文章](https://jvns.ca/categories/strace/)**
  - **這篇說什麼**：strace 的各種實戰用法和案例
  - **讀哪裡**：Julia Evans 的系列（debug 案例）
  - **為什麼值得讀**：本章用法的大量實戰補充，把 strace debug 講得最實用

- **[strace cheat sheet](https://gist.github.com/dctrwatson/4216702)** — 各種 strace 速查
  - **為什麼值得讀**：放手邊查 strace 選項

### 官方文件

- **[strace(1) man page](https://man7.org/linux/man-pages/man1/strace.1.html)** — strace
  - **讀哪裡**：-e 的各種 trace 類別、所有選項
  - **為什麼值得讀**：strace 所有選項的權威

### 書籍

- **《The Linux Programming Interface》— 各 syscall 章**
  - **為什麼值得讀**：strace 顯示的 syscall，這本書解釋每個是什麼用（看到不認得的 syscall 來查）

下一章看 ltrace 和動態連結——strace 看 syscall，ltrace 看 library 函式呼叫（malloc/strcpy）。理解動態連結（PLT/GOT）你就懂 ltrace 怎麼攔截 library 呼叫。

→ [Ch 6 ltrace 與動態連結](./06-ltrace-and-dynamic-linking.md)
