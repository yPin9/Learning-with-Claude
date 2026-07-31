# Ch 20 — LD_PRELOAD 攔截器

> **目標**：親手用 LD_PRELOAD 攔截 library 函式——寫一個共享函式庫，覆蓋（hook）malloc/free/open 等函式，在它們前後插入你的邏輯（記錄/統計/修改）。理解它怎麼運作（利用動態連結的 GOT，Ch 6）、和 ptrace 攔截（Ch 19）的差別。這是「動手三大章」之三，讓你能用最簡單的方式攔截 library 呼叫——寫個 malloc tracker、fault injector、或 API monitor。

> **環境**：Linux，C（共享函式庫）。`gcc -shared -fPIC`。

## 為什麼 LD_PRELOAD 是「最簡單的攔截」？

Ch 19 用 ptrace 攔截 syscall（強大但複雜——要寫 tracer、處理暫存器）。但如果你只想攔截 **library 函式**（malloc/free/open/printf），有個更簡單的方法——**LD_PRELOAD**。它利用動態連結（Ch 6 的 PLT/GOT）——你寫一個共享函式庫，定義同名的函式（如你的 malloc），用 LD_PRELOAD 讓它「先被載入」，於是程式呼叫 malloc 時呼叫到**你的**版本（而非真正的 glibc malloc）。

這讓你不用改程式碼、不用 ptrace，就能攔截任何 library 函式——記錄每次 malloc、統計、注入錯誤、修改行為。它是「動手三大章」的最後一章（mini-strace 用 ptrace 攔截 syscall、ptrace 注入控制 process、LD_PRELOAD 攔截 library）。理解它，你掌握了「攔截 library 呼叫」這個極實用的技術，也完整理解了動態連結（Ch 6）怎麼被利用。

## 先建立直覺:插隊載入

```
LD_PRELOAD = 讓你的 library「插隊」先載入

  正常：程式呼叫 malloc → 動態連結器找到 glibc 的 malloc（Ch 6 PLT/GOT）
        │
  LD_PRELOAD：指定一個 library「優先」載入
    LD_PRELOAD=./mymalloc.so ./prog
    → 載入 prog 時，先載入 mymalloc.so
    → 如果 mymalloc.so 有 malloc，程式的 malloc 就解析到「你的」！
        │
  你的 malloc 裡：
    1. 做你的事（記錄/統計）
    2. 呼叫「真正的」malloc（用 dlsym 找到原本的）
    3. 回傳
        │
  → 程式不知道 malloc 被換了（透明攔截）
    程式呼叫 malloc → 你的 malloc → 真 malloc
    你在中間插入了邏輯
        │
  原理：動態連結器先載入的函式優先（Ch 6 的符號解析）
    LD_PRELOAD 讓你的 library 排在最前面
```

關鍵心智：LD_PRELOAD 讓你的 library「插隊先載入」——程式呼叫 malloc 時，因為你的 library 先載入且有同名的 malloc，就解析到**你的**版本。你的 malloc 做你的事（記錄/統計）+ 呼叫真正的 malloc（用 dlsym 找）+ 回傳。程式不知道 malloc 被換了（透明攔截）。原理是動態連結器「先載入的優先」（Ch 6 的符號解析）。

> LD_PRELOAD 利用 Ch 6 的動態連結（PLT/GOT、符號解析）。如果對動態連結、為什麼能覆蓋函式不熟，回看 [Ch 6](./06-ltrace-and-dynamic-linking.md)。它是 ptrace 攔截（Ch 19）之外的另一種攔截方式。

## 寫一個 malloc tracker

