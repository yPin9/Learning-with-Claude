# Ch 28 — CFI / KCFI：原理、為什麼 kernel ROP 路越走越窄

> **目標**：搞懂 forward-edge CFI（Control-Flow Integrity，控制流完整性）到底在每個間接呼叫點檢查什麼，Linux 用的 clang KCFI 怎麼把「型別 hash」放在函式入口前、怎麼在呼叫點比對、檢查失敗時 kernel 怎麼反應。學完你能精確回答三個問題：KCFI 擋住了攻擊者的哪一招（劫 RIP 去任意位址）；它擋不住哪三類（backward-edge 的 `ret`、型別吻合的合法 target 之間的呼叫、完全不碰控制流的 data-only）；為什麼 CFI 普及之後，data-only 攻擊（[Ch 29](./29-data-only-attack.md)）成為主流而不是「進階加料」。

> **環境**：Linux 6.6 LTS、x86-64 基線。KCFI 需要 clang 16 以上、`CONFIG_CFI_CLANG=y`（6.1 起 x86 與 arm64；6.6 的 Kconfig 名稱是 `CFI_CLANG`，更新的 kernel 因為加入其他編譯器支援而改名，請以你的 Kconfig 為準）。本章的 userland 反組譯片段是在 WSL2 Ubuntu、clang 14 上**實測**的（clang 14 不支援 KCFI，我們用舊式的 LTO CFI 作對照，稍後會解釋）；KCFI 本身的指令序列與 kernel 失敗訊息格式**未實測，為理論預期行為**，依據是 2022 年的 patch 討論串與原始碼記憶，細節可能因 clang / kernel 版本而異。

## 為什麼需要這個？

[Ch 21](./21-heap-to-rip.md) 的利用鏈有一個共同的瞬間：攻擊者覆寫了一個函式指標（`tty_struct->ops->ioctl`、`seq_operations->start`、`pipe_buffer->ops->release`），kernel 隨後以 `call *%rax` 之類的指令透過這個指標呼叫，**RIP 被攻擊者決定**。接下來是 stack pivot、ROP（[Ch 13](./13-smep-smap.md)），最後 `commit_creds(prepare_kernel_cred(0))`。

這個瞬間有一個設計上的缺口：**間接呼叫（indirect call）不檢查目標**。CPU 看到 `call *%rax` 就跳；kernel 的編譯器根本不知道 `%rax` 理論上應該指向哪一類函式。

CFI 的出發點就是補這個缺口：既然原始碼裡每個函式指標都有型別（`int (*)(struct file *, unsigned long)`），那編譯器可以在呼叫前檢查「這個目標函式的型別，和這個呼叫點宣告的型別是否一致」。劫到 `commit_creds`？它的型別是 `int (struct cred *)`，和 `ioctl` 的型別不同，擋下。

歷史脈絡（版本與來源標明，不確定的降級）：

| 年代 | 事件 |
|---|---|
| 2005 | Abadi 等人的論文《Control-Flow Integrity》提出概念（學術起點） |
| 2010 年代 | PaX / grsecurity 的 RAP 是 Linux 世界最早的實用型 CFI 實作之一（見 [Ch 51](./51-defense-detection.md)），但不在 mainline |
| 約 2018–2021 | Google 為 Android 在 arm64 上以 clang 的 LTO-based CFI 維護 out-of-tree patch；mainline 的 `CONFIG_CFI_CLANG` 在 5.13 前後進入（就我所知，版本號未逐字驗證） |
| 2022 | Sami Tolvanen 提出 **KCFI**，專為 kernel 設計，clang 16 內建支援 |
| **6.1**（2022-12） | KCFI 進 mainline，同時支援 **arm64 與 x86-64**；舊的 LTO-based CFI 被取代（查證：v6.1-rc1 的 kcfi pull request 與 cover letter） |

為什麼要有 KCFI 而不是繼續用舊式 CFI？cover letter 列的理由我們查到三項：KCFI **不需要 LTO**（Link-Time Optimization，連結期最佳化）、**不把函式位址改指向 jump table**（所以函式指標的位址相等性不被破壞）、**實作更貼近 kernel 的需求**。對 kernel 而言，「函式 `f` 的位址就是 `f` 的位址」這個性質太基礎了，任何改寫位址的方案都會在 kallsyms、tracing、live patching 上製造連鎖問題。

## 先建立直覺

想像一棟辦公大樓，每個房間門上都有一張牌子：「**只收型別 A 的訪客**」。舊世界沒有警衛：只要你走到門口，門就開了。CFI 在每個門口放一個警衛，他只做一件事——看你胸前的識別證（型別 hash）是不是 A。

