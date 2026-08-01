# Ch 37 — data-only attacks：繞過所有 CFI

> **目標**：系統化理解 data-only 攻擊的完整原理與技法譜系——當 CFG + XFG + CET + ACG 把「劫持控制流」和「注入新程式碼」全堵死，攻擊者轉向「只改資料、不動程式碼指標」，用合法的呼叫路徑達成任意行為；弄清楚 DOP（Data-Oriented Programming）的圖靈完備性為何讓它不只是一個 workaround，而是一個完整的計算模型；對照你在 browser_pwn 課程裡做過的 V8 data-only 繞 sandbox，把這套心法遷移到 Windows 語境。

---

走到這章，我們需要面對一個不舒服的事實：

你已經學完了幾乎所有的 Windows 控制流保護機制。CFG 限縮間接呼叫目標，XFG 加上型別簽章，CET shadow stack 讓 ROP 在硬體層失效，ACG 讓注入 shellcode 沒有可執行的落腳點，CIG 擋掉惡意 DLL 載入。如果全部都開，攻擊者的傳統路線——劫持控制流、注入 shellcode——確實被堵得非常徹底。

但「完全防禦」的假設有一個致命的裂縫：**這些緩解全部只保護控制流資料（return address、函式指標、vtable 指標）和程式碼的可執行性。它們不保護業務邏輯資料。**

攻擊者不需要改 `RIP`。他只需要改程式的「想法」——讓程式相信你是管理員、讓程式相信那塊記憶體很小所以不用邊界檢查、讓程式相信那個物件是另一種型別。程式碼流程完全合法，CFI 完全看不到任何異常，但結果完全在攻擊者掌控之中。

這就是 data-only 攻擊。

## 為什麼需要這個？

### CFI 的根本假設，以及它沒保護的東西

控制流完整性（Control-Flow Integrity, CFI）的核心假設是：**攻擊的最終目標是劫持控制流**——讓執行流跳到攻擊者想要的程式碼。所以所有 CFI 機制的保護對象，都是「控制流資料」：

```
CFI 家族保護的資料：
──────────────────────────────────────────────────────────────────

  ✅ CFG/XFG 保護：  函式指標（fp）、vtable 指標、間接呼叫目標
  ✅ CET 保護：      return address（shadow stack 備份）
  ✅ SafeSEH/SEHOP： SEH 鏈的完整性
  ✅ /GS：           stack cookie（保護局部的 return address）

  ❌ CFI 家族完全不保護的：
     - 函式參數（誰說 WinExec 的第一個參數不能被改成 "cmd.exe"）
     - 物件的狀態旗標（isAdmin, length, type_id, capacity...）
     - 資料指標（指向資料的指標，不是指向函式的指標）
     - 業務邏輯變數（帳戶餘額、權限等級、物件大小）
     - Loop 的計數器、條件判斷的結果
```

換一個角度來說：CFI 保護的是「程式要去哪裡執行」，它完全不管「程式執行的時候用什麼資料」。而攻擊者只要能改資料，就能在不改任何程式碼指標的情況下，讓程式做它本來不應該做的事。

### 你在 browser_pwn 做過的事

你在 browser_pwn 課裡做了一件事：用 V8 型別混淆漏洞，把一個 `JSObject` 的 `map` 指標改掉，讓 V8 把一個 `JSArray` 當成另一種型別讀取，因此拿到一個越界讀寫的原語，最後改掉 `ArrayBuffer` 的 backing store 指標，把 JS 的讀寫對應到沙箱外的任意記憶體。

整個過程：
- **沒有**竄改任何函式指標
- **沒有**竄改任何 vtable
- **沒有**修改任何 return address
- **沒有**注入任何可執行程式碼

你改的全部都是資料（物件的 `map`、backing store 指標），然後讓 V8 自己的合法程式碼去執行「讀取 map 說這是什麼型別」→「按型別讀取欄位」→「用那個欄位做 memory access」的邏輯。CFI 完全看不到問題——每一個 indirect call 都是合法的目標。

這就是 data-only 攻擊。你已經做過了。現在我們把它系統化，並且把這套手法遷移到 Windows 語境。

## 先建立直覺

### 「改資料而非改 RIP」的攻擊資料流

傳統控制流劫持的攻擊流：

```
傳統控制流劫持（CFI 看得到的異常）：
────────────────────────────────────────────────────────────────

  漏洞原語（任意寫）
        │
        ▼
  目標：函式指標 / vtable / return address
        │
        ▼ CFG/XFG/CET 在這裡擋截！
        │
  竄改後的指標指向 gadget / shellcode
        │
        ▼
  執行攻擊者的程式碼 ← 控制流被劫持
```

Data-only 攻擊的資料流：

