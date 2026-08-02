# 練習 C — 打一個 stateful daemon

## 目標

拿一個含有 intentional bug 的 stateful server（4 狀態機），分別用 naive afl++ 和 stateful 策略對打，親眼看到 naive 方法卡死在 State 0、stateful 方法在幾分鐘內找到 crash。理解為什麼「到達 State 2 才能觸發的 bug」是普通 fuzzer 的死角，以及實務上的兩種繞法。

這題直接對應 Ch 16（stateful fuzzing 核心問題）、Ch 17（AFLNet 延伸路徑）、Ch 19（harness 策略）、Ch 20（crash triage 流程）。

---

## 背景

大多數真實網路服務不是「一個輸入觸發一個行為」，而是有**對話狀態**：login → session → command。傳統 afl++ 的 stdin 噴射模式在這種場景下有根本缺陷——fuzzer 不知道「這個輸入要在哪個狀態下丟進去」，只能從 State 0 開始，而隨機 mutation 幾乎不可能湊出合法的握手序列。

這個練習的目標 daemon 刻意放了一個 **只在 State 2 可觸發的 stack buffer overflow**：

```
State 0 (INIT)   → HELLO <client_id>  → State 1
State 1 (AUTH)   → AUTH <token>       → State 2   ← 必須通過才能繼續
State 2 (CONFIG) → SET <key>=<value>  → BUG HERE  ← target
State 3 (ACTIVE) → GET / QUIT
```

`value` 寫入一個 32-byte fixed buffer 時沒做長度檢查，這是 CVE 級別的 classic 漏洞。

naive fuzzer 幾乎不可能自行發現「先 HELLO → AUTH FUZZING2024 → 再 SET」這條路徑，原因有三：

1. `HELLO` 要求 1–16 個英數字元，隨機 bytes 命中率極低
2. `AUTH` 的 token 是固定字串 `FUZZING2024`，隨機 mutation 機率天文數字
3. 即使勉強通過前兩關，coverage 回饋也不足以引導 fuzzer 繼續往 State 2 前進

這就是 Ch 16 的核心問題場景。

---

## 任務規格

### 目標 server 原始碼

將以下程式碼存成 `stateful_server.c`，不要修改（先確認 bug 存在）：

