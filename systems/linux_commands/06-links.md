# Ch 6 — hard link vs symlink

> **目標**：徹底理解兩種 link——hard link（同一個 inode 的多個檔名）和 symbolic link（指向另一個路徑的特殊檔案）的根本差異、各自的限制與用途、link count 的真正意義，以及為什麼刪除行為不同。這是 Ch 4-5（inode/目錄）的直接應用。

> **環境**：Linux，ln/ln -s。承接 Ch 4（inode）、Ch 5（目錄）。

## 為什麼有兩種 link？

Ch 4-5 建立了「檔名是指向 inode 的標籤」「目錄是檔名→inode 的對應表」。**hard link** 是這個機制的直接結果——既然檔名只是標籤，那就能有多個標籤指向同一個 inode。

但 hard link 有限制（不能跨檔案系統、不能指向目錄）。**symbolic link**（symlink）是另一種設計——它是個特殊檔案，內容是「另一個路徑字串」，更靈活但多一層間接。

理解兩者的差異（一個是 inode 層的別名，一個是路徑層的指標），你就能在「捷徑、共享檔案、節省空間」等需求下選對工具，並解釋它們不同的刪除行為。

## 先建立直覺：兩種 link 的根本不同

```
hard link：同一個 inode 的多個檔名（inode 層）
  file_a ─┐
          ├──→ inode 12345（檔案本體）
  file_b ─┘
  兩個檔名「平等」地指向同一個 inode
  刪一個，另一個還在（inode 還被引用）

symbolic link：指向「路徑」的特殊檔案（路徑層）
  link ──→ 「/path/to/file_a」（一個路徑字串）
                    │
              file_a ──→ inode 12345
  symlink 自己是個小檔案，內容是目標路徑
  刪 file_a，symlink 變「斷掉」（指向不存在的路徑）
```

核心差異：
- **hard link** 在 **inode 層**——多個檔名直接指向同一個 inode（平等的別名）
- **symlink** 在 **路徑層**——一個特殊檔案，內容是「另一個路徑」（間接的指標）

## Hard link：同一個 inode 的多個名字

```bash
cd ~/cmdlab
echo "original content" > original.txt
stat -c "inode=%i links=%h" original.txt
# inode=1234567 links=1       ← 1 個檔名指向這個 inode

# 建立 hard link
ln original.txt hardlink.txt
#  ↑ 沒有 -s，是 hard link

stat -c "inode=%i links=%h" original.txt
stat -c "inode=%i links=%h" hardlink.txt
# inode=1234567 links=2       ← 兩個檔名，同一個 inode！
# inode=1234567 links=2       ← 同樣的 inode 號，link count = 2
```

```
hard link 的本質（用 Ch 5 的目錄表理解）：
  ln original.txt hardlink.txt 做的事：
    在目錄表加一條：hardlink.txt → inode 1234567
    （和 original.txt 指向同一個 inode）
    inode 的 link count +1
        │
  → 兩個檔名平等，沒有「誰是本尊」
  → 改任一個的內容，兩個都變（同一個 inode 的資料）
  → 刪一個，inode 的 link count -1，inode 還在（另一個指著）
```

```bash
# 驗證：改一個，另一個跟著變（同一個 inode）
echo "modified" >> hardlink.txt
cat original.txt
# original content
# modified                   ← original.txt 也變了（同一個 inode）

# 刪一個，另一個還在
rm original.txt
stat -c "links=%h" hardlink.txt
# links=1                    ← link count 從 2 變 1，inode 還在
cat hardlink.txt             # 內容完整（inode 沒被釋放）
```

> hard link 揭示了 Ch 4 的「link count」的真正意義：**有幾個檔名指向這個 inode**。`rm` 不是「刪除檔案」，是「移除一個檔名 + inode link count -1」。只有 link count 降到 0（且無 process 開著，Ch 19），inode 才真正釋放。這就是為什麼「刪了 original.txt，hardlink.txt 還在」——inode 還有一個檔名指著，沒被釋放。`rm` 的本質是 `unlink`（移除一個連結），不是 delete。

