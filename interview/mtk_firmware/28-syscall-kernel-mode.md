# Ch 28 — system call、user/kernel mode

> **目標**：搞懂 user mode vs kernel mode（特權等級）、system call（user 怎麼請 OS 做事）、trap/context switch 的差別。這把 OS 的「保護」與「核心服務」串起來，也對照 Ch 17 ARM 的特權等級、Ch 14 的中斷。

> **環境**：概念為主。前置：Ch 14（中斷/例外）、Ch 17（ARM 特權等級）、Ch 20（process）。

## 為什麼考這個

「user mode 和 kernel mode 差在哪」「system call 怎麼運作」是 OS 面試常考——它測你懂不懂「OS 怎麼保護自己和硬體、user program 怎麼安全地請 OS 做特權操作」。這也串韌體（Ch 17 ARM 特權等級）和中斷（Ch 14）。

## 先建立直覺：銀行櫃台

```
   user mode（顧客區）：你（應用程式）能做一般的事，但碰不到金庫
      - 不能直接存取硬體、不能直接管記憶體、不能執行特權指令
      - 想做這些事 → 要「請櫃員幫忙」

   kernel mode（櫃員/金庫區）：OS 有完整權限，能碰一切硬體和資源

   system call（填單請櫃員）：顧客（user program）填單（syscall），
      請櫃員（OS）幫忙做特權操作（讀檔、配記憶體、開 process...）
```

核心：**CPU 有兩種特權等級——user mode（受限）和 kernel mode（完整權限）。應用程式跑在 user mode（不能亂碰硬體/記憶體，保護系統）；要做特權操作就透過 system call 請 OS（切到 kernel mode）做。** 這是 OS 保護機制的基礎。

## user mode vs kernel mode

```
   kernel mode（特權模式 / supervisor mode）：
   - 能執行所有指令（含特權指令：關中斷、改 page table、存取硬體）
   - 能存取所有記憶體和硬體
   - OS kernel 跑在這

   user mode（非特權模式）：
   - 只能執行非特權指令
   - 只能存取自己的（虛擬）位址空間（Ch 25）
   - 不能直接碰硬體、不能執行特權指令
   - 應用程式跑在這
```

為什麼要分：**保護**。如果應用程式能直接碰硬體/所有記憶體，一個惡意或有 bug 的程式就能搞垮整個系統、讀別人的記憶體。user mode 限制應用程式的能力，要做危險操作必須透過 OS（OS 會檢查權限）。

硬體支援：CPU 有一個「模式位元」標示當前 mode（如 ARM 的特權等級 Ch 17）。在 user mode 執行特權指令 → 觸發例外（fault），OS 接管。

## system call（系統呼叫）

應用程式要做特權操作（讀檔、配記憶體、建 process、網路...）——自己做不到（user mode 受限），要請 OS。**system call 就是「user program 請 OS 做事」的介面。**

```
   應用程式呼叫 read() / malloc() / fork() ...
        │（這些底層觸發 system call）
        ▼
   觸發 trap（軟體中斷 / 特殊指令，如 ARM 的 SVC、x86 的 syscall）
        │
   CPU 切到 kernel mode、跳到 OS 的 syscall handler
        │
   OS 執行特權操作（檢查權限 → 讀檔/配記憶體/...）
        │
   切回 user mode、返回結果給應用程式
```

關鍵：**system call 透過「trap（軟體觸發的例外）」從 user mode 切到 kernel mode。** 這是「受控的進入點」——user program 不能隨意跳進 kernel，只能透過 syscall（OS 定義好的入口），OS 會檢查參數和權限。常見 syscall：`read`/`write`（檔案 I/O）、`open`/`close`、`fork`/`exec`（process）、`mmap`/`brk`（記憶體）、`socket`（網路）。

> library function vs system call：`printf` 是 C 函式庫函式（在 user mode 格式化字串），它**底層**呼叫 `write` system call 把結果輸出。並非每個函式都是 syscall——很多在 user mode 做完，只在需要 OS 服務時才 syscall。面試問「printf 是 syscall 嗎」答「不是，它是 library function，底層用 write syscall」。

