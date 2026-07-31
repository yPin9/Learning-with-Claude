# Ch 21 — tun/tap 裝置

> **目標**：理解 tun/tap 虛擬網路裝置——它怎麼讓「用戶空間程式」收發網路封包（VPN 的底層機制）、tun（L3，IP 封包）和 tap（L2，乙太訊框）的差別、VPN 怎麼用 tun 把封包「抓進」程式加密再送出。這是所有 VPN（Ch 23-26）的共同底層——理解 tun/tap，你就懂了 VPN「封包怎麼被攔截、加密、重新送出」的核心。

> **環境**：Linux（/dev/net/tun）。概念為主，搭配觀察。

## 為什麼 tun/tap 是 VPN 的關鍵？

VPN 要做的事是：把你的網路封包「攔截下來」、加密、透過隧道送到 VPN 伺服器、伺服器解密後再送出。問題是——一個普通的用戶程式（VPN 客戶端）怎麼「攔截」你的網路封包？封包是 kernel 在處理的，用戶程式碰不到。

答案是 **tun/tap 裝置**——它是一個「虛擬網路介面」，但另一端連到一個**用戶空間程式**。封包送進這個虛擬介面，不是送到實體網卡，而是送給那個程式。VPN 客戶端就是讀這個介面拿到封包、加密、再從實體網卡送出。理解 tun/tap，你就懂了所有 VPN 的共同底層——它們都靠 tun（或 tap）把封包「引」進用戶空間處理。這是 Part 6（VPN）的關鍵前置。

## 先建立直覺:一個「假裝是網卡」的程式介面

```
tun/tap = 一端是「網路介面」，另一端是「用戶程式」

  普通網卡（eth0）：
    封包 → eth0 → 實體網路（電訊號出去）
        │
  tun 裝置（虛擬）：
    封包 → tun0 → 一個用戶空間程式（不是實體網路！）
                   程式讀到封包，想怎麼處理都行
        │
  VPN 怎麼用它：
    1. 設路由讓「要走 VPN 的流量」送到 tun0
    2. VPN 程式從 tun0 讀到這些封包（明文）
    3. 加密它們
    4. 透過「實體網卡」送到 VPN 伺服器（加密的隧道）
    5. 伺服器解密，送到真正目的地
        │
  → tun 讓用戶程式能「收發封包」，像個可程式化的網卡
    VPN = 用 tun 攔截封包 + 加密 + 從實體網卡送出
```

關鍵心智：tun/tap 是「一端是網路介面、另一端是用戶程式」的虛擬裝置。封包送進 tun0 不是去實體網路，而是給一個用戶空間程式——程式想怎麼處理都行。VPN 就是用它：設路由讓流量進 tun0、VPN 程式讀到封包、加密、從實體網卡送到 VPN 伺服器。tun 讓用戶程式能像「可程式化的網卡」一樣收發封包。

> tun/tap 是 Part 6（VPN）的底層。它建立在 netns（Ch 20）的虛擬網路概念上——都是「kernel 提供的虛擬網路設施」。WireGuard（Ch 24）用 tun、OpenVPN（Ch 25）也用 tun/tap。理解這章，Part 6 的 VPN 就有了共同的底層理解。

## tun vs tap:L3 vs L2

```
tun 和 tap 的差別（在哪一層工作）：

  tun（network TUNnel）：L3，處理「IP 封包」
    程式收到的是「IP 封包」（從 IP 標頭開始，Ch 4）
    沒有乙太網標頭（沒有 MAC，Ch 3）
    用途：VPN（路由 IP 流量就夠）—— WireGuard/OpenVPN 常用 tun
        │
  tap（network TAP）：L2，處理「乙太訊框」
    程式收到的是完整「乙太訊框」（含 MAC 標頭，Ch 3）
    像一張真的虛擬網卡（有 MAC）
    用途：需要 L2 的場景——橋接、虛擬機網路、L2 VPN
        │
  選擇：
    只需要路由 IP 流量（大多數 VPN）→ tun（簡單、開銷小）
    需要 L2 功能（橋接、廣播、非 IP 協定）→ tap
        │
  → tun 處理 IP 封包（L3），tap 處理乙太訊框（L2）
    VPN 大多用 tun（路由 IP 就夠，不需要 MAC 層）
```

