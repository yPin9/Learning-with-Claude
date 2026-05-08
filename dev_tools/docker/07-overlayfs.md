# Ch 7 — OverlayFS：容器的檔案系統魔法

> 目標：理解 OverlayFS（聯合檔案系統）的四層結構與 Copy-on-Write 機制，能不靠 Docker 手動掛載 OverlayFS，並能用 `docker diff` 追蹤容器對 image 做了哪些修改。

---

## Union Filesystem 是什麼

你在 `docker run` 進去一個容器，看起來有完整的 `/bin`、`/etc`、`/usr`，但那些檔案實際上是唯讀的 image layers。你新建、修改的任何檔案卻不會影響 image 本身。這個「疊加視圖」就是 Union Filesystem（聯合檔案系統）做的事。

概念很簡單：把多個目錄疊在一起，對使用者呈現成一個統一的掛載點。讀取時由上往下找，寫入時只寫最上層。

Linux 原生的實作叫 **OverlayFS**，kernel 3.18 合入主線，是現在 Docker 預設的 storage driver（儲存驅動）。

---

## OverlayFS 四個目錄

```
+--------------------------------------------------+
|                  merged/                         |  <- 使用者看到的視圖（掛載點）
|  a.txt (已修改)   b.txt   c.txt (新建)           |
+--------------------------------------------------+
         |          讀取從上往下找          |
         v                                 v
+------------------+          +------------------+
|    upperdir/     |          |    lowerdir/     |  <- 可以多層，冒號分隔
|  a.txt (CoW副本) |          |  lower1/a.txt    |
|  c.txt (新建)    |          |  lower1/...      |
+------------------+          |  lower2/b.txt    |
                              |  lower2/...      |
+------------------+          +------------------+
|    workdir/      |  <- kernel 內部用，不給使用者直接存取
|  (暫存空間)      |
+------------------+
```

| 目錄 | 可寫 | 用途 |
|------|------|------|
| lowerdir | 唯讀 | image layers，可以多層（冒號分隔，左邊優先） |
| upperdir | 可寫 | container 的所有修改都落在這裡 |
| workdir  | 可寫（kernel 用） | atomic rename 的暫存空間，必須和 upperdir 同一個 filesystem |
| merged   | 可寫（透過 CoW） | 使用者看到的統一視圖 |

Docker 的 image 每一層對應一個 lowerdir，從最舊的 base 層疊到最新層，container 啟動時再加一個 upperdir。

---

## 動手掛載 OverlayFS（不靠 Docker）

以下操作需要 root，在 Linux 主機或 WSL2 均可執行。

```bash
# 建目錄結構
mkdir -p /tmp/ol/{lower1,lower2,upper,work,merged}

# 在 lower 層放一些檔案
echo "from lower1" > /tmp/ol/lower1/a.txt
echo "from lower2" > /tmp/ol/lower2/b.txt

# 掛載 OverlayFS
# lowerdir 左邊優先：lower1 會蓋過 lower2 的同名檔案
mount -t overlay overlay \
  -o lowerdir=/tmp/ol/lower1:/tmp/ol/lower2,\
upperdir=/tmp/ol/upper,\
workdir=/tmp/ol/work \
  /tmp/ol/merged

# 確認掛載成功
mount | grep overlay
```

現在在 merged 裡可以看到兩個 lower 的所有檔案：

```bash
ls /tmp/ol/merged
# a.txt  b.txt

cat /tmp/ol/merged/a.txt
# from lower1

cat /tmp/ol/merged/b.txt
# from lower2
```

### 實驗一：修改 lowerdir 的檔案，觀察 CoW

```bash
# 修改來自 lower1 的 a.txt
echo "modified" > /tmp/ol/merged/a.txt

# merged 裡看到的是新內容
cat /tmp/ol/merged/a.txt
# modified

# lower1 的原始檔案完全沒動
cat /tmp/ol/lower1/a.txt
# from lower1

# upper 裡多了一份 copy
cat /tmp/ol/upper/a.txt
# modified

ls /tmp/ol/upper/
# a.txt
```

這就是 Copy-on-Write（寫時複製）。kernel 先把 lowerdir 的檔案複製到 upperdir，再對 upperdir 的副本執行寫入，lowerdir 永遠不動。

### 實驗二：新建檔案

```bash
echo "new file" > /tmp/ol/merged/c.txt

# 新檔只在 upper，lower 不受影響
ls /tmp/ol/upper/
# a.txt  c.txt

ls /tmp/ol/lower1/
# a.txt    （沒有 c.txt）
```

### 實驗三：刪除 lowerdir 的檔案（whiteout）

OverlayFS 用「whiteout 檔案（遮蔽檔）」標記刪除，lower 裡的原始檔不動。

```bash
rm /tmp/ol/merged/b.txt

# upper 裡會出現一個特殊的 char device，major:minor = 0:0
ls -la /tmp/ol/upper/
# c---------  1 root root 0, 0 ... b.txt   <- whiteout

# lower2 的 b.txt 還在
cat /tmp/ol/lower2/b.txt
# from lower2

# merged 裡看不到 b.txt（被 whiteout 遮住）
ls /tmp/ol/merged/
# a.txt  c.txt
```

