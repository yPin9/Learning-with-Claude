# Ch 20 — Egghunter 原理與實作

> **目標**：搞懂 egghunter（egg hunter）這段極小 stub 怎麼在不知道目標 shellcode 位址的情況下，安全地掃過整個行程的虛擬位址空間找到它——重點是「安全」兩個字：怎麼避免掃到未對應頁面時直接 access violation 讓整支 exploit 死掉。

> **環境**：Win10 x64 + WinDbg（2026）+ mona v3 + Python 3

## 為什麼需要這一章

Ch 19 講完「空間不夠放完整 shellcode」有四種解法，egghunter 是最常被考的那一個：**用一段約 32 bytes 的極小 stub，去行程記憶體裡找一個 8-byte 的標記（egg），找到後跳過去執行貼在 egg 後面的完整 shellcode**。這個概念聽起來簡單，但魔鬼在細節——egghunter 要做的事，本質上是**盲目掃描一整塊它完全不了解佈局的記憶體**，而 Windows 的虛擬位址空間裡大部分頁面根本沒有被對應（mapped）。直接去讀一個沒對應的位址，CPU 會丟 access violation，而這個違規發生在你的 shellcode 裡——沒有 exception handler 接住它，服務直接崩潰，整支 exploit 失敗。

所以 egghunter 真正的核心工程問題不是「怎麼找 8 bytes」（那是個簡單的字串比對迴圈），而是：**怎麼在掃描每個候選位址之前，先確認這個位址的頁面可讀，避免自己把自己炸死**。這一章要把這個機制講清楚。

## 先建立直覺

```
行程虛擬位址空間（32-bit，簡化示意，實際遠比這張圖碎）

0x00000000 ┌──────────────┐
           │ 未對應 (不可讀) │  ← 直接讀會 access violation
           ├──────────────┤
           │   已對應頁面   │  ← 可讀，但大概率不是我們要的 egg
0x00401000 ├──────────────┤
           │   未對應      │
           ├──────────────┤
           │   已對應頁面   │ [egg][egg][shellcode......]  ← 目標！
0x0073A000 ├──────────────┤      ↑
           │   未對應      │    找到了，jmp 到這裡之後執行
           ├──────────────┤
0xFFFFFFFF └──────────────┘

egghunter 的工作流程：
  從某個起始位址開始，每次跳 PAGE_SIZE (0x1000)
    │
    ▼
  這個位址的頁面可讀嗎？ ──否──► 跳到下一頁 (+0x1000)，重試
    │是
    ▼
  這 8 bytes 是不是 egg+egg？ ──否──► 位址 +4，繼續在這頁內找
    │是
    ▼
  jmp 到 egg 後面那個位址，開始執行真正的 shellcode
```

關鍵的「否」那條分支——怎麼判斷「可讀嗎」而不直接讀下去——就是整章要拆的東西。

## 核心：egghunter 要解決的兩個子問題

1. **怎麼安全地測試一個位址是否可讀**（不會自己把自己弄死）。
2. **怎麼有效率地掃過足夠大的位址空間**（不要每個 byte 都測試，通常以頁為單位跳）。

### 子問題 1：安全測試頁面可讀性——兩種經典 syscall 手法

Windows 的 `ntdll.dll` 裡有一批系統呼叫（native API，前綴 `Nt...`），它們的本職工作跟「記憶體讀寫是否合法」完全無關，但有個副作用：**呼叫時會先對傳入的記憶體位址做存取檢查，如果位址不可讀，它會回傳一個特定的錯誤碼，而不是讓呼叫者直接噴 access violation**。egghunter 就是利用這個副作用——故意呼叫一個這樣的 syscall，把要測試的位址當參數傳進去，靠系統呼叫自己的檢查邏輯來判斷可讀性，而不是自己去讀那塊記憶體。

**(A) `NtAccessCheckAndAuditAlarm` 法（Corelan / skape 經典版）**

這是 Corelan egghunter 最常用的版本，原始想法來自 skape 2004 年的論文（見延伸閱讀）。`NtAccessCheckAndAuditAlarm` 原本是核心用來做存取控制審計的函式，它需要好幾個指標參數，其中一個會指向使用者傳入的記憶體。呼叫這個 syscall 時：

