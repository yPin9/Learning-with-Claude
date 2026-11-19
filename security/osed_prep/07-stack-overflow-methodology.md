# Ch 7 — vanilla stack overflow：fuzz → crash → 控 EIP

> **目標**：建立 OSED 五步法的全景圖，並完成前兩步——用 fuzzing 讓一支陌生網路服務（vulnserver）崩潰，在 WinDbg 裡確認你已經控制了 `EIP`。這是整門課 Part 2 的起點，後面每一章都是在這個 crash 上面繼續蓋東西。

> **環境**：Win10 x64 22H2 + WinDbg（2026）+ mona v3 + Python 3 + Kali。靶程式 vulnserver（32-bit），聽 TCP 9999。

## 為什麼需要先學方法論，而不是直接背 exploit

Linux pwn 的 CTF 題目通常把漏洞點明擺在眼前：題目給你一支 binary，你知道哪個函式有洞。OSED 的現場不是這樣——你拿到一支陌生的網路服務，**不知道哪個指令、哪個參數有洞**，必須自己從外部摸出來。這就是 fuzzing 存在的理由：在你知道內部結構之前，先用「送資料、看反應」這個低成本手段縮小範圍。

OSED 對付一個 vanilla（無防護）stack overflow 的完整生產線分五步，貫穿 Part 2：

```
Step 1  Fuzzing          送遞增長度的資料，讓服務崩潰              ← 本章
Step 2  控制 EIP          確認崩潰點、EIP 被覆寫、算出精確位置       ← 本章後半
Step 3  壞字元分析         找出哪些 byte 在傳輸過程會被動過手        → Ch 8
Step 4  找 jump point      找一個穩定位址把執行流導向你的 shellcode  → Ch 9
Step 5  上 shellcode       組出完整 payload，拿到 shell            → Ch 10
```

本章只做前兩步。這個順序不是隨意排的：**先確認你能控 EIP，再去煩惱壞字元和跳板，否則你在優化一個可能根本打不穿的洞**。OSED 考場上常見的失分是跳過步驟直接嘗試完整 exploit，卡住後不知道是哪一步出錯——按五步法走，每步都有明確的「過/不過」判斷。

## 先建立直覺：fuzzing 在打什麼

Fuzzing 的核心邏輯很樸素：**你不知道 buffer 多大，就用遞增的長度去撞，撞到崩潰為止**。

```
送 100 bytes   → 服務正常回應
送 500 bytes   → 服務正常回應
送 2000 bytes  → 服務正常回應
送 3000 bytes  → 服務崩潰（access violation）
                 ↑
                 真正的 buffer 邊界介於 2000~3000 之間
```

這和你在 Linux pwn 做的「先用超大 payload 確認有 overflow，再用 cyclic 定位 offset」是同一個思路，只是 OSED 情境多了一層：**你不知道協定的語法**。vulnserver 這類服務收一行指令加參數（例如 `TRUN .<data>`），你得先確認：

1. 哪個指令（command）有洞——vulnserver 用 `HELP` 列出一堆指令（`TRUN`、`GMON`、`GTER`、`KSTET`…），每個背後可能是不同的漏洞類型甚至完全沒洞。
2. 該指令對哪個參數位置敏感——通常是指令後面那段自由輸入的資料。
3. 多長的資料會讓它崩——這步才是傳統意義的 fuzzing。

## 核心：對 vulnserver TRUN 寫一支 fuzzer

TRUN 是 vulnserver 最廣為人知的指令，背後是一個經典的 stack overflow（詳細機制下面會畫圖）。協定格式是 `TRUN .<一堆資料>\r\n`。

```python
#!/usr/bin/env python3
"""
vulnserver TRUN fuzzer
用遞增長度的 'A' 字串去撞，直到服務不回應（崩潰）或連線被拒絕
"""
import socket
import sys
import time

target_ip   = "192.168.x.x"
target_port = 9999

def send_payload(size):
    buf = b"A" * size
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        s.connect((target_ip, target_port))
        s.recv(1024)                      # 吃掉服務的歡迎訊息
        s.send(b"TRUN ." + buf + b"\r\n")
        s.recv(1024)
        s.close()
        return True
    except Exception as e:
        print(f"[!] 連線在 size={size} 時失敗：{e}")
        return False

if __name__ == "__main__":
    size = 100
    step = 100
    while True:
        print(f"[*] 送出 size={size}")
        ok = send_payload(size)
        if not ok:
            print(f"[+] 服務疑似已崩潰，上一個成功的 size 約在 {size - step} 附近")
            sys.exit(0)
        size += step
        time.sleep(1)
```

這支腳本的邏輯和你寫過的任何 Linux fuzzer沒有本質差異：遞增、送、觀察、紀錄。**唯一 Windows 特有的部分**是「觀察」這一步——Linux 下你看的是程序是否還在跑（`ps` 或連線斷開），Windows 下你要同時盯著 WinDbg：服務可能連線斷開但進程沒死（被某個例外處理器接住），也可能進程直接消失。真正判斷崩潰與否的權威來源是 WinDbg 的斷點，不是 socket 連線狀態。

