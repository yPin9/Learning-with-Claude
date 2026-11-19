# Ch 9 — 找 jump point（jmp esp）與模組選擇

> **目標**：理解為什麼覆寫 `EIP` 之後不能直接塞 shellcode 位址,而要先跳到 `ESP`；學會用 mona 找一個穩定、不含壞字元的 `jmp esp`/等效指令位址,並建立挑選模組（module）時該看哪些條件的判斷力。

> **環境**：Win10 x64 22H2 + WinDbg（2026）+ mona v3 + Python 3 + Kali。

## 為什麼不能直接把 EIP 設成 shellcode 的位址

Ch 8 算出了精確 offset,理論上你可以把覆寫 `EIP` 的 4 bytes 直接寫成「shellcode 在記憶體裡的位址」,讓 `ret` 之後直接跳進 shellcode 執行。問題是:**你不知道 shellcode 會落在哪個固定位址**。

shellcode 是你 payload 裡 offset 之後、覆寫 `EIP` 之後的那一段資料——它會被複製到 stack 上,但 stack 上的絕對位址**每次執行都可能不同**(就算 ASLR 完全關閉,不同的呼叫路徑、不同的輸入長度都可能讓 buffer 的起始位址有微小差異)。硬編碼一個猜測的絕對位址,穩定性很差,考場上這種 exploit 會「有時候中,有時候不中」。

但有一個暫存器的值,在崩潰那一刻幾乎是**可預期**的:`ESP`。因為 `ret` 指令執行的那一刻,`ESP` 剛好指向「`EIP` 被彈出之後,緊接著的下一個 stack 位置」——而這個位置,正好就是你 payload 裡**緊接在覆寫 EIP 之後的那一段資料**,也就是你打算放 shellcode 的地方。

## 先建立直覺:EIP → jmp esp → shellcode 的接力

```
payload 結構（offset 之後）：
┌──────────┬──────────┬───────────────────────────┐
│ A*offset │  跳板位址  │  shellcode（緊接在後）        │
└──────────┴──────────┴───────────────────────────┘
                 │
                 ▼ ret 執行後，EIP = 跳板位址
         ┌──────────────┐
         │ jmp esp 指令  │  ← 跳板位址指向的這行指令
         └──────────────┘
                 │
                 ▼ 執行 jmp esp，CPU 跳到 ESP 當前指向的位置
         ┌──────────────┐
         │ ESP 指向的記憶體 │  ← 這裡剛好就是你 payload 裡「跳板位址」後面那段，即 shellcode
         └──────────────┘
                 │
                 ▼
            shellcode 開始執行
```

你不是把 `EIP` 直接設成 shellcode 的位址(不可預期),而是設成一個**穩定已知**的位址,這個位址上剛好有一行 `jmp esp`(或等效)指令——利用這行指令把執行流「轉接」到 `ESP`,而 `ESP` 本身因為 stack 的運作方式,剛好就是指向你 shellcode 所在的位置。這是整個 OSED stack overflow 技法裡最關鍵的一個間接跳轉技巧,務必確實理解,後面 SEH overwrite(Part 3)也是同一套思路的變體。

## 核心:找到合用的指令與對應 opcode

你需要在目標行程載入的某個模組裡,找到下面三種指令中的任何一種:

| 組語 | opcode（機器碼） | 效果 |
|---|---|---|
| `jmp esp` | `FF E4` | 直接跳到 ESP 指向的位置 |
| `call esp` | `FF D4` | call 會先把下一行指令位址 push 進 stack 再跳,效果上也會執行到 ESP 指向的程式碼(但 stack 會多一層,視情況影響後續 shellcode) |
| `push esp` 接 `ret` | `54` `C3` | push 把 ESP 的值疊上 stack,再 ret 把它當成返回位址彈出,等效跳到 ESP |

這三種的本質都是「把執行流導向 `ESP` 當前的值」,選哪一種視你在目標模組裡能找到哪個,通常 `jmp esp`(`FF E4`)最直接、最常見。

