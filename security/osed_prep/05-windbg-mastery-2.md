# Ch 5 — WinDbg 精通 II：例外觀測、搜尋、crash triage 工作流

> **目標**：把 WinDbg 從「會用命令」練到「會分析一次崩潰」。你會看懂 first/second chance 例外、用 `!exchain` 讀 SEH 鏈、在整個行程記憶體裡搜尋你的資料，並建立一套「crash 當下該記什麼」的 triage SOP——這是 OSED 考場上每次送 payload 後都要跑的動作。

> **環境**：Win10 x64 22H2 + WinDbg（2026）+ mona v3 + Python 3，偵錯對象為 32-bit 行程。

## 為什麼 crash 分析要單獨一章？

Ch 4 教你怎麼「看」，這章教你怎麼「判讀一次崩潰」。兩者差在哪？看，是知道 `r eip` 印什麼；判讀，是看到 `eip=41414141` 的下一秒就知道：我控到返回位址了、我的資料在 ESP 往下、這是 stack overflow 不是 SEH、下一步該量 offset。

OSED 的工作循環是這樣的：

```
送 payload → 程式崩 → 在 WinDbg 判讀崩在哪、我控到什麼
          ↑                                    │
          └──────── 依判讀調整 payload ←────────┘
```

這個循環你一天要跑幾十上百次。判讀慢，整個循環就慢。所以這章的重點不是更多命令，是**一套固定的判讀動作順序**，練到看一眼就有結論。

## 先建立直覺：什麼是 first chance / second chance？

這是 Windows 例外處理的核心節奏，不懂它你會被 WinDbg「停了兩次」搞糊塗。

```
程式執行時發生例外（例如存取無效記憶體）
          │
          ▼
   ┌─────────────────┐   WinDbg 先被通知一次
   │  FIRST CHANCE   │ ← 「程式剛出事，但它自己可能有 handler 能處理」
   └─────────────────┘   （預設：WinDbg 讓程式繼續，看它的 SEH 能不能救）
          │
          ▼
   程式的 SEH handler 嘗試處理這個例外
          │
     ┌────┴────┐
   救得了      救不了
     │           │
   繼續跑        ▼
          ┌─────────────────┐  WinDbg 第二次被通知
          │ SECOND CHANCE   │ ← 「沒人能救了，這是致命崩潰」
          └─────────────────┘  （這次 WinDbg 一定停住，交給你）
```

> **為什麼這對 OSED 關鍵？** 因為 **SEH overwrite 利用**（Ch 11–13）正是發生在這個節奏裡：你的 overflow 砸壞了 SEH handler 指標，當例外觸發、系統去呼叫 handler 時，就跳到你控制的位址。在 WinDbg 裡，你會在 first chance 看到原始的存取違規，然後要觀察「系統接下來去呼叫哪個 handler」——那個 handler 指標就是你要控的東西。

## 核心一：控制例外的攔截

預設下 WinDbg 對某些例外只在 second chance 停。你有時想在 first chance 就停下來看：

```
sxe av            ; set exception enabled：access violation 一發生（first chance）就停
sxd av            ; set exception disabled：不在 first chance 停（回到預設）
sx                ; 列出目前所有例外的處理設定
```

存取違規（access violation, AV）是 OSED 最常遇到的例外。crash 停下來時，WinDbg 通常會印出類似：

```
(aec.1b4): Access violation - code c0000005 (!!! second chance !!!)
eip=41414141 ...
```

> `c0000005` 就是 `STATUS_ACCESS_VIOLATION`。看到 `!!! second chance !!!` 代表這是沒人能救的致命崩潰——你通常就是在這裡接管。

> **未實測，理論預期**：以上輸出形狀來自 WinDbg 對 AV 的標準回報（官方文件，見延伸閱讀）。實際的行程 id、位址依你的環境而定。

## 核心二：讀 SEH 鏈 `!exchain`

當你懷疑這是個 SEH-based 的崩潰（長溢位、EIP 沒直接被控但程式掛了），用 `!exchain` 看目前的例外處理鏈：

```
0:004> !exchain
0012ff40: 42424242          ; nSEH 位置（被你蓋成 BBBB）
0012ff44: 41414141          ; SEH handler 指標（被你蓋成 AAAA）—— 控到這個就能劫持
Invalid exception stack at 41414141
```

> **未實測，理論預期**：這是 SEH 被覆寫後 `!exchain` 的典型表現（Ch 11–12 完整拆解）。看到 handler 指標變成你送的字元，就確認了「這是可利用的 SEH overwrite」。相對地，若 `!exchain` 顯示的是正常的模組內 handler，表示你還沒蓋到 SEH。

搭配看結構的 `dt` 和 `!teb`：

```
!teb              ; 看 Thread Environment Block，其中 ExceptionList 指向 SEH 鏈頭
dt _EXCEPTION_REGISTRATION_RECORD <addr>   ; 把某位址當 SEH record 解析（next + handler）
```

## 核心三：在記憶體裡搜尋你的資料 `s`

