# Ch 31 — root / Magisk 檢測與繞過

> **目標**：搞懂 App 怎麼判斷「這台裝置有沒有 root、是不是動過手腳」——從最原始的**找 `su`/`busybox`/檢 build tag**，到把判斷外包給 Google 的**SafetyNet / Play Integrity（硬體背書）**；並學會對應的隱藏與繞過：Magisk 的 **DenyList / Zygisk / Shamiko**，以及 Play Integrity 的現況與侷限。核心心法：**root 檢測分兩個世界——「本地掃檔案/屬性」你能藏，「硬體背書」你藏不掉、只能靠 Google 端沒收緊時的縫隙。**

> **環境**：檢測邏輯以 **Python 3** 表達演算法（掃路徑、比對 build tag 字串）。凡需要真跑 Magisk/Zygisk/Play Integrity API 才能重現的，一律標「**未實測，理論預期行為**」與 AVD/真機驗證步驟。**時效性警告**：Play Integrity 的強度、繞過現況變動極快，本章反映 **2026 年中的狀態，隨時可能被 Google 收緊**。本 repo 沙箱是 Windows，無 Android/Magisk。

## 為什麼需要這個？

銀行、支付、遊戲、串流 App 特別在意 root——因為 root 讓你能讀別的 App 的私有資料、注入 Frida、改記憶體作弊、繞授權。所以它們裝了 root 檢測：偵測到就拒絕啟動、鎖功能、或直接封號。你想在自己的 AVD（本來就 root）上分析這些 App，第一關就是**過 root 檢測**。

而 root 檢測跟上一章的反調試不同：反調試防的是「你正在動態分析」，root 檢測防的是「這台裝置本身不可信」。兩者常一起出現，但繞法邏輯不同——尤其 **Play Integrity 把信任錨定在硬體，本地怎麼藏都沒用**，這是本章要讓你徹底理解的分水嶺：**哪些能靠藏繞過，哪些不能。** 搞混這條線，你會在「無論怎麼 hook 都過不了」的 App 上白白耗掉好幾天。

## 先建立直覺：兩個世界的信任模型

root 檢測從弱到強，本質是**信任錨定在哪裡**：

```
   世界一：本地檢測（App 自己看）          世界二：硬體背書（Google 幫看）
 ┌────────────────────────────────┐    ┌──────────────────────────────────┐
 │ App 在裝置上找證據：            │    │ App 呼叫 Play Integrity API        │
 │  · /system/bin/su 存在嗎？      │    │      │                             │
 │  · build tag 是 test-keys？     │    │      ▼                             │
 │  · Magisk 的路徑/package？      │    │  Google Play 服務 + 硬體 keystore  │
 │  · 能不能 sudo？                │    │      │ 用裝置出廠的硬體金鑰簽章     │
 │                                │    │      ▼                             │
 │  信任錨 = 裝置本地狀態          │    │  Google 伺服器回一張「這台裝置    │
 │  ⇒ 你能改本地狀態 ⇒ 能藏能繞    │    │  通過/未通過」的簽章憑證           │
 └────────────────────────────────┘    │                                    │
                                        │  信任錨 = 硬體 + Google 後端        │
                                        │  ⇒ 本地怎麼改都動不了那把硬體金鑰    │
                                        └──────────────────────────────────┘
```

這張圖是整章的地基。**世界一你能贏**（改本地狀態、藏檔案、hook 回傳），**世界二你贏不了正面**（硬體金鑰在 TEE 裡，你拿不到、偽造不了簽章）——只能靠：(a) Google 尚未把某個裝置/等級收緊的縫隙，(b) 讓 App 收到的**是 API 回傳前的資料**、在它被硬體簽章前攔截（極難）。搞懂這條線，你才知道一個 root 檢測「值不值得花時間繞」。

## 世界一（一）：找 `su` 與 `busybox`

最原始、也最常見的 root 檢測：root 意味著系統裡多了 `su`（switch user，root 授權的核心二進位）與常伴隨的 `busybox`。App 就去**一堆已知路徑**找這些檔案存不存在。

```
常被檢查的 su 路徑（App 逐個 File.exists / access() ）：
  /system/bin/su          /system/xbin/su
  /sbin/su                /su/bin/su
  /system/sd/xbin/su      /data/local/xbin/su
  /data/local/bin/su      /data/local/su
  /system/app/Superuser.apk
外加：直接嘗試執行 `which su` / Runtime.exec("su")
```