- 如果某個傳入的位址**不可讀**，syscall 會在進入核心模式做參數驗證時發現，回傳 `STATUS_ACCESS_VIOLATION`（錯誤碼 `0xC0000005`）給呼叫端，**而不會真的讓你的行程崩潰**——因為違規發生在核心驗證參數的階段，核心自己處理掉了，只是把結果透過 `EAX` 回傳。
- 如果位址**可讀**，這個 syscall 的其他參數大概率也不滿足它原本的業務邏輯（我們根本沒打算真的做存取審計），所以會回傳其他錯誤碼——但只要不是 `0xC0000005`，egghunter 就知道「這頁至少可讀，可以放心去比對 egg」。

egghunter 檢查的方式是看 `EAX` 回傳值：是 `0xC0000005` 就跳過這整頁（通常跳到下一個 `0x1000` 邊界），不是就繼續比對這個位址開始的 8 bytes 是不是 egg。

**(B) `NtDisplayString` 法**

另一個常見替代是 `NtDisplayString`，它的用途是在開機/核心層顯示字串（跟我們的目的一樣毫無關係），但一樣會在核心驗證傳入的字串指標時做存取檢查，行為模式與 (A) 相同：位址不可讀回傳 `0xC0000005`，egghunter 照樣用 `EAX` 判斷。選哪一個通常取決於 bad chars（兩者產生的機械碼不同，某個函式的 syscall number 或相關位元組可能恰好撞到你的壞字元清單）或純粹的篇幅考量（哪個版本編出來更短）。

> **為什麼叫「安全搜尋」**：skape 2004 年的論文標題正是《Safely Searching Process Virtual Address Space》——"safely" 指的就是這個機制：把本來會讓行程崩潰的記憶體探測，轉換成一個會被核心攔下來、安全回傳錯誤碼的 syscall 呼叫。這是整個 egghunter 技巧的理論源頭，OSED 教材與 Corelan 的實作都是在這篇論文的思路上發展出來的。

### 子問題 2：怎麼有效率地掃

逐個 byte 測試可讀性太慢也沒必要——x86 分頁機制的最小單位是一頁 **`PAGE_SIZE = 0x1000`（4096 bytes）**，同一頁內的位址可讀性是一致的（都可讀或都不可讀，不會同頁一半可讀一半不可讀）。所以 egghunter 的外層迴圈通常是：

- 每次位址 **以 `0x1000` 為單位**往上跳，呼叫 syscall 驗證這一頁的起始位址可讀。
- 可讀的話，**內層迴圈**才真的去逐個位址（或以 4 bytes 為粒度）比對 8-byte egg 標記，直到比對成功、或走到這一頁的邊界再跳下一頁。

這個「大跳頁、頁內細找」的兩層結構，是所有經典 egghunter 版本共通的骨架。

## 底層機制：掃描流程全貌

```
   ┌─────────────────────────────┐
   │ 設定起始位址 (EDI/EAX 等)      │
   └──────────────┬──────────────┘
                  ▼
   ┌─────────────────────────────┐
   │ 位址對齊到下一個 PAGE_SIZE 邊界 │  ← 避免同一頁內重複呼叫 syscall
   └──────────────┬──────────────┘
                  ▼
   ┌─────────────────────────────┐
┌─►│ 呼叫 NtAccessCheckAndAuditAlarm │
│  │ (或 NtDisplayString)，傳入目前位址│
│  └──────────────┬──────────────┘
│                 ▼
│  ┌─────────────────────────────┐
│  │ EAX == 0xC0000005 (不可讀)？  │
│  └──┬───────────────────────┬──┘
│    是                      否
│    ▼                       ▼
│  ┌───────────┐   ┌─────────────────────┐
│  │位址+0x1000 │   │比對這個位址開始的8 bytes│
│  │(跳下一頁)  │   │是不是 egg+egg          │
│  └─────┬─────┘   └──────┬──────────────┘
│        │               不符合              符合
│        │                ▼                  ▼
│        │        ┌──────────────┐  ┌──────────────────┐
│        │        │ 位址 +4，留在  │  │ jmp 到 egg 之後的   │
│        │        │ 這頁內繼續找   │  │ 位址，執行真shellcode│
│        │        └──────┬───────┘  └──────────────────┘
│        │               │
└────────┴───────────────┘
      （回到外層迴圈，繼續掃下一輪）
```

這張圖裡「比對這個位址開始的 8 bytes」這一步值得多說一句：為什麼 egg 要設計成 **8 bytes、且是同一個 4-byte 標記重複兩次**（例如預設值 `w00tw00t`，也就是 `w00t` 重複一次）？因為單純比對 4 bytes 太容易在正常資料裡誤判（隨機撞到剛好等於 `w00t` 的機率不低），要求連續兩次重複大幅降低誤判率，同時掃描邏輯只需要「讀 4 bytes、比較、再讀下 4 bytes、再比較」這麼簡單的兩次比對，不需要更複雜的字串搜尋演算法。

