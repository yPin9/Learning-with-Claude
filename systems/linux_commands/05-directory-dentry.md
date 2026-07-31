# Ch 5 — 目錄與 dentry

> **目標**：理解目錄的真相——目錄是「檔名 → inode 號」的對應表（一種特殊檔案）、`getdents64` 怎麼讀目錄、dentry cache 的角色、以及為什麼「在目錄裡建檔」需要對目錄有寫權限。這解釋了檔名到底存在哪。

> **環境**：Linux，ext4 為主。承接 Ch 4（inode）。原理深挖章。

## 為什麼目錄不是「裝檔案的資料夾」？

Ch 4 留下一個問題：inode 不存檔名，那檔名存在哪？答案是**目錄**。但目錄不是你想像的「裝檔案的容器」——它是一個**特殊的檔案，內容是一張「檔名 → inode 號」的對應表**。

理解這個顛覆性的真相，很多事就通了：為什麼 hard link 能讓一個 inode 有多個名字（目錄裡多條 entry 指向同一 inode）、為什麼 `rm` 需要對目錄有寫權限而不是對檔案（rm 改的是目錄這張表）、為什麼 `ls` 底層是 `getdents64`（讀目錄這個檔案的內容）。

## 先建立直覺：目錄是一張對應表

```
目錄的真相：它是一個特殊檔案，內容是對應表

  目錄 /home/you/cmdlab（其實是個檔案）的「內容」：
  ┌──────────────┬───────────┐
  │  檔名         │  inode 號  │
  ├──────────────┼───────────┤
  │  .           │  1000     │  ← 指向自己（這個目錄的 inode）
  │  ..          │  900      │  ← 指向上層目錄的 inode
  │  file.txt    │  1234567  │  ← Ch 4 的那個檔案
  │  notes.md    │  1234568  │
  │  subdir      │  1234569  │  ← 子目錄（也是個目錄檔案）
  └──────────────┴───────────┘
        │
  → 目錄 = 「檔名 → inode 號」的映射表
  → 檔名存在「目錄」這個檔案裡，不在 inode 裡（Ch 4）
```

關鍵心智轉變：目錄不是「裝東西的盒子」，是「一張電話簿」——把名字（檔名）對應到號碼（inode 號）。檔案的本體（inode + 資料）不在目錄裡，目錄只存「名字 → inode 號」的對應。`ls` 就是讀這張電話簿。

## 目錄裡的 . 和 ..

每個目錄至少有兩條特殊 entry：

```
. （點）   → 指向「這個目錄自己」的 inode
.. （點點）→ 指向「上層目錄」的 inode
        │
  這就是為什麼 . 和 .. 能用（Ch 3）：
  它們是目錄裡實際存在的 entry！
        │
  ls -a 會顯示它們（-a 顯示隱藏的，包括 . 和 ..）
```

```bash
cd ~/cmdlab
ls -ai          # -a 顯示全部（含 . ..），-i 顯示 inode 號
# 1000 .        ← . 的 inode = 這個目錄的 inode
#  900 ..       ← .. 的 inode = 上層目錄的 inode
# 1234567 file.txt
# ...

# 驗證：. 的 inode 就是當前目錄的 inode
stat -c %i .            # 當前目錄的 inode 號
ls -di .               # 同上（-d 看目錄本身不看內容）
```

> `.` 和 `..` 不是 shell 的魔法——它們是目錄檔案裡**實際存在的 entry**。每個目錄建立時，kernel 自動加 `.`（指向自己）和 `..`（指向父目錄）。這解釋了 Ch 3 的 `cd ..` 為什麼能上一層——`..` 是目錄裡真實的 entry，指向父目錄的 inode。也解釋了為什麼空目錄的 link count 是 2（自己的名字 + 它自己的 `.`，Ch 6 詳述）。

## ls 底層：getdents64

`ls` 怎麼讀目錄？用 `getdents64` syscall（get directory entries）讀目錄這個檔案的內容：

```bash
strace -e openat,getdents64 ls ~/cmdlab 2>&1 | head
# openat(AT_FDCWD, "/home/you/cmdlab", O_RDONLY|O_DIRECTORY) = 3
#   ↑ 開啟目錄（注意 O_DIRECTORY，表示這是目錄）
# getdents64(3, ...) = 192
#   ↑ 讀目錄 entry（檔名 → inode 號的列表）
# getdents64(3, ...) = 0    ← 0 表示讀完了
```

```
ls 的完整流程（Ch 0 的 strace 現在懂了）：
  1. openat 開啟目錄（O_DIRECTORY）
  2. getdents64 讀出所有 entry（檔名 + inode 號）
  3. 對每個檔名，statx 讀它的 inode（取得大小/權限/時間，Ch 4）
     （ls -l 才需要；單純 ls 不一定 stat 每個）
  4. write 把結果排版輸出（fd 1，Ch 19）
        │
  → ls = 讀目錄表 + 查每個 inode + 排版輸出
```

