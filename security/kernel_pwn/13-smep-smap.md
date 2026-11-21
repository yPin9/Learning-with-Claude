# Ch 13 — SMEP / SMAP：ret2usr 封死後的 kernel ROP 與 stack pivot

> **目標**：把 [Ch 11](./11-stack-overflow.md) 刻意關掉的 SMEP、SMAP 加回來，看它們怎麼一刀斬斷 ret2usr，然後學會兩個「全程待在 kernel space」的替代打法——kernel ROP 與 stack pivot。學完你要能解釋這兩道牆各擋什麼、為什麼「把 shellcode 放 user page」和「從 kernel 讀 user buffer」同時變得不可行，以及現代 exploit 為什麼寧可湊一條 kernel 內的 ROP 鏈也不碰 user memory。

> **環境**：Linux 6.6 LTS、x86-64、QEMU。本章把 SMEP/SMAP 打開（移除 Ch 0 flag 表裡的 `nosmep nosmap`，或用 `-cpu` 帶 `+smep,+smap`），KASLR 暫時仍關（`nokaslr`）以便專注在 SMEP/SMAP。exploit code 未在本環境實測，標為理論預期行為。

## 為什麼需要這兩道牆

ret2usr（[Ch 11](./11-stack-overflow.md)）之所以好用，踩在兩個假設上：

1. kernel 可以**執行** user-space page 上的指令（所以能跳去 `escalate()`）。
2. kernel 可以**讀寫** user-space 的資料（所以能從 user buffer 拿 payload、函式指標）。

這兩個假設在 2010 年代初都還成立——於是 ret2usr 是當年的萬用招。SMEP（Supervisor Mode Execution Prevention，Intel 2012 Ivy Bridge 引入）打掉假設 1；SMAP（Supervisor Mode Access Prevention，2014 Broadwell 引入）打掉假設 2。兩道牆都由 CPU 硬體在 ring 0 強制，kernel 只負責在開機時把對應的 CR4 bit 打開。

```
  CR4 暫存器（部分 bit）
  ┌───────────────────────────────┐
  │ ... │ SMAP(21) │ SMEP(20) │ ...│
  └───────────────────────────────┘
       bit=1 時硬體強制對應保護
```

它們的共同設計哲學：**ring 0 不該碰 ring 3 的東西**。kernel 正常運作時本來就不需要執行 user 指令；要讀 user 資料也該走 `copy_from_user` 這類**受控**介面（這介面會臨時解除 SMAP，見下）。所以把「ring 0 直接碰 user memory」整個禁掉，對正常 kernel 幾乎無痛，對攻擊者卻是釜底抽薪。

## 先建立直覺：牆立在哪

```
            ring 0 (kernel) 想做的事          SMEP   SMAP
  ──────────────────────────────────────    ─────  ─────
  跳去 user page 執行 shellcode  ─────────▶   擋!     —     ← ret2usr 執行面死在這
  讀 user buffer 當 payload/指標 ─────────▶    —    擋!     ← ret2usr 資料面死在這
  在 kernel text 內跳來跳去(ROP) ─────────▶    過     過     ← 替代路：全程 kernel space
  copy_from_user() 讀 user 資料  ─────────▶    —    放行*   ← *靠 stac/clac 臨時開窗
```

結論一句話：**SMEP/SMAP 把 user memory 變成 kernel 的禁區，於是攻擊必須「全程留在 kernel space」。** 你的 payload 要擺在 kernel 能合法碰的地方、你的指令要是 kernel 自己的指令。這就逼出了 kernel ROP。

## SMAP 不是全封：stac / clac 的縫

SMAP 開著，kernel 卻還是得讀 user 資料（`copy_from_user` 總得運作）。解法是：合法的 uaccess 路徑會用 `stac`（set AC flag）**臨時關掉** SMAP、拷完再 `clac` 開回來。

```
copy_from_user:
    stac                 ; 暫時解除 SMAP
    rep movsb            ; 真正搬資料
    clac                 ; 恢復 SMAP
```

這對攻擊者的意義：你**不能**在任意 kernel RIP 控制點直接解 user 指標，但若能劫持到一段「本來就夾在 stac/clac 之間」的邏輯、或湊出 `stac` gadget，就有機會開窗。真實題偶爾利用這點，但更常見的是乾脆不碰 user memory——把一切搬進 kernel。

