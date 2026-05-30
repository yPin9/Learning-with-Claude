# Ch 2 — x86 啟動時的 CPU 狀態

> **目標**：理解 x86 CPU 上電後的精確狀態——reset vector（`0xFFFFFFF0`）、real mode 的 16-bit 世界、segment:offset 定址、A20 line 的歷史包袱，讓你知道你的 boot code「從什麼樣的赤裸環境開始執行」。

> **環境**：x86-64 CPU，QEMU 模擬。本章描述的是真實硬體行為，QEMU 忠實模擬。

## 為什麼要懂 CPU 上電狀態？

你寫 boot sector 時，CPU 不是處於你熟悉的 64-bit、有作業系統、有記憶體保護的環境。它處於一個**赤裸、原始、充滿 1980 年代歷史包袱**的狀態：16-bit、只能定址 1MB、沒有記憶體保護、暫存器有奇怪的初始值。

如果你不知道這個起點長什麼樣，你的 boot code 會做出錯誤假設（以為能用 32-bit 暫存器、以為能定址大記憶體），然後神秘地失敗。這章把「CPU 交棒給你的那一刻」的精確狀態講清楚。

## 先建立直覺：CPU 醒來時像個 1981 年的 CPU

```
現代 x86-64 CPU 上電的反直覺事實：

  它假裝自己是一顆 1981 年的 Intel 8086
        │
  為什麼？向後相容。Intel 承諾：
  任何 1981 年能跑的 code，今天的 CPU 上電後都能跑
        │
  所以上電 = 16-bit real mode（8086 模式）
        │
  你要自己一步步把它「喚醒」成 64-bit：
  real mode → protected mode (32-bit) → long mode (64-bit)
  （Ch 7-8 做這個喚醒）
```

這是 x86 最大的歷史包袱也是它的設計哲學：**極致的向後相容**。一顆 2024 年的 CPU 上電後，行為和 1981 年的 8086 幾乎一樣，讓四十年前的 code 還能跑。代價是：你的現代 boot code 得從這個古老狀態開始，自己爬到現代模式。

## Reset Vector：第一條指令在哪

CPU 上電（或 reset）後，從一個**固定的物理位址**開始取指令執行——這叫 **reset vector**。

```
x86 的 reset vector：0xFFFFFFF0
                     （32-bit 位址空間的頂端附近，差 16 bytes 到頂）

為什麼是這裡？
  這個位址映射到主機板的韌體 flash 晶片
  （BIOS/UEFI 韌體燒在那）
  CPU 上電就跳這裡 = 執行韌體的第一條指令
```

但有個微妙之處：上電時 CPU 是 16-bit real mode，照理只能定址 1MB（20-bit），怎麼能執行 `0xFFFFFFF0`（32-bit 位址）？

```
答案：CS 的隱藏 base
  上電時 CS（code segment）有個特殊的隱藏 base = 0xFFFF0000
  CS:IP = base(0xFFFF0000) + IP(0xFFF0) = 0xFFFFFFF0
        │
  這是個「假 real mode」狀態——CPU 在 real mode 但能存取高位址
  直到第一次 far jump（重載 CS），才回到正常 real mode 的定址
```

> 這個 reset vector 的細節是韌體開發者的事（韌體的第一條指令通常就是個 `jmp` 跳到真正的初始化 code）。對你寫 boot sector 來說，重點是：**韌體跑完後，會把你的 boot code 載入記憶體並跳過去，那時 CPU 是「正常的」16-bit real mode**。你的起點是那一刻，不是 reset vector。

## Real Mode：16-bit 的世界

韌體把控制權交給你的 boot code 時，CPU 在 **real mode**（8086 相容模式）。它的特徵：

```
Real Mode 的限制：
  - 16-bit 暫存器（ax, bx, cx, dx, si, di, sp, bp 都是 16-bit）
  - 只能定址 1 MB（20-bit 位址空間）
  - 沒有記憶體保護（任何 code 能讀寫任何記憶體）
  - 沒有虛擬記憶體（位址就是物理位址）
  - 用 segment:offset 定址（見下）
```

16-bit 暫存器意味著一個暫存器最大裝 65535（0xFFFF）。但要定址 1MB 需要 20 bits。怎麼用 16-bit 暫存器定址 20-bit 空間？答案是 **segmentation**。

## Segment:Offset 定址

real mode 用兩個 16-bit 值組合出 20-bit 物理位址：

```
物理位址 = segment × 16 + offset
         = (segment << 4) + offset

例：CS = 0x07C0, IP = 0x0000
   物理位址 = 0x07C0 × 16 + 0x0000
            = 0x7C00 × 16... 不對，是 0x07C0 << 4 = 0x7C00
            = 0x7C00 + 0x0000 = 0x7C00

或：CS = 0x0000, IP = 0x7C00
   物理位址 = 0x0000 + 0x7C00 = 0x7C00
   （同一個物理位址有多種 segment:offset 表示！）
```

