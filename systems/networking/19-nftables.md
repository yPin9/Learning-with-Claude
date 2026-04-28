# Ch 19 — nftables

> 目標：認識 nftables 是什麼、為什麼取代 iptables、語法差異與遷移建議。

## nftables 為什麼存在

iptables 老了：

- 4 個 table 各自獨立框架（filter / nat / mangle / raw），規則重複
- IPv4 / IPv6 各有 `iptables` / `ip6tables` / `arptables` / `ebtables`，分裂
- 沒原生 set / map（要用第三方 ipset）
- 規則更新慢（每次 atomic replace 全部）
- syntax 不一致

nftables 重新設計：

- **單一框架**統一 IPv4 / IPv6 / ARP / bridge
- 原生 set / map
- 變數 / include
- atomic 規則更新
- 語法統一、可讀

linux kernel 從 3.13 (2014) 開始有，多數現代 distro 預設 nftables。

## nftables vs iptables

```bash
# 看當前用什麼
sudo nft list ruleset           # nftables 規則
sudo iptables-legacy -L         # 老 iptables（可能空）
sudo iptables-nft -L            # iptables 命令但底層 nftables（相容層）
```

多數 distro 的 `iptables` 命令是 `iptables-nft`（相容包裝），底層其實是 nftables。

## 基本概念

nftables 結構：

```
 table → chain → rule
```

跟 iptables 4 表 5 鏈不同，nftables 的 table 你**自己命名**。chain 也自己命名 + 指定 hook。

```
 table inet my_filter {
     chain input {
         type filter hook input priority 0; policy drop;
         ct state established,related accept
         iifname "lo" accept
         tcp dport 22 accept
         tcp dport {80, 443} accept
     }
 }
```

## table 範例

```bash
# 建 table（IPv4 + IPv6）
sudo nft add table inet my_filter

# 建 chain（attach 到 input hook）
sudo nft add chain inet my_filter input { type filter hook input priority 0\; policy drop\; }

# 加規則
sudo nft add rule inet my_filter input ct state established,related accept
sudo nft add rule inet my_filter input iifname "lo" accept
sudo nft add rule inet my_filter input tcp dport 22 accept
sudo nft add rule inet my_filter input tcp dport { 80, 443 } accept
sudo nft add rule inet my_filter input ip saddr 1.2.3.4 drop
```

## 用設定檔

更實用：寫 config 檔，atomic load。

```
# /etc/nftables.conf
#!/usr/sbin/nft -f

flush ruleset

table inet filter {
    chain input {
        type filter hook input priority 0; policy drop;
        
        ct state established,related accept
        iifname "lo" accept
        
        # SSH
        tcp dport 22 accept
        
        # HTTP/HTTPS
        tcp dport {80, 443} accept
        
        # ICMP
        icmp type echo-request accept
        icmpv6 type echo-request accept
        
        # Log + DROP rest
        log prefix "nft-DROP: " level warn
    }
    
    chain forward {
        type filter hook forward priority 0; policy drop;
    }
    
    chain output {
        type filter hook output priority 0; policy accept;
    }
}

table ip nat {
    chain prerouting {
        type nat hook prerouting priority -100;
    }
    
    chain postrouting {
        type nat hook postrouting priority 100;
        # NAT for LAN
        oifname "eth0" masquerade
    }
}
```

載入：

```bash
sudo nft -f /etc/nftables.conf

# 或服務化
sudo systemctl enable nftables
sudo systemctl start nftables
```

## 命令對照

| iptables | nftables |
|---|---|
| `iptables -L` | `nft list ruleset` |
| `iptables -A INPUT -p tcp --dport 22 -j ACCEPT` | `nft add rule inet filter input tcp dport 22 accept` |
| `iptables -F INPUT` | `nft flush chain inet filter input` |
| `iptables -P INPUT DROP` | nftables 的 chain 定義裡寫 `policy drop` |
| `iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE` | `nft add rule ip nat postrouting oifname "eth0" masquerade` |

## 強大的「set」

iptables 要阻擋 100 個 IP 要寫 100 條規則。nftables 用 set：

```
table inet filter {
    set blacklist {
        type ipv4_addr
        elements = { 1.1.1.1, 2.2.2.2, 3.3.3.3 }
    }
    
    chain input {
        type filter hook input priority 0;
        ip saddr @blacklist drop
    }
}
```

