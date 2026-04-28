# 網路學習筆記：從 TCP/IP 原理到 VPN / VPS 自架

> 給會 Linux、想徹底搞懂網路黑魔法、最後能自己買台 VPS 架 VPN 跑服務的工程師。

這系列把網路這個既熟悉又陌生的領域一次教完：底層 TCP/IP 協定、各種診斷工具、Linux firewall 與網路 namespace、VPN 三大主流（WireGuard / OpenVPN / IPSec）、翻牆生態（Shadowsocks / V2Ray）、VPS 實務部署。讀完你能解釋 TCP 三次握手細節、能 debug `Connection refused` 為什麼、能在 VPS 上架 HTTPS 跟 VPN。

## 為什麼學這個？

- **網路問題天天有**：服務連不上、HTTPS 失效、`504` 超時 — 沒有網路 debug 能力的工程師永遠在猜
- **VPN / VPS 是現代必備**：翻牆、跨國連、自架服務、保護隱私 — 不會配只能花錢買套裝
- **理解就不再是黑魔法**：「TCP 握手」「TLS 憑證」「NAT 穿透」這些詞背後有具體機制，懂了一切變直觀
- **DevOps / SRE 入場券**：所有現代後端工作都假設你懂網路

## 一個必須先講清楚的事

**這課跟 `systems/observability_tools` 部分重疊**（tcpdump / ss / lsof）。但切入角度不同：

- observability_tools：從 syscall 看（user/kernel boundary）
- 本課：從 packet flow 看（網路協定）

兩個視角互補。**沒讀過 observability_tools 也能讀本課**，相關工具會重新從網路角度教。

## 課程地圖

### Part 1 — 環境與全貌
- [Ch 0 環境搭建](./00-environment-setup.md)
- [Ch 1 你按 enter 後發生什麼](./01-internet-journey.md)

### Part 2 — TCP/IP 核心
- [Ch 2 OSI 與 TCP/IP 模型](./02-osi-tcpip-models.md)
- [Ch 3 鏈結層：Ethernet / ARP](./03-link-layer-ethernet-arp.md)
- [Ch 4 網路層：IP / ICMP / 路由](./04-network-layer-ip-icmp.md)
- [Ch 5 IP 位址、subnet、CIDR](./05-ip-addressing-cidr.md)
- [Ch 6 TCP 完整解析](./06-tcp-deep-dive.md)
- [Ch 7 UDP 與 TCP 的選擇](./07-udp-vs-tcp.md)
- [Ch 8 NAT 完整解析](./08-nat-explained.md)

### Part 3 — 應用層協議
- [Ch 9 DNS](./09-dns.md)
- [Ch 10 HTTP/1.1 → HTTP/2 → HTTP/3](./10-http-evolution.md)
- [Ch 11 TLS / HTTPS](./11-tls-https.md)
- [Ch 12 SSH 與其他應用層速覽](./12-ssh-and-others.md)
- [練習 A：用 Wireshark 看完整 HTTPS 請求](./practice-a-https-wireshark.md)

### Part 4 — 工具完整指南
- [Ch 13 ip / ss / route](./13-ip-ss-route.md)
- [Ch 14 tcpdump / Wireshark](./14-tcpdump-wireshark.md)
- [Ch 15 dig / nslookup](./15-dig-nslookup.md)
- [Ch 16 traceroute / mtr / ping](./16-traceroute-mtr-ping.md)
- [Ch 17 nmap / netcat / curl 進階](./17-nmap-netcat-curl.md)
- [練習 B：debug 5 個常見網路問題](./practice-b-debug-5-problems.md)

### Part 5 — Firewall + Linux 網路機制
- [Ch 18 iptables 完整指南](./18-iptables-complete.md)
- [Ch 19 nftables](./19-nftables.md)
- [Ch 20 network namespaces](./20-network-namespaces.md)
- [Ch 21 tun/tap interface](./21-tun-tap.md)
- [Ch 22 bridge / veth pair](./22-bridge-veth.md)

### Part 6 — VPN 全解
- [Ch 23 VPN 全景](./23-vpn-overview.md)
- [Ch 24 WireGuard 原理 + 自架](./24-wireguard.md)
- [Ch 25 OpenVPN 原理 + 自架](./25-openvpn.md)
- [Ch 26 IPSec](./26-ipsec.md)
- [Ch 27 三家對比與選擇](./27-vpn-comparison.md)
- [練習 C：自架 WireGuard 雙端配置](./practice-c-wireguard-setup.md)

### Part 7 — Proxy 與翻牆
- [Ch 28 HTTP Proxy / SOCKS5](./28-http-proxy-socks5.md)
- [Ch 29 Shadowsocks](./29-shadowsocks.md)
- [Ch 30 V2Ray / Xray](./30-v2ray-xray.md)
- [Ch 31 GFW 對抗演進史](./31-gfw-evolution.md)

### Part 8 — VPS 實務
- [Ch 32 VPS vs VM vs 容器 vs dedicated](./32-vps-vs-vm-container.md)
- [Ch 33 買 VPS](./33-buying-vps.md)
- [Ch 34 SSH 完整指南](./34-ssh-complete.md)
- [Ch 35 VPS 安全配置](./35-vps-security.md)
- [Ch 36 部署服務 (nginx + HTTPS)](./36-nginx-deploy.md)
- [練習 D：買 VPS + 部署 nginx + HTTPS](./practice-d-deploy-https.md)

### Part 9 — 進階速覽
- [Ch 37 容器網路](./37-container-networking.md)
- [Ch 38 IPv6](./38-ipv6.md)
- [Ch 39 QUIC / HTTP/3 / BGP](./39-quic-http3-bgp.md)

### Final Project
- [Final Project：完整 VPS 部署](./final-project-complete-deployment.md)

## 學習方式建議

1. **每章配命令練習**：純讀網路書沒效，必須敲命令、看輸出、改參數重試
2. **兩個視角看同一件事**：tcpdump 看 packet + ss 看 socket，對照才看得透
3. **故意弄壞**：故意設錯 firewall、故意給壞 DNS、故意斷線 — 看每種錯的「徵狀」
4. **真的買 VPS**：免費教學跟付費玩 VPS 學到的東西差 10 倍。台幣 100-200 / 月，必要投資

## 參考資料

- 《TCP/IP Illustrated Vol 1》— Stevens, 經典中的經典
- 《Computer Networking: A Top-Down Approach》— Kurose & Ross, 大學教科書最好的一本
- 《High Performance Browser Networking》— Ilya Grigorik (免費線上版), HTTP/2/3 講得最清楚
- WireGuard 官方文件：https://www.wireguard.com/
- Cloudflare blog — 大量現代網路議題深度文章
- `man tcpdump` `man iptables` — 別跳過
