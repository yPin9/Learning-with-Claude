# OSCP 備考全攻略：從零到拿證

> 給完全沒有滲透測試背景、目標是通過 OSCP 考試的工程師。

這門課從 Linux 指令開始，走過情報蒐集、Web 攻擊、服務利用、Linux/Windows 提權、Active Directory、Buffer Overflow，到最後的考試策略與報告撰寫。每章附對應 HackTheBox / TryHackMe 靶機，讀完就上機練。

## 為什麼學這個？

- **OSCP 是滲透測試的行業門票**：技術面試、紅隊職位、資安顧問——這張證書是最通用的語言。
- **考試逼你真的學會**：24 小時實機考試，沒有選擇題，不能靠背答案。
- **完整的攻擊思維**：不只是工具操作，而是從零建立「看到一台機器，如何系統性地打下它」的方法論。

## OSCP 考試結構（2023 PEN-200）

```
總分：100 分，及格線：70 分
├── 獨立機器 × 3：各 20 分（10 初始立足 + 10 提權）= 60 分
└── Active Directory 三機器鏈：40 分（10+10+20 for DC）

時間：24 小時滲透 + 24 小時報告撰寫
```

## 課程地圖

### Part 1 — 地基（Linux + 網路 + 方法論）
- [Ch 1 OSCP 考試全解析：評分規則與思考框架](./01-oscp-overview.md)
- [Ch 2 Kali Linux 環境建置：工具鏈 + VPN](./02-kali-setup.md)
- [Ch 3 Linux 滲透必備：指令、檔案系統、權限](./03-linux-basics.md)
- [Ch 4 網路基礎：TCP/IP、埠口、協定速查](./04-networking-basics.md)
- [Ch 5 滲透測試方法論：枚舉 → 利用 → 提權 → 報告](./05-pentest-methodology.md)

### Part 2 — 情報蒐集（Enumeration）
- [Ch 6 nmap 精通：主機發現、服務指紋、NSE 腳本](./06-nmap.md)
- [Ch 7 服務枚舉：SMB / FTP / SSH / SNMP / DNS](./07-service-enumeration.md)
- [Ch 8 Web 情報蒐集：目錄爆破、vhost、技術指紋](./08-web-recon.md)
- [Ch 9 漏洞搜尋：searchsploit / Exploit-DB / CVE](./09-vuln-search.md)
- [練習 A：完整枚舉 3 台 HTB 機器](./practice-a-enumeration.md)

### Part 3 — Web 應用攻擊
- [Ch 10 Burp Suite 精通：攔截、Repeater、Intruder](./10-burp-suite.md)
- [Ch 11 SQL Injection：手注 + sqlmap](./11-sqli.md)
- [Ch 12 檔案包含（LFI/RFI）與日誌毒化](./12-lfi-rfi.md)
- [Ch 13 檔案上傳繞過：MIME / 副檔名 / 魔術字元](./13-file-upload.md)
- [Ch 14 命令注入（Command Injection）](./14-command-injection.md)
- [Ch 15 身份驗證繞過：預設憑證、弱 JWT、登入繞過](./15-auth-bypass.md)
- [練習 B：4 台 Web 主題 HTB 機器](./practice-b-web-attacks.md)

### Part 4 — 服務漏洞利用
- [Ch 16 Metasploit 框架精通：search / use / exploit](./16-metasploit.md)
- [Ch 17 手動漏洞利用：修改 Python/C exploit 腳本](./17-manual-exploitation.md)
- [Ch 18 密碼攻擊：Hydra / Hashcat / John the Ripper](./18-password-attacks.md)
- [Ch 19 反彈 Shell 技巧全集：各語言、各協定](./19-reverse-shells.md)

### Part 5 — Linux 提權
- [Ch 20 Linux 提權方法論：系統資訊收集清單](./20-linux-privesc-methodology.md)
- [Ch 21 SUID / SUDO 提權：GTFOBins 活用](./21-suid-sudo.md)
- [Ch 22 Cron Job 濫用 + 弱檔案權限](./22-cron-weak-perms.md)
- [Ch 23 NFS / PATH 劫持 / 環境變數](./23-nfs-path-hijack.md)
- [Ch 24 linPEAS / linux-smart-enumeration 解讀](./24-linux-enum-tools.md)
- [練習 C：3 台 Linux 提權靶機](./practice-c-linux-privesc.md)

### Part 6 — Windows 提權
- [Ch 25 Windows 提權方法論：環境收集清單](./25-windows-privesc-methodology.md)
- [Ch 26 服務濫用：不安全路徑 + 弱服務權限](./26-service-abuse.md)
- [Ch 27 Token 竊取：SeImpersonatePrivilege + Potato](./27-token-impersonation.md)
- [Ch 28 AlwaysInstallElevated / 排程任務 / Registry](./28-windows-misc-privesc.md)
- [Ch 29 winPEAS / PowerUp / Seatbelt 解讀](./29-windows-enum-tools.md)
- [練習 D：3 台 Windows 靶機](./practice-d-windows-privesc.md)

### Part 7 — Active Directory（40 分關鍵）
- [Ch 30 AD 基礎：Domain / Forest / Kerberos / LDAP](./30-ad-fundamentals.md)
- [Ch 31 初始立足：Password Spray / AS-REP Roasting](./31-ad-initial-access.md)
- [Ch 32 Kerberoasting：服務票證離線破解](./32-kerberoasting.md)
- [Ch 33 BloodHound + SharpHound：攻擊路徑視覺化](./33-bloodhound.md)
- [Ch 34 Pass-the-Hash / Pass-the-Ticket](./34-lateral-movement.md)
- [Ch 35 DCSync + Golden Ticket：接管 Domain Controller](./35-domain-dominance.md)
- [Ch 36 OSCP AD 三機器鏈實戰模擬](./36-ad-chain-simulation.md)

### Part 8 — 進階技術
- [Ch 37 Buffer Overflow（x86 Windows）：EIP 控制到 shellcode](./37-buffer-overflow.md)
- [Ch 38 Pivoting + Port Forwarding：Chisel / SSH tunnel](./38-pivoting.md)
- [Ch 39 AV 規避基礎：混淆、msfvenom payload 修改](./39-av-evasion.md)

### Part 9 — 考試策略與報告
- [Ch 40 OSCP 24 小時考試策略：時間分配與心態](./40-exam-strategy.md)
- [Ch 41 報告撰寫：OffSec 要求格式 + Markdown 模板](./41-report-writing.md)
- [Ch 42 必練機器清單：HTB / Proving Grounds 優先順序](./42-machine-list.md)
- [Final Project：模擬 OSCP 考試](./final-project-oscp-simulation.md)

## 學習方式建議

1. **每章讀完當天就上機**：理論和實機間隔超過三天，東西就忘了。
2. **卡關不超過 30 分鐘就看提示**：OSCP 備考時間寶貴，但要先真的卡過才看。
3. **每台機器都寫筆記**：考試報告寫得好不好，取決於打機器時有沒有截圖。

## 參考資源

- OffSec PEN-200 課程 — 官方教材，報名後可用
- HackTheBox / Proving Grounds — 主要練習平台
- GTFOBins — gtfobins.github.io（SUID/SUDO 提權速查）
- PayloadsAllTheThings — GitHub，各種攻擊 payload 大全
- RevShells — revshells.com（反彈 shell 生成器）