> [Ch 4](./04-syscall-boundary.md) 講 uaccess 時留的那個伏筆（為什麼不能直接解 user pointer）在這裡收口：不只是「邏輯上該用 copy_from_user」，是**硬體層 SMAP 會打你**，除非走 stac/clac 開過窗的路徑。

## 替代打法一：kernel ROP

既然不能跳 user page，那就**只用 kernel 自己的指令**。和 user-space ROP 同理：在已載入的可執行記憶體（kernel text、已載入模組）裡找以 `ret` 結尾的小片段（gadget），用蓋在 stack 上的一串位址把它們串起來，拼出你要的操作。

要拼的目標通常是「用 ROP 呼叫 `commit_creds(prepare_kernel_cred(NULL))`」：

```
  被蓋掉的 saved RIP 之後，stack 上排一串 gadget 位址：
  ┌────────────────────────────┐
  │ pop rdi ; ret              │  → 下一格進 rdi
  │ 0 (NULL)                   │     prepare_kernel_cred 的參數
  │ prepare_kernel_cred        │  → 回傳值在 rax
  │ pop rcx ; ret / mov rdi,rax│  → 把 rax 搬進 rdi（視 gadget 而定）
  │ commit_creds               │  → commit_creds(那份 cred)
  │ <KPTI 回程 trampoline>     │  → swapgs_restore_regs_and_return_to_usermode (Ch 15)
  │ ... iretq frame ...        │
  └────────────────────────────┘
```

難點在「把 `prepare_kernel_cred` 的回傳值（rax）接到 `commit_creds` 的參數（rdi）」——需要一個 `mov rdi, rax ; ret` 或等效的搬運 gadget，這類 gadget 不一定好找，常要退而求其次用幾個 gadget 接力。找 gadget 的工具：對你的 vmlinux 跑 `ROPgadget --binary vmlinux` 或 `ropper`。

```bash
# 對你自己的 vmlinux 抽 gadget（位址是未 KASLR 的靜態位址；
# 開 KASLR 時全部要加上 leak 出來的 base，見 Ch 14）
ROPgadget --binary vmlinux | grep ": pop rdi ; ret"
ROPgadget --binary vmlinux | grep "mov rdi, rax"
```

> gadget 的實際位址**完全依賴你那份 vmlinux 的 build**——不同 config/編譯器會排出不同 gadget。所以本章所有 gadget 都以符號描述，標「依你的 vmlinux 用 ROPgadget 抽出、填入實際 offset」。照抄別人 writeup 的 gadget 位址幾乎一定錯。

## 替代打法二：stack pivot

kernel ROP 有個前提——你得能在 stack 上**連續排一長串** gadget 位址。但很多 bug 給你的控制很有限：也許只夠蓋一兩個 8-byte，或你的大段可控資料在 **heap**（某個 kmalloc buffer）而不在 stack。這時用 **stack pivot**：先用一兩個 gadget 把 `rsp` 轉指到你**真正塞滿 ROP 鏈的那塊可控記憶體**，之後 `ret` 就沿著你的鏈跑。

```
  有限的 stack 控制          可控的大塊記憶體（heap 上的 kmalloc buffer）
  ┌──────────────┐          ┌─────────────────────────────┐
  │ saved RIP →  │          │ gadget1 │ gadget2 │ ... 整條 │
  │ pivot gadget │──┐       │ ROP 鏈都在這                 │
  └──────────────┘  │       └─────────────────────────────┘
                    │  pivot：mov rsp, <此塊位址> ; ret        ▲
                    └─────────────────────────────────────────┘
                       之後 ret 就沿著這條完整的鏈走
```

常見 pivot gadget：`mov rsp, rXX ; ret`、`xchg rax, rsp ; ret`、`leave ; ret`（把 rbp 搬進 rsp）、或 `add rsp, <大常數> ; ret`。挑哪個取決於「bug 當下哪個暫存器指向你的可控塊」。stack pivot 是把「短控制」接到「長 payload」的橋，heap 類漏洞（Part 3）尤其常用。

## 老招與它為何被封：寫 CR4 關 SMEP

早年還有一招更直接：既然 SMEP 由 `CR4.SMEP` 控制，那就 ROP 呼叫 `native_write_cr4()`、把那個 bit 清掉，SMEP 當場失效，然後回頭用 ret2usr。這招曾經很流行。

