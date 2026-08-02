# Ch 32 — dbx / SBAT 撤銷與軍備競賽

> **目標**：把 Secure Boot 的撤銷機制從設計原理打通到實際部署的困境。為什麼 hash 撤銷會爆炸、SBAT 如何用 generation 解決這個問題、廠商怎麼推送更新、撤銷本身如何造成 brick——以及 BlackLotus 之後這場軍備競賽的現實。

---

## 撤銷的核心問題

Secure Boot 的信任模型假設：db 中的 key/hash 代表「目前可信任的 binary」。但這個前提有個漏洞：**曾經合法的 binary 可能之後被發現有漏洞**。

要讓 Secure Boot 真正有效，你不只需要「驗章」，還需要「能快速失效曾經信任但現在有洞的 binary」——這就是撤銷（revocation）。

Secure Boot 提供兩種撤銷機制：
1. **dbx**（Forbidden Signature Database）：明確列出不可信任的 hash 或 key
2. **SBAT**（Secure Boot Advanced Targeting）：generation-based 的更細粒度撤銷

兩者分別解決不同的問題，也各有代價。

---

## dbx：Hash 撤銷與 Certificate 撤銷

### dbx 是什麼

dbx 是 UEFI Secure Boot 的「黑名單」，格式與 db 相同（EFI_SIGNATURE_DATABASE），可以存放：
1. **Hash（SHA-256）**：特定 binary 的 hash，精確撤銷某一個特定版本
2. **Certificate**：整個 CA 或 signing certificate，一次撤銷該 CA 簽的所有 binary

Secure Boot 在驗章時，同時查 db（白名單）和 dbx（黑名單）。如果 binary 的 hash 或簽章 CA 出現在 dbx，即使它在 db 中也被拒絕。

### Hash 撤銷的代價

Hash 撤銷是點對點的：一個 hash 只能撤銷一個特定的 binary。

這在撤銷小數量 binary 時有效，但 BootHole 事件暴露了它的極限：

```
BootHole 影響的 GRUB2 binary 清單（估計）：
  Ubuntu 20.04 x86_64     grubx64.efi hash: aa11bb...
  Ubuntu 20.04 i386       grubx64.efi hash: cc22dd...
  Ubuntu 18.04 x86_64     grubx64.efi hash: ee33ff...
  Fedora 33 x86_64        grubx64.efi hash: 001122...
  Fedora 32 x86_64        grubx64.efi hash: 334455...
  CentOS 7                grubx64.efi hash: 667788...
  Debian 10 amd64         grubx64.efi hash: 99aabb...
  Debian 9 amd64          grubx64.efi hash: ccddee...
  openSUSE Leap 15.2      grubx64.efi hash: ff0011...
  ... （估計數百個不同的 binary）

每個發行版、每個版本、每個 CPU 架構 → 不同的 hash
要用 dbx 撤銷全部 → dbx 需要儲存數百個 hash
```

dbx 存在 UEFI NVRAM，NVRAM 的空間有限（通常幾 KB 到幾十 KB）。如果每個有洞的 GRUB2 binary 都要加一個 hash，dbx 很快就會爆滿——這就是「dbx 爆炸」問題。

### Certificate 撤銷的代價

如果用 Certificate 撤銷（撤銷 Canonical 的簽章 key）：

```
撤銷 Canonical 的 GRUB2 signing key
→ 所有 Ubuntu 版本的 GRUB2 全部失效
→ 包括修了洞的新版本也失效
→ 使用者必須更換到用新 key 簽的 binary 才能繼續使用

問題：
  ├── 新 key 必須加入 db（需要 Microsoft 重新簽章）
  ├── 所有舊的 Ubuntu 安裝介質失效
  └── 雙開機機器如果還有舊 key 的 GRUB2 → 無法開機
```

Certificate 撤銷是核武器：殺傷力太大，無法在實際場景中使用。

### dbx 的現實限制總表