```c
// mymalloc.c — 攔截 malloc/free，記錄每次配置
// 編譯：gcc -shared -fPIC -o mymalloc.so mymalloc.c -ldl
#define _GNU_SOURCE
#include <stdio.h>
#include <dlfcn.h>      // dlsym
#include <stdlib.h>

// 函式指標，指向「真正的」malloc/free
static void* (*real_malloc)(size_t) = NULL;
static void (*real_free)(void*) = NULL;
static long total_allocated = 0;
static int alloc_count = 0;

// 你的 malloc（覆蓋程式的 malloc）
void* malloc(size_t size) {
    // 第一次呼叫時，找到真正的 malloc（用 dlsym）
    if (!real_malloc) {
        real_malloc = dlsym(RTLD_NEXT, "malloc");  // RTLD_NEXT = 找「下一個」malloc（真的）
    }
    void *ptr = real_malloc(size);   // 呼叫真正的 malloc

    // 你的邏輯：記錄
    total_allocated += size;
    alloc_count++;
    fprintf(stderr, "[malloc] size=%zu ptr=%p (total: %ld bytes, %d allocs)\n",
            size, ptr, total_allocated, alloc_count);

    return ptr;
}

void free(void *ptr) {
    if (!real_free) {
        real_free = dlsym(RTLD_NEXT, "free");
    }
    fprintf(stderr, "[free] ptr=%p\n", ptr);
    real_free(ptr);   // 呼叫真正的 free
}
```

```bash
# 編譯成共享函式庫
gcc -shared -fPIC -o mymalloc.so mymalloc.c -ldl

# 用 LD_PRELOAD 攔截任何程式的 malloc
cd ~/obslab
cat > app.c <<'EOF'
#include <stdlib.h>
int main() {
    char *a = malloc(100);
    char *b = malloc(200);
    free(a);
    free(b);
    return 0;
}
EOF
gcc -o app app.c

LD_PRELOAD=./mymalloc.so ./app
# [malloc] size=100 ptr=0x... (total: 100 bytes, 1 allocs)
# [malloc] size=200 ptr=0x... (total: 300 bytes, 2 allocs)
# [free] ptr=0x...
# [free] ptr=0x...
# → 你攔截了 app 的 malloc/free！不用改 app 的程式碼
#   能用於：記錄記憶體配置、統計、找 leak、注入...
```

> **LD_PRELOAD + dlsym(RTLD_NEXT) 讓你攔截任何程式的 library 函式——`RTLD_NEXT` 是找到「真正的」函式的關鍵**。寫 LD_PRELOAD 攔截器的核心：(1) **定義同名函式**（你的 `malloc`）覆蓋程式的；(2) 在你的函式裡，用 **`dlsym(RTLD_NEXT, "malloc")`** 找到「真正的」malloc——**`RTLD_NEXT`** 是關鍵，它的意思是「找**下一個**叫 malloc 的函式」（跳過你自己的，找到 glibc 的真 malloc），這樣你才能呼叫真正的 malloc（否則無限遞迴呼叫你自己）；(3) 做你的邏輯（記錄/統計）+ 呼叫真正的函式 + 回傳。編譯成共享函式庫（`gcc -shared -fPIC ... -ldl`），用 `LD_PRELOAD=./your.so ./prog` 執行——程式的 malloc 就被你的攔截。**威力**：不用改程式碼、不用 ptrace，就能攔截任何 library 函式——記錄記憶體配置、統計分配模式、找 leak（記錄 malloc/free 配對）、注入錯誤（讓某些 malloc 回 NULL 測試錯誤處理）、改變行為。這比 ptrace 簡單得多（不用寫 tracer、處理暫存器），但只能攔截 library 函式（不能攔截 syscall——syscall 不經過 PLT/GOT，要用 ptrace）。`RTLD_NEXT` 的初始化要小心（第一次呼叫時用 dlsym 找，但 dlsym 自己可能呼叫 malloc，要處理這個循環——進階主題）。這個「攔截 library 函式」是極實用的技術——很多工具（記憶體 profiler、API monitor、fault injector）用 LD_PRELOAD 實現。

## LD_PRELOAD 的應用

```c
// 應用 1：fault injection（讓某些 malloc 失敗，測試錯誤處理）
void* malloc(size_t size) {
    if (!real_malloc) real_malloc = dlsym(RTLD_NEXT, "malloc");
    // 每 10 次 malloc 故意失敗一次（測試程式有沒有處理 NULL）
    static int count = 0;
    if (++count % 10 == 0) {
        return NULL;   // 故意失敗！
    }
    return real_malloc(size);
}

// 應用 2：攔截 open（記錄/重導向檔案存取）
int open(const char *path, int flags, ...) {
    static int (*real_open)(const char*, int, ...) = NULL;
    if (!real_open) real_open = dlsym(RTLD_NEXT, "open");
    fprintf(stderr, "[open] %s\n", path);   // 記錄開了哪些檔案
    // 能重導向：if (strcmp(path,"a")==0) path = "b";
    return real_open(path, flags);
}

// 應用 3：攔截時間函式（讓程式「以為」是某個時間，測試時間相關邏輯）
// 應用 4：攔截網路函式（記錄/模擬網路行為）
```

