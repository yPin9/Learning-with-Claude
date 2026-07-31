# 練習 A — 用 strace 抓 bug

> **目標**：整合 Part 1-2 的知識，用 strace（和 ltrace）抓出五個藏在 C 程式裡的 bug。每個 bug 對應一類常見問題（找不到檔案、權限、卡住、fd 洩漏、相對路徑），你要用 strace 找出根因。完成後你具備「用 strace 系統化 debug」的能力——這是本課的核心技能，也是面試常考的實戰題。

## 背景與動機

你學了 strace 的原理（Ch 3-4，親手寫過 mini-strace）和用法（Ch 5），ltrace（Ch 6）。現在實戰——用它們抓真實的 bug。

這正是真實 debug 的樣子：有一個程式行為不對（找不到檔案、卡住、權限錯），你不知道原因，要用 strace 看「它實際做了什麼」找出問題。好的 debugger 不是讀碼猜，而是用 strace 看真實行為。這個練習用五個典型 bug 訓練這個能力——每個都能用 strace 一眼看穿（如果你會用），但讀碼可能看半天。完成它，你建立了「拿到問題 → strace 看 → 定位根因」的反射，這是 debug 能力的核心。

## 任務規格

對五個 bug 程式，你要：
1. **重現**問題（看到錯誤的行為）
2. **用 strace 找出根因**（看它實際做了什麼）
3. **說出 bug 是什麼**（並指出修法）

| Bug | 症狀 | strace 怎麼看 |
|---|---|---|
| Bug 1 | 「找不到設定」| openat 哪個路徑 ENOENT |
| Bug 2 | 「Permission denied」| 哪個操作 EACCES |
| Bug 3 | 程式卡住不動 | 卡在哪個 syscall |
| Bug 4 | 跑久了「too many open files」| fd 洩漏（openat 沒對應 close）|
| Bug 5 | 「有時 work 有時不 work」| 相對路徑/環境依賴 |

**核心要求**：每個 bug 都用 strace 找出根因（不是讀碼猜），並能說出「strace 的哪一行揭示了問題」。

## 五個 bug 程式

```c
// bugs.c —— 編譯成五個程式（或一個程式多個模式）
// 編譯：gcc -o bugN bugN.c
```

### Bug 1：找不到設定

```c
// bug1.c
#include <stdio.h>
int main() {
    FILE *f = fopen("/etc/myapp/config.conf", "r");
    if (!f) { fprintf(stderr, "Config not found!\n"); return 1; }
    printf("OK\n"); fclose(f); return 0;
}
```

### Bug 2：權限錯誤

```c
// bug2.c
#include <stdio.h>
int main() {
    FILE *f = fopen("/var/log/myapp.log", "a");  // 沒權限寫 /var/log
    if (!f) { perror("fopen"); return 1; }
    fprintf(f, "log entry\n"); fclose(f); return 0;
}
```

### Bug 3：卡住

```c
// bug3.c
#include <stdio.h>
int main() {
    char buf[100];
    printf("Processing...\n");
    fgets(buf, sizeof(buf), stdin);  // 等 stdin！但沒人給輸入 → 卡住
    printf("Done\n");
    return 0;
}
```

### Bug 4：fd 洩漏

```c
// bug4.c
#include <stdio.h>
int main() {
    for (int i = 0; i < 2000; i++) {
        FILE *f = fopen("/tmp/test.txt", "r");  // 一直開
        // 忘了 fclose(f)！→ fd 洩漏
    }
    printf("Done\n"); return 0;   // 跑久了 too many open files
}
```

### Bug 5：相對路徑

```c
// bug5.c
#include <stdio.h>
int main() {
    FILE *f = fopen("data.txt", "r");  // 相對路徑！
    if (!f) { fprintf(stderr, "data.txt not found\n"); return 1; }
    printf("OK\n"); fclose(f); return 0;
}
```

## 如果你卡住了

1. 每個 bug 先「重現」（編譯跑，看錯誤行為），再 strace
2. Bug 1/2/5：`strace -e trace=openat ./bugN 2>&1 | grep -E 'ENOENT|EACCES'` 看開檔案的結果
3. Bug 3：程式卡住時，另一個終端機 `strace -p <PID>` 看它卡在哪個 syscall
4. Bug 4：`strace -e trace=openat,close -f ./bug4 2>&1 | grep -c openat` 數開了幾次、close 幾次
5. 看 errno（strace 顯示的 ENOENT/EACCES/...）是定位的關鍵
6. 「= -1」是失敗的 syscall，往往就是問題

## 實作步驟建議

### Step 1：編譯五個 bug 程式
### Step 2：逐個重現問題（看錯誤行為）
### Step 3：對每個用 strace 找根因
### Step 4：說出每個 bug 是什麼 + 修法
### Step 5：對照「讀碼 vs strace」哪個快

## 完整參考解答

**自己抓一遍再看！** 親手用 strace 找出來才學得到。

<details>
<summary>五個 bug 的 strace 診斷</summary>