```
Data-only 攻擊（CFI 完全看不到異常）：
────────────────────────────────────────────────────────────────

  漏洞原語（任意寫）
        │
        ▼
  目標：業務資料（參數、旗標、指標、狀態）
        │
        ▼ CFI：沒有任何 indirect call 被竄改，不觸發任何警報
        │
  程式繼續執行「合法」的控制流
        │
        ▼
  在某個合法函式呼叫裡，用了被竄改的業務資料
        │
        ▼
  合法函式執行了攻擊者想要的操作
  （例如：WinExec("cmd.exe", SW_SHOW) 用了被改掉的第一個參數）
```

整個攻擊路徑，從頭到尾，`RIP` 的每一次跳轉都是合法的控制流目標。CFG 滿意，XFG 滿意，CET 滿意——因為控制流本來就沒有被劫持。

### 核心心法

data-only 攻擊的本質是：**利用程式自己的邏輯，讓它做我們想要的事**。攻擊者變成了一個資料的操控者，而程式成了一個精確執行我們劇本的自動機。

## 核心技法一：竄改函式參數

這是最直接的 data-only 技法。攻擊者不改函式指標，只改即將被呼叫的函式的**參數**。

### WinExec 參數竄改

```
典型場景：程式裡有一個「按條件執行某個命令」的邏輯：

  char cmd_buf[256];
  strncpy(cmd_buf, user_controlled_prefix, 32);
  strcat(cmd_buf, "/safe_arg");
  if (is_safe_operation) {
      WinExec(cmd_buf, SW_HIDE);   // 合法的控制流，WinExec 是合法 CFG 目標
  }

攻擊者有任意寫原語 → 在 WinExec 被呼叫之前，把 cmd_buf 的內容改掉：
  [cmd_buf] = "cmd.exe /c calc.exe"

結果：WinExec("cmd.exe /c calc.exe", SW_HIDE) 被用合法的控制流呼叫
CFG 觀察到：WinExec 是一個合法的 CFG 目標 → 沒有問題
```

這個例子很小，但原理可以推廣到任何函式呼叫的參數。關鍵在於找到程式流程中「一個合法的函式呼叫，但它的參數是從我們能控制的記憶體位置讀取的」。

### VirtualProtect 參數竄改（更精妙）

如果程式本身在某個場景下會呼叫 `VirtualProtect`（比如 JIT compiler 的出口路徑），攻擊者可以：

```
目標：程式裡有一段合法的 VirtualProtect 呼叫路徑

正常流程：
  VirtualProtect(code_buf, 0x1000, PAGE_EXECUTE_READ, &old_prot)
  → 把 JIT 生成的程式碼改成 RX（合法的 JIT 操作）

Data-only 攻擊：
  1. 在 VirtualProtect 呼叫之前，把 code_buf 的值改成攻擊者控制的位址
  2. 把 protection 的值改成 PAGE_EXECUTE_READWRITE (0x40)

結果：VirtualProtect([攻擊者的位址], [大], PAGE_EXECUTE_READWRITE, &old)
→ 攻擊者在 ACG 之外（如果這個 process 沒有 ACG）拿到了 RWX 頁面

注意：如果目標 process 有 ACG，這個 VirtualProtect 在 kernel 仍然會被擋住
→ 但它說明了「竄改參數」這個思路的威力
```

### stack 上的參數竄改

在 x64 Windows 呼叫慣例（Microsoft x64 ABI）裡，前四個參數放在 `RCX, RDX, R8, R9`，但大型結構和第五個以後的參數放在 stack 的 home space 區域。如果攻擊者有 stack 的任意寫原語，在函式呼叫前竄改 stack 上的參數，可以在不改任何指標的情況下控制函式行為。

```
x64 stack 佈局（呼叫者在呼叫 WinExec 前）：

  RSP + 0x00: return address（← CET shadow stack 保護這個）
  RSP + 0x08: home space for RCX（第一個參數，lpCmdLine）
  RSP + 0x10: home space for RDX（第二個參數，uCmdShow）

  攻擊者：寫 RSP + 0x08 → 讓 WinExec 看到的 lpCmdLine 是攻擊者的字串
  CET 保護：return address（RSP + 0x00）← 這個沒被動，CET 沒有觸發
  結果：WinExec 用了竄改的 lpCmdLine
```

## 核心技法二：竄改權限與狀態旗標

許多安全決策不在控制流裡，而在資料欄位裡。

### 物件的 isAdmin / isPrivileged 欄位

```c
// 假想目標程式的物件結構
struct UserContext {
    char username[64];
    int  privilege_level;   // 0 = guest, 1 = user, 2 = admin
    HANDLE token;
    // ...
};

void execute_privileged_operation(UserContext* ctx) {
    if (ctx->privilege_level >= 2) {  // 這個判斷讀的是資料，CFI 看不到
        // 執行管理員操作
        do_admin_thing();
    }
}
```

攻擊者有任意寫原語，把 `ctx->privilege_level` 從 0 改成 2。