```bash
# 建一個 tun 裝置（觀察用）
sudo ip tuntap add dev tun0 mode tun
sudo ip link set tun0 up
sudo ip addr add 10.8.0.1/24 dev tun0
ip link show tun0
# tun0: <POINTOPOINT,...> ... 　← POINTOPOINT（點對點，tun 的特徵）

# 建一個 tap 裝置（對比）
sudo ip tuntap add dev tap0 mode tap
ip link show tap0
# tap0: <BROADCAST,MULTICAST,...> ... link/ether xx:xx...　← 有 MAC（像真網卡）

# 清理
sudo ip link del tun0
sudo ip link del tap0
```

> **tun（L3，IP 封包）vs tap（L2，乙太訊框）——VPN 大多用 tun，因為「路由 IP 流量」不需要 MAC 層**。**tun** 工作在 L3（網路層，Ch 4）——程式收到的是「IP 封包」（從 IP 標頭開始，沒有乙太網標頭/MAC）。**tap** 工作在 L2（連結層，Ch 3）——程式收到完整「乙太訊框」（含 MAC），像一張真的虛擬網卡。**選擇取決於需求**：大多數 VPN 只需要「路由 IP 流量」（把你的 IP 封包送到 VPN 伺服器），用 tun 就夠（簡單、開銷小，少了 MAC 層的處理）——WireGuard（Ch 24）只支援 tun，OpenVPN（Ch 25）預設用 tun。需要 **L2 功能**（橋接兩個網段、傳遞廣播、非 IP 協定如某些遊戲/區網協定）才用 tap——如「我要讓遠端機器像在同一個區網」（L2 VPN）。觀察：tun 裝置是 `POINTOPOINT`（點對點，沒有 MAC），tap 是 `BROADCAST,MULTICAST` 且有 MAC（像真網卡）。理解這個差別，你看 VPN 設定時就知道「為什麼是 tun」（路由模式）還是「tap」（橋接模式），以及它們的取捨。

## VPN 怎麼用 tun:完整流程

```
VPN（如 WireGuard）用 tun 的完整封包流：

  你的程式（瀏覽器）想連 example.com
        │
  1. 封包產生：目標 example.com，來源你的 VPN IP（10.8.0.2）
        │
  2. 路由決策（Ch 4）：路由表設定「這些流量走 tun0」
     ip route：example.com 的流量 → 送到 tun0
        │
  3. 封包進 tun0 → VPN 程式讀到它（明文 IP 封包）
        │
  4. VPN 程式：
     - 加密這個封包（Ch 11 的加密原理）
     - 把加密後的資料包成一個「新的 UDP 封包」
       （來源=你的真實 IP，目標=VPN 伺服器）
        │
  5. 這個新 UDP 封包從「實體網卡」送出 → VPN 伺服器
        │
  6. VPN 伺服器：解密 → 拿到原始封包 → 用自己的網路送到 example.com
        │
  7. 回應沿原路加密回來 → 你的 VPN 程式解密 → 送進 tun0 → 你的程式收到
        │
  → 封包「被包進另一個封包」（封裝，Ch 2 的套娃再一層）
    你的真實流量在「VPN 隧道」裡，外面只看到「你和 VPN 伺服器的加密 UDP」
```

```bash
# 觀察 VPN 的雙層封包（Ch 24 架好 WireGuard 後）
# 在 tun 介面抓 → 看到「明文的內部封包」（你真正要連的）
# sudo tcpdump -i wg0 -n           # tun 介面：明文（VPN 內部）
# 在實體介面抓 → 看到「加密的外層封包」（你和 VPN 伺服器）
# sudo tcpdump -i eth0 -n udp port 51820   # 實體介面：加密的 UDP（隧道）
#   → 同一個流量，在兩個介面看起來完全不同（內層明文 vs 外層加密）
```

> **VPN 用 tun 的本質是「封包被包進另一個封包」——這是 Ch 2 封裝（套娃）再加一層**。完整流程：你的封包經路由送進 tun0 → VPN 程式讀到（明文）→ **加密並包成一個新的 UDP 封包**（來源=你的真實 IP、目標=VPN 伺服器）→ 從實體網卡送出 → VPN 伺服器解密 → 用自己的網路送到真正目的地。回應沿原路加密回來。關鍵洞察：你的真實流量（內層封包）被**包進**「你和 VPN 伺服器之間的加密 UDP 封包」（外層）——這是 Ch 2 的封裝再加一層（VPN 隧道層）。所以**在 tun 介面抓封包看到明文**（VPN 內部，你真正要連的內容），**在實體介面抓看到加密的 UDP**（外人只看到「你和 VPN 伺服器在傳加密資料」，看不到你實際訪問什麼）。這就是 VPN 的隱私保護——你的 ISP/中間人只看到「你連到 VPN 伺服器」，看不到隧道內的真實流量。也是 VPN 能「翻牆」的原理（Ch 31）——審查者看到的是加密 UDP，不知道裡面是被封鎖的網站。這個「雙層封包」的理解是 Part 6 所有 VPN 的核心，Ch 24（WireGuard）會實際抓給你看。

