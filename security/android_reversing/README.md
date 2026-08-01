# 安卓逆向學習筆記：從 APK 到 ART 底層的完整拆解

> 給有 ARM / binary exploitation / Frida 底子、想做**安全研究與 App 破解**的工程師。

拿到一個陌生的 Android App——帶加固殼、關鍵邏輯藏在 native `.so`、走 HTTPS 還做了 SSL pinning——你能不能把它拆到：脫殼還原 DEX、逆出 native 簽名演算法、繞過反調試把它跑起來、抓包還原出它跟伺服器對話的協議、最後寫出一支能重放請求的 PoC？這門課就是把你練到這個程度。

四條主軸全部拉滿：**App 層還原（DEX/Smali/Java）→ 動態插樁（Frida/Xposed）→ Native 逆向（.so/ARM64/JNI）→ 加固對抗（脫殼/反調試/混淆）→ ART 系統底層**。不是「教你按幾個工具按鈕」，是把每一層的底層機制講到你能自己寫工具、繞未知的防護。

## 為什麼學這個？

- **安卓是全世界最大的攻擊面**：幾十億台裝置、幾百萬個 App，每一個都是黑盒。會逆向，你才有能力驗證一個 App 到底在幹嘛、有沒有偷資料、它的「安全設計」是真的還是紙糊的。
- **它逼你把整個系統棧打通**：Java runtime、Dalvik/ART、JNI、ARM64、ELF、Linux 進程模型、SELinux——安卓逆向是少數能一次踩過所有這些層的題材。學完你對「一個程式怎麼從打包到執行」的理解會脫胎換骨。
- **安全職涯的硬通貨**：App 安全評估、SDK 稽核、malware 分析、漏洞挖掘、紅隊——這些工作的入場券就是「能逆得動」。這門課的 final 直接產出一份可放進作品集的逆向報告。

## 先修知識

- **C / 指標 / 記憶體佈局**（程度：能讀懂 struct、指標運算、stack frame）——native 層與 ART 內部大量用到
- **ARM64 組合語言基礎**（程度：知道 x0–x30、`bl`/`ret`、呼叫慣例即可；Ch 20 會補齊逆向需要的部分）——你已有 arm 課底子，這裡只補逆向視角
- **Java 或任一 OOP 語言**（程度：class/method/繼承/介面看得懂）——App 層都是 Java/Kotlin
- **Linux 命令列與進程概念**（程度：會用 shell、知道 pid/fd/signal）
- 沒有也沒關係的：Kotlin（會 Java 就讀得懂反編譯輸出）、Frida（Ch 12–15 從零教）、IDA/Ghidra（Ch 22 從零教逆 `.so`）

## 環境

- **主力**：Android Studio 內建 **AVD（x86_64 emulator，Android 13 / API 33，可 root 的 Google APIs image）**
- **工具鏈**：`adb` / `apktool` / `jadx` / `Frida` + `frida-server` / `objection` / IDA 或 Ghidra / `mitmproxy`
- **關於實測誠實標注**：Frida hook、smali 重打包、DEX 解析這類我在 AVD 上實際跑得動的，範例都是「你照著跑」；需要**特定真機、廠商殼、動態 IDA remote** 才能重現的段落，會明確標「**未實測，理論預期行為**」並給你在自己環境驗證的步驟。不會拿沒跑過的輸出裝成跑過的。

## 課程地圖（42 章 + 5 練習 + 1 final）

### Part 1 — 平台與工作台（Ch 0–3）
- [Ch 0 環境搭建：AVD、adb、frida-server 與逆向工作台](./00-environment-setup.md)
- [Ch 1 安卓逆向全貌：攻擊者視角與工作流](./01-android-re-overview.md)
- [Ch 2 APK 結構解剖：從 zip 到簽名 scheme](./02-apk-anatomy.md)
- [Ch 3 執行與安全模型：Zygote、沙箱、權限、SELinux](./03-execution-security-model.md)