> **未實測，理論預期**：上面這支 fuzzer 的行為邏輯基於 vulnserver 公開的已知弱點（TRUN 無長度檢查）。實際撞到崩潰的 size 門檻，請在你自己的 VM 裡接上 WinDbg 跑一次確認——下方「如何驗證」會列出步驟。

### 失敗與邊界情況

- **送太小的長度**：服務正常回應，沒有任何異常——這是預期的，繼續加大。
- **送到某個長度後連線被拒絕、但 WinDbg 沒有顯示 access violation**：可能是服務自己的某層例外處理器把它接住了（Windows 的 SEH 機制，Part 3 會細講），進程沒死但那次請求失敗。這不算「控制了 EIP」，只能算「觸發了某種錯誤」——繼續觀察 WinDbg 裡暫存器的狀態才能判斷。
- **送極大長度（例如 50000 bytes）直接讓進程消失、WinDbg 沒機會附掛上**：先用較保守的遞增步伐（如本例 100）逼近邊界，別一開始就送誇張大小，否則你會錯過觀察崩潰瞬間的機會。

## 底層機制：crash 瞬間，stack 上發生了什麼

TRUN 指令背後（已知的公開行為）大致是把你送進去的資料用類似 `strcpy` 的方式複製進一個固定大小的 stack buffer，沒有做長度檢查。32-bit cdecl 的 stack frame 長這樣：

```
高位址（stack 底部）
┌──────────────────────────────┐
│ 呼叫者的 frame                │
│ ...                           │
│ saved EIP（return address）    │  ← 被覆寫的目標
├──────────────────────────────┤  ← EBP
│ saved EBP                     │
│ local buffer[N]                │  ← strcpy 類函式的目的地
│ ...                            │
低位址（stack 頂部，ESP 方向）
```

資料往高位址方向溢出，蓋過 `saved EBP`，繼續蓋過 `saved EIP`。當函式執行 `ret` 指令，CPU 從 stack 上彈出「本該是 return address」的那個值放進 `EIP`——如果那個值被你的 `A`（`0x41414141`）蓋過，CPU 就會嘗試去執行位址 `0x41414141` 的指令，炸出 access violation。

這一步在 WinDbg 裡看到的畫面（未實測，理論預期，架構請在你的環境自行驗證）：

```
(a1c.a20): Access violation - code c0000005 (!!! second chance !!!)
eax=... ebx=... ecx=... edx=...
eip=41414141 esp=0012fa50 ebp=41414141
...
41414141 ??              ???
```

`EIP=41414141` 這一行就是你要的訊號：**CPU 試圖從 `0x41414141` 取指令**，而這個值完全來自你送的 `A` 字串。這代表你對「指令指標接下來要去哪裡」有了控制權——這就是整個 exploit 開發的起點。

### 如何在你自己的環境驗證

1. 啟動 vulnserver，WinDbg Attach to Process 掛上去，按 `g` 繼續跑。
2. 跑上面的 fuzzer，逐步加大 size。
3. 每次崩潰，記下 WinDbg 顯示的 `eip` 值和整個暫存器區塊，截圖或複製下來——這是你之後對照 Ch 8 offset 計算的原始證據。
4. 如果 `eip` 不是全 `0x41` 構成，先看 `esp`/`ebp` 的內容是否也被 `A` 填滿——這通常代表你蓋到的範圍夠大，只是精確位置還沒算出來（這正是 Ch 8 要解決的問題）。

## 對比取捨：遞增 fuzzing vs 直接送超大 payload

| 做法 | 優點 | 缺點 |
|---|---|---|
| 遞增長度逐步撞（本章） | 能估出大概的 buffer 邊界，觀察崩潰行為隨長度變化 | 慢，需要多次請求 |
| 直接送一次超大 payload（如 5000 bytes） | 快，一次確認「有沒有洞」 | 不知道精確邊界，之後定位 offset 還是得靠 Ch 8 的 cyclic pattern，不如直接跳過遞增 |

OSED 實務上常見做法是：**先送一次誇張大的 payload 確認「有洞、EIP 被覆寫」，再用 Ch 8 的 cyclic pattern 直接算精確 offset**，省掉遞增撞邊界的步驟。本章用遞增法是為了讓你紮實理解 fuzzing 的原理；之後你會發現對已知弱點的指令（如 TRUN），直接送大 payload 更有效率。

## 踩雷集錦

1. **錯誤直覺：EIP 變成某個亂碼位址就代表 exploit 成功了。**
   正確理解：`EIP=41414141` 只代表你**控制了執行流要去哪裡**，離「執行你的程式碼」還差三步（offset 精確定位、壞字元、跳板）。這只是起點。

2. **錯誤直覺：fuzzing 時用隨機字元比較接近真實攻擊情境。**
   正確理解：fuzzing 階段故意只用單一字元（通常是 `A` / `0x41`）而非隨機資料，是為了讓崩潰時的暫存器內容「乾淨可辨識」——看到 `41414141` 你立刻知道是你送的資料造成的；換成隨機 byte，你反而分不清是哪個位置的資料跑到 `EIP` 裡。

