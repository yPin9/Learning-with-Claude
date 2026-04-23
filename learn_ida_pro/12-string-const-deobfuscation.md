# Ch 12 — 字串 / 常數解混淆

> 目標：處理 stack string、XOR table、簡易 VM opcode — 用 IDAPython 還原並把結果寫回 IDB（comment 或 patch bytes）。

## Malware 為什麼要混淆字串

直接在 `.rodata` 放 `"http://evil.com/c2"` → 靜態掃描器一抓一個準。所以 malware 作者把字串：

1. **編譯時 XOR / RC4 加密**，塞進 binary 的 data section，runtime 解密。
2. **拆成 stack string**：一堆 `mov byte ptr [rsp+N], 'h'; mov byte ptr [rsp+N+1], 't'; ...` 塞進 stack，不進 `.rodata`。
3. **從數學運算組出字元**：`char c = (key * 3 + salt) ^ 0x5F;`
4. **走 VM opcode 逐步還原**：最狠的一種，字串是 bytecode，VM 跑完才算解開。

前三種用 IDAPython 能通殺，第四種要案例分析。這章專做前三種。

## 類型 1：Stack string 還原

特徵：連續幾條 `mov` 把字元塞進 stack：

```asm
mov  byte ptr [rsp+0x20], 'h'
mov  byte ptr [rsp+0x21], 't'
mov  byte ptr [rsp+0x22], 't'
mov  byte ptr [rsp+0x23], 'p'
mov  byte ptr [rsp+0x24], 's'
mov  byte ptr [rsp+0x25], ':'
...
```

或是用 dword / qword 批次塞：

```asm
mov  dword ptr [rsp+0x20], 'sptth'   ; "htps" (little endian)
mov  dword ptr [rsp+0x24], '//:'
mov  word  ptr [rsp+0x28], 0x7665    ; "ev"
```

### 偵測 + 還原腳本

```python
import idautils, ida_funcs, ida_ua, ida_bytes, ida_nalt
import ida_name

def recover_stack_strings_in_func(func_ea):
    """收集 function 裡的 stack string 並印出"""
    f = ida_funcs.get_func(func_ea)
    if not f:
        return

    # 收集 (stack_offset, byte_value, ea) 的 mov
    by_off = {}                             # stack_offset -> [(byte, ea), ...]
    for ea in idautils.FuncItems(func_ea):
        insn = ida_ua.insn_t()
        if ida_ua.decode_insn(insn, ea) <= 0:
            continue
        if ida_ua.print_insn_mnem(ea).lower() != "mov":
            continue
        dst, src = insn.ops[0], insn.ops[1]
        if dst.type != ida_ua.o_displ or src.type != ida_ua.o_imm:
            continue

        # 拆 imm 成 bytes（看 dst 的 access size）
        size = {ida_ua.dt_byte: 1, ida_ua.dt_word: 2, ida_ua.dt_dword: 4, ida_ua.dt_qword: 8}.get(dst.dtype, 0)
        if size == 0:
            continue

        val = src.value
        base_off = dst.addr
        if base_off > 0x100000:
            base_off -= 0x100000000

        for i in range(size):
            b = (val >> (i * 8)) & 0xFF
            if 0x20 <= b < 0x7F or b == 0:    # 可列印 or NUL
                by_off.setdefault(base_off + i, []).append((b, ea))

    # 組連續片段
    if not by_off:
        return

    offsets = sorted(by_off)
    groups = []
    current = []
    prev = None
    for o in offsets:
        if prev is None or o == prev + 1:
            current.append(o)
        else:
            if len(current) >= 4:
                groups.append(current)
            current = [o]
        prev = o
    if len(current) >= 4:
        groups.append(current)

    # 輸出
    for grp in groups:
        chars = bytes(by_off[o][-1][0] for o in grp)     # 取 last write（最後一次覆蓋的值才是定版）
        s = chars.rstrip(b"\x00").decode("latin1", errors="replace")
        anchor_ea = by_off[grp[0]][0][1]
        print(f"[{anchor_ea:#x}] stackstr @ rsp+{grp[0]:#x}: {s!r}")
        # 寫 repeatable comment 到第一個 mov 的 ea
        ida_bytes.set_cmt(anchor_ea, f'stackstr: "{s}"', True)

# 掃整個 binary
for func_ea in idautils.Functions():
    recover_stack_strings_in_func(func_ea)
```

