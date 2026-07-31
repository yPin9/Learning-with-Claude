# Ch 9 — mount 與檔案系統階層

> **目標**：理解 mount 機制——磁碟/檔案系統如何「掛載」到目錄樹的某個節點、mount namespace、/etc/fstab、以及為什麼 Linux 能把不同磁碟、不同檔案系統、甚至網路儲存統一成一棵樹。這完成了 Part 2 的檔案系統全圖。

> **環境**：Linux，mount/umount，/etc/fstab。承接 Ch 3（單一樹）、Ch 4（VFS）、Ch 8（區塊裝置）。

## 為什麼需要 mount？

Ch 3 說 Linux 是「單一一棵樹」，磁碟「掛載」到樹的節點。但這個「掛載」到底是什麼？你有一顆新硬碟（`/dev/sdb`，一個區塊裝置，Ch 8），上面有檔案系統。怎麼讓它的內容出現在目錄樹裡，讓你能 `cd` 進去、`ls` 它的檔案？

答案是 **mount**——把一個檔案系統「接」到目錄樹的某個目錄上。理解 mount，你就懂了 Linux「單一樹」的實現機制、為什麼 `/` `/home` `/boot` 可能是不同磁碟、以及容器/chroot 怎麼用 mount 做隔離。這完成了 Part 2 的檔案系統全圖。

## 先建立直覺：mount 是「把檔案系統接到樹上」

```
mount 的概念：

  你有一個檔案系統（在 /dev/sdb1 上，Ch 8 的區塊裝置）：
    /dev/sdb1 的內容：
      docs/  photos/  data.txt

  目錄樹上有一個空目錄 /mnt/disk：
    /mnt/disk/  （空的）

  mount /dev/sdb1 /mnt/disk 做的事：
    把 /dev/sdb1 的檔案系統「接」到 /mnt/disk
        │
  之後：
    /mnt/disk/docs/      → 其實是 /dev/sdb1 上的 docs
    /mnt/disk/data.txt   → 其實是 /dev/sdb1 上的 data.txt
        │
  → /mnt/disk 這個「掛載點」變成通往 /dev/sdb1 的入口
    你 cd /mnt/disk 就進到那顆磁碟的檔案系統
```

mount 是「把檔案系統接到目錄樹的某個點（掛載點）」。掛載點原本是個空目錄，mount 後它變成「通往那個檔案系統的門」。這就是 Ch 3「磁碟掛載到樹的節點」的具體機制——不是 Windows 的 C:/D: 分槽，而是接到同一棵樹的不同節點。

## 基本 mount 操作

```bash
# 看當前掛了哪些檔案系統
mount | head
# /dev/sda2 on / type ext4 (rw,relatime)        ← 根在 sda2
# /dev/sda1 on /boot/efi type vfat (...)         ← ESP（Ch linux_boot）
# proc on /proc type proc (...)                  ← /proc 也是「掛載」的（Ch 16）
# ...

# 或用 findmnt（更清楚的樹狀顯示）
findmnt | head

# 掛載一個檔案系統（需要 root）
sudo mkdir -p /mnt/disk
sudo mount /dev/sdb1 /mnt/disk      # 把 sdb1 掛到 /mnt/disk
ls /mnt/disk                        # 看到 sdb1 的內容

# 卸載
sudo umount /mnt/disk               # 卸載（disk 變回空目錄）
# 或 umount /dev/sdb1（用裝置名也行）
```

> 注意 `mount` 不帶參數時列出所有掛載——包括 `/proc`、`/sys`、`/dev` 這些「虛擬檔案系統」。它們不是真磁碟，但也用 mount 機制接到樹上（`/proc` 是 proc 檔案系統，Ch 16）。這體現了 mount 的通用性：不只磁碟，虛擬檔案系統、網路儲存、tmpfs（記憶體檔案系統）都用同一個 mount 機制接到樹上。「一切皆檔案系統，都用 mount 接」是 Ch 1「一切皆檔案」的延伸。

## 掛載點原本的內容怎麼了

一個常見困惑：如果掛載點目錄原本有檔案，mount 後它們去哪了？

