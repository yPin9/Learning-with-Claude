# Ch 27 — Secure Boot：簽署鏈

> **目標**：理解 UEFI Secure Boot 的信任鏈——PK/KEK/db/dbx 金鑰階層、`.efi` 的數位簽署驗證、shim 如何讓 Linux 在 Secure Boot 下開機、MOK（Machine Owner Key）機制，以及 kernel/module 簽署。

> **環境**：UEFI Secure Boot，shim，mokutil。承接 Ch 10/15（UEFI、變數）、Ch 16/19（開機鏈、shim）。

## 為什麼需要 Secure Boot？

開機鏈的每一棒（韌體 → bootloader → kernel → ...）如果有一棒被惡意替換——例如惡意軟體替換你的 bootloader（bootkit），它就能在 OS 之前執行、隱藏自己、繞過所有 OS 層的防護。這是最深層的攻擊。

```
沒有 Secure Boot 的威脅：
  惡意軟體替換 /boot 的 bootloader 或 kernel
        │
  下次開機，惡意 bootloader 先跑（在 OS 之前）
        │
  它能：隱藏自己、植入後門、繞過 OS 防毒、竊取加密金鑰
        │
  → bootkit / rootkit，極難偵測和清除
```

**Secure Boot** 的目標：確保開機鏈的每一棒都是「可信的、未被竄改的」——透過數位簽署驗證。韌體只執行簽署過的 bootloader，bootloader 只執行簽署過的 kernel，環環相扣。

## 先建立直覺：開機鏈的每一棒都驗證下一棒的簽署

```
Secure Boot 的信任鏈：

  韌體（有內建的信任金鑰）
        │ 驗證 bootloader 的簽署
        ▼ （簽署有效才執行）
  bootloader（shim/GRUB，被簽署）
        │ 驗證 kernel 的簽署
        ▼
  kernel（被簽署）
        │ 驗證 module 的簽署
        ▼
  kernel modules（被簽署）
        │
  → 每一棒驗證下一棒，任何一棒被竄改（簽署失效）就拒絕執行
    惡意替換的東西沒有有效簽署 → 開不了機
```

Secure Boot 像一條「信任的鎖鏈」——每一環驗證下一環的簽署，確保整條鏈沒有被插入惡意的環。攻擊者要插入惡意 bootloader/kernel，必須有有效簽署（私鑰），但私鑰不在他手上，所以插不進去。

## 金鑰階層：PK / KEK / db / dbx

Secure Boot 用一個金鑰階層（存在 UEFI 變數，Ch 15）：

```
Secure Boot 的金鑰階層（存在 NVRAM，AUTHENTICATED_WRITE，Ch 15）：

  PK（Platform Key）：
    最高層，平台擁有者的金鑰（通常 OEM/你）
    控制誰能改 KEK
        │
  KEK（Key Exchange Key）：
    第二層，控制誰能改 db/dbx
    通常含微軟和 OEM 的金鑰
        │
  db（Signature Database）：
    「允許」清單——這些金鑰簽署的 .efi 可以執行
    通常含微軟的金鑰（簽署 Windows bootloader 和 shim）
        │
  dbx（Forbidden Signature Database）：
    「拒絕」清單——這些（即使曾有效）被撤銷
    用於撤銷有漏洞的舊 bootloader
```

```bash
# 看 Secure Boot 狀態和金鑰
mokutil --sb-state          # Secure Boot 開或關
# SecureBoot enabled

# 看金鑰（需要 efitools/mokutil）
mokutil --list-enrolled     # 看 MOK
efi-readvar                 # 看 PK/KEK/db/dbx（需 efitools）
```

韌體開機時，用 `db` 裡的金鑰驗證要執行的 `.efi`——簽署在 `db` 裡才執行，在 `dbx` 裡就拒絕。

## shim：讓 Linux 在 Secure Boot 下開機

問題：`db` 裡通常只有微軟的金鑰（主機板 OEM 預裝）。Linux 的 GRUB/kernel 不是微軟簽的，怎麼在 Secure Boot 下開機？

```
shim 的巧妙解法：

  問題：db 只有微軟金鑰，Linux bootloader 沒被微軟簽
        │
  解法：shim（一個小 bootloader）
    - 微軟「簽署」shim（Linux 發行商付費請微軟簽）
    - shim 在 db 驗證下能執行（因為微軟簽了）
        │
  shim 內建發行版自己的金鑰
    - shim 用「發行版的金鑰」驗證 GRUB/kernel
    - 不需要每個發行版的 GRUB/kernel 都找微軟簽
        │
  鏈：韌體（db=微軟）→ 驗證 shim（微軟簽）→
      shim（發行版金鑰）→ 驗證 GRUB → 驗證 kernel
```

