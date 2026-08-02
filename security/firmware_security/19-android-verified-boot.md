# Ch 19 — Android Verified Boot

> **目標**：理解 AVB 2.0 的完整信任鏈——從 bootloader 如何驗 vbmeta、dm-verity 如何保護 system，到 rollback protection 如何防止降級——以及 bootloader unlock 在哪裡打開了缺口。

## 為什麼需要 Android Verified Boot？

Android 裝置的攻擊面是 PC 的超集：SoC 廠商、ODM、OEM、電信商各自插入自訂韌體，供應鏈長度讓「從出廠到用戶手中」這段路充滿機會。攻擊者可以：

- 替換 `/system` 的 APK（注入惡意 app 且無法卸除）
- 刷降級 ROM 繞過已修補的漏洞
- 竄改 `boot.img` 植入 root backdoor

AVB（Android Verified Boot）2.0 是 Google 從 Android 8（Oreo）強制推行的機制，核心思想和 UEFI Secure Boot 相同：**建立從 bootloader 到 kernel 再到檔案系統的完整信任鏈**，任何一環被竄改都要讓裝置明顯表示「有事」。

```
SoC BootROM（burned key）
   │  驗 bootloader（BL2/aboot/lk）
   ▼
Bootloader（OEM key）
   │  驗 vbmeta partition
   ▼
vbmeta（AVB metadata）
   │  hash descriptor → 驗 boot.img kernel hash
   │  hashtree descriptor → 設定 dm-verity 給 system/vendor
   ▼
Linux kernel（DM-Verity active）
   │  每次讀 block 都驗 hash tree
   ▼
/system、/vendor、/product（read-only, verified）
```

這是「防竄改」而不是「防逆向」——AVB 不加密，它只是不讓你改了之後裝置照常開機。

---

## vbmeta 分區解剖

vbmeta 是 AVB 的核心 metadata 容器，獨立佔一個小分區（通常 64 KB 以內）。

```
┌──────────────────────────────────────────────────────┐
│  AvbVBMetaImageHeader（256 bytes）                   │
│   magic: "AVB0"                                      │
│   required_libavb_version_major / minor              │
│   authentication_data_block_size                     │
│   auxiliary_data_block_size                          │
│   algorithm_type  (SHA256_RSA4096 / SHA512_RSA8192)  │
│   hash_offset / hash_size   → 指向下面的 hash 欄位   │
│   signature_offset / signature_size                  │
│   public_key_offset / public_key_size                │
│   descriptors_offset / descriptors_size              │
│   rollback_index                                     │
│   flags                                              │
├──────────────────────────────────────────────────────┤
│  Authentication Data Block                           │
│   hash（SHA256/SHA512，覆蓋 auxiliary block）         │
│   signature（RSA，簽的是 hash）                      │
├──────────────────────────────────────────────────────┤
│  Auxiliary Data Block                                │
│   public key（DER 格式 RSA 公鑰）                    │
│   descriptors[]（可多個，順序排列）                  │
│     - Hash Descriptor（boot, dtbo, …）               │
│     - Hashtree Descriptor（system, vendor, …）       │
│     - Chained Partition Descriptor（vendor_boot, …） │
│     - Property Descriptor                            │
└──────────────────────────────────────────────────────┘
```

bootloader 的驗證流程：
1. 讀 vbmeta，從 header 取出 `authentication_data_block_size`
2. 用 burned-in OEM public key 驗 signature → 驗通了才信任 auxiliary block
3. 從 auxiliary block 取出 descriptors
4. 對每個 hash descriptor：讀對應分區、計算 SHA256/SHA512，比對 descriptor 內的 `digest`
5. 對每個 hashtree descriptor：把 root hash 和 salt 傳給 kernel commandline，kernel 用它初始化 dm-verity

### Hash Descriptor vs Hashtree Descriptor