```
        間接呼叫點                                    被呼叫的函式
  ┌────────────────────────┐                  ┌──────────────────────────┐
  │  預期型別 = T_ioctl      │                  │  foo:                    │
  │                        │  call *%r11      │   [前 4 byte: 型別 hash]  │
  │  警衛：                 │ ───────────────▶ │   push rbp               │
  │   hash(目標) == T_ioctl?│  (r11 = &foo)    │   ...                    │
  │      ├─ 是 → 放行       │                  └──────────────────────────┘
  │      └─ 否 → ud2 (trap) │
  └────────────────────────┘
```

這個比喻要小心兩個邊界：

1. **警衛看的是型別，不是身分**。所有型別 A 的函式，在警衛眼中都一樣。攻擊者如果能把指標指向另一個「型別 A 但行為有用」的函式，警衛放行。
2. **警衛只在「門口」。** 走出房間（`ret`）、翻窗（data-only，根本沒有「呼叫」）都不歸他管。

> 如果你對 [Ch 21](./21-heap-to-rip.md) 的 ops table 劫持還不熟，先回看那章的「為什麼是函式指標」一節。

## CFI 的家族與 KCFI 的位置

控制流轉移分兩類，防禦也分兩類：

| 類別 | 指令 | 目標由誰決定 | 防禦 |
|---|---|---|---|
| **forward-edge** | `call *reg`、`jmp *reg` | 函式指標變數（在 heap / data） | CFI（本章主角）、IBT |
| **backward-edge** | `ret` | stack 上的 return address | stack canary、shadow stack、PAC |

KCFI 只管 forward-edge 的**間接呼叫**。同一個「方向」的其他方案對照：

| 方案 | 粒度 | 機制 | 備註 |
|---|---|---|---|
| IBT（Indirect Branch Tracking，Intel CET 的一部分） | 粗：「是不是合法入口」 | 間接跳轉的目標必須是 `endbr64` 指令 | x86 `CONFIG_X86_KERNEL_IBT`，5.18 起（WSL2 6.18 樣本實測為開） |
| KCFI | 中：型別 | 比對型別 hash | 6.1 起；需 clang 16+ |
| FineIBT（x86） | 中：型別 + IBT | 把型別檢查搬進函式入口，與 `endbr` 結合 | 6.2 前後引入（就我所知，未逐字驗證）；執行期可選用 KCFI 或 FineIBT |
| arm64 BTI + PAC | 粗 + 簽章 | 見 [Ch 46](./46-arm64-kernel-pwn.md) | |

> IBT 的粒度是「全部函式入口」：任何函式都是合法目標，所以它擋的是「跳到函式中間」（ROP gadget 的典型位置），不是「跳到任意函式」。KCFI 在此之上再縮小為「型別相同的函式」。兩者疊加才是現代 x86 的 forward-edge 防線。

## 核心機制：型別 hash 放在哪、怎麼檢查

### 編譯期做兩件事

1. **算出每個「address-taken」函式的型別 hash，放在函式入口前**。
   - 「address-taken」指程式裡有取過這個函式位址的（賦給函式指標、塞進 ops table）。純粹被直接 `call foo` 的函式不需要被間接呼叫，也就不需要簽章。
   - hash 是對該函式型別（回傳型別 + 參數型別）的 mangled name 做 hash 取 32 bit（就我所知 clang 用 xxHash 系列；演算法細節不影響本章的推理，請以 clang 文件為準）。型別相同 → hash 相同。
2. **在每個間接呼叫點前插入檢查**：取出目標函式入口前的 hash，與編譯器已知的「這個呼叫點預期的 hash」比較，不符就觸發 trap。

### 在 x86-64 上的樣子（概念示意）

下面的序列來自 2022 年 x86 KCFI patch 的討論串，用來說明結構；**最終 clang 的輸出（暫存器選擇、padding 長度、是否含 `endbr64`）會隨版本變動，未實測**：

```asm
; ---- 被呼叫端：每個 address-taken 函式前 ----
__cfi_foo:
    mov   $0xDEADC0DE, %eax        ; 這個立即值就是 foo 的型別 hash
                                   ; （此指令從不被執行，是被「當資料」讀）
    int3 ... (padding)
foo:                                ; ← 函式指標指向這裡
    endbr64
    push  %rbp
    ...

; ---- 呼叫端：每個間接呼叫點 ----
    mov   $(-0xDEADC0DE), %r10d    ; 呼叫點預期型別 hash 的「取反」（編譯期常數）
    add   -0x4(%r11), %r10d        ; r11 = 目標；-4(%r11) = foo 前 4 byte 的 hash
    je    1f                       ; 相加為 0 → 兩者相等 → 放行
    ud2                            ; 否則：非法指令 trap
1:  call  *%r11
```

