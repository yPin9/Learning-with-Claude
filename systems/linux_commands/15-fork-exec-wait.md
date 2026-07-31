# Ch 15 — fork/exec/wait

> **目標**：深入 Unix 建立 process 的核心三 syscall——fork（複製 process）、exec（取代成新程式）、wait（回收 child），理解「為什麼是 fork+exec 兩步而不是一步」、copy-on-write、以及這個模型如何讓 shell 的所有命令執行運作。這是 Ch 1 的「fork+exec」的深挖。

> **環境**：Linux，C + strace。承接 Ch 1（fork+exec 模型）、Ch 14（process 狀態）。原理深挖章。

## 為什麼建立 process 要兩步（fork + exec）？

其他系統（如 Windows）用一個 `CreateProcess` 一步建立並執行新程式。Unix 卻分成兩步：`fork`（複製當前 process）+ `exec`（讓複製出的 process 變成新程式）。為什麼這麼「麻煩」？

這個設計選擇是 Unix 最深刻的決定之一。兩步分離帶來巨大的彈性——在 fork 之後、exec 之前，你能調整子 process 的環境（重導向 fd、改變數、設權限）。這就是 shell 怎麼實作管線、重導向、背景執行的關鍵。理解 fork+exec，你就理解了 shell 所有 process 操作的底層。

## 先建立直覺：fork 是複製，exec 是變身

```
fork + exec 兩步：

  shell（parent process）
        │ fork()：複製自己
        ▼
  ┌──────────────┬──────────────┐
  │  parent      │  child       │
  │（原 shell）  │（複製的 shell）│  ← child 是 parent 的「複製品」
  │              │              │     （同樣的記憶體、fd、環境）
  │ wait(child)  │ exec("ls")   │  ← child 變身成 ls
  │（等 child）  │              │
  └──────────────┴──────────────┘
                       │ exec 後 child 不再是 shell，是 ls
                       ▼
                   ls 執行，結束
                       │
  parent 的 wait 返回 ←┘（回收 child）
        │
  shell 繼續（顯示 prompt）
        │
  → fork：一個 process 變兩個（複製）
    exec：process 換掉自己的程式（變身，PID 不變）
    wait：parent 回收結束的 child
```

兩步的精髓：`fork` 複製出一個一模一樣的子 process，`exec` 讓子 process「變身」成要執行的程式。中間有個窗口——fork 後、exec 前——子 process 還是 shell 的複製，這時能調整它（這是彈性的來源）。

## fork：複製 process

```c
// fork 的行為（C，概念）
#include <unistd.h>
#include <stdio.h>

int main() {
    pid_t pid = fork();    // 複製當前 process
    // fork 後，有「兩個」process 從這裡繼續執行！

    if (pid == 0) {
        // child：fork 回傳 0
        printf("I am child, my PID is %d\n", getpid());
    } else if (pid > 0) {
        // parent：fork 回傳 child 的 PID
        printf("I am parent, my child is %d\n", pid);
    } else {
        // fork 失敗（pid < 0）
        perror("fork");
    }
    return 0;
}
```

```
fork 的神奇之處：
  fork() 呼叫「一次」，但「回傳兩次」！
    在 parent：回傳 child 的 PID（> 0）
    在 child： 回傳 0
        │
  fork 後，parent 和 child 是「幾乎一樣」的兩個 process：
    - 同樣的程式碼、變數值（複製的記憶體）
    - 同樣的開啟檔案（fd，Ch 19）
    - 同樣的 CWD、環境變數
    - 但：不同的 PID、fork 回傳值不同
        │
  → 用 fork 的回傳值區分「我是 parent 還是 child」
    這是 fork 程式的標準模式（if pid == 0 是 child）
```

```bash
# 用 strace 看 shell fork（Ch 1 看過）
strace -f -e clone,fork,execve bash -c 'ls' 2>&1 | grep -E "clone|execve"
# clone(...) = 12345                    ← fork（現代用 clone syscall 實作）
# execve("/usr/bin/ls", ...) = 0        ← child exec 成 ls
```

> fork「呼叫一次回傳兩次」是它最反直覺的特性。一個 fork 呼叫後，**兩個** process 從那一行繼續執行——parent 拿到 child 的 PID，child 拿到 0。用回傳值區分身份（`if (pid == 0)` 是 child）。fork 複製了 parent 的一切（記憶體、fd、環境），所以 child 一開始和 parent 一模一樣。現代 Linux 用 `clone` syscall 實作 fork（clone 更通用，能控制複製什麼——這也是 thread 的基礎）。

## copy-on-write：fork 為什麼不慢