`execute_privileged_operation` 的控制流完全合法：它就是正常地讀這個欄位、做判斷、呼叫 `do_admin_thing()`。CFG 看到的 indirect call（如果 `do_admin_thing` 是透過函式指標呼叫）是合法的 CFG 目標。沒有任何 CFI 訊號。

### 長度欄位竄改（引發二階越界存取）

```c
struct Buffer {
    uint8_t* data;    // 資料指標
    size_t   length;  // 長度（程式自己會用這個做邊界檢查）
    size_t   capacity;
};

size_t Buffer_read(Buffer* buf, size_t offset, void* out, size_t count) {
    if (offset + count > buf->length) return 0;  // 邊界檢查
    memcpy(out, buf->data + offset, count);
    return count;
}
```

攻擊者把 `buf->length` 從 64 改成 `0xFFFFFFFF`：
- `Buffer_read` 的邊界檢查永遠通過（`offset + count` 幾乎不可能超過 4GB）
- `memcpy` 讀取 `buf->data + offset` 的任意位置
- 合法的控制流，合法的函式呼叫，CFI 沒有任何警報
- 但攻擊者現在有了任意讀原語

這是一個典型的「用 data-only 技法把一個受限原語（固定長度的有限讀）升格成更強的原語（任意讀）」的案例。

### 型別欄位竄改（Type Confusion via Data-Only）

這正是你在 browser_pwn 課做過的。

```
V8 JSObject 的 map 指標（隱藏類別指標）決定了 V8 如何解釋這個物件的欄位佈局。
把 JSArray 的 map 改成 JSFloat64Array 的 map：
→ V8 按 Float64Array 的欄位偏移解讀 JSArray 的內容
→ JSArray 的長度欄位（32-bit int）被解讀為 Float64 值的元素個數（擴大了範圍）
→ 取得越界讀寫原語

整個過程：改的是 map 指標（一個資料指標，不是函式指標）
CFG：V8 讀取 map 是合法的記憶體存取，呼叫 map 上的方法是合法的 virtual dispatch
CET：沒有 return address 被竄改
結論：CFI 系統沒有任何警報，但攻擊者已經有了越界原語
```

在 Windows 的 C++ 應用程式語境，`type_id` 欄位或 COM 物件的 `IUnknown*` 背後的型別假設，都可以是同樣的攻擊目標。

## 核心技法三：竄改資料指標而非函式指標

CFI 保護函式指標；但它對「指向資料的指標」無能為力。

### 資料指標升格為能力

```
目標物件：
  struct MemoryPool {
      void*  backing;    // 指向後端緩衝區（資料指標）
      size_t size;
      // 操作 backing 的方法呼叫合法的 vtable 方法
  };

正常情況：backing 指向一塊合法的堆塊
攻擊者把 backing 改成任意位址（比如 ntdll 的 .data section）

當程式呼叫 pool.write(offset, data)：
  vtable dispatch → MemoryPool::write（合法 CFG 目標）
  write 實作：memcpy(this->backing + offset, data, len)
  結果：往 ntdll .data section 的任意位置寫入
  CFI 看到：合法的 vtable dispatch，合法的 write 方法
```

這個模式的威力在於：資料指標提供了「間接定址能力」，而程式自己的邏輯提供了「操作能力」。攻擊者只需要找一個「資料指標 + 操作它的合法程式碼」的組合，就能組裝出任意讀寫原語。

### 雙重 data-only：從讀寫原語到執行

即使有了任意讀寫原語，在 ACG+CIG 全開的情況下，攻擊者還需要把「任意讀寫」轉換成「有意義的行為」（通常是：啟動 shell、讀取敏感資料、竄改另一個 process 的狀態）。

```
data-only 的「執行」路徑（在 ACG 環境下）：

方法一：竄改另一個 process 的記憶體（如果有 WriteProcessMemory 的機會）
  → 目標 process 沒有 ACG → 在那裡注入 shellcode（ACG 是 per-process 設定）
  
方法二：竄改 IPC/shared memory 的內容影響另一個 process 的決策
  → 找到一個 non-ACG process 透過 IPC 做敏感操作，污染它的輸入
  
方法三：竄改 token/privilege 相關的使用者態資料結構
  → 影響後續的 Windows API 呼叫的安全性判斷
  
方法四：竄改網路/磁碟的輸出資料
  → data exfiltration 不需要執行任何程式碼
```

## 核心技法四：Confused Deputy 呼叫鏈

「Confused deputy」原本是指一個擁有某些特權的 process，被欺騙替攻擊者做它本來不該做的事。在 data-only 語境裡，我們找的是程式裡的「合法特權操作的呼叫路徑」，然後透過竄改資料讓程式走到那條路徑。

### 找「自帶炮火」的功能