```c
/* stateful_server.c — intentionally buggy, for fuzzing practice */
#include <stdio.h>
#include <string.h>
#include <ctype.h>
#include <stdlib.h>

#define MAX_KV    8
#define KEY_LEN   32
#define VAL_LEN   32   /* BUG: value input is NOT bounded to this */

typedef struct {
    char key[KEY_LEN];
    char value[VAL_LEN];  /* stack/heap buffer, only 32 bytes */
} KVPair;

static int   state = 0;
static char  client_id[64];
static KVPair store[MAX_KV];
static int   count = 0;

static int is_alnum_str(const char *s, int min, int max) {
    int len = 0;
    for (; *s; s++, len++) {
        if (!isalnum((unsigned char)*s)) return 0;
    }
    return len >= min && len <= max;
}

static void handle_init(const char *line) {
    /* HELLO <client_id>\n */
    if (strncmp(line, "HELLO ", 6) != 0) {
        printf("ERR: expected HELLO\n");
        return;
    }
    const char *id = line + 6;
    /* strip trailing newline */
    char tmp[128];
    strncpy(tmp, id, sizeof(tmp) - 1);
    tmp[sizeof(tmp)-1] = '\0';
    size_t l = strlen(tmp);
    if (l > 0 && tmp[l-1] == '\n') tmp[--l] = '\0';

    if (!is_alnum_str(tmp, 1, 16)) {
        printf("ERR: invalid client_id (alphanumeric, 1-16 chars)\n");
        return;
    }
    strncpy(client_id, tmp, sizeof(client_id) - 1);
    printf("OK: HELLO %s\n", client_id);
    state = 1;
}

static void handle_auth(const char *line) {
    /* AUTH <token>\n */
    if (strncmp(line, "AUTH ", 5) != 0) {
        printf("ERR: expected AUTH\n");
        return;
    }
    char token[128];
    strncpy(token, line + 5, sizeof(token) - 1);
    token[sizeof(token)-1] = '\0';
    size_t l = strlen(token);
    if (l > 0 && token[l-1] == '\n') token[--l] = '\0';

    if (strcmp(token, "FUZZING2024") != 0) {
        printf("ERR: AUTH failed\n");
        return;
    }
    printf("OK: AUTH accepted\n");
    state = 2;
}

static void handle_config(const char *line) {
    /* SET <key>=<value>\n  or  COMMIT\n */
    if (strcmp(line, "COMMIT\n") == 0 || strcmp(line, "COMMIT") == 0) {
        printf("OK: COMMIT, %d key(s) stored\n", count);
        state = 3;
        return;
    }
    if (strncmp(line, "SET ", 4) != 0) {
        printf("ERR: expected SET or COMMIT\n");
        return;
    }
    if (count >= MAX_KV) {
        printf("ERR: store full\n");
        return;
    }
    const char *kv = line + 4;
    const char *eq = strchr(kv, '=');
    if (!eq) {
        printf("ERR: missing '='\n");
        return;
    }

    /* copy key */
    size_t klen = eq - kv;
    if (klen >= KEY_LEN) {
        printf("ERR: key too long\n");
        return;
    }
    memcpy(store[count].key, kv, klen);
    store[count].key[klen] = '\0';

    /* BUG: copy value WITHOUT length check */
    const char *val = eq + 1;
    char tmp_val[256];
    strncpy(tmp_val, val, sizeof(tmp_val) - 1);
    tmp_val[sizeof(tmp_val)-1] = '\0';
    size_t vl = strlen(tmp_val);
    if (vl > 0 && tmp_val[vl-1] == '\n') tmp_val[--vl] = '\0';

    strcpy(store[count].value, tmp_val);  /* <--- STACK BUFFER OVERFLOW */
                                          /* store[count].value is 32 bytes */
                                          /* tmp_val can be up to 255 bytes */
    printf("OK: SET %s = %s\n", store[count].key, store[count].value);
    count++;
}

static void handle_active(const char *line) {
    /* GET <key>\n  or  QUIT\n */
    if (strcmp(line, "QUIT\n") == 0 || strcmp(line, "QUIT") == 0) {
        printf("OK: BYE\n");
        state = 0;
        count = 0;
        memset(store, 0, sizeof(store));
        return;
    }
    if (strncmp(line, "GET ", 4) != 0) {
        printf("ERR: expected GET or QUIT\n");
        return;
    }
    char key[KEY_LEN];
    strncpy(key, line + 4, KEY_LEN - 1);
    key[KEY_LEN-1] = '\0';
    size_t l = strlen(key);
    if (l > 0 && key[l-1] == '\n') key[--l] = '\0';

    for (int i = 0; i < count; i++) {
        if (strcmp(store[i].key, key) == 0) {
            printf("OK: %s = %s\n", key, store[i].value);
            return;
        }
    }
    printf("ERR: key not found\n");
}

int process_line(const char *line) {
    switch (state) {
        case 0: handle_init(line);   break;
        case 1: handle_auth(line);   break;
        case 2: handle_config(line); break;
        case 3: handle_active(line); break;
        default:
            printf("ERR: unknown state\n");
            return -1;
    }
    fflush(stdout);
    return 0;
}

int main(void) {
    char buf[512];
    while (fgets(buf, sizeof(buf), stdin)) {
        if (process_line(buf) < 0) break;
    }
    return 0;
}
```

**重點說明**：
- 從 `stdin` 讀取，不是 TCP socket，直接可以接 afl++ pipe
- `store[count].value` 只有 32 bytes，但 `strcpy` 不檢查長度
- bug 只在 `state == 2` 才能碰到，必須先過 HELLO + AUTH

---

## 期望輸出

完成本練習後，你應該能夠：

1. 編譯 server，手動觸發 ASAN stack-buffer-overflow
2. 跑 naive afl++，觀察到 coverage 卡住（paths 停在個位數，State 2 從沒進去）
3. 跑 stateful fuzzing（seed-based 或 harness），幾分鐘內找到 crash
4. 讀懂 ASAN 的 stack trace，定位到 `strcpy` 那行
5. 寫出 3 行 minimal PoC