exploit 開發有個反覆出現的問題：「我送進去的那串資料，現在躺在記憶體哪裡？」因為你的 shellcode 可能不在 ESP，而在某個你意想不到的緩衝區。`s`（search）就是幹這個的：

```
s -b 0 L?80000000 41 41 41 41        ; 在整個位址空間搜 byte 序列 41 41 41 41
s -d 0 L?80000000 41414141           ; 以 dword 搜
s -a 0 L?80000000 "w00tw00t"         ; 搜 ASCII 字串（egghunter 的 egg 就用這個找）
```

語法拆解：`-b`/`-d`/`-a` 是搜尋單位（byte/dword/ASCII）；`0 L?80000000` 是「從位址 0 起、長度 0x80000000」涵蓋 user-mode 範圍；後面是要找的樣式。

> **為什麼這在 OSED 很重要？** 兩個場景：(1) 你的 shellcode 空間不夠，需要找「同一份資料在記憶體別處有沒有更大的落點」（Ch 19 空間限制）。(2) egghunter 要靠 egg 標記定位 shellcode，你得先確認 egg 真的落在可搜範圍（Ch 20）。`s -a ... "w00t"` 就是你驗證 egg 位置的命令。

## 核心四：看頁面保護 `!address`

DEP（Ch 27）之後，你很在意「這塊記憶體能不能執行」。`!address` 告訴你任一位址所在頁面的保護屬性：

```
!address esp      ; 看 esp 所在區域：Base/Size/State/Protect/Type
```

輸出會標 `PAGE_EXECUTE_READWRITE`、`PAGE_READWRITE` 之類。如果你的 shellcode 落在只有 `READWRITE`（不可執行）的 stack 上、又開了 DEP，直接跳過去就會再 crash——這正是 Ch 27 要用 ROP 把頁面改成可執行的理由。

## crash triage SOP：固定動作順序

把前面的命令串成一套「每次 crash 都跑」的流程。建議背下來：

```
1. r eip          → EIP 被控了嗎？是我的資料（41414141）嗎？
2. r esp / dd esp → 我的資料在 ESP 附近嗎？（判斷是不是單純 stack overflow）
3. db esp L200    → 逐 byte 看，有沒有壞字元截斷的跡象
4. !exchain       → EIP 沒直接被控但程式掛了？看看是不是 SEH 被蓋
5. s -b ...        → 我的資料到底落在記憶體哪些地方？哪塊最大？
6. !address <落點> → 那塊能執行嗎？（決定要不要 ROP）
7. ln eip / lm     → 崩在哪個模組？這模組有沒有 ASLR？
```

每跑一次，心裡要能回答一句話：「這是哪種漏洞、我控到什麼、下一步做什麼」。這就是 triage 的全部意義。

## 範例：兩種 crash 的判讀對照

**情況 A — 直接控 EIP（stack overflow）**

```
0:004> r eip
eip=41414141                 ; ← 直接是我的資料
0:004> dd esp
0012f1f0  41414141 41414141   ; ← 資料在 ESP
```
判讀：單純 stack overflow，EIP 直接可控。下一步量 offset（Ch 8）、找 jmp esp（Ch 9）。

**情況 B — EIP 沒被控，但程式掛了（SEH）**

```
0:004> r eip
eip=76f3a1b2 ntdll!...        ; ← EIP 在系統模組裡，不是我的資料
0:004> !exchain
0012ff44: 41414141            ; ← 但 SEH handler 被我蓋了
```
判讀：這是 SEH overwrite 路線。EIP 現在沒被控是因為例外正在被分派；真正的控制點是那個被蓋的 handler 指標。下一步走 Ch 11–13 的 SEH 技法。

> **未實測，理論預期**：兩種情況的區分方式來自標準 Windows exploit 分析實務。你自己的靶會落在其中一種——學會從「EIP 是不是我的資料」+「!exchain 有沒有被蓋」兩個訊號快速分類，是這章最值錢的技能。

## 進階：Time Travel Debugging（TTD）

新版 WinDbg 能把一整段執行錄下來（trace），之後像影片一樣前後回放，連「往回走」都行（`g-` 反向執行）。

- **適合**：crash 之前到底發生了什麼？是哪條指令、用什麼值覆寫了我的返回位址？正向偵錯只能看到「已經壞了」的現場，TTD 能讓你倒帶看「正在壞」的那一刻。
- **代價**：錄製有效能與空間成本，trace 檔可能很大；不是每次都開。
- OSED 層級多半用不到 TTD 也能過，但遇到「覆寫來源不明」的疑難 crash 時它是解法。深入留給 [Ch 41](./41-deeper-cfg-cet-rop-automation.md) 與 `windows_pwn` 的 TTD 章；現在知道「有這招、什麼時候想到它」就夠。

## 對比與取捨

