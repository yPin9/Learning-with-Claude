# Final Project — 24 小時 OSCP 模擬考試

> 目標：在真實考試壓力下完整走過一次 OSCP 流程——從開始計時到提交報告，找出你還有哪些盲點。

## 為什麼要模擬考試

讀完理論和打過單台靶機，不代表你能在 24 小時壓力下有條理地拿 70 分。

模擬考試驗證：

```
✓ 時間分配（你真的知道什麼時候該換機器）
✓ 截圖習慣（你不會等到最後才發現沒截圖）
✓ 筆記組織（你能在 H+20 快速整理報告）
✓ 卡住的心態（你能在卡住 45 分鐘後換方向）
✓ 體力管理（24 小時連續工作的真實感受）
```

## 模擬考試機器選擇

### 推薦組合 A（全 Proving Grounds）

使用 Proving Grounds Practice 機器，風格最接近 OSCP 考試。

```
獨立機器 3 台：
  Linux Easy：Bratarina 或 Gaara
  Linux Medium：Pelican 或 Sybaris
  Windows Medium：Nickel 或 Authby

AD 鏈（模擬用）：
  如果有 PG Practice 的 AD 機器，選一組；
  否則用 HTB Forest 代替（AS-REP → WriteDACL → DCSync）
```

### 推薦組合 B（HTB 混合）

```
獨立機器 3 台：
  Linux Easy：Shocker（ShellShock）
  Linux Medium：Cronos（DNS + SQLi + Cron）
  Windows Medium：Optimum（HFS RCE + 提權）

AD 鏈：
  HTB Forest（完整 AD 鏈，有詳細步驟可對照）
```

## 模擬考試規則

### 必須遵守的規則

```
□ 計時 24 小時，時間到強制停筆
□ 模擬期間不能看 Writeup（包括 Google 搜尋已知解法）
□ Metasploit 只能用一次（選你最需要的時候用）
□ 每拿到一個 shell 立刻截圖（不要「等等再截」）
□ proof.txt 必須 cat proof.txt && ifconfig 一起截
□ 保留所有 terminal 輸出到檔案（用 tee 或 script）
```

### 允許的事

```
✓ Google 查工具使用方式（不是查這台機器怎麼打）
✓ 查 GTFOBins 提權技術
✓ 查 HackTricks 技術參考
✓ 查 msfvenom payload 語法
✓ 查 impacket 工具用法
✓ 中途休息（模擬真實考試的疲勞管理）
```

## 開始前的準備清單

### 目錄結構

```bash
mkdir -p ~/oscp_sim/{machine1,machine2,machine3,ad_chain}/{nmap,exploit,loot,screenshots}
```

### 工具確認

```bash
# 確認工具都在位
ls ~/tools/
# 應該有：linpeas.sh, winpeas.exe, chisel, chisel.exe, nc.exe,
#         PowerUp.ps1, SharpHound.exe, PrintSpoofer.exe

# 確認 wordlist
ls /usr/share/wordlists/rockyou.txt
ls /usr/share/seclists/Discovery/Web-Content/

# 確認 impacket
which secretsdump.py || python3 -c "import impacket"
```

### Terminal 記錄設定

```bash
# 開始記錄所有 terminal 輸出（可選）
script -a ~/oscp_sim/terminal_log.txt

# 或每個機器目錄用 tee
nmap -p- 10.10.10.x | tee ~/oscp_sim/machine1/nmap/all-ports.txt
```

## 模擬考試時間表

### 計時開始

```
H+0    開始計時。記錄開始時間。
       並行啟動所有機器的 nmap all-ports 掃描：
       
       for ip in 10.10.10.1 10.10.10.2 10.10.10.3 10.10.10.4; do
           nmap -p- --min-rate 5000 -T4 $ip -oN ~/oscp_sim/machine${n}/nmap/all-ports.txt &
       done

H+0.5  所有機器有基本 port 清單
       開始 targeted 掃描 + 服務分析

H+1    所有機器有服務清單
       選一台「最有機會」的機器開始打

H+4    目標：拿到第一台 shell（local.txt 截圖）
       如果沒拿到：換機器，記錄試過什麼

H+5    提權第一台

H+7    開始 AD 鏈

H+10   目標：AD 鏈至少拿到 DC 以外的機器

H+12   強制 20 分鐘休息（不管在哪，站起來走走）

H+13   繼續打剩下的機器

H+20   停止打機器。開始整理截圖和筆記。

H+22   截圖整理完畢，開始填報告模板

H+24   計時結束。
```