這份對 9 成 stack string 都通。局限：

- 多次覆寫同 byte 沒完整歷史（我們只取最後一次）。大部分 case 夠用。
- Byte order 依 operand 與架構推定，x86 / x86-64 預設 little endian。

## 類型 2：XOR table 還原

特徵：binary 裡有一段 bytes 是密文 + 一個 function 在 runtime 跑 XOR loop。

典型 XOR loop pseudocode：

```c
for (i = 0; i < len; i++)
    dst[i] = src[i] ^ key[i % key_len];
```

### 半自動流程

假設你已經人工識別：
- 密文位址 `0x405000`，長度 128
- key 位址 `0x405100`，長度 16

用 script 直接解：

```python
import ida_bytes

def xor_decrypt(src_ea, key_ea, size, key_size):
    src = ida_bytes.get_bytes(src_ea, size)
    key = ida_bytes.get_bytes(key_ea, key_size)
    out = bytes(b ^ key[i % key_size] for i, b in enumerate(src))
    return out

plain = xor_decrypt(0x405000, 0x405100, 128, 16)
print(plain)
# b'http://evil.com/c2/beacon\x00...'
```

### 寫回 IDB

解出的結果有兩種寫回方式：

**方式 A：加 comment（不動 bytes）**

```python
def annotate_decrypted_string(ea, decrypted):
    s = decrypted.split(b"\x00")[0].decode("latin1", errors="replace")
    ida_bytes.set_cmt(ea, f'decrypted: "{s}"', True)
```

安全、可逆、隨時可改。**優先選這個。**

**方式 B：patch bytes**

```python
ida_bytes.patch_bytes(0x405000, plain)
ida_bytes.create_strlit(0x405000, len(plain.split(b"\x00")[0]), ida_nalt.STRTYPE_C)
```

優點：之後每次看到 `0x405000` 就看到明文字串，不用翻 comment。缺點：IDB 的 raw bytes 被改，某些場景（對照原檔）會混淆。

**我的慣例**：
- 分析用 → comment
- 要產報告 / 截圖給別人看 → patch

## 類型 3：拆解 XOR loop function 自動化

如果 binary 有 10 個不同位址呼叫同一個 decrypt function，每次傳不同 src / key，你不想手動挑：

```python
import idautils, ida_xref, ida_ua, ida_bytes
import ida_funcs, ida_name
import idaapi

DECRYPT_FUNC_EA = 0x402000    # 你已識別出的 decrypt function

def find_call_sites(func_ea):
    return [x.frm for x in idautils.XrefsTo(func_ea, 0)
            if x.type in (ida_xref.fl_CN, ida_xref.fl_CF)]

def extract_args_above(call_ea, num_args=3):
    """
    往 call 上面看 num_args 條指令，找 mov rdi / rsi / rdx 的 immediate（x86-64 SysV 前 3 個 int args）
    超級簡化版 — 生產用要做 basic-block 內的 dataflow。
    """
    args = {"rdi": None, "rsi": None, "rdx": None, "rcx": None}
    ea = ida_bytes.prev_head(call_ea)
    for _ in range(30):                               # 往上最多看 30 條
        if ea == idaapi.BADADDR:
            break
        insn = ida_ua.insn_t()
        if ida_ua.decode_insn(insn, ea) <= 0:
            ea = ida_bytes.prev_head(ea)
            continue

        mnem = ida_ua.print_insn_mnem(ea).lower()
        if mnem in ("mov", "lea"):
            dst, src = insn.ops[0], insn.ops[1]
            if dst.type == ida_ua.o_reg:
                reg_name = ida_ua.print_operand(ea, 0).lower()
                if reg_name in args and args[reg_name] is None:
                    if src.type in (ida_ua.o_imm, ida_ua.o_mem):
                        args[reg_name] = src.value if src.type == ida_ua.o_imm else src.addr
        if all(v is not None for v in list(args.values())[:num_args]):
            break
        ea = ida_bytes.prev_head(ea)
    return args

for call_ea in find_call_sites(DECRYPT_FUNC_EA):
    args = extract_args_above(call_ea)
    print(f"{call_ea:#x}  {args}")
    # 根據 calling convention 拿 src / key / size，呼叫 xor_decrypt
```