| 特性 | Hash Descriptor | Hashtree Descriptor |
|------|----------------|---------------------|
| 適用分區 | boot, dtbo, vbmeta_system | system, vendor, product |
| 如何驗 | bootloader 讀整個分區算一次 hash | kernel 掛 dm-verity，讀時逐 block 驗 |
| 分區大小限制 | 小（幾十 MB） | 大（幾 GB） |
| 核心欄位 | `partition_name`, `salt`, `digest` | `partition_name`, `salt`, `root_digest`, `data_block_size`, `hash_block_size`, `fec_*`（Forward Error Correction） |

### Chained Partition

chained partition 讓 vbmeta 不必直接管全部分區——它指向另一個 vbmeta-like 結構（`vbmeta_system`、`vbmeta_vendor`），並包含那個結構的公鑰 hash。這樣 OEM 可以把不同分區的 key 分開管理，不用把所有 descriptor 塞進主 vbmeta。

```
vbmeta
  └── chained: vbmeta_vendor (key_sha256 = AABBCC...)
        └── hashtree descriptor: vendor partition
```

---

## dm-verity：kernel 如何在讀取時驗證分區

dm-verity（Device Mapper Verity）是 Linux 的一個 device mapper target，把一個 block device 包裝成「讀任何 block 時自動驗完整性」的只讀設備。

### Hash Tree 結構

```
                    Root Hash（傳入 kernel commandline）
                         │
          ┌──────────────┴──────────────┐
          H(L1[0])               H(L1[1])          Level 1
             │                      │
    ┌─────┬──┴──┐            ┌─────┬──┴──┐
  H(L2)  H(L2)  H(L2)  H(L2)  H(L2)  H(L2)  H(L2)  H(L2)   Level 2
   │                                                    │
data block[0] … data block[1] … … … … data block[N-1]   Data（/system）
```

- 每個 data block（預設 4096 bytes）算一個 SHA256，結果存入 leaf hash block
- leaf hash blocks 再往上疊一層 hash，以此類推直到 root
- root hash 在開機時已由 vbmeta 驗過（bootloader 驗 vbmeta，vbmeta descriptor 含 root_digest）

讀一個 data block 時，kernel 從 leaf 走到 root，驗整條 hash chain，任一環不對即觸發錯誤處理。

### 三種 dm-verity 模式

| 模式 | 行為 | 對應狀況 |
|------|------|---------|
| `enforcing`（預設） | 驗失敗 → I/O error，通常觸發 kernel panic | LOCKED 裝置正常運作 |
| `eio` | 驗失敗 → 只回 EIO，不 panic | 某些 A/B OTA 過渡期暫時用 |
| `disabled` | 完全不驗 | UNLOCKED 裝置，`avb disable-verity` 後 |

---

## Rollback Protection：防止刷降級版本

### Rollback Index 機制

vbmeta header 有一個 `rollback_index` 欄位（uint64）。裝置的 secure storage（通常在 TEE/TrustZone 保護的 RPMB 或 e-fuse）記著「目前允許的最小 rollback_index」。

```
開機驗證流程（rollback check）：
  vbmeta.rollback_index  ≥  device.stored_rollback_index   → OK
  vbmeta.rollback_index  <  device.stored_rollback_index   → 拒絕開機
```

OTA 更新時，如果新版 ROM 的 rollback_index 比裝置當前值高，更新完成後 bootloader 會把 stored value 更新到新值。這個寫入是**單向的**（anti-rollback，AR fuse 的精神），TEE 或 RPMB 保護讓它無法從 OS 層軟體降回。

### Rollback Index Location

rollback_index 有「slot」的概念，主 vbmeta 可以在不同 rollback index location（`rollback_index_location`）存放，讓不同分區群組有獨立的 rollback counter。

```
rollback_index_location = 0 → AVB_AB_METADATA_MISC_PARTITION_OFFSET 的主 counter
rollback_index_location = 1 → vendor_boot / vbmeta_vendor 用的 counter
…（最多 32 個 location）
```

---

## Bootloader 狀態模型

AVB 用四個 verified boot state 對應「信任程度」：

