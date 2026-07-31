# 網路完整課程：從 TCP 三次握手到自架 VPN

> 給懂一點 C、想把網路從「會設定」變成「懂原理＋能實戰」的工程師。

這門課從「一個封包在線上實際發生什麼」出發，把網路的整個 stack 講透：TCP/IP 協定原理、Linux 的網路機制（namespace/iptables/tun-tap）、三大 VPN、Proxy 與翻牆生態的攻防演進、到買一台 VPS 自架服務。每個概念都動手驗證——用 tcpdump 看封包、用 netns 建虛擬網路、用 WireGuard 架 VPN。讀完你能自己買台 VPS、架 VPN、配防火牆、部署 HTTPS 服務、看 tcpdump 解 bug，並講得出 TCP 三次握手的每個細節。

## 為什麼學這個？

- **網路是所有分散式系統的底層**：後端、DevOps、SRE、資安——服務跨機器溝通就是網路。「設定能動」和「懂為什麼」差在出問題時你能不能解
- **理解底層 = debug 能力**：「連線為什麼 timeout」「為什麼 TLS 握手失敗」「封包為什麼被丟」——這些只有看得懂封包、懂 TCP 狀態機、懂 NAT/防火牆的人能解
- **VPN/翻牆的攻防是最好的網路教材**：GFW 和翻牆工具的二十年攻防，把 TCP/TLS/DPI/流量特徵分析的原理逼到極致——學它等於把網路原理走一遍實戰
- **職涯角度**：能自架 VPS、配 VPN、debug 網路問題，是 DevOps/SRE 的硬通貨，也是把你和「只會點雲端控制台」的人區分開的東西

## 先修知識

- **C 語言**（程度：會指標、struct、知道 socket 大概是什麼；不需要寫過網路程式）
- **Linux 命令列**（程度：會基本操作、知道 sudo、編輯檔案；建議先有 linux_commands 課的基礎）
- 不需要：網路證照知識、CCNA、任何網路工程背景（課程從封包層從零建立）

## 課程地圖

### Part 1 — 環境與全貌（Ch 0–1）
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 一個封包的旅程](./01-internet-journey.md)

### Part 2 — TCP/IP 核心（Ch 2–8）
- [Ch 2 OSI 與 TCP/IP 模型](./02-osi-tcpip-models.md)
- [Ch 3 連結層：Ethernet 與 ARP](./03-link-layer-ethernet-arp.md)
- [Ch 4 網路層：IP 與 ICMP](./04-network-layer-ip-icmp.md)
- [Ch 5 IP 定址與 CIDR](./05-ip-addressing-cidr.md)
- [Ch 6 TCP 深入：握手、狀態機、流量控制](./06-tcp-deep-dive.md)
- [Ch 7 UDP vs TCP](./07-udp-vs-tcp.md)
- [Ch 8 NAT 透徹理解](./08-nat-explained.md)

### Part 3 — 應用層協定（Ch 9–12）
- [Ch 9 DNS](./09-dns.md)
- [Ch 10 HTTP 演進（1.0 到 3）](./10-http-evolution.md)
- [Ch 11 TLS 與 HTTPS](./11-tls-https.md)
- [Ch 12 SSH 與其他協定](./12-ssh-and-others.md)
- [練習 A：用 Wireshark 解剖一次 HTTPS](./practice-a-https-wireshark.md)

### Part 4 — 網路工具（Ch 13–17）
- [Ch 13 ip / ss / route](./13-ip-ss-route.md)
- [Ch 14 tcpdump 與 Wireshark](./14-tcpdump-wireshark.md)
- [Ch 15 dig / nslookup](./15-dig-nslookup.md)
- [Ch 16 traceroute / mtr / ping](./16-traceroute-mtr-ping.md)
- [Ch 17 nmap / netcat / curl](./17-nmap-netcat-curl.md)
- [練習 B：debug 五個網路問題](./practice-b-debug-5-problems.md)

### Part 5 — 防火牆與 Linux 網路機制（Ch 18–22）
- [Ch 18 iptables 完整](./18-iptables-complete.md)
- [Ch 19 nftables](./19-nftables.md)
- [Ch 20 network namespace](./20-network-namespaces.md)
- [Ch 21 tun/tap 裝置](./21-tun-tap.md)
- [Ch 22 bridge 與 veth](./22-bridge-veth.md)