```bash
# 掛載點原本有東西
sudo mkdir -p /mnt/test
echo "original" | sudo tee /mnt/test/before.txt
ls /mnt/test                        # before.txt

# 掛載一個檔案系統上去
sudo mount /dev/sdb1 /mnt/test      # （假設 sdb1 有別的內容）
ls /mnt/test                        # sdb1 的內容，before.txt「不見了」！

# 卸載後 before.txt 又回來
sudo umount /mnt/test
ls /mnt/test                        # before.txt（回來了）
```

```
掛載點原本內容的命運：
  mount 後，掛載點原本的內容被「遮蓋」（不是刪除！）
    /mnt/test/before.txt 還在原本的磁碟（根檔案系統）
    只是被掛上去的 sdb1 「蓋住」了，看不到
        │
  卸載後，遮蓋解除，原本內容又出現
        │
  → mount 是「覆蓋」，不是「合併」或「刪除」
    原本內容安全（在底層的檔案系統上），只是暫時看不到
```

> 「掛載點原本內容被遮蓋」是個重要概念。mount 不刪除掛載點原本的東西，是「蓋住」它。原本的 `before.txt` 還在根檔案系統上，只是被掛上去的 sdb1 蓋住看不到。卸載後又出現。這解釋了一個常見事故：如果你不小心掛東西到一個有重要資料的目錄，資料「不見了」（其實被遮蓋），卸載就回來。也是某些「磁碟空間之謎」的來源——被掛載遮蓋的檔案還佔著底層磁碟空間，但你看不到它們。

## /etc/fstab：開機自動掛載

手動 mount 重開機就沒了。`/etc/fstab` 定義「開機時自動掛載哪些」：

```bash
cat /etc/fstab
# <device>           <mount point>  <type>  <options>      <dump> <pass>
# UUID=abc-123       /              ext4    defaults        0      1
# UUID=def-456       /boot/efi      vfat    umask=0077      0      1
# UUID=ghi-789       /home          ext4    defaults        0      2
# /dev/sdb1          /mnt/data      ext4    defaults,noauto 0      0
```

fstab 各欄位：

| 欄位 | 意義 |
|---|---|
| device | 掛什麼（UUID 最穩定，Ch 8 的 udev）|
| mount point | 掛到哪 |
| type | 檔案系統類型（ext4/vfat/...）|
| options | 選項（defaults/ro/noauto/...）|
| dump | 是否被 dump 備份（通常 0）|
| pass | fsck 檢查順序（根=1，其他=2，不檢查=0）|

```bash
# fstab 設好後，可以用掛載點直接 mount（不用打裝置和類型）
sudo mount /mnt/data         # mount 從 fstab 查 /mnt/data 的設定

# 測試 fstab 是否正確（不真的掛，只檢查語法）
sudo mount -a                # 掛載所有 fstab 裡 auto 的項目
```

> **用 UUID 而非 /dev/sda1**：fstab 用裝置 UUID（`UUID=abc-123`）比用 `/dev/sda1` 穩定——因為 `sda`/`sdb` 的編號可能隨硬碟插拔順序變（Ch 8 的 udev），但 UUID 跟著檔案系統不變。fstab 寫錯（如錯誤的裝置、錯誤的選項）可能讓系統開不了機（卡在掛載失敗）——這呼應 linux_boot 課程的開機診斷。改 fstab 後用 `mount -a` 測試，確認沒問題再重開機。`pass` 欄位的 fsck 順序：根檔案系統先檢查（1），其他後檢查（2）。

## mount 選項

mount 的選項控制掛載的行為：

```bash
# 常用 mount 選項
sudo mount -o ro /dev/sdb1 /mnt/disk          # ro：唯讀掛載（防止寫入）
sudo mount -o noexec /dev/sdb1 /mnt/disk      # noexec：禁止執行（安全）
sudo mount -o nosuid /dev/sdb1 /mnt/disk      # nosuid：忽略 setuid（安全）
sudo mount -o remount,rw /                     # remount：重新掛載改選項
#   ↑ 把根從唯讀改可寫（救援模式常用，linux_boot Ch 29）

# 看一個掛載的選項
findmnt /
# /  /dev/sda2  ext4  rw,relatime
#                     ↑ 選項
```

> mount 選項是安全和功能的重要工具。`ro`（唯讀）防止寫入（保護資料、救援時防破壞）。`noexec`/`nosuid`/`nodev`（合稱 noexec,nosuid,nodev）用於不信任的檔案系統（如 USB、/tmp）——禁止執行、忽略 setuid、忽略裝置檔案，防止透過掛載的檔案系統提權或執行惡意程式。`remount,rw` 在救援模式把唯讀根改可寫（linux_boot Ch 29 的救援技巧）。理解這些選項，能做出更安全的掛載配置。