### 用 mona 自動搜尋

在 WinDbg 命令列:

```
!py -3.9 C:\Tools\mona3\mona.py jmp -r esp -cpb "\x00\x0a\x0d"
```

- `-r esp` 告訴 mona 要找「跳到 ESP」的指令組合(它會自動找 `jmp esp`、`call esp`、`push esp;ret` 等多種等效形式)。
- `-cpb "\x00\x0a\x0d"` 傳入 Ch 8 找到的壞字元清單,mona 會自動排除**位址本身包含這些 byte** 的候選——這一步非常重要,下面會解釋原因。

mona 會印出一串候選位址,附帶來源模組與屬性,類似(未實測,理論預期,請以你環境實跑結果為準):

```
0x6250fa66 : jmp esp |  {PAGE_EXECUTE_READ} [essfunc.dll] ASLR: False, Rebase: False, SafeSEH: False, OS: False, v: -
```

### 為什麼位址本身不能含壞字元

跳板位址是 4 bytes,一樣要經過 socket 傳輸、協定解析這條路徑——如果這 4 bytes 裡剛好出現 `\x00` 或 `\x0a`,就會跟 Ch 8 講的壞字元問題一樣,在傳輸過程中失真,導致你送出去的跳板位址根本不是你以為的那個值。這也是為什麼 mona 的 `-cpb` 參數要傳 Ch 8 找到的壞字元清單——Ch 8 的成果在這裡直接被複用。

### 模組選擇準則

mona 通常會列出一長串候選,怎麼選是 OSED 的實務判斷題,看以下幾項:

1. **位址不含壞字元**——已透過 `-cpb` 自動排除,但仍建議肉眼再確認一次。
2. **模組沒有 ASLR(`Rebase: False` 或 mona 標示 `ASLR: False`)**——如果模組開了 ASLR,每次行程啟動它的基底位址都會變,你寫死的跳板位址下次就失效了。選 ASLR 關閉的模組是穩定性的第一要件。
3. **優先選程式自帶的模組,而非系統 DLL**——vulnserver 自己附帶的 `essfunc.dll` 這類模組,通常是為了教學目的刻意關掉 ASLR、保持固定基底位址;而 `kernel32.dll`、`ntdll.dll` 這些系統 DLL 在不同 Windows 版本、甚至同版本不同 patch 之間,基底位址和內部 offset 都可能變動,拿它們當跳板位址的可移植性較差。OSED 考場更看重你能不能寫出「在你這台靶機上穩定可重現」的 exploit,選自帶模組通常是更安全的選擇。
4. **`SafeSEH: False`**——這個屬性對 jmp esp 跳板本身不直接相關,但如果你之後要在 Part 3 做 SEH overwrite,同一套「找不受保護模組」的邏輯會再出現一次,先建立這個判斷習慣。

### 失敗與邊界情況

- **mona 回報「沒有找到任何候選」**:可能目標行程載入的模組太少、太新(都開了 ASLR),或壞字元清單太嚴格排除掉了所有候選。這時可以放寬搜尋(先不排除壞字元看有多少候選,再手動篩),或換一種等效指令形式(`call esp` 代替 `jmp esp`)。
- **選到的位址在系統 DLL 裡,換一台 Windows patch 版本就失效**:這正是為什麼準則 3 強調優先選程式自帶模組——如果考場或練習環境的 Windows 版本和你原本測試的不同,系統 DLL 內部的 offset 可能整個位移。
- **位址本身沒壞字元,但該位址所在的 page 沒有執行權限**:mona 輸出裡的 `{PAGE_EXECUTE_READ}` 這類標記就是在確認這件事,選位址時務必確認屬性包含可執行,否則跳過去會直接炸另一個 access violation。

## 底層機制:ret → jmp esp → shellcode 的暫存器視角