fork 要「複製整個 process 的記憶體」——如果 process 用了幾 GB 記憶體，每次 fork 都複製幾 GB 不是很慢？答案是 **copy-on-write（CoW）**：

```
fork 的 copy-on-write 優化：
  天真做法：fork 時把 parent 的記憶體全複製給 child
    → process 用 4GB，fork 要複製 4GB（慢、浪費）
        │
  CoW（copy-on-write）：fork 時「不」真的複製記憶體
    parent 和 child「共享」同一份物理記憶體（標記為唯讀）
    → fork 瞬間完成（只複製頁表，不複製資料）
        │
  當 parent 或 child 「寫入」某頁記憶體時：
    kernel 偵測到寫入唯讀頁 → 才複製那一頁（copy-on-write）
    → 只複製被修改的頁，沒改的繼續共享
        │
  → fork 快（不複製全部），且省記憶體（只複製改的部分）
    這對「fork 後馬上 exec」特別高效
    （exec 換掉整個記憶體，所以 fork 複製的記憶體根本沒用到）
```

> copy-on-write 是 fork 高效的關鍵。fork 不真的複製記憶體，而是讓 parent 和 child **共享**同一份物理記憶體（標記唯讀），只在某方「寫入」時才複製那一頁。這讓 fork 瞬間完成（不複製 GB 級記憶體）。對「fork 後馬上 exec」（shell 執行命令的模式）特別高效——exec 會換掉整個記憶體，所以 fork 複製的記憶體本來就沒用到，CoW 避免了無謂的複製。CoW 是現代 OS 記憶體管理的核心技巧（也用於 mmap、容器映像等）。理解它，你會懂為什麼 fork 一個大 process 不慢。

## exec：取代成新程式

```c
// exec 系列（C，概念）
#include <unistd.h>

int main() {
    // execvp：在 PATH 找程式，用 argv 陣列
    char *args[] = {"ls", "-l", "/tmp", NULL};
    execvp("ls", args);
    // 如果 exec 成功，下面的 code 「永遠不會執行」！
    // 因為 process 已經變成 ls 了（自己被取代）
    perror("execvp");   // 只有 exec 失敗才到這
    return 1;
}
```

```
exec 的本質：
  exec 不建立新 process，它「取代當前 process 的程式」
    - 換掉記憶體（程式碼、資料、堆疊）
    - 換成新程式（如 ls）
    - PID 不變！（還是同一個 process，只是程式換了）
        │
  exec 成功 → 不返回（當前程式沒了，變成新程式）
  exec 失敗 → 返回 -1（找不到程式、沒權限）
        │
  exec 家族（execl/execv/execlp/execvp/execve...）：
    差別在參數傳遞方式（list vs vector、有無 PATH 搜尋、有無 env）
    底層都是 execve syscall
        │
  → exec = 「變身」，不是「生小孩」
    Ch 1 的 `exec ls`（取代 shell）就是直接 exec 不 fork
```

> exec 是「變身」不是「生小孩」——它取代當前 process 的程式（記憶體換掉，PID 不變），不建立新 process。exec 成功就**不返回**（當前程式沒了），所以 exec 後面的 code 只有「exec 失敗」才會執行（這是判斷 exec 是否成功的方式）。exec 家族（execl/execvp/execve...）差別在參數傳遞（list/vector、有無 PATH 搜尋），底層都是 execve syscall。Ch 1 的 `exec ls`（讓 shell 直接變成 ls）就是只 exec 不 fork——所以 shell 沒了。

## wait：回收 child

parent 用 `wait` 等待並回收 child（避免 zombie，Ch 14）：

```c
// wait（C，概念）
#include <sys/wait.h>
#include <unistd.h>
#include <stdio.h>

int main() {
    pid_t pid = fork();
    if (pid == 0) {
        // child：執行某事
        execlp("ls", "ls", NULL);
    } else {
        // parent：等 child 結束
        int status;
        wait(&status);     // 阻塞，直到 child 結束
        // 從 status 取得 child 的退出碼
        if (WIFEXITED(status)) {
            printf("child exited with %d\n", WEXITSTATUS(status));
        }
    }
    return 0;
}
```

```
wait 的作用（Ch 14 的 zombie 解法）：
  child 結束 → 變 zombie（屍體，保留退出狀態）
  parent wait() → 讀取 child 的退出狀態 + 回收 zombie
        │
  wait 變體：
    wait()：等任一 child 結束
    waitpid()：等特定 child，或非阻塞（WNOHANG）
        │
  退出狀態（status）編碼了 child 怎麼結束：
    WIFEXITED + WEXITSTATUS：正常 exit，退出碼
    WIFSIGNALED + WTERMSIG：被 signal 殺死，哪個 signal
        │
  → parent 不 wait → child 變永久 zombie（Ch 14）
    這就是 shell 的 $? 變數的來源（上一個命令的退出碼，Part 8）
```

