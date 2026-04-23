# Ch 26 — Under-constrained symbolic execution

> 目標：介紹 under-constrained SE（UCSE）為什麼存在、什麼時候用、代價是什麼。

## 動機

Traditional symex 從 `main` 開始、input 是外部 argv / stdin。這對 standalone program 很好。

但很多實用場景不對：

- **function-level unit analysis**：我想分析 `parse_header()`，不要從 `main` 跑過 1000 條 instruction 才到它
- **kernel subsystem**：我想分析一個 syscall handler，不是整個 kernel boot
- **library function**：分析 `libxml2` 某個 function，不跑整個 xml 處理流程

直接從該 function 跑？碰到的問題：

```c
void parse_header(struct ctx* c, char* buf, int len) {
    if (c->magic != 0xdead) return;   // c 從哪來？
    if (c->state != READY) return;
    // process
}
```

c 是 caller 給的 pointer。你從這個 function 開始 symex，c 指向哪？c->magic 是什麼值？什麼都不知道。

**UCSE 的答案**：直接讓 c, c->magic, c->state 都是 **unconstrained symbolic**。假設 caller 可能給任意 c，function 的 input space 是「所有可能的 pointer state」。

## UCSE 的定義

**Under-constrained symbolic execution**：
- 從中間 function 開始 symex
- 所有 caller-provided input（args、global state、heap state）都是 **unconstrained symbolic**
- 缺乏 precondition（caller 有 enforce 的 invariant），你不知道

"Under-constrained" 意思：你的 path constraint **比真實可達性少**（缺了 caller 應加的 constraint），所以可能出**偽陽 bug**（實際 caller 不會傳那種 input，但你 symex 以為會）。

## 代表作：UC-KLEE (Ramos, Engler, USENIX 2015)

Stanford 做的，在 KLEE 基礎上加 UCSE。

- 對一個 function 自動生成 **unit test**
- 發現 bug 時會嘗試 classify：是真 bug 還是 false positive（由 precondition 缺失造成）

UC-KLEE 在 libc 實作（dietlibc、uclibc）上找到 **~80 個 bug** — 部分是 CVE-worthy。

## 另一個代表：Wild Fire (Engler, 2019)

持續 UC-KLEE 的思路，進一步自動化。把「這個 bug 是不是 false positive」用 **cross-checking** 解：
- 同一個 function 在兩個 library 有不同實作（例 glibc vs uclibc）
- 對相同 input，兩個實作是否 output 相同？
- 不同 → 其中一個有 bug

這樣 UCSE 的 false positive 被 filter 掉 — 只留下 **implementation divergence** 當證據。

## Kernel / Device driver 的 UCSE

Linux kernel 最常用 UCSE（或類似思想）分析 driver。

- 從 driver ioctl handler 跑 symex
- 參數 `struct user_request*` 是 attacker-controlled → unconstrained symbolic
- 每個 field dereference 都 fork 出新可能性
- 找 NULL deref、OOB access、type confusion

代表工具：
- **DR. CHECKER**（USENIX Sec 2017）：static UCSE on LLVM IR
- **SymDriverGen**、**S2E** 的 driver mode：dynamic

## UCSE 的好處

- **局部化分析**：不用整個 program
- **快**：path space 集中在 target function
- **適合 library 驗證**：不需要完整 client

## UCSE 的代價

### 1. False positive

```c
void foo(char* p) {
    p[0] = 'a';     // UCSE 會 fork: p == NULL? OOB? 
                     // 但 caller 保證 p 非空
}
```

caller 的 precondition 丟失 → UCSE 認為 p 可能 NULL → 報 NULL deref。實際不是 bug。

### 2. 要 precondition 輸入

UC-KLEE 允許使用者加 `__klee_assume(p != NULL)` 之類的 hint 減少 FP。但這變成 **manual annotation**，失去全自動的 promise。

### 3. 全 symbolic heap state 時 OOM

如果 function 有 complex pointer chase：

```c
void walk(struct node* head) {
    while (head) {
        process(head->data);
        head = head->next;
    }
}
```

UCSE 會對 `head`、`head->next`、`head->next->next` 一路 symbolic，**每一層 fork 成 NULL/非 NULL**。path 指數爆炸。

