# 練習 C — 從 core 檔還原現場

> 目標：給定一份 core 檔跟 binary，**不看 source**，從 core 推導出 crash 原因、當時資料結構狀態、為什麼會走到這裡。

## 情境

你在 on-call。凌晨 3 點一個 service crash 了。DevOps 丟給你：

- `app`（stripped production binary，但有對應的 `app.debug` 給你）
- `app.core.12345`（kernel 自動生的 core）
- 一段 log 截圖（只有一行：`processing user_id=3117`）

你進辦公室打開電腦。這份練習模擬那個時刻。

## 任務

### 先自己重建這個 core（因為這是練習）

本練習先請你寫出一個會 crash 的 `app.c`、跑一遍產生 core、**然後假裝自己沒看過 source**，從 core 反推全景。

### `app.c`（故意讓你之後忘掉它的細節）

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct User {
    int id;
    char name[32];
    int score;
    struct User *next;
} User;

static User *user_db = NULL;

User *add_user(int id, const char *name, int score) {
    User *u = malloc(sizeof(User));
    u->id = id;
    strncpy(u->name, name, sizeof(u->name) - 1);
    u->name[sizeof(u->name) - 1] = '\0';
    u->score = score;
    u->next = user_db;
    user_db = u;
    return u;
}

User *find_user(int id) {
    User *cur = user_db;
    while (cur != NULL) {
        if (cur->id == id) return cur;
        cur = cur->next;
    }
    return NULL;
}

int process_user(int id) {
    User *u = find_user(id);
    printf("processing user_id=%d\n", id);
    fflush(stdout);
    int bonus = u->score * 2;           // ← u 如果是 NULL 就會爆
    return bonus;
}

int main(void) {
    add_user(1, "Alice", 100);
    add_user(2, "Bob", 150);
    add_user(1042, "Carol", 200);

    int ids[] = {1, 2, 1042, 3117, 9999};
    for (int i = 0; i < 5; i++) {
        process_user(ids[i]);
    }
    return 0;
}
```

編譯、設 ulimit、跑：

```bash
gcc -g -O0 app.c -o app.debug
cp app.debug app
strip app                                       ; strip production 版本
ulimit -c unlimited
sudo sysctl -w kernel.core_pattern=core.%p

./app
# => processing user_id=1
# => processing user_id=2
# => processing user_id=1042
# => processing user_id=3117
# Segmentation fault (core dumped)

ls core.*                                        ; 記下檔名，例如 core.98765
```

把 `app.c` **放到另一個資料夾藏起來**，或者至少告訴自己「假裝不知道 source」。

## 分析步驟

### Step 1：基本資訊

```bash
gdb -q ./app.debug core.98765
```

觀察自動輸出：什麼 signal、停在哪、pid 是多少。

### Step 2：bt 看當時位置

```
(gdb) bt
```

應該會看到類似：

```
#0  0x0000... in process_user (id=3117) at app.c:32
#1  0x0000... in main () at app.c:43
```

**注意 `id=3117`** — 跟 log 訊息吻合。log 說「processing user_id=3117」，然後就沒下文了，bt 顯示 crash 在 `process_user(3117)`。Log + bt 已經讓你鎖定到函式。

### Step 3：看局部狀態

```
(gdb) info locals
u = 0x0
bonus = <optimized out>     ; 或某個垃圾值
```

`u == NULL`。`find_user(3117)` 回 NULL，但 `process_user` 沒 check 就 deref → crash。

### Step 4：確認資料結構一致性

想確認「3117 真的不在 user list 裡」：

```
(gdb) p user_db
$1 = (User *) 0x555555559...

(gdb) p *user_db
$2 = {id = 1042, name = "Carol", '\0' <repeats 27 times>, score = 200, next = 0x...}

(gdb) p *user_db->next
$3 = {id = 2, name = "Bob", '\0' <repeats 29 times>, score = 150, next = 0x...}

(gdb) p *user_db->next->next
$4 = {id = 1, name = "Alice", '\0' <repeats 27 times>, score = 100, next = 0x0}
```

List 裡是 Carol → Bob → Alice。**3117 確實不存在。**

### Step 5：重建「怎麼走到這裡」

```
(gdb) frame 1
#1  0x... in main () at app.c:43
43              process_user(ids[i]);

(gdb) p i
$5 = 3

(gdb) p ids
$6 = {1, 2, 1042, 3117, 9999}

(gdb) p ids[3]
$7 = 3117
```

啊哈 — `main` 有個硬編碼 id 陣列，iterate 呼叫 `process_user`。當 i=3 時 id=3117，找不到 user、NULL deref、crash。

### Step 6：推結論

你的 crash report 寫：

> **Crash cause**：`process_user` 收到 id=3117，呼叫 `find_user` 回傳 NULL，但沒有 NULL check 就 deref `u->score`，NULL deref → SIGSEGV。
>
> **Root cause**：`main` 寫死了一個測試陣列，包含不存在的 id 3117。這個 id 看起來像 placeholder 或 bug — 某人當初貼了測試資料沒清掉。
>
> **Fix**：
> 1. `process_user` 加 NULL check，`if (!u) { log error; return -1; }`。
> 2. 移除 main 裡不合法的測試 id。
> 3. 加單元測試覆蓋「不存在的 user」。

## 延伸挑戰

### 挑戰 A：不看 bt 的情況下找 crash 點

假裝 bt 被破壞（stack overflow 的情境）：

```
(gdb) bt
#0  0x0000 in ?? ()
```

只剩 registers：

```
(gdb) info registers
rip            0x555...
rax            0x0
rdi            0xc2d
...
```

用 `x/i $rip` 看當前指令、用 `x/32gx $rsp` 在 stack 上找 return address、手工 unwind。這是 Ch 21 的技能，但練習一下 raw forensics 的感覺。

### 挑戰 B：multi-thread core

寫個 multi-thread 版 `app.c`，4 個 thread 同時 process user，在共用 list 上沒 lock。跑到 crash、產 core、分析：

- `thread apply all bt` 看所有 thread 的狀態
- 哪個 thread crash？
- 其他 thread 當時在做什麼？
- 有沒有 race condition 的跡象（兩個 thread 同時在改 user_db）？

### 挑戰 C：strip 的 binary 能 debug 多少

用 `app`（strip 過的）開 core：

```bash
gdb -q ./app core.98765
```

觀察：

- bt 有函式名嗎？（可能只有 `??`）
- `info functions` 還看得到什麼？（通常動態 symbol 會保留）
- 怎麼用 `/usr/lib/debug/` 下的 debug-link 恢復 symbol？

提示：`objcopy --only-keep-debug app.debug app.debug-info`，然後 strip 後的 `app` 仍可連結到這份 debug info。實際上許多發行版就這樣 package — `package-debuginfo` 單獨裝。

## 自我檢核

- [ ] 我能從 core 快速取得 crash 位址、signal、呼叫鏈
- [ ] 我能從 `info locals` 推斷「NULL 參數是從哪來的」
- [ ] 我會 traverse linked list / hash table 等 data structure 驗證一致性
- [ ] 我能寫清楚的 crash report，包含 root cause 跟 fix 建議
- [ ] 我知道 strip 過的 binary 仍可配 debug-info 檔來分析
- [ ] 我能分析 multi-thread core 找 race / deadlock 線索

Part 4 結束。你已經有「工具化 GDB」的基本功。Part 5 進階到腳本化：`~/.gdbinit`、command macros、Python API — 把日常 debug 動作自動化。

→ [Ch 14 .gdbinit 與 command script](./14-gdbinit-and-command-scripts.md)