```
┌──────────────────────────────────────────────────────────────────┐
│  GREEN  ─────── LOCKED + AVB 完全通過 + 使用 OEM key            │
│                 正常出廠狀態。無任何警告畫面。                    │
├──────────────────────────────────────────────────────────────────┤
│  YELLOW ─────── LOCKED + 使用者自訂 key（非 OEM key 但有 key）  │
│                 顯示警告畫面幾秒（可跳過）。                      │
│                 Android 9+ 才有此狀態。                          │
├──────────────────────────────────────────────────────────────────┤
│  ORANGE ─────── UNLOCKED（bootloader 已解鎖）                   │
│                 顯示橘色警告畫面，告知「使用者自行負責」。         │
├──────────────────────────────────────────────────────────────────┤
│  RED    ─────── LOCKED 但 AVB 驗證失敗                          │
│                 vbmeta 被竄改、hash 不對、或 rollback 攻擊。     │
│                 顯示紅色警告，某些裝置直接拒絕開機。              │
└──────────────────────────────────────────────────────────────────┘
```

這個狀態會透過 `ro.boot.verifiedbootstate`（`green`/`yellow`/`orange`/`red`）傳入 Android，SafetyNet/Play Integrity API 用它決定是否信任裝置。

### LOCKED vs UNLOCKED

bootloader lock state 儲存在安全儲存（通常是 TrustZone 管的 persistent register 或 RPMB）：

- **LOCKED**：bootloader 只接受有效 AVB 簽章的 image
- **UNLOCKED**：bootloader 跳過 AVB 驗證，任何 image 都載入（verified boot state = ORANGE）

---

## Bootloader Unlock：`fastboot flashing unlock`

```
adb reboot bootloader
fastboot flashing unlock
```

觸發後，裝置顯示警告畫面，使用者必須用音量鍵確認。確認後：

1. 裝置執行 **factory reset**（清除 `/data`、`/cache`）——這是必要的，防止舊 session 的加密金鑰洩漏給解鎖後的 root 環境
2. bootloader lock state 設為 UNLOCKED
3. 之後每次開機顯示橘色警告畫面（ORANGE state）

**OEM unlock 開關**：在 Android 設定 → 開發者選項 → OEM unlocking，這個開關的狀態也儲存在 secure storage，`fastboot flashing unlock` 只有在這個開關開啟時才會生效。部分電信商版本的裝置會把 OEM unlock 永遠鎖死（`ro.oem_unlock_supported=0`），無法解鎖。

### 解鎖後信任影響的精確分析

| 項目 | LOCKED GREEN | UNLOCKED ORANGE |
|------|-------------|----------------|
| AVB 驗 vbmeta | 是 | 否 |
| dm-verity | 啟用 | 預設停用（可手動啟用） |
| Rollback protection | 啟用 | 部分裝置仍檢查 |
| SafetyNet/Play Integrity | 通過（若未 root） | basicIntegrity 失敗 |
| TEE 功能（WideVine L1） | 完整 | 降級 L3（不能播高清 DRM） |
| 使用者資料清除 | 無 | 解鎖時強制 |

WideVine L1 的降級是不可逆的——某些廠商（Sony、三星）在第一次 unlock 後就永久燒斷 WideVine L1 fuse。

---

## 攻擊面分析

### 1. Downgrade 攻擊

**前提**：rollback protection 沒有正確實作，或 stored rollback index 未更新。

某些早期 Android 8 裝置用 eMMC 的 RPMB 存 rollback counter，但 RPMB 驗證需要 key，而 key 在 TEE 初始化後才可用。如果 bootloader 在 TEE 初始化前就讀 rollback counter 失敗，會 fallback 到「不驗」。

```
攻擊步驟：
1. 找到舊版韌體（含已修補 CVE 的修補前版本）
2. 若 rollback_index 未更新或驗證邏輯 fallback → 直接刷舊版
3. 舊版 kernel exploit 復活
```