```
典型的 confused deputy 場景：

目標程式裡有一個「管理員維護功能」：
  void admin_execute_command(const char* trusted_cmd) {
      // 這個函式只應該被管理員介面觸發
      WinExec(trusted_cmd, SW_SHOW);
  }

程式的「一般使用者路徑」從來不會呼叫這個函式。
但如果有一個物件的 vtable dispatch 或函式指標可以指向這個函式：

data-only 版本：
  不改函式指標，改流程判斷的旗標：
  
  if (user_ctx.is_admin) {   // ← 改這個旗標
      admin_execute_command(get_pending_command());   // 合法呼叫路徑
  }
  
  + 把 get_pending_command() 的緩衝區改成 "cmd.exe"
  
  兩個 data 修改：is_admin 旗標 + 命令緩衝區
  控制流：完全合法，每一個 call 都是合法的 CFG 目標
```

## DOP：Data-Oriented Programming

2016 年，Hu et al.（Shengzhi Hu, Zhihao Liang, et al.）在 IEEE S&P 2016 發表了「Data-Oriented Programming: On the Expressiveness of Non-Control Data Attacks」，正式建立了 DOP 作為 ROP 的 data 對應物的理論框架。

### DOP 的圖靈完備性

論文的核心結論是：**Data-Oriented Programming 是圖靈完備的**——在 data-only 限制下（不改任何控制流資料），攻擊者可以模擬任意計算。

DOP gadget 的定義（對照 ROP gadget）：

```
ROP gadget：  一小段機器碼，最後以 ret 結尾
DOP gadget：  程式裡的一段邏輯，它讀取某個資料、對它做操作、把結果寫回某個地方
              而這段邏輯是在合法的控制流裡執行的
```

三類基本 DOP gadget：

```
1. Assignment gadget（賦值）
   *p = *q;   // 把 q 指向的值，透過 p 的間接定址寫到目標
   效果：memory write

2. Arithmetic gadget（運算）
   *p = *q + *r;  // 讀兩個值，做加法，寫結果
   效果：ALU 操作

3. Dereference gadget（間接存取）
   *p = **q;   // 透過兩層解參考讀取
   效果：pointer dereference，可以用來遍歷資料結構
```

只要程式裡存在足夠多的這三類 gadget（在任何非平凡的程式裡幾乎都存在），攻擊者可以用 data 竄改把這些 gadget「串接」起來，執行任意計算。

論文用 `wu-ftpd` 為靶，展示了一個完全 data-only 的利用，實現了任意讀寫和任意函式呼叫的效果——沒有改任何控制流資料。

> **論文引用**：Shengzhi Hu, Zhihao Liang, Purui Su, et al. "Data-Oriented Programming: On the Expressiveness of Non-Control Data Attacks." *IEEE Symposium on Security and Privacy (S&P)*, 2016. — 這是整個 DOP 理論框架的原始出處，值得讀完整論文。

### DOP gadget 鏈的結構

```
DOP 攻擊的執行流（對比 ROP chain）：

ROP chain 的概念：
  [gadget1_addr][gadget2_addr][gadget3_addr]...
  每個 ret 跳到下一個 gadget 的位址

DOP gadget chain 的概念（資料空間裡的「鏈」）：
  攻擊者控制的資料區域：
  [dispatch_selector_1][data_for_gadget_1][dispatch_selector_2][data_for_gadget_2]...
  
  程式裡有一個「loop with dispatch」結構：
  while (has_more_work) {
      op = *dispatch_ptr;       // 讀 selector（資料，不是程式碼指標）
      switch (op) {             // 根據 selector 選擇操作
          case OP_COPY:  *dst = *src;  break;   // DOP assignment gadget
          case OP_ADD:   *dst = *a + *b; break; // DOP arithmetic gadget
          // ...
      }
      advance_dispatch_ptr();   // 移動到下一個 selector
  }
  
  攻擊者把 dispatch_ptr 指向自己的 DOP chain，控制整個計算序列
  CFI 看到：每一個 switch-case 的跳轉都是合法的 CFG 目標
            沒有任何間接呼叫被竄改
```

### 為什麼 DOP 難防

```
傳統緩解 vs DOP：
───────────────────────────────────────────────────────────────────────

 緩解措施          | 防 ROP | 防 JIT spray | 防 shellcode | 防 DOP
───────────────────|--------|-------------|--------------|--------
 DEP/NX            |   ✅   |    ✅        |    ✅        |  ❌
 ASLR              |   部分 |    部分      |    部分      |  部分
 CFG               |   部分 |    ✅        |    ✅        |  ❌
 XFG               |   部分 |    ✅        |    ✅        |  ❌
 CET               |   ✅   |    N/A       |    N/A       |  ❌
 ACG               |   N/A  |    ✅        |    ✅        |  ❌
 CIG               |   N/A  |    部分      |    ✅        |  ❌

 DOP 的防禦難點：
 - DOP gadget 不需要任何新的程式碼 → DEP/ACG/CIG 無效
 - DOP 透過合法的控制流執行 → CFG/XFG/CET 無效
 - DOP 沒有固定的 gadget 結構 → 難以靜態偵測
 - DOP 計算發生在資料空間 → 即使監控 RIP 的每一步也看不出異常
```