## /dev/net/tun:用戶空間的介面

```c
// tun 在 C 層怎麼運作（理解用，VPN 程式都這樣做）
// 1. 開啟 /dev/net/tun
int fd = open("/dev/net/tun", O_RDWR);

// 2. 設定它成 tun 模式、命名
struct ifreq ifr;
ifr.ifr_flags = IFF_TUN;        // tun 模式（IFF_TAP 是 tap）
strcpy(ifr.ifr_name, "tun0");
ioctl(fd, TUNSETIFF, &ifr);     // 建立 tun0 介面

// 3. 現在這個 fd 就是 tun0 的「另一端」
//    read(fd, ...)  → 讀到送進 tun0 的封包（IP 封包）
//    write(fd, ...) → 寫一個封包進 tun0（注入到網路 stack）

// VPN 程式的核心迴圈：
//    while (1) {
//        read(fd, packet);          // 從 tun0 讀封包（明文）
//        encrypt(packet);            // 加密
//        send_to_vpn_server(packet); // 從實體網卡送到 VPN 伺服器
//    }
```

> **VPN 程式的核心就是「read tun fd 拿封包、加密、送出」這個迴圈——tun 把網路封包變成了一個可讀寫的檔案描述符**。在 C 層，tun 的運作是：開啟 `/dev/net/tun`、用 ioctl 設成 tun 模式並命名，得到一個 **fd**——這個 fd 就是 tun 介面的「用戶空間端」。`read(fd)` 讀到「送進 tun0 的封包」（IP 封包，明文），`write(fd)` 把封包「注入網路 stack」（像是從 tun0 收到的）。這呼應 Unix 的「一切皆檔案」（如果你學過 linux_commands 課的 fd 概念）——網路封包透過 tun 變成了可讀寫的位元組流。**VPN 程式的核心迴圈**就是：`read(tun_fd)` 拿封包 → 加密 → 從實體 socket 送到 VPN 伺服器（反向：從伺服器收加密資料 → 解密 → `write(tun_fd)` 注入回網路 stack）。WireGuard、OpenVPN 本質上都在做這件事（WireGuard 更進一步把加密放進 kernel 模組以提速，Ch 24）。理解這個迴圈，你就懂了「VPN 程式到底在做什麼」——它是一個「封包加解密的中轉站」，tun 是它和網路 stack 之間的橋樑。這也解釋了為什麼 VPN 需要特殊權限（開 tun、設路由都要 root/CAP_NET_ADMIN）。

## 故意弄壞:觀察 tun 的封包流向

```bash
# 建一個 tun 並觀察封包怎麼進去（不接 VPN 程式，封包會「卡住」）
sudo ip tuntap add dev tun0 mode tun
sudo ip addr add 10.9.0.1/24 dev tun0
sudo ip link set tun0 up

# 送一個封包到 tun0 的網段（但沒有程式讀 tun0 的另一端）
ping -c1 -W1 10.9.0.2
# 不通！封包送進 tun0，但「另一端沒有程式讀它」→ 封包消失
#   → 證明 tun 需要「用戶程式在另一端」才有意義
#   （VPN 程式就是那個讀 tun 的程式）

# 看路由怎麼把流量導向 tun
ip route get 10.9.0.2
# 10.9.0.2 dev tun0 ...      ← 這個網段的流量會走 tun0

sudo ip link del tun0

# VPN 的「全流量路由」陷阱（Ch 24 會遇到）：
# VPN 設 AllowedIPs = 0.0.0.0/0（所有流量走 VPN）
# → 路由表加 default via tun
# → 但要小心：到 VPN 伺服器本身的流量「不能」也走 VPN（會迴圈！）
# → 所以要有例外路由（VPN 伺服器的 IP 走原本的網卡）
```