```
崩潰前一刻（ret 指令執行中）：
  stack 上：[... offset 填充 A ...][跳板位址][shellcode 開頭...]
                                    ↑ ret 彈出這 4 bytes 放進 EIP
  ret 執行後：
  EIP = 跳板位址（例如 0x6250fa66）
  ESP = 跳板位址 4 bytes 之後的位置（也就是 shellcode 的開頭！）
        因為 ret 彈出一個值之後，ESP 自然往高位址方向移動 4 bytes

  CPU 跳去執行 0x6250fa66 處的指令：
  0x6250fa66:  FF E4         jmp esp

  執行這行指令：
  EIP = ESP 當前的值 = shellcode 開頭位址
  CPU 開始逐行執行 shellcode
```

這條鏈的關鍵是:你完全不需要知道 shellcode 的絕對位址是多少——你只需要知道「`ret` 之後 `ESP` 自然就指向它」,然後找一個穩定位址幫你完成「跳到 ESP」這個動作。

## 對比取捨:jmp esp vs call esp vs push esp; ret

| 做法 | 優點 | 缺點 |
|---|---|---|
| `jmp esp`（`FF E4`） | 最直接，執行後 ESP 不變，shellcode 從原位置開始跑，最常用 | 需要目標模組裡恰好有這個 opcode |
| `call esp`（`FF D4`） | 效果相同，候選數量可能更多 | call 會先 push 下一行指令的位址進 stack，ESP 往下移 4 bytes，shellcode 要考慮這個位移（或在 shellcode 前墊 NOP 吸收） |
| `push esp; ret`（`54 C3`，需相鄰兩個 byte） | 在某些模組裡比單一 `jmp esp` 更容易找到 | 需要找到兩個連續指令的組合，稍微限制候選範圍 |

## 踩雷集錦

1. **錯誤直覺:找到任何一個 `jmp esp` 位址就能用。**
   正確理解:位址本身的 4 bytes 必須不含壞字元,且所在模組最好沒開 ASLR——隨便挑 mona 列表第一個不檢查這兩項,exploit 在你的環境可能第一次就失敗,或換一台機器就失效。

2. **錯誤直覺:系統 DLL(如 `kernel32.dll`)裡的位址比程式自帶的 DLL 更「正規」,應該優先選。**
   正確理解:恰恰相反。系統 DLL 的基底位址和內部 offset 隨 Windows 版本、甚至同版本的更新而變動,穩定性反而較差。OSED 練習與考場更偏好選程式自帶、沒有 ASLR 的模組。

3. **錯誤直覺:`call esp` 和 `jmp esp` 完全等價,選哪個都一樣。**
   正確理解:`call` 會多 push 一個返回位址到 stack,讓 `ESP` 執行後的位置往下移動 4 bytes——如果你的 shellcode 緊貼著跳板位址後面擺放,用 `call esp` 可能導致 CPU 從 shellcode 中間某個位置開始執行,邏輯整個錯亂。選用時要知道這個差異,必要時調整 shellcode 前的填充。

4. **錯誤直覺:跳板位址只要能讓 EIP 跳過去就行,不管該位址所在 page 有沒有執行權限。**
   正確理解:x86 的 DEP(Data Execution Prevention)如果對該 page 生效,就算你精確跳過去,CPU 也無法執行該位址的指令,一樣會炸例外。mona 輸出裡的 page 屬性(`PAGE_EXECUTE_READ` 等)就是在確認這件事,Ch 27 之後會深入 DEP 繞過,但本章至少要先確認跳板本身所在的頁是可執行的。

5. **錯誤直覺:找 jmp esp 一定要用 mona,手動翻 disassembly 太慢不值得。**
   正確理解:mona 自動化了搜尋,但理解它背後在找什麼 opcode(`FF E4` 等)很重要——考場環境如果 mona 因故跑不動(環境問題在 OSED 考場真實發生過),你需要能用 IDA/WinDbg 手動搜尋同樣的 byte pattern 當備案。