### Part 6 — VPN（Ch 23–27）
- [Ch 23 VPN 總覽與原理](./23-vpn-overview.md)
- [Ch 24 WireGuard](./24-wireguard.md)
- [Ch 25 OpenVPN](./25-openvpn.md)
- [Ch 26 IPSec](./26-ipsec.md)
- [Ch 27 三家 VPN 比較](./27-vpn-comparison.md)
- [練習 C：架一個 WireGuard VPN](./practice-c-wireguard-setup.md)

### Part 7 — Proxy 與翻牆生態（Ch 28–31）
- [Ch 28 HTTP proxy 與 SOCKS5](./28-http-proxy-socks5.md)
- [Ch 29 Shadowsocks](./29-shadowsocks.md)
- [Ch 30 V2Ray / Xray](./30-v2ray-xray.md)
- [Ch 31 GFW 演進史與對抗](./31-gfw-evolution.md)

### Part 8 — VPS 實務（Ch 32–36）
- [Ch 32 VPS vs VM vs 容器](./32-vps-vs-vm-container.md)
- [Ch 33 買 VPS 與初始設定](./33-buying-vps.md)
- [Ch 34 SSH 完整](./34-ssh-complete.md)
- [Ch 35 VPS 安全加固](./35-vps-security.md)
- [Ch 36 用 nginx 部署服務](./36-nginx-deploy.md)
- [練習 D：部署一個 HTTPS 網站](./practice-d-deploy-https.md)

### Part 9 — 進階速覽（Ch 37–39）
- [Ch 37 容器網路](./37-container-networking.md)
- [Ch 38 IPv6](./38-ipv6.md)
- [Ch 39 QUIC / HTTP3 / BGP](./39-quic-http3-bgp.md)

### Final Project
- [Final Project：完整部署一台生產 VPS](./final-project-complete-deployment.md)

## 學習方式建議

1. **每個協定都抓封包看**：`tcpdump` / Wireshark 看 TCP 握手、TLS 協商、DNS 查詢的真實封包。把「協定文字」變成「眼睛看到的 bytes」，是本課的核心手法
2. **用 netns 建實驗網路**：network namespace 讓你在一台機器上建出多台「虛擬主機」+ 路由 + NAT，安全地玩任何拓樸，弄壞了刪掉重來
3. **故意把它弄壞**：防火牆規則設錯看連線怎麼斷、MTU 設太大看封包怎麼分片失敗、DNS 指錯看解析怎麼壞——看現象比讀 RFC 有效
4. **真的買一台 VPS**：Part 8 開始建議花幾美元買台 VPS（每月 ~$5），把學的東西部署上去。紙上談兵和真的暴露在公網被掃描是兩回事

## 精選資料庫

### 必讀基礎

- **《TCP/IP Illustrated, Volume 1》** — W. Richard Stevens & Kevin Fall（Addison-Wesley, 2nd ed）
  - 本課協定部分的聖經；Part 2-3 的 TCP/IP/DNS/HTTP 底層權威。用實際封包講協定，和本課手法一致
- **[RFC editor](https://www.rfc-editor.org/)** — 協定的原始定義
  - 遇到行為爭議時的最終仲裁；本課反覆指向特定 RFC 的特定小節（如 RFC 9293 TCP、RFC 8446 TLS 1.3）

### 推薦部落格 / 文章

- **[Julia Evans (jvns.ca)](https://jvns.ca/)** — Julia Evans
  - 把網路工具（tcpdump、DNS、TLS、netns）講得最清楚易懂；她的網路 zine 是 Part 4 工具章的最佳補充
- **[Cloudflare Blog](https://blog.cloudflare.com/)** — Cloudflare 工程團隊
  - TLS、QUIC、DDoS、BGP 的第一線實戰文章；Part 3 和 Part 9 的進階延伸都指向這裡

### 推薦工具與書

- **[Wireshark](https://www.wireshark.org/)** + 官方文件
  - 封包分析的標準工具，本課大量使用；官方的 sample captures 是練習素材
- **《Computer Networking: A Top-Down Approach》** — Kurose & Ross
  - 大學網路課的標準教材，從應用層往下講，補充本課沒展開的理論

### 讀完本課之後

- **《BPF Performance Tools》** — Brendan Gregg（用 eBPF 觀測網路 stack，接 bpf 課）
- **[High Performance Browser Networking](https://hpbn.co/)** — Ilya Grigorik（免費線上，把 TCP/TLS/HTTP/QUIC 的效能推到極致）

---

> 本課所有指令以 Linux（Ubuntu 22.04+ / Debian 12+）為準。VPS 章節以主流雲商（Vultr/DigitalOcean/Linode）為例。協定行為標注對應的 RFC。