> 現在你完全懂 Ch 0 的 `strace ls` 了：`openat`（開目錄）+ `getdents64`（讀目錄表）+ `statx`（查每個檔案的 inode）+ `write`（輸出）。`ls -l` 慢是因為它要對每個檔案 `statx` 一次（讀 inode 取得詳細資訊）；單純 `ls`（只列名字）不用 stat，快很多。這解釋了為什麼 `ls` 一個有海量檔案的目錄，`ls -l` 比 `ls` 慢得多。

## 為什麼建檔/刪檔需要目錄的寫權限

這是個常讓人困惑的點：刪除一個檔案，需要的是**對目錄的寫權限**，不是對檔案的寫權限：

```
建檔/刪檔修改的是「目錄」這張表：
  建檔 = 在目錄表加一條 entry（檔名 → inode）
  刪檔 = 從目錄表移除一條 entry
  改名 = 改目錄表的 entry
        │
  這些都是「修改目錄這個檔案的內容」
  → 需要對「目錄」有寫權限（w）
  → 不需要對「檔案」有寫權限！
```

```bash
cd ~/cmdlab
# 建一個唯讀檔案
echo "data" > readonly.txt
chmod 444 readonly.txt       # 檔案唯讀（沒有寫權限）

# 還是能刪除它！（因為刪除改的是目錄，不是檔案）
rm readonly.txt              # rm: remove write-protected file? 
# 它會問（因為檔案唯讀是反常），但 y 就刪得掉
# 只要你對「目錄」有寫權限
```

```bash
# 反過來：對目錄唯讀，即使檔案可寫也刪不掉
mkdir locked
echo "data" > locked/file.txt
chmod 555 locked             # 目錄唯讀（沒寫權限）
rm locked/file.txt           # rm: cannot remove: Permission denied
#   ↑ 即使 file.txt 可寫，但目錄不可寫，刪不掉
#     （刪除要改目錄表，目錄唯讀就不行）
chmod 755 locked             # 恢復
```

> 「刪檔需要目錄寫權限，不是檔案寫權限」是 Unix 權限最反直覺的點之一。因為刪檔（移除 entry）改的是**目錄**這張表，不是檔案本身。這解釋了：(1) 唯讀檔案也能刪（只要目錄可寫）；(2) `/tmp` 用 sticky bit 防止互刪（Ch 7）——大家都能在 /tmp 建檔（目錄可寫），但 sticky bit 限制只能刪自己的。理解目錄是「表」，這些權限行為就合理了。

## dentry cache：加速路徑解析

路徑解析（Ch 3）要逐段查目錄，頻繁查目錄很慢。kernel 用 **dentry cache**（directory entry cache）加速：

```
dentry cache（kernel 的快取）：
  路徑解析 /home/you/file 要查：
    / 裡的 "home" → /home 裡的 "you" → /home/you 裡的 "file"
        │
  每次都查磁碟很慢
        │
  dentry cache：把「最近查過的 檔名 → inode 對應」快取在記憶體
    第二次查同樣的路徑 → 直接從 cache 拿，不碰磁碟
        │
  → 大幅加速重複的路徑解析
```

dentry（directory entry）是 VFS（Ch 4）的概念——它代表「一個檔名到 inode 的對應」。dentry cache 把這些對應快取起來，避免重複查磁碟。你不直接操作 dentry cache，但它解釋了為什麼「第二次 ls 同個目錄比第一次快」（部分資訊在 cache）。

## 故意弄壞：目錄的 link count 之謎

```bash
cd ~/cmdlab
mkdir testdir
stat -c "%h %n" testdir      # link count 和名字
# 2 testdir                  ← 空目錄 link count = 2！為什麼不是 1？

# 加一個子目錄
mkdir testdir/sub
stat -c "%h %n" testdir
# 3 testdir                  ← 變 3 了！每加一個子目錄就 +1
```

為什麼空目錄 link count 是 2，加子目錄就 +1？

```
目錄的 link count 計算：
  testdir 的 link count = 指向 testdir inode 的「名字」數量
    1. 父目錄裡的 "testdir" entry         （+1）
    2. testdir 自己裡的 "." entry          （+1，. 指向自己）
    = 2（空目錄）
        │
  加一個子目錄 sub：
    3. sub 裡的 ".." entry 指向 testdir    （+1）
    = 3
        │
  → 目錄 link count = 2 + 子目錄數量
    （這是判斷「一個目錄有幾個子目錄」的技巧）
```

這個「空目錄 link count = 2」之謎，揭示了 `.` 和 `..` 是真實的 hard link（Ch 6）。每個目錄被「父目錄的名字」+「自己的 `.`」指向（=2），每個子目錄的 `..` 再 +1。理解這個，你完全掌握了「目錄是對應表，. 和 .. 是真實 entry」。

## 踩雷集錦

1. **以為目錄是「裝檔案的容器」**：目錄是「檔名 → inode 號」的對應表（一種特殊檔案）。檔案本體（inode）不在目錄裡，目錄只存對應

2. **以為刪檔需要檔案的寫權限**：刪檔改的是目錄表，需要**目錄**的寫權限。唯讀檔案也能刪（只要目錄可寫）

