# Ch 22 — seccomp-bpf：syscall 過濾

> 目標：認識 seccomp-bpf 的歷史、它仍然用 cBPF（不是 eBPF）的原因、`seccomp_data` struct、寫一個基本的 syscall allowlist、看 Docker default profile 怎麼運作。

## seccomp 歷史

Linux 2.6.12 (2005) 加入原始 seccomp — 一個 process 進 seccomp 模式後**只准呼叫四個 syscall**：`read`, `write`, `_exit`, `sigreturn`。**真的就這四個**，太死板，幾乎沒人用。

2012 加入 **seccomp-bpf**：讓 user 用 cBPF program 自訂「哪個 syscall 過、哪個 syscall 阻擋」。一夕之間變成 sandbox 的核心：

- **Chrome** 的 renderer process
- **Docker / Kubernetes** 的 default profile（去掉一堆危險 syscall）
- **Firefox** 的 web content
- **systemd** 的 service hardening

## 為什麼還是 cBPF 不是 eBPF

seccomp filter 出現在 eBPF 之前。eBPF 普及後 kernel 沒把 seccomp 升級成 eBPF — 主因：

- **能力受限是好事**：seccomp filter 應該被簡單、易審計。eBPF 的能力反而是負擔
- **API 已穩定**：大量工具（libseccomp、systemd、Docker）綁 cBPF 介面
- **沒迫切需求**：cBPF 對「看 syscall + arg + 決定 allow/deny」夠用

但 5.x 後加了一個現代版：**BPF_PROG_TYPE_LSM**（Ch 23）— 如果你要寫複雜安全策略，用 BPF LSM。seccomp 仍是「快速 syscall allowlist」的標準工具。

## seccomp_data：filter 看到的世界

每次 process 呼叫 syscall，seccomp filter 跑，輸入是這個 struct：

```c
struct seccomp_data {
    int   nr;                      // syscall number
    __u32 arch;                    // 哪個架構（x86_64 / arm64 ...）
    __u64 instruction_pointer;     // 觸發 syscall 的 user IP
    __u64 args[6];                 // syscall 參數 0–5
};
```

filter 可以看 syscall number 與 args，但**不能看記憶體**（args 可能是 user-space pointer，pointer 內容看不到 — 否則會有 TOCTOU race）。

## Filter return value

cBPF return value 高 16 bit 是動作：

| Return | 動作 |
|---|---|
| `SECCOMP_RET_ALLOW` | 放行 |
| `SECCOMP_RET_KILL_PROCESS` | 立刻殺整個 process |
| `SECCOMP_RET_KILL_THREAD` | 殺呼叫的 thread |
| `SECCOMP_RET_ERRNO \| (errno)` | syscall 回傳這個 errno（low 16 bit） |
| `SECCOMP_RET_LOG` | log 但放行 |
| `SECCOMP_RET_TRACE` | 通知 ptrace tracer |
| `SECCOMP_RET_USER_NOTIF` | 通知 user-space supervisor 決定 |

`SECCOMP_RET_USER_NOTIF` 是 5.0+ 加的 — 讓你寫一個 supervisor process 動態決定，是 OCI runtime 高階 sandbox 的基礎。

## 寫一個基本 filter — 阻擋 write

```c
#include <linux/seccomp.h>
#include <linux/filter.h>
#include <linux/audit.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <stdio.h>

int main() {
    struct sock_filter filter[] = {
        // 載入 arch
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, arch)),
        // 確認是 x86_64
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, AUDIT_ARCH_X86_64, 1, 0),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS),
        // 載入 syscall number
        BPF_STMT(BPF_LD | BPF_W | BPF_ABS, offsetof(struct seccomp_data, nr)),
        // write 嗎？
        BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K, __NR_write, 0, 1),
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ERRNO | (EACCES & 0xFFFF)),
        // 其他放行
        BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW),
    };
    struct sock_fprog prog = { .len = sizeof(filter)/sizeof(*filter), .filter = filter };

    prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0);
    prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &prog);

    // 試試 write — 應該失敗
    if (write(1, "hello\n", 6) < 0) perror("write");
    return 0;
}
```

