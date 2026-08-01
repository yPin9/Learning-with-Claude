# Ch 13 — 雲端網路：VPC / Security Group / 橫向移動

> **目標**：理解 AWS VPC 的網路模型，能用 CLI 掃出帳號內所有對外暴露的 security group，掌握攻擊者如何從一個 VPC 橫向移動到其他 VPC，以及 SSRF 如何配合內網服務讓雲端入侵深度翻倍。

---

## 為什麼需要

傳統滲透測試，「進了 DMZ」就算取得橋頭堡，接下來對內網掃橫向。雲端環境把這個概念包進 VPC（Virtual Private Cloud）裡，但更複雜：帳號內可以有幾十個 VPC，每個 VPC 有公網子網（public subnet）和私網子網（private subnet），子網之間的隔離靠 security group（SG）和 NACL 控制——這些規則全是 API 設定的，沒有實體防火牆盒，也沒有人在機房巡視。

防守方設定錯了，整個帳號的東西向（east-west）流量就等於透明。攻擊者只要找到一個暴露面（0.0.0.0/0 的 SG），就能把 VPC 內的管理 endpoint、K8s API server、資料庫全當目標。

---

## 先建直覺

把 VPC 想成一棟辦公大樓，區域（region）是城市，可用區（Availability Zone, AZ）是棟，子網（subnet）是樓層。

```
  Region: ap-northeast-1
  ┌────────────────────────────────────────────────────────┐
  │  VPC: 10.0.0.0/16                                      │
  │                                                        │
  │  AZ-a (ap-northeast-1a)    AZ-b (ap-northeast-1b)     │
  │  ┌──────────────────┐      ┌──────────────────┐       │
  │  │ public subnet    │      │ public subnet    │       │
  │  │ 10.0.0.0/24      │      │ 10.0.2.0/24      │       │
  │  │  [EC2 + EIP]  ───┼──────┼──►  Internet GW  │       │
  │  └──────────────────┘      └──────────────────┘       │
  │  ┌──────────────────┐      ┌──────────────────┐       │
  │  │ private subnet   │      │ private subnet   │       │
  │  │ 10.0.1.0/24      │      │ 10.0.3.0/24      │       │
  │  │  [RDS, Lambda]   │      │  [EKS node]      │       │
  │  └──────────────────┘      └──────────────────┘       │
  │                                  │                     │
  │  NAT GW (在 public subnet)◄───── │ (outbound only)    │
  └────────────────────────────────────────────────────────┘
```

**Internet Gateway（IGW）** 讓 public subnet 的資源雙向對外。**NAT Gateway** 讓 private subnet 的資源只能主動出去，外面不能主動進來。路由表（route table）決定哪個 subnet 走 IGW 還是 NAT GW——這個設定錯了，private subnet 就等於 public。

Security group 是掛在資源（EC2、RDS、Lambda ENI）上的「虛擬防火牆」，stateful，允許的 inbound 流量自動允許 response。NACL（Network ACL）是掛在 subnet 上的，stateless，要對 inbound/outbound 分別設規則。

---

## 底層機制

### VPC 子網路由：public vs private 的本質差異

子網本身是 public 還是 private，不是靠名字決定，靠路由表：

```
# 查 VPC 的所有路由表
aws ec2 describe-route-tables \
  --filters "Name=vpc-id,Values=vpc-0abc1234def56789" \
  --query 'RouteTables[*].{ID:RouteTableId,Routes:Routes}' \
  --output json
```

如果某個路由表有 `"DestinationCidrBlock": "0.0.0.0/0"` 且 gateway 是 `igw-xxxxx`，掛這個路由表的 subnet 就是 public subnet。如果 0.0.0.0/0 指向 `nat-xxxxx`，就是 private subnet（只能 outbound）。

```
# 預期輸出（public subnet 的路由表片段）
{
  "DestinationCidrBlock": "0.0.0.0/0",
  "GatewayId": "igw-0a1b2c3d4e5f67890"
}
```

### 暴露面分析：掃出所有對外開放的 Security Group