---

## 卡住提示

**提示 1：naive fuzzer 為什麼卡住？**

在 afl++ 的 coverage 面板看 `paths found` 數字。如果跑了 5 分鐘還在 5 條以內，代表 fuzzer 從來沒有成功進入 State 1。原因是 HELLO 需要英數字元，afl++ 的初始 seed `HELLO` 後面接隨機 bytes，大機率觸發 `ERR: invalid client_id`，而這條 error path 已經被 fuzzer 覆蓋過了——fuzzer 看不到新的 coverage，不知道應該往「通過驗證」的方向走。

解法：讓 fuzzer 從已通過握手的狀態開始，而不是從 State 0 開始。

**提示 2：seed corpus 的正確做法**

不是 `echo "HELLO" > seeds/01.txt`，而是一個包含完整合法握手序列的 seed：

```
HELLO client1
AUTH FUZZING2024
SET x=AAAA
COMMIT
```

afl++ mutation 在此基礎上變異 `AAAA` 的長度，很快就能碰到 overflow。關鍵：seed 必須讓 fuzzer 能走到 State 2 並產生新的 coverage。

**提示 3：harness 方法比 seed 方法更可靠**

seed 方法依賴 mutation 不去把前面的 `HELLO`/`AUTH` 行破壞掉——但 afl++ 有機率改動 seed 的任何位置。更穩的做法是寫一個 harness，把 `state` 和 `count` 直接設成 State 2 的初始條件，讓 fuzzer 只需要 fuzz `SET <key>=<value>` 這一行。參考 Task 3。

**提示 4：ASAN 報告怎麼解讀**

看到 `stack-buffer-overflow` 時，`WRITE of size N` 的 N 就是你丟進去的 value 長度（減去 null terminator）。`#0 in strcpy` → `#1 in handle_config` 這個 stack trace 直接指向第 76 行的 `strcpy`。如果你看到的是 `heap-buffer-overflow`，代表 `store` 陣列被編譯器放在 heap（加上 `-fstack-protector` 的某些 gcc 版本會這樣），行為一樣。

**提示 5：persistent mode 的陷阱**

用 `__AFL_LOOP()` 時，server 的 global state（`state`、`count`、`store`）在每個 loop iteration 開始時必須手動 reset。如果忘記 reset，第一次 iteration 成功進入 State 3 後，第二次 iteration 從 State 3 開始，完全不會測試 State 2 的 bug。

---

## 實作步驟

### Step 1：編譯並手動確認 bug 存在

```bash
gcc -g -fsanitize=address,undefined stateful_server.c -o stateful_server
```

手動觸發：

```bash
printf "HELLO client1\nAUTH FUZZING2024\nSET x=%s\nCOMMIT\n" \
    "$(python3 -c "print('A'*100, end='')")" | ./stateful_server
```

預期看到 ASAN 爆出：

```
=================================================================
==NNNNN==ERROR: AddressSanitizer: stack-buffer-overflow on address 0x... pc 0x...
WRITE of size 101 at 0x... thread T0
    #0 0x... in __interceptor_strcpy ...
    #1 0x... in handle_config stateful_server.c:76
    #2 0x... in process_line stateful_server.c:92
    #3 0x... in main stateful_server.c:104
```

如果沒看到這個，確認 `-fsanitize=address` 有編進去，並且 `LD_PRELOAD` 環境正常。

### Step 2：跑 naive afl++，觀察失敗

安裝或確認 afl++ 已在 PATH：

```bash
afl-clang-fast -g -fsanitize=address,undefined stateful_server.c \
    -o stateful_server_afl

mkdir -p seeds_naive
echo -n "HELLO" > seeds_naive/01.txt

mkdir -p naive_out
timeout 300 afl-fuzz -i seeds_naive/ -o naive_out/ -- ./stateful_server_afl
```

跑 5 分鐘後按 Ctrl-C，看 afl-fuzz 的統計面板。典型結果：

```
         total execs : 12,543 (42/sec)
        paths found  : 4
       crashes found : 0
       ...
```

