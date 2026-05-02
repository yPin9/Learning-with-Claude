# Ch 28 — 網路命令

> 目標：掌握 `ip`/`ss`/`curl`/`wget`/`ssh`/`scp`/`rsync` 這幾個維運必備工具。

## ip：網路介面和路由

`ip` 是 `ifconfig`/`route` 的現代替代品。`ifconfig` 在新版 Linux 已預設不安裝。

```bash
ip addr            # 列出所有介面和 IP（簡寫 ip a）
ip link            # 列出介面（不含 IP）
ip route           # 顯示路由表（簡寫 ip r）
ip neigh           # ARP cache（鄰居）

# 操作（需要 root）
ip addr add 192.168.1.100/24 dev eth0    # 加 IP
ip addr del 192.168.1.100/24 dev eth0    # 刪 IP
ip link set eth0 up                       # 啟用介面
ip link set eth0 down                     # 停用介面
ip route add default via 192.168.1.1     # 加預設路由
```

## ss：Socket 狀態

`ss` 是 `netstat` 的現代替代品，速度更快。

```bash
ss -tlnp           # TCP 監聽中的 port，顯示 PID
                   # -t=TCP -l=listening -n=不解析名稱 -p=顯示行程

ss -tulnp          # 加上 UDP（-u）
ss -an             # 所有連線（包含 ESTABLISHED、TIME_WAIT 等）
ss -s              # 統計摘要
ss -tnp state established   # 已建立的 TCP 連線

# 找誰在用某個 port
ss -tlnp | grep :80
ss -tlnp | grep :22
```

輸出範例：

```
Netid  State   Local Address:Port   Peer Address:Port  Process
tcp    LISTEN  0.0.0.0:22           0.0.0.0:*           users:(("sshd",pid=1234))
tcp    LISTEN  127.0.0.1:5432       0.0.0.0:*           users:(("postgres",pid=5678))
```

## curl：HTTP 工具

```bash
curl https://example.com              # GET，輸出到 stdout
curl -o output.html https://example.com   # 儲存到檔案
curl -s https://example.com           # -s = silent（不顯示進度）
curl -I https://example.com           # 只顯示 HTTP headers

# POST 請求
curl -X POST https://api.example.com/data \
     -H "Content-Type: application/json" \
     -d '{"key":"value"}'

# Bearer token
curl -H "Authorization: Bearer $TOKEN" https://api.example.com/me

# 基本認證
curl -u user:password https://api.example.com

# 跟隨重新導向
curl -L https://example.com

# 顯示詳細資訊
curl -v https://example.com     # 含 TLS、headers
curl -w "%{http_code}\n" -o /dev/null -s https://example.com   # 只顯示 HTTP status code

# 測試連線速度
curl -o /dev/null -s -w "time: %{time_total}s\n" https://example.com
```

## wget：下載工具

```bash
wget https://example.com/file.tar.gz    # 下載檔案
wget -q https://example.com/file.tar.gz # -q = quiet
wget -c https://example.com/large.tar.gz   # -c = 斷點續傳
wget -O /tmp/result.html https://example.com  # 指定輸出檔名
wget -r -np -l2 https://example.com    # 遞迴下載，不往上，深度 2

# 靜默下載並確認（腳本常用）
wget -q --show-progress https://example.com/file
```

## ssh：遠端連線

```bash
ssh user@host                    # 基本連線
ssh -p 2222 user@host            # 指定 port
ssh -i ~/.ssh/mykey user@host    # 指定私鑰
ssh -J jump@bastion user@host    # Jump host（ProxyJump）

# 遠端執行命令
ssh user@host "ls /tmp"
ssh user@host "df -h" | grep -v tmpfs

# Port forwarding
ssh -L 8080:localhost:80 user@host   # 本機 8080 → 遠端 80
ssh -R 9090:localhost:3000 user@host # 遠端 9090 → 本機 3000
ssh -N -f -L 5432:db.internal:5432 user@bastion  # 背景 tunnel
```

### SSH Config（強烈建議）

`~/.ssh/config` 讓你省掉每次輸入長指令：

```
Host prod
    HostName prod.example.com
    User admin
    Port 2222
    IdentityFile ~/.ssh/prod_key
    ForwardAgent yes

Host bastion
    HostName bastion.example.com
    User ec2-user
    IdentityFile ~/.ssh/aws.pem

Host internal
    HostName 10.0.0.50
    User ubuntu
    ProxyJump bastion
```

設定好之後：`ssh prod` 就夠了。

## scp：遠端複製

```bash
scp file.txt user@host:/tmp/           # 上傳
scp user@host:/tmp/file.txt ./         # 下載
scp -r ./dir user@host:/backup/        # 遞迴複製目錄
scp -P 2222 file.txt user@host:/tmp/   # 指定 port（注意是大寫 -P）
```

`rsync` 比 `scp` 更適合大量傳輸，因為它做增量同步。

## rsync：同步工具

```bash
# 基本同步（-a = archive，保留權限/時間等）
rsync -av /src/ user@host:/dst/

# -a：archive（= -rlptgoD）
# -v：verbose
# -z：傳輸時壓縮
# --delete：刪除目的地有、來源沒有的檔案
# --dry-run：模擬，不真的執行

rsync -avz --delete /src/ user@host:/dst/
rsync -av --dry-run /src/ /dst/         # 先看看會做什麼

# 斷點續傳大檔案
rsync -avP large-file.tar.gz user@host:/tmp/   # -P = --progress --partial

# 只同步特定類型
rsync -av --include="*.log" --exclude="*" /var/log/ /backup/logs/
```

`rsync` 的 trailing slash 很重要：

```bash
rsync -av /src/ /dst/   # 把 /src/ 的「內容」同步到 /dst/
rsync -av /src  /dst/   # 把 /src「目錄」同步到 /dst/（結果是 /dst/src/）
```

## 動手練習

```bash
# 1. 看系統的 IP 和路由
ip addr
ip route

# 2. 找哪些服務在監聽
ss -tlnp

# 3. 用 curl 測試 API
curl -s https://httpbin.org/json | python3 -m json.tool
curl -w "\nStatus: %{http_code}\n" -o /dev/null -s https://httpbin.org/status/200

# 4. 用 curl 模擬 POST
curl -s -X POST https://httpbin.org/post \
     -H "Content-Type: application/json" \
     -d '{"name":"Alice","age":30}' | python3 -m json.tool

# 5. SSH 設定（如果有遠端主機）
# cat ~/.ssh/config
# ssh-keygen -t ed25519 -C "mykey"    # 建立 ed25519 key
# ssh-copy-id user@host               # 把公鑰複製過去

# 6. rsync 本地測試
mkdir -p /tmp/src /tmp/dst
echo "file1" > /tmp/src/file1.txt
echo "file2" > /tmp/src/file2.txt
rsync -av /tmp/src/ /tmp/dst/
ls /tmp/dst
```

## 自我檢核

- [ ] 知道 `ss -tlnp` 比 `netstat -tlnp` 更現代
- [ ] 能用 `curl -w` 取得 HTTP status code
- [ ] 知道 ssh 的 config 檔可以大幅簡化連線指令
- [ ] 知道 `rsync` 的 trailing slash 決定同步行為

→ [Ch 29 系統監控](./29-system-monitoring.md)