Python 表達這個「掃路徑」邏輯（**字串/路徑判斷，本 repo 沙箱可跑其結構**；Android 上路徑語意相同）：

```python
# check_su.py —— root 檢測掃 su 路徑的核心邏輯
import os
SU_PATHS = [
    "/system/bin/su", "/system/xbin/su", "/sbin/su", "/su/bin/su",
    "/data/local/xbin/su", "/data/local/bin/su", "/system/sd/xbin/su",
    "/system/app/Superuser.apk", "/data/local/su",
]
def is_rooted_by_su():
    return any(os.path.exists(p) for p in SU_PATHS)

# 進階：嘗試執行 su，能起來就有 root
def is_rooted_by_exec():
    # Android 上 = Runtime.getRuntime().exec("su") 或 which su
    # 這裡示意判斷結構
    import shutil
    return shutil.which("su") is not None
```

> **未實測其在 Android 的結果（沙箱是 Windows）**。在任何 Linux 上 `check_su.py` 的路徑不存在會回 `False`；把某個路徑指向真檔就回 `True`——邏輯本身可驗。Android 上，root 過的裝置這些路徑會有真的 `su`。

**繞過**——這正是 Magisk 的 DenyList/Shamiko 主場（下面專講）。純 Frida 層的繞法是 hook `File.exists`/`access`/`Runtime.exec`，當參數是 su 路徑就回「不存在」：

```javascript
// root-file-hide.js —— 讓 File.exists 對 su 路徑回 false
// 未實測，理論預期行為（需 AVD + frida-server）
Java.perform(function () {
    const File = Java.use("java.io.File");
    const SU_HINTS = ["su", "magisk", "supersu", "busybox"];
    File.exists.implementation = function () {
        const path = this.getAbsolutePath();
        for (const h of SU_HINTS) {
            if (path.toLowerCase().indexOf(h) !== -1) {
                console.log("[root-hide] File.exists(" + path + ") -> false");
                return false;
            }
        }
        return this.exists();
    };
});
```

驗證：hook 前 App 因偵測到 `su` 而拒啟動；hook 後這些路徑回 false，App 以為乾淨。**邊界**：App 若用 native `access()`/`stat()` 而非 Java `File.exists`，這支 Java hook 攔不到，得改 hook native libc——跟上一章「libc 層 hook」同理。

## 世界一（二）：檢 build tag（test-keys）

正式出廠的 Android 是用 Google/廠商的私鑰簽的，`ro.build.tags` 屬性顯示 **`release-keys`**。自編譯的 AOSP、或某些魔改 ROM 用測試金鑰簽，顯示 **`test-keys`**——這是「這不是官方出廠韌體」的強訊號。App 讀這個屬性判斷。

```python
# check_buildtags.py —— 檢 build tag 的邏輯
def is_custom_rom(build_tags: str) -> bool:
    # Android 上 = android.os.Build.TAGS，或 getprop ro.build.tags
    return "test-keys" in build_tags

print(is_custom_rom("release-keys"))   # 官方 → False
print(is_custom_rom("test-keys"))      # 魔改 → True
```

**實際輸出**（本 repo 沙箱 Python 3 實跑，純字串判斷）：

```
False
True
```

> **為什麼 `test-keys` 是訊號**：Android 的系統映像必須被簽章，`release-keys` = 用正式發行金鑰簽（官方韌體）；`test-keys` = 用 AOSP 公開的測試金鑰簽（任何人都能用，代表非官方）。這不是 magic 字串，是 build 簽章金鑰類型的直接反映。你的 AVD（Google APIs image）通常是 `release-keys` 但 `adb root` 得開，狀態微妙——這也是為什麼有些 App 在 AVD 上表現跟真機不同。

**繞過**：hook `android.os.Build.TAGS` 回傳 `"release-keys"`，或 hook 讀 `ro.build.tags` 的 `SystemProperties.get`。這類「屬性偽裝」很好做，因為值就是個字串。

## 世界一（三）：檢 Magisk 的痕跡

Magisk 是現在最主流的 systemless root 方案。App 針對性地找 Magisk 特徵：