`paths found` 停在 4–6，因為只有 `ERR: expected HELLO`、`ERR: invalid client_id`、`OK: HELLO`、`ERR: expected AUTH` 這幾條 coverage。State 2 的 code 從來沒跑到過，bug 當然找不到。

**這就是 naive fuzzing 對 stateful daemon 無效的直接證據。**

### Step 3：Seed-based stateful fuzzing（快速路）

建立包含合法握手序列的 seed：

```bash
mkdir -p seeds_stateful
printf "HELLO client1\nAUTH FUZZING2024\nSET x=AAAA\nCOMMIT\n" \
    > seeds_stateful/01.txt
```

用同一個 binary 跑：

```bash
mkdir -p stateful_out
afl-fuzz -i seeds_stateful/ -o stateful_out/ -- ./stateful_server_afl
```

這次 afl++ 的 mutation 會以合法握手為基礎，變異 `AAAA` 部分，並且因為 `SET x=<longer value>` 能觸發新的 code path（`strcpy` 的執行、ASAN 的 overflow 偵測），coverage 會持續增長。幾分鐘內應該在 `stateful_out/crashes/` 看到第一個 crash。

### Step 4：Harness 方法（更可靠的工程解）

將 `stateful_server.c` 的 `handle_config` 函式抽出，或直接讓 harness 操控 global state：

```c
/* harness_state2.c */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stddef.h>

/* 引用 server 的 internal state 與函式 */
extern int   state;
extern int   count;
extern void  handle_config(const char *line);

/* afl++ persistent mode: 每次 loop 重置 state */
__AFL_FUZZ_INIT();

int main(void) {
    __AFL_INIT();
    unsigned char *buf = __AFL_FUZZ_TESTCASE_BUF;

    while (__AFL_LOOP(10000)) {
        size_t len = __AFL_FUZZ_TESTCASE_LEN;

        /* 重置到 State 2 的初始條件 */
        state = 2;
        count = 0;

        /* 把 fuzz input 包裝成 "SET x=<data>\n" */
        char line[600];
        size_t safe_len = len < 500 ? len : 500;
        memcpy(line, "SET x=", 6);
        memcpy(line + 6, buf, safe_len);
        line[6 + safe_len] = '\n';
        line[6 + safe_len + 1] = '\0';

        handle_config(line);

        /* 或者直接 "COMMIT\n" 讓 state 轉到 3，不影響 crash 偵測 */
    }
    return 0;
}
```

編譯（需要把 `main` rename 或用 `-Wl,--allow-multiple-definition`）：

```bash
# 先把 stateful_server.c 的 main 改名，或用分離編譯
# 最簡單的做法：把 server 的 main() rename 成 server_main()
afl-clang-fast -g -fsanitize=address,undefined \
    -D'main=server_main' stateful_server.c \
    harness_state2.c \
    -o harness_afl

mkdir -p harness_seeds
printf "AAAA" > harness_seeds/01.txt

mkdir -p harness_out
afl-fuzz -i harness_seeds/ -o harness_out/ -- ./harness_afl
```

harness 方法的優點：seed corpus 不需要包含握手序列，mutation 不會意外破壞前置條件，fuzzer 100% 的計算資源都花在 `handle_config` 本體上。

### Step 5：找 crash、triage、寫 PoC

找到 crash 後重放：

```bash
cat stateful_out/crashes/id:000000,* | ./stateful_server
```

或者用 ASAN binary：

```bash
cat stateful_out/crashes/id:000000,* | ./stateful_server_afl
```

讀 ASAN 輸出，確認：
- `stack-buffer-overflow`（或 `heap-buffer-overflow`）
- `WRITE of size N`：N > 32 代表 overflow
- stack trace 第一層 `strcpy`，第二層 `handle_config`

從 crash input 提煉 minimal PoC：

```
HELLO c
AUTH FUZZING2024
SET x=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
```

（40 個 A 已超過 32-byte buffer，31 個 A 是邊界，33 個開始 overflow）

確認 PoC 有效：

```bash
printf "HELLO c\nAUTH FUZZING2024\nSET x=%s\n" \
    "$(python3 -c "print('A'*40, end='')")" | ./stateful_server
```

---