攻擊者視角：帳號內所有 SG 裡，哪些規則允許 0.0.0.0/0 或 ::/0 進入任意 port？

```bash
# 找出所有 inbound 規則允許 0.0.0.0/0 的 security group
aws ec2 describe-security-groups \
  --query "SecurityGroups[?contains(IpPermissions[].IpRanges[].CidrIp, '0.0.0.0/0')].{ID:GroupId,Name:GroupName,VPC:VpcId}" \
  --output table
```

```
# 預期輸出範例
-----------------------------------------
|       DescribeSecurityGroups          |
+-------------------+----------+--------+
|  ID               | Name     | VPC    |
+-------------------+----------+--------+
|  sg-0abc12345678  | web-sg   | vpc-xx |
|  sg-0def98765432  | admin-sg | vpc-xx |
+-------------------+----------+--------+
```

再進一步，針對高風險 port（22、3389、5432、6379、9200）：

```bash
# 掃出允許 0.0.0.0/0 進入 port 22 的 SG
aws ec2 describe-security-groups \
  --filters \
    "Name=ip-permission.from-port,Values=22" \
    "Name=ip-permission.to-port,Values=22" \
    "Name=ip-permission.cidr,Values=0.0.0.0/0" \
  --query "SecurityGroups[*].{ID:GroupId,Name:GroupName}" \
  --output table
```

配合 `describe-instances` 找哪些 EC2 真的掛了這些 SG，就能確認暴露面是否有真實資源：

```bash
aws ec2 describe-instances \
  --filters "Name=instance.group-id,Values=sg-0abc12345678" \
  --query "Reservations[*].Instances[*].{ID:InstanceId,IP:PublicIpAddress,State:State.Name}" \
  --output table
```

### Security Group vs NACL：攻擊者怎麼看這兩層

| 特性 | Security Group | NACL |
|------|---------------|------|
| 作用層次 | 資源層（ENI 上） | 子網層 |
| 有無狀態 | Stateful（回應自動放行） | Stateless（雙向要分別設） |
| 規則邏輯 | 只有 allow，無 deny | allow + deny，有順序 |
| 預設行為 | 拒絕所有 inbound | 允許所有（新建預設） |

攻擊者視角：SG 開了 0.0.0.0/0:22，NACL 能擋住嗎？

**能，但幾乎沒人設 NACL deny 規則。** AWS 預設 NACL 全部 allow，大多數組織沒有動 NACL。所以實際上 NACL 這層防禦在大多數帳號形同虛設，攻擊者直接看 SG 就好。

**本段未實測，為理論預期行為**：若 NACL 設了 deny port 22 的入站規則（優先順序數字比 allow 小），即使 SG 允許，TCP 連線也無法建立。自驗方法：建一個 EC2，SG 開 22，NACL 加 rule 100 deny 0.0.0.0/0 port 22，嘗試 SSH 連線確認是否 timeout。

### Security Group 常見誤設

**誤設一：SSH/RDP 全開**

```bash
# 問題設定：0.0.0.0/0 on port 22
aws ec2 authorize-security-group-ingress \
  --group-id sg-0abc12345678 \
  --protocol tcp --port 22 --cidr 0.0.0.0/0
# 這讓地球上任何 IP 都能嘗試 SSH brute force
```

修法：限制到跳板機 IP 或 VPN 出口 CIDR，或改用 AWS Systems Manager Session Manager 完全不開 22。

**誤設二：開 443 但實際服務 bind 到 debug port**

SG 只設 inbound 443，但應用程式同時在 8080 開了一個 debug/admin 端點，開發者以為 SG 沒開 8080 就安全了——但如果後來有人為了測試加了 `0.0.0.0/0:8080` 的規則，debug 端點就直接暴露。

**誤設三：信任整個 VPN CIDR**

```
# 看起來安全
Source: 10.200.0.0/16  (公司 VPN)
Port: 0-65535
```

VPN 子網 10.200.0.0/16 有 65536 個 IP。如果任何一台 VPN 用戶的機器被入侵，攻擊者從那台機器對所有 port 橫向攻擊完全不受 SG 限制。

