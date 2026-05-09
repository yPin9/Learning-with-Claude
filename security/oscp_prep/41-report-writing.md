# Ch 41 — 報告撰寫：OffSec 要求格式 + Markdown 模板

> 目標：學會 OSCP 考試報告的格式要求，能在 24 小時報告撰寫窗口內交出一份符合標準的報告。

## 為什麼報告很重要

OSCP 報告不是附加題——**截圖不夠或步驟不清楚，分數直接扣甚至失格。**

OffSec 的評分標準：
- 每台機器的每個步驟都要有截圖
- 截圖要包含機器 IP（ifconfig / ipconfig 可見）
- flag 截圖要同時顯示 flag 內容 + 機器 IP
- 步驟要能讓別人重現（靠你的截圖和指令）

## 報告結構

OffSec 要求的報告格式：

```markdown
# OSCP Exam Report

## Information
- Student OS ID: OS-XXXXX
- Exam Date: YYYY-MM-DD
- Report Submission Date: YYYY-MM-DD

## 1. Introduction
簡短說明這份報告的目的和範圍。

## 2. High-Level Summary
簡要列出所有成功入侵的機器和使用的技術。

## 3. Methodologies
說明你的測試方法論（枚舉 → 利用 → 提權）。

## 4. Independent Machines

### 4.1 Machine 1 – 10.10.10.x (OS-XXXXX)

#### 4.1.1 Service Enumeration
[截圖：nmap 輸出]

**Identified Services:**
| Port | Service | Version |
|------|---------|---------|
| 80   | HTTP    | Apache 2.4.29 |

#### 4.1.2 Initial Access
[說明怎麼入侵]
[截圖：執行 exploit 的過程]
[截圖：whoami 顯示初始 shell 身份]
[截圖：cat local.txt + ifconfig，flag 和 IP 都看得到]

#### 4.1.3 Privilege Escalation
[說明提權路徑]
[截圖：發現提權路徑的指令輸出]
[截圖：執行提權的過程]
[截圖：whoami 顯示 root/SYSTEM + cat proof.txt + ifconfig]

### 4.2 Machine 2 – ... （同格式）

## 5. Active Directory Chain

### 5.1 Machine 1 (Initial Access)
### 5.2 Machine 2 (Lateral Movement)
### 5.3 Domain Controller

## 6. Appendices
[工具清單、參考資料等]
```

## OffSec 官方模板

OffSec 提供官方的 Word / LibreOffice 模板，在你的控制面板可以下載。考試時使用官方模板更安全，格式不會出錯。

## 截圖要求細節

### 每台機器的必要截圖

```
1. nmap 輸出（至少顯示開放的 port 和服務）
2. 漏洞發現的截圖（searchsploit 結果、版本號等）
3. Exploit 執行的截圖（看到連線建立）
4. 拿到 shell 後的第一個指令：whoami
5. local.txt 截圖：
   cat /tmp/local.txt && ifconfig
   （flag + IP 同時可見）
6. 提權過程的截圖
7. proof.txt 截圖：
   cat /root/proof.txt && ifconfig
   （flag + IP 同時可見，以 root/SYSTEM 身份）
```

### 截圖原則

```
□ IP 地址要看得清楚
□ 使用者名稱要看得清楚（whoami 的輸出）
□ Flag 內容要看得清楚
□ 如果 terminal 字太小，放大後截圖
□ 截圖要清晰，不能模糊
```

## Obsidian 作為考試筆記工具

考試期間推薦用 Obsidian 記筆記：

```
優點：
  Markdown 格式，直接能轉成報告
  支援截圖貼入
  搜尋功能強大
  可以連結相關資訊
```

每台機器一個 Markdown 檔，考後整理成報告格式。

## 報告撰寫 Checklist

```
考試結束後，報告撰寫階段：

□ 確認有每台機器的截圖（local.txt 和 proof.txt）
□ 整理截圖，命名清楚（machine1_nmap.png, machine1_root.png）
□ 按報告格式填入每台機器的細節
□ 每個漏洞說明：
  - 服務名稱和版本
  - CVE 或漏洞描述
  - 利用方法（指令）
  - 截圖證明
□ 每個提權說明：
  - 發現方式
  - 利用方法（指令）
  - 截圖證明
□ 用 OffSec 官方格式打包 PDF
□ 提交到 OffSec 考試系統
```

## 常見扣分原因

```
✗ proof.txt 截圖看不到 IP（只截 flag 沒截 ifconfig）
✗ 截圖模糊或解析度太低
✗ 步驟不連貫（缺少中間截圖）
✗ 沒有說明發現漏洞的過程（只寫了「發現了漏洞」）
✗ PDF 格式不對（沒用官方模板，或格式亂掉）
✗ 提交時間超過 24 小時
```

## Markdown 機器模板

存成 `template.md` 備用：

```markdown
## Machine – <hostname> (10.10.10.x)

### Service Enumeration

**nmap all ports:**
```
[nmap 輸出貼這裡]
```

**nmap targeted:**
```
[nmap -sC -sV 輸出貼這裡]
```

Identified Services:
| Port | State | Service | Version |
|------|-------|---------|---------|
| X | open | X | X |

### Exploitation

**Vulnerability:** <漏洞名稱/CVE>

**Affected Version:** <版本>

**Exploit Used:**
```bash
[執行的指令]
```

**Result:** Obtained shell as <username>

[截圖：shell 建立]
[截圖：whoami 輸出]

**Local.txt:**
```
[flag 內容]
```
[截圖：cat local.txt && ifconfig]

### Privilege Escalation

**Method:** <提權方法>

**Discovery:**
```bash
[發現提權路徑的指令]
```

**Exploitation:**
```bash
[提權指令]
```

[截圖：提權成功，whoami 顯示 root/SYSTEM]

**Proof.txt:**
```
[flag 內容]
```
[截圖：cat proof.txt && ifconfig（以 root 身份）]
```

## 自我檢核

- [ ] 了解 OffSec 報告格式要求（每台機器需要哪些截圖）
- [ ] 準備好 Markdown 模板，考試時能直接填
- [ ] 知道 proof.txt 截圖必須同時顯示 flag + IP
- [ ] 知道報告提交截止是考試開始後 48 小時

→ [Ch 42 必練機器清單：HTB / Proving Grounds 優先順序](./42-machine-list.md)