## trap vs interrupt vs context switch（容易混）

```
   interrupt（中斷）：硬體非同步觸發（按鈕、計時器、I/O 完成，Ch 14）
                      → 跳去 ISR

   trap（陷阱）：軟體同步觸發——程式主動執行特殊指令（syscall）
                 或發生錯誤（除以 0、非法存取 → exception/fault）
                 → 跳去 kernel handler、切 kernel mode

   兩者都是「例外（exception）」（Ch 17），都會切 kernel mode、跳 handler。
   差別：interrupt 是「外部硬體非同步」、trap 是「程式內部同步」。

   context switch（上下文切換，Ch 20）：OS 換另一個 process/thread 上 CPU
   ── 不一定和 trap/interrupt 同時發生，但常由它們觸發
      （如 timer interrupt → OS 決定換 process → context switch）
```

三者關係：interrupt/trap 是「進入 kernel 的事件」（切 mode、跳 handler）；context switch 是「換執行單元」（OS 在 kernel 裡決定要不要做）。一個 timer interrupt 可能觸發 OS 做 context switch（排程，Ch 21）。

面試問「system call 和 interrupt 差在哪」：syscall 是軟體主動觸發的 trap（程式請 OS 做事）、interrupt 是硬體非同步觸發（外部事件）。兩者都進 kernel mode。

## mode switch vs context switch（細微但常考）

```
   mode switch（模式切換）：user mode ⇄ kernel mode（同一個 process 內）
      - syscall 進 kernel、返回 user：是 mode switch，不是 context switch！
      - 開銷較小（不換位址空間）

   context switch（上下文切換）：換「另一個 process/thread」（Ch 20）
      - 換暫存器、（process 的話）換位址空間/flush TLB
      - 開銷較大
```

**重點：system call 是 mode switch（user↔kernel，同 process），不是 context switch（換 process）！** 很多人以為 syscall = context switch，錯。syscall 只是同一個 process 從 user mode 切到 kernel mode 做事、再切回來——沒換 process。context switch 是換不同的執行單元（更貴）。這個區別常考。

## 考古題詳解

### Q1：user mode 和 kernel mode 差在哪？為什麼要分？

<details>
<summary>詳解</summary>

- **kernel mode**：完整權限，能執行特權指令、存取所有硬體/記憶體。OS 跑這。
- **user mode**：受限，只能執行非特權指令、只能存取自己的位址空間。應用程式跑這。

為什麼分：**保護**。應用程式不能直接碰硬體/所有記憶體（防惡意/bug 搞垮系統、讀別人記憶體），要做特權操作必須透過 OS（system call），OS 檢查權限。

**考點**：user/kernel mode + 為什麼，必考。
</details>

### Q2：system call 怎麼運作？

<details>
<summary>詳解</summary>

應用程式要做特權操作（讀檔、配記憶體...）→ 觸發 **trap**（特殊指令，如 ARM SVC / x86 syscall）→ CPU 切到 kernel mode、跳 OS 的 syscall handler → OS 檢查權限、執行操作 → 切回 user mode、返回結果。

關鍵：syscall 是「受控的進入點」——user program 只能透過 OS 定義的 syscall 入口進 kernel，不能隨意跳進去。

**考點**：syscall 流程，必考。
</details>

### Q3：printf 是 system call 嗎？

<details>
<summary>詳解</summary>

**不是。** printf 是 **C 函式庫函式**——它在 user mode 做字串格式化（把 `%d` 換成數字等），格式化好後**底層呼叫 `write` system call** 把結果輸出到 stdout。

所以 printf 包含「user mode 的格式化」+「一個 write syscall」。並非每個函式都是 syscall——很多事在 user mode 做完，只在需要 OS 服務（I/O、記憶體、process）時才 syscall。

**考點**：library function vs system call，常考。
</details>

### Q4：system call 是 context switch 嗎？

<details>
<summary>詳解</summary>