```bash
# 常見的 LD_PRELOAD 工具：
#   記憶體 profiler（記錄所有 malloc/free → 分析記憶體用量）
#   API monitor（記錄程式呼叫哪些 library 函式）
#   fault injection（故意讓函式失敗 → 測試錯誤處理）
#   兼容層/shim（讓舊程式用新 library，攔截轉換）
#   libfaketime（攔截時間函式，讓程式以為是別的時間）
```

> **LD_PRELOAD 的應用——fault injection、API monitor、時間偽造——都是「在 library 函式前後插入邏輯」的變化**。LD_PRELOAD 的應用都基於「攔截 library 函式、插入你的邏輯」：(1) **fault injection**——讓某些 malloc/open 故意失敗（回 NULL/-1），測試程式的錯誤處理（「如果 malloc 失敗會怎樣」——很多程式沒檢查 malloc 回 NULL，注入失敗能抓出這些 bug）；(2) **API monitor**——記錄程式呼叫了哪些 library 函式、參數是什麼（像 ltrace，但你能客製記錄什麼、怎麼統計）；(3) **時間偽造**（libfaketime）——攔截 time/gettimeofday，讓程式「以為」是別的時間（測試時間相關邏輯，如「憑證過期」「跨年」的行為，不用真的等到那個時間）；(4) **重導向**（攔截 open，把對某檔案的存取重導向到另一個）；(5) **兼容層/shim**（讓舊程式用新 library，攔截轉換 API）；(6) **記憶體 profiler**（記錄所有 malloc/free 分析記憶體用量）。這些都是「不改程式碼、透明地改變或觀察 library 行為」——LD_PRELOAD 的核心價值。它的限制：只能攔截**動態連結的 library 函式**（靜態連結的不行，Ch 6——沒有 PLT/GOT 可覆蓋）、不能攔截 syscall（要用 ptrace）、setuid 程式忽略 LD_PRELOAD（安全機制，防止用 LD_PRELOAD 提權）。理解這些應用，你看到 LD_PRELOAD 是個「輕量的攔截框架」——比 ptrace 簡單，對 library 函式攔截足夠強大。很多實用工具（libfaketime、各種記憶體工具、fault injection 框架）用它實現。

## LD_PRELOAD vs ptrace 攔截

```
LD_PRELOAD vs ptrace（兩種攔截的取捨）：

  LD_PRELOAD：
    攔截：library 函式（malloc/open/printf）
    機制：動態連結，覆蓋符號（Ch 6）
    優點：簡單（寫個 .so）、開銷小（直接函式呼叫）
    限制：只能 library 函式、靜態連結無效、syscall 不行
        │
  ptrace（Ch 4/19）：
    攔截：syscall（甚至任意指令）
    機制：tracer 控制 tracee（暫停/讀寫）
    優點：能攔截 syscall、能控制執行、能注入
    限制：複雜（寫 tracer）、開銷大（每次暫停）
        │
  → 攔截 library 函式 → LD_PRELOAD（簡單）
    攔截 syscall / 控制 process → ptrace（強大）
        │
  對照本課的攔截工具：
    strace（ptrace 攔 syscall）、ltrace（攔 library 用斷點）
    你的 mini-strace（ptrace）、LD_PRELOAD（覆蓋符號）
    → 不同的攔截機制，各有適用
```