```
Magisk 常被檢查的痕跡：
  · package：com.topjohnwu.magisk（Magisk App 本體，可改名但預設是這）
  · 路徑：/sbin/.magisk、/data/adb/magisk、/data/adb/modules
  · Magisk 的 unix socket（早期版本 abstract socket 名含 "magisk"）
  · mount 資訊：/proc/self/mountinfo 裡有 magisk 的 bind mount 痕跡
  · props：ro.debuggable、service.adb.root 等異常組合
```

檢查 mount 痕跡是進階招——Magisk 用 bind mount 把檔案「蓋」上去，`/proc/self/mountinfo` 會露餡。這也是 Magisk 早期最難藏的地方，催生了後來的 Zygisk + DenyList。

## 世界二：SafetyNet → Play Integrity（硬體背書）

當本地檢測全被繞光，App 開發者的終極武器是**把判斷外包給 Google**。演進：

- **SafetyNet Attestation**（舊，已於 2024 停用）：App 呼叫 SafetyNet API，Google 檢查裝置完整性回一張 JWT。核心是 `ctsProfileMatch`（裝置是否通過 CTS 相容性）與 `basicIntegrity`。**弱點**：早期只做軟體層檢查，Magisk 能靠 hide 過關；後期加了 **hardware-backed attestation**（用 TEE 硬體金鑰簽），軟體 hide 就失效了。
- **Play Integrity API**（現行，SafetyNet 的接班人）：回傳三個等級的判定：

```
Play Integrity 三個 verdict（由弱到強）：
 ┌──────────────────────────────────────────────────────────────┐
 │ MEETS_BASIC_INTEGRITY   基本：裝置在跑、非明顯竄改             │
 │ MEETS_DEVICE_INTEGRITY  裝置：通過 Google 認證的裝置 + 韌體    │
 │                         ← 這一級開始牽涉硬體背書，root 難過    │
 │ MEETS_STRONG_INTEGRITY  強：硬體背書 + 近期安全更新 + bootloader│
 │                         鎖定 ← 幾乎不可能在解鎖/root 裝置上過   │
 └──────────────────────────────────────────────────────────────┘
```

關鍵理解：**`DEVICE`/`STRONG` 級用 TEE（Trusted Execution Environment）裡出廠燒死的硬體金鑰簽章**。這把金鑰你拿不到、TEE 你進不去（那是比 root 更底層的安全世界，Ch 3 的安全模型延伸），所以**你無法偽造一張「通過」的簽章憑證**。這就是「世界二你贏不了正面」的物理原因。

**那為什麼還有人能過？** 2026 現況（**可能已變動**）：

1. **`BASIC` 級仍相對好過**——它不強依賴硬體，某些 App 只驗到 BASIC，Magisk hide + Play Integrity Fix 模組（靠偽造 device fingerprint 成一台已認證的乾淨裝置）常能過。
2. **`DEVICE` 級靠「盜用/租用真裝置的 keybox」**——社群曾用某些洩漏的、或未撤銷的裝置認證金鑰（keybox）讓 Play Integrity Fix 過 DEVICE 級。**但 Google 持續撤銷這些洩漏 keybox**，這條路愈來愈窄、時效極短。
3. **`STRONG` 級基本上繞不了**——需要 bootloader 鎖定 + 硬體背書，解鎖/root 的裝置過不了。碰到硬驗 STRONG 的 App，正規逆向路線是**別在 root 真機上跑，改用其他分析手段**（靜態、或不觸發 attestation 的路徑）。

> **時效性強烈警告（2026 現況）**：上面每一條「能過」的縫隙都建立在 Google 尚未收緊的前提上。keybox 被撤、Play Integrity 收緊 BASIC 級門檻、Play Integrity Fix 模組被針對——這些幾乎每季都在變。**寫作當下（2026 年中）可行的繞法，你讀到時可能已失效。** 唯一穩定的知識是上面那張「信任錨在哪」的圖：硬體背書的部分，本質上就是設計來讓你繞不過的。

## Magisk 的隱藏機制：DenyList / Zygisk / Shamiko

這三者是「世界一」繞過的實戰工具鏈，理解它們的分工很重要：