segment 暫存器：
| 暫存器 | 用途 |
|---|---|
| `CS` | Code Segment（指令從這取）|
| `DS` | Data Segment（資料存取預設用這）|
| `SS` | Stack Segment（堆疊用這）|
| `ES`, `FS`, `GS` | Extra segments（額外資料用）|

```asm
; segment:offset 的實際使用
mov ax, 0x07C0      ; 不能直接 mov 到 segment reg
mov ds, ax          ; 設 DS = 0x07C0
mov al, [0x0000]    ; 存取 DS:0x0000 = 0x07C0<<4 + 0 = 0x7C00
```

> segment:offset 的「同一物理位址多種表示」是 real mode 的怪癖。`0x07C0:0x0000` 和 `0x0000:0x7C00` 指向同一個物理位址 `0x7C00`。這在 Ch 6 寫 boot sector 時很重要——你的 `org` 指令（告訴組譯器假設的 offset）必須和韌體實際用的 segment:offset 一致，否則位址算錯（Ch 6 的經典雷）。

## 上電時的暫存器初始值

CPU reset 後暫存器有定義好的初始值（部分）：

```
上電/reset 後（韌體跑之前）：
  CS:IP    指向 reset vector（0xFFFFFFF0 的特殊狀態）
  其他 segment regs  通常 0x0000
  EFLAGS   0x00000002（固定 bit 1）
  其他通用暫存器  多半未定義/0

韌體跑完，跳到你的 boot code 時（real mode）：
  CS:IP    指向你的 boot code（BIOS 把你載到 0x7C00，jmp 0x0000:0x7C00）
  DL       開機磁碟編號（BIOS 設定！0x80 = 第一個硬碟）← 重要
  其他暫存器  不要假設任何值，自己初始化
```

> **關鍵實務**：BIOS 跳到你的 boot sector 時，`DL` 暫存器存著「你從哪個磁碟開機」的編號（`0x00` = 軟碟，`0x80` = 第一硬碟）。你之後要用 int 13h 讀更多 sector（Ch 9）時需要這個編號。所以 boot sector 開頭常常先 `mov [boot_drive], dl` 把它存起來——這是 BIOS 給你的唯一「我從哪來」的資訊。其他暫存器不要假設，自己初始化（尤其 segment regs 和 stack）。

## A20 Line：最荒謬的歷史包袱

real mode 的 segment:offset 最大能算出的位址是：

```
最大 segment:offset = 0xFFFF:0xFFFF
                    = 0xFFFF × 16 + 0xFFFF
                    = 0xFFFF0 + 0xFFFF
                    = 0x10FFEF   ← 超過 1MB（0x100000）！
```

8086 只有 20 條位址線（A0-A19），算出超過 1MB 的位址會**回繞**（wrap around）回到低位址（`0x10FFEF` 變成 `0x0FFEF`）。早期軟體**依賴**這個回繞行為。

當 80286 有了更多位址線（能定址超過 1MB），為了相容那些依賴回繞的舊軟體，IBM 加了一個 hack：**A20 line gate**——一個能強制 A20 位址線為 0 的開關。預設 A20 被禁用（強制回繞，相容舊軟體）。

```
A20 的荒謬現實：
  上電時 A20 被「禁用」（位址第 20 bit 強制為 0）
        │
  你想存取 1MB 以上的記憶體？必須先「開啟 A20」
        │
  開啟 A20 有好幾種方法（鍵盤控制器、Fast A20、BIOS int 15h）
  ——對，是「鍵盤控制器」，因為 IBM 當年把這個 gate 接在鍵盤控制器上
```

```asm
; 開啟 A20 的一種方法：Fast A20（透過 port 0x92）
in al, 0x92
or al, 2
out 0x92, al
; 還有透過鍵盤控制器（0x64/0x60）的傳統方法，更繁瑣
```

> A20 line 是計算機歷史上最著名的「相容性包袱」之一——為了讓 1981 年依賴位址回繞的軟體能跑，後續四十年的 CPU 都帶著這個預設禁用的位址線，每個 bootloader 都得記得開啟它才能用 1MB 以上的記憶體。Ch 7 切換到 protected mode 前必須開 A20，否則高位址存取會出錯。現代 UEFI 環境已經幫你處理好 A20（這也是 UEFI 的好處之一）。

## 故意弄壞：在 real mode 用 32-bit 暫存器

```asm
; boot sector 裡（real mode）誤用 32-bit
mov eax, 0x12345678    ; 在 real mode 用 32-bit 暫存器
; 這「能」執行（有 operand-size prefix），但...
mov [es:0x100000], eax ; 想寫到 1MB 以上 → 如果沒開 A20，位址回繞，寫錯地方！
```

real mode 能用 32-bit 暫存器（透過 operand-size prefix），但定址還是受 segment:offset 和 A20 限制。新手常以為「我用了 eax 就是 32-bit 環境了」——不是，模式還是 real mode，定址還是受限。要真正的 32-bit 環境，必須切到 protected mode（Ch 7）。

## 踩雷集錦

1. **以為上電就是 64-bit**：上電是 16-bit real mode（8086 相容）。要自己一路切到 long mode（Ch 7-8）。這是最大的認知錯誤