build & run：

```bash
gcc seccomp-test.c -o seccomp-test
./seccomp-test
# write: Permission denied
```

`prctl(PR_SET_NO_NEW_PRIVS, 1, ...)` **必加** — 跟 seccomp 一起鎖住 setuid 行為。

## libseccomp — 不要手寫 cBPF

直接寫 cBPF 維護痛苦。實務都用 [libseccomp](https://github.com/seccomp/libseccomp)：

```c
#include <seccomp.h>

int main() {
    scmp_filter_ctx ctx = seccomp_init(SCMP_ACT_ALLOW);   // 預設全允許

    // 阻擋 ptrace
    seccomp_rule_add(ctx, SCMP_ACT_KILL, SCMP_SYS(ptrace), 0);

    // 阻擋 exec 的 path 是 /bin/sh
    seccomp_rule_add(ctx, SCMP_ACT_KILL, SCMP_SYS(execve), 1,
                     SCMP_A0(SCMP_CMP_EQ, (scmp_datum_t)"/bin/sh"));

    seccomp_load(ctx);
    seccomp_release(ctx);
}
```

`seccomp_load` 內部編譯成 cBPF + 呼叫 syscall。乾淨多了。

## Docker default profile

Docker 預設 `--security-opt seccomp` 是 [這份 JSON](https://github.com/moby/moby/blob/master/profiles/seccomp/default.json)。把幾百個 syscall 標 `SCMP_ACT_ALLOW`，少數危險的（`reboot`、`mount`、`init_module`、`kexec_load`、`ptrace`...）標 `SCMP_ACT_ERRNO`。

容器啟動時 runc 把 JSON 轉成 cBPF filter 套到 process。**這是你的容器跑「無 root cap、無危險 syscall」的核心機制**。

關掉看看：

```bash
docker run --security-opt seccomp=unconfined ...   # 危險，不要在 prod 開
```

## seccomp 的盲點

- **無法看 memory**：filter 不能驗證 syscall pointer 指向的內容（避免 TOCTOU）。所以「阻擋 `open("/etc/passwd")`」這種需求 seccomp 做不到 — 改用 LSM（Ch 23）
- **filter 一旦 load 不能拿掉**：設計如此，避免被反向 bypass
- **不能看 process state**：純 syscall 介面 — 想做「process 在哪個 namespace 才阻擋」要靠 LSM

## 一個常見誤解

「seccomp 就是 BPF 防護」 — **錯**。

seccomp 只看 syscall + arg。要做：「禁止讀 /etc/shadow」「禁止連 1.2.3.4」「禁止 process 改 file capability」 — 這些 seccomp 都不行，要 LSM。

seccomp 是「最常用」但**不是「最有能力」**的 BPF 安全機制。

## 動手練習

1. **寫一個 syscall allowlist**：用 libseccomp 寫一個只准 read/write/exit 的 filter，跑 `ls` 看會被殺。
2. **看 Docker profile**：讀 `default.json`，挑五個被擋的 syscall 查它們是什麼。
3. **比較 seccomp + 沒 seccomp 啟動 nginx 的開銷**：seccomp 確實有 overhead（每個 syscall 跑 cBPF），但通常 < 1%。
4. **用 SECCOMP_RET_LOG**：寫一個 filter 不殺只 log，跑一個 web server 30 秒，看 `/var/log/audit/audit.log` 哪些 syscall 被觸發。

## 自我檢核

- [ ] 我能解釋 seccomp 為什麼還是用 cBPF
- [ ] 我能列出 5 個 SECCOMP_RET 動作
- [ ] 我能用 libseccomp 寫 deny / allow 規則
- [ ] 我知道 Docker default profile 攔截哪類 syscall
- [ ] 我知道 seccomp 不能做什麼（要靠 LSM）

下一章我們進現代 BPF 安全的主流：BPF LSM。它是 SELinux / AppArmor 的同位素，但你寫 eBPF 而不是政策語言。

→ [Ch 23 BPF LSM：kernel 級安全鉤子](./23-bpf-lsm.md)
