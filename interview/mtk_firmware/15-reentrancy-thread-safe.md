# Ch 15 — reentrancy 與 thread-safe

> **目標**：搞懂可重入（reentrant）函式是什麼、為什麼 ISR/多工環境需要它、reentrant 與 thread-safe 的差異、以及怎麼寫出安全的函式。這是 Ch 14（ISR）的延伸，韌體並行正確性的核心。

> **環境**：C，嵌入式/多工。前置：Ch 11（記憶體）、Ch 14（ISR）。

## 為什麼考這個

韌體常常「同一段 code 同時被多個執行流跑」——主程式跑到一半被 ISR 打斷、ISR 又呼叫某函式；或 RTOS 多個 task 同時呼叫同一函式。如果這函式用了共享狀態、又沒保護，就會資料損壞。**reentrancy 決定一個函式能不能安全地被「重入」**——這是並行正確性的基礎，面試會問「這函式 reentrant 嗎」。

## 先建立直覺：函式被「重入」

```
   主程式呼叫 func()，執行到一半...
        │ 中斷發生！
        ▼
   ISR 也呼叫 func()  ← func 被「重新進入」（reentered），同一函式有兩個執行流在跑
        │
   ISR 的 func() 跑完，回到主程式的 func() 繼續

   問題：如果 func() 用了「共享的狀態」（全域變數、static 變數），
        ISR 那次呼叫改了它，主程式那次回來就看到被改壞的狀態 → bug！
```

**可重入函式**：能被多個執行流「同時進入」而不出錯——因為它不依賴任何共享/持久狀態。**不可重入函式**：用了共享狀態，重入會壞。

## 可重入函式的條件

一個函式是 reentrant，要滿足（核心）：

```
   1. 不使用 static / 全域變數（或只讀不寫）
      → 所有狀態都在「自己的 stack（區域變數、參數）」，每次呼叫獨立
   2. 不回傳指向 static/全域資料的指標
   3. 不呼叫不可重入的函式
   4. （多工）不依賴未受保護的共享資源
```

核心：**狀態都放自己的 stack（區域變數），不碰共享的東西。** 每次呼叫有自己獨立的 stack frame（Ch 11），互不干擾，所以能安全重入。

## 對比：可重入 vs 不可重入

```c
// 不可重入：用了 static（共享狀態）
int counter_bad(void) {
    static int count = 0;     // 共享！重入會 race
    count++;                  // RMW，重入時可能丟更新
    return count;
}

// 可重入：狀態都在 stack
int add_good(int a, int b) {
    int result = a + b;       // 區域變數，每次呼叫獨立
    return result;
}

// 不可重入：用全域 buffer
char *itoa_bad(int n) {
    static char buf[16];      // 共享 buffer！重入會互相覆蓋
    sprintf(buf, "%d", n);
    return buf;               // 回傳指向 static 的指標 → 危險
}

// 可重入版：呼叫者提供 buffer
char *itoa_good(int n, char *buf) {  // buffer 由呼叫者給，不共享
    sprintf(buf, "%d", n);
    return buf;
}
```

關鍵差異：用 `static`/全域 = 不可重入；狀態全在 stack（區域變數、傳入的參數/buffer）= 可重入。

## 標準函式庫的不可重入陷阱

很多標準函式**不可重入**（用了內部 static 狀態），在 ISR / 多工裡用會出問題：

```
   不可重入（有內部 static 狀態）：
   - strtok()      ← 用 static 記住上次位置
   - asctime(), localtime(), gmtime()  ← 回傳指向 static 的指標
   - malloc/free   ← 操作共享的 heap 結構（要鎖）
   - rand()        ← static 種子狀態
   - printf        ← 內部 buffer/鎖（Ch 14 為何 ISR 不用它）

   可重入版（通常有 _r 後綴或要傳 buffer）：
   - strtok_r()    ← 呼叫者傳狀態
   - localtime_r()
```

面試會問「哪些標準函式不可重入」——strtok、localtime、malloc、printf 是經典答案。它們的共同點：**用了內部 static 狀態或共享資源**。

## reentrant vs thread-safe（容易混，面試愛問）

兩者相關但不同：