**誤設四：Transitive trust 的 SG 引用**

```
sg-A (web tier) → allows sg-B (app tier)
sg-B (app tier) → allows sg-C (db tier)
```

這不代表 sg-A 可以直接到 sg-C，SG 引用不傳遞（non-transitive）。但如果 app tier 被打穿，攻擊者在 app tier 機器上就能直接到 db tier，因為 sg-B 被信任。水平移動路徑：web → app（sg-A allow sg-B）→ db（sg-B allow sg-C）。

### VPC Peering 與 PrivateLink 橫向移動

**VPC Peering（對等連線）**：兩個 VPC 之間建立路由，資源互相直接通訊，不走 Internet。

```
  VPC-prod (10.0.0.0/16) ←──peering──→ VPC-dev (10.1.0.0/16)
```

特性：
- **不傳遞**：prod peer dev，dev peer staging，不代表 prod 能到 staging
- **雙向**：兩邊的路由表都要加對方的 CIDR 才能通
- 攻擊者打穿 VPC-dev，能不能到 VPC-prod？看路由表和 SG 是否允許，通常開發環境設定較鬆

```bash
# 查帳號內所有 VPC peering 連線
aws ec2 describe-vpc-peering-connections \
  --query "VpcPeeringConnections[*].{ID:VpcPeeringConnectionId,Requester:RequesterVpcInfo.VpcId,Accepter:AccepterVpcInfo.VpcId,Status:Status.Code}" \
  --output table
```

**AWS PrivateLink（私有端點服務）**：服務提供方建立 endpoint service，消費方 VPC 建立 interface endpoint 連進來。單向、服務導向，流量不離開 AWS 骨幹網路。

```
  Consumer VPC ──interface endpoint──► Provider VPC (endpoint service)
  (只能消費，不能反向進 Consumer VPC)
```

攻擊者入侵 Consumer VPC，可能透過 PrivateLink 打到 Provider VPC 上的服務（如果那個服務有漏洞）。但 Provider VPC 無法透過 PrivateLink 反向打 Consumer VPC。

### SSRF 打內網服務

拿到 web 應用的 SSRF（Server-Side Request Forgery，伺服器端請求偽造）漏洞後，兩個最重要的目標：

**目標一：169.254.169.254（IMDS，Instance Metadata Service，實例元數據服務）**

```bash
# 從有 SSRF 的 EC2 上，攻擊者讓伺服器請求這個位址
curl http://169.254.169.254/latest/meta-data/iam/security-credentials/
# 如果 EC2 有 IAM role，這個 endpoint 會回傳 role 名稱
# 再打一層：
curl http://169.254.169.254/latest/meta-data/iam/security-credentials/MyEc2Role
```

```json
// 預期回傳（IMDS v1，無需 token）
{
  "Code": "Success",
  "LastUpdated": "2026-08-01T00:00:00Z",
  "Type": "AWS-HMAC",
  "AccessKeyId": "ASIA...",
  "SecretAccessKey": "...",
  "Token": "...",
  "Expiration": "2026-08-01T06:00:00Z"
}
```

拿到這組 credential 就能用 EC2 的 IAM role 權限操作 AWS API。IMDSv2 要求先 PUT 一個 token，對某些 SSRF 場景會阻擋——但不少應用還是跑在 IMDSv1 上。

**目標二：VPC 內部管理 endpoint**

```
# K8s API server 通常在 private subnet
curl http://10.0.1.100:6443/api/v1/namespaces/kube-system/secrets
# RDS
# 直接對 10.0.1.50:5432 打 PostgreSQL
# ElastiCache Redis
# 直接 RESP 協議到 10.0.1.60:6379
```

SSRF 結合 VPC 內網的威脅：web 應用在 public subnet，但 SSRF 讓攻擊者能對 private subnet 的服務直接送 HTTP（或其他協議）請求。

### VPC 內 DNS 行為

AWS VPC 預設在 VPC 的 base CIDR + 2（如 10.0.0.2）提供 DNS 解析器。Route 53 私有託管區（Private Hosted Zone）讓 VPC 內的域名解析到內部 IP。