它後來被**CR4 pinning**（約 2019 進主線）封掉：kernel 把 SMEP/SMAP 等「開了就不該關」的 CR4 bit 標記為 pinned，`native_write_cr4` 若偵測到有人想清掉 pinned bit，會把它**強制補回**。於是「寫 CR4 關 SMEP」在新 kernel 上通常無效（寫進去又被改回來）。

> 這是認識論誠實的好例子：很多舊教材還在教「ROP 到 native_write_cr4 關 SMEP」。它在**夠舊的 kernel** 仍可能有效，在 **CR4 pinning 之後的 kernel 無效**。遇到一題先確認目標 kernel 版本有沒有 pinning，別把這招當常青樹。（版本相關，未逐版實測。）

## ret2dir：用 physmap 繞過 SMEP 的「執行面」

還有一條思路值得知道（細節在 physmap 與 Dirty Pagetable 章展開）：SMEP 禁止 kernel 執行 **user page**，但 kernel 的 **direct map / physmap**（[Ch 6](./06-kernel-memory-layout.md)）把所有實體記憶體都映在 kernel space——包括你 user-space 那塊 shellcode 所在的實體頁。於是同一塊實體記憶體有兩個位址：一個 user 虛擬位址（SMEP 禁止執行）、一個 kernel physmap 位址（SMEP 不管，因為它是 kernel 位址）。把 RIP 導到 physmap 裡對應你 shellcode 的那個 kernel 位址，就繞過了 SMEP 的執行面——這就是 ret2dir。它後來也被 physmap 不可執行化（把 direct map 設為 NX）等 mitigation 針對。先建立這個「同一塊實體、兩個視角」的概念，Ch 6 與 [Ch 23](./23-dirty-pagetable.md) 會反覆用到。

## 踩雷集錦

1. **以為關了 `nosmep` 就能順便驗 SMAP**——SMEP 和 SMAP 是**兩個獨立的 bit、兩道獨立的牆**。只開 SMEP 時 ret2usr 的「執行」死了但「讀 user buffer」還活；只開 SMAP 反之。要分別測，才知道你的招被哪一道擋。Ch 0 flag 表的 `nosmep`/`nosmap` 要能分開切。
2. **照抄別人的 gadget 位址**——gadget 位址綁死在特定 vmlinux build。拿別題的位址貼過來，輕則跳進亂碼 panic，重則你以為 exploit 邏輯錯其實只是 gadget 不對。永遠對**你自己的 vmlinux** 抽 gadget。
3. **忘了 ROP 鏈最後要接回程**——ROP 呼叫完 `commit_creds` 不會自己回 user，鏈尾一定要接 KPTI trampoline + iretq frame（[Ch 15](./15-kpti.md)），否則提權成功卻卡在 kernel panic。
4. **stack pivot 後找不到鏈**——pivot 的目標位址要真的指向你排好 ROP 的那塊，且那塊在 pivot 當下**還活著、內容還在**。heap 上的可控塊若在觸發 bug 時已被其他 allocation 覆寫，pivot 過去就是垃圾。pivot 與 payload 存活要一起規劃。
5. **把「寫 CR4 關 SMEP」當預設招**——CR4 pinning 之後多半無效。先驗版本；沒驗就用，會浪費大把時間 debug 一個「寫進去又被改回來」的詭異現象。

## 進階：為什麼現代 exploit 乾脆不碰 user memory

把前面幾招連起來看，會發現一個趨勢：**最穩的做法是整條 exploit 都不依賴 user memory**——payload 放在 kernel 能合法持有的物件裡（某個 spray 進去的 kmalloc buffer）、指令用 kernel 自己的 gadget、連提權都盡量走 data-only（改 cred/改 physmap）而非跳函式。這樣 SMEP、SMAP、乃至後面的 CFI 都少了著力點。

這也是本書結構的伏筆：Ch 13 你還在「湊 kernel ROP」，到 Part 3 後半（Ch 23–25）與 Part 4（Ch 28–29）你會看到更徹底的答案——連 ROP 都不要，純靠資料覆寫拿任意讀寫再提權。SMEP/SMAP 是逼你離開 user memory 的第一道推力；CFI 是逼你離開控制流劫持的第二道。方向一致：越現代的 kernel exploit，越「安靜」。

## 動手練習

在自有 QEMU 靶上，用 Ch 11 的同一個 stack overflow 靶，做對照實驗：

