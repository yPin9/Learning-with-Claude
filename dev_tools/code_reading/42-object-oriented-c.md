# Ch 42 — C 語言裡的物件導向：struct 嵌入、vtable、kobject

> **目標**：在 kernel 程式碼裡一眼認出「這是一個類別階層」「這是向下轉型」「這是虛擬函式呼叫」「這是建構子／解構子」，能把 `tcp_sk(sk)`、`EXT4_I(inode)`、`kobj_to_dev(kobj)` 這類巨集翻譯成 C++ 的心智模型，並理解 kobject 怎麼把物件模型延伸到 sysfs 與生命週期管理。

> **鴻溝**：本章主要練**慣用法鴻溝**（`container_of` 階層、ops table、release callback 的寫法）與**間接鴻溝**（多型呼叫讓呼叫鏈斷掉）。

> **環境**：Linux kernel v6.18 原始碼；WSL2 Ubuntu 22.04，gcc／g++ 11.4。struct 大小以 `pahole` v1.25 量本課 `~/code_reading/build`（x86_64）的物件檔，換 config 會變。

## 為什麼需要這個？

Ch 41 結尾留了一個問題：VFS 拿到的是 `struct inode *`，ext4 卻能從它取出 `struct ext4_inode_info` 的私有欄位；`vfs_read` 呼叫的是 `f_op->read_iter`，實際跑的卻是 `ext4_file_read_iter`。這兩件事在 C++ 裡分別叫做**向下轉型**與**虛擬函式**，但 kernel 是用 C 寫的。

kernel 不用 C++ 有它的歷史理由（Linus 在 2004 年前後的郵件裡把 C++ 的例外處理、隱藏的記憶體配置、編譯器生成程式碼的不可預測性列為主要顧慮，這裡不展開爭論）。但它要解決的問題和任何大型 OO 系統一樣：**幾十種檔案系統、幾百種網路協定、幾千種 driver，要共用同一套上層邏輯**。於是 kernel 用 C 手工打造了一套物件模型，而且這套模型有固定的慣用法。

這對讀碼的意義很直接：如果你不知道這套慣用法，`tcp_sk(sk)->snd_cwnd` 看起來就是一個神秘巨集加一個神秘欄位；一旦你知道「這是把基底類別指標轉成衍生類別指標」，整段程式碼的意圖就浮出來了。Spinellis 在《Code Reading》Ch 9.3「Element Packaging」裡談到物件與抽象資料型別作為封裝單位，也專門提到 C 程式如何用 struct 加函式指標模擬物件——這正是 kernel 把它推到極致的地方。

## 先建立直覺

把 kernel 的物件想成**俄羅斯娃娃加一支遙控器**：

```
  struct tcp_sock (2304 bytes)
  ┌─────────────────────────────────────────────┐
  │ struct inet_connection_sock (1384)          │
  │ ┌─────────────────────────────────────────┐ │
  │ │ struct inet_sock (1000)                 │ │
  │ │ ┌─────────────────────────────────────┐ │ │
  │ │ │ struct sock (776)                   │ │ │
  │ │ │   sk_prot ──────────────► tcp_prot   │ │ │  ← 遙控器（vtable）
  │ │ │   sk_state, sk_data_ready, ...      │ │ │
  │ │ └─────────────────────────────────────┘ │ │
  │ │   inet_flags, inet_saddr, ...           │ │
  │ └─────────────────────────────────────────┘ │
  │   icsk_accept_queue, icsk_ca_ops, ...       │
  └─────────────────────────────────────────────┘
    snd_cwnd, rcv_nxt, ...（TCP 自己的欄位）
```

- **最內層的娃娃**（`struct sock`）是基底類別。所有通用網路程式碼只拿著指向它的指標。
- **外層娃娃**把內層整個包住，所以指向內層的指標和指向外層的指標，在記憶體上有固定的位移關係（這張圖裡位移剛好是 0）。`container_of` 就是用這個位移從內層找回外層（Ch 16）。
- **遙控器**（`sk_prot` 指向的 `struct proto`）是一張函式指標表。通用程式碼按遙控器上的按鈕（`sk->sk_prot->sendmsg(...)`），實際執行的是 TCP 或 UDP 的版本。

這個類比的限制：真實的 C++ 物件裡，編譯器幫你維護 vptr 與轉型安全；kernel 的娃娃沒有任何執行期型別檢查，把 UDP 的娃娃當 TCP 的打開，編譯器不會攔你。下面的實驗會故意做這件事。

> 如果你對 `container_of` 的位移計算還不熟，先回看 [Ch 16 — container_of](./16-container-of.md)。

## 先在 user space 做一次：C 與 C++ 的等價版本

在讀 kernel 的真實階層之前，先用一個縮小版把四個 OO 概念（封裝、繼承、多型、建構）各自對應到 C 的寫法。下面的 C 程式刻意模仿 kernel 的命名。