## Symbolic link：指向路徑的特殊檔案

```bash
cd ~/cmdlab
echo "target content" > target.txt
ln -s target.txt symlink.txt
#  ↑ -s 是 symbolic link

stat -c "inode=%i type=%F" target.txt symlink.txt
# inode=1111 type=regular file       ← target 是一般檔案
# inode=2222 type=symbolic link      ← symlink 是「不同的 inode」，類型是 symlink！

# symlink 的「內容」就是目標路徑
readlink symlink.txt
# target.txt                         ← symlink 的內容 = 它指向的路徑

ls -l symlink.txt
# lrwxrwxrwx ... symlink.txt -> target.txt
# ↑ l 表示 symlink            ↑ 指向哪
```

```
symlink 的本質：
  symlink 是「自己的 inode」（和目標不同！）
  它的「資料」就是一個路徑字串（"target.txt"）
        │
  存取 symlink 時：
    kernel 讀到它是 symlink → 讀它的內容（目標路徑）
    → 再去解析那個路徑（多一層間接）
        │
  → symlink 是「路徑層」的指標
  → 刪目標，symlink 變「斷鏈」（指向不存在的路徑）
```

```bash
# 刪目標，symlink 斷鏈
rm target.txt
cat symlink.txt
# cat: symlink.txt: No such file or directory
#   ↑ symlink 還在，但它指向的 target.txt 沒了 → 斷鏈
ls -l symlink.txt
# lrwxrwxrwx ... symlink.txt -> target.txt   ← symlink 本身還在，只是指向空
readlink symlink.txt
# target.txt                                 ← 還是指向 target.txt（已不存在）
```

## Hard link vs symlink 對照

| 面向 | hard link | symbolic link |
|---|---|---|
| 本質 | 同一 inode 的多個檔名 | 指向路徑的特殊檔案 |
| inode | 和目標同一個 inode | 自己獨立的 inode |
| 跨檔案系統 | ✗ 不能（inode 號只在同一檔案系統有效）| ✓ 能（存的是路徑字串）|
| 指向目錄 | ✗ 不能（避免循環，除了 . ..）| ✓ 能 |
| 刪目標後 | 還在（link count > 0，inode 還在）| 斷鏈（指向不存在的路徑）|
| 看目標 | 看不出（平等的檔名）| `readlink` / `ls -l` 看得到 |
| 大小 | = 檔案大小（同一 inode）| = 目標路徑字串的長度 |

## 為什麼 hard link 的限制

```
hard link 不能跨檔案系統：
  hard link = 同一個 inode 的多個檔名
  但 inode 號只在「同一個檔案系統」內有效
  （不同檔案系統的 inode 號各自獨立）
  → 不能讓 A 檔案系統的檔名指向 B 檔案系統的 inode

hard link 不能指向目錄（一般情況）：
  如果目錄能 hard link，可能造成檔案系統「循環」
  （A 指向 B，B 又指向 A 的某個祖先...）
  → 破壞「目錄樹」的樹狀結構，工具（find 等）會無限循環
  → 所以禁止（. 和 .. 是 kernel 管理的特例，Ch 5）
```

> hard link 的限制源自它的本質（inode 層）。inode 號只在單一檔案系統有效，所以 hard link 不能跨檔案系統。目錄 hard link 會破壞樹狀結構造成循環，所以禁止（`.` 和 `..` 是 kernel 維護的特例）。symlink 沒這些限制，因為它存的是「路徑字串」（跨檔案系統、指向目錄都行）——但代價是多一層間接（要解析路徑）且可能斷鏈。這是「直接但受限」vs「靈活但間接」的經典取捨。

## 什麼時候用哪個