1. `nosmep nosmap` 跑原本的 ret2usr，確認會動（回到 Ch 11 的結果）。
2. 只拿掉 `nosmep`（SMEP 開、SMAP 關），重跑 ret2usr——觀察它怎麼在「跳去 user 的 escalate」那一刻 fault，讀 dmesg 的 RIP 和錯誤碼。
3. 換成 kernel ROP：對你的 vmlinux 抽 `pop rdi; ret` 等 gadget，排一條呼叫 `commit_creds(prepare_kernel_cred(NULL))` 的鏈（KASLR 仍關，位址用靜態值），鏈尾先簡單 `iretq` 回程（KPTI 關）。
4. 再把 `nosmap` 也拿掉（SMEP+SMAP 全開），確認你的 kernel ROP 版因為「全程沒碰 user memory」而依然能動——這就是這章的收穫。

## 本章重點整理

- SMEP 禁 ring 0 執行 user page（斬 ret2usr 執行面）；SMAP 禁 ring 0 存取 user data（斬資料面）；兩者皆 CR4 bit、硬體強制。
- SMAP 靠 `stac/clac` 給合法 uaccess 開臨時窗，這是唯一受控的縫。
- 替代打法：kernel ROP（只用 kernel 自己的 gadget）、stack pivot（把短控制接到長 payload，heap 類尤其常用）。
- 「寫 CR4 關 SMEP」被 CR4 pinning 封掉；ret2dir 用 physmap 的「同一實體兩視角」繞 SMEP 執行面，但 direct map NX 化後也被針對。
- 大方向：現代 exploit 盡量全程待在 kernel space、不碰 user memory，SMEP/SMAP 是這個趨勢的第一道推力。

## 自我檢核

- [ ] 能分別說出 SMEP 和 SMAP 各擋 ret2usr 的哪一面，並解釋為什麼要兩道牆
- [ ] 能解釋 `stac/clac` 是什麼、為什麼 `copy_from_user` 在 SMAP 開著時仍能運作
- [ ] 能說出 kernel ROP 和 user-space ROP 的異同，以及為什麼 gadget 位址不能照抄
- [ ] 知道 stack pivot 解決什麼問題（短控制 → 長 payload / payload 在 heap）
- [ ] 能解釋「寫 CR4 關 SMEP」為何在新 kernel 失效，以及 ret2dir 繞過 SMEP 的原理

## 延伸閱讀

- **[Intel SDM Vol. 3A, §4.6（Access Rights）與 CR4 說明](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html)**
  - **讀哪裡**：CR4.SMEP、CR4.SMAP 的定義與 AC flag（`stac/clac`）對 SMAP 的影響。
  - **和本章的關聯**：這是 SMEP/SMAP 行為的最終權威；本章對「硬體何時擋」的描述以此為準。
  - **前提知識**：會讀 CPU 手冊的 paging/privilege 章節；只需看 SMEP/SMAP 相關小節。
- **[kernel ROP 與 ret2usr bypass（lkmidas Part 2）](https://lkmidas.github.io/posts/20210205-linux-kernel-pwn-part-2/)**
  - **這篇說什麼**：接續 Ch 11/12 的靶，示範 SMEP 開啟後怎麼改用 ROP，含完整 gadget 尋找與鏈組裝。
  - **讀哪裡**：整篇，特別是 "Bypassing SMEP" 一節。
  - **為什麼值得讀**：把本章的 kernel ROP 落到可跑的 code，gadget 尋找流程可直接照做。
- **[ret2dir: Rethinking Kernel Isolation](https://www.usenix.org/conference/usenixsecurity14/technical-sessions/presentation/kemerlis)** — Kemerlis 等，USENIX Security 2014
  - **核心貢獻**：首次系統性提出用 physmap/direct map 繞過 SMEP 的執行隔離，並討論緩解。
  - **讀哪裡**：Section 3（攻擊原理）與 Section 5（緩解）；實驗章可略。
  - **和本章的關聯**：本章 ret2dir 一節的來源；讀它才懂「同一實體、兩個視角」為何是 physmap 類攻擊的根基，直通 [Ch 23](./23-dirty-pagetable.md)。

SMEP/SMAP 逼我們把一切留在 kernel space，但到目前為止我們都還假設 `nokaslr`、位址已知。下一章把 KASLR 加回來——沒有 leak，你連一個 gadget 的位址都不知道，整條 ROP 鏈無從排起。

→ [Ch 14 — KASLR 與 info leak 大全：leak 途徑、percpu、uninitialized 記憶體、oracle](./14-kaslr-infoleak.md)