現有的防禦機制裡，對 DOP 最有效的是：
1. **Data-flow integrity（DFI）**：追蹤程式的資料流，確保每個資料操作使用的資料來自合法的定義點。這是學術研究方向，尚未在生產系統廣泛部署，因為效能成本很高（10x–100x 的 overhead）。
2. **Sandboxing 隔離**：即使 DOP 成功，如果 process 是沙箱化的（low integrity、job object 限制、系統呼叫過濾），它能做的傷害有限。Edge renderer sandbox 的整個設計都是基於這個思路——就算攻擊者在 renderer 裡完成了 DOP 攻擊，他還需要沙箱逃逸才能接觸到系統。

## 對照 browser_pwn：Windows 語境的系統化

你在 browser_pwn 課程裡學的一套，和本章對照如下：

```
browser_pwn 的 data-only 技法  ↔  Windows userland 語境

型別混淆（V8 map 竄改）
  ↔ COM 物件型別欄位竄改 / C++ 型別欄位竄改

ArrayBuffer backing store 竄改（資料指標）
  ↔ MemoryPool backing 指標竄改 / struct 裡的資料指標

JSObject 屬性直接修改（函式長度等）
  ↔ Buffer.length 欄位竄改 / UserContext.privilege_level

V8 WASM memory 的越界讀寫（沙箱內）
  ↔ Windows low-IL process 的越界讀寫（沙箱內）

逃出 V8 沙箱的 data-only 路線（改 Isolate 結構的外部指標）
  ↔ 竄改 IPC 通道的共享記憶體影響 non-sandboxed process

renderer → browser 的 IPC 訊息污染
  ↔ Edge renderer → JIT process 的 OOP-JIT IPC 污染
```

核心差異：

1. **粒度不同**：V8 的物件模型是 GC 管理的，物件佈局由 map（隱藏類別）控制，型別混淆是最常見的向量。Windows C++ 物件的佈局更靜態，但 COM 介面的 vtable 型別假設和 Windows Runtime 的型別系統也有類似的攻擊面。

2. **目標環境不同**：V8 data-only 的最終目標通常是逃出 V8 沙箱拿到沙箱外的讀寫；Windows data-only 的最終目標可能是提升 IL（integrity level）、影響 privileged IPC 通道、或在 non-ACG 的 process 裡注入 shellcode（ACG 是 per-process 設定）。

3. **型別系統不同**：JS 的動態型別讓型別混淆更容易；C++ 的靜態型別讓「找到型別混淆點」需要更仔細的逆向分析，但一旦找到（UAF 重用、union 的類型 punning），同樣致命。

## 實際案例研究

### CVE-2018-8373：IE 的 data-only 資訊洩漏

Internet Explorer 的 `VBScript` 引擎有一個型別混淆漏洞，把一個 `VBScript` 物件的型別欄位（一個資料欄位）改成另一種型別，讓引擎把原本是指標的欄位讀成整數輸出，實現了地址洩漏（ASLR bypass）。

整個漏洞的利用：沒有任何控制流竄改，只改了一個型別欄位，讓 IE 的合法程式碼把位址當成 VBScript 整數值讀出來。

### Edge renderer 的 OOP-JIT IPC 攻擊面（理論）

out-of-process JIT 架構（Ch 36）把 JIT 移到另一個 process，但 renderer 需要把 IR 送給 JIT process，JIT process 把生成的機器碼映射回 renderer。這個 IPC 通道如果有剖析漏洞：

```
data-only 攻擊 OOP-JIT：

  renderer（ACG 啟用）：
    攻擊者有任意寫原語 → 污染 IPC shared buffer 的內容
    → 讓 JIT process 誤解 IR 的結構（data-only 在 renderer 端）

  JIT process（ACG 關閉）：
    處理被污染的 IR → 生成「包含攻擊者想要機器碼」的 JIT 輸出
    → 把這個惡意機器碼映射到 renderer 的 RX 頁面

  renderer：
    執行了 JIT 生成的惡意機器碼（這個 RX 頁面是合法映射，ACG 允許）
    → 實現了任意程式碼執行
```

這個攻擊鏈的第一步（污染 IPC buffer）是 data-only；第二步依賴 JIT process 的行為；第三步是在一個 ACG 允許的 RX 頁面執行程式碼（因為那個 RX 頁面是 JIT process 合法建立並映射回來的）。這說明：ACG 的強度取決於整個系統的設計，不只是單一 process 的設定。

## 對比與取捨

