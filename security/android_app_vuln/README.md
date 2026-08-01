# 安卓 App 漏洞分析學習筆記：從逆向到 bug bounty

> 給會逆向、想系統化「找 App 漏洞」的工程師。接 [android_reversing](../android_reversing/README.md) 的逆向技能，往漏洞挖掘與 App 安全評估走。

會逆向讓你「看得懂 App」，但看得懂不等於找得到洞。這門課教的是**漏洞類型學 + 方法論**：拿到一個 App，怎麼系統化地把它的攻擊面掃一遍、哪些是真的可利用的洞、怎麼從「可疑」驗證到「可打」、怎麼寫成一份能拿去 bug bounty 或評估報告的東西。全程以 OWASP **MASVS / MASTG** 為骨架，靶場實戰，drozer / MobSF / Frida / semgrep 上手。

## 為什麼學這個？

- **逆向的下一步**：android_reversing 教你拆 App，這門教你「拆完之後找什麼」。少了這塊，逆向能力沒有變現的出口。
- **App 漏洞是最大的實戰場**：幾百萬個 App，多數團隊安全意識參差，元件暴露、WebView RCE、PendingIntent 劫持、Provider 洩漏這些類別到 2026 年還在大量出現。這是 bug bounty 與滲透測試投報率最高的一塊。
- **方法論比工具重要**：工具會過時，但「攻擊面怎麼枚舉、一個可疑點怎麼驗證成真洞、怎麼寫報告」這套思路不會。這門課把它變成你能重複執行的流程。

## 先修知識

- **安卓逆向基礎**（強烈建議先修 [android_reversing](../android_reversing/README.md) 的 Part 1–3）：讀 smali/Java、Frida hook、看懂 Manifest。這門課大量假設你能逆
- **Java / Android 元件模型**（程度：知道 Activity/Service/Broadcast/Provider 與 Intent 是什麼）
- **基本 Web 安全概念**（程度：知道 SQLi、XSS、path traversal 的原理；WebView 那章會用到）
- 沒有也沒關係的：drozer / MobSF（Ch 0 從零教）、MASTG（Ch 1 導覽）

## 環境

- **AVD**（x86_64, google_apis, Android 13 / API 33，可 root）+ 選配真機
- **工具**：drozer、MobSF、Frida、objection、jadx、apktool、apksigner、adb、`semgrep`（+ mobsfscan）、apkleaks、mitmproxy
- **靶場**：DIVA、AndroGoat、InsecureBankv2、Pivaa、OWASP MASTG 的 crackme/靶
- **實測誠實標注**：能在 AVD/靶場上跑通的（drozer 打元件、Frida 觸發、semgrep 掃）標「你照著跑」；需要特定靶版本或真機的段落標「未實測，理論預期」並給驗證步驟。**只分析你有權測試的目標**（自己的 App、開源靶、明確授權的評估對象、有 scope 的 bug bounty 專案）。

## 課程地圖（16 章 + 3 練習 + 1 final）

### Part 1 — 基礎與方法論（Ch 0–2）
- [Ch 0 環境搭建：AVD、drozer、MobSF、Frida、靶場](./00-environment-setup.md)
- [Ch 1 App 攻擊面全貌與 MASVS/MASTG 方法論](./01-attack-surface-masvs.md)
- [Ch 2 四大元件與 IPC 安全模型](./02-components-ipc-model.md)

### Part 2 — 元件與 IPC 漏洞（Ch 3–6）
- [Ch 3 exported 元件濫用](./03-exported-components.md)
- [Ch 4 Intent redirection 與 confused deputy](./04-intent-redirection.md)
- [Ch 5 PendingIntent 劫持](./05-pendingintent-hijacking.md)
- [Ch 6 ContentProvider 漏洞：SQLi、path traversal、openFile](./06-contentprovider-vulns.md)
- [練習 A：drozer 打靶找元件漏洞](./practice-a-drozer-hunt.md)