### Part 2 — App 層逆向：DEX / Smali / Java（Ch 4–10）
- [Ch 4 Dalvik bytecode 與 DEX 格式深挖](./04-dalvik-dex-format.md)
- [Ch 5 Smali 語法完整導覽](./05-smali-language.md)
- [Ch 6 apktool：反編譯、改 smali、回編譯、重簽名](./06-apktool-rebuild.md)
- [Ch 7 Jadx 與 Java 反編譯：原理與限制](./07-jadx-java-decompile.md)
- [Ch 8 讀懂反編譯輸出：匿名類、lambda、協程陷阱](./08-reading-decompiled-output.md)
- [Ch 9 資源與 Manifest 逆向](./09-resources-manifest-re.md)
- [Ch 10 Smali patch 實戰：繞校驗與改邏輯](./10-smali-patching.md)
- [練習 A：手改 smali 破 crackme](./practice-a-smali-crackme.md)

### Part 3 — 動態插樁與 Hook（Ch 11–18）
- [Ch 11 為什麼動態贏靜態](./11-dynamic-beats-static.md)
- [Ch 12 Frida 架構與原理](./12-frida-architecture.md)
- [Ch 13 Frida hook Java 層](./13-frida-hook-java.md)
- [Ch 14 Frida hook native 層](./14-frida-hook-native.md)
- [Ch 15 Frida 進階：Stalker、掃描、dump](./15-frida-advanced-stalker.md)
- [Ch 16 Xposed / LSPosed：持久化 hook](./16-xposed-lsposed.md)
- [Ch 17 SSL Pinning 與抓包](./17-ssl-pinning-bypass.md)
- [Ch 18 協議還原：從抓包到簽名演算法](./18-protocol-recovery.md)
- [練習 B：用 Frida 還原請求簽名演算法](./practice-b-frida-signature.md)

### Part 4 — Native 層逆向：.so / ARM64 / JNI（Ch 19–25）
- [Ch 19 JNI 機制：Java 與 native 的邊界](./19-jni-mechanism.md)
- [Ch 20 ARM64 逆向必備](./20-arm64-for-re.md)
- [Ch 21 ELF / .so 結構](./21-elf-so-structure.md)
- [Ch 22 IDA / Ghidra 逆 .so](./22-ida-ghidra-so.md)
- [Ch 23 native 演算法識別與加密還原](./23-native-algorithm-id.md)
- [Ch 24 動態調試 native](./24-native-dynamic-debug.md)
- [Ch 25 hook native 進階：inline / PLT hook](./25-native-hooking.md)
- [練習 C：逆一個把簽名搬進 .so 的 App](./practice-c-native-signature.md)

### Part 5 — 對抗：加固 / 混淆 / 反調試（Ch 26–32）
- [Ch 26 混淆技術全譜](./26-obfuscation-landscape.md)
- [Ch 27 OLLVM 與 native 混淆的去混淆](./27-ollvm-deobfuscation.md)
- [Ch 28 加固加殼原理與分代](./28-packers-overview.md)
- [Ch 29 脫殼技術](./29-unpacking-techniques.md)
- [Ch 30 反調試、反 Frida、反注入](./30-anti-debug-anti-frida.md)
- [Ch 31 root / Magisk 檢測與繞過](./31-root-magisk-detection.md)
- [Ch 32 完整性校驗對抗](./32-integrity-checks.md)
- [練習 D：脫殼 + 繞反調試把 App 跑起來](./practice-d-unpack-antidebug.md)

### Part 6 — ART / Dalvik 系統底層（Ch 33–37）
- [Ch 33 Dalvik 到 ART 的演進](./33-dalvik-to-art.md)
- [Ch 34 ART runtime 內部](./34-art-runtime-internals.md)
- [Ch 35 ClassLoader 機制與熱補](./35-classloader-hotpatch.md)
- [Ch 36 從 ART 內部脫殼與 hook](./36-art-unpacking-hook.md)
- [Ch 37 Zygote、進程、SELinux 對逆向的影響](./37-zygote-process-selinux.md)
- [練習 E：寫一個 ArtMethod-level 主動調用脫殼器（mini FART）](./practice-e-mini-fart.md)