> **攔截 library 函式用 LD_PRELOAD（簡單），攔截 syscall/控制 process 用 ptrace（強大）——兩種攔截機制各有適用**。本課教了兩種攔截技術，理解它們的取捨讓你選對：**LD_PRELOAD**——攔截 **library 函式**（malloc/open），機制是動態連結覆蓋符號（Ch 6），**簡單**（寫個 .so）、**開銷小**（直接函式呼叫，不暫停），但只能攔 library 函式、對靜態連結無效、不能攔 syscall。**ptrace**（Ch 4/19）——攔截 **syscall**（甚至任意指令），機制是 tracer 控制 tracee，**強大**（能攔 syscall、控制執行、注入），但**複雜**（要寫 tracer）、**開銷大**（每次暫停）。選擇：**攔截 library 函式**（記錄 malloc、fault injection）→ LD_PRELOAD（簡單夠用）；**攔截 syscall 或控制 process**（看 open/read 的 syscall、注入、設斷點）→ ptrace。對照本課的工具：strace（ptrace 攔 syscall）、ltrace（攔 library 用 PLT 斷點，Ch 6）、你的 mini-strace（ptrace，Ch 4）、LD_PRELOAD 攔截器（覆蓋符號，這章）——這些用不同的攔截機制（ptrace、PLT 斷點、符號覆蓋），各有適用場景。理解這個攔截技術的全景，你能根據需求選對工具或自己造一個——這是本課「理解工具底層、能自己造工具」的最高境界。你現在掌握了攔截的兩大機制（ptrace 控制、LD_PRELOAD 覆蓋），能攔截從 syscall 到 library 函式的各層，並理解它們怎麼運作。這完成了「動手三大章」（mini-strace、ptrace 注入、LD_PRELOAD）——你不只會用觀察工具，還能造它們。

## 故意弄壞:用 LD_PRELOAD 抓 malloc 失敗未處理

```bash
cd ~/obslab
# 用 fault injection 抓「沒檢查 malloc 回傳值」的 bug
cat > inject.c <<'EOF'
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdlib.h>
#include <stdio.h>
static void* (*real_malloc)(size_t) = NULL;
void* malloc(size_t size) {
    if (!real_malloc) real_malloc = dlsym(RTLD_NEXT, "malloc");
    static int count = 0;
    if (++count == 3) {            // 第 3 次 malloc 故意失敗
        fprintf(stderr, "[inject] failing malloc #3\n");
        return NULL;
    }
    return real_malloc(size);
}
EOF
gcc -shared -fPIC -o inject.so inject.c -ldl

# 一個「沒檢查 malloc」的程式
cat > nocheck.c <<'EOF'
#include <stdlib.h>
#include <string.h>
int main() {
    for (int i = 0; i < 5; i++) {
        char *p = malloc(100);     // 沒檢查 p 是否 NULL！
        strcpy(p, "data");         // 如果 malloc 失敗，p=NULL → segfault
    }
    return 0;
}
EOF
gcc -g -o nocheck nocheck.c

# 正常跑：沒事（malloc 都成功）
./nocheck    # 正常結束

# 用 fault injection：第 3 次 malloc 失敗 → 抓出「沒檢查」的 bug
LD_PRELOAD=./inject.so ./nocheck
# [inject] failing malloc #3
# Segmentation fault           ← strcpy 到 NULL！（沒檢查 malloc 的 bug 暴露）
# → fault injection 暴露了「沒檢查 malloc 回傳值」的 bug
#   正常跑不會觸發（malloc 很少失敗），但生產環境記憶體不足時會崩潰
#   LD_PRELOAD 主動製造失敗，提前抓出這個隱藏的 bug
```

> **LD_PRELOAD 的 fault injection 暴露「沒檢查 malloc 回傳值」的隱藏 bug——這是「主動製造失敗」抓出錯誤處理缺陷的威力**。這個例子展示 LD_PRELOAD 的實用價值——**fault injection 抓錯誤處理 bug**。`nocheck.c` 有經典 bug：`malloc(100)` 後**沒檢查回傳值是否 NULL**就直接用（`strcpy(p, ...)`）。**正常跑不會出事**（malloc 幾乎總是成功），所以這個 bug 隱藏著——但**生產環境記憶體不足時，malloc 失敗回 NULL，`strcpy` 到 NULL → segfault**（在最糟的時候崩潰）。這種「錯誤處理缺陷」極難用一般測試抓出（因為錯誤情況很少自然發生）。**LD_PRELOAD fault injection** 主動製造失敗——讓第 3 次 malloc 故意回 NULL，立刻暴露「沒檢查」的 bug（segfault）。這是測試**錯誤處理路徑**的強大技術——主動注入各種失敗（malloc 失敗、open 失敗、read 失敗），看程式有沒有正確處理。很多隱藏的 bug 在「錯誤路徑」（正常路徑測試了，錯誤路徑沒測，因為錯誤難觸發）——fault injection 讓你能測試這些。這呼應 Ch 19 的 ptrace fault injection（改 syscall 回傳值）——LD_PRELOAD 是更簡單的方式（攔 library 函式）。這完成了 Part 7 的進階自製工具——你能用 ptrace（注入/控制）和 LD_PRELOAD（覆蓋）造各種觀察和測試工具。這是本課的最高目標——不只用工具，還理解它們、造它們、用它們做進階的事（fault injection、監控、攔截）。