```c
/* oo.c — 用 C 模仿 kernel 的 sock -> inet_sock -> tcp_sock 階層 */
#include <stdio.h>
#include <stdlib.h>
#include <stddef.h>
#include <string.h>

#define container_of(ptr, type, member) \
    ((type *)((char *)(ptr) - offsetof(type, member)))

struct sock;
struct proto {                       /* vtable + 類別資訊 */
    const char *name;
    size_t obj_size;                 /* 「這個類別的物件多大」：給工廠用 */
    void (*init)(struct sock *sk);
    int  (*sendmsg)(struct sock *sk, int len);
};

struct sock {                        /* 基底類別 */
    int state;
    const struct proto *prot;        /* 每個物件一個 vptr */
};

struct inet_sock {                   /* 衍生一層 */
    struct sock sk;                  /* 必須放第一個？不是必須，見下文 */
    unsigned short sport;
};

struct tcp_sock {                    /* 再衍生一層 */
    struct inet_sock inet;
    unsigned int snd_cwnd;
};

#define inet_sk(p) container_of(p, struct inet_sock, sk)
#define tcp_sk(p)  container_of(p, struct tcp_sock, inet.sk)

static void tcp_init(struct sock *sk) { tcp_sk(sk)->snd_cwnd = 10; }
static int tcp_sendmsg(struct sock *sk, int len)
{
    struct tcp_sock *tp = tcp_sk(sk);              /* 向下轉型 */
    printf("  tcp_sendmsg: sport=%u cwnd=%u len=%d\n",
           inet_sk(sk)->sport, tp->snd_cwnd, len);
    return len;
}
static int udp_sendmsg(struct sock *sk, int len)
{
    printf("  udp_sendmsg: sport=%u len=%d\n", inet_sk(sk)->sport, len);
    return len;
}

static const struct proto tcp_prot = { "TCP", sizeof(struct tcp_sock), tcp_init, tcp_sendmsg };
static const struct proto udp_prot = { "UDP", sizeof(struct inet_sock), NULL,   udp_sendmsg };

/* 工廠 + 建構子：照 prot->obj_size 配置，再呼叫 prot->init */
static struct sock *sk_alloc(const struct proto *prot, unsigned short port)
{
    struct sock *sk = calloc(1, prot->obj_size);
    sk->prot = prot;
    inet_sk(sk)->sport = port;
    if (prot->init)
        prot->init(sk);
    return sk;
}

int main(void)
{
    struct sock *socks[] = { sk_alloc(&tcp_prot, 8080), sk_alloc(&udp_prot, 53) };

    printf("sizeof: sock=%zu inet_sock=%zu tcp_sock=%zu\n",
           sizeof(struct sock), sizeof(struct inet_sock), sizeof(struct tcp_sock));
    printf("offsetof(tcp_sock, inet.sk)=%zu\n", offsetof(struct tcp_sock, inet.sk));

    for (int i = 0; i < 2; i++) {                   /* 多型呼叫 */
        printf("%s:\n", socks[i]->prot->name);
        socks[i]->prot->sendmsg(socks[i], 100);
    }

    /* 錯誤示範：把 UDP 物件當 TCP 用，編譯器不會阻止你 */
    printf("bogus cast: cwnd=%u\n", tcp_sk(socks[1])->snd_cwnd);
    return 0;
}
```

```
$ gcc -Wall -Wextra -O0 -g -o oo oo.c && ./oo
sizeof: sock=16 inet_sock=24 tcp_sock=32
offsetof(tcp_sock, inet.sk)=0
TCP:
  tcp_sendmsg: sport=8080 cwnd=10 len=100
UDP:
  udp_sendmsg: sport=53 len=100
bogus cast: cwnd=4113
```

`-Wall -Wextra` 沒有任何警告，最後一行卻讀出了垃圾值（這次是 4113，每次跑可能不同）：UDP 物件只配置了 24 bytes，`snd_cwnd` 在第 24–27 byte，已經在物件外面。用 AddressSanitizer 才抓得到：

```
$ gcc -Wall -O0 -g -fsanitize=address -o oo_asan oo.c && ./oo_asan
==319462==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x503000000088 ...
READ of size 4 at 0x503000000088 thread T0
    #0 0x61ca65b4385e in main /home/ypp/code_reading/scratch/41-45/oo.c:78
...
0x503000000088 is located 0 bytes to the right of 24-byte region [0x503000000070,0x503000000088)
allocated by thread T0 here:
    #0 0x7f404feb4a57 in __interceptor_calloc ...
    #1 0x61ca65b434aa in sk_alloc /home/ypp/code_reading/scratch/41-45/oo.c:56
```

同一個階層的 C++ 版本：