對策：**limit pointer depth**（只 dereference K 層）、**lazy initialization**（只在實際 dereference 時才 fork 出該 pointer 是 NULL 還是有效）。

## Lazy initialization

UCSE 的重要 optimization：碰到未見過的 pointer field，**延遲決定**。

```c
void foo(struct ctx* c) {
    if (c->magic == 0xdead)    // 第一次 access c->magic
        return;
    // ...
    bar(c->inner);              // 第一次 access c->inner
    // ...
}
```

Lazy init 的策略：

- 第一次讀 `c->magic`：產生 symbolic value（未用）
- 第一次讀 `c->inner`：fork 兩條
  - state_A：`c->inner = NULL`
  - state_B：`c->inner = new_symbolic_object`（同樣 lazy init）

只在真用到時才 fork。減少無用分支。

UC-KLEE 有這個 optimization。現代 UCSE 工具都用。

## UCSE 的一個 pitfall：state 相依

真實 function 有**多個 arg 互相有 constraint**：

```c
void read_n(char* buf, size_t n) {
    for (size_t i = 0; i < n; i++) {
        buf[i] = getc();
    }
}
```

真實 caller：`buf` 是大小 ≥ n 的 buffer。UCSE 不知道這個 invariant，可能 fork：

- state_A：n = 0（ok）
- state_B：n = 100、buf 指向 10 byte 的 buffer（OOB！）

state_B 報 bug，但 caller 不會這樣呼叫。這是 UCSE 典型 FP。

**工程對策**：
1. 手動加 caller-enforced invariant：`__klee_assume(buf_size >= n)`
2. 用 **function summary**：先分析 caller，萃取出它對 callee 的保證
3. Cross-check（Wild Fire 方式）

## UCSE vs Fuzzing with harness

兩者都解「局部分析」問題：

- **UCSE**：完全 symbolic input
- **Harness-based fuzzing**：你寫 harness、手動 populate input

Fuzzing 的 harness 經常也會加 unconstrained input：

```c
// harness.c
void LLVMFuzzerTestOneInput(const uint8_t* data, size_t size) {
    parse_header(&ctx, (char*)data, size);
}
```

ctx 呢？fuzzer 沒動，就是 zeroed stack memory → 實際跑 fuzzing 只探索 ctx = 0 那條 path。Missing many paths。

UCSE 好處：自動 explore 所有 ctx 可能。缺點：FP、慢。

實務：**fuzzing 先、UCSE 後**。fuzzing 找簡單的，UCSE 找 fuzzing 覆蓋不到的 deep path。

## 當前研究方向

- **Compositional symbolic execution**：先對 callee 算 summary、再分析 caller。更好地 propagate precondition
- **UCSE + 神經網路**：學 precondition 而不是手寫
- **UCSE + Taint**：追 attacker-controlled 的 taint 到 sink，精度比純 UCSE 高

這些是 2022+ 的熱點。工具不成熟、paper 多、production 少。

## 實務用 UCSE 的建議

一般 security engineer **很少親手用 UCSE**。它是：

- Research 工具（UC-KLEE、Wild Fire、SymDriverGen）
- Kernel security 專家用（如 Project Zero、Grsecurity）
- 大型 library 的内部 validation（Google、Facebook 可能有）

如果你做 kernel subsystem 的 vuln research，值得學；如果你做 userspace bug hunting，**fuzzing + hybrid 更實用**。

## 心法

UCSE 的核心是 **承認 spec 不完整、仍然做分析**。

- 直接上：報大量 bug，其中多數是 FP
- 加 precondition：變成半自動，有價值但 setup 貴
- Cross-check：自動化 FP 過濾，但需要兩個實作

對普通 task 太重；對 kernel / critical library validation 有獨特價值。

## 自我檢核

- [ ] 解釋 "under-constrained" 的含義（path constraint 比真實 reachability 少）
- [ ] 列 UCSE 的三個經典 FP 來源
- [ ] 解釋 lazy initialization 為什麼對 UCSE 重要
- [ ] 知道 UCSE 跟 harness-based fuzzing 的分工
- [ ] 知道什麼情境值得用 UCSE

下一章把 symex 跟 taint **真的合體** — Triton-style hybrid 的實作策略。

→ [Ch 27 — Symex + taint 聯手：Triton-style hybrid](./27-symex-taint-hybrid.md)