幾個關鍵設計：

- **hash 存在函式入口前 4 byte（offset -4）**，呼叫端用 `-0x4(%r11)` 取。「取反再相加比 0」是為了避免把 hash 立即值**直接**寫在呼叫點的 `cmp` 指令裡。2022 年討論串中有人指出：如果呼叫點是 `cmpl $imm32, -6(%r11)` 這種形式，那條指令本身帶有完整的 32-bit hash，**距離這個 cmpl 固定 offset 的位置都可能被當成合法的間接呼叫目標**（意外地創造 gadget）。最終版本改成目前這種樣子，就是避免這個問題。這是個漂亮的例子：防禦機制本身的 byte pattern 也會成為攻擊面。
- **trap 用 `ud2`**：觸發 invalid opcode exception（`#UD`），由 kernel 的 trap handler 接手。

### 真實反組譯：舊式 CFI 的對照

我們在 WSL2（Ubuntu clang 14）上實測了兩件事。第一，clang 14 不認識 KCFI：

```
$ clang -O0 -fsanitize=kcfi k.c -o k
clang: error: unsupported argument 'kcfi' to option 'fsanitize='
```

這就是「需要 clang 16+」的現場證據。第二，我們用舊式的 `-fsanitize=cfi-icall`（需要 `-flto`）編了一個故意型別不匹配的程式，並反組譯 `main`：

```c
typedef int (*op_t)(int);
int  good(int x){ return x+1; }
long bad (long x){ return x+2; }       // 型別不同：long(long)
int main(void){
  op_t f = good;       printf("%d\n", f(1));
  f = (op_t)bad;       printf("%d\n", f(1));   // 型別不匹配
}
```

```
$ ./k_cfi ; echo "exit=$?"
bash: Illegal instruction     (core dumped) ./k_cfi
exit=132                      # 128 + SIGILL(4)
```

反組譯（節錄，兩個呼叫點結構相同）：

```
11b6: lea    -0x6d(%rip),%rax        # <bad>
11bd: mov    %rax,-0x8(%rbp)
11c1: mov    -0x8(%rbp),%rcx
11c5: lea    0x34(%rip),%rax         # <__typeid__ZTSFiiE_global_addr>
11cc: cmp    %rax,%rcx               # 目標位址是否落在「型別 int(int) 的 jump table」
11cf: je     11d6
11d1: ud1    0x2(%eax),%eax          # 不符 → 非法指令 → SIGILL
11d6: mov    $0x1,%edi
11db: call   *%rcx
```

這段實測給我們三個觀察：

1. **結構一致：「比對 → 相符放行、不符 trap」**。舊式與 KCFI 長得不一樣，但骨架相同。
2. **舊式 CFI 比對的是「位址落在不落在某個型別專屬的 jump table」**（`__typeid__ZTSFiiE_global_addr`；`ZTSFiiE` 是 `int(int)` 的 mangled 型別名），所以它得把函式位址改寫成指向 jump table 的位址（LTO 才做得到）。KCFI 把 hash 放在函式本體前、檢查時讀它，所以不需要動函式位址，也就不需要 LTO。
3. **失敗是 SIGILL，而且程式的第一個 `printf("%d\n", f(1))` 輸出在 pipe 時被緩衝、一起被丟掉了**——這是一個真實的教訓：CFI 失敗是硬性的 trap，不是 warning。

### 失敗在 kernel 裡長什麼樣

userland 是 SIGILL；kernel 裡 `ud2` 觸發 `#UD`，交給 trap handler。KCFI 的處理路徑（概念，**未實測，依 6.x 原始碼記憶；請對 `arch/x86/kernel/traps.c`、`arch/x86/kernel/cfi.c`、`kernel/cfi.c` 驗證**）：

```
  ud2 → #UD exception
        │
        ▼
  exc_invalid_op()  →  handle_bug()
        │
        ├─ 認出這個 ud2 是「CFI 檢查」產生的（解碼 ud2 前面的指令，取得
        │   預期型別 hash 與目標位址所在暫存器）
        ▼
  report_cfi_failure(regs, addr, &target, type)
        │
        ├─ 印出一行（格式示意）：
        │     CFI failure at <呼叫點>+0x.../0x... (target: <目標>+0x0/0x...; expected type: 0x........)
        │
        ├─ CONFIG_CFI_PERMISSIVE=y → 只 WARN，繼續執行（僅供除錯）
        └─ 否則 → BUG() → oops → （若 panic_on_oops）panic
```

