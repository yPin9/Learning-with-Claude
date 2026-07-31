# Ch 19 — nftables

> **目標**：理解 iptables 的現代繼任者 nftables——它為什麼要取代 iptables（統一 IPv4/IPv6、更高效的規則比對、更一致的語法）、基本概念（table/chain/rule 的新模型）、和 iptables 的對照、以及實務上怎麼用。現代 distro（Debian 11+/RHEL 8+）預設後端已是 nftables，懂它是現在進行式。雖然概念和 iptables 相通，但語法和架構更乾淨。

> **環境**：Linux（nftables，現代 distro 內建）。對照 Ch 18 的 iptables。

## 為什麼有了 iptables 還要 nftables？

iptables（Ch 18）用了二十年，但它有結構性問題：IPv4 和 IPv6 要分開的工具（iptables/ip6tables）、每種協定一套（arptables/ebtables）、規則是「線性比對」（規則多時慢）、語法不一致。nftables 是 netfilter 專案的「重新設計」——用**一個工具、一套語法**統一所有這些，規則比對更高效（用類似 BPF 的虛擬機），語法更一致。

現代 distro（Debian 11+、RHEL 8+、Ubuntu 21+）的 iptables 命令**其實是 nftables 的相容層**（`iptables-nft`）——你打 iptables，底層是 nftables。所以理解 nftables 是「現在」而非「未來」。這章講它的新模型、和 iptables 的對照、實務用法。好消息是核心概念（封包經過 chain、規則 match-action）和 iptables 相通，主要是語法和組織更乾淨。

## 先建立直覺:iptables 的問題與 nftables 的解法

```
iptables 的結構性問題 → nftables 的解法：

  問題 1：IPv4/IPv6/ARP 各一套工具
    iptables（v4）、ip6tables（v6）、arptables、ebtables
    → 同樣的規則要寫好幾次
  解法：nftables 一個工具、一套語法管全部（用 family 區分）
        │
  問題 2：表和鏈是「內建固定」的
    iptables 有固定的 filter/nat/mangle 表和固定的鏈
  解法：nftables 的表和鏈「自己定義」（更靈活）
        │
  問題 3：規則線性比對（規則多時慢）
    iptables 一條條比對，1000 條規則就比 1000 次
  解法：nftables 支援「集合（set）和映射（map）」→ 用查找而非線性比對
        │
  問題 4：語法不一致、難以原子更新
  解法：nftables 語法統一、支援原子載入整個規則集
        │
  → nftables = iptables 的重新設計，更統一、高效、靈活
```

關鍵心智：nftables 解決 iptables 的結構性問題——一個工具統一 IPv4/IPv6/ARP（不用多套）、表和鏈自己定義（更靈活）、支援集合/映射查找（規則多時更快）、語法統一可原子更新。核心概念（chain、match-action）和 iptables 相通，主要是組織和語法更乾淨。

> nftables 和 iptables（Ch 18）共享 netfilter 框架——封包經過的 hook 點（對應 iptables 的 chain）是一樣的。如果對 netfilter 的封包流、表/鏈概念不熟，先讀 [Ch 18](./18-iptables-complete.md)。本章專注「nftables 怎麼不同、怎麼用」。

## nftables 的基本結構

```
nftables 的模型（table → chain → rule）：

  table（表）：容器，屬於某個 family
    family：ip（v4）、ip6（v6）、inet（v4+v6 統一！）、arp、bridge
        │
  chain（鏈）：在 table 裡，掛到某個 hook
    hook：prerouting/input/forward/output/postrouting（對應 iptables 的鏈）
    type：filter / nat / route
    priority：多個 chain 在同 hook 時的順序
        │
  rule（規則）：在 chain 裡，match + action
        │
  關鍵差異 vs iptables：
    - table/chain 都自己命名定義（不是固定的）
    - inet family 同時管 v4+v6（一套規則搞定兩者！）
    - 一條規則能寫多個條件和動作
```