```cpp
// oo.cpp — 同一個階層，用 C++ 寫
#include <cstdio>
#include <cstddef>

struct Sock {
    int state = 0;
    virtual ~Sock() = default;
    virtual const char *name() const = 0;
    virtual int sendmsg(int len) = 0;
};

struct InetSock : Sock {
    unsigned short sport;
    explicit InetSock(unsigned short p) : sport(p) {}
};

struct TcpSock : InetSock {
    unsigned int snd_cwnd = 10;               // 建構子取代 tcp_init
    using InetSock::InetSock;
    const char *name() const override { return "TCP"; }
    int sendmsg(int len) override {
        std::printf("  tcp_sendmsg: sport=%u cwnd=%u len=%d\n", sport, snd_cwnd, len);
        return len;
    }
};

struct UdpSock : InetSock {
    using InetSock::InetSock;
    const char *name() const override { return "UDP"; }
    int sendmsg(int len) override {
        std::printf("  udp_sendmsg: sport=%u len=%d\n", sport, len);
        return len;
    }
};

int main()
{
    Sock *socks[] = { new TcpSock(8080), new UdpSock(53) };

    std::printf("sizeof: Sock=%zu InetSock=%zu TcpSock=%zu\n",
                sizeof(Sock), sizeof(InetSock), sizeof(TcpSock));
    for (Sock *s : socks) {
        std::printf("%s:\n", s->name());
        s->sendmsg(100);
    }
    // 錯誤示範的 C++ 版：dynamic_cast 會在執行期檢查型別
    TcpSock *t = dynamic_cast<TcpSock *>(socks[1]);
    std::printf("dynamic_cast<TcpSock*>(udp) = %p\n", (void *)t);
    for (Sock *s : socks) delete s;
}
```

```
$ g++ -Wall -Wextra -O0 -g -std=c++17 -o oocpp oo.cpp && ./oocpp
sizeof: Sock=16 InetSock=16 TcpSock=24
TCP:
  tcp_sendmsg: sport=8080 cwnd=10 len=100
UDP:
  udp_sendmsg: sport=53 len=100
dynamic_cast<TcpSock*>(udp) = (nil)
```

逐項對照：

| OO 概念 | C++ | C（kernel 慣用法） |
|---|---|---|
| 基底類別 | `struct Sock` | `struct sock` |
| 繼承 | `struct InetSock : Sock` | 把 `struct sock sk;` 嵌入成成員 |
| vptr | 編譯器隱藏插入 | 明寫的 `const struct proto *prot` |
| vtable | 編譯器生成 | 手寫的 `static const struct proto tcp_prot = {...}` |
| 虛擬呼叫 | `s->sendmsg(100)` | `sk->prot->sendmsg(sk, 100)`（`this` 要自己傳） |
| 向上轉型 | 隱式 | `&tp->inet.sk`（取成員位址） |
| 向下轉型 | `static_cast` / `dynamic_cast` | `container_of`，**沒有執行期檢查** |
| 建構子 | `TcpSock(...)` | 工廠函式 + `prot->init` hook |
| 物件大小 | `sizeof(TcpSock)`，`new` 自動用 | 存在 vtable 裡的 `obj_size`，工廠讀它來配置 |

有一個細節值得停下來看：C 版 `inet_sock` 是 24 bytes，C++ 版 `InetSock` 只有 16。C 的 `struct sock` 是 `int`（4）+ 4 bytes padding + 指標（8）= 16，嵌入後 padding 留在原地，`sport` 只能放在 16 之後。C++ 的 `Sock` 有虛擬函式，不是 POD 型別，x86-64 的 Itanium C++ ABI 允許衍生類別重用基底的尾端 padding，`sport` 被塞進第 12–13 byte。這個差異說明了一件事：**C 的嵌入繼承在記憶體上就是字面上的「包住」**，你在 `pahole` 裡看到什麼，物件就長什麼樣子。讀 kernel 時這反而是優點。

## 封裝：opaque struct 與 private_data

C 的封裝靠的是**只給宣告、不給定義**。

```c
/* include/linux/mempool.h:13（v6.18）——其他很多 header 也一樣 */
struct kmem_cache;
```

`struct kmem_cache`（slab allocator 的 cache 描述）被 `include/linux/mempool.h`、`kasan.h`、`memcontrol.h` 等十幾個 header 以這種方式前置宣告，真正的定義在 `mm/slab.h:238`——一個**不在 `include/` 底下**的 header。這代表 `mm/` 以外的程式碼只能拿著 `struct kmem_cache *` 呼叫 `kmem_cache_alloc()`，不可能直接讀它的欄位。這是 C 語言能做到最強的封裝：違反它會**編譯失敗**（incomplete type）。

讀碼技巧：當你 `rg '^struct foo \{'` 只在某個子目錄的私有 header（`mm/slab.h`、`kernel/sched/sched.h`、`drivers/base/base.h`）找到定義時，這個 struct 就是該子系統的私有實作細節。外面的人用它只能透過函式，你讀外部程式碼時可以把它當黑箱。

另一種常見的封裝是反方向的：**通用物件留一個 `void *` 給具體實作掛私有資料**。`struct file` 的 `private_data`、`struct super_block` 的 `s_fs_info`、`struct device` 的 `driver_data`（透過 `dev_get_drvdata()`）都是。Ch 43 的 vmgenid driver 會用到 `driver_data`。讀到 `xxx->private_data` 被強制轉型時，往回找它在 `open` 或 `probe` 裡被設成什麼。