3. **以為 . 和 .. 是 shell 的特殊語法**：它們是目錄裡真實存在的 entry（每個目錄都有）。kernel 建目錄時自動加

4. **困惑空目錄 link count 為什麼是 2**：父目錄的名字 + 自己的 `.`（=2）。加子目錄 +1（子目錄的 `..`）。是 hard link 的計數

5. **以為 ls 直接「看到」檔案**：ls 是 openat 開目錄 + getdents64 讀對應表 + statx 查每個 inode。ls -l 慢是因為每個檔案 stat 一次

## 進階：目錄的內部結構演進

目錄這張「對應表」在底層怎麼存？隨檔案系統演進：

```
目錄內部結構的演進：
  早期（ext2）：線性列表
    目錄就是一串 entry，查找要線性掃描
    → 目錄裡檔案多時，查找 O(n) 慢
        │
  現代（ext4 htree、XFS B+tree）：樹狀索引
    用 hash tree / B+tree 索引檔名
    → 查找 O(log n)，海量檔案也快
        │
  這解釋了為什麼現代檔案系統能應付「一個目錄幾百萬檔案」
  （早期檔案系統這樣會慢到無法用）
```

> 目錄的內部結構是檔案系統效能的關鍵。早期 ext2 用線性列表，目錄裡檔案一多，每次查找（路徑解析的一段）就慢。現代 ext4（htree）、XFS（B+tree）用樹狀索引，查找快得多。這就是為什麼「在一個目錄放幾十萬個檔案」在現代檔案系統可行（雖然仍不建議——`ls` 還是要讀全部 entry）。如果你修過資料結構，會認出這是「線性查找 vs 樹查找」的經典問題應用在檔案系統。

## 動手練習

1. 看目錄是對應表：`ls -ai ~/cmdlab`，看 `.`（自己的 inode）、`..`（父的 inode）、和各檔案的 inode 號。確認 `.` 的 inode = 當前目錄的 inode（`stat -c %i .`）

2. 看 ls 底層：`strace -e openat,getdents64,statx ls -l ~/cmdlab`，認出開目錄、讀目錄表、stat 每個檔案。對比 `ls`（不 -l）少了很多 statx

3. 跑「故意弄壞」的權限實驗：唯讀檔案能刪（目錄可寫）、可寫檔案刪不掉（目錄唯讀）。理解刪檔需要目錄寫權限

4. 解 link count 之謎：建一個目錄看 link count = 2，加子目錄看變 3、4...。理解 = 2 + 子目錄數，以及 . 和 .. 是 hard link

## 本章重點整理

- 目錄是「檔名 → inode 號」的對應表（一種特殊檔案）；檔名存在目錄裡，不在 inode 裡（接 Ch 4）
- `.`（指向自己的 inode）和 `..`（指向父目錄）是目錄裡真實存在的 entry，kernel 自動建立
- ls 底層 = openat（開目錄）+ getdents64（讀對應表）+ statx（查每個 inode）；ls -l 慢因為每檔 stat
- 建檔/刪檔/改名改的是「目錄這張表」，需要**目錄**的寫權限，不是檔案的（唯讀檔案也能刪）
- 目錄 link count = 2 + 子目錄數（父的名字 + 自己的 . + 每個子目錄的 ..）；dentry cache 加速路徑解析

## 自我檢核

- [ ] 能解釋目錄是什麼（檔名 → inode 號的對應表），檔名存在哪
- [ ] 知道 `.` 和 `..` 是目錄裡真實的 entry，不是 shell 語法
- [ ] 能解釋為什麼刪檔需要目錄的寫權限而非檔案的
- [ ] 能解釋空目錄 link count 為什麼是 2、加子目錄為什麼 +1
- [ ] 知道 ls 底層的 getdents64，以及為什麼 ls -l 比 ls 慢

## 延伸閱讀

### 書籍

- **《The Linux Programming Interface》— Ch 18 (Directories and Links)** — Michael Kerrisk
  - **讀哪幾章**：Ch 18（目錄、getdents、.和..、link count）
  - **這本書的定位**：目錄和 link 機制的權威來源
  - **前提**：本章 + Ch 4

### 官方文件

- **[getdents(2) man page](https://man7.org/linux/man-pages/man2/getdents.2.html)** 和 **[readdir(3)](https://man7.org/linux/man-pages/man3/readdir.3.html)**
  - **讀哪裡**：getdents 的 linux_dirent 結構
  - **學什麼**：目錄 entry 的底層格式（檔名 + inode 號）
  - **前提**：本章

### 部落格 / 文章

- **[What is a directory, really?](https://jvns.ca/blog/2015/04/30/dotfiles-are-a-manifesto/)** 或 Julia Evans 關於 inode/目錄的文章
  - **這篇說什麼**：用易懂方式講目錄和 inode 的關係
  - **讀哪裡**：inode/目錄相關段落
  - **為什麼值得讀**：把本章的「目錄是對應表」講得更生活化

→ [Ch 6 hard link vs symlink](./06-links.md)