**不是。** system call 是 **mode switch**（同一個 process 從 user mode 切到 kernel mode 做事、再切回），**沒有換 process**。

context switch 是「換另一個 process/thread」（換暫存器、process 的話換位址空間/flush TLB，Ch 20）——比 mode switch 貴。

很多人誤以為 syscall = context switch。實際 syscall 只是同 process 內的 mode 切換（較便宜）。

**考點**：mode switch vs context switch，常考易錯。
</details>

### Q5：trap 和 interrupt 差在哪？

<details>
<summary>詳解</summary>

兩者都是例外（Ch 17），都切 kernel mode、跳 handler。差別：
- **interrupt（中斷）**：硬體**非同步**觸發（外部事件：按鈕、計時器、I/O 完成，Ch 14）。
- **trap（陷阱）**：軟體**同步**觸發——程式主動（system call）或錯誤（除0、非法存取 → fault）。

interrupt = 外部硬體非同步；trap = 程式內部同步。system call 是一種 trap。

**考點**：trap vs interrupt，串 Ch 14/17。
</details>

## 踩雷集錦

1. **以為 syscall = context switch**：syscall 是 mode switch（同 process，user↔kernel），不換 process。context switch 才是換 process（更貴）。
2. **以為每個函式都是 syscall**：很多是 library function（user mode 做完）；只有需要 OS 服務時才 syscall。printf 底層才有 write syscall。
3. **以為 user mode 能直接碰硬體**：不行（受限）。要透過 syscall 請 OS。
4. **trap 和 interrupt 不分**：trap 軟體同步（syscall/錯誤）、interrupt 硬體非同步（外部事件）。
5. **不懂為什麼要分 mode**：保護——防應用程式搞垮系統、亂讀記憶體。
6. **以為 kernel mode 是某個 process**：mode 是 CPU 的狀態（特權等級），不是 process。同一 process 可在 user 和 kernel mode 之間切（透過 syscall）。

## 速記

- **user mode**（受限，跑應用，不能碰硬體/特權指令）vs **kernel mode**（完整權限，跑 OS）。分 mode 是為**保護**。
- **system call**：user program 請 OS 做特權操作的介面；透過 **trap**（特殊指令）切 kernel mode、OS 執行、切回。受控進入點。
- **printf 不是 syscall**（是 library function，底層用 write syscall）。
- **syscall 是 mode switch（同 process，user↔kernel）≠ context switch（換 process）**。
- **trap**（軟體同步：syscall/錯誤）vs **interrupt**（硬體非同步：外部事件，Ch 14）；都進 kernel mode。

## 自我檢核

- [ ] user mode 和 kernel mode 差在哪？為什麼要分？
- [ ] system call 怎麼從 user mode 進到 kernel mode？
- [ ] printf 是 system call 嗎？為什麼？
- [ ] system call 是 context switch 嗎？mode switch 和 context switch 差在哪？
- [ ] trap 和 interrupt 差在哪？

## 延伸閱讀

### 書籍

- **《Operating System Concepts (恐龍書)》** — Ch 1.5 (dual mode)、Ch 2.3 (system calls)
  - **讀哪幾章**：1.5（user/kernel mode）、2.3（system call 介面）。
  - **和本章的關聯**：mode 與 syscall 的標準教材。

- **《Computer Systems: A Programmer's Perspective (CSAPP)》** — §8.1–8.2 Exceptions
  - **讀哪幾章**：8.1（exception 類型：interrupt/trap/fault/abort）、8.2（process）。
  - **為什麼值得讀**：把 trap/interrupt/exception 的分類講得最清楚。

### 文章

- **[面試紀錄 & 練習（聯發科）— HackMD](https://hackmd.io/@chiangkd/interview)**
  - **讀哪裡**：user/kernel mode、syscall 題。
  - **和本章的關聯**：MTK 的 OS 考點。

Part 3（OS）寫完了！用練習 C 把 OS 考點綜合驗收。

→ [練習 C：OS 綜合考古題](./practice-c-os-questions.md)