2. **忘記存 DL（開機磁碟）**：BIOS 用 DL 告訴你從哪開機。boot sector 開頭沒存它，之後 int 13h 讀磁碟時不知道讀哪個磁碟。第一件事就是 `mov [boot_drive], dl`

3. **不初始化 segment 和 stack**：上電後 segment regs 和 SP 的值不可靠。boot code 要自己設好 DS/ES/SS 和 SP，否則資料存取和 stack 操作會到處亂指

4. **忘記開 A20 就存取高記憶體**：A20 預設禁用，存取 1MB 以上會回繞到低位址，資料寫錯地方且難 debug。切 protected mode 前要開 A20

5. **segment:offset 算錯位址**：忘記 segment 要 `<< 4`（×16），或 `org` 和實際載入位址不一致。Ch 6 會詳細踩這個雷

## 進階：為什麼 x86 背這麼重的相容包袱

x86 的「上電當 8086」「A20 gate」「real mode」這些包袱，反映了 Intel 的核心商業策略：**絕對的向後相容**。

```
向後相容的代價 vs 好處：
  代價：每顆現代 CPU 都帶著 40 年的歷史狀態，
        boot code 要爬過 real → protected → long 三個模式
  好處：龐大的軟體生態不會因為換 CPU 而報廢，
        這是 x86 統治 PC 市場的關鍵
```

對比：ARM 沒有這個包袱（Ch 30）——ARM 上電後直接是它的原生模式，沒有 real mode、沒有 A20、沒有模式切換的爬樓梯。這讓 ARM 開機更簡潔，但 ARM 也因此沒有 x86 那種「四十年二進位相容」的承諾。這是設計哲學的根本不同：x86 選相容，ARM 選簡潔。理解這個對比，你會更懂為什麼 x86 開機這麼繞。

## 動手練習

1. 用 gdb（Ch 0 的設定）連 QEMU，在 boot sector 載入前單步。`info registers` 看上電後的暫存器值。特別看 `cs`、`ip`、`dl`

2. 驗證 segment:offset：在 gdb 裡，當 CS=0x0000、IP=0x7C00 時，確認執行的指令就是你 boot sector 的第一條（物理位址 0x7C00）

3. 寫一段 real mode code 存 DL 到記憶體再印出來，確認 BIOS 給的開機磁碟編號（QEMU 從硬碟開機應該是 0x80）

4. 故意在 boot sector 用 `mov eax, ...` 然後 gdb 單步，觀察它確實能跑（operand-size prefix），但理解模式還是 real mode

## 本章重點整理

- x86 CPU 上電後是 16-bit real mode（8086 相容），為了極致向後相容；要自己爬到 64-bit
- reset vector（`0xFFFFFFF0`）是韌體的第一條指令；韌體跑完才把控制權交給你的 boot code（那時是正常 real mode）
- real mode 用 segment:offset 定址（物理位址 = segment×16 + offset），只能定址 1MB
- BIOS 跳到 boot code 時，DL 存開機磁碟編號（要存起來）；其他暫存器自己初始化
- A20 line 是相容包袱：預設禁用（位址回繞），要存取 1MB 以上得先開啟 A20

## 自我檢核

- [ ] 能解釋為什麼現代 x86 CPU 上電後是 16-bit real mode（向後相容）
- [ ] 能算出 segment:offset 對應的物理位址（×16 + offset）
- [ ] 知道 BIOS 用哪個暫存器告訴你開機磁碟，為什麼要存它
- [ ] 能解釋 A20 line 是什麼、為什麼存在、不開會怎樣
- [ ] 知道 real mode 用 32-bit 暫存器不等於進入 32-bit 模式

## 延伸閱讀

### 官方文件

- **[Intel SDM Vol 3, Ch 9 (Processor Management and Initialization)](https://www.intel.com/sdm)**
  - **讀哪裡**：9.1（Initialization Overview）、9.1.4（First Instruction）
  - **學什麼**：CPU reset 後的精確狀態、reset vector、各暫存器初始值的權威定義
  - **前提**：本章建立的概念

- **[OSDev Wiki: Real Mode](https://wiki.osdev.org/Real_Mode)** 和 **[A20 Line](https://wiki.osdev.org/A20_Line)**
  - **讀哪裡**：兩個條目整頁
  - **學什麼**：real mode 定址細節、A20 的所有開啟方法（鍵盤控制器/Fast A20/BIOS）
  - **前提**：無

### 部落格 / 文章

- **[The A20 line: a tale of backward compatibility](https://www.os2museum.com/wp/the-a20-gate/)** — OS/2 Museum
  - **這篇說什麼**：A20 line 的完整歷史，為什麼這個荒謬的 hack 存在四十年
  - **讀哪裡**：整篇
  - **為什麼值得讀**：理解 x86 相容包袱的最佳案例研究，讀完你會對「歷史債」有深刻體會

→ [Ch 3 開機時的記憶體佈局](./03-early-memory-layout.md)
