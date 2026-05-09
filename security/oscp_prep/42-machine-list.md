# Ch 42 — 必練機器清單：HTB / Proving Grounds 優先順序

> 目標：不浪費時間在「不像 OSCP」的機器，把有限的練習時間用在最有轉移價值的靶機上。

## 選靶機的標準

OSCP 靶機的特徵：

```
✓ 需要手動枚舉（不是直接 searchsploit 一個 CVE 就結束）
✓ 有合理的提權路徑（SUID/sudo/服務/弱權限）
✓ 不需要 kernel exploit 就能解（BoF 除外）
✓ 不是 CTF 謎題（現實的服務配置）
```

機器不是越難越好——OSCP 考試是**現實配置的滲透**，不是 CTF 解謎。

## HTB Retired 機器

### Linux Easy — 優先練

| 機器 | 主要技術 | 值得學的點 |
|------|---------|-----------|
| **Lame** | SMB + smbclient | 第一台練習機，枚舉重要 |
| **Legacy** | SMB MS08-067 | Windows 舊版服務漏洞 |
| **Blue** | EternalBlue (MS17-010) | 先手動再 Metasploit |
| **Devel** | FTP 上傳 + IIS | Windows 初始立足基礎 |
| **Bashed** | Webshell + sudo -u | Linux sudo 提權 |
| **Nibbles** | Web 漏洞 + sudo script | sudo NOPASSWD 提權 |
| **Shocker** | ShellShock (CGI) | bash 漏洞注射 |
| **Beep** | LFI + 憑證重用 | 多條入侵路徑 |

### Linux Medium — 進階練

| 機器 | 主要技術 | 值得學的點 |
|------|---------|-----------|
| **Cronos** | DNS zone transfer + SQLi + Cron | 完整攻擊鏈 |
| **Networked** | File upload bypass + cron | 圖片上傳繞過 |
| **Valentine** | Heartbleed + SSH key | 讀私鑰登入 |
| **Blocky** | JAR 反編譯 + 密碼重用 | 密碼在 config 裡 |
| **Aragog** | WordPress + XXE | XML 注入 |

### Windows Easy/Medium

| 機器 | 主要技術 | 值得學的點 |
|------|---------|-----------|
| **Grandpa** | IIS WebDAV + MS14-070 | Windows 老版提權 |
| **Granny** | IIS WebDAV ASPX | Windows 上傳技術 |
| **Optimum** | HFS 2.3 RCE + Sherlock | Windows 提權腳本 |
| **Bounty** | IIS WebDAV web.config | 上傳 .config 執行 |
| **Jerry** | Tomcat default creds + WAR | 管理界面接管 |
| **Bastard** | Drupalgeddon | CMS 漏洞模板 |

### Active Directory — 必打

| 機器 | 主要技術 | 值得學的點 |
|------|---------|-----------|
| **Forest** | AS-REP Roasting + WriteDACL | AD 完整鏈，必練！ |
| **Active** | Kerberoasting + GPP 密碼 | 經典 AD 入口 |
| **Sauna** | AS-REP + DCSync | BloodHound 路徑分析 |
| **Cascade** | LDAP + AD 回收站 | 非標準 AD 枚舉 |
| **Return** | Printer LDAP 憑證洩漏 | 服務配置錯誤 |

## Proving Grounds Practice（最接近考試風格）

PG Practice 由 OffSec 出題，風格和考試最接近，是練習的首選。

### Linux

| 機器 | 難度 | 主要技術 |
|------|------|---------|
| **Bratarina** | Easy | SMTP + 命令注射 |
| **Gaara** | Easy | 枚舉 + SUID |
| **Shakabrah** | Easy | Web + sudo |
| **Djinn3** | Easy | 多服務枚舉 |
| **Exghost** | Medium | 服務版本漏洞 |
| **Sybaris** | Medium | Redis + 憑證 |
| **Pelican** | Medium | 服務枚舉 + privesc |
| **Zipper** | Medium | Zabbix + sudo |
| **Helpdesk** | Medium | MantisBT SQLi |

### Windows

| 機器 | 難度 | 主要技術 |
|------|------|---------|
| **Algernon** | Easy | SmarterMail RCE |
| **Nickel** | Easy | Web + Windows privesc |
| **Authby** | Easy | 服務弱密碼 |
| **Hepet** | Medium | AD 環境 |
| **Shenzi** | Medium | WordPress + Windows |
| **Hutch** | Medium | AD + Kerberos |

## TryHackMe 練習房間

TryHackMe 有引導式的 OSCP 準備路徑，適合打 HTB 前先建概念。

### 必做的房間

```
1. Offensive Pentesting Path（整條路徑跟下來）

2. Buffer Overflow Prep
   - OVERFLOW1 到 OVERFLOW10
   - 每個練到 20 分鐘內完成

3. Attacktive Directory
   - AS-REP Roasting + Kerberoasting 完整練習

4. Wreath
   - 完整的 Pivoting 練習（三機器鏈）
   - 學 Chisel + proxychains 在真實場景下的用法
```

## 練習順序建議

### 第一個月（基礎）

```
Week 1–2：HTB Easy Linux × 4（Lame, Bashed, Shocker, Nibbles）
Week 3–4：HTB Easy Windows × 4（Legacy, Blue, Devel, Optimum）
同時：TryHackMe Offensive Pentesting Path（理論配合）
```

### 第二個月（技術強化）

```
Week 5–6：HTB Medium Linux × 3（Cronos, Valentine, Blocky）
Week 7–8：Buffer Overflow Prep（完成 OVERFLOW1–5）
          + HTB Medium Windows × 2
同時：Proving Grounds Easy × 3（Bratarina, Gaara, Shakabrah）
```

### 第三個月（AD + 衝刺）

```
Week 9–10：HTB AD × 3（Forest 必打，Active, Sauna）
Week 11–12：Proving Grounds Medium × 4（Pelican, Sybaris, Shenzi, Hutch）
最後兩週：Final Project — 24 小時 OSCP 模擬考試
```

## 機器紀錄格式

每台打完後留一份筆記：

```markdown
## 機器名（平台 + 難度）

**IP**: 10.10.10.x  
**OS**: Linux / Windows  
**攻擊路徑**: 枚舉 → 初始立足 → 提權

### 入侵路徑摘要

1. [枚舉] nmap 發現 80/22，gobuster 找到 /admin
2. [漏洞] admin 頁面有 LFI 漏洞，包含 /var/log/apache2/access.log
3. [初始立足] Log poisoning → webshell → user shell
4. [提權] sudo -l 顯示 NOPASSWD: /usr/bin/python3，GTFOBins

### 關鍵指令

\`\`\`bash
# 關鍵步驟的指令，之後可以直接複製用
\`\`\`
```

## 不要練的機器

```
✗ HTB Insane 難度（跟 OSCP 考試差太遠）
✗ 純 Forensics 或 Reversing 靶機
✗ 需要自己開發 0-day 的機器
✗ 謎題風格的 CTF（隱寫術、密碼學解謎）
```

## 自我檢核

- [ ] 打完至少 5 台 HTB Easy 機器（Linux + Windows 各有）
- [ ] 完成 Buffer Overflow Prep OVERFLOW1–5
- [ ] 打完 HTB Forest（AD 必練）
- [ ] 打完至少 3 台 Proving Grounds Practice 機器
- [ ] 每台機器都有完整筆記（入侵路徑 + 關鍵指令）

→ [Final Project：24 小時 OSCP 模擬考試](./final-project-oscp-simulation.md)