**緩解**：RPMB 必須在 BootROM 或 BL2 就初始化並驗讀；rollback index 失敗應直接拒絕而非 fallback。

### 2. vbmeta 竄改（需 UNLOCKED 狀態）

UNLOCKED 裝置跳過 AVB 驗證，所以可以刷任意 vbmeta + boot.img：

```
# 清除 vbmeta（disable verification）
python avbtool.py make_vbmeta_image --flag 2 --output vbmeta_empty.img
fastboot flash vbmeta vbmeta_empty.img
fastboot flash boot patched_boot.img
fastboot reboot
```

這是 root 的標準做法（Magisk），在 UNLOCKED 裝置上合法且被設計接受。問題在於：如果 LOCKED 裝置有方法繞過 AVB 驗證本身（例如 bootloader 漏洞），才是真正的攻擊。

### 3. Rollback Index 未防護的設備

部分 vendor 的 bootloader 雖有 AVB，但 rollback index 儲存在 misc 分區（普通 eMMC，非 RPMB），這意味著從 OS root 就能清掉：

```
# （假設已 root，且 rollback 存在 misc）
dd if=/dev/zero of=/dev/block/by-name/misc bs=4096 count=1
# 重啟 → rollback counter 歸零 → 可刷舊版
```

**本段為理論預期行為，實際效果取決於特定 SoC/bootloader 實作。**
驗證方法：用 `fastboot getvar avb-version` 和 `fastboot getvar rollback-index` 查看裝置支援程度；用 `avbtool info_image` 讀 vbmeta 確認 rollback_index 值。

### 4. 自訂 key 注入（yellow state）

如果有辦法取得 bootloader unlock 並燒入自訂公鑰（部分測試裝置支援 `fastboot flash avb_custom_key`），可以用自己的私鑰簽 vbmeta，讓裝置在 yellow state 下開機而非 orange——這對 TEE/WideVine 的影響因裝置而異。

```
openssl genrsa -out my_key.pem 4096
python avbtool.py extract_public_key --key my_key.pem --output my_public_key.bin
fastboot flash avb_custom_key my_public_key.bin
# 之後用 my_key.pem 簽的 vbmeta → yellow state
```

---

## 底層機制：AVB 驗證的 C 實作骨架

libavb 的核心驗證邏輯在 `avb_slot_verify.c`（AOSP 開源）：

```c
// 簡化版流程，非完整原始碼
AvbSlotVerifyResult avb_slot_verify(
    AvbOps* ops,                    // 平台相關 ops（讀 partition、讀 rollback）
    const char* const* requested_partitions,
    const char* ab_suffix,          // "_a" 或 "_b"
    AvbSlotVerifyFlags flags,
    AvbHashtreeErrorMode hashtree_error_mode,
    AvbSlotVerifyData** out_data) {

    // 1. 讀 vbmeta partition
    // 2. 驗 vbmeta 的 signature（用 ops->validate_vbmeta_public_key）
    // 3. 驗 rollback index（用 ops->read_rollback_index）
    // 4. 遍歷 descriptors：
    //    - hash descriptor → 讀 partition → 計算並比對 digest
    //    - hashtree descriptor → 準備 dm-verity cmdline 參數
    //    - chained partition descriptor → 遞迴驗另一個 vbmeta
    // 5. 建立 kernel commandline（含 dm-verity 參數）
    // 6. 回傳 AVB_SLOT_VERIFY_RESULT_OK 或錯誤碼
}
```

`ops->validate_vbmeta_public_key` 是平台相關的——bootloader 把 OEM 公鑰 hash 燒進 fuse，驗的是「vbmeta 裡附的公鑰的 hash 是否等於 fuse 值」而不是直接用 fuse 公鑰驗簽。這讓 key rollover 成為可能（換 key 時只需更新 fuse hash，不是換整個 fuse）。

---

## 與 x86 Secure Boot 的對比