## bind mount：把目錄掛到另一個目錄

`bind mount` 是特殊的 mount——把一個**目錄**掛到另一個目錄（不是掛磁碟）：

```bash
# bind mount：讓 /mnt/projects 等同於 /home/you/projects
sudo mkdir -p /mnt/projects
sudo mount --bind /home/you/projects /mnt/projects
ls /mnt/projects             # = /home/you/projects 的內容
#   ↑ 同一份資料，兩個路徑都能存取

sudo umount /mnt/projects
```

```
bind mount 的用途：
  - chroot/容器：把 host 的目錄掛進隔離環境
    （linux_boot Ch 29 的 chroot 救援：mount --bind /dev /mnt/dev）
  - 讓同一份資料出現在多個路徑
  - 把某個目錄掛成唯讀（mount --bind + remount,ro）
        │
  和 symlink 的差別：
  symlink 是「路徑指標」（Ch 6），bind mount 是「真正的掛載」
  bind mount 在 mount table 裡（mount 看得到），更底層
```

bind mount 是容器和 chroot 的基礎（linux_boot Ch 29 的 chroot 救援就用 `mount --bind /dev /mnt/dev`）。它讓一個目錄出現在另一個位置，比 symlink 更底層（真正的掛載，不是路徑指標）。Docker 的 volume 掛載本質就是 bind mount。

## 故意弄壞：umount 一個正在用的檔案系統

```bash
sudo mount /dev/sdb1 /mnt/disk
cd /mnt/disk                 # 你的 shell 的 CWD 在裡面（Ch 3）

# 嘗試卸載
sudo umount /mnt/disk
# umount: /mnt/disk: target is busy.
#   ↑ 卸載失敗！因為有 process（你的 shell）的 CWD 在裡面

# 找出誰在用
sudo lsof /mnt/disk          # 或 fuser -m /mnt/disk
# COMMAND  PID  USER  ...  bash  ...  /mnt/disk
#   ↑ 你的 bash 在用它

cd ~                         # 離開那個目錄
sudo umount /mnt/disk        # 現在能卸載了
```

「target is busy」是 umount 最常見的錯誤——有 process 正在用那個檔案系統（CWD 在裡面、開著裡面的檔案）。用 `lsof`/`fuser` 找出誰在用，讓它離開（cd 走、關閉檔案），才能卸載。`umount -l`（lazy umount）能「延遲卸載」（等沒人用時才真卸），但要小心使用。這呼應 Ch 4/6 的「inode 被引用就不釋放」——檔案系統被引用就不能卸載。

## 踩雷集錦

1. **以為 mount 會合併內容**：mount 是「覆蓋」掛載點原本的內容（遮蓋，不刪除）。原本內容卸載後回來。別誤以為資料不見了

2. **fstab 用 /dev/sdX 而非 UUID**：裝置名（sda/sdb）可能隨插拔變。用 UUID 穩定。fstab 寫錯可能讓系統開不了機

3. **umount 報 target is busy 不知道為什麼**：有 process 在用（CWD 在裡面、開著檔案）。`lsof`/`fuser` 找出誰，讓它離開

4. **掛載到有資料的目錄遮蓋資料**：mount 到非空目錄會遮蓋原內容。確認掛載點是空的（或你知道在做什麼）

5. **混淆 bind mount 和 symlink**：symlink 是路徑指標（Ch 6），bind mount 是真正的掛載（在 mount table）。chroot/容器用 bind mount，不是 symlink

## 進階：mount namespace 與容器隔離

mount 不是全系統共享的——每個 **mount namespace** 有自己的 mount table，這是容器隔離的基礎：

```
mount namespace（容器隔離的基礎）：
  傳統：全系統共享一個 mount table（大家看到一樣的掛載）
        │
  mount namespace：每個 namespace 有自己的 mount table
    容器 A 的 namespace：看到自己的 /、/proc...
    容器 B 的 namespace：看到不同的 /
    host：看到 host 的掛載
        │
  → 容器裡 mount 東西，不影響 host（隔離）
  → 容器的「根」是 host 上某個目錄（用 pivot_root/chroot）
        │
  unshare --mount 能建立新的 mount namespace（手動實驗）
```