DNS rebinding 風險：若應用程式信任某個 hostname 並做 SSRF 防禦（白名單 domain），攻擊者可以控制一個 DNS 記錄先解析到自己的 IP 通過驗證，再 rebind 到 169.254.169.254——取決於應用程式的 DNS TTL 快取行為。

**本段未實測，為理論預期行為**：IMDSv2 的 PUT token 步驟需要設定 `X-aws-ec2-metadata-token-ttl-seconds` header，一般 SSRF 工具較難自動帶這個 header，可以降低 rebinding 的實際成功率。自驗方法：在 EC2 啟用 IMDSv2 only，嘗試直接 GET IMDS 確認 401。

---

## 對比取捨表

| 場景 | Internet Gateway | NAT Gateway | VPC Peering | PrivateLink |
|------|-----------------|-------------|-------------|-------------|
| 流量方向 | 雙向 | 僅 outbound | 雙向（P2P） | 單向（consumer→service） |
| 是否傳遞 | N/A | N/A | 不傳遞 | 不傳遞 |
| 暴露面風險 | 最高（public IP） | 低（只出不進） | 中（peered VPC 被打穿） | 低（服務端無法反向） |
| 費用 | 低 | 高（per GB） | 低 | 中 |

---

## 踩雷集錦

**1. Private subnet 裡的 EC2 有 public IP**

subnet 是 private（路由表走 NAT GW），但 EC2 建立時勾了「Auto-assign public IP」。這台機器不能從外部直接連（沒有 IGW 路由），但如果路由表之後被改成指向 IGW，立刻就變成公開暴露。要在 subnet 層關掉「Auto-assign public IP」，別依賴路由表設定。

**2. Security group 開了「allow all from self」**

```
# 常見的預設設定：允許同 SG 的資源互相通訊
Source: sg-0abc12345678 (自身)
Port: all
```

表面看起來沒問題，但如果任何一台掛了這個 SG 的機器被打穿，攻擊者在那台機器上能對所有同 SG 資源的所有 port 橫向掃描。

**3. NAT Gateway 出口 IP 被加入白名單後當成「安全來源」**

某些第三方服務把 NAT GW 的 elastic IP 加入 IP 白名單，允許不做身份驗證的請求。任何在 VPC 內能出 NAT GW 的資源（包括被打穿的 Lambda）都能使用這個白名單 IP，等於身份驗證繞過。

**4. VPC Peering 路由表只加了一邊**

兩個 VPC 建立 peering 後，雙方要各自在路由表加對方的 CIDR。如果只加了一邊，另一邊的回應包找不到路由——連線無法建立，但有人以為「沒回應就是 peering 沒作用」而去把 SG 也開成 0.0.0.0/0 debug，結果 SG 洞開著但 peering 其實沒問題。

**5. 刪掉 EC2 但忘了刪 SG 和 ENI**

EC2 刪了，掛的 SG 還在，之後新建的 EC2 可能被指定到同一個 SG（誤以為是空的、乾淨的規則），繼承了一堆舊的 inbound 規則。定期清理沒有 attach 資源的 SG。

---

## 進階延伸

**AWS Network Firewall**：部署在 VPC 內，可以做 layer 7（應用層）過濾，包括 domain 白名單、IPS 規則。比 SG/NACL 更細緻，適合需要對 outbound 流量做 egress 管控的場景。

**VPC Flow Logs**：記錄 VPC 內 ENI 的所有網路流量摘要（5-tuple + accept/reject）。送到 CloudWatch Logs 或 S3，是橫向移動偵測的基礎資料來源。攻擊者橫向掃描時，Flow Log 會留下大量拒絕記錄（REJECT）。

**GuardDuty 的 VPC Flow Log 分析**：GuardDuty 會自動分析 Flow Log，偵測 port scanning、C2 通訊等異常模式，不需要自己寫 detection rule。

**Ingress-only Internet Gateway（IPv6 專用）**：對 IPv6 的 private subnet，使用 Egress-only IGW 達成類似 NAT GW 的效果（只 outbound）。注意 IPv6 不需要 NAT，每個資源都有 public IPv6，更需要 SG 細緻設定。