```
 Magisk root ──────────────────────────────────────────────
   │
   ├─ DenyList：一份「對這些 App 隱藏 root」的名單
   │            Magisk 在這些 App 的進程裡撤銷 root 掛載、還原被蓋的檔案
   │
   ├─ Zygisk：Magisk 的「在 Zygote 注入」框架（類 Riru）
   │          讓模組能在每個 App 進程 fork 出來的最早期執行程式碼
   │          → DenyList 的進階實作靠它
   │
   └─ Shamiko：一個 Zygisk 模組，把 DenyList 升級成更強的「白名單式」隱藏
              （Magisk 官方 DenyList 是黑名單邏輯，Shamiko 反過來，藏得更乾淨）
              → 對付「檢 mount 痕跡/更刁鑽偵測」的 App
```

分工白話版：

- **DenyList** 回答「要對哪些 App 隱藏？」——你把目標 App 加進去。
- **Zygisk** 是「怎麼在 App 進程最早期動手」的底層框架。
- **Shamiko** 是「藏得更徹底」的加強模組，尤其擋 mount 痕跡與更進階的偵測。

實戰配置（**未實測，理論預期行為**；需真機或支援 Magisk 的環境，AVD 上 Magisk 支援有限）：

```
1. Magisk 設定裡開啟 Zygisk（重開機生效）
2. 安裝 Shamiko 模組（zip 刷入），並在 Magisk 的 DenyList 裡「勾選」目標 App
   —— 注意：裝了 Shamiko 後，Magisk 的 "Enforce DenyList" 開關要「關掉」，
      因為 Shamiko 接管隱藏、把 DenyList 當白名單用（這是最多人踩的配置陷阱）
3. 重開機，用目標 App 測試 root 檢測是否還觸發
```

> **配置陷阱（最常踩）**：裝 Shamiko 後**不要**再開 Magisk 內建的 "Enforce DenyList"——兩者邏輯衝突（一黑名單一白名單），同時開會失效。Shamiko 的 README 明講這點。這個坑讓無數人「明明照做卻還被偵測」。

## 繞過策略的分層決策

碰到一個做 root 檢測的 App，別亂試。按這個順序判斷：

```
1. 它驗到哪一層？先搞清楚，別對 STRONG 級硬幹
   ├─ 只掃本地檔案/屬性（su/build tag/Magisk 路徑）
   │     → DenyList + Shamiko 藏；或 Frida hook File.exists/exec/getprop
   ├─ 檢 mount 痕跡 / 更刁鑽本地偵測
   │     → Shamiko（比裸 DenyList 藏得徹底）
   ├─ Play Integrity BASIC
   │     → Play Integrity Fix 模組（時效性，可能失效）
   ├─ Play Integrity DEVICE
   │     → 需未撤銷 keybox，窄且短命；評估值不值得
   └─ Play Integrity STRONG
         → 正面繞不了。改靜態分析，或找不觸發 attestation 的功能路徑
```

**這個決策樹的價值在於「早放棄硬幹 STRONG」**——你可以省下好幾天。逆向的時間該花在能贏的戰場上。

## 對比與取捨：各層檢測與繞過

| 檢測 | 信任錨 | 偵測強度 | 繞法 | 繞過難度 |
|---|---|---|---|---|
| 掃 su/busybox 路徑 | 本地檔案 | 低 | DenyList / hook `File.exists` | 低 |
| 檢 build tag（test-keys） | 本地屬性 | 低 | hook `Build.TAGS`/getprop | 低 |
| 檢 Magisk 路徑/package | 本地檔案 | 中 | DenyList + 改 Magisk App 名 | 低中 |
| 檢 mount 痕跡 | 本地 mountinfo | 中高 | Shamiko | 中 |
| Play Integrity BASIC | 軟體 + 部分硬體 | 高 | Play Integrity Fix（時效） | 中高 |
| Play Integrity DEVICE | 硬體 keybox | 很高 | 未撤銷 keybox（窄、短命） | 很高 |
| Play Integrity STRONG | 硬體 + bootloader 鎖 | 極高 | 正面繞不了 | 幾乎不可能 |

**核心取捨**：本地檢測（前四行）你幾乎穩贏，工具成熟（Shamiko）。一跨進 Play Integrity 的硬體背書，成本陡升、且**你的成敗取決於 Google 端的政策而非你的技術**——這是逆向者少數「技術再強也無解」的領域。認清這點是專業判斷力的一部分。

## 踩雷集錦