```
用 hard link：
  - 同一檔案系統內，要多個名字指向同一檔案
  - 要「刪一個名字不影響檔案」（備份/快照場景）
  - 例：rsnapshot 用 hard link 做增量備份（沒變的檔案共享 inode，省空間）

用 symlink：
  - 跨檔案系統（hard link 不行）
  - 指向目錄（hard link 不行）
  - 要「捷徑」語意（明顯看得出指向哪）
  - 例：/usr/bin/python → python3.11（版本切換）
  - 例：設定檔的 dotfiles 管理（~/.bashrc → ~/dotfiles/bashrc）
        │
  實務上 symlink 用得多（更靈活、語意清楚）
  hard link 用於特定場景（節省空間的備份、確保檔案不被誤刪）
```

## 故意弄壞：symlink 的相對路徑陷阱

```bash
cd ~/cmdlab
mkdir -p a/b
echo "data" > a/target.txt

# 在 a/b 裡建一個 symlink 指向 ../target.txt（相對路徑）
cd a/b
ln -s ../target.txt mylink
cat mylink              # data（OK，相對路徑從 symlink 所在位置算）

# 把 symlink 移到別的地方，相對路徑就斷了
cd ~/cmdlab
mv a/b/mylink .         # 移到 cmdlab 根
cat mylink              # No such file or directory
#   ↑ mylink 指向 ../target.txt，但現在 mylink 在 cmdlab
#     ../target.txt 從 cmdlab 算 = ~/target.txt（不存在）
readlink mylink         # ../target.txt（還是相對路徑，但基準變了）
```

symlink 的相對路徑陷阱：相對路徑的 symlink，目標是「相對於 symlink 所在的目錄」算的。移動 symlink，相對路徑的基準變了，可能斷鏈。所以「會被移動的 symlink 用絕對路徑」，「跟著一起移動的（如整個目錄）用相對路徑」。這是 symlink 比 hard link 多的一個坑（hard link 沒有路徑概念，不會斷）。

## 踩雷集錦

1. **以為 hard link 是「複製」**：hard link 不複製內容，是同一個 inode 的另一個名字。改一個兩個都變（同一份資料）。複製是 `cp`（不同 inode）

2. **以為 symlink 和 hard link 差不多**：根本不同。hard link 是 inode 層別名（平等），symlink 是路徑層指標（有間接、會斷鏈）。`ls -l` 看 symlink 有 `->`，hard link 看不出

3. **hard link 跨檔案系統失敗**：`ln file /mnt/other_fs/link` 報 "Invalid cross-device link"。hard link 不能跨檔案系統，用 symlink

4. **symlink 相對路徑移動後斷鏈**：相對路徑 symlink 的目標相對於它所在目錄。移動它，基準變，可能斷。會移動的用絕對路徑

5. **以為 rm 是「刪除檔案」**：rm 是 unlink（移除一個檔名 + link count -1）。link count > 0 時檔案還在（另一個 hard link 指著）。rm 刪的是連結，不是檔案本體

## 進階：link count、unlink、與 inode 釋放的完整生命週期

把 Ch 4、5、6 串起來，inode 的完整生命週期：

```
inode 的生命週期（完整）：
  建立檔案：
    分配一個 inode + 在目錄加 entry（檔名 → inode）
    link count = 1
        │
  ln 建 hard link：
    目錄加 entry，link count +1
        │
  rm（unlink）一個檔名：
    目錄移除 entry，link count -1
        │
  inode 真正釋放的條件（兩個都要滿足）：
    1. link count = 0（沒有檔名指向它）
    AND
    2. 沒有 process 開著它（沒有 open 的 fd，Ch 19）
        │
  → 兩個條件都滿足，kernel 才釋放 inode 和它的資料 block
    （這就是 Ch 4「rm 後空間沒釋放」的完整解釋）
```

```bash
# 驗證完整生命週期
echo "data" > f.txt          # link count = 1
ln f.txt f2.txt              # link count = 2
exec 3< f.txt                # 開一個 fd 引用它（Ch 19）
rm f.txt f2.txt              # 移除兩個檔名，link count = 0
# 但 inode 還沒釋放！因為 fd 3 還開著
cat /proc/self/fd/3          # 還能讀到內容（inode 沒釋放）
# data
exec 3<&-                    # 關閉 fd 3
# 現在 link count = 0 且無 process 開著 → inode 釋放
```