## 繼承：struct 嵌入與 `container_of`

kernel 真正的 socket 階層比上面的玩具多一層：

```c
/* include/net/inet_sock.h:212（v6.18） */
struct inet_sock {
	/* sk and pinet6 has to be the first two members of inet_sock */
	struct sock		sk;
	...

/* include/net/inet_connection_sock.h:78（v6.18） */
struct inet_connection_sock {
	/* inet_sock has to be the first member! */
	struct inet_sock	  icsk_inet;
	...

/* include/linux/tcp.h:200（v6.18） */
struct tcp_sock {
	...
	/* inet_connection_sock has to be the first member of tcp_sock */
	struct inet_connection_sock	inet_conn;
```

每一層各有一個「向下轉型」巨集：

```c
/* include/net/inet_sock.h:355 */
#define inet_sk(ptr) container_of_const(ptr, struct inet_sock, sk)
/* include/net/inet_connection_sock.h:145 */
#define inet_csk(ptr) container_of_const(ptr, struct inet_connection_sock, icsk_inet.sk)
/* include/linux/tcp.h:552 */
#define tcp_sk(ptr) container_of_const(ptr, struct tcp_sock, inet_conn.icsk_inet.sk)
```

注意 `tcp_sk` 的 member 參數是 `inet_conn.icsk_inet.sk`——一路穿過三層娃娃。`container_of_const` 是 `container_of` 的變體：傳入 `const struct sock *` 就回傳 `const struct tcp_sock *`，保留 const 性質（所以 `tcp.h:557` 另外有一個 `tcp_sk_rw` 給需要去掉 const 的地方）。

用 `pahole` 對本課 build 出來的 `net/ipv4/tcp.o` 量，四層的大小與位移是：

```
$ cd ~/code_reading/build
$ for s in sock inet_sock inet_connection_sock tcp_sock; do
>   echo "$s: $(pahole -C $s net/ipv4/tcp.o | grep -m1 '/\* size:')"; done
sock: 	/* size: 776, cachelines: 13, members: 108 */
inet_sock: 	/* size: 1000, cachelines: 16, members: 20 */
inet_connection_sock: 	/* size: 1384, cachelines: 22, members: 31 */
tcp_sock: 	/* size: 2304, cachelines: 36, members: 170 */
$ pahole -C tcp_sock net/ipv4/tcp.o | head -2
struct tcp_sock {
	struct inet_connection_sock inet_conn __attribute__((__aligned__(8))); /*     0  1384 */
```

### 「必須放第一個」是真的必須嗎？

`container_of` 本身**不需要**成員在第一個，它會減去正確的位移。ext4 就是反例：

```
$ pahole -C ext4_inode_info fs/ext4/super.o | grep -E 'vfs_inode|size:'
	struct inode               vfs_inode __attribute__((__aligned__(8))); /*   272   592 */
	/* size: 1088, cachelines: 17, members: 45 */
```

`vfs_inode` 在第 272 byte，`EXT4_I()`（`fs/ext4/ext4.h:1789`）照樣用 `container_of(inode, struct ext4_inode_info, vfs_inode)` 正確轉回來。

那 socket 階層為什麼堅持「放第一個」？因為有**其他程式碼依賴位移為 0**。一個例子是 `sk_prot_alloc`（`net/core/sock.c:2225`）：它用 `prot->obj_size` 配置一整塊 `tcp_sock`，然後**直接把回傳值當 `struct sock *`**。如果 `sock` 不在位移 0，這個指標就錯了。另一個例子是 `inet_sock.h:357` 的 `__inet_sk_copy_descendant`，它用 `sizeof` 算出「祖先之後的部分」整段 `memcpy`。註解裡的「has to be the first member」不是風格偏好，而是**對其他程式碼的承諾**。讀到這種註解要當真：改了位置，編譯會過，執行會壞。

### has-a 與 is-a：`struct socket` 與 `struct sock`

網路子系統同時有 `struct socket`（`include/linux/net.h:116`）和 `struct sock`，名字像但關係不是繼承：

```c
/* include/linux/net.h:116（v6.18） */
struct socket {
	socket_state		state;
	short			type;
	unsigned long		flags;
	struct file		*file;
	struct sock		*sk;
	const struct proto_ops	*ops; /* Might change with IPV6_ADDRFORM or MPTCP. */
	struct socket_wq	wq;
};
```

`struct socket` 用**指標**持有 `struct sock`（has-a，組合），它是面向 VFS 與 syscall 的那一半（BSD socket 層）；`struct sock` 是面向協定堆疊的那一半。兩邊各自有一張 vtable：`socket->ops`（`struct proto_ops`，例如 `inet_stream_ops`，`net/ipv4/af_inet.c:1054`）處理 `bind`/`connect`/`accept` 這類 socket 語意；`sk->sk_prot`（`struct proto`，例如 `tcp_prot`，`net/ipv4/tcp_ipv4.c:3485`）處理協定本身。