## 另一條路：SEH-based egghunter

除了用 syscall 繞過違規，另一種思路更直接——**自己裝一個例外處理常式（exception handler），讓它接住 access violation 本身**：egghunter 開頭先用 `push`/`pop` 之類的手法把自己的一小段 handler 位址放進 SEH chain（在 `fs:[0]` 指向的結構上），然後直接大膽去讀記憶體；如果踩到不可讀頁面，CPU 丟出的例外會被自己裝的 handler 接住，handler 做的事就是「把目前掃描位址跳到下一頁，然後恢復執行」，而不是讓 Windows 的預設行為（服務崩潰）發生。

這個手法的優點是不依賴特定 syscall（也就不受那個 syscall 機械碼裡的 bad chars 影響），缺點是裝 SEH handler 本身的程式碼通常比 syscall 版本更長、更複雜，而且牽涉到的暫存器與堆疊操作更細膩，寫錯容易連自己的 exploit 流程都弄壞。OSED 教材與 mona 產生的預設版本以 syscall 法（尤其 `NtAccessCheckAndAuditAlarm`）為主流，SEH-based 版本通常留作「syscall 法踩到壞字元走不通」時的備案。

## 拆解一個經典 egghunter（逐指令，未實測）

下面是 Corelan 風格 egghunter 的典型邏輯拆解（具體機械碼依 mona 產生版本、暴露的暫存器配置會有差異，這裡講的是**邏輯骨架**，不是逐 byte 的權威版本）：

```asm
; 標注：未實測，理論預期——邏輯對照 skape 論文與 Corelan 公開版本的敘述，
; 實際機械碼請以 mona 產生的版本為準，並在自己的 VM 裡用 WinDbg 單步驗證。

start:
    xor  ecx, ecx        ; ECX 清零，之後用來墊 syscall 的部分參數
    mov  edi, 0x00000004  ; 之類的手法把起始搜尋位址放進可用暫存器
                          ; 並確保低位對齊到接下來要用的頁邊界
next_page:
    or   edi, 0x00000fff  ; 把 EDI 的低 12 bits 全部設成 1
                          ; 等於「跳到下一個 0x1000 頁邊界再減 1」
    inc  edi              ; +1 之後 EDI 正好落在下一頁的起始位址
                          ; 這是用兩條指令湊出「頁對齊」的常見省 bytes 寫法
check_page:
    push edi               ; 把目前要測試的位址推進堆疊，當 syscall 參數
    push 0x0000000a        ; (示意) 準備其他固定參數
    push edi
    mov  eax, 0x...        ; 載入 NtAccessCheckAndAuditAlarm 的 syscall number
    int  0x2e              ; (或等價觸發方式) 進核心呼叫
    cmp  eax, 0xc0000005   ; 檢查回傳是不是 access violation 錯誤碼
    jz   next_page         ; 是的話，這頁不可讀，跳回去對齊下一頁重試
compare_egg:
    mov  eax, 0x74303077   ; 'w00t'，egg 標記的 4 bytes（小端序存成這個常數）
    mov  edx, edi           ; 把目前候選位址存一份
    scasd                   ; 比較 EDI 指向的 4 bytes 跟 EAX，EDI 自動 +4
    jnz  check_page         ; 不符合，位址已經 +4，回頭當作新位址繼續測
    scasd                   ; 再比一次（第二個 'w00t'），確認是完整 8-byte egg
    jnz  check_page         ; 不是重複兩次，不算真正的 egg，繼續找
found_egg:
    jmp  edi                ; EDI 現在正好指在 egg 後面，直接跳過去執行
                            ; 真正的 shellcode 就在這裡開始
```

幾個值得注意的設計點：

- `or edi, 0xfff` + `inc edi` 是用極短指令湊出「頁對齊」計算的常見手法——OSED/shellcode 寫作普遍追求指令數最少、bytes 最省，這類「用算術技巧換一條指令」的寫法到處都是。
- `scasd` 指令本身會自動把 `EDI` 往前移 4 bytes，天然適合拿來做「比較 4 bytes、同時遊標往前走」這個動作，寫 egghunter 幾乎是標配。
- syscall number（`mov eax, 0x...` 那行）**在不同 Windows 版本之間會變動**——這是 `Nt*` 系列原生 API 最大的陷阱，一定要針對目標系統版本用 mona 或實機查出正確數值，不能照抄另一個 Windows 版本的教學。