你從 crash log 看到的就是「呼叫點在哪個函式、目標是誰、預期的型別 hash 是多少」。這個資訊對攻擊者開發過程與對防禦者偵測（[Ch 51](./51-defense-detection.md)）都有價值：**CFI 失敗幾乎不可能是正常程式的行為，是極高訊號的 exploit 嘗試指標**。

## 例子 1：KCFI 擋下 tty ops 劫持

回到 [Ch 21](./21-heap-to-rip.md)。假設 UAF 讓我們控制了一個 `tty_struct`，把 `tty->ops` 指向我們偽造在 heap 上的 ops 表，表中 `ioctl` 欄位填入某個 gadget 位址 G（例如一個 `xchg eax, esp ; ret` 之類的 stack pivot）。使用者對 tty fd 做 `ioctl`，kernel 內部：

```c
// tty_ioctl() 之類的路徑，概念
retval = tty->ops->ioctl(tty, cmd, arg);   // 間接呼叫，預期型別：
                                           //   int (*)(struct tty_struct *, unsigned int, unsigned long)
```

**無 KCFI**：`call *%rax`，RIP = G，pivot 成功。

**有 KCFI**：呼叫前，編譯器插入的檢查讀 `-0x4(G)`。G 是函式中間的某個位址，它前 4 byte 是別的指令的 byte，幾乎不可能恰好等於 `ioctl` 型別的 hash（約 2^-32 的機率，這就是 32-bit hash 的來由）。比對失敗 → `ud2` → oops。

這一招的死亡不是因為 stack pivot 本身，而是因為**第一跳就不能是任意位址**。注意這同時殺掉兩類做法：

- 「跳到 gadget」：位址不是函式入口，hash 讀出來是隨機 byte。
- 「直接跳到 `commit_creds`」：它的入口前 4 byte 是它自己的型別 hash（`int (struct cred *)`），與 `ioctl` 型別不同。

這就是 README 所說「kernel ROP 路越走越窄」的精確意思：**ROP chain 本身（gadget 之間靠 `ret` 串接）沒被擋，被擋的是「第一個進入 ROP 的機會」**。

## 例子 2：KCFI 擋不住的第一類——型別吻合的合法 target

KCFI 的檢查只問「型別相同嗎」。kernel 中**有大量函式共享同一個型別**。以 `ioctl` 型的函式指標為例（file_operations 的 `unlocked_ioctl`：`long (*)(struct file *, unsigned int, unsigned long)`），整個 kernel 裡每個 driver 的 ioctl handler 都是這個型別。對攻擊者而言：

```
  被覆寫的指標預期型別 = T
         │
         ▼
  KCFI 放行所有「型別 = T」的函式
         │
         ▼
  攻擊者的問題變成：kernel 裡有沒有一個型別 = T、
  而且「行為對我有用」的函式？
```

這個問題的答案常常是有的。公開文獻中常見的思路是找「**型別相容的跳板（trampoline）函式**」：一個本身接受參數（資料）並再做一次呼叫、讀寫的函式。典型形狀（用 `work_for_cpu_fn` 說明樣式；這是我們為說明而選的例子，**未驗證它在特定版本是否仍可利用**）：

```c
// kernel/workqueue.c（概念）
static void work_for_cpu_fn(struct work_struct *work)
{
    struct work_for_cpu *wfc = container_of(work, struct work_for_cpu, work);
    wfc->ret = wfc->fn(wfc->arg);        // ← 這又是一次間接呼叫，fn 與 arg 來自 struct
}
```

形狀：型別 `void (*)(struct work_struct *)` 的函式很多，這類函式裡有一個「呼叫另一個函式並傳入我控制的參數」的動作。**但**這第二次間接呼叫同樣被 KCFI 檢查，目標型別得吻合 `long (*)(void *)`。所以攻擊者能呼叫的是「型別吻合 + 行為有用」的函式，**集合很小，且逐版本變動**。

防禦者的視角：KCFI 的粒度是型別，所以降低這類風險的方向是**讓型別更細**。FineIBT 與後續的 arity 資訊（參數個數）檢查（就我所知是後續 kernel 版本討論 / 加入的方向，未逐字驗證）就是往這個方向走。

## 例子 3：KCFI 擋不住的第二類——backward-edge 與純 `ret` ROP

KCFI **只檢查間接 `call`**，不檢查 `ret`。看 [Ch 11](./11-stack-overflow.md) 的 stack overflow：

```
  stack overflow 覆寫 saved return address
        │
        ▼
  函式結束時 ret → RIP = 攻擊者放的位址
        │  （沒有任何「間接呼叫」，KCFI 的檢查根本不會被執行）
        ▼
  ROP chain（每個 gadget 以 ret 串接）
```