| 面向 | 傳統控制流劫持 | Data-only / DOP | 差異解讀 |
|---|---|---|---|
| **CFI 防護效果** | 大幅壓制（CFG+XFG+CET） | 無效（控制流合法） | CFI 的根本盲點 |
| **ACG/CIG 防護效果** | 完全封死（無法注入） | 無效（不需要新程式碼） | data-only 的核心優勢 |
| **偵測難度** | 相對容易（control-flow anomaly） | 極難（行為合法） | 防禦側最大挑戰 |
| **漏洞利用複雜度** | 中等（找 gadget、繞 CFI） | 高（需要深度程式分析） | 攻擊者成本也更高 |
| **圖靈完備性** | 是（ROP 已證明） | 是（DOP 已證明） | 兩者都是完整計算模型 |
| **現有防禦** | CFG + XFG + CET | DFI（未廣泛部署）、Sandboxing | data-only 的防禦薄弱 |
| **在沙箱環境的效果** | 受沙箱限制（low IL） | 受沙箱限制（但更難偵測） | 沙箱仍是有效的第二道防線 |
| **與 browser pwn 的關聯** | 傳統的 type confusion → control flow | V8 map 竄改型別混淆 = data-only | 讀者已有第一手經驗 |

## 踩雷集錦

1. **「data-only 就是改資料然後呼叫一個函式——很簡單嘛」**：這個概念在原理上是直接的，但實際利用的難點在於：你需要找到程式裡「正確的資料目標 + 正確的使用時機 + 正確的呼叫路徑」的三向組合。任何一個複雜的真實程式都需要仔細的靜態和動態分析才能找到這個組合。它在理論上簡單，在實踐上要求深刻的程式理解。

2. **「我有任意寫了，直接改 token pointer 就好了吧」**：Token 相關的結構在 kernel 物件裡，userland 的任意寫原語碰不到 kernel 空間（除非你先做了 kernel exploit）。Userland 的 data-only 目標必須在 userland 的位址空間裡。常見的錯誤直覺是把 kernel exploit 的技法直接套到 userland。

3. **「DOP gadget 鏈很好找，程式裡到處都是 *p = *q 這種操作」**：DOP gadget 的確廣泛存在，但要找到一個可以被攻擊者「控制 dispatch 的 gadget 鏈」，需要程式裡有一個「以資料控制操作序列」的 dispatch 迴圈（比如 VM bytecode 解釋器、狀態機）。並非所有程式都有這樣的結構。論文作者在 `wu-ftpd` 裡能做到，是因為那個程式有一個類似解釋器的命令分派機制。

4. **「data-only 繞過了 CFI，所以 CFI 沒有意義」**：錯。CFI（CFG + XFG + CET）對於壓制傳統的 ROP 和控制流劫持是有真實效果的，它確實讓攻擊的入門門檻大幅提高。data-only 攻擊的存在說明的是「CFI 不是全集」，而不是「CFI 無用」。正確的理解是：現代 Windows 攻擊需要 CFI + sandboxing + DFI（未來）的組合，任何單一緩解都不是銀彈。

5. **「browser_pwn 裡學的 data-only 是 V8 專屬的」**：型別混淆、資料指標竄改、長度欄位竄改——這些技法的原理完全語言/執行期無關。V8 只是一個具體的靶，讓你學會了「改物件的型別描述，讓 runtime 用錯誤的視角解釋記憶體」這個普遍思路。在 Windows 的 COM 物件、C++ 的虛擬繼承、Windows Runtime 的型別系統，甚至是一般的 C struct，同樣的思路都適用。

## 進階：再往深一層

### 實作一個最小的 DOP 示範

理解 DOP 最好的方式是自己寫一個「可被 DOP 利用的程式」，然後設計 gadget chain。

```c
// 未實測，理論預期
// 一個帶有 dispatch loop 的「虛擬機」——這是最容易被 DOP 利用的程式模式
#include <windows.h>
#include <stdint.h>

// DOP gadget 的 opcode 定義
#define OP_MOV   0  // *dst = *src
#define OP_ADD   1  // *dst = *a + *b
#define OP_LOAD  2  // *dst = **src_ptr
#define OP_STORE 3  // **dst_ptr = *src
#define OP_END   0xFF

struct DopInstruction {
    uint8_t  opcode;
    uint64_t *dst;
    uint64_t *src_a;
    uint64_t *src_b;
};

// 這個 dispatch loop 就是 DOP 的 gadget 串接機制
// 攻擊者只需要控制 instrs 陣列的內容，就能控制整個計算
void dop_vm_execute(struct DopInstruction *instrs, size_t count) {
    for (size_t i = 0; i < count; i++) {
        switch (instrs[i].opcode) {
            case OP_MOV:   *instrs[i].dst = *instrs[i].src_a; break;
            case OP_ADD:   *instrs[i].dst = *instrs[i].src_a + *instrs[i].src_b; break;
            case OP_LOAD:  *instrs[i].dst = **(uint64_t**)(instrs[i].src_a); break;
            case OP_STORE: **(uint64_t**)(instrs[i].dst) = *instrs[i].src_a; break;
            case OP_END:   return;
        }
    }
}
```