而 `struct socket` 自己又和一個 inode 綁在一起，用的是**多重繼承**的 C 版本：

```c
/* include/net/sock.h:1502（v6.18） */
struct socket_alloc {
	struct socket socket;
	struct inode vfs_inode;
};

static inline struct socket *SOCKET_I(struct inode *inode)
{
	return &container_of(inode, struct socket_alloc, vfs_inode)->socket;
}
```

一塊記憶體同時「是」一個 socket 和一個 inode：VFS 看到 inode，網路層看到 socket，`SOCKET_I` 在兩個視角之間切換。嵌入兩個基底，就是兩個父類別。kernel 裡更常見的多重繼承是嵌入多個 `list_head`／`rb_node`：一個物件同時是好幾個容器的成員（Ch 23）。

## 多型：ops table 就是 vtable

kernel 的多型和 C++ 有三個重要差異，讀碼時要放在心上。

**差異一：vptr 的位置不固定。** C++ 的 vptr 一律在物件開頭；kernel 的 ops 指標可以在物件裡任何位置、而且一個物件可以有好幾個（inode 有 `i_op`、`i_fop`、`i_mapping->a_ops`）。有時 ops 指標甚至不在物件本身，而在它的「類別物件」裡：`inode->i_sb->s_op` 是透過 superblock 找到的。

**差異二：可以逐物件覆寫單一方法。** `struct sock` 裡有一組**直接存在物件上**的函式指標：

```c
/* net/core/sock.c:3661（v6.18），sock_init_data_uid() 內 */
	sk->sk_state_change	=	sock_def_wakeup;
	sk->sk_data_ready	=	sock_def_readable;
	sk->sk_write_space	=	sock_def_write_space;
	sk->sk_error_report	=	sock_def_error_report;
```

這些是預設值。kernel 內部使用 socket 的模組（例如 NFS client、iSCSI、TLS、BPF sockmap）會把**某一個 socket 物件**的 `sk_data_ready` 換成自己的 callback，其他 socket 不受影響。C++ 做不到這件事（vtable 是整個類別共用的），這更接近 JavaScript 的 prototype 物件。讀碼時遇到 `sk->sk_data_ready(sk)` 這種呼叫，**光找類別的 vtable 不夠**，要找所有對這個欄位賦值的地方。Ch 44 會把它當成事件驅動的 callback 再看一次。

**差異三：「方法不存在」是合法狀態。** C++ 的純虛擬函式必須被實作；kernel 的 ops 欄位常常是 `NULL`，呼叫端要先檢查（`if (file->f_op->read)`），或者通用層在 `NULL` 時退回預設行為（`alloc_inode` 在 `s_op->alloc_inode` 為 `NULL` 時用通用的 inode cache）。這個「`NULL` 代表用預設」的慣例，是 Ch 45 template method 的基礎。

## 建構與解構：工廠 hook 與 refcount

C 沒有建構子，kernel 用兩種方式補。

**一、vtable 提供 `obj_size` 或 alloc hook，通用層當工廠。** 我們玩具版的 `sk_alloc` 就是照抄 kernel：

```c
/* net/core/sock.c:2225（v6.18） */
static struct sock *sk_prot_alloc(struct proto *prot, gfp_t priority,
		int family)
{
	struct sock *sk;
	struct kmem_cache *slab;

	slab = prot->slab;
	if (slab != NULL) {
		sk = kmem_cache_alloc(slab, priority & ~__GFP_ZERO);
		if (!sk)
			return sk;
		if (want_init_on_alloc(priority))
			sk_prot_clear_nulls(sk, prot->obj_size);
	} else
		sk = kmalloc(prot->obj_size, priority);
```

`tcp_prot` 裡寫著 `.obj_size = sizeof(struct tcp_sock)`（`net/ipv4/tcp_ipv4.c:3526`），所以通用的 socket 層不需要知道 TCP 的存在，就能配置出一個完整的 `tcp_sock`。

inode 用的是 hook 而不是大小：

```c
/* fs/inode.c:340（v6.18） */
struct inode *alloc_inode(struct super_block *sb)
{
	const struct super_operations *ops = sb->s_op;
	struct inode *inode;

	if (ops->alloc_inode)
		inode = ops->alloc_inode(sb);
	else
		inode = alloc_inode_sb(sb, inode_cachep, GFP_KERNEL);
```

ext4 的 `ext4_alloc_inode`（`fs/ext4/super.c:1389`）從自己的 `ext4_inode_cachep` 配置一整個 `ext4_inode_info`，回傳其中 `vfs_inode` 的位址。對應的解構是 `.free_inode = ext4_free_in_core_inode`（`fs/ext4/super.c:1438`），它用 `EXT4_I(inode)` 轉回外層再 `kmem_cache_free`。**配置和釋放一定成對出現在同一張 ops 表裡**——讀碼時找到其中一個，就去同一張表找另一個。

**二、解構不是由擁有者呼叫，而是由最後一個引用者觸發。** kernel 物件常被很多人同時引用，沒有人「擁有」它。解構子因此被設計成 refcount 歸零時的 callback（Ch 20）。kobject 是這套做法的集大成者。