```
   reentrant（可重入）：能被「重入」而不錯——更嚴格，不依賴任何共享狀態
                       （連加鎖都不算 reentrant，因為 ISR 重入時鎖會 deadlock）

   thread-safe（執行緒安全）：多執行緒同時呼叫不出錯——可以靠「加鎖」達成
```

關鍵區別：

- **加鎖的函式是 thread-safe，但不一定 reentrant！** 因為如果一個函式拿了鎖、執行到一半被 ISR 打斷、ISR 又呼叫同函式想拿同一個鎖 → **deadlock**（鎖已被自己持有，等不到）。所以「靠鎖達成 thread-safe」在 ISR 場景（重入）會死。
- **reentrant 更強**：不用鎖（不依賴共享狀態），所以 ISR 重入也安全。

```
   情境：主程式呼叫 f()（拿了 mutex），中斷打斷，ISR 也呼叫 f()
   - f() 是 thread-safe（靠 mutex）：ISR 的 f() 想拿 mutex → 主程式還持有 → deadlock！
   - f() 是 reentrant（不用共享狀態）：ISR 的 f() 用自己的 stack → 安全
```

口訣：**reentrant ⊆ thread-safe 的概念上更嚴格——reentrant 一定能安全重入（含 ISR）；thread-safe 可能靠鎖（ISR 重入會 deadlock）。** ISR 裡呼叫的函式必須 reentrant，不能只是 thread-safe。

## 怎麼處理共享狀態（當無法避免）

如果函式非用共享狀態不可（如全域計數器），保護它：

```c
volatile uint32_t shared_counter;

// 方法 1：關中斷（critical section，單核 ISR 場景）
void inc_counter(void) {
    disable_interrupts();      // 進入臨界區
    shared_counter++;          // 受保護的 RMW
    enable_interrupts();       // 離開
}

// 方法 2：atomic 操作（硬體支援）
// __atomic_fetch_add(&shared_counter, 1, __ATOMIC_SEQ_CST);

// 方法 3：mutex（RTOS 多 task 場景，但不能用在 ISR！）
// mutex_lock(&m); shared_counter++; mutex_unlock(&m);
```

選哪個：
- **ISR 與主程式共享** → 關中斷（短）或 atomic（mutex 在 ISR 會 deadlock）。
- **RTOS 多 task 共享** → mutex / semaphore（Ch 18, 22）。
- **單純旗標** → volatile（Ch 3）+ 注意原子性。

## 考古題詳解

### Q1：什麼是可重入函式？條件是什麼？

<details>
<summary>詳解</summary>

可重入函式：能被多個執行流「同時進入/重入」而不出錯。條件：
1. 不用（或只讀）static/全域變數——狀態全在 stack。
2. 不回傳指向 static/全域資料的指標。
3. 不呼叫不可重入的函式。
4. 不依賴未受保護的共享資源。

核心：**狀態都在自己的 stack（區域變數、參數），不碰共享。**

**考點**：reentrant 定義與條件，韌體必考。
</details>

### Q2：舉三個不可重入的標準函式，為什麼？

<details>
<summary>詳解</summary>

- **strtok()**：用 static 變數記住上次切到哪——重入會覆蓋上次狀態。
- **localtime() / asctime()**：回傳指向內部 static buffer 的指標——重入會覆蓋。
- **malloc() / free()**：操作共享的 heap 管理結構（要鎖）——重入（ISR）可能損壞 heap 或 deadlock。
- printf、rand 也是。

共同點：用了內部 static 狀態或共享資源。可重入版多有 `_r` 後綴（strtok_r、localtime_r）。

**考點**：不可重入的標準函式，高頻。
</details>

### Q3：reentrant 和 thread-safe 差在哪？

<details>
<summary>詳解</summary>

- **thread-safe**：多執行緒同時呼叫不出錯——**可以靠加鎖**達成。
- **reentrant**：能被重入不出錯——**不依賴共享狀態**（連鎖都不行）。

關鍵：**加鎖的函式 thread-safe 但不一定 reentrant**——若主程式拿鎖時被 ISR 打斷、ISR 呼叫同函式想拿同鎖 → deadlock。所以 ISR 裡只能呼叫 reentrant 函式（不能是「靠鎖的 thread-safe」函式）。reentrant 是更強的保證。