加 IP 到 blacklist：

```bash
sudo nft add element inet filter blacklist { 4.4.4.4 }
```

刪：

```bash
sudo nft delete element inet filter blacklist { 4.4.4.4 }
```

set 用 hash table，**比 iptables 個別規則快 100x+**（特別是大量 entry）。

## 動態 set（時間限制）

「**這個 IP 黑 1 小時**」：

```
set tarpit {
    type ipv4_addr
    flags timeout
    timeout 1h
}

chain input {
    ip saddr @tarpit drop
    tcp dport 22 ct state new add @tarpit { ip saddr timeout 1m limit rate over 5/minute } drop
    tcp dport 22 accept
}
```

太多 SSH 嘗試 → 動態加進 tarpit → 黑 1 小時。**比 fail2ban 還簡潔**。

## 一個常見誤解：「裝了 nftables 還能用 iptables」

**部分對**。多數 distro 用 `iptables-nft` 命令 → 命令是 iptables 風格，**底層** kernel 用 nftables。能共存。

但**不要混用 `iptables-legacy`** 跟 `iptables-nft` 同時操作 → 規則互不見、行為怪。

```bash
# 看你的系統用哪個
ls -l /usr/sbin/iptables
# /usr/sbin/iptables -> /etc/alternatives/iptables
ls -l /etc/alternatives/iptables
# /etc/alternatives/iptables -> /usr/sbin/iptables-nft
```

## 一個常見誤解：「nftables 比 iptables 快」

**部分對**。對小規則集差不多。對大規則集（1000+ rules）nftables **快很多**（set / map / 樹狀資料結構）。

對 production 高速網路（10G+），nftables 才有實質優勢。

## 一個常見誤解：「我會 iptables，不需要學 nftables」

**短期對**。iptables 命令還能用（透過 iptables-nft 相容）。

但**新教學 / 文件越來越用 nftables 語法**。現代 distro（Fedora / Ubuntu 22+）預設 nftables。

「**會 iptables 也學 nftables 基本**」是 2025 年的合理姿態。

## 一個常見誤解：「nftables 跟 iptables 規則互通」

**錯**。雖然命令兼容（iptables-nft），但**直接寫 nftables 的規則** vs **iptables 命令翻譯成 nftables**，兩者格式不同。

最好「**整套都用 nftables**」或「**整套都用 iptables**」，不要混。

## 動手練習

**1. 看你的系統用什麼**

```bash
sudo nft list ruleset
sudo iptables-nft -L
sudo iptables-legacy -L
```

哪個有規則？哪個是空？

**2. 寫個小 nftables ruleset**

在 VPS 上（小心斷 SSH）：

```bash
# 先設 5 分鐘後自動 reset
echo "nft flush ruleset" | at now + 5 min

cat <<'EOF' | sudo nft -f -
flush ruleset

table inet test {
    chain input {
        type filter hook input priority 0; policy drop;
        ct state established,related accept
        iifname "lo" accept
        tcp dport 22 accept
        tcp dport {80, 443} accept
        icmp type echo-request accept
    }
}
EOF

sudo nft list ruleset
```

確認 SSH 還能連、HTTP 通。5 分鐘後 at job 自動清除。

**3. 用 set 阻擋多個 IP**

```bash
sudo nft add table inet test
sudo nft add set inet test blacklist { type ipv4_addr\; }
sudo nft add element inet test blacklist { 1.1.1.1, 2.2.2.2 }

sudo nft add chain inet test input { type filter hook input priority 0\; }
sudo nft add rule inet test input ip saddr @blacklist drop

sudo nft list ruleset
```

**4. 看 docker / podman 的 nftables**

```bash
sudo nft list ruleset | head -50
```

如果有 docker 跑，會看到大量 docker 自動加的 chain。

## 自我檢核

- [ ] 知道 nftables 為什麼取代 iptables
- [ ] 講得出 table → chain → rule 結構
- [ ] iptables 命令 vs nftables 命令的對照
- [ ] 知道 set / map 的價值
- [ ] 知道 iptables-nft 相容層的存在
- [ ] 寫過 nftables 規則並 load

下一章進 network namespaces — 容器網路的根基。

→ [Ch 20 network namespaces](./20-network-namespaces.md)