## mona 自動產生 egghunter

手刻以上邏輯是為了理解機制，實戰（和考場）大多直接讓 mona 產：

```
!py mona egg -t w00t
```

`-t w00t` 指定 4-byte tag（egg 的一半，mona 會自動重複兩次組成 8-byte egg），mona 會告訴你 egg 標記完整的 8 bytes、並把它放進之後產生的 payload 時要注意的位置。

```
!py mona egghunter -eggtag w00t
```

這條會直接產出一段完整的 egghunter stub（機械碼 + 組語註解），預設通常就是 `NtAccessCheckAndAuditAlarm` 版本。拿到輸出後第一件事：**過一輪壞字元分析**（Ch 8 的流程）——egghunter 雖然短，一樣可能含壞字元，mona 有時提供替代版本或你需要手動調整幾條指令，這是 Ch 21 的主題。

## 驗證方法：怎麼在 WinDbg 裡確認 egghunter 真的在動

因為這段機械碼無法在本機脫離實際 Windows 行程空間跑（本章所有 bytes 都標了「未實測」），實戰驗證一定要在你自己的 VM 裡動手做，流程大致是：

1. 用 mona 產生 egghunter，把它放進一個你能完全控制、容易重現的小測試程式（或直接放進 vulnserver 的某個欄位），先確認它被正確送進目標行程記憶體。
2. WinDbg 附掛（Ch 4）之後，在 egghunter 的第一條指令下斷點（`bp` 配上你算出的位址，或用 `u` 反組譯確認落點）。
3. 用 `t`（trace，單步）而不是 `g` 一路往下走，每走一步看暫存器（尤其 `EDI`／目前候選位址，和 `EAX`／syscall 回傳值）怎麼變化。
4. 重點觀察兩個時刻：**遇到不可讀頁面時 `EAX` 是不是真的變成 `0xC0000005`，而且程式沒有崩潰**；以及**找到 egg 時 `jmp` 的目的位址是不是真的落在你放 shellcode 的地方**。
5. 如果中途真的噴了 access violation（不是 syscall 回傳的那種，而是 WinDbg 直接停在一個 `!exchain` 查不到 handler 的例外），通常代表 syscall number 錯誤、或某個暫存器在你移植機械碼時被用壞了——回頭比對 mona 原始輸出，逐條指令核對。

這個流程本身就是 OSED 考場排錯 egghunter 最常用的手法：**不要整段跑，單步看它每一步在哪個位址、判斷結果是什麼**，比對照理論描述猜測快得多。

## 對比取捨：兩種經典 syscall 法 + SEH 法

| 方法 | 機制 | 優點 | 缺點 |
|---|---|---|---|
| `NtAccessCheckAndAuditAlarm` | syscall 回傳 `0xC0000005` 判斷可讀性 | Corelan/mona 預設、資料最多、業界最常見 | 機械碼裡可能含特定壞字元；syscall number 隨版本變 |
| `NtDisplayString` | 同機制，不同 syscall | 機械碼不同，可當 bad char 衝突時的替代 | 同樣受版本差異影響；較少公開範例可對照 |
| SEH-based | 自裝 exception handler 接住 access violation | 不依賴特定 syscall 的機械碼/壞字元 | 程式碼更長更複雜，牽涉堆疊/SEH chain 操作，寫錯風險高 |

## 踩雷

1. **syscall number 抄錯 Windows 版本**：`Nt*` 原生 API 的 syscall number 不是 ABI 保證穩定的東西，Win7/Win10/Win11 之間可能不同。沒有在**目標機器**上重新確認，整個 egghunter 會在第一步就壞掉（判斷結果全錯）。
2. **egg 標記本身含壞字元，或跟正常資料衝突**：挑 tag 前要先過一輪壞字元分析，也要確認這 8 bytes 不會剛好在協定正常流量裡大量出現（降低誤判機率）——Ch 21 會細講挑選策略。
3. **忘記頁對齊，逐 byte 呼叫 syscall**：如果沒做 `0x1000` 對齊，每個候選位址都重新呼叫一次 syscall，效率極差，掃描可能慢到讓人誤以為 egghunter 沒在動。
4. **把"理論預期"的 bytes 當成可以直接貼上戰場的成品**：本章拆解的組語是邏輯骨架，不是權威機械碼——真正要用的版本必須用 mona 在目標系統上現場產生，並在 WinDbg 單步跑過一輪確認行為。
5. **以為 egghunter 掃描是瞬間完成**：掃過幾百 MB 到數 GB 的位址空間需要時間，別把「晚個幾百毫秒才連上 shell」誤判成 exploit 失敗而提前放棄除錯。