**考點**：reentrant vs thread-safe，進階必考（答對展現深度）。
</details>

### Q4：ISR 裡能呼叫 malloc 嗎？為什麼？

<details>
<summary>詳解</summary>

**不能（或極不建議）**。malloc 不可重入——它操作共享的 heap 結構、內部可能用鎖。如果主程式正在 malloc（持有 heap 鎖/改 heap 結構）時被中斷、ISR 又 malloc → 可能 deadlock（搶同鎖）或損壞 heap 結構。

ISR 應避免動態配置——需要的記憶體預先配好（靜態配置）。這也呼應 ISR「快進快出、不呼叫不可重入函式」（Ch 14）。

**考點**：ISR 不能 malloc（不可重入），串 Ch 14。
</details>

## 踩雷集錦

1. **以為 thread-safe = reentrant**：加鎖的是 thread-safe 但不 reentrant（ISR 重入會 deadlock）。reentrant 更強。
2. **ISR 裡呼叫不可重入函式**：strtok、malloc、printf、localtime——重入會壞/deadlock。ISR 只呼叫 reentrant 函式。
3. **函式用 static 區域變數還以為安全**：static = 共享狀態 = 不可重入。
4. **回傳指向 static/全域的指標**：多執行流共用同一塊，互相覆蓋（如 itoa 用 static buf）。讓呼叫者傳 buffer。
5. **共享變數只加 volatile 以為夠**：volatile 只保證重讀，不保證原子性（Ch 3）。RMW 仍要關中斷/atomic/鎖。
6. **ISR 與主程式共享用 mutex**：mutex 在 ISR 會 deadlock。ISR 場景用關中斷/atomic。

## 速記

- **可重入**：能安全重入——狀態全在 stack（區域變數/參數），不用 static/全域、不回傳 static 指標、不呼叫不可重入函式。
- 不可重入標準函式：**strtok、localtime/asctime、malloc/free、printf、rand**（用內部 static/共享資源）；可重入版常有 `_r`。
- **reentrant vs thread-safe**：thread-safe 可靠鎖達成；reentrant 不依賴共享狀態（更強）。**加鎖的 thread-safe 在 ISR 重入會 deadlock**——ISR 只能用 reentrant 函式。
- 保護共享狀態：ISR↔主程式用關中斷/atomic；RTOS task 間用 mutex/semaphore（Ch 22）。
- volatile（重讀）≠ 原子性——RMW 仍要保護。

## 自我檢核

- [ ] 什麼是可重入函式？它的核心條件是什麼（提示：stack vs 共享狀態）？
- [ ] 舉三個不可重入的標準函式，說出為什麼。
- [ ] reentrant 和 thread-safe 差在哪？為什麼「加鎖的 thread-safe 函式」在 ISR 重入會 deadlock？
- [ ] ISR 裡能呼叫 malloc/printf 嗎？為什麼？
- [ ] ISR 和主程式共享一個 32-bit 計數器，怎麼安全地讀寫它？

## 延伸閱讀

### 書籍

- **《Making Embedded Systems》** — Elecia White — 並行/中斷相關章
  - **讀哪幾章**：reentrancy、共享資料保護章。
  - **為什麼值得讀**：把 reentrant、共享狀態保護講得很實際。

### 文章

- **[Reentrancy — Wikipedia / embedded.com](https://en.wikipedia.org/wiki/Reentrancy_(computing))**
  - **讀哪裡**：reentrant 定義、與 thread-safe 的區別、例子。
  - **和本章的關聯**：reentrant vs thread-safe 的權威說明。

- **[韌體工程師的0x10個問題 — HackMD](https://hackmd.io/@Chienyu/S1loEqCuo)**
  - **讀哪裡**：reentrancy / ISR 相關段。
  - **和本章的關聯**：嵌入式並行考點。

並行正確性的基礎有了，下一章是韌體跨平台的經典坑——endianness，big/little endian 怎麼測、什麼時候會出事。

→ [Ch 16 endianness](./16-endianness.md)