1. **裝了 Shamiko 卻還開 "Enforce DenyList"**：兩者邏輯衝突，隱藏失效。裝 Shamiko 後要**關掉**內建 Enforce DenyList，讓 Shamiko 用白名單接管。這是 Magisk 隱藏配置的頭號坑。
2. **hook 了 Java `File.exists` 卻沒攔到**：App 用 native `access()`/`stat()` 掃 su，或直接 `Runtime.exec("su")`。Java hook 攔不到 native 掃描，得補 hook libc 或 `Runtime.exec`。**先確認它用哪招再 hook。**
3. **對 Play Integrity STRONG 級死磕**：耗一週還過不了，因為它錨定硬體+bootloader 鎖，root 裝置物理上過不了。碰到硬 STRONG，正確反應是換策略（靜態、或繞開觸發點），不是繼續 hook。
4. **以為 SafetyNet 還能用**：SafetyNet 已於 2024 停用，全面轉 Play Integrity。看到老教學講「過 SafetyNet」要知道那套 API 已死，現況是 Play Integrity。
5. **在 AVD 上測 Play Integrity**：AVD（尤其非 Play 認證的 Google APIs image）本來就過不了裝置完整性，測不出真機的行為。Play Integrity 相關的繞過要在**真機 + Magisk** 上驗，AVD 只適合測本地檔案/屬性層的檢測。
6. **把 root 檢測繞過就以為萬事俱備**：root 檢測跟反調試（Ch 30）、完整性校驗（Ch 32）是**各自獨立的防線**。過了 root 檢測，App 還可能因為你 hook 了它、或改了它而觸發別的防護。防線要一條條拆。

## 進階：再往深一層

- **硬體背書的信任鏈**：Play Integrity DEVICE/STRONG 的簽章來自裝置的 **attestation key**，這把金鑰在出廠時由廠商用 Google 認證的憑證鏈簽發、燒進 TEE。整條鏈一路上溯到 Google 的根憑證。你之所以繞不了，是因為要偽造就得偽造這整條由硬體 root of trust 錨定的鏈——這正是 Ch 3 安全模型裡「信任要有物理根」的體現。想深挖讀 Android 的 Key Attestation 文件。
- **keybox 洩漏與撤銷的貓鼠遊戲**：DEVICE 級曾被「用洩漏的真裝置 keybox」繞過，Google 的反制是維護撤銷清單（CRL）持續作廢已洩漏的 keybox。這是純粹的軍備競賽，攻防雙方都在 Google 的規則場裡玩——這也是為什麼這條路「時效性」極強。
- **Zygisk vs 舊 Riru**：Magisk 早期靠 Riru（hook `libmemtrack` 之類系統庫在 Zygote 注入），Magisk 後來內建 Zygisk 取代它。理解「在 Zygote fork 出 App 進程的最早期就注入」為什麼是隱藏 root 的最佳時機——因為那時 App 自己的偵測程式碼都還沒開始跑，你有機會先把環境佈置乾淨。這與 Ch 30「偵測在 constructor 裡跑得比你早」是同一個時間競賽的一體兩面。
- **Play Integrity Fix 的原理**：它不是「破解」硬體背書，而是**偽造一份看起來像某台已認證乾淨裝置的 device fingerprint / props**，讓軟體層的 BASIC 檢查通過。它動不了 DEVICE/STRONG 的硬體簽章——理解它「能過 BASIC、過不了 STRONG」正好印證兩個世界的分界。

## 動手練習

1. 在本 repo 沙箱跑 `check_su.py` 與 `check_buildtags.py`，把 `SU_PATHS` 指向一個你手動建立的真檔，確認偵測翻成 `True`；改 build tag 字串試 `release-keys`/`test-keys`。**目的**：在無 Android 環境把「本地檢測 = 掃檔案/比字串」的本質吃透。
2. 畫出本章「兩個世界的信任模型」那張圖，不看筆記，並在旁邊標註：哪些檢測落在世界一（能藏）、哪些落在世界二（藏不掉），以及各自的繞法。**目的**：把「能不能繞取決於信任錨在哪」內化成直覺。
3. （需真機 + Magisk）配置 Zygisk + Shamiko + DenyList 勾選一個銀行類 App（自己有帳號的），驗證它從「偵測到 root 拒啟動」變成「正常啟動」。**注意 Shamiko 要關 Enforce DenyList 的坑。目的**：完成一次真實的本地層 root 隱藏。

## 本章重點整理