> `wait` 是 parent 回收 child 並讀取退出狀態的機制。child 結束變 zombie（保留退出狀態），parent `wait()` 讀狀態 + 回收 zombie（Ch 14）。退出狀態編碼了 child 怎麼結束——正常 exit（退出碼）或被 signal 殺（哪個 signal）。shell 的 `$?` 變數（上一命令的退出碼，Part 8）就來自 wait 取得的狀態。`waitpid(WNOHANG)`（非阻塞）讓 parent 不卡住等 child（job control 用，Ch 18）。理解 wait，你會懂 shell 怎麼知道命令成功失敗（`$?`），以及為什麼忘記 wait 會留 zombie。

## fork+exec 的彈性：shell 的所有魔法

為什麼 Unix 分成 fork+exec 兩步？因為**中間的窗口**讓你能調整 child：

```
fork 後、exec 前的窗口（child 還是 shell 的複製）：
  shell 在這個窗口調整 child，實作各種功能：

  重導向（Ch 19）：command > file
    fork 後，child 把 fd 1（stdout）重導到 file，再 exec
    → command 的輸出進 file（command 不知道，它只是寫 fd 1）

  管線（Ch 20）：cmd1 | cmd2
    fork 兩個 child，用 pipe 連接它們的 fd，再各自 exec

  背景執行（Ch 18）：command &
    fork child 後 parent 不 wait（不等），繼續顯示 prompt

  改環境/權限：
    fork 後 child 改環境變數、降權限（setuid），再 exec
        │
  → 這就是 fork+exec 兩步的價值：
    在「複製」和「變身」之間有個窗口能調整
    一步式的 CreateProcess（Windows）做這些要傳一堆參數
    fork+exec 用「先複製再調整再變身」更彈性
```

> **fork+exec 兩步的價值在「中間的窗口」**。fork 後、exec 前，child 還是 shell 的複製，這時 shell 能調整它——重導向 fd（`> file`，Ch 19）、用 pipe 連接（`|`，Ch 20）、改環境、降權限——然後才 exec。command 自己不知道這些（它只是讀寫 fd），調整在 exec 前就做好了。這就是 shell 所有「魔法」（重導向、管線、背景）的底層機制。Windows 的一步式 CreateProcess 要做這些得傳一大堆參數；Unix 的「先複製、再調整、後變身」更彈性優雅。這是 Unix 設計哲學的經典展現——簡單的原語（fork/exec）組合出強大的功能。

## 故意弄壞：fork bomb（別在真機跑）

```bash
# fork bomb：無限 fork，耗盡 process table（DoS 自己）
# 千萬不要在重要系統跑！（會讓系統無法回應）
# :(){ :|:& };:
#   ↑ 定義一個函式 :，它呼叫自己兩次（fork）並背景執行
#     無限遞迴 fork → process 數爆炸 → 系統卡死

# 防護：ulimit 限制 process 數
ulimit -u                # 看當前使用者的 process 數限制
ulimit -u 100            # 限制最多 100 個 process（防 fork bomb）
```

fork bomb（`:(){ :|:& };:`）是無限 fork 的惡意（或意外）程式碼——每個 process fork 兩個，指數爆炸，耗盡 process table，系統卡死。防護是 `ulimit -u`（限制每使用者的 process 數）。這展示了 fork 的威力和危險——無節制的 fork 能 DoS 系統。理解這個，你會懂為什麼系統要限制 process 數，以及為什麼這串看似無意義的符號這麼危險（別好奇在真機跑）。

## 踩雷集錦

1. **以為 fork 回傳一次**：fork 呼叫一次回傳兩次（parent 拿 child PID，child 拿 0）。用回傳值區分身份

2. **以為 exec 會返回**：exec 成功不返回（process 變身了）。exec 後的 code 只有失敗才執行。別在 exec 後寫「正常流程」的 code

3. **以為 fork 複製記憶體很慢**：CoW 讓 fork 不真的複製（共享 + 寫時才複製）。fork 一個大 process 不慢

4. **parent 不 wait 留 zombie**：fork 了 child 要 wait 回收，否則 child 變 zombie（Ch 14）。shell 自動處理，但寫程式要記得

5. **混淆 PID 在 exec 前後**：exec 不改 PID（同一個 process，換了程式）。fork 才產生新 PID（新 process）

## 進階：vfork、posix_spawn 與 fork 的爭議

fork 雖經典，但有爭議和替代：