| 問題 | 原因 | 後果 |
|------|------|------|
| Hash 爆炸 | N 個有洞 binary 需要 N 個 hash | NVRAM 空間耗盡，無法繼續撤銷 |
| Certificate 核武 | 撤銷 key 會讓所有該 key 簽的 binary 失效 | 包含修了洞的新版本也一起失效 |
| 撤銷有 brick 風險 | 舊版安裝介質、雙開機舊版 OS 失效 | 使用者系統無法開機 |
| 時間延遲 | dbx 更新需要 Microsoft 推送、廠商更新韌體 | 漏洞公開到撤銷中間存在攻擊窗口 |
| 沒有版本粒度 | hash 只能區分特定 binary，無法說「版本 < X 的都不行」 | 不可能「撤銷所有低於某版本的 GRUB2」 |

BootHole 事件後，dbx 的問題從理論變成了現實。解法是 SBAT。

---

## SBAT：Generation-Based 撤銷

### 設計動機

SBAT（Secure Boot Advanced Targeting）是由 Microsoft、Canonical、Red Hat 等在 2020-2021 年共同設計，作為 BootHole 事件的根本解法。核心想法：

**不要撤銷特定 binary 的 hash，而是撤銷特定「generation」之前的所有版本。**

就像 AVB 的 rollback_index（Ch 19），但設計給 Secure Boot 的 shim/GRUB 生態。

### SBAT metadata 格式

每個 SBAT 兼容的 EFI binary（shim, GRUB2, kernel 等）在 `.sbat` PE section 中包含一個 CSV 格式的元數據：

```csv
# .sbat section 格式（每行一個元件）
# component, generation, vendor, package, version, url

sbat,1,SBAT Version,sbat,1,https://github.com/rhboot/shim/blob/main/SBAT.md
grub,3,Free Software Foundation,grub2,2.06-3,https://www.gnu.org/software/grub/
grub.ubuntu,1,Canonical,grub2,2.06-2ubuntu7,https://packages.ubuntu.com/
```

欄位說明：
- `component`：元件名稱（`grub`、`shim`、`kernel` 等）
- `generation`：這個版本的 generation 號碼（整數，只增不減）
- `vendor`：提供這個 binary 的廠商名稱（顯示用）
- `package`：套件名稱
- `version`：版本字串（顯示用，非用於撤銷判斷）
- `url`：說明文件 URL

### SBAT 撤銷如何運作

撤銷資訊存在 UEFI NVRAM 的 `SbatLevel` variable：

```
# SbatLevel variable 格式（CSV）
sbat,1,2022030000
grub,2,2020030001
```

解讀：
- `grub,2,...`：grub 元件的 generation < 2 的 binary 全部被拒絕
- `sbat,1,...`：sbat 機制本身的 generation（用於版本相容性）

**驗章流程加上 SBAT 後**：

```
shim 驗章流程（加入 SBAT 後）：

1. 讀取待載入 binary（e.g., grubx64.efi）的 .sbat section
2. 解析 CSV，取得各元件的 generation
3. 讀取 NVRAM 的 SbatLevel variable
4. 比較：binary 的 generation >= SbatLevel 要求的 generation？
   ├── 是 → 通過，繼續
   └── 否 → 拒絕，回傳 SECURITY_VIOLATION
5. 如果通過 SBAT 檢查，再做 Secure Boot db 驗章
```

### Generation 遞增邏輯

當發現一個影響某版本 GRUB2 的漏洞時：

```
事件流程：

1. 漏洞發現：GRUB2 2.06 以下的版本有 CVE-XXXX-YYYY
2. 修補版本：GRUB2 2.07，在 .sbat 中把 grub generation 從 2 改為 3
   （所有有洞版本的 generation <= 2，新版本 generation = 3）
3. 推送 SbatLevel 更新：把 SbatLevel 中 grub 的最低要求設為 3
   （SbatLevel 更新非常小：只有一行 CSV，不是 N 個 hash）
4. 效果：
   ├── GRUB 2.06 及以下（generation=2）→ SBAT 拒絕
   └── GRUB 2.07 及以上（generation=3）→ SBAT 通過
```