清理：

```bash
umount /tmp/ol/merged
rm -rf /tmp/ol
```

---

## Copy-on-Write 詳細流程

```
讀取 merged/a.txt
  -> 先找 upperdir/a.txt  -> 找到就用（upper 優先）
  -> 找不到往下找         -> lower1/a.txt -> 找到，傳回

寫入 merged/a.txt（a.txt 原本只在 lowerdir）
  step 1. kernel 把 lowerdir/a.txt 完整 copy 到 upperdir/a.txt
  step 2. 對 upperdir/a.txt 執行實際寫入
  lowerdir 完全不動，永遠唯讀
```

這個設計讓 image layer 永遠唯讀，多個容器可以共用同一份 lowerdir，只有 upperdir 是每個容器獨立的，節省大量磁碟空間。10 個容器跑同一個 ubuntu image，lowerdir 只有一份，upperdir 各自獨立。

---

## docker diff：看容器對 image 做了哪些修改

```bash
# 跑一個容器，做些修改
docker run -it --name test alpine sh
# 在容器裡：
# echo "hello" > /root/test.txt
# rm /etc/hostname
# exit

docker diff test
```

輸出格式：

```
A /root/test.txt        <- Added（新增）
D /etc/hostname         <- Deleted（刪除）
C /etc                  <- Changed（目錄內容有變動）
C /root
```

`docker diff` 實際上就是在掃描 container 的 upperdir，把 whiteout 檔案轉成 D，新增的顯示 A，修改的顯示 C。

---

## Docker image layers 在磁碟上的實際位置

```bash
# 確認 storage driver
docker info | grep "Storage Driver"
# Storage Driver: overlay2

# image layers 的實際位置
ls /var/lib/docker/overlay2/
```

典型的 `/var/lib/docker/overlay2/` 結構：

```
/var/lib/docker/overlay2/
├── <hash_A>/              <- 一個 layer
│   ├── diff/              <- 這層的實際檔案（對應 lowerdir 的一層）
│   ├── link               <- 短連結 ID（避免 lowerdir= 參數過長）
│   ├── lower              <- 指向更下面的 layer（用 link ID 串接）
│   └── work/
├── <hash_B>/
│   ├── diff/
│   ├── link
│   └── lower
└── l/                     <- 短連結目錄
    ├── ABCDEF -> ../<hash_A>/diff
    └── GHIJKL -> ../<hash_B>/diff
```

查看 running container 實際掛載的 overlay 參數：

```bash
# 從 inspect 找到 GraphDriver 資訊
docker inspect <container_id> | python3 -m json.tool | grep -A 20 '"GraphDriver"'

# 直接看 /proc 的 mountinfo
PID=$(docker inspect <container_id> --format '{{.State.Pid}}')
cat /proc/$PID/mounts | grep overlay
```

---

## docker commit：把 upperdir 包成新 image layer

```bash
# 先跑一個容器，做些修改
docker run -it --name myapp ubuntu:22.04 bash
# apt-get update && apt-get install -y curl
# exit

# 把容器目前狀態（upperdir）打包成新 image
docker commit myapp myapp:v1

# 看新 image 多了哪些 layer
docker history myapp:v1
```

`docker commit` 的本質：把 container 的 upperdir 打包成一個 tar，追加到 image 的 layer 堆疊。

**為什麼生產環境不用 docker commit**：

- 沒有可重現性，無法知道你在容器裡做了哪些操作
- 每次 commit 都是不透明的 layer，安全 audit 困難
- 正確做法是寫 Dockerfile，每一步都有文字記錄，可以 code review

`docker commit` 的合理用途：快速做 debug snapshot，或是把容器裡的臨時狀態帶走給別人重現問題。了解原理即可，不要在 CI/CD 流程裡用它。

---

## 自我檢核

- [ ] 能說清楚 lowerdir / upperdir / workdir / merged 各自的功能
- [ ] 能在不靠 Docker 的情況下，手動 mount 一個 OverlayFS 並觀察 CoW 行為
- [ ] 知道修改 lowerdir 的檔案時，kernel 的兩步動作是什麼
- [ ] 知道 OverlayFS 用什麼機制標記「刪除 lowerdir 的檔案」
- [ ] 能用 `docker diff` 列出容器的修改，並解讀 A/C/D 代表什麼
- [ ] 知道 Docker image layers 實際放在 `/var/lib/docker/overlay2/` 的哪個子目錄
- [ ] 知道 `docker commit` 的原理和為什麼生產環境不用它

OverlayFS 解決了「多個容器共用 image 又互相隔離」的問題，但容器裡的 process 是怎麼被啟動的？`docker run` 背後有多少層軟體在工作？

→ [Ch 8 containerd 與 runc](./08-containerd-runc.md)