```
fork 的問題與替代：
  fork 的問題：
    - 大 process fork 即使有 CoW，複製頁表也有成本
    - fork 後在 multithreaded 程式裡很危險
      （只複製呼叫 fork 的 thread，鎖狀態可能不一致）
    - 「fork 後只能 exec」的場景，複製整個地址空間是浪費
        │
  替代：
    posix_spawn：fork+exec 的「合一」介面（內部可能用 vfork 優化）
      適合「fork 後馬上 exec」的常見場景，更高效安全
    vfork：fork 但不複製地址空間（child 借用 parent 的，直到 exec）
      危險（child 改東西會影響 parent），現代少用
    clone：fork 的底層，能精細控制複製什麼（thread 用它）
        │
  → 簡單場景 fork+exec 仍主流；
    高效能/multithreaded 場景考慮 posix_spawn
```

> fork 雖是 Unix 經典，但有現代爭議。在 multithreaded 程式裡 fork 很危險——它只複製呼叫 fork 的 thread，其他 thread 持有的鎖在 child 裡永遠鎖著（死鎖）。而「fork 後馬上 exec」的常見場景，複製整個地址空間（即使 CoW）是浪費。**posix_spawn** 是 fork+exec 的合一介面（內部優化），更適合這個場景。**vfork**（不複製地址空間，child 借 parent 的直到 exec）更快但危險（現代少用）。有篇著名論文〈A fork() in the road〉論證 fork 在現代是個有問題的抽象。但簡單場景 fork+exec 仍是主流且最易懂——本課用它建立理解。理解這些爭議，你會知道 fork 不是唯一解。

## 動手練習

1. 寫一個 fork 程式（C）：fork 後 parent 印 child PID、child 印自己 PID。觀察「一次呼叫兩次返回」。用 strace -f 看 clone

2. 看 shell 的 fork+exec：`strace -f -e clone,execve,wait4 bash -c 'ls'`，認出 clone（fork）、execve（child 變 ls）、wait4（parent 回收）。這是 Ch 1 的完整版

3. 觀察 CoW：fork 一個用了一些記憶體的 process，看 fork 是否瞬間（不複製全部）。`/proc/<pid>/smaps` 看共享頁

4. 安全地理解 fork bomb：讀 `:(){ :|:& };:` 的結構（函式遞迴 fork），**不要執行**。設 `ulimit -u 50` 理解防護

## 本章重點整理

- 建立 process 是 fork+exec 兩步：fork（複製 process，一次呼叫兩次返回）+ exec（取代成新程式，PID 不變）
- fork 用 copy-on-write：不真的複製記憶體（共享 + 寫時才複製），所以 fork 快、省記憶體
- exec 是「變身」不是「生小孩」：取代當前程式，成功不返回；exec 家族底層都是 execve
- wait 回收 child 並讀退出狀態（避免 zombie，Ch 14）；shell 的 $? 來自 wait
- 兩步的價值在「中間的窗口」：fork 後 exec 前能調整 child（重導向/管線/背景/降權），這是 shell 所有魔法的底層

## 自我檢核

- [ ] 能解釋 fork「一次呼叫兩次返回」，以及怎麼區分 parent/child
- [ ] 能解釋 copy-on-write 怎麼讓 fork 高效
- [ ] 知道 exec 是「變身」（取代程式，PID 不變，成功不返回）
- [ ] 知道 wait 的作用，以及和 zombie、$? 的關係
- [ ] 能解釋為什麼 fork+exec 兩步比一步式更彈性（中間的調整窗口）

## 延伸閱讀

### 書籍

- **《The Linux Programming Interface》— Ch 24 (Process Creation), Ch 27 (Program Execution)** — Michael Kerrisk
  - **讀哪幾章**：Ch 24（fork、CoW）、Ch 27（exec 家族）
  - **這本書的定位**：fork/exec 的權威來源
  - **前提**：本章

### 論文

- **[A fork() in the road](https://www.microsoft.com/en-us/research/uploads/prod/2019/04/fork-hotos19.pdf)** — Baumann et al., HotOS 2019
  - **核心貢獻**：論證 fork 在現代系統是個有問題的抽象（multithreaded、效能、安全）
  - **讀哪裡**：整篇（短）
  - **和本章的關聯**：本章「進階」段落的 fork 爭議，這是源頭論文

### 部落格 / 文章

- **[fork, exec, wait explained](https://jvns.ca/blog/2016/02/05/whats-up-with-ld-preload/)** 或 Julia Evans 的 process 文章
  - **這篇說什麼**：用實例講 fork/exec/wait
  - **讀哪裡**：fork/exec 相關段落
  - **為什麼值得讀**：把抽象的 syscall 講得具體

→ [Ch 16 ps/top/proc filesystem](./16-ps-proc.md)