| 面向 | Android AVB 2.0 | x86 UEFI Secure Boot |
|------|----------------|----------------------|
| 信任錨 | SoC BootROM + OEM fuse | UEFI db/PK/KEK |
| Metadata 格式 | vbmeta（libavb 自定） | Authenticode 簽章（PE/COFF） |
| 檔案系統驗證 | dm-verity（runtime） | 無原生支援（靠 IMA） |
| Rollback protection | rollback index + RPMB/fuse | dbx（撤銷 hash） |
| 使用者 unlock 機制 | fastboot flashing unlock | 進 UEFI 介面停用 Secure Boot |
| 警告 UI | 彩色警告畫面（orange/red） | 無強制標準（廠商自定） |
| 遠端驗證整合 | Play Integrity API | TPM Attestation |

---

## 踩雷

1. **「UNLOCKED 裝置驗 AVB」是常見誤解**：unlock 後 AVB 完全跳過，`adb shell getprop ro.boot.verifiedbootstate` 會是 `orange`，dm-verity 預設停用，這是設計行為，不是 bypass。真正的攻擊是在 LOCKED 狀態下找 bootloader 漏洞。

2. **rollback_index 更新時機陷阱**：OTA 更新後，rollback counter 不是立即更新，而是在新系統**成功開機並確認**後才寫入（A/B OTA 流程的 `mark_as_successful`）。如果在確認前就重啟舊版，counter 不會增加，降級仍然可能。

3. **vbmeta flags = 2（HASHTREE_DISABLED）的濫用**：`avbtool make_vbmeta_image --flag 2` 產生的 vbmeta 會讓 bootloader 跳過 hashtree 驗證（dm-verity 停用），即使裝置 LOCKED 也有效——**但前提是 bootloader 允許這個 flag，部分 bootloader 在 LOCKED 下拒絕此 flag**。不要假設所有裝置行為一致。

4. **TEE 存的 rollback index 不一定是唯一的**：AVB 最多支援 32 個 rollback index location，主 vbmeta 用 location 0，chained partition 可以用不同 location。如果只清 location 0，location 1 的 vendor counter 仍然有效，刷舊版 vendor 分區還是會失敗。

5. **WideVine L1 降級不可逆**：某些廠商（尤其日系 Sony）在第一次 unlock 時就燒斷 WideVine fuse，不要在重要的測試機上解鎖。

6. **Yellow state 的 key hash 對照**：即使在 yellow state，裝置仍然驗 vbmeta 的簽章，只是用的是使用者燒入的自訂 key 而非 OEM key。如果自訂 key 燒錯（hash 不符），仍然是 RED state。

---

## 進階延伸

- **libavb 原始碼審計**：AOSP `external/avb/` 下的 `avb_slot_verify.c`、`avb_vbmeta_image.c` 是最直接的學習材料，配合 `avbtool.py` 的 Python 實作對照理解。

- **A/B OTA 與 AVB 的互動**：A/B slot 機制讓裝置在更新期間始終有一個可開機的 slot，`bootctrl` HAL 管理 slot 切換，rollback counter 的「確認後才更新」邏輯在這裡。

- **Android 14 的 AVB + VBMeta System**：Android 14 引入 `vbmeta_system` chained partition，把 system-level 分區（system, system_ext, product）獨立出來，允許 Google 和 OEM 分別管理 key。

- **Play Integrity API 的繞過研究**：AVB orange/red state 是 Play Integrity 失敗的直接原因，但有研究者透過 hook TEE 的 `KeyAttestation` 介面偽造 GREEN state attestation——這是 TEE 漏洞的衍生攻擊。

---

## 動手練習

### 練習 1：解析真實 vbmeta

```bash
# 用 avbtool 分析任意 Android ROM 的 vbmeta.img
pip install avbtool  # 或從 AOSP external/avb 複製 avbtool.py

# 下載公開的 Pixel factory image，解壓後找 vbmeta.img
python avbtool.py info_image --image vbmeta.img
```

期望輸出：algorithm、rollback_index、descriptors 列表（每個分區的 digest 或 root_digest）。

### 練習 2：模擬 hash descriptor 驗證