**Transit Gateway（TGW）**：取代 N 個 VPC peering 的全網格連線，讓多個 VPC 透過一個 hub 互通。攻擊者打穿一個連到 TGW 的 VPC，路由表決定能到哪些其他 VPC——TGW 的路由表設定是橫向移動的關鍵。

---

## 本章重點整理

- VPC 的 public/private subnet 區別靠路由表（IGW vs NAT GW），不靠名字
- Security Group 是 stateful、resource-level；NACL 是 stateless、subnet-level；實際上大多數帳號 NACL 等於沒設防
- 掃 0.0.0.0/0 的 SG 用 `describe-security-groups` 加 filter，配合 `describe-instances` 確認真實暴露面
- SG 常見誤設：22/3389 全開、debug port 被額外開放、VPN CIDR 信任過度、SG 互信造成橫向移動路徑
- VPC Peering 雙向不傳遞；PrivateLink 單向（consumer→service），被打穿的 VPC 的 peer 是橫向目標
- SSRF 首要目標：169.254.169.254（IMDS，拿 IAM role credential），次要目標：VPC 內的 K8s API server、RDS、Redis
- IMDSv2 增加 PUT token 步驟，但不是萬能的 SSRF 防禦；根本防禦是應用層不信任 user-controlled URL 去 fetch 內部資源

---

## 自我檢核

- [ ] 我能解釋為什麼 private subnet 的 EC2 沒有公網 IP 也能 outbound 到 internet
- [ ] 我能用一行 AWS CLI 列出帳號內所有允許 0.0.0.0/0 進入的 security group
- [ ] 我能說出 security group 和 NACL 在 stateful/stateless 上的差異，以及這對攻擊者意味著什麼
- [ ] 我能解釋 VPC peering 的「不傳遞」特性，以及如何影響橫向移動路徑
- [ ] 我能說出 SSRF 打 169.254.169.254 拿到什麼，以及 IMDSv2 如何改變攻擊難度
- [ ] 我知道 VPC Flow Logs 記錄什麼格式的資料，以及為什麼橫向移動掃描會在 Flow Log 留下痕跡

---

## 延伸閱讀

1. **AWS VPC 官方文件：路由表概念**
   - 搜尋：`site:docs.aws.amazon.com "route tables" "vpc"`
   - 為什麼讀：搞清楚路由表、subnet association 的精確語義，避免誤解 public/private 的判斷條件

2. **HackTricks Cloud — AWS VPC Pivoting**
   - URL：`https://cloud.hacktricks.wiki/pentesting-cloud/aws-security/aws-services/aws-vpc-and-networking`
   - 為什麼讀：攻擊者視角整理 VPC 橫向移動技術，含 peering/Transit Gateway 的實際利用手法

3. **Datadog Security Labs — Abusing VPC Peering**
   - 搜尋：`datadog security labs VPC peering lateral movement`
   - 為什麼讀：真實案例說明 peering 設定過於開放如何讓攻擊者從 dev 打到 prod

4. **AWS Blog — IMDSv2 強制啟用指南**
   - 搜尋：`aws blog "IMDSv2" "enforce" "ec2"`
   - 為什麼讀：了解 IMDSv2 如何阻擋 SSRF，以及怎麼用 AWS Config rule 偵測還在跑 IMDSv1 的 EC2

5. **Rhino Security Labs — AWS SSRF to RCE**
   - 搜尋：`rhino security labs aws ssrf metadata service exploitation`
   - 為什麼讀：完整攻擊鏈示範：SSRF 打 IMDS → 拿 IAM credential → 用 credential 橫向移動

---

我們搞清楚了 VPC 的網路邊界和暴露面之後，下一章要討論攻擊者在取得初始立足點後，如何在雲端環境建立持久化後門——包括後門 IAM 帳號、Lambda 觸發器、EC2 userdata 植入等技術。

下一章：[Ch 14 — 雲端持久化與後門](14-cloud-persistence.md)