> **未實測，理論預期**：如果 `instrs` 陣列能被攻擊者控制（例如透過 buffer overflow 或 UAF），攻擊者可以填入一串 `DopInstruction`，讓 `dop_vm_execute` 執行任意的記憶體讀寫序列——沒有改任何函式指標，CFI 不觸發任何警報。這個 pattern 在真實的 bytecode VM（比如 IE 的 script engine、一些 application-level VM）裡廣泛存在。

### DFI（Data-Flow Integrity）：data-only 的理論防禦

2006 年，Castro et al. 提出了 DFI：追蹤每個資料存取操作，確保資料的「reaching definition」符合靜態分析時計算的合法 definition 集合。如果一個 `*p = *q` 操作，`*q` 的值不是來自任何靜態分析允許的定義點（比如它被攻擊者改掉了），DFI 就能偵測到。

現實問題：DFI 在實用系統上的 overhead 在 100%–1000% 之間，目前沒有在生產系統廣泛部署。但這是學術界對 data-only 攻擊的最直接理論回應。

### Windows 系統 DLL 裡的 data-only 攻擊面

Windows 的 NT Heap 管理器（`ntdll.dll`）本身有大量的「讀 metadata → 做操作」的 pattern。如果攻擊者能控制 heap chunk 的 metadata（`FreeEntryOffset`、`SubSegmentCode`、`SizeIndex` 等欄位），可以讓 heap 管理器的合法程式碼進行任意記憶體寫入——這正是 Ch 17（heap metadata encoding）的核心攻擊面。Heap 利用和 DOP 在本質上是同一件事：**透過竄改 data 讓已知的合法程式碼做壞事**。

## 動手練習

設計一個最小的 data-only 利用，不使用任何 Windows 特定 API，用純 C 實現。

```c
// 未實測，理論預期
// 靶程式：一個帶有「管理員功能」的系統，但有 UAF 漏洞

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

// 用戶上下文物件
typedef struct {
    int  privilege;     // 0 = guest, 1 = admin
    char name[56];      // 名稱（讓整個結構剛好 64 bytes，對齊 heap chunk）
} UserCtx;

// 「敏感操作」只允許管理員
void do_admin_action(UserCtx* ctx) {
    if (ctx->privilege == 1) {
        printf("[ADMIN] Executing privileged command\n");
        // 假想這裡做了敏感操作
        WinExec("calc.exe", SW_SHOW);
    } else {
        printf("[DENIED] You are not an admin\n");
    }
}

int main(void) {
    // 步驟一：建立 guest 使用者
    UserCtx* user = (UserCtx*)malloc(sizeof(UserCtx));
    user->privilege = 0;
    strncpy(user->name, "alice", sizeof(user->name));

    printf("Normal user: privilege = %d\n", user->privilege);
    do_admin_action(user);   // 應該被拒絕

    // 步驟二：釋放（UAF 模擬攻擊者建立的懸空指標）
    free(user);
    // user 指標現在是 dangling pointer

    // 步驟三：用另一個同樣大小的分配重用這個 chunk
    // 攻擊者控制這個新分配的內容
    char* attacker_data = (char*)malloc(sizeof(UserCtx));
    // 攻擊者把 privilege 欄位的位置（偏移 0）設成 1
    memset(attacker_data, 0, sizeof(UserCtx));
    *(int*)attacker_data = 1;   // 把 privilege 改成 1（data-only！）
    strncpy(attacker_data + 4, "attacker_overlay", 16);

    // 步驟四：透過 dangling pointer 使用原本的物件
    // user 指向的記憶體已被重用為 attacker_data
    printf("After UAF: privilege = %d\n", user->privilege);
    do_admin_action(user);   // 這次應該成功——data-only 利用

    free(attacker_data);
    return 0;
}
```

練習目標：
1. 編譯並執行（mingw gcc，不需要 MSVC），確認 UAF 重用讓 `do_admin_action` 的行為被改變。
2. 分析：這個利用有沒有改任何函式指標？有沒有改任何 return address？CFG 看得到任何異常嗎？
3. 延伸：如果 `do_admin_action` 裡的 `WinExec` 呼叫改成透過函式指標，CFG 會保護它嗎？為什麼？

> **注意**：現代 Windows heap（Segment Heap 或 LFH）的分配策略會影響 UAF 重用的可預測性，這個範例在無防護的 debug heap 下行為最可預測。生產環境的 heap grooming 需要 Ch 28/29 的技法配合。此程式碼未實跑，為理論預期。

## 本章重點整理

- **Data-only 攻擊的核心**：不改控制流資料，只改業務資料（參數、旗標、資料指標、型別欄位），讓程式的合法程式碼做攻擊者想要的事。CFI 家族（CFG + XFG + CET + ACG + CIG）對此完全無效。
- **四條技法路線**：（1）竄改函式參數；（2）竄改權限/狀態旗標；（3）竄改資料指標；（4）利用 confused deputy 呼叫鏈。這四條路線可以組合。
- **DOP 的圖靈完備性**（Hu et al., S&P 2016）：data-only 攻擊在理論上能模擬任意計算，和 ROP 一樣是完整的計算模型，但在合法的控制流裡靜默執行。
- **防禦現狀**：現有的緩解（CFI + sandboxing）對 data-only 的遏制來自沙箱隔離，而不是直接的 data-only 偵測；DFI 是理論上的直接防禦，但因效能成本未廣泛部署。
- **與 browser_pwn 的連結**：你在 V8 課程做過的型別混淆、backing store 竄改，都是 data-only 的具體實現；本章是把那套手法系統化並遷移到 Windows 語境的理論基礎。