**關鍵優勢**：SbatLevel 的更新只需要一個小 CSV 更改，而不是列出所有有洞版本的 hash。

```
dbx 方法（解決 BootHole）：
  需要加入 ~300 個 SHA-256 hash
  NVRAM 空間：~9600 bytes
  dbx 原始可用空間：通常 8-32 KB
  → 很快就爆滿

SBAT 方法（解決 BootHole）：
  更新 SbatLevel：把 grub 的 generation 要求從 1 改為 2
  SbatLevel 變化：一行 CSV 改一個數字
  NVRAM 空間：< 100 bytes
  → 幾乎不占空間
```

### 廠商視角的 SBAT 操作

```bash
# 以 Ubuntu 為例：
# 1. 修補 GRUB2，更新 .sbat section 中的 generation
# ubuntu 的 grub2-unsigned package 在 debian/sbat.csv 中更新：
cat debian/sbat.csv
# sbat,1,SBAT Version,sbat,1,...
# grub,3,Free Software Foundation,grub2,2.06-3,...
# grub.ubuntu,2,Canonical,grub2,2.06-2ubuntu8,...  ← generation 從 1 改為 2

# 2. 重新 build + 簽章 grubx64.efi

# 3. 向 Microsoft 請求更新 SbatLevel（或透過 Windows Update 推送）

# 4. 使用者系統在下次 Windows Update 後，SbatLevel variable 被更新
# 之後舊版 grubx64.efi（generation=1）開機就被擋
```

---

## Windows / fwupd / LVFS 的更新派送

### Windows Update 的 dbx 更新

Microsoft 透過 Windows Update 推送 dbx 更新，以 Windows Driver 的形式發布：

```
dbx 更新包（.bin 格式）：
  包含新的 dbx 內容，用 PK 的 key 簽章
  Windows Update 把它傳給 Windows 的 EFI 更新服務
  EFI 更新服務呼叫 gRT->SetVariable("dbx", ...)
  UEFI firmware 驗章後更新 NVRAM 中的 dbx
```

只有 Windows 啟動時才能推送（需要 Windows 在線）。純 Linux 機器依賴 fwupd。

### fwupd / LVFS 的角色