整條路徑沒有經過任何 KCFI 檢查點。擋這一條的是別的機制：

| 機制 | 擋什麼 | 在 x86 kernel 的狀態 |
|---|---|---|
| stack canary（`CONFIG_STACKPROTECTOR_STRONG`） | 線性 stack overflow 在 return 前改寫 canary | 開（WSL2 樣本實測為開）；需先 leak canary 才能繞過（[Ch 11](./11-stack-overflow.md)） |
| shadow stack（CET SS） | return address 與影子 stack 不一致 | x86 的 **user-space** shadow stack 在 6.6 進入（就我所知）；**kernel 自己的 `ret` 在 x86 沒有**，是 backward-edge 的真空地帶 |
| arm64 Shadow Call Stack（`CONFIG_SHADOW_CALL_STACK`） | return address 另存在影子 stack | arm64 kernel 有（就我所知 5.8 起），見 [Ch 46](./46-arm64-kernel-pwn.md) |
| arm64 PAC | return address 簽章 | 見 Ch 46 |

結論：在 x86-64 的 6.6 上，**KCFI 加上 canary 並沒有封死 backward-edge**。有 stack overflow 又能 leak canary 的 bug，仍可走 ROP。但就 kernelCTF 公開 submission 的觀察，真實 exploit 的大宗是 heap bug（stack overflow 類在近年的 kernel 相對少見，這是我們的印象，沒有統計數字），而 heap bug 的「控制流」出口恰恰是函式指標（forward-edge），被 KCFI 擋住。

## 例子 4：KCFI 擋不住的第三類——不控制流，改資料

KCFI 不干涉的行為清單：

- 改 `cred` 的 uid 欄位（[Ch 16](./16-classic-primitives.md)）：一次寫入，沒有呼叫。
- 改 `modprobe_path`：寫字串，之後由 kernel 自己的**合法**呼叫（`call_usermodehelper`）執行。
- 換 `struct file`（Dirty Cred，[Ch 24](./24-dirty-cred.md)）。
- 改 PTE（Dirty Pagetable，[Ch 23](./23-dirty-pagetable.md)）。
- 把 `file->f_op` 指向**另一個合法的 ops 表**（表裡的函式指標本來就是合法的，被呼叫時型別都吻合）。

這些都有同一個特徵：**kernel 全程只做它本來就會做的、型別吻合的事，只是資料被改過了**。CFI 保護的是「程式的控制流圖」，而資料決定了「在這張圖上走哪條路」。攻擊者只要不離開這張圖，CFI 沒有立場阻止。

```
        KCFI 保護的範圍                         KCFI 不管的範圍
  ┌──────────────────────────┐          ┌──────────────────────────────┐
  │ 間接呼叫的目標必須是       │          │ 合法 ops 之間的切換            │
  │ 型別相同的函式入口         │          │ cred / file / PTE / 字串       │
  │                          │          │ ret（backward-edge）           │
  │ （第一次進入 ROP 的機會）  │          │ 型別相同的合法跳板             │
  └──────────────────────────┘          └──────────────────────────────┘
        ← CFI 之前的主流                      ← CFI 之後的主流 (Ch 29)
```

這就是為什麼 README 說「別迷信 ROP」，也是 [Ch 29](./29-data-only-attack.md) 存在的理由：CFI 把控制流出口一個個封死之後，攻擊者的最佳解就是**不走控制流**。這不是「進階技術」，是防禦演化的必然結果。

## KCFI 的實作細節補充

- **module 一起被保護**：out-of-tree 與 in-tree module 若用同一個支援 KCFI 的編譯器與設定編譯，函式型別 hash 的算法相同，跨 module 的間接呼叫照樣檢查。反過來，**沒用 KCFI 編譯的 module**（例如廠商提供的 binary blob）呼叫端沒有檢查，被它呼叫的 kernel 函式即使有 hash 也沒有人驗證。
- **assembly 函式**：組語函式若會被間接呼叫，要用 `SYM_TYPED_FUNC_START` 之類的巨集明確標示並提供型別（就我所知的做法，請對 `include/linux/cfi_types.h` 驗證）。
- **BPF JIT**：JIT 產生的程式碼也需要符合 KCFI 的呼叫約定（就我所知 JIT 會替 BPF 程式 emit hash，細節見 [Ch 32](./32-ebpf.md)，未驗證）。
- **執行期模式（x86）**：6.2 前後加入 `cfi=` 開機參數（`kcfi`、`fineibt`、`off` 等，就我所知），讓同一個 kernel 依硬體是否支援 IBT 選擇 KCFI 或 FineIBT。**未實測**；如果你要用，請查 `Documentation/admin-guide/kernel-parameters.txt`。
- **效能**：每個間接呼叫多幾條指令（一次記憶體讀取與比較）。官方與 Android 的經驗是整體開銷可接受（Android GKI 以此量產）；具體百分比隨 workload 變動，我們沒有量，不給數字。