```bash
# 看當前 nftables 規則集（整個一次看）
sudo nft list ruleset
# table inet filter {
#     chain input {
#         type filter hook input priority 0; policy drop;
#         iif "lo" accept
#         ct state established,related accept
#         tcp dport 22 accept
#         tcp dport { 80, 443 } accept    ← 注意：用集合 { } 一次多個！
#     }
# }

# 列出特定表
sudo nft list table inet filter
```

> **nftables 的 `inet` family 一套規則同時管 IPv4 和 IPv6——這是它最實用的改進**。iptables 要為 IPv4（iptables）和 IPv6（ip6tables）寫**兩套**規則（容易漏一套，造成 IPv6 沒防護的安全漏洞）。nftables 的 `inet` family **一套規則同時涵蓋 v4 和 v6**——你寫一次 `tcp dport 22 accept`，v4 和 v6 都生效。這消除了「IPv6 防火牆忘了設」的常見安全漏洞（Ch 38）。其他改進：**集合語法** `{ 80, 443 }`（一條規則匹配多個 port，不用寫多條）、**table/chain 自己命名**（不像 iptables 固定的 filter/nat）、**`nft list ruleset`** 一次看整個規則集（不用分表看，比 iptables 的分散查詢清楚）。chain 要指定 `hook`（對應 iptables 的鏈：input/forward/output/pre/postrouting）、`type`（filter/nat/route）、`priority`（同 hook 多 chain 的順序）和 `policy`（預設動作）。雖然概念和 iptables 相通，但這些組織上的改進讓 nftables 的規則集更清楚、更不易出錯。

## nftables 實務:寫一個防火牆

```bash
# === 用 nft 命令建防火牆（對照 Ch 18 的 iptables）===

# 建一個 table（inet = v4+v6 統一）
sudo nft add table inet myfilter

# 建一個 input chain（掛到 input hook，預設 drop）
sudo nft add chain inet myfilter input '{ type filter hook input priority 0; policy drop; }'

# 加規則
sudo nft add rule inet myfilter input iif lo accept                         # loopback
sudo nft add rule inet myfilter input ct state established,related accept   # 已建立連線
sudo nft add rule inet myfilter input tcp dport 22 accept                   # SSH
sudo nft add rule inet myfilter input tcp dport '{ 80, 443 }' accept        # HTTP/HTTPS（集合）

# === 用設定檔（推薦，可原子載入整個規則集）===
sudo tee /etc/nftables.conf > /dev/null <<'EOF'
#!/usr/sbin/nft -f
flush ruleset                     # 先清空（原子替換）

table inet filter {
    chain input {
        type filter hook input priority 0; policy drop;
        iif "lo" accept
        ct state established,related accept
        ct state invalid drop
        tcp dport 22 accept
        tcp dport { 80, 443 } accept
        ip protocol icmp accept    # 允許 ping（v4）
        icmpv6 type { echo-request, nd-neighbor-solicit } accept  # v6 必要的 ICMP
    }
    chain forward {
        type filter hook forward priority 0; policy drop;
    }
    chain output {
        type filter hook output priority 0; policy accept;
    }
}
EOF

# 載入整個設定檔（原子操作——全部成功或全部不變）
sudo nft -f /etc/nftables.conf
# 持久化（開機自動載入）
sudo systemctl enable nftables
```

> **nftables 用設定檔 + `nft -f` 原子載入整個規則集——比 iptables 一條條加更安全可靠**。iptables 是一條條 `-A` 加規則（中途出錯會留下半套規則，狀態不一致）。nftables 推薦用**設定檔**（`/etc/nftables.conf`）寫整個規則集，用 `nft -f` **原子載入**（`flush ruleset` 先清空，整份成功才生效，失敗則不變）——這避免了「改規則改到一半出錯，防火牆處於半設定狀態」的危險。設定檔也更易讀、易版本控制、易複製到多台機器。注意 IPv6 需要允許某些 **ICMPv6**（如 neighbor solicitation，是 IPv6 的 ARP 替代，Ch 38）——v6 比 v4 更依賴 ICMP，全擋會壞掉（這是 IPv6 防火牆的常見坑）。`systemctl enable nftables` 讓規則開機自動載入（解決 Ch 18 提的「iptables 重開機規則消失」問題——nftables 的持久化更乾淨）。實務上：用 inet family 一套管 v4/v6、用設定檔原子載入、記得允許必要的 ICMPv6——這是現代 Linux 防火牆的標準做法。