[LVFS（Linux Vendor Firmware Service）](https://fwupd.org) 是 Linux 生態的韌體更新平台，fwupd 是對應的工具。dbx 更新也透過 LVFS/fwupd 推送：

```bash
# 在 Linux 上更新 dbx（需要 UEFI 支援且 secure boot 啟用）：
sudo fwupdmgr refresh
sudo fwupdmgr get-updates    # 查看是否有 dbx 更新
sudo fwupdmgr update         # 應用更新

# 查看目前 dbx 狀態：
sudo dbxtool list            # 列出目前 dbx 中的所有 hash/cert
sudo dbxtool check           # 檢查是否有需要更新的項目
```

### SbatLevel 更新的派送

SbatLevel 是一個 UEFI variable，更新方式與 dbx 類似，也透過 Windows Update 或 fwupd 推送。技術上，任何能寫 UEFI variable 的程式（有適當的 EFI_VARIABLE_AUTHENTICATED_WRITE_ACCESS 屬性）都可以更新它。

shim 本身在開機時也會讀取並強制 SbatLevel 的最低值——如果新版 shim 帶有更嚴格的 SbatLevel 要求，它會在第一次開機時自動把 NVRAM 的 SbatLevel 更新到新值（只能增加，不能降低）。

---

## 撤銷造成 brick 的風險

### 誰會被 brick？

更新 dbx 或 SbatLevel 會讓舊版的合法 binary 被拒絕。以下幾類使用者有被 brick 的風險：

```
風險類別 1：使用舊安裝媒體的使用者
  場景：用幾年前燒的 Ubuntu 20.04 USB 重裝系統
  問題：USB 上的 grubx64.efi generation=1，SbatLevel 要求 generation >= 2
  結果：從 USB 開機時，shim 拒絕載入 grub → 無法開機

風險類別 2：雙開機使用者
  場景：Windows + Ubuntu 雙開機，Ubuntu 版本老舊
  問題：Windows 更新 SbatLevel 後，Ubuntu 的舊版 grub 被擋
  結果：選 Ubuntu 的開機選項時，Secure Boot 阻擋 → Ubuntu 無法開機

風險類別 3：Recovery 媒體
  場景：IT 部門用舊版 WinPE rescue disk 救援系統
  問題：舊版 bootmgfw.efi 被 dbx 撤銷
  結果：WinPE 無法開機

風險類別 4：虛擬機器快照
  場景：雲端平台的 VM 映像包含舊版 bootloader
  問題：Host 更新 OVMF 的 dbx/SbatLevel 後，舊 VM 映像開機失敗
  結果：VM 無法啟動
```

### Microsoft 的「安全部署」策略

BlackLotus 之後，Microsoft 設計了一個三階段的 dbx 更新策略，以降低 brick 風險：

```
階段 1（僅稽核模式）：
  更新 dbx，但加上「only audit, don't block」標記
  受影響的 binary 仍然可以開機，但 Windows Event Log 記錄警告
  目的：讓使用者（和 IT）了解哪些 binary 會被影響

階段 2（封鎖模式，但有例外）：
  真正把有洞版本加入 dbx 並封鎖
  但保留一個「opt-out」機制：管理員可以暫時停用撤銷
  讓企業有時間更新所有系統

階段 3（全面強制）：
  移除 opt-out 機制
  所有有洞的舊版 binary 被完全封鎖
```

BlackLotus 的 dbx 更新（2023 年 5-7 月）就是這樣分批推出的，從公告到完全強制封鎖超過六個月。

### Brick 發生後的恢復

如果使用者真的 brick 了（Secure Boot 擋掉了所有能開機的 binary）：

```
選項 1：進入 UEFI Setup，暫時停用 Secure Boot
  → 使用不驗章模式開機
  → 更新到支援新 SBAT generation 的 bootloader
  → 重新啟用 Secure Boot

選項 2：從 UEFI Setup 清除 dbx（回到出廠 dbx）
  → 失去撤銷保護（有安全風險）
  → 更新後再重新應用 dbx

選項 3（最徹底）：從 UEFI Setup 恢復出廠 Secure Boot 設定
  → 清除所有自訂 key，回到 OEM 預設 db/dbx
  → 重灌 OS 後重新配置
```

---

## 軍備競賽：BlackLotus 之後

### 時間軸

```
2022-01：CVE-2022-21894 的修補版本 Windows 推出
2022-01：漏洞細節未公開，dbx 未更新（有洞舊版仍在 db 受信任）

2023-03：ESET 公開 BlackLotus 分析
         有洞的 bootmgfw.efi 的具體版本和 hash 已知
         攻擊者可以精確取得觸發漏洞的版本

2023-04：Microsoft 開始「稽核模式」dbx 更新
         還沒有真正封鎖

2023-05：Microsoft 發布 KB5025885：
         第一批封鎖有洞版本的 dbx 更新
         影響少數特定版本

2023-07：Microsoft 發布 KB5028185：
         更大範圍的 dbx 更新
         但雙開機機器 brick 問題出現（Ubuntu 在某些設定下失效）

2023-08：部分 dbx 更新被暫時撤回（因為 brick 報告）
         Microsoft 重新評估部署策略

2024-01 起：更謹慎的分批 dbx 更新繼續推進
            SBAT 機制加強，shim 更新中加入更嚴格的 SbatLevel
```

### 攻擊者的繞過角度

dbx 更新後，BlackLotus 還能用嗎？

```
dbx 更新前：
  舊版 bootmgfw.efi → db 通過 → CVE-2022-21894 可用

dbx 更新後（2023-05 的 KB5025885）：
  舊版 bootmgfw.efi → dbx 中存在 → 被拒絕

攻擊者的應對：
  選項 A：找 CVE-2022-21894 以外的 Secure Boot bypass
           （需要新的 research，有攻擊者已開始這個工作）
  選項 B：在 dbx 更新前的時間窗內攻擊
           （從漏洞公開到 dbx 更新之間的幾個月）
  選項 C：攻擊 dbx 更新本身
           （讓 dbx 更新失敗，或讓機器保持舊 dbx）
  選項 D：尋找 dbx 更新未覆蓋的「另一個版本」的有洞 binary
           （Windows 有很多版本，不一定所有版本的 hash 都進了 dbx）
```

選項 D 是現實威脅：Microsoft 的 dbx 更新每次只覆蓋已知影響版本的 hash，如果有某個中間版本被遺漏，它仍然可以被降級攻擊。

### SBAT 在軍備競賽中的角色

SBAT 對 grub 的撤銷問題比 dbx 更有效，但 Windows 生態（bootmgfw.efi）沒有用 SBAT——SBAT 主要是 Linux 生態（shim/grub/kernel）的解法。

```
保護的層面：
  SBAT 解決了：「Linux 的 GRUB2 需要大量 dbx hash」的問題
  dbx 仍然解決：「Windows bootmgfw.efi 的版本撤銷」問題
  → 兩個機制並存，各管不同的生態
```

---

## SBAT metadata 範例與操作

### 查看 binary 的 SBAT section

```bash
# 在 Linux 上，用 objdump 看 .sbat section：
objdump -s -j .sbat /boot/efi/EFI/ubuntu/grubx64.efi | head -40

# 或用 python-efitools：
sbatool show /boot/efi/EFI/ubuntu/grubx64.efi

# 預期輸出類似：
# sbat,1,SBAT Version,sbat,1,https://github.com/rhboot/shim/blob/main/SBAT.md
# grub,3,Free Software Foundation,grub2,2.06-3,https://www.gnu.org/software/grub/
# grub.ubuntu,2,Canonical,grub2,2.06-2ubuntu8,https://packages.ubuntu.com/
```

### 查看目前的 SbatLevel

```bash
# 讀取 SbatLevel NVRAM variable：
sudo efivar --name 605dab50-e046-4300-abb6-3dd810dd8b23-SbatLevel

# 或用更友善的格式：
cat /sys/firmware/efi/efivars/SbatLevel-605dab50-e046-4300-abb6-3dd810dd8b23 | \
    hexdump -C | head

# 解析後應類似：
# sbat,1,2022030000
# grub,2,2022030000
# （generation 要求：grub binary 的 generation 必須 >= 2）
```

### 在 OVMF 環境示範 SBAT

```bash
# WSL QEMU 環境中：
# 1. 建立一個帶 .sbat section 的測試 EFI（用 objcopy 注入）

# 建立 sbat.csv：
cat > /tmp/sbat.csv << 'EOF'
sbat,1,SBAT Version,sbat,1,https://example.com
testapp,1,Test Vendor,testapp,1.0,https://example.com
EOF

# 把 .sbat section 加入一個 EFI binary：
objcopy --add-section .sbat=/tmp/sbat.csv \
        --set-section-flags .sbat=readonly,data \
        test.efi test_with_sbat.efi

# 2. 在 OVMF（支援 SBAT 的版本）中：
# 設定 SbatLevel 要求 testapp generation >= 2：
efivar --name ... --write "sbat,1,2022...\ntestapp,2,2022..."

# 3. 嘗試從 UEFI Shell 執行 test_with_sbat.efi（generation=1）
# 預期：SECURITY_VIOLATION，binary 被拒絕

# 4. 建立 generation=2 的版本：
cat > /tmp/sbat_v2.csv << 'EOF'
sbat,1,SBAT Version,sbat,1,https://example.com
testapp,2,Test Vendor,testapp,2.0,https://example.com
EOF
# 重複 objcopy，這次 test_with_sbat_v2.efi 應該通過 SBAT 檢查
```

---

## dbx vs SBAT 總結對比

```
┌─────────────────┬──────────────────────┬──────────────────────┐
│ 屬性            │ dbx                  │ SBAT                 │
├─────────────────┼──────────────────────┼──────────────────────┤
│ 撤銷單位        │ 單一 binary 的 hash  │ 元件的 generation    │
│                 │ 或整個 CA cert       │ 小於 N 的所有版本    │
├─────────────────┼──────────────────────┼──────────────────────┤
│ 更新大小        │ 每個 binary 加一個   │ 一行 CSV 改一個數字  │
│                 │ SHA-256 hash（32B）  │ （< 100 bytes）      │
├─────────────────┼──────────────────────┼──────────────────────┤
│ NVRAM 壓力      │ 高（N 個 binary →   │ 低（O(1) 大小增長）  │
│                 │  N 個 hash）         │                      │
├─────────────────┼──────────────────────┼──────────────────────┤
│ 粒度            │ 精確到單一 binary    │ 元件層級（整個元件   │
│                 │                      │ 的所有舊 generation）│
├─────────────────┼──────────────────────┼──────────────────────┤
│ 生態支援        │ 所有 Secure Boot     │ 需要 shim 和 binary  │
│                 │ 系統                 │ 都加入 .sbat section │
├─────────────────┼──────────────────────┼──────────────────────┤
│ 主要使用場景    │ Windows boot loader  │ Linux shim/grub/     │
│                 │ 撤銷；CA 撤銷        │ kernel               │
├─────────────────┼──────────────────────┼──────────────────────┤
│ Brick 風險      │ 較高（影響所有含     │ 較低（但舊安裝媒體   │
│                 │ 該 hash 的系統）     │ 仍然有風險）         │
└─────────────────┴──────────────────────┴──────────────────────┘
```

---

## 踩雷

1. **以為 SBAT 完全取代 dbx**：SBAT 和 dbx 解決不同問題，必須並存。dbx 處理 certificate 撤銷和 Windows 生態的版本撤銷；SBAT 處理 Linux shim/grub 生態的版本撤銷。

2. **低估 SbatLevel 的「只增不減」特性**：SbatLevel 被設計成 ratchet（棘輪）——只能增加，不能降低。一旦你的系統設定了更高的 SbatLevel，舊版 shim/grub 就永久失效（除非手動清 NVRAM）。攻擊者可以利用這點做「惡意提高 SbatLevel DoS」——把 SbatLevel 設到非常高，讓所有現有 binary 都失效。

3. **不理解 generation 的語意**：generation 不是 binary 的版本號，而是「安全 generation」——一個 generation 可能跨越多個版本。Ubuntu 的 grub2.06-2ubuntu7 和 grub2.06-2ubuntu8 可能都是 generation=2，只是後者修了一個功能性 bug 而非安全漏洞。

4. **忽略 shim 自身的 SBAT enforcement**：shim 在開機時不只讀取 SbatLevel，也**寫入** SbatLevel（如果它帶有更新的最低要求）。這意味著更新 shim 可能自動提高 SbatLevel，讓舊版 grub 在下次開機就被擋。IT 環境中更新 shim 前要先評估影響。

5. **以為 dbx 更新後 BlackLotus 完全無效**：dbx 更新只封鎖了已知的有洞版本。攻擊者可以找未被列入 dbx 的中間版本，或轉向其他 CVE。撤銷是必要但不充分的緩解措施。

6. **在雙開機機器上忽略 SbatLevel 同步問題**：Windows 更新 SbatLevel 後，Linux 的 grub 版本如果 generation 不夠，下次開機就被擋。這是「Windows Update brick Ubuntu」問題的根源之一。雙開機使用者在套用 Windows Security Update 前要確認 Ubuntu 的 grub 版本足夠新。

---

## 進階延伸

- **UEFI variable 的認證機制**：dbx 和 SbatLevel 都是「authenticated variable」，寫入需要有 PK/KEK 簽章的 AuthVar 結構。攻擊者如果能寫 dbx（例如從 SMM exploit 得到 variable write 能力），就能清除撤銷保護。這個設計有意義：dbx/SbatLevel 的修改需要授權，防止 OS 層惡意程式降低安全性。

- **Shim 的 SBAT 強制邏輯**：Ubuntu 的 shim 原始碼在 `github.com/rhboot/shim`，`sbat.c` 中有完整的 SBAT 驗證邏輯。閱讀這段程式碼，理解 `check_sbat()` 如何比較 binary 的 .sbat CSV 和 NVRAM 的 SbatLevel，是理解 SBAT 工作原理的最佳方式。

- **PKfail：UEFI Platform Key 大規模洩漏**：2024 年 Binarly 發現多家廠商使用了相同的 test PK（Platform Key），且這個 PK 的私鑰在公開的 edk2 測試庫中可以找到。PK 是整個 Secure Boot 信任鏈的根，PK 私鑰洩漏意味著攻擊者可以任意修改 db/dbx/SbatLevel——比 BlackLotus 更根本的威脅。這是 T6（公開金鑰）在 UEFI 的最大案例。

---

## 動手練習

### 練習：在 OVMF 環境測試 SBAT 撤銷

```bash
# WSL 環境，需要支援 SBAT 的 shim（Ubuntu 22.04 的 shim 1.15 以上）
# 目標：親眼看到 SBAT generation 不足的 binary 被擋

# 步驟 1：取得 OVMF with secboot 和 snakeoil key（見 Ch 00）
# 步驟 2：建立兩個版本的測試 EFI（generation=1 和 generation=2）

# 建立 sbat_v1.csv（generation=1，舊版）
cat > /tmp/sbat_v1.csv << 'EOF'
sbat,1,SBAT Version,sbat,1,https://github.com/rhboot/shim/blob/main/SBAT.md
testpkg,1,Test,testpkg,1.0,https://example.com
EOF

# 建立 sbat_v2.csv（generation=2，新版）
cat > /tmp/sbat_v2.csv << 'EOF'
sbat,1,SBAT Version,sbat,1,https://github.com/rhboot/shim/blob/main/SBAT.md
testpkg,2,Test,testpkg,2.0,https://example.com
EOF

# 步驟 3：把 .sbat section 注入一個簡單的 EFI hello world binary
# （假設已有 hello.efi，從 edk2 sample 或 gnu-efi 編譯）
objcopy --add-section .sbat=/tmp/sbat_v1.csv --set-section-flags .sbat=readonly,data \
    hello.efi hello_v1.efi
objcopy --add-section .sbat=/tmp/sbat_v2.csv --set-section-flags .sbat=readonly,data \
    hello.efi hello_v2.efi

# 步驟 4：用 snakeoil key 簽章兩個版本
sbsign --key snakeoil.key --cert snakeoil.crt hello_v1.efi --output hello_v1_signed.efi
sbsign --key snakeoil.key --cert snakeoil.crt hello_v2.efi --output hello_v2_signed.efi

# 步驟 5：設定 SbatLevel（要求 testpkg generation >= 2）
# 這需要在 UEFI 環境中操作（用 UEFI Shell 的 efivar 工具，或 shim 的 MokManager）

# 步驟 6：分別嘗試執行 hello_v1_signed.efi 和 hello_v2_signed.efi
# 預期：v1 被 SBAT 拒絕（SECURITY_VIOLATION），v2 正常執行
```

### 練習：觀察 dbx 的實際內容

```bash
# Linux 系統上（需要 UEFI 開機的真實機器或 VM）
# 查看 dbx 目前有多少 entry：
sudo mokutil --list-blocked 2>/dev/null | wc -l
# 或直接：
sudo dbxtool list | wc -l

# 查看 dbx 中最近加入的 entry（BlackLotus 相關的 hash）
# BootHole 相關的 hash 數量大約有數十到數百個
sudo dbxtool list | tail -20
```

---

## 本章重點

- **dbx hash 撤銷的問題**：N 個有洞 binary 需要 N 個 hash，BootHole 讓這個問題從理論變成現實（數百個 distro/版本組合），是 SBAT 設計的直接動因
- **dbx certificate 撤銷是核武器**：會讓所有該 CA 簽的 binary 失效，包括修了洞的新版本，實際上無法使用
- **SBAT 用 generation 解決 dbx 爆炸**：更新 SbatLevel 只需要改一個數字，空間消耗 O(1) 而非 O(N)；binary 的 .sbat section 記錄自己的 generation
- **撤銷有 brick 風險**：舊安裝媒體、雙開機舊版 Linux、企業 WinPE 都可能因 dbx/SbatLevel 更新而失效，是 Microsoft 分批緩慢推出 BlackLotus 相關 dbx 的原因
- **軍備競賽的現實**：從漏洞公開到完全撤銷，BlackLotus 有超過一年的攻擊窗口；dbx 更新覆蓋的是已知版本，中間版本仍可能被降級攻擊
- **SbatLevel 只能增加**：棘輪設計防止降低安全要求，但也讓 IT 環境的 shim 更新需要謹慎評估影響

---

## 自我檢核

- [ ] 能解釋為什麼 BootHole 導致「dbx 爆炸」問題（N 個 distro × M 個版本 = 大量 hash）
- [ ] 能說出 dbx hash 撤銷和 dbx certificate 撤銷的差別及各自的代價
- [ ] 能解釋 SBAT .sbat section 的 CSV 格式，並說明 generation 欄位如何被用於撤銷決策
- [ ] 能描述 SbatLevel 更新如何用一行 CSV 完成「撤銷所有 generation < N 的 GRUB2」
- [ ] 能說出 dbx 更新造成 brick 的三種使用者類型（舊媒體、雙開機、企業 recovery）
- [ ] 理解 BlackLotus dbx 更新為何分批緩慢推出（2023-05 到 2024，風險管理）
- [ ] 能說出 SbatLevel 的「只增不減」（ratchet）特性及其安全意義

---

## 延伸閱讀

1. **SBAT 設計文件 — rhboot/shim**
   讀哪裡：`github.com/rhboot/shim/blob/main/SBAT.md`（官方設計文件，不長，值得全讀）
   學什麼：SBAT 的 metadata 格式精確定義、generation 語意、SbatLevel variable 格式、與 dbx 的配合設計
   關聯：本章 SBAT 章節的一手資料；做 practice-e 時的技術規範

2. **Microsoft KB5025885 / KB5028185 技術說明**
   讀哪裡：`support.microsoft.com` 搜尋 KB5025885，看「Known Issues」章節
   學什麼：Microsoft 的三階段部署策略；雙開機 brick 的具體條件；opt-out 機制的設計
   關聯：本章「軍備競賽：BlackLotus 之後」時間軸的一手資料；理解撤銷的實際部署代價

3. **"PKfail: Untrusted Platform Keys in UEFI" — Binarly（2024）**
   讀哪裡：`binarly.io/blog` 搜尋 PKfail，完整報告
   學什麼：UEFI PK（Platform Key）大規模洩漏的技術分析；PK 洩漏如何讓 dbx/SbatLevel 所有保護失效；T6（公開金鑰）在 UEFI 信任鏈根部的最壞情境
   關聯：本章 dbx 的「寫入需要 PK/KEK 授權」設計的反面；Ch 44 廠商緩解的失敗案例

→ [下一章](./practice-e-data-bypass-poc.md)