## 完整參考解答

<details>
<summary>展開完整參考解答（含 server 完整源碼、harness、指令、ASAN 預期輸出）</summary>

### 1. 完整 server 源碼（同任務規格，附行號說明）

```c
/* stateful_server.c */
/* 關鍵 bug 在第 76 行：strcpy(store[count].value, tmp_val) */
/* store[count].value 是 32 bytes，tmp_val 最長可達 255 bytes */
/* 完整原始碼見「任務規格」章節，不重複貼 */
```

### 2. Build 指令

```bash
# ASAN build（手動驗證）
gcc -g -O0 -fsanitize=address,undefined stateful_server.c -o stateful_server

# afl++ build（naive fuzzing）
afl-clang-fast -g -O1 -fsanitize=address,undefined stateful_server.c \
    -o stateful_server_afl

# harness build（stateful fuzzing）
afl-clang-fast -g -O1 -fsanitize=address,undefined \
    -D'main=server_main' stateful_server.c \
    harness_state2.c \
    -o harness_afl
```

### 3. Seed corpus

```bash
# Naive（會失敗）
mkdir -p seeds_naive && echo -n "HELLO" > seeds_naive/01.txt

# Stateful seed-based
mkdir -p seeds_stateful
printf "HELLO client1\nAUTH FUZZING2024\nSET x=AAAA\nCOMMIT\n" \
    > seeds_stateful/01.txt

# Harness（只需要短 seed）
mkdir -p harness_seeds && printf "AAAA" > harness_seeds/01.txt
```

### 4. 執行 fuzzing

```bash
# Naive（跑 5 分鐘，確認找不到 crash）
timeout 300 afl-fuzz -i seeds_naive/ -o naive_out/ -- ./stateful_server_afl

# Seed-based stateful
afl-fuzz -i seeds_stateful/ -o stateful_out/ -- ./stateful_server_afl

# Harness（最快找到 crash）
afl-fuzz -i harness_seeds/ -o harness_out/ \
    -m none -- ./harness_afl
```

### 5. 預期 ASAN 輸出

```
OK: HELLO client1
OK: AUTH accepted
=================================================================
==12345==ERROR: AddressSanitizer: stack-buffer-overflow on address 0x7fff5fbff8e0 pc 0x5555555551a0 bp 0x7fff5fbff8b0 sp 0x7fff5fbff020
WRITE of size 41 at 0x7fff5fbff8e0 thread T0
    #0 0x5555555551a0 in __interceptor_strcpy (/path/to/stateful_server+0x...)
    #1 0x555555555320 in handle_config stateful_server.c:76
    #2 0x5555555553f0 in process_line stateful_server.c:92
    #3 0x555555555450 in main stateful_server.c:104

Address 0x7fff5fbff8e0 is located in stack of thread T0 at offset 32 in frame
    #0 0x555555555280 in handle_config stateful_server.c:55

  This frame has 3 object(s):
    [32, 64) 'store' (line 16) <== Memory access at offset 32 overflows this variable
HINT: this may be a false positive if your program uses some custom stack unwind mechanism, swapcontext or vfork
      (longjmp and C++ exceptions *are* supported)
SUMMARY: AddressSanitizer: stack-buffer-overflow stateful_server.c:76 in handle_config
Shadow bytes around the buggy address:
  ...
==12345==ABORTING
```

**本段未實測，為理論預期行為**（ASAN 的精確 offset 和地址視編譯環境而定，但 `stack-buffer-overflow in handle_config stateful_server.c:76` 這行是確定的）

### 6. Minimal PoC

```
HELLO c
AUTH FUZZING2024
SET x=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
```

對應指令：

```bash
printf "HELLO c\nAUTH FUZZING2024\nSET x=%s\n" \
    "$(python3 -c "print('A'*40, end='')")" | ./stateful_server
```

### 7. 為什麼 naive 失敗、stateful 成功（機制解說）

naive fuzzer 拿到 `HELLO` 作為 seed，mutation 可能產生：
- `HELLO !!!@#` → `ERR: invalid client_id`（已覆蓋，不增加 coverage）
- `HELL0 abc` → `ERR: expected HELLO`（已覆蓋）
- `HELLO abc` → `OK: HELLO abc`，但下一行是隨機，`ERR: expected AUTH`（已覆蓋）