```bash
cd ~/obslab

# === Bug 1：找不到設定 ===
strace -e trace=openat ./bug1 2>&1 | grep config
# openat(AT_FDCWD, "/etc/myapp/config.conf", O_RDONLY) = -1 ENOENT (No such file or directory)
# 根因：要開的設定檔不存在（/etc/myapp/config.conf 沒建）
# 修法：建立設定檔，或程式該有預設值/更友善的錯誤訊息
# → strace 直接顯示「試圖開哪個檔案、失敗原因 ENOENT」

# === Bug 2：權限錯誤 ===
strace -e trace=openat ./bug2 2>&1 | grep myapp.log
# openat(AT_FDCWD, "/var/log/myapp.log", O_WRONLY|O_CREAT|O_APPEND, 0666) = -1 EACCES (Permission denied)
# 根因：一般使用者沒權限寫 /var/log（EACCES）
# 修法：寫到有權限的目錄，或用適當權限跑，或改 log 位置
# → EACCES 直接指出權限問題，而非「程式邏輯錯」

# === Bug 3：卡住 ===
# 跑起來（會卡住）
./bug3 &
PID=$!
sleep 1
strace -p $PID 2>&1 | head -3
# read(0, ...        ← 卡在這！（停著不動）
# 根因：fgets 在等 stdin（read fd 0），但沒人給輸入 → 卡住
# 修法：給輸入（echo "x" | ./bug3）或程式不該無條件等 stdin
# → strace -p 顯示「卡在 read(0)」= 在等標準輸入
kill $PID

# === Bug 4：fd 洩漏 ===
strace -e trace=openat,close ./bug4 2>&1 > /dev/null
# 統計開了幾次、close 幾次
strace -e trace=openat,close ./bug4 2>&1 | grep -c 'openat.*test.txt'   # 2000 次 open
strace -e trace=openat,close ./bug4 2>&1 | grep -c 'close'              # 很少 close！
# 根因：迴圈裡 fopen 2000 次但沒 fclose → fd 一直累積（洩漏）
#   （如果 ulimit 夠低，會在某次 openat 回 -1 EMFILE "Too many open files"）
# 修法：每次 fopen 後 fclose
# → strace 顯示「open 一堆但沒對應的 close」= fd 洩漏的鐵證
# 驗證 fd 累積（跑時看 /proc/PID/fd）：
# ./bug4 & watch -n0.1 "ls /proc/$!/fd | wc -l"   # fd 數一直漲

# === Bug 5：相對路徑 ===
cd ~ && strace -e trace=openat ~/obslab/bug5 2>&1 | grep data.txt
# openat(AT_FDCWD, "data.txt", O_RDONLY) = -1 ENOENT
# 根因：用相對路徑 "data.txt"（AT_FDCWD = 相對當前目錄）→ 換目錄就找不到
# 修法：用絕對路徑，或明確的設定路徑，或相對於程式位置
# → AT_FDCWD + 相對路徑 → 解釋「為什麼有時 work（在對的目錄）有時不 work」
```

**解答說明**：

- **Bug 1/2/5 都是 openat 失敗**，但 errno 不同：ENOENT（不存在，Bug 1/5）vs EACCES（權限，Bug 2）——errno 是定位的關鍵
- **Bug 1 vs Bug 5**：都 ENOENT，但 Bug 1 是絕對路徑（檔案真的不存在）、Bug 5 是相對路徑（AT_FDCWD，換目錄就找不到）——strace 顯示路徑的形式（絕對 vs 相對）區分它們
- **Bug 3 用 `strace -p`**：程式卡住時 attach，看它停在 `read(0)` = 在等 stdin。這是 debug「卡住」的標準手法
- **Bug 4 數 open/close**：fd 洩漏的特徵是「open 多、close 少」。strace 統計或看 /proc/PID/fd 數量增長
- **核心教訓**：每個 bug，strace 都「直接顯示問題」（失敗的 syscall + errno、卡住的 syscall、open/close 不配對），而讀碼可能看半天（特別是 Bug 5 的相對路徑、Bug 3 的卡住）

</details>

## 測試用案例

| Bug | strace 看到的關鍵 | 根因 |
|---|---|---|
| Bug 1 | openat ... config ... = -1 ENOENT | 設定檔不存在 |
| Bug 2 | openat ... = -1 EACCES | 沒權限寫 |
| Bug 3 | 卡在 read(0) | 等 stdin |
| Bug 4 | open 2000 次, close 極少 | fd 洩漏 |
| Bug 5 | openat AT_FDCWD "data.txt" | 相對路徑 |

## 延伸挑戰（加分）

- **挑戰一**：用你的 mini-strace（Ch 4）抓這些 bug——看它能抓出哪些（openat 失敗的能，需要 errno 顯示的可能要擴充）

- **挑戰二**：寫一個「fd 洩漏偵測腳本」——對一個 process，用 strace 統計 open vs close，報告淨增長（fd 洩漏的自動偵測）

- **挑戰三**：用 ltrace（Ch 6）抓一個記憶體 leak 的 bug（malloc 沒對應 free），對比 strace（看不到 malloc）

- **挑戰四**：抓一個網路 bug——寫一個 connect 到不存在 server 的程式，用 `strace -e network` 看 connect 的失敗（ECONNREFUSED/ETIMEDOUT）

- **挑戰五**：抓一個「環境變數依賴」的 bug——程式讀某個環境變數決定行為，用 strace 看它怎麼讀（或用 ltrace 看 getenv）

## 自我檢核

- [ ] 能用 strace 抓「找不到檔案」（openat + ENOENT）
- [ ] 能區分 ENOENT（不存在）和 EACCES（權限），定位不同問題
- [ ] 會用 `strace -p` 看卡住的 process 在等什麼 syscall
- [ ] 能用 strace 偵測 fd 洩漏（open/close 不配對）
- [ ] 體會到 strace 比「讀碼猜」快——直接看實際行為

這個練習訓練了「用 strace 系統化 debug」的核心能力。接下來 Part 3 進入系統狀態觀察——/proc、lsof、ss、sysstat，從「行為」（strace）擴展到「狀態」（當前快照）。

→ [Ch 7 /proc 檔案系統導覽](./07-proc-filesystem-tour.md)