> shim 是個政治+技術的巧妙妥協。微軟控制了多數主機板的 `db`（OEM 預裝微軟金鑰）。Linux 發行版不可能讓微軟簽每個 kernel 更新。shim 的解法：微軟只簽一個小小的 shim（很少變），shim 內建發行版的金鑰，由 shim 去驗證 GRUB/kernel。這讓 Linux 能在 Secure Boot 下開機，而不需要微軟簽每個 Linux 元件。這就是為什麼 Ubuntu 的 ESP 有 `shimx64.efi`（Ch 16）——它是 Secure Boot 開機鏈的第一環。

## MOK：Machine Owner Key

如果你自己編譯 kernel/module（沒有發行版簽署），怎麼在 Secure Boot 下用？**MOK**（Machine Owner Key）讓你註冊自己的金鑰：

```
MOK（Machine Owner Key）：
  你生成自己的金鑰
        │
  用 mokutil 註冊到 shim 的 MOK 清單（要重開機在韌體確認）
        │
  shim 除了驗證「發行版金鑰」，也驗證「MOK」
        │
  → 你用自己的 MOK 簽署的 kernel/module 能在 Secure Boot 下執行
```

```bash
# MOK 的使用流程（如自己編譯的 kernel module）
# 1. 生成 MOK
openssl req -new -x509 -newkey rsa:2048 -keyout MOK.key \
    -out MOK.crt -nodes -days 3650 -subj "/CN=My MOK/"

# 2. 用 MOK 簽署你的 kernel module
sudo kmodsign sha512 MOK.key MOK.crt my_module.ko

# 3. 註冊 MOK 到 shim（會要求設密碼，重開機時確認）
sudo mokutil --import MOK.crt
# 重開機 → shim 的 MokManager 介面 → Enroll MOK → 輸入密碼

# 4. 之後你的 MOK 簽署的 module 能載入（Secure Boot 下）
sudo modprobe my_module
```

> MOK 解決「我想用自己編譯的 kernel/module，但 Secure Boot 不認」的問題（Ch 30 的 DKMS 也碰到）。它讓你註冊自己的金鑰到 shim，之後用這個金鑰簽的東西就被信任。這是 Secure Boot 給「機器擁有者」的後門——你掌控你的機器，能加自己的信任金鑰。NVIDIA 驅動、自編 kernel 等都靠 MOK 在 Secure Boot 下運作。

## kernel 與 module 簽署

Secure Boot 的信任鏈延伸到 kernel module：

```
kernel module 簽署（Secure Boot 啟用時）：
  kernel（被簽署）啟用「lockdown」模式
        │
  只載入「被信任金鑰簽署」的 module
    - 發行版的金鑰
    - MOK
    - kernel 內建的金鑰
        │
  未簽署的 module → 拒絕載入
    "Loading of unsigned module is rejected"
        │
  防止：惡意 module 注入 kernel（即使你是 root）
```

```bash
# 看 kernel 是否要求簽署 module
cat /sys/kernel/security/lockdown
# none [integrity] confidentiality  ← integrity 模式要求 module 簽署

# 簽署的 module 載入正常；未簽署的：
sudo modprobe unsigned_module
# modprobe: ERROR: could not insert 'unsigned_module':
#   Key was rejected by service
```

這是 Ch 30 的 DKMS module 在 Secure Boot 下需要 MOK 簽署的原因——kernel 在 lockdown 模式只載入簽署的 module。

## 故意對照：Secure Boot 開與關

```
Secure Boot 關閉：
  韌體執行任何 .efi（不驗證簽署）
  kernel 載入任何 module
        │
  方便（自編 kernel/module 直接用）
  但 bootkit/rootkit 能在開機鏈插入

Secure Boot 開啟：
  韌體只執行 db/MOK 簽署的 .efi
  kernel 只載入簽署的 module
        │
  安全（防 bootkit、防惡意 module）
  但自編東西要簽署（MOK）
        │
  取捨：安全 vs 方便
```

## 踩雷集錦

1. **以為 Secure Boot 能防一切**：它防「開機鏈被竄改」（bootkit），不防 OS 層的攻擊（已執行的惡意軟體）。它是一層防護，不是萬能

2. **自編 kernel/module 在 Secure Boot 下載不了**：kernel lockdown 要求簽署。用 MOK 簽署，或（不建議）關 Secure Boot

3. **混淆 Secure Boot 和 Measured Boot**：Secure Boot 驗證簽署（拒絕未簽署的）；Measured Boot（Ch 28）記錄開機狀態（不拒絕，只記錄供之後驗證）。不同機制

4. **刪 PK 進入 setup mode 沒搞懂**：刪掉 PK 讓 Secure Boot 進「setup mode」（能改金鑰）。亂搞可能讓系統開不了機或無法重新啟用 Secure Boot