- root 檢測分**兩個世界**：世界一（本地掃 su/build tag/Magisk 痕跡，**你能藏**）、世界二（Play Integrity 硬體背書，**你藏不掉，只能靠 Google 端縫隙**）。
- 本地檢測靠找 **`su`/`busybox` 路徑**、**`test-keys` build tag**、**Magisk 的 package/路徑/mount 痕跡**；繞法是 Magisk **DenyList + Zygisk + Shamiko** 或 Frida hook `File.exists`/getprop。
- **Shamiko 要關掉 Enforce DenyList**（白名單 vs 黑名單邏輯衝突），這是頭號配置陷阱。
- Play Integrity 三級：**BASIC（軟體，尚可繞）→ DEVICE（硬體 keybox，窄且短命）→ STRONG（硬體+bootloader 鎖，正面繞不了）**；SafetyNet 已於 2024 停用。
- 繞過現況**時效性極強**（keybox 被撤、模組被針對），2026 現況隨時可能變；唯一穩定的是「硬體背書設計上就繞不了」這條原理。

## 自我檢核

- [ ] 不看筆記，能畫出「本地檢測 vs 硬體背書」兩個世界，並說出各自的信任錨與能否繞
- [ ] 能列出至少三種本地 root 檢測（su 路徑 / build tag / Magisk 痕跡）與各自繞法
- [ ] 能解釋 DenyList、Zygisk、Shamiko 三者的分工，以及 Shamiko 的配置陷阱
- [ ] 能說出 Play Integrity 三個 verdict 等級，以及為什麼 STRONG 級正面繞不了
- [ ] 知道 SafetyNet 已停用、現況是 Play Integrity，且繞過現況有強時效性
- [ ] 碰到硬驗 STRONG 的 App，知道正確反應是換策略而非死磕

## 延伸閱讀

- **[OWASP MASTG — Testing Root Detection](https://mas.owasp.org/MASTG/techniques/android/MASTG-TECH-0032/)**
  - **讀哪裡**：Android root detection 的測試案例，含 su 路徑、build tag、package 檢查的清單
  - **學什麼**：本章本地檢測的系統化測試方法，補齊你沒想到的檢測點
  - **關聯**：本章講原理與繞法，MASTG 給你完整的「App 可能怎麼檢」清單
- **[Magisk 官方文件 — DenyList / Zygisk](https://topjohnwu.github.io/Magisk/)**
  - **讀哪裡**：DenyList、Zygisk 的說明，以及 hide 機制的設計
  - **學什麼**：DenyList 到底做了什麼（撤銷掛載、還原檔案）、Zygisk 為什麼能在 Zygote 注入
  - **關聯**：本章「Magisk 隱藏機制」的一手權威，配置細節以官方為準
- **[Shamiko GitHub repo](https://github.com/LSPosed/LSPosed.github.io/releases)**（Shamiko 發布頁）
  - **讀哪裡**：README 的使用說明，特別是「裝了 Shamiko 要關 Enforce DenyList」那段
  - **學什麼**：Shamiko 相對裸 DenyList 強在哪、白名單模式怎麼設定
  - **關聯**：本章頭號配置陷阱的原始出處，實作前務必讀
- **[Play Integrity API 官方文件](https://developer.android.com/google/play/integrity)**
  - **讀哪裡**：三個 integrity verdict 的定義、device/strong integrity 的要求
  - **學什麼**：從防禦方視角看每個等級要求什麼（bootloader 鎖、安全更新），反推為什麼難繞
  - **關聯**：本章「世界二」的官方定義，讀它才懂 STRONG 級的物理門檻
- **[HackTricks — Root Detection Bypass](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/index.html)**
  - **讀哪裡**：root detection bypass 段落，含 Frida hook 與 Magisk hide 的實戰指令
  - **學什麼**：本章繞法對應的可複製命令與現成 Frida 腳本
  - **關聯**：本章思路的落地指令集，卡住時來查

過了 root 檢測，App 還有一道專門盯著「你有沒有動過我」的防線——**完整性校驗**。它自己算簽名憑證的 hash、算 DEX/`.so` 的 CRC，跟出廠值比對，一旦你重打包或 hook 就露餡。下一章我們拆它怎麼校驗、藏在哪、怎麼定位與繞過。

→ [Ch 32 完整性校驗對抗](./32-integrity-checks.md)