```bash
# 看一個 process 的 mount namespace
ls -l /proc/self/ns/mnt
# lrwxrwxrwx ... mnt -> 'mnt:[4026531840]'    ← namespace ID

# 建立新的 mount namespace（隔離的）
sudo unshare --mount --fork bash
# 在這個新 namespace 裡 mount 東西，不影響外面
```

> mount namespace 是容器（Docker）隔離的核心之一。每個容器有自己的 mount table——容器裡看到的 `/`、`/proc` 是容器自己的，容器裡 mount 東西不影響 host。這和 linux_boot 的 `switch_root`、bind mount 串起來：容器啟動時，用 mount namespace 隔離 + bind mount 把 host 的某些目錄掛進去 + pivot_root 切換根。如果你修過 docker 課程，會認出這是 namespace 隔離的一部分。理解 mount namespace，你會懂「為什麼容器裡的掛載和 host 不同」「為什麼 `mount` 在容器裡看到的不一樣」。

## 動手練習

1. 看當前掛載：`mount` 和 `findmnt`，認出根（/）、/boot、/proc 等各掛在哪個裝置/類型。注意 /proc /sys 是虛擬檔案系統也用 mount

2. 看 fstab：`cat /etc/fstab`，理解每個項目（裝置 UUID、掛載點、類型、選項）。對照 `findmnt` 看實際掛載

3. 玩 bind mount（需 root，VM）：`mount --bind` 一個目錄到另一個，確認兩個路徑同一份資料。理解它和 symlink 的差別

4. 跑「故意弄壞」：cd 進一個掛載點再 umount，看 "target is busy"。用 lsof 找出是你的 shell 在用，cd 走後卸載

## 本章重點整理

- mount 把檔案系統「接」到目錄樹的某個節點（掛載點）；這是 Linux「單一樹」的實現（Ch 3）
- 不只磁碟，虛擬檔案系統（/proc /sys）、tmpfs、網路儲存都用 mount 接到樹上
- mount 覆蓋（遮蓋）掛載點原本的內容（不刪除，卸載後回來）
- /etc/fstab 定義開機自動掛載（用 UUID 穩定）；mount 選項（ro/noexec/nosuid）控制安全和行為
- bind mount 把目錄掛到另一個目錄（chroot/容器基礎）；mount namespace 讓每個容器有獨立 mount table（隔離）

## 自我檢核

- [ ] 能解釋 mount 是什麼（把檔案系統接到目錄樹的節點），以及它如何實現「單一樹」
- [ ] 知道掛載點原本內容的命運（被遮蓋，不刪除）
- [ ] 能讀懂 /etc/fstab，知道為什麼用 UUID 而非 /dev/sdX
- [ ] 知道 bind mount 是什麼、和 symlink 的差別、用於 chroot/容器
- [ ] 知道 mount namespace 怎麼讓容器有獨立的掛載視圖

## 延伸閱讀

### 書籍

- **《The Linux Programming Interface》— Ch 14 (File Systems - mounting)** — Michael Kerrisk
  - **讀哪幾章**：Ch 14 的 mounting、bind mount、mount namespace 那幾節
  - **這本書的定位**：mount 機制的權威來源
  - **前提**：本章

### 官方文件

- **[mount(8)](https://man7.org/linux/man-pages/man8/mount.8.html)** 和 **[mount_namespaces(7)](https://man7.org/linux/man-pages/man7/mount_namespaces.7.html)** man pages
  - **讀哪裡**：mount 的選項、mount_namespaces 的隔離機制
  - **學什麼**：mount 選項的完整列表、namespace 的權威定義
  - **前提**：本章

- **[fstab(5) man page](https://man7.org/linux/man-pages/man5/fstab.5.html)**
  - **讀哪裡**：各欄位定義
  - **學什麼**：fstab 格式的權威定義
  - **前提**：本章

### 部落格 / 文章

- **[How containers work: mount namespaces](https://jvns.ca/blog/2016/10/10/what-even-is-a-container/)** — Julia Evans
  - **這篇說什麼**：容器怎麼用 namespace（含 mount namespace）做隔離
  - **讀哪裡**：mount namespace 那部分
  - **為什麼值得讀**：把本章的 mount namespace 連到容器的實際運作

→ [練習 A：手工探索 inode/link/權限](./practice-a-inode-explore.md)