3. **錯誤直覺：服務連線斷掉 = 服務崩潰了。**
   正確理解：連線斷開可能只是該次請求處理失敗、連線被動關閉，進程本身可能還活著（甚至自動重啟）。唯一可信的崩潰判斷來源是 WinDbg 裡的 access violation 中斷，不是 socket 的行為。

4. **錯誤直覺：vulnserver 每個指令都有洞，可以隨便挑一個練。**
   正確理解：vulnserver 的指令各自藏不同種類的漏洞（甚至有些指令根本沒洞，是正常功能）。TRUN 是公開最廣為人知的 vanilla stack overflow 範例，但練習 A 會要你換一個指令重做一次整套流程——不能假設「看起來像」就是同一種洞。

5. **錯誤直覺：fuzzer 跑完一次沒崩就代表這個指令沒洞。**
   正確理解：遞增的步伐、payload 的格式（例如要不要加 `.`、要不要有特定 prefix）都可能影響是否觸發得到洞。沒崩可能只是你的 fuzz 格式不對，不代表該指令真的安全——這點在練習 A 換指令時會更明顯。

## 進階：再往深一層

- **協定感知型 fuzzing**：本章的 fuzzer 完全不懂 vulnserver 的協定語法，只是對已知指令的參數硬撞。真正的 fuzzer（如 boofuzz）會先解析協定結構，對每個欄位分別變異——OSED 考場時間有限，通常手寫腳本針對已知指令就夠，但面對完全陌生協定時，Ch 15（逆向網路服務）會教你怎麼先讀懂協定再動手。
- **WinDbg 的 `sxe av` 設定**：讓 WinDbg 一遇到 access violation 就自動中斷（而不是略過第一次 chance），這在 Ch 4–5 已教過，fuzzing 階段務必確認這個設定開著，否則你可能錯過第一次 crash 的瞬間狀態。

## 重點整理

- OSED 打穿一個 vanilla stack overflow 分五步：fuzzing → 控 EIP → 壞字元 → jump point → shellcode。本章完成前兩步。
- Fuzzing 用遞增長度撞出崩潰邊界，故意用單一字元（`A`/`0x41`）讓崩潰狀態可辨識。
- `EIP=41414141` 代表你控制了執行流指向，是後續所有步驟的前提，但離可用的 exploit還有三步。
- 判斷「崩潰」要看 WinDbg 的 access violation，不能只看 socket 連線狀態。

## 自我檢核

- [ ] 能說出 OSED 五步法的順序與每步解決的問題
- [ ] 能解釋為什麼 fuzzing 故意用單一字元而非隨機資料
- [ ] 能說出 `EIP=41414141` 代表什麼、不代表什麼
- [ ] 能講出至少一種「連線斷開但不算真正崩潰」的情境
- [ ] 知道為什麼要在 WinDbg 裡確認而不是只靠 fuzzer 腳本的回傳值判斷

## 延伸閱讀

- **[Corelan — Exploit Writing Tutorial part 1: Stack Based Overflows](https://www.corelan.be/index.php/2009/07/19/exploit-writing-tutorial-part-1-stack-based-overflows/)** — Peter Van Eeckhoutte
  - 讀哪裡：全篇，尤其是 fuzzing 與初次崩潰確認的段落。
  - 學什麼：本章五步法的原始出處，和 vanilla stack overflow 的經典範例。
  - 前提：無，是整個系列的開篇。

- **[vulnserver（GitHub）— Stephen Bradshaw](https://github.com/stephenbradshaw/vulnserver)**
  - 讀哪裡：README 的指令列表與原始碼中 TRUN 的處理邏輯。
  - 學什麼：確認 TRUN 的協定格式與已知弱點描述，練習 A 選別的指令時也從這裡找線索。
  - 前提：Ch 0 已裝好環境。

- **本 repo [`windows_pwn` Ch 19 — stack buffer overflow](../windows_pwn/19-stack-overflow.md)**
  - 讀哪裡：stack frame 圖與 x86 cdecl 的 saved EIP 覆寫原理。
  - 學什麼：和本章「底層機制」段落交叉對照，理解 OSED 的 vulnserver 案例和該章 mingw 靶程式本質是同一種漏洞。
  - 前提：已讀過 x86 calling convention（本課 Ch 3）。

- **[FuzzySecurity — Windows Exploit Development Tutorial Series](https://www.fuzzysecurity.com/tutorials.html)** — b33f (Ruben Boonen)
  - 讀哪裡：Part 1 的 fuzzing 與初次 crash 分析章節。
  - 學什麼：另一個角度的 fuzz → crash 流程範例，可和 Corelan 的講法互相補強。
  - 前提：無。

確認你能重複穩定讓 vulnserver 崩潰、`EIP` 被 `A` 覆寫之後，下一章要解決兩個問題：**覆寫 EIP 的精確 offset 是多少**，以及**哪些 byte 不能用在你的 payload 裡**。

→ [Ch 8 壞字元分析與 offset 定位](./08-badchars-and-offset.md)