## 自我檢核

- [ ] 不看筆記，能畫出「傳統控制流劫持」和「data-only 攻擊」兩條路的資料流圖，並標出 CFI 在哪一步介入、為什麼 data-only 繞開了它
- [ ] 面試被問「CFI 能不能防 data-only 攻擊」——能給出精確的否定答案，並說明原因（CFI 只保護控制流資料，不保護業務邏輯資料）
- [ ] 能解釋 DOP 的圖靈完備性是什麼意思，以及為什麼程式裡的 dispatch loop 是 DOP gadget chain 的前提
- [ ] 從你在 browser_pwn 課做的 V8 data-only 利用，能識別出它用了本章的哪幾條技法路線（型別欄位竄改 + 資料指標竄改）
- [ ] 知道 DFI 是什麼，以及它為什麼尚未在生產系統廣泛部署（效能成本）

## 延伸閱讀

### 原始論文

- **Shengzhi Hu, Zhihao Liang, Purui Su, et al. "Data-Oriented Programming: On the Expressiveness of Non-Control Data Attacks." *IEEE Symposium on Security and Privacy (S&P)*, 2016.**
  - **讀哪裡**：Abstract + Section II（DOP Gadget 定義）+ Section III（圖靈完備性證明）+ Section V（wu-ftpd 案例研究）；Section IV 的形式化可略過
  - **和本章關聯**：本章 DOP 節的理論基礎；圖靈完備性的完整論證、DOP gadget 的形式定義都在這裡
  - **前提知識**：本章讀完；了解基本計算模型（圖靈機）概念

- **Miguel Castro, Manuel Costa, Tim Harris. "Securing Software by Enforcing Data-flow Integrity." *OSDI*, 2006.**
  - **讀哪裡**：Introduction + DFI 的設計（Section 3）+ Evaluation（效能 overhead 數字在這裡）
  - **和本章關聯**：data-only 攻擊的理論防禦——DFI；理解為什麼它尚未普及（效能成本）
  - **前提知識**：編譯器基礎（SSA、資料流分析）

### 部落格 / 分析報告

- **[Project Zero — In-the-Wild Series: October 2020 0-day exploitation](https://googleprojectzero.blogspot.com/2021/01/in-wild-series-october-0-day.html)**
  - **讀哪裡**：CVE-2020-16009（V8 型別混淆）的利用分析段落
  - **和本章關聯**：從真實野外漏洞的視角看型別混淆 → data-only → 沙箱逃逸的完整鏈；補充 browser_pwn 和本章的實戰連結
  - **前提知識**：V8 物件模型（browser_pwn Ch 3–4）+ 本章型別欄位竄改

- **[Connor McGarr — Exploit Development: Browser Exploitation on Windows — A Case Study of CVE-2019-0567](https://connormcgarr.github.io/browser-exploitation/)**
  - **讀哪裡**：Data-only 段落與利用鏈分析
  - **和本章關聯**：Windows 瀏覽器漏洞的 data-only 利用鏈，比學術論文更貼近實作；Connor McGarr 是 Windows 現代緩解研究的一線研究者
  - **前提知識**：本章 + Ch 33（CFG bypass）的思路

### 書籍 / 教學材料

- **《The Shellcoder's Handbook, 2nd ed.》第 20 章（Non-Control Data Attacks）** — Jack Koziol, David Litchfield, et al.（Wiley）
  - **讀哪裡**：第 20 章「Format String Bugs and Non-Control Data Attacks」
  - **和本章關聯**：data-only 攻擊的早期系統化整理（2007 年），確立了「非控制流資料也是攻擊目標」的框架；歷史脈絡視角
  - **前提知識**：format string 漏洞基礎

- **[MITRE ATT&CK T1055.012 — Process Hollowing](https://attack.mitre.org/techniques/T1055/012/) 與 T1546 系列**
  - **讀哪裡**：T1055（Process Injection）全系列子技術，理解哪些被 ACG/CIG 擋住、哪些是 data-only 路線
  - **和本章關聯**：把本章的技法框架對應到 MITRE ATT&CK 的戰術分類，對紅隊和防禦端都有用的語彙

EMET 和 WDEG 的歷史給了我們一個罕見的機會：看清楚「緩解 → 繞過 → 更強的緩解 → 新的繞過」這個軍備競賽的完整演進過程。

→ [Ch 38 — EMET→WDEG 緩解演進史](./38-emet-wdeg-history.md)