### Part 7 — 整合實戰（Ch 38–41）
- [Ch 38 完整逆向方法論：陌生 App 的 SOP](./38-re-methodology.md)
- [Ch 39 案例：拆一個綜合防護的類真實 App](./39-case-study-hardened-app.md)
- [Ch 40 自動化：Frida 腳本庫與批量分析](./40-automation-frida-scripts.md)
- [Ch 41 防禦視角：懂防守才更會攻](./41-defense-perspective.md)
- [Final Project：綜合防護目標 App 完整拆解](./final-project-hardened-app-teardown.md)

## 學習方式建議

1. **讀完一章就在 AVD 上動手**：這門課的每個工具、每個 hook 腳本都要自己敲一遍。看別人 dump 記憶體跟自己 dump 出來，理解差一個數量級。
2. **故意把它弄壞**：改 smali 回編譯簽名失敗、Frida 腳本 attach 不上、脫殼脫出半殘的 DEX——這些失敗本身就是教材。每章的「踩雷集錦」多半是我踩過的。
3. **靜態與動態互相印證**：靜態看到一個可疑函式，就用 Frida hook 它印出參數；動態發現一個關鍵字串，就回 Jadx 搜它。兩邊對照才不會被混淆騙。
4. **合法邊界**：只逆你有權分析的 App（自己寫的、開源的、CTF 題、明確授權的評估目標）。這門課的目標是安全研究與防禦理解，不是幫你破解別人的付費牆。

## 精選資料庫

整門課最值得反覆參照的資源，每章「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **《Android Security Internals》** — Nikolay Elenkov（No Starch，2014）
  - 安卓安全模型的權威書；權限、簽名、進程沙箱、SELinux 的機制講得最清楚。雖然版本偏舊，核心架構到今天沒變，Part 1 與 Part 6 大量參照
- **[官方 AOSP 原始碼](https://cs.android.com/)**（Android Code Search）
  - ART / Zygote / 簽名驗證 行為的最終仲裁。遇到「這到底怎麼實作的」就直接查 `art/runtime/` 與 `frameworks/base/`
- **[Frida 官方文件](https://frida.re/docs/)**
  - 動態插樁全課的主要參考；JavaScript API、Interceptor、Stalker、RPC 都在這

### 推薦論文 / 技術報告

- **[Android Dalvik/ART 官方設計文件](https://source.android.com/docs/core/runtime)** — AOSP
  - ART 的 AOT/JIT 混合、dex2oat、oat 檔格式，Part 6 的一手依據
- **[DEX bytecode 格式規格](https://source.android.com/docs/core/runtime/dex-format)** — AOSP
  - DEX 檔每個欄位的定義；Ch 4 手撕 DEX 時攤開這頁對照

### 推薦部落格 / 社群

- **[HackTricks — Mobile Apps Pentesting](https://book.hacktricks.wiki/en/mobile-pentesting/android-app-pentesting/index.html)**
  - 最全的安卓逆向/滲透 cheat sheet；每個技術都有可複製的指令，卡住時先來這查一遍
- **[OWASP MASTG（Mobile App Security Testing Guide）](https://mas.owasp.org/MASTG/)**
  - 業界標準的行動 App 安全測試方法論；逆向、反調試、pinning、儲存安全的系統化測試流程
- **[Frida CodeShare](https://codeshare.frida.re/)**
  - 社群 Frida 腳本庫；SSL pinning bypass、反調試繞過的現成腳本，讀它們的原始碼比自己從零寫學得快

### 讀完本課之後

- **《The Art of Mac Malware》/《Practical Binary Analysis》** — 把二進位分析推得更深，native 逆向的通用能力
- **[Google Project Zero blog](https://googleprojectzero.blogspot.com/)** — 世界頂級的行動漏洞研究，讀他們怎麼從逆向走到 0day，這門課之後的天花板在這裡