**這份 script 的誠實評語**：它對「args 直接是 mov immediate」有效，對「args 來自暫存器算式」會失敗。真實 binary 大概 50-70% 命中。剩下要手動補。

這是自動化的 pareto 原則 — 寫 script 吃掉簡單 80%，省下時間手動解決難的 20%。

## 類型 4：小 VM 解讀

特徵：看到一段 dispatcher code：

```c
while (pc < end) {
    op = bytecode[pc++];
    switch (op) {
        case 0x01: ...; break;
        case 0x02: ...; break;
        ...
    }
}
```

bytecode 本身放在 `.rodata`。解讀方法：

1. **把 dispatcher 的每個 case 手動反組譯**，搞清楚每個 opcode 做什麼。
2. **把 bytecode 用 Python 寫一個 mini interpreter 跑**，輸出每條指令 log。
3. 結果通常能看到還原的 string / behavior trace。

這超出「script 自動化」範圍，因為每個 sample 的 VM 不同。IDAPython 在這裡的角色是：自動匯出 bytecode bytes + 輔助標註 dispatcher 的每個 handler function。

## 踩雷 & 經驗

- **XOR key 可能是 running key**：每次 iteration 更新 key（key = key * 31 + 7），不是靜態 table。這種要看 loop body 的算法。
- **多層加密**：base64 → RC4 → GZIP。要一層層剝。
- **解密後還是亂碼**：key 找錯、endian 錯、或 shift 對齊錯。在 script 裡印前 16 bytes 十六進位 + ASCII 雙欄對照，多少看得出問題。
- **NOT (取反) 不是 XOR**：`NOT AL` 等於 `AL ^ 0xFF`，特例，不是任意 key。
- **ADD / SUB 當「XOR」**：有些懶作者用 `byte += key_byte`。邏輯一樣，算式不同。

## 動手練習

1. 寫一個 real-world malware sample（合法來源如 MalwareBazaar 的 unpacked loader）跑 stack string 還原。
2. 找一個有 XOR 字串的 binary，手動定位一個解密呼叫，用 script 跑 xor_decrypt，把結果寫成 comment。
3. 擴充 `extract_args_above`：支援 x86 32-bit 的 stack-based calling convention（args 在 `[esp+N]`）。
4. 難題：寫一個 script 自動找「XOR loop」模式 — 線索是 `xor reg, reg2` 在 loop 裡、有 `cmp` 檢查 length、有寫回 memory 的 mov。

## 自我檢核

- [ ] 能識別 stack string 的 mov pattern
- [ ] 能用 `ida_bytes.get_bytes` 讀密文、xor 解密、寫回 comment
- [ ] 知道 patch 和 comment 的 trade-off
- [ ] 知道 dataflow 的限制（`extract_args_above` 只做簡化版）
- [ ] 知道 VM-based 混淆需要另外做 interpreter

Part 2 到這結束 — 你已經會寫對 stripped binary 有生產力的 script。下一站進 **練習 B**：綜合前五章，寫一支自動 annotate 工具。

→ [練習 B：stripped binary 自動 annotate](./practice-b-auto-annotate.md)