5. **以為 shim 是 GRUB 的一部分**：shim 是獨立的小 bootloader（微軟簽），它驗證並載入 GRUB。shim → GRUB → kernel 是三個獨立元件（Ch 16）

## 進階：Secure Boot 的爭議與信任問題

Secure Boot 有政治和信任的爭議：

```
Secure Boot 的爭議：
  1. 微軟控制 db（多數主機板預裝微軟金鑰）
     → 早期擔心微軟用它鎖死 Linux（後來 shim 解決，但仍有人不滿）
  2. 信任根在哪？
     → 你信任 OEM/微軟的金鑰管理嗎？
     → 韌體本身（在 Secure Boot 之前）沒被驗證——信任從韌體開始
  3. 自由軟體的張力
     → 「你的機器你做主」vs「廠商控制能執行什麼」
        │
  支持：防 bootkit 是真實的安全價值
  反對：中心化的信任、廠商控制的疑慮
```

> **認識論誠實**：Secure Boot 的安全價值是真實的（防 bootkit），但它的信任模型有爭議。信任根在韌體和 OEM/微軟的金鑰——你必須信任他們。對「機器擁有者完全掌控」的自由軟體理念，這是個張力。MOK 緩解了部分（你能加自己的金鑰），但根本的「廠商控制 db」仍在。多數人接受這個取捨（安全 > 顧慮），但理解這個爭議讓你對 Secure Boot 有完整認識，而非盲目認為「開了就安全」。

## 動手練習

1. 看你系統的 Secure Boot：`mokutil --sb-state`（開或關）、`mokutil --list-enrolled`（MOK）、`cat /sys/kernel/security/lockdown`（lockdown 模式）

2. 看開機鏈：UEFI 系統 `ls /boot/efi/EFI/<distro>/`，找 shimx64.efi、grubx64.efi。`efibootmgr -v` 看韌體執行哪個（應該是 shim）

3. 看簽署：`sbverify --list /boot/vmlinuz-$(uname -r)`（如果有 sbsigntool），看 kernel 的簽署。`modinfo <module> | grep sig`（看 module 簽署資訊）

4. 概念練習（不一定真做）：理解「自編一個 kernel module，要在 Secure Boot 下載入」的完整流程（生成 MOK → 簽 module → 註冊 MOK → 載入）

## 本章重點整理

- Secure Boot 防 bootkit：開機鏈每一棒驗證下一棒的數位簽署，竄改的東西沒有效簽署就拒絕執行
- 金鑰階層：PK（平台金鑰）→ KEK（控制 db/dbx）→ db（允許清單）/ dbx（撤銷清單），存 NVRAM
- shim：微軟簽署的小 bootloader，內建發行版金鑰驗證 GRUB/kernel，讓 Linux 在 Secure Boot 下開機
- MOK（Machine Owner Key）：讓你註冊自己的金鑰，簽署自編 kernel/module 在 Secure Boot 下用
- kernel lockdown 要求 module 簽署；Secure Boot 有真實安全價值但信任模型有爭議（廠商控制 db）

## 自我檢核

- [ ] 能解釋 Secure Boot 防的是什麼威脅（bootkit，開機鏈竄改）
- [ ] 知道 PK/KEK/db/dbx 的階層和各自作用
- [ ] 能解釋 shim 如何讓 Linux 在 Secure Boot 下開機（微軟簽 shim，shim 驗 Linux）
- [ ] 知道 MOK 解決什麼問題（自編 kernel/module 的簽署）
- [ ] 能說出 Secure Boot 的安全價值和爭議（不只一面）

## 延伸閱讀

### 官方文件

- **[UEFI Spec, Section 32 (Secure Boot and Driver Signing)](https://uefi.org/specifications)**
  - **讀哪裡**：32.1-32.3，PK/KEK/db 和簽署驗證
  - **學什麼**：Secure Boot 的權威定義
  - **前提**：本章 + Ch 15

- **[shim documentation](https://github.com/rhboot/shim/blob/main/README.md)**
  - **讀哪裡**：README 和 MOK 那部分
  - **學什麼**：shim 的運作、MOK 機制
  - **前提**：本章

### 部落格 / 文章

- **[Secure Boot and Linux (Matthew Garrett)](https://mjg59.dreamwidth.org/)** — Matthew Garrett（shim 設計者之一）
  - **這篇說什麼**：Secure Boot 的設計、shim 的由來、爭議
  - **讀哪裡**：搜尋他關於 Secure Boot、shim 的文章
  - **為什麼值得讀**：shim 設計者本人，講 Secure Boot 政治和技術的權威來源

→ [Ch 28 Measured Boot 與 TPM](./28-measured-boot-tpm.md)