## kobject：kernel 的「Object 基底類別」

`Documentation/core-api/kobject.rst` 的「Embedding kobjects」一節直接說：熟悉物件導向的人可以把 kobject 看成一個頂層的抽象類別，其他類別從它衍生；C 沒有繼承語法，所以用 struct 嵌入。

```c
/* include/linux/kobject.h:64（v6.18） */
struct kobject {
	const char		*name;
	struct list_head	entry;
	struct kobject		*parent;
	struct kset		*kset;
	const struct kobj_type	*ktype;
	struct kernfs_node	*sd; /* sysfs directory entry */
	struct kref		kref;
	...
};

/* include/linux/kobject.h:116（v6.18） */
struct kobj_type {
	void (*release)(struct kobject *kobj);
	const struct sysfs_ops *sysfs_ops;
	const struct attribute_group **default_groups;
	...
};
```

kobject 一次提供四種能力，每一種都對應 OO 的一個概念：

| 欄位 | 能力 | OO 對應 |
|---|---|---|
| `kref` | 引用計數 | 物件生命週期（像 `shared_ptr`） |
| `ktype->release` | refcount 歸零時的清理 | 虛擬解構子 |
| `parent` / `kset` | 物件之間的階層與分組 | 組合樹（不是繼承樹） |
| `sd` + `ktype->sysfs_ops` | 在 `/sys` 下以目錄呈現，屬性以檔案呈現 | 反射／序列化 |

最常見的「衍生類別」是 `struct device`，它把 kobject 嵌在位移 0（本課 build 的 `pahole`：`struct kobject kobj; /* 0 64 */`，`struct device` 共 728 bytes）。轉換巨集：

```c
/* include/linux/device.h:721（v6.18） */
#define kobj_to_dev(__kobj)	container_of_const(__kobj, struct device, kobj)
```

真正有教學價值的是 device 的虛擬解構子。`device_ktype` 把 `release` 設成 `device_release`（`drivers/base/core.c:2591`），而 `device_release` 本身又是一次多型分派，而且是**依序找三層**：

```c
/* drivers/base/core.c:2542（v6.18，節錄） */
static void device_release(struct kobject *kobj)
{
	struct device *dev = kobj_to_dev(kobj);
	...
	devres_release_all(dev);
	...
	if (dev->release)
		dev->release(dev);
	else if (dev->type && dev->type->release)
		dev->type->release(dev);
	else if (dev->class && dev->class->dev_release)
		dev->class->dev_release(dev);
	else
		WARN(1, KERN_ERR "Device '%s' does not have a release() function, it is broken and must be fixed. See Documentation/core-api/kobject.rst.\n",
```

這是**手寫的方法解析順序**（method resolution order）：先找物件自己的 `release`，再找它的 `device_type`，再找它的 `class`，都沒有就 `WARN`。C++ 編譯器在編譯時替你做的 vtable 查找，kernel 在這裡用 `if/else if` 在執行時做。讀 driver 時看到 `dev->release = xxx_release` 就知道：這是在覆寫最優先的那一層。

kobject 的 `parent` 指標直接變成 sysfs 的目錄結構。在 WSL 上看一個真實的 platform device（Ch 43 會詳細讀它的 driver）：

```
$ ls /sys/bus/platform/devices/MSFT1002:00/
driver  driver_override  firmware_node  modalias  power  subsystem  uevent
$ readlink -f /sys/bus/platform/devices/MSFT1002:00
/sys/devices/platform/MSFT1002:00
```

`/sys/devices/platform/MSFT1002:00` 這條路徑就是 kobject 的 parent 鏈：`MSFT1002:00` 的 parent 是 `platform`，`platform` 的 parent 是 `devices`。`driver`、`subsystem`、`firmware_node` 是 symlink，代表物件之間的其他關係。**sysfs 就是 kernel 物件模型的即時快照**，讀 driver model 程式碼時開一個終端機對著 `/sys` 看，比讀十頁文件有用。

kobject.rst 的「ktypes and release methods」一節有一條硬規則：kobject 一旦 `kobject_add()` 之後，**絕對不能直接 `kfree()`**，只能 `kobject_put()`，讓 refcount 歸零時由 `release` 釋放。理由是 sysfs 裡可能有人正開著它的屬性檔，你不知道最後一個引用者是誰。

## 對比與取捨

| 面向 | C++ 原生 OO | kernel 的手工 OO |
|---|---|---|
| 型別安全 | 編譯器檢查轉型；有 `dynamic_cast` | `container_of` 完全信任呼叫者；轉錯就是記憶體錯誤 |
| 記憶體佈局 | 由 ABI 決定，可能重用 padding、插入 vptr | 完全由 struct 定義決定，`pahole` 看到的就是事實 |
| 方法覆寫粒度 | 整個類別 | 類別（ops 表）或單一物件（物件上的函式指標）皆可 |
| 可選方法 | 需要預設實作或純虛擬 | `NULL` 是合法值，通用層提供退路 |
| 建構／解構 | 語言保證成對呼叫 | 慣例保證（同一張 ops 表裡成對）＋ refcount 觸發 |
| 讀碼成本 | IDE 可以列出所有覆寫 | 要靠 `rg` 找所有 ops 表與賦值點（Ch 28、Ch 49） |