## 截圖規範

每台機器的必要截圖清單（打勾追蹤）：

```
Machine 1（_______ IP: _______）
□ nmap 輸出（顯示開放 port 和版本）
□ 漏洞發現截圖（searchsploit 或版本資訊）
□ Exploit 執行截圖
□ whoami（初始 shell 身份）
□ cat local.txt && ifconfig（flag + IP 同時可見）
□ 提權發現截圖（sudo -l / linPEAS 結果）
□ 提權執行截圖
□ whoami（root/SYSTEM 身份）
□ cat /root/proof.txt && ifconfig

Machine 2（_______ IP: _______）
□ [同上格式]

Machine 3（_______ IP: _______）
□ [同上格式]

AD 鏈
□ AD 第一台 whoami + ifconfig + local.txt
□ 橫向移動截圖（hash 或 ticket）
□ DC shell 截圖
□ cat proof.txt && ifconfig（DC 上，以 SYSTEM/DA 身份）
```

## 報告撰寫階段（H+24 後，24 小時內）

### 報告填寫流程

```
1. 整理截圖（重命名為清楚的名稱）
   machine1_nmap.png
   machine1_initial_shell.png
   machine1_local_txt.png
   machine1_privesc_sudo.png
   machine1_proof_txt.png

2. 按 OffSec 格式逐台填入
   - 服務枚舉表格（port / service / version）
   - 初始立足說明 + 截圖
   - 提權說明 + 截圖

3. 填 AD 鏈部分（每台一個子節）

4. 轉成 PDF（用 OffSec 官方模板，或 Pandoc）

5. 檢查：
   □ 每個 proof.txt 截圖都有 IP
   □ 每個步驟都有截圖支撐
   □ 沒有遺漏某台機器的節
```

## 模擬結束後的自我評估

打完模擬考試後，誠實回答：

### 分數估算

```
□ 拿到了幾個 local.txt？× 10 = _____ 分
□ 拿到了幾個 proof.txt？× 10 = _____ 分
□ AD 鏈：拿到 1台 = 10分, 2台 = 20分, DC = 40分
□ 估計總分：_____ 分

通過標準：70 分
```

### 技術盲點分析

```
□ 有哪台機器完全沒思路？
  → 是什麼服務？去找對應的 HTB 機器練
  
□ 有哪個提權路徑想到了但執行失敗？
  → 是工具問題、還是理解問題？

□ 截圖有沒有遺漏？
  → 漏了哪個環節？考試時這樣會扣分

□ 時間分配合理嗎？
  → 有沒有在某台機器上死磕超過 2 小時？
```

### 報告質量評估

```
□ 報告完整度：_____ / 100
□ 步驟清晰度（別人能重現嗎？）
□ 截圖清晰度和完整度
□ 有沒有在截止前完成？
```

## 盲點對應練習

根據模擬結果，針對性補練：

| 問題 | 對應練習 |
|------|---------|
| Web 漏洞看不出來 | 重打 Beep, Cronos，搭配 HackTricks Web 章節 |
| Windows 提權沒思路 | 重看 Ch 25–28，打 Optimum + Bounty |
| AD 完全沒進展 | 先做 TryHackMe Attacktive Directory，再打 HTB Forest |
| BoF 超過 1 小時 | THM Buffer Overflow Prep 再練 5 個 |
| 截圖習慣差 | 重打一台機器，強制要求每步驟截圖後才繼續 |
| 報告寫不完 | 模擬期間就開著報告模板，邊打邊填 |

## 最後提醒

```
模擬考試的目的不是「在模擬中拿高分」。

目的是找出你的盲點，讓你在真實考試前修正。

模擬中卡住、失敗、報告寫不完——這些在模擬中發生比在真實考試中發生好得多。

誠實評估，針對性補強，再去考試。
```

## 自我檢核

- [ ] 完成一次 24 小時模擬考試（計時到底，不中途棄賽）
- [ ] 模擬結束後估算分數（達到 70 分說明準備充足）
- [ ] 寫出一份完整的模擬報告（即使機器沒全打完）
- [ ] 根據模擬結果找出 2 個最大的技術盲點，制定補練計劃
- [ ] 補練完成後，信心大於 70 分 → 預約考試

---

**課程完成。**

從枚舉到 AD 完整鏈，從 BoF 到報告撰寫，這 42 章是你通過 OSCP 的系統性準備。

技術會忘，但方法論會留下：**枚舉 → 找漏洞 → 利用 → 提權 → 記錄**。

考試見。