## iptables 對照 nftables

| 操作 | iptables | nftables |
|---|---|---|
| 看規則 | `iptables -L -n -v` | `nft list ruleset` |
| v4/v6 | 分開（iptables/ip6tables）| 統一（inet family）|
| 允許 port | `-A INPUT -p tcp --dport 22 -j ACCEPT` | `add rule ... tcp dport 22 accept` |
| 多個 port | 寫多條 | `tcp dport { 80, 443 } accept`（集合）|
| NAT | `-t nat -A POSTROUTING ... -j MASQUERADE` | `... masquerade`（在 nat 類型 chain）|
| 預設策略 | `-P INPUT DROP` | `policy drop`（chain 定義裡）|
| 持久化 | iptables-save + 套件 | `/etc/nftables.conf` + enable |

```bash
# 轉換現有 iptables 規則到 nftables（工具）
sudo iptables-save > rules.v4
sudo iptables-restore-translate -f rules.v4 > rules.nft   # 自動翻譯
# 看翻譯結果，調整後用 nft -f 載入

# 檢查你的系統用哪個後端
sudo iptables --version
# iptables v1.8.x (nf_tables)   ← (nf_tables) = 其實是 nftables 後端！
# iptables v1.8.x (legacy)      ← 舊的 legacy 後端
```

> **現代 distro 的 iptables 命令其實是 nftables 後端——`iptables --version` 顯示 `(nf_tables)` 就證明了**。從 Debian 11/RHEL 8/Ubuntu 21 開始，你打的 `iptables` 命令底層是 `iptables-nft`（nftables 的相容層）——`iptables --version` 顯示 `(nf_tables)` 而非 `(legacy)`。這意味著：你用 iptables 語法，但規則存在 nftables 的結構裡（`nft list ruleset` 看得到）。**所以 iptables 知識沒白學**——它仍然有用（Docker、無數教學、現存系統都用 iptables 語法），只是底層換了。**過渡建議**：理解兩者（iptables 語法到處有、nftables 是新標準），新專案直接用 nftables（更乾淨）、維護舊系統用 iptables（相容）。`iptables-restore-translate` 能自動把 iptables 規則翻譯成 nftables。不要混用兩種後端的命令（會混亂）——確認系統用哪個（`--version`）後一致地用。這個「iptables 是 nftables 的前端」的現狀，是 Linux 防火牆「漸進遷移」的典型——保持相容，底層演進。Ch 35（VPS 安全）會用這些做實際的防火牆加固。

## 故意弄壞:nftables 的常見陷阱

```bash
# 在 netns 測試（安全，Ch 0）
sudo ip netns add nfttest
sudo ip netns exec nfttest ip link set lo up

# 陷阱 1：priority 衝突（多個 chain 在同 hook）
# 如果同時有 iptables-nft 的規則和你的 nft 規則，可能交互影響
sudo ip netns exec nfttest nft list ruleset   # 確認沒有意外的規則

# 陷阱 2：policy drop 但忘了允許 loopback（同 Ch 18）
sudo ip netns exec nfttest nft add table inet t
sudo ip netns exec nfttest nft add chain inet t input '{ type filter hook input priority 0; policy drop; }'
sudo ip netns exec nfttest ping -c1 127.0.0.1   # 不通（loopback 被 drop）
sudo ip netns exec nfttest nft add rule inet t input iif lo accept
sudo ip netns exec nfttest ping -c1 127.0.0.1   # 通了

# 陷阱 3：IPv6 ICMP 全擋造成問題（v6 依賴 ICMP）
# 設 inet family 防火牆要記得允許必要的 icmpv6

sudo ip netns del nfttest

# 陷阱 4：混用 iptables（legacy）和 nft 規則 → 兩套規則交互，難 debug
# 解法：統一用一種（確認後端，別混）
```