## 動手練習

1. 寫 malloc tracker：編譯 mymalloc.so，用 LD_PRELOAD 攔截一個程式的 malloc/free，看記錄

2. 理解 RTLD_NEXT：理解為什麼要用 dlsym(RTLD_NEXT) 找真正的 malloc（否則無限遞迴）

3. 攔截 open：寫一個攔截 open 的 .so，記錄程式開了哪些檔案

4. fault injection：寫一個讓某些 malloc 失敗的 .so，抓「沒檢查 malloc」的 bug

5. LD_PRELOAD vs ptrace：思考什麼時候用 LD_PRELOAD（library 函式）vs ptrace（syscall/控制）

## 本章重點整理

- LD_PRELOAD 讓你的 library 插隊先載入，覆蓋同名函式（利用動態連結符號解析，Ch 6）——透明攔截 library 函式
- 寫攔截器：定義同名函式 + dlsym(RTLD_NEXT) 找真正的函式 + 你的邏輯 + 呼叫真的；`gcc -shared -fPIC`
- 應用：fault injection（測錯誤處理）、API monitor、時間偽造、重導向、記憶體 profiler
- LD_PRELOAD（簡單，攔 library 函式）vs ptrace（強大，攔 syscall/控制 process）——各有適用
- 限制：只能動態連結的 library 函式、靜態連結無效、syscall 要 ptrace、setuid 程式忽略它

## 自我檢核

- [ ] 理解 LD_PRELOAD 怎麼覆蓋 library 函式（動態連結符號解析）
- [ ] 會寫一個 LD_PRELOAD 攔截器（dlsym RTLD_NEXT + 你的邏輯）
- [ ] 知道 LD_PRELOAD 的應用（fault injection/monitor/時間偽造）
- [ ] 知道 LD_PRELOAD 和 ptrace 攔截的取捨
- [ ] 能用 fault injection 抓「沒檢查回傳值」的 bug

## 延伸閱讀

### 文章

- **[LD_PRELOAD 教學](https://www.baeldung.com/linux/ld_preload-trick-what-is)** — Baeldung
  - **這篇說什麼**：LD_PRELOAD 的原理和攔截範例
  - **讀哪裡**：整篇
  - **為什麼值得讀**：本章 LD_PRELOAD 的入門擴充

- **[Hooking with LD_PRELOAD](https://catonmat.net/simple-ld-preload-tutorial)** — Peteris Krumins
  - **這篇說什麼**：用 LD_PRELOAD 做各種 hook 的範例
  - **為什麼值得讀**：更多攔截應用的範例

### 工具

- **[libfaketime](https://github.com/wolfcw/libfaketime)** — 時間偽造
  - **為什麼值得讀**：LD_PRELOAD 的實用工具範例（攔截時間函式）

### 官方文件

- **[ld.so(8)](https://man7.org/linux/man-pages/man8/ld.so.8.html)** + **[dlsym(3)](https://man7.org/linux/man-pages/man3/dlsym.3.html)**
  - **讀哪裡**：LD_PRELOAD、RTLD_NEXT 的說明
  - **為什麼值得讀**：LD_PRELOAD 和 dlsym 的權威

下一章是 Part 7 的最後——core dump 與 signal，從「崩潰後」分析程式（core dump 是崩潰瞬間的記憶體快照）。理解 signal 怎麼產生 core dump、怎麼用 gdb 分析 core。

→ [Ch 21 core dump 與 signal](./21-coredump-and-signals.md)