> 這個完整生命週期統一了 Ch 4、5、6 的所有概念：inode（本體）、目錄 entry（檔名）、link count（檔名數）、fd 引用（Ch 19）。inode 釋放需要 link count = 0 **且** 無 fd 開著。這解釋了 Ch 4 的「rm 後空間沒釋放」（fd 還開著）、本章的「hard link 刪一個還在」（link count > 0）。這是 Unix 檔案系統最精妙的設計——用引用計數（link count + fd）管理 inode 生命週期。`/proc/self/fd/3` 能讀到「已被 rm 但 fd 還開著」的檔案，是這個機制的直接展現。

## 動手練習

1. 玩 hard link：建檔、ln 建 hard link、`stat` 看兩個檔名同一個 inode、link count = 2。改一個內容看另一個變。刪一個看另一個還在、link count 變 1

2. 玩 symlink：`ln -s` 建 symlink、`ls -l` 看 `->`、`readlink` 看目標。刪目標看 symlink 斷鏈（但 symlink 本身還在）

3. 試限制：hard link 跨檔案系統（`ln ~/file /tmp/link` 如果 /tmp 是不同檔案系統會失敗）、hard link 指向目錄（失敗）。symlink 都能

4. 跑「故意弄壞」：相對路徑 symlink 移動後斷鏈。以及完整生命週期（hard link + fd 開著，rm 後 inode 不釋放，關 fd 才釋放）

## 本章重點整理

- hard link：同一個 inode 的多個平等檔名（inode 層）；symlink：指向路徑的特殊檔案（路徑層，有間接）
- hard link 限制：不能跨檔案系統（inode 號只在單一檔案系統有效）、不能指向目錄（避免循環）
- symlink 靈活（跨檔案系統、指向目錄）但會斷鏈、相對路徑移動後可能失效
- link count = 指向 inode 的檔名數；rm 是 unlink（移除檔名 + count -1），不是刪檔案本體
- inode 釋放條件：link count = 0 **且** 無 process 開著（fd）——統一了 Ch 4/5/6 的概念

## 自我檢核

- [ ] 能解釋 hard link 和 symlink 的根本差異（inode 層別名 vs 路徑層指標）
- [ ] 知道 hard link 的兩個限制（跨檔案系統、指向目錄）及其原因
- [ ] 能解釋 link count 的意義，以及 rm 為什麼是 unlink 不是 delete
- [ ] 知道 symlink 相對路徑的陷阱
- [ ] 能說出 inode 真正釋放的兩個條件（link count 0 + 無 fd 開著）

## 延伸閱讀

### 書籍

- **《The Linux Programming Interface》— Ch 18 (Directories and Links)** — Michael Kerrisk
  - **讀哪幾章**：Ch 18 的 hard link、symbolic link、link count 那幾節
  - **這本書的定位**：link 機制的權威來源，本章的深度補充
  - **前提**：本章 + Ch 4-5

### 官方文件

- **[link(2)](https://man7.org/linux/man-pages/man2/link.2.html)**, **[symlink(2)](https://man7.org/linux/man-pages/man2/symlink.2.html)**, **[unlink(2)](https://man7.org/linux/man-pages/man2/unlink.2.html)** man pages
  - **讀哪裡**：三個 syscall 的 DESCRIPTION 和 ERRORS
  - **學什麼**：link/symlink/unlink 的精確語意；ln/rm 命令底層就是這些
  - **前提**：本章

### 部落格 / 文章

- **[Hard links and symbolic links explained](https://jvns.ca/blog/2015/04/13/some-strace-tips/)** 或 Julia Evans 的 link 相關文章
  - **這篇說什麼**：用實例講兩種 link 的差異
  - **讀哪裡**：link 相關段落
  - **為什麼值得讀**：把本章的概念用更多實例鞏固

→ [Ch 7 權限位元與 ownership](./07-permissions.md)