## 對比與取捨

| 方案 | 粒度 | 需要什麼 | 擋什麼 | 繞法（概念） |
|---|---|---|---|---|
| 無 CFI | 無 | — | — | 任意 RIP |
| IBT（`endbr64`） | 所有函式入口 | CET 硬體、5.18+ | 跳進函式中間 | 跳到任何函式入口 |
| 舊式 LTO CFI | 型別（jump table） | clang + LTO | 型別不符的間接呼叫 | 型別相符的目標；jump table 位址改寫帶來的相容性問題 |
| **KCFI** | 型別（hash） | clang 16+，6.1+ | 型別不符的間接呼叫 | 型別相符的目標、`ret`、data-only |
| FineIBT | 型別 + IBT | CET 硬體 | 同 KCFI，且 `endbr` 與檢查合一 | 同上 |
| shadow stack / SCS / PAC | backward-edge | 硬體或編譯器支援 | 改寫 return address | x86 kernel 無；arm64 見 Ch 46 |
| 無（data-only 層） | — | — | — | 沒有 production 對應物 |

為什麼 kernel 沒有採用「更強的」方案（例如精確到每個呼叫點的白名單）？因為那需要整程式分析（whole-program analysis），而 kernel 有大量 module 動態載入、組語、JIT 與 tracing 機制，精確的呼叫圖在編譯期不可得。型別是**編譯器在單一翻譯單元內就能知道的最強資訊**，所以 KCFI 停在這裡。這是個真實的工程取捨：「粒度」與「可部署性」的交換。

## 踩雷集錦

1. **「開了 CFI，kernel ROP 就死了」**：錯。死掉的是「**第一次進入 ROP 的間接呼叫**」。如果你的 bug 提供的是 stack overflow（`ret` 路徑），ROP 完全不經過 KCFI 檢查點。要看 bug 給你的是哪一種控制流出口。
2. **「型別 hash 是函式的身分」**：不是。hash 只代表型別。同型別函式之間無法區分。在評估 CFI 的強度時，你要估的是「**同型別的函式集合有多大、其中有多少行為有用**」，不是「有 2^32 種 hash」。
3. **「CFI 失敗 = 程式 bug」**：在 mainline 的正常使用下，KCFI 失敗幾乎一定是不合法的間接呼叫。但也有 false positive 的來源：沒有被 KCFI 編譯的 module、型別在 C 裡被強制轉型的**合法**程式（有些 kernel 程式碼為了方便把函式指標 cast 成不同型別，在啟用 KCFI 時要修正）。調試時 `CONFIG_CFI_PERMISSIVE` 能先把 trap 降成 warning。
4. **「FGKASLR / KASLR 與 CFI 互相取代」**：互補。KASLR 讓攻擊者不知道位址，CFI 讓攻擊者知道位址也不能亂跳。兩者都防「劫 RIP」，但擋的是不同的前提。
5. **「用 `-fsanitize=cfi-icall` 就是 Linux 的 KCFI」**：不是。前者是 LTO-based、改寫函式位址的舊式方案（本章實測的 `__typeid__ZTSFiiE_global_addr` 就是它的 jump table），後者是 clang 16 起的 `-fsanitize=kcfi`。兩者的 kernel Kconfig 與 ABI 影響不同；舊式在 6.1 被取代。
6. **「`ud2` 只是 crash，不會洩漏資訊」**：crash log 會印呼叫點、目標位址與預期型別。若你的 log 會被未授權者讀到（`dmesg_restrict=0` 且系統不是你的），這些位址對攻擊者是 KASLR leak。這就是 [Ch 14](./14-kaslr-infoleak.md) 的 `kptr_restrict` / `dmesg_restrict` 存在的原因（`%pS` 在 `kptr_restrict` 下的行為請自行驗證）。

## 進階：再往深一層

### 攻擊者在 KCFI 下的剩餘空間（概念層）

用 KCFI 的定義反推，剩下的空間只有這幾個方向：