## 進階:再往深一層

- **ROP 式跳板**:如果找不到單一指令的 `jmp esp`,有時可以串接兩段短 gadget 達到同等效果(例如先 `pop something; ret` 再接別的 gadget)——這是 Part 7 ROP 技法的簡化預告,本章先專注單一指令跳板就夠。
- **egghunter 替代方案**:如果 ESP 附近的可用空間太小塞不下完整 shellcode,Part 5 的 egghunter 技法提供另一條路——先跳到一小段「搜尋器」程式碼,讓它在記憶體裡搜尋你真正的 shellcode(放在別處、不受空間限制)。先知道這條路存在,之後遇到空間不足不會卡住。

## 重點整理

- 覆寫 `EIP` 後不直接指向 shellcode 絕對位址(不可預期),而是指向一個穩定位址上的「跳轉到 ESP」指令——因為 `ret` 之後 `ESP` 自然指向你 payload 裡緊接著的 shellcode。
- `jmp esp`(`FF E4`)、`call esp`(`FF D4`)、`push esp; ret`(`54 C3`)是三種常見等效寫法。
- 用 mona `!py mona jmp -r esp -cpb "<壞字元>"` 自動搜尋,務必帶入 Ch 8 找到的壞字元清單。
- 選模組優先順序:不含壞字元 > 沒有 ASLR > 程式自帶模組優先於系統 DLL > 所在 page 可執行。

## 自我檢核

- [ ] 能解釋為什麼不能直接把 EIP 設成 shellcode 的絕對位址
- [ ] 能畫出 ret → jmp esp → shellcode 這條接力鏈,說出每一步 EIP/ESP 的值怎麼變
- [ ] 能列出三種跳到 ESP 的等效指令與對應 opcode
- [ ] 能說出為什麼優先選程式自帶模組而非系統 DLL 當跳板來源
- [ ] 能解釋為什麼跳板位址本身也要排除壞字元

## 延伸閱讀

- **[Corelan — Exploit Writing Tutorial part 1: Stack Based Overflows](https://www.corelan.be/index.php/2009/07/19/exploit-writing-tutorial-part-1-stack-based-overflows/)** — Peter Van Eeckhoutte
  - 讀哪裡：找 `jmp esp` 與選模組的段落。
  - 學什麼：本章跳板技法與模組選擇準則的原始出處。
  - 前提：已讀 Ch 7、Ch 8。

- **[corelan/mona（GitHub）](https://github.com/corelan/mona)**
  - 讀哪裡：README 裡 `jmp` 指令的參數說明(`-r`、`-cpb`、`-m` 限定模組等)。
  - 學什麼：mona v3 現行的跳板搜尋語法與輸出欄位意義(ASLR/Rebase/SafeSEH 等標記)。
  - 前提：Ch 0 已裝好 mona v3。

- **[FuzzySecurity — Windows Exploit Development Tutorial Series](https://www.fuzzysecurity.com/tutorials.html)** — b33f (Ruben Boonen)
  - 讀哪裡：第一支完整 exploit 的跳板選擇段落。
  - 學什麼：另一個角度看模組選擇的實戰判斷,可和 Corelan 講法互相補強。
  - 前提：無。

- **[Microsoft Learn — Data Execution Prevention](https://learn.microsoft.com/en-us/windows/win32/memory/data-execution-prevention)**
  - 讀哪裡：DEP 如何依 page 屬性決定能不能執行的概述。
  - 學什麼：理解為什麼跳板位址所在 page 的可執行屬性很重要,銜接 Part 7 DEP bypass。
  - 前提：無,先知道有這個機制即可。

跳板位址找好,壞字元清單也確定了。下一章把前三步的所有材料組裝起來,寫出第一支完整、可拿到反彈 shell 的 exploit 腳本。

→ [Ch 10 第一個完整 exploit：壞字元安全 shellcode + 反彈 shell](./10-first-complete-exploit.md)