fuzzer 看不到新的 coverage，認為這個方向沒有價值，停止往 AUTH/CONFIG 方向探索。coverage-guided fuzzing 的 「coverage」在這裡反而成為障礙：ERR path 的 coverage 被「飽和」，fuzzer 沒有動機繼續嘗試。

stateful seed 給了 fuzzer 一條已知可走的路，mutation 只會改動 `SET x=AAAA` 的 `AAAA` 部分，而修改這部分可以觸發 `strcpy` 的新行為（更長的 write，ASAN 偵測），這是真正的新 coverage，fuzzer 會積極往這個方向走。

harness 方法更徹底：完全繞開 State 0/1，fuzzer 的每一次 iteration 都直接從 State 2 開始，100% 的計算力花在 bug 所在的 `handle_config`。

### 8. 完整 harness_state2.c

```c
/* harness_state2.c — afl++ persistent mode harness for State 2 */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stddef.h>

/* server 的 global state（extern，link 時從 stateful_server.c 取得） */
extern int   state;
extern int   count;
extern char  client_id[64];

/* server 的 handler（extern）*/
extern void handle_config(const char *line);

/* afl++ persistent mode macros */
__AFL_FUZZ_INIT();

int main(void) {
    __AFL_INIT();
    unsigned char *buf = __AFL_FUZZ_TESTCASE_BUF;

    while (__AFL_LOOP(10000)) {
        size_t len = __AFL_FUZZ_TESTCASE_LEN;

        /* 重置 server 到 State 2 的初始條件 */
        state = 2;
        count = 0;
        memset(client_id, 0, sizeof(client_id));
        strncpy(client_id, "harness", 7);

        /* 把 fuzz data 包裝成 SET 命令 */
        if (len == 0) continue;
        size_t safe_len = len < 480 ? len : 480;

        char line[512];
        memcpy(line, "SET k=", 6);
        memcpy(line + 6, buf, safe_len);
        line[6 + safe_len] = '\n';
        line[6 + safe_len + 1] = '\0';

        handle_config(line);
    }
    return 0;
}
```

### 9. 修改 stateful_server.c 以支援 harness 編譯

在 `stateful_server.c` 的 `main` 函式外加上條件編譯：

```c
#ifndef HARNESS_MODE
int main(void) {
    char buf[512];
    while (fgets(buf, sizeof(buf), stdin)) {
        if (process_line(buf) < 0) break;
    }
    return 0;
}
#endif
```

然後 harness 的 build 指令改為：

```bash
afl-clang-fast -g -O1 -fsanitize=address,undefined \
    -DHARNESS_MODE \
    stateful_server.c \
    harness_state2.c \
    -o harness_afl
```

</details>

---

## 測試用例表

| 輸入序列 | 預期 server 回應 | 預期最終狀態 | 備註 |
|----------|-----------------|--------------|------|
| `HELLO abc\nINVALID cmd\n` | `OK: HELLO abc` → `ERR: expected AUTH` | State 1（AUTH 未通過） | 確認 HELLO 只驗格式，不驗後續命令 |
| `HELLO abc\nAUTH wrong\n` | `OK: HELLO abc` → `ERR: AUTH failed` | State 1（AUTH 拒絕，不轉移） | token 不符，state 不變 |
| `HELLO abc\nAUTH FUZZING2024\nSET x=short\nCOMMIT\n` | `OK: HELLO abc` → `OK: AUTH accepted` → `OK: SET x = short` → `OK: COMMIT, 1 key(s) stored` | State 3 | 正常流程，value < 32 bytes |
| `HELLO abc\nAUTH FUZZING2024\nSET x=<100個A>\n` | `OK: HELLO abc` → `OK: AUTH accepted` → ASAN crash | crash（不到 State 3） | 觸發 stack-buffer-overflow |
| `<隨機 bytes，如 \x00\x01\xff>` | `ERR: expected HELLO`（或 `ERR: invalid client_id`）| State 0 | 卡在 INIT，naive fuzzer 的典型場景 |
| `HELLO abc\nAUTH FUZZING2024\nSET k1=v1\n...\nSET k8=v8\nSET k9=v9\n` | 前 8 個 SET OK，第 9 個 `ERR: store full` | State 2（count 上限保護） | 測試 `count >= MAX_KV` 的 guard |
| `HELLO abc\nAUTH FUZZING2024\nSET =nokey\n` | `ERR: missing '='`（key 為空，實際上 `=` 在第一位，klen=0） | State 2（不轉移） | 邊界：key 為空字串 |