> **tun 裝置「另一端沒有程式讀」時封包會消失——這證明了 tun 只是「橋樑」，真正幹活的是讀它的 VPN 程式**。如果你建一個 tun 但不接任何程式（沒有 VPN 客戶端讀它），送進去的封包就「卡住消失」（沒人處理）——`ping` tun 網段不通。這驗證了 tun 的本質：它只是「網路 stack 和用戶程式之間的管道」，真正處理封包的是**讀 tun 的程式**（VPN 客戶端）。另一個 Ch 24 會遇到的關鍵陷阱：**VPN 的「全流量路由」迴圈**——當 VPN 設定「所有流量走 VPN」（`AllowedIPs = 0.0.0.0/0`），路由表加 `default via tun`，但這會造成問題：**連到 VPN 伺服器本身的封包也想走 VPN**（因為 0.0.0.0/0 包含 VPN 伺服器的 IP）→ 但走 VPN 又要先連到 VPN 伺服器 → 無限迴圈！解法是加一條**例外路由**：「到 VPN 伺服器 IP 的流量走原本的實體網卡」（不走 VPN）。WireGuard 等會自動處理這個，但理解它你才能 debug「VPN 連上但完全沒網路」的問題（可能是路由迴圈）。這些是 Part 6 架 VPN 時的實際問題，tun/tap 的理解是基礎。

## 動手練習

1. 建 tun/tap：用 `ip tuntap add` 建 tun 和 tap，對比它們的 link 屬性（POINTOPOINT vs 有 MAC）

2. 看路由導向：建 tun + 設網段，用 `ip route get` 看流量怎麼導向 tun

3. 觀察封包消失：建 tun 但不接程式，ping 它的網段，理解「另一端要有程式」

4. 理解雙層（Ch 24 後）：架 WireGuard 後，在 wg0（tun）和 eth0 各抓封包，看明文 vs 加密

5. 思考迴圈：理解「全流量走 VPN」為什麼需要 VPN 伺服器的例外路由

## 本章重點整理

- tun/tap 是「一端網路介面、一端用戶程式」的虛擬裝置——讓用戶程式能收發封包（VPN 的底層）
- tun（L3，IP 封包，無 MAC）vs tap（L2，乙太訊框，有 MAC）；VPN 大多用 tun（路由 IP 就夠）
- VPN 用 tun：路由把流量導進 tun → VPN 程式讀到（明文）→ 加密包成 UDP → 從實體網卡送到 VPN 伺服器
- 雙層封包（封裝再一層）：tun 介面看明文（內層）、實體介面看加密 UDP（外層）——這是 VPN 隱私/翻牆的原理
- tun 在 C 層是個 fd（read 拿封包、write 注入）；VPN 程式核心 = read tun→加密→送出的迴圈
- 陷阱：tun 另一端沒程式讀則封包消失；全流量走 VPN 要 VPN 伺服器的例外路由（防迴圈）

## 自我檢核

- [ ] 能解釋 tun/tap 是什麼，為什麼是 VPN 的底層
- [ ] 知道 tun（L3）和 tap（L2）的差別，VPN 為什麼大多用 tun
- [ ] 能描述 VPN 用 tun 的完整封包流（雙層封包/封裝）
- [ ] 理解為什麼 tun 介面看明文、實體介面看加密
- [ ] 知道「全流量走 VPN」的路由迴圈問題

## 延伸閱讀

### 官方文件

- **[Universal TUN/TAP device driver](https://www.kernel.org/doc/Documentation/networking/tuntap.txt)** — Linux kernel docs
  - **讀哪裡**：整篇 + C 範例
  - **為什麼值得讀**：tun/tap 的權威定義和 C 層用法（本章 C 範例的來源）

### 文章

- **[Tun/Tap interface tutorial](https://backreference.org/2010/03/26/tuntap-interface-tutorial/)** — backreference
  - **這篇說什麼**：tun/tap 的完整教學，含寫一個簡單的 tun 程式
  - **讀哪裡**：整篇
  - **為什麼值得讀**：把 tun 從概念到 C 程式講透，理解 VPN 底層

- **[Write your own VPN](https://github.com/gsliepen/tinc) / [Building a simple VPN](https://www.cisco.com/c/en/us/support/docs/security-vpn/...)**
  - **這篇說什麼**：用 tun 寫一個最簡 VPN
  - **為什麼值得讀**：動手體會 VPN = tun + 加密 + 隧道

### 書籍

- **《UNIX Network Programming》— Stevens（tun 相關章節）**
  - **這本書的定位**：網路程式設計的權威，理解 tun 的 socket/fd 操作

下一章是 Part 5 的最後一塊——bridge 和 veth，把 netns 用虛擬網線和虛擬交換器連成完整拓樸，這是 Docker 網路的完整底層。

→ [Ch 22 bridge 與 veth](./22-bridge-veth.md)