1. **型別相符的合法目標**：搜尋 kernel 裡型別 T 的所有函式，看有沒有行為有用的（例如把參數寫進指定位址、或再做一次受控的呼叫）。
2. **未被 KCFI 保護的路徑**：組語路徑、沒有編譯 KCFI 的 module、`jmp *reg` 型的間接跳轉（kernel 大多以 `-fno-jump-tables` 避免產生，但不是零）、`ret`。
3. **改變「被呼叫的合法目標」**：不改函式指標，而是改 `file->f_op` 指向另一個合法的 ops 表（資料層的指標替換）。這是 Ch 29 的「合法 target 搜尋」。
4. **完全離開控制流**：Ch 29 的主題。

每個方向的難度與穩定度，是 kernelCTF mitigation 賽道（[Ch 49](./49-kernelctf-overview.md)）近年 submission 的分野。

### 為什麼 CFI 要等到 2022 才在 x86 kernel 落地

- 舊式 CFI 需要 LTO，kernel 的 LTO 長期處於實驗性（建置時間、除錯、與 tracing 的相容性）。
- 位址改寫（jump table）破壞「函式位址就是函式位址」。
- clang 與 kernel 的緊密綁定（kernel 要用 clang 編譯）在 x86 發行版上有採用阻力。

KCFI 的設計是對這三點的答案：**不需要 LTO、不改位址、與 clang 16 的新 sanitizer 模式綁定**。仍然是「必須用 clang 編譯」這一點讓它在多數 x86 distro 預設不開——這也是你今天看到 KCFI 主要出現在 Android GKI 與少數雲廠商 kernel 的原因。GCC 側的支援（就我所知是後續幾年的工作，未逐字驗證）則是另一條時間線。

## 動手練習

> 在你自己的環境操作。標「實測」者我們在 WSL2 跑過；其餘為理論預期行為。

1. **重現本章的實測**（實測於 clang 14）：
   ```bash
   cat > k.c <<'EOF'
   #include <stdio.h>
   typedef int (*op_t)(int);
   int  good(int x){ return x+1; }
   long bad (long x){ return x+2; }
   int main(void){
     op_t f = good;       printf("%d\n", f(1));
     f = (op_t)bad;       printf("%d\n", f(1));
     return 0;
   }
   EOF
   clang -O0 -fsanitize=kcfi k.c -o k            # clang 14 預期：unsupported argument 'kcfi'
   clang -flto -fvisibility=hidden -fsanitize=cfi-icall k.c -o k_cfi
   ./k_cfi; echo "exit=$?"                        # 預期 exit=132（SIGILL）
   objdump -d --no-show-raw-insn k_cfi | sed -n '/<main>:/,/ret/p'
   ```
2. **用 clang 16+ 跑 KCFI**（未實測）：同一份 `k.c`，`clang-16 -O0 -fsanitize=kcfi k.c -o k_kcfi`，`objdump -d` 找函式前的 `mov $imm32, %eax` 與呼叫點的 `add -0x4(%r1x)`。把你看到的實際序列和本章的示意對照，標出差異。注意：userland 沒有 kernel 的 handler，失敗是 SIGILL。
3. **查你 kernel 的 CFI 狀態**（實測於 WSL2 6.18）：`zcat /proc/config.gz | grep -E 'CFI|IBT'`。我們的樣本輸出：`CONFIG_X86_KERNEL_IBT=y`、`CONFIG_ARCH_SUPPORTS_CFI=y`、`CONFIG_FUNCTION_PADDING_CFI=11`，**沒有**啟用 CFI 本身。解讀：硬體 IBT 開了、CFI 的函式前 padding 在，但 KCFI 沒有編進這個 kernel。
4. **自己編 6.6 並開 `CFI_CLANG`**（未實測）：`make LLVM=1 menuconfig`，開 `CFI_CLANG`，再開 `CFI_PERMISSIVE` 方便觀察。用 Ch 5 的 vulnerable module，故意把 `file_operations` 的 ioctl 指標改成型別不吻合的函式，載入後觸發，預期 dmesg 出現 `CFI failure at ...`。
5. **型別相容性的紙上實驗**：在 6.6 原始碼（Elixir）找 `unlocked_ioctl` 的賦值處，數 `long (*)(struct file *, unsigned int, unsigned long)` 型別有多少實作。這個數字就是例子 2 的「同型別集合」的粗略大小。
6. **複習 Ch 21**：把 tty ops 劫持的每一步標出 KCFI 擋在哪一步，並寫下你會改成什麼（Ch 24 / Ch 29 的方向）。

## 本章重點整理