### Part 3 — deeplink / WebView / 前端面（Ch 7–9）
- [Ch 7 Deeplink / App Link 劫持與 task hijacking](./07-deeplink-task-hijacking.md)
- [Ch 8 WebView 攻擊面](./08-webview-attacks.md)
- [Ch 9 網路層漏洞：明文、pinning 缺失、network_security_config 誤配](./09-network-layer-vulns.md)
- [練習 B：WebView + deeplink 鏈成 RCE](./practice-b-webview-deeplink-rce.md)

### Part 4 — 資料 / 密碼 / 儲存（Ch 10–12）
- [Ch 10 不安全儲存](./10-insecure-storage.md)
- [Ch 11 密碼學誤用](./11-crypto-misuse.md)
- [Ch 12 憑證與 secret 洩漏](./12-secret-leakage.md)

### Part 5 — 權限 / 進階 / 自動化（Ch 13–15）
- [Ch 13 自訂 permission 缺陷與簽名權限](./13-custom-permission-flaws.md)
- [Ch 14 路徑穿越、zip slip 與不安全下載](./14-path-traversal-zipslip.md)
- [Ch 15 自動化掃描與報告撰寫](./15-automation-reporting.md)
- [練習 C：對靶 App 出完整評估報告](./practice-c-full-assessment.md)
- [Final Project：完整 App 安全評估（MASTG 導向）](./final-project-app-security-assessment.md)

## 學習方式建議

1. **每一類漏洞都在靶場打一遍**：讀懂原理不算會，能在 DIVA/AndroGoat 上把它打出來才算。每章都指定對應的靶。
2. **從「可疑」到「可打」**：找到一個 exported 元件不是結束，能構造出觸發漏洞的 `adb`/drozer/Frida PoC 才是。這門課的每個漏洞都要求你做到 PoC。
3. **寫下來**：每打一個洞就照報告模板寫一段（影響、重現步驟、PoC、修復建議）。final 就是把這些拼成一份完整評估報告。
4. **合法邊界**：只測有授權的目標。bug bounty 要看清 scope，別越界。

## 精選資料庫

### 必讀基礎

- **[OWASP MASTG（Mobile App Security Testing Guide）](https://mas.owasp.org/MASTG/)**
  - 整門課的主骨架；每一類漏洞的系統化測試流程都在這，遇到不確定「這算不算洞、怎麼測」回這裡
- **[OWASP MASVS（Mobile App Security Verification Standard）](https://mas.owasp.org/MASVS/)**
  - 安全需求的分級標準；報告寫作與評估範圍的依據

### 工具與靶場

- **[drozer](https://github.com/WithSecureLabs/drozer)** — WithSecure
  - 安卓元件攻擊面的瑞士刀；Part 2 的主力，枚舉與攻擊 exported 元件
- **[MobSF（Mobile Security Framework）](https://github.com/MobSF/Mobile-Security-Framework-MobSF)**
  - 自動化靜動態掃描；Ch 15 自動化與快速偵察會用
- **[DIVA / AndroGoat / InsecureBankv2](https://github.com/payatu/diva-android)** 等靶場
  - 每章漏洞的練習對象；刻意埋洞的 App，安全地練手

### 推薦部落格 / 研究

- **[HackTricks — Android Pentesting](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/index.html)**
  - 最全的實戰 cheat sheet；每類漏洞都有可複製指令
- **[Oversecured blog](https://blog.oversecured.com/)**
  - 高品質的安卓 App 漏洞研究；PendingIntent 劫持、intent redirection、Provider 漏洞的深度案例，本課多章的一手參考

### 讀完本課之後

- **[android_exploitation](../android_exploitation/README.md)**（本 repo）— 從 App 層往系統/native 利用走：Binder LPE、scudo/MTE、fuzzing、CVE 研究
- **[Google Bug Hunters — Android & Google Devices](https://bughunters.google.com/)** — 真實把技能變現的地方