kernel 付出的代價是型別安全與工具支援，換到的是**佈局可控**、**沒有隱藏的程式碼生成**，以及「逐物件覆寫」這種 C++ 沒有的彈性。

## 踩雷集錦

1. **以為 `container_of` 會檢查型別**：它只做指標減法。kernel 的 `container_of` 有一個 `static_assert` 檢查「`ptr` 的型別和 `member` 的型別相容」，但這只能擋住「傳錯成員」，擋不住「這個 `struct sock` 其實是 UDP 的」。上面的 `bogus cast` 實驗就是這種錯誤。kernel 裡防範它靠的是**不變量**：`tcp_sk()` 只在 TCP 的程式碼路徑裡被呼叫，而那條路徑只有 `sk->sk_prot == &tcp_prot` 的 socket 才會進來。
2. **把「has to be the first member」當註解看看就好**：它是對 `sk_prot_alloc`、`__inet_sk_copy_descendant` 這類程式碼的承諾。調換欄位順序可以編譯通過，然後在執行時以難以追查的方式壞掉。
3. **只找類別的 ops 表，漏掉物件層級的覆寫**：追 `sk->sk_data_ready(sk)` 時，只看 `sock_init_data` 會以為一定是 `sock_def_readable`。用 `rg -n 'sk_data_ready\s*=[^=]'` 才會發現 v6.18 裡有 79 處賦值，分散在 33 個檔案。
4. **把 `struct socket` 和 `struct sock` 搞成同一個東西**：前者是 BSD socket 層、和 file/inode 綁定；後者是協定層。兩者是 has-a 關係，各有一張 vtable（`proto_ops` vs `proto`）。
5. **直接 `kfree` 一個嵌入了 kobject 的物件**：生命週期由 refcount 控制，正確做法是 `kobject_put()`／`put_device()`，由 `release` 釋放。沒有 `release` 的 device 在釋放時會觸發 `device_release` 裡那個 `WARN`。

## 進階：再往深一層

- **讀 `__inet_sk_copy_descendant`**（`include/net/inet_sock.h:357`）：它利用「祖先放在開頭」這個不變量，用 `sizeof(祖先)` 當起點複製衍生類別的部分。這是「佈局即設計」的極端例子，讀懂它就理解為什麼 kernel 開發者把欄位順序當 API。
- **`container_of` 搭配 `list_head` 的「一個物件、多個容器」**：一個 `struct inode` 同時在 `i_hash`、`i_lru`、`i_sb_list`、`i_io_list` 四個串列裡。從 C++ 的角度，這是四個「介面」的多重繼承；從資料結構角度，這是侵入式容器（Ch 23）。
- **kobject 的 kset 與 uevent**：kset 讓一群 kobject 共用 uevent 行為，`udev` 收到的熱插拔事件就是從這裡發出的。`samples/kobject/kobject-example.c` 和 `kset-example.c` 是 kobject.rst 推薦的「可以照抄的範例」，大約一兩百行，適合在 QEMU guest 裡載入實驗（本段未實測，需要能載入 module 的 guest）。
- **Rust for Linux 的對照**：v6.18 的 `rust/kernel/` 用 trait 包裝這些 ops 表，例如 `rust/kernel/miscdevice.rs:115` 的 `MiscDevice` trait 把 misc device 的 `file_operations` 包成 trait 方法。拿同一個概念在 C 與 Rust 兩邊的寫法對照，可以更清楚看出 C 版本省略了哪些保證。

## 動手練習

1. **補完 C 版的 `dynamic_cast`**：替 `oo.c` 加一個 `struct tcp_sock *tcp_sk_checked(struct sock *sk)`，在 `sk->prot != &tcp_prot` 時回傳 `NULL`。這正是 kernel 某些地方的寫法：例如網路程式碼裡常見先檢查 `sk->sk_protocol == IPPROTO_TCP` 再呼叫 `tcp_sk()`。用 `rg -n 'sk_is_tcp'` 找 kernel 的對應 helper。
2. **畫出 `struct ext4_inode_info` 的娃娃圖**：用 `pahole -C ext4_inode_info ~/code_reading/build/fs/ext4/super.o`，標出 `vfs_inode` 的位移，並找出 `EXT4_I()` 的所有呼叫者大概有多少個（`rg -c 'EXT4_I\(' fs/ext4 | awk -F: '{s+=$2} END{print s}'`）。
3. **追 `release` 的方法解析**：在 `drivers/base/platform.c` 找出 platform device 的 `release` 是在哪一層（`dev->release`、`type->release` 還是 `class->dev_release`）被設定的。
4. **故意違反「first member」**：在 `oo.c` 裡把 `struct inet_sock` 的 `sk` 移到 `sport` 之後，不改其他程式碼，重新編譯執行，用 ASan 看哪一行出錯，並解釋原因（提示：`sk_alloc` 把 `calloc` 的回傳值直接當 `struct sock *`）。