| 訊號 | 看什麼命令 | 代表什麼 |
|---|---|---|
| EIP = 我的資料 | `r eip` | 直接控返回位址，stack overflow 路線 |
| EIP 在系統模組 + SEH 被蓋 | `r eip` + `!exchain` | SEH overwrite 路線 |
| 資料在 ESP | `dd esp` | shellcode 可落 ESP，找 jmp esp |
| 資料被截斷 | `db esp` | 有壞字元（Ch 8） |
| 落點不可執行 + 開 DEP | `!address` | 需要 ROP（Ch 27） |
| 崩在無 ASLR 模組 | `ln`/`lm` | 該模組位址可寫死（Ch 31） |

## 踩雷集錦

1. **「WinDbg 停了兩次，以為程式崩了兩次」**：錯誤直覺是「停一次=崩一次」。正確認識：那是同一個例外的 first chance 和 second chance。first chance 是「剛出事、還沒人救」，second chance 是「救不了、致命」。你通常在 second chance 接管。
2. **「EIP 沒被控就下結論說不可利用」**：很多 SEH 漏洞的 EIP 當下是在系統模組裡（例外正在分派），直接看 EIP 會誤判成「沒控到」。一定要加看 `!exchain`，否則漏掉整條 SEH 利用路線。
3. **「`s` 搜不到資料就以為沒送進去」**：常見原因是搜尋範圍或單位設錯（用 `-d` 搜但資料沒對齊、範圍沒涵蓋到）。先用 `-b`（byte，不管對齊）在 user 範圍 `0 L?80000000` 搜，最不容易漏。
4. **「看到 `c0000005` 不知道是什麼」**：那是存取違規的 NTSTATUS 碼。背起來：`c0000005`=access violation。它是 OSED 最常見的崩潰碼，不是什麼罕見錯誤。
5. **「忘了不同 thread 有不同 SEH 鏈」**：`!exchain`/`!teb` 是看目前 thread 的。服務多執行緒時，確認你停在處理你 payload 的那個 thread（`~` 列 thread，`~Ns` 切換）。

## 本章重點整理

- 例外有 first chance（剛出事）與 second chance（救不了），SEH 利用就活在這個分派過程裡。
- `sxe av` 可在 first chance 就停；`c0000005` = access violation。
- `!exchain` 是判斷「這是不是 SEH overwrite」的關鍵命令，補 `r eip` 的盲區。
- `s -b/-d/-a` 在全記憶體找你的資料；egg 定位、找更大落點都靠它。
- `!address` 看頁面能不能執行，決定要不要 ROP。
- 練成固定的 triage SOP：eip → esp → db → !exchain → s → !address → 模組。

## 自我檢核

- [ ] 不看筆記，能畫出 first chance → SEH 嘗試 → second chance 的流程，並說出 SEH 利用卡在哪一段
- [ ] 能說出「EIP 沒被控但程式掛了」時，下一個該打的命令是什麼、為什麼
- [ ] 能寫出搜尋 `w00tw00t` 這個 egg 的 `s` 命令
- [ ] 知道為什麼 crash 後要看 `!address`，它的結論會影響哪一章的做法
- [ ] 能複述一遍 crash triage 的七步動作順序

## 延伸閱讀

- **[Controlling Exceptions and Events — Microsoft Learn](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/controlling-exceptions-and-events)**
  - **讀哪裡**：`sx*` 命令與 first/second chance 的定義段落。
  - **學什麼**：本章例外控制的權威來源；各例外的預設處理設定。
  - **前提**：先有本章 first/second chance 的直覺圖。

- **[Memory searching (`s` command) — Microsoft Learn](https://learn.microsoft.com/en-us/windows-hardware/drivers/debuggercmds/s--search-memory-)**
  - **讀哪裡**：`s` 的單位旗標（`-b`/`-d`/`-a`/`-u`）與範圍語法。
  - **學什麼**：精準搜尋記憶體；egg 定位、找落點必備。

- **[Time Travel Debugging — Microsoft Learn](https://learn.microsoft.com/en-us/windows-hardware/drivers/debugger/time-travel-debugging-overview)**
  - **讀哪裡**：Overview 與「record a trace」段落。
  - **學什麼**：何時值得開 TTD、怎麼錄；本章進階段的延伸。
  - **注意**：OSED 多數題不需要 TTD，當疑難雜症的備案看待。

- **[Corelan — Exploit Writing Tutorial part 3 (SEH)](https://www.corelan.be/index.php/2009/07/25/writing-buffer-overflow-exploits-a-quick-and-basic-tutorial-part-3-seh/)** — Peter Van Eeckhoutte
  - **讀哪裡**：用 WinDbg/偵錯器看 SEH 鏈與 `!exchain` 的段落。
  - **為什麼值得讀**：把本章的例外判讀直接接到 Ch 11–13 的 SEH 利用。

偵錯器就位了。下一章補最後一塊地基——PE 格式。你要能讀懂一支 Windows 執行檔的骨架，後面的逆向找洞、egghunter、ASLR 繞過、PE 後門才有立足點。

→ [Ch 6 PE 格式與記憶體佈局速成](./06-pe-format-crash-course.md)