```bash
# 用 Python 手動計算 boot.img 的 SHA256 並與 avbtool 輸出比對
python3 -c "
import hashlib, sys
with open('boot.img', 'rb') as f:
    data = f.read()
salt = bytes.fromhex('AABBCC...')  # 從 avbtool info_image 取得
h = hashlib.sha256(salt + data).hexdigest()
print('digest:', h)
"
```

### 練習 3：構造一個 vbmeta（UNLOCKED 裝置）

```bash
# 產生測試 key
openssl genrsa -out test_key.pem 4096

# 對修改過的 boot.img 簽 vbmeta
python avbtool.py add_hash_footer \
    --image boot.img \
    --partition_name boot \
    --partition_size $(stat -c%s boot.img) \
    --key test_key.pem \
    --algorithm SHA256_RSA4096

python avbtool.py make_vbmeta_image \
    --include_descriptors_from_image boot.img \
    --key test_key.pem \
    --algorithm SHA256_RSA4096 \
    --output vbmeta_custom.img
```

---

## 本章重點

- vbmeta 是 AVB 的核心容器，包含 hash/hashtree/chained partition descriptors，由 OEM 私鑰簽章
- hash descriptor：bootloader 在開機時一次驗整個分區（boot, dtbo）
- hashtree descriptor：dm-verity 在 kernel 層每次 read block 時驗 hash tree（system, vendor）
- rollback_index 由 TEE/RPMB 保護的 stored counter 防止降級，寫入單向
- verified boot state（GREEN/YELLOW/ORANGE/RED）決定裝置信任等級和 DRM 能力
- UNLOCKED 裝置跳過 AVB，真正的攻擊是在 LOCKED 狀態下找 bootloader 漏洞

---

## 自我檢核

- [ ] 能解釋 vbmeta authentication block 和 auxiliary block 的分工
- [ ] 知道 hash descriptor 和 hashtree descriptor 在用途和觸發時機上的差異
- [ ] 能說明 dm-verity 的 hash tree 結構，從 root hash 到 data block 的驗證路徑
- [ ] 知道 rollback_index 為什麼不能只用 eMMC 普通分區存
- [ ] 能區分 LOCKED/UNLOCKED 和 GREEN/YELLOW/ORANGE/RED 這兩組概念的關係
- [ ] 知道 OEM unlock 開關（OEM unlocking）在哪裡、為什麼需要 factory reset

---

## 延伸閱讀

1. **AOSP — Android Verified Boot 2.0 官方文件**（`source.android.com/docs/security/features/verifiedboot/avb`）
   讀哪裡：AVB overview、vbmeta descriptor 格式定義、rollback protection 設計理念
   學什麼：AVB 的設計決策（為什麼用 rollback_index 而非 X.509 CRL）
   關聯：本章 vbmeta 結構和 rollback 機制的一手來源

2. **external/avb/avbtool.py 與 avb_slot_verify.c**（AOSP 開源）
   讀哪裡：`avbtool.py` 的 `info_image`、`add_hash_footer`、`make_vbmeta_image` 函式；`libavb/avb_slot_verify.c` 的主驗證迴圈
   學什麼：descriptor 序列化格式的精確位元布局，驗證的實際控制流
   關聯：接 Ch 25 逆 ARM bootloader 時，libavb 移植版（Qualcomm ABL/MTK LK）的反編譯對照

3. **"Android Bootloader Security" — Aleph Research（2021）**（`alephsecurity.com/2021/01/13/grub2-lpe/` 相關推薦；或搜索 BlackHat 2019 "Breaking Samsung's ARM TrustZone"）
   讀哪裡：bootloader 攻擊面的實際 CVE 分析部分
   學什麼：LOCKED 狀態下 bootloader 漏洞如何真正威脅 AVB（rollback bypass、parse overflow）
   關聯：直接接 Ch 20 MTK bootloader 攻擊面、Ch 21 嵌入式繞過類型學

→ [下一章](./20-mtk-vendor-soc.md)