## 本章重點整理

- kernel 用 struct 嵌入做繼承、`container_of` 做向下轉型、ops table 做 vtable、工廠函式加 `obj_size`／`alloc_*` hook 做建構、refcount 加 `release` 做解構。
- `container_of` 不需要成員在第一個，但「has to be the first member」的註解代表有別的程式碼依賴位移為 0，必須遵守。
- kernel 的多型比 C++ 更鬆：vptr 位置不固定、可以逐物件覆寫單一方法、`NULL` 方法是合法狀態。
- kobject 是頂層基底類別，提供 refcount、虛擬解構子（`ktype->release`）、階層與 sysfs 呈現；`device_release` 示範了手寫的方法解析順序。
- 沒有執行期型別檢查：轉型的正確性靠程式路徑上的不變量維持。

## 自我檢核

- [ ] 不看筆記，把 `tcp_sk(sk)->snd_cwnd` 翻譯成 C++ 寫法，並說出 C 版本少了哪一個保證。
- [ ] 如果面試官問「C 怎麼做多型？kernel 的做法和 C++ vtable 有什麼不同？」，你能說出至少兩個 kernel 比 C++ 更有彈性的地方，以及一個更危險的地方嗎？
- [ ] 能解釋為什麼 `struct ext4_inode_info` 的 `vfs_inode` 不在第一個也沒問題，但 `struct inet_sock` 的 `sk` 必須在第一個？
- [ ] 看到 `dev->release = foo_release;`，你能說出它在 `device_release` 的三層解析裡排第幾、什麼時候會被呼叫嗎？
- [ ] 想一個你寫過或讀過的 C 專案（Redis、SQLite、Lua），它用什麼方式做多型？和 kernel 的 ops table 有什麼異同？

## 延伸閱讀

### 部落格 / 技術文章

- **[Object-oriented design patterns in the kernel, part 1](https://lwn.net/Articles/444910/)** 與 **[part 2](https://lwn.net/Articles/446317/)** — Neil Brown（LWN.net，2011）
  - **這篇說什麼**：part 1 談方法分派：ops table、vtable 放在物件裡還是放在「類別」裡、`NULL` 方法與預設行為；part 2 談資料繼承：struct 嵌入、`container_of`、`void *` 私有資料，以及各種做法的取捨。
  - **讀哪裡**：兩篇都值得整篇讀。part 1 對應本章「多型」一節，part 2 對應「繼承」與「封裝」。
  - **為什麼值得讀**：作者是長期的 kernel 檔案系統維護者，這兩篇是討論 kernel OO 慣用法最常被引用的文章。2011 年的 API 名稱有些已改，但模式完全沒變。

### 官方文件

- **[Everything you never wanted to know about kobjects, ksets, and ktypes](https://docs.kernel.org/core-api/kobject.html)**（tree 內：`Documentation/core-api/kobject.rst`）
  - **讀哪裡**：「Embedding kobjects」與「ktypes and release methods」兩節是本章 kobject 部分的出處；「Example code to copy from」指向 `samples/kobject/`。
  - **前提知識**：Ch 16（container_of）、Ch 20（kref）。
- **[Device Driver Design Patterns](https://docs.kernel.org/driver-api/driver-model/design-patterns.html)**（tree 內：`Documentation/driver-api/driver-model/design-patterns.rst`）
  - **讀哪裡**：第 1 節「State Container」與第 2 節「container_of()」，用 driver 的角度講同一套 OO 慣用法，篇幅短，可以當 Ch 43 的預習。

### 書籍

- **《Code Reading: The Open Source Perspective》** — Diomidis Spinellis（Addison-Wesley, 2003）
  - **讀哪幾章**：Ch 9.3「Element Packaging」裡談 objects 與 abstract data types 的部分，原書用 C 與 C++ 的例子對照封裝方式；本章是把它換成 kernel 的版本。
- **《Inside the C++ Object Model》** — Stanley B. Lippman（Addison-Wesley, 1996）
  - **這本書的定位**：解釋 C++ 編譯器怎麼把 class、繼承、虛擬函式翻譯成 struct 與函式指標，等於本章的反方向。
  - **讀哪幾章**：Ch 1「Object Lessons」與 Ch 4「The Semantics of Function」中虛擬函式的部分。書很舊，ABI 細節以 Itanium C++ ABI 為準，但「C++ 物件在記憶體裡長什麼樣」的心智模型仍然適用。

物件模型給了我們「類別」與「實例」，但 driver 的世界還多一個問題：一個 device 物件和一個 driver 物件是怎麼互相找到的？下一章讀 driver model，看 bus、device、driver 三者怎麼配對，以及 probe 在什麼時候被呼叫。

→ [Ch 43 driver model：bus、device、driver 與 probe](./43-driver-model.md)