## 進階：再往深一層

skape 的原始論文其實討論了比本章更多的安全探測手法（不只這兩個 syscall），而且是 2004 年寫的——在那之後 Windows 版本一路演進，哪些手法還能用、syscall number 怎麼對應，都需要在現行系統上重新驗證，不能假設二十年前的細節原封不動延續到今天。這也是為什麼 OSED 現場強調「用 mona 當場產生、自己在 WinDbg 裡驗證」而不是背一份固定的 bytes 清單。

## 本章重點整理

- egghunter 真正的工程難點是「安全探測記憶體可讀性」，不是「怎麼比對 8 bytes」。
- 兩種經典 syscall 法：`NtAccessCheckAndAuditAlarm`、`NtDisplayString`，都是利用核心做參數驗證時的副作用，把 access violation 轉成安全的錯誤碼回傳（`EAX = 0xC0000005`）。
- 掃描是兩層迴圈：外層以 `PAGE_SIZE (0x1000)` 跳頁驗證可讀性，內層在可讀頁內逐段比對 egg。
- 8-byte egg = 4-byte tag 重複兩次（如 `w00tw00t`），降低誤判率同時讓比對邏輯極簡單。
- SEH-based egghunter 是另一條不依賴特定 syscall 的替代路線，代價是程式碼更複雜。
- mona 指令：`!py mona egg -t <tag>` 產 egg，`!py mona egghunter -eggtag <tag>` 產完整 stub。

## 自我檢核

- [ ] 能解釋為什麼直接讀記憶體測試可讀性會有危險，syscall 法怎麼繞開這個危險
- [ ] 能說出 `EAX` 回傳 `0xC0000005` 在這個機制裡代表什麼
- [ ] 能說出為什麼 egg 要設計成 8 bytes、重複兩次的 4-byte tag
- [ ] 能說出 egghunter 掃描為什麼要以 `PAGE_SIZE` 為單位跳頁，而不是逐 byte 探測
- [ ] 能說出 SEH-based egghunter 和 syscall-based 的核心機制差異與取捨
- [ ] 能說出 mona 產 egghunter 的指令，以及拿到輸出後第一件該做的事

## 延伸閱讀

- **skape — "Safely Searching Process Virtual Address Space"（2004）**
  - **讀哪裡**：全文，尤其是討論各種安全探測手法（含本章兩個 syscall 法的理論源頭）的章節。
  - **學什麼**：egghunter 整個技巧體系的原始出處與理論基礎，本章機制講解的根本依據。
  - **前提**：對 Windows 記憶體管理與 syscall 機制有基本概念即可，建議配合本章一起讀。
- **[Corelan — Exploit Writing Tutorial part 8: Win32 Egg Hunting](https://www.corelan.be/index.php/2010/01/09/exploit-writing-tutorials-part-8-win32-egg-hunting/)** — Peter Van Eeckhoutte
  - **讀哪裡**：全文，尤其是不同 egghunter 版本（`NtDisplayString`、`NtAccessCheckAndAuditAlarm`、SEH-based）的逐段比較。
  - **學什麼**：本章對比表格的直接依據，以及 mona 產生 egghunter 的實務細節。
  - **前提**：先讀完 Ch 19。
- **[Connor McGarr — Exploit Development blog](https://connormcgarr.github.io/)** — Connor McGarr
  - **讀哪裡**：站內 egghunter 相關文章，通常有更現代的 WinDbg 單步示範。
  - **學什麼**：在現行 Windows 版本上重新驗證 egghunter 行為的實務流程。
  - **前提**：先讀完本章，熟悉 WinDbg 單步操作（Ch 4–5）。
- **[corelan/mona（GitHub）](https://github.com/corelan/mona)** — Corelan
  - **讀哪裡**：`egg`、`egghunter` 指令的原始碼與說明。
  - **學什麼**：mona 產生 egghunter 時實際支援哪些變體、參數選項。
  - **前提**：環境已裝好 mona v3（Ch 0）。

egghunter 的機制講完了，下一章要把它從「理論骨架」調成「能真正打進考場的版本」——壞字元調校、egg 標記挑選、放置策略，以及它失敗時怎麼用 WinDbg 單步除錯。

→ [Ch 21 客製 egghunter 與受限字元調校](./21-custom-egghunter.md)