> **nftables 和 iptables 共享 netfilter 的 hook，所以「混用兩者」會造成難以 debug 的交互——統一用一種**。如果你的系統同時有 iptables（legacy 後端）的規則和 nftables 的規則，它們都掛在同一個 netfilter hook 上（按 priority 排序執行），規則會**互相影響**——封包可能先過 iptables 規則再過 nftables 規則，造成「我明明設了 accept 怎麼還是被擋」的困惑（被另一套的 drop 擋了）。解法：**確認系統用哪個後端（`iptables --version`），統一用一種**，不要混。其他陷阱和 iptables 共通：`policy drop` 忘了允許 loopback（連本機都不通）、IPv6 把 ICMPv6 全擋（v6 比 v4 更依賴 ICMP，會壞 neighbor discovery）。在 netns 測試（弄壞了刪掉重來）是學防火牆的安全方式。nftables 雖然更乾淨，但「防火牆把自己鎖死」「規則交互」這些坑和 iptables 一樣要小心——改遠端防火牆永遠留後路（Ch 18）。

## 動手練習

1. 看你的系統：`iptables --version` 確認後端（nf_tables vs legacy）、`nft list ruleset` 看 nftables 規則

2. 寫防火牆：在 netns 用 nft 建一個白名單防火牆（inet family + 設定檔），對照 Ch 18 的 iptables 版

3. 用集合：寫 `tcp dport { 22, 80, 443 } accept`，體會比 iptables 寫多條簡潔

4. 對照轉換：用 `iptables-restore-translate` 把一段 iptables 規則翻譯成 nftables，比較語法

5. 跑「故意弄壞」：體驗 policy drop 擋 loopback、混用兩種後端的問題

## 本章重點整理

- nftables 是 iptables 的重新設計：統一 IPv4/IPv6/ARP（一個工具）、表/鏈自定義、集合/映射查找（規則多時更快）、原子更新
- 模型：table（屬於 family：ip/ip6/inet）→ chain（掛 hook，有 type/priority/policy）→ rule（match+action）
- inet family 一套規則管 v4+v6（消除「IPv6 忘了設防火牆」的漏洞）；集合 `{ 80, 443 }` 一條匹配多個
- 用 `/etc/nftables.conf` + `nft -f` 原子載入整個規則集（比 iptables 一條條加安全）；記得允許必要 ICMPv6
- 現代 distro 的 iptables 是 nftables 後端（`--version` 顯示 nf_tables）；iptables 知識仍有用，別混用兩種後端

## 自我檢核

- [ ] 能說出 nftables 解決了 iptables 的哪些問題
- [ ] 理解 nftables 的 table/chain/rule 模型，特別是 inet family 的價值
- [ ] 會用 nft 寫基本防火牆，用設定檔原子載入
- [ ] 知道現代 iptables 其實是 nftables 後端，怎麼確認
- [ ] 知道不要混用兩種後端，以及 IPv6 ICMP 的注意事項

## 延伸閱讀

### 官方文件

- **[nftables wiki](https://wiki.nftables.org/)** — netfilter 專案
  - **讀哪裡**：Quick reference、Configuring tables/chains/rules
  - **為什麼值得讀**：nftables 的權威文件，語法和概念的完整參考

### 文章

- **[nftables 完整教學](https://www.zenarmor.com/docs/network-security-tutorials/what-is-nftables)** / **[Red Hat nftables 指南](https://access.redhat.com/documentation/en-us/red_hat_enterprise_linux/9/html/configuring_firewalls_and_packet_filters/getting-started-with-nftables_firewall-packet-filters)**
  - **這篇說什麼**：nftables 的實務設定，從基礎到 NAT
  - **讀哪裡**：Getting started 那幾節
  - **為什麼值得讀**：本章實務的擴充，含 NAT/集合的進階用法

- **[Moving from iptables to nftables](https://developers.redhat.com/blog/2017/01/03/moving-from-iptables-to-nftables)** — Red Hat
  - **這篇說什麼**：為什麼遷移、怎麼遷移、對照表
  - **為什麼值得讀**：理解 iptables→nftables 的過渡

下一章進入虛擬網路的核心——network namespace，把 Ch 0 玩過的 netns 講透，這是容器隔離和本課所有網路實驗的基礎。

→ [Ch 20 network namespace](./20-network-namespaces.md)
