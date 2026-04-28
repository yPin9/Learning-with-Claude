# 練習 B — debug 5 個常見網路問題

> 目標：用 Ch 13-17 學的工具，獨立 debug 5 種真實 production 場景的網路問題。

## 5 個情境

每個場景模擬一個真實問題。你用工具找出 root cause、寫下完整診斷流程、修復或解釋為何修不了。

| # | 情境 | 主要工具 |
|---|---|---|
| 1 | DNS 失效（無法解析某 domain） | dig / nslookup |
| 2 | TCP port 連不上 | nc / nmap / ss |
| 3 | TLS 握手失敗 | curl / openssl |
| 4 | 連線可建但慢 | mtr / iperf / curl timing |
| 5 | 路由黑洞 / MTU 問題 | traceroute / ping -M do |

## 模擬環境

每個場景在自己機器或 VPS 上模擬：

### 場景 1：DNS 失效

```bash
# 在 /etc/resolv.conf 改成壞的
sudo cp /etc/resolv.conf /tmp/resolv.conf.bak
echo "nameserver 1.2.3.4" | sudo tee /etc/resolv.conf

# 試
dig example.com
# 應該 timeout

# 你的任務：找出問題、修
```

預期：`dig example.com` 會 SERVFAIL / timeout。`dig @1.1.1.1 example.com` OK。

修：改回 resolv.conf 或加可用的 nameserver。

### 場景 2：TCP port 連不上

在 VPS 上跑：

```bash
# 假設 VPS 預期跑 HTTP server 在 8080
sudo iptables -A INPUT -p tcp --dport 8080 -j DROP
```

從本機測：

```bash
nc -zv <VPS-IP> 8080
# Connection timed out
```

你的任務：

- 確認問題是 firewall / server / 網路？
- 怎麼判斷？

### 場景 3：TLS 握手失敗

模擬：找一個過期憑證的 site：

```bash
curl https://expired.badssl.com
# curl: (60) SSL certificate problem: certificate has expired
```

你的任務：

- 用 openssl 看憑證細節
- 確認過期日
- 解釋對 production 影響

### 場景 4：慢連線

```bash
# 設網路 throttle（需要 root）
sudo tc qdisc add dev eth0 root netem delay 200ms loss 5%

# 試
ping -c 5 8.8.8.8     # 看 RTT 增加
mtr -r -c 50 8.8.8.8  # 看丟包率
curl -o /dev/null -s -w "Total: %{time_total}\n" https://example.com
```

你的任務：

- 量化網路差有多差
- 用 mtr 看哪段 lossy
- 移除 tc 設定

```bash
# 清除
sudo tc qdisc del dev eth0 root
```

### 場景 5：MTU 問題

模擬：

```bash
# 改本機 MTU 變小
sudo ip link set dev eth0 mtu 1200

# 試大 packet
ping -M do -s 1500 -c 1 example.com   # 應該失敗
ping -M do -s 1100 -c 1 example.com   # 應該成功

# 復原
sudo ip link set dev eth0 mtu 1500
```

你的任務：

- 找出 path MTU
- 寫下症狀清單（什麼 traffic 受影響）

## 5 場景的工具優先順序

```
 場景 1 DNS：dig → dig +trace → dig @other_server → /etc/resolv.conf → systemd-resolved status
 場景 2 Port：nc -zv → nmap → ss → telnet → traceroute → tcpdump → iptables -L
 場景 3 TLS：curl -v → openssl s_client → 看 cert 過期 → 看 cipher
 場景 4 慢：ping → mtr → curl timing → iperf3 → ss -tnpi
 場景 5 MTU：ping -M do (binary search size) → traceroute path MTU
```

## 報告模板

對每個場景寫：

```markdown
# 場景 N：(名稱)

## 症狀
- ?
- ?

## 用工具檢查的順序
1. 用 X：結果 / 觀察
2. 用 Y：結果 / 觀察
...

## Root cause
- ?

## 修法
- ?

## 怎麼防再發生
- ?
```

5 個場景完成 = 5 份報告。

## 完整參考

**做完再看！**

<details>
<summary>場景 1 解答</summary>

```markdown
# 場景 1：DNS 失效

## 症狀
- 任何 domain 都 timeout / SERVFAIL
- IP 直接連 OK（如 ping 8.8.8.8）

## 工具檢查
1. `dig example.com` → timeout 或 ;; SERVFAIL
2. `dig @1.1.1.1 example.com` → 正常
   → 不是 root domain 問題，是「我的 DNS server 壞了」
3. `cat /etc/resolv.conf` → 顯示 nameserver 1.2.3.4
4. `resolvectl status` → 看實際用的 DNS server
5. ping 1.2.3.4 → 通但不是真 DNS server

## Root cause
/etc/resolv.conf 設了不能用的 nameserver

## 修
sudo cp /tmp/resolv.conf.bak /etc/resolv.conf
# 或
sudo sed -i 's/1.2.3.4/1.1.1.1/' /etc/resolv.conf

## 怎麼防
- 用 systemd-resolved 集中管理
- 多設幾個 nameserver（fallback）
```

</details>

<details>
<summary>場景 2 解答</summary>

```markdown
# 場景 2：TCP port 連不上

## 症狀
- nc -zv <IP> 8080 timeout
- 但 ping 通
- ssh 22 通

## 工具檢查
1. nc -zv <IP> 8080 → timeout
2. nc -zv <IP> 22 → OK
3. ping <IP> → OK
   → 不是路由 / 主機問題，是 port 8080 specific
4. SSH 進 VPS：ss -tnlp | grep 8080 → 沒 listen
   → 可能 server 沒起，也可能 firewall 擋
5. SSH 進 VPS 起一個 listener：nc -l 8080 → 從本機 nc -zv 還是 timeout
   → server 起了但 firewall 擋
6. sudo iptables -L INPUT -n -v | grep 8080 → 看到 DROP rule

## Root cause
iptables INPUT rule DROP 8080

## 修
sudo iptables -D INPUT -p tcp --dport 8080 -j DROP

## 怎麼防
- iptables 規則寫文件
- 用 ufw / nftables 比較好管
```

</details>

## 進階挑戰

**A. 自己出 5 個情境**：找朋友 / 同事互相設，互相 debug。

**B. 寫成 dashboard**：對自己的 VPS 寫 monitoring script，每分鐘 check DNS / TCP / latency，異常 alert。

**C. 解 production case**：去 Stack Overflow / Reddit r/networking 找實際問題，假裝你是工程師、寫 debug 流程。

## 自我檢核

- [ ] 5 個場景都做過
- [ ] 5 份報告都寫
- [ ] 知道每種情境的「**第一招用什麼**」
- [ ] 體會「**先 isolate 範圍，再深挖**」的 debug 思維

下個 Part 進 firewall + Linux 網路機制。

→ [Ch 18 iptables 完整指南](./18-iptables-complete.md)