---

## 延伸挑戰

**挑戰 1：改成 TCP server，用 AFLNet 打**

把 `main()` 改成 TCP server（`bind`/`listen`/`accept`），監聽 localhost:9999。安裝 AFLNet 後：

```bash
afl-fuzz -i seeds_stateful/ -o aflnet_out/ \
    -N tcp://127.0.0.1/9999 \
    -P CUSTOM \
    -D 10 \
    -- ./stateful_server_tcp
```

AFLNet 會記錄 message sequences，知道「這條路徑需要先 HELLO 再 AUTH」，比 stdin 方式更接近真實網路服務的模糊測試場景。**本段未實測，為理論預期行為**——AFLNet 的 `-P CUSTOM` 需要額外的 protocol definition 設定。

**挑戰 2：用 LibAFL 的 stateful 組件重寫 executor**

LibAFL 有 `StatefulExecutor` trait，可以讓你明確定義「狀態轉移事件」和「當前 state」。寫一個 Rust executor，把 server 的 4 個狀態機對應到 LibAFL 的 state model，讓 fuzzer 在 mutation 時知道「現在在 State 1，只有 AUTH 命令能推進 coverage」。這是比 seed-based 方法更精確的 stateful fuzzing 工程實踐。

**挑戰 3：改成二進位協定，用 StateAFL 打**

把協定從文字改成：

```
byte[0] = 0x01 (HELLO) | 0x02 (AUTH) | 0x03 (SET) | 0x04 (GET) | 0x05 (COMMIT)
byte[1..N] = payload
```

這時 seed-based 方法效果更差（因為改動任何 byte 都可能破壞命令類型）。StateAFL 利用動態 taint analysis 追蹤 protocol field 的影響範圍，生成更精準的 mutation。

**挑戰 4：在 State 3 加 format string vulnerability**

在 `handle_active` 的 GET handler 加：

```c
printf(store[i].value);  /* 改成 printf(format_string)，format string bug */
```

這樣 server 同時有兩個 bug：
1. State 2 的 stack overflow（需要通過 HELLO + AUTH）
2. State 3 的 format string（還需要通過 COMMIT，且 value 必須在 SET 時寫入 format specifier）

設計一個 fuzzer 策略，讓它能同時找到兩個 bug，並輸出兩份獨立的 crash report。

---

## 自我檢核

- [ ] 能夠編譯 server 並用 `printf | ./stateful_server` 手動觸發 ASAN stack-buffer-overflow，看到 `handle_config stateful_server.c:76` 的 stack trace
- [ ] 理解為什麼 naive afl++ 的 paths found 停在個位數：HELLO/AUTH 的 validation 讓 fuzzer 看不到 State 2 的 coverage
- [ ] 跑過 seed-based stateful fuzzing，`stateful_out/crashes/` 目錄下有 crash input 出現
- [ ] 能讀懂 ASAN 報告：`WRITE of size N`（N > 32）、`stack-buffer-overflow`、`strcpy` → `handle_config` 的 call chain
- [ ] 能從 crash input 提煉出 3 行 minimal PoC，並確認 PoC 可獨立重現 crash
- [ ] 理解 seed-based 方法和 harness 方法的根本差異：seed-based 依賴 mutation 不去破壞握手序列；harness 從根本上消除對握手的依賴
- [ ] 能解釋「coverage-guided fuzzing 在 stateful protocol 下的 coverage 飽和問題」——為什麼 ERR path 的 coverage 反而阻止了 fuzzer 繼續探索
- [ ] （選修）跑過 harness 方法，比較 harness 和 seed-based 兩種方式找到 crash 的時間差