- forward-edge CFI = 間接呼叫前驗證目標型別；KCFI（clang 16+、Linux 6.1+）把型別 hash 放在函式入口前 4 byte，呼叫點讀它比對，失敗 `ud2` → oops。
- 它擋的是「**第一次進入控制流劫持**」：任意位址、gadget、`commit_creds` 這類型別不符的目標。
- 它擋不住：型別吻合的合法 target、backward-edge 的 `ret`（x86 kernel 沒有 shadow stack）、不碰控制流的 data-only。
- CFI 把控制流出口封死後，攻擊者唯一不被限制的是資料層，這就是 data-only 成為主流的結構性原因。

## 自我檢核

- [ ] 不看筆記，能畫出 KCFI 在「呼叫端」與「被呼叫端」各放了什麼，並說明 hash 為什麼放在函式前而不是呼叫點的立即值裡嗎？
- [ ] 能解釋為什麼 KCFI 不需要 LTO、不改函式位址，而舊式 CFI 需要嗎？
- [ ] 面試被問「CFI 能不能擋 kernel ROP」，你能在一分鐘內講清楚「擋第一跳、不擋 `ret` chain」的區別嗎？
- [ ] 能說出至少三種 KCFI 擋不住的情況，並為每一種指出一章內容嗎？
- [ ] 想像一個你熟悉的 driver 的函式指標，估一下「和它同型別的函式有多少」，這對 CFI 的實際強度意味著什麼？
- [ ] 能說明為什麼 crash log 中的 `CFI failure` 是好的偵測訊號，又為什麼它可能洩漏位址嗎？

## 延伸閱讀

### 論文

- **[Control-Flow Integrity: Principles, Implementations, and Applications](https://dl.acm.org/doi/10.1145/1609956.1609960)** — Abadi, Budiu, Erlingsson, Ligatti（ACM CCS 2005；TISSEC 2009 擴充版）
  - **核心貢獻**：定義 CFI——限制間接控制轉移的目標在靜態算出的合法集合內，並證明在合理的攻擊者模型下能擋住控制流劫持。
  - **讀哪裡**：Section 2（威脅模型與 CFI 定義）與 Section 3 的 CFG 與 label 檢查機制；證明部分可跳過。
  - **和本章的關聯**：KCFI 的「型別」是這篇 CFG 的一個粗粒度近似；讀它才懂為什麼「合法 target 集合的大小」是 CFI 強度的核心指標。前提：基本編譯器與組語知識。

### Patch 與討論串

- **[KCFI support（Sami Tolvanen，LKML 2022，v4/v5 cover letter）](https://lkml.iu.edu/hypermail/linux/kernel/2209.1/01345.html)**
  - **這篇說什麼**：KCFI 相對舊式 CFI 的動機、設計與 arm64 / x86 的實作取捨。
  - **讀哪裡**：cover letter 的「為什麼 KCFI」與 x86 patch 的 commit message（preamble 與檢查序列、hash 與 endbr 的互動）。
  - **為什麼值得讀**：作者就是實作者；x86 patch 的討論串包含「cmpl 指令帶完整 hash 會製造 gadget」這類防禦機制自身 byte pattern 的討論，極有教學價值。
- **[GIT PULL kcfi updates for v6.1-rc1（Kees Cook）](https://lkml.iu.edu/hypermail/linux/kernel/2210.0/00259.html)**
  - **這篇說什麼**：KCFI 進 mainline 的摘要與測試狀況。
  - **讀哪裡**：整篇（很短）。
  - **和本章的關聯**：本章「6.1、x86 + arm64」的來源。

### 文件

- **clang 文件〈Control Flow Integrity〉**（<https://clang.llvm.org/docs/ControlFlowIntegrity.html>）
  - **讀哪裡**：`-fsanitize=kcfi` 一節，以及 `cfi-icall` 一節（對照舊式）。
  - **注意**：clang 版本更新時細節會變；以你使用的版本的文件為準。
  - **前提**：本章。
- **Linux 原始碼 `kernel/cfi.c`、`arch/x86/kernel/cfi.c`、`include/linux/cfi_types.h`**（[Bootlin Elixir](https://elixir.bootlin.com/linux/v6.6/source/kernel/cfi.c)）
  - **讀哪裡**：failure report 的格式化與 permissive 行為；型別巨集的用法。
  - **和本章的關聯**：本章「失敗長什麼樣」那一節標「未實測」的部分，在這裡驗證。前提：[Ch 2](./02-reading-kernel-source.md)。

CFI 把「劫 RIP」那條路封到只剩型別相同的縫；下一章我們不再走控制流，直接改資料：為什麼 data-only 成為主流，以及怎麼在 kernel 裡找出能被利用的合法 target。

→ [Ch 29 data-only attack 技術集：為什麼成主流、pipe_buffer、cred、合法 target 搜尋](./29-data-only-attack.md)
