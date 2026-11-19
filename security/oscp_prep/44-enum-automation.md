# Ch 44 — 枚舉自動化與方法論強化：AutoRecon / nmap 腳本化

> 目標：用自動化工具把「重複、機械」的初期枚舉外包出去，讓你把腦力留給分析，同時清楚知道**自動化的邊界**——哪些事它幫你做、哪些事只能靠人。

## 為什麼要自動化枚舉

OSCP 考試 24 小時、多台機器。每台機器開頭都是一樣的動作：全埠掃描 → 針對開放服務跑對應枚舉 → 目錄爆破 → 整理結果。手動做每台都要 20–40 分鐘，而且會漏。

```
手動枚舉的風險：累了就跳步、忘了掃 UDP、忘了某個 port
自動化的價值　：開場同時對所有目標跑完整枚舉，你邊看結果邊想
```

**但**：自動化只負責「把資料攤開」，**找入口、判讀、串攻擊鏈還是你的事**。把 AutoRecon 當成「幫你把所有門都敲一遍並記錄」，不是「幫你破門」。

## AutoRecon（開場神器）

AutoRecon 會對目標自動跑 nmap 全埠、再依開放服務觸發對應工具（gobuster、enum4linux、nikto、smbclient…），結果分門別類存檔。

```bash
# 安裝
pipx install git+https://github.com/Tib3rius/AutoRecon.git
# 相依工具（nmap、gobuster、enum4linux-ng、feroxbuster 等）需先裝齊

# 對單一目標
sudo autorecon 10.10.10.x

# 對多個目標（考試開場一次全丟）
sudo autorecon 10.10.10.1 10.10.10.2 10.10.10.3

# 結果結構
results/10.10.10.x/
  ├── scans/      # 所有掃描原始輸出（nmap、服務枚舉）
  ├── exploit/    # 給你放 exploit 的空目錄
  ├── loot/       # 給你放拿到的憑證/檔案
  └── report/     # 筆記模板
```

### 怎麼用結果

1. 先看 `scans/_full_tcp_nmap.txt`：哪些 port 開著
2. 看各服務的子報告（如 `tcp_80_http_feroxbuster.txt` 的目錄、`smb` 的分享）
3. **人工判讀**：哪個版本有已知漏洞？哪個目錄可疑？哪個分享可寫？
4. 把可疑點手動深挖——自動化到此為止

> 考試提示：AutoRecon 不違規（不是 Metasploit）。但**別只看它的結論**——它不會告訴你「這兩個發現合起來是一條鏈」。

## nmap 腳本化（自己的快掃腳本）

不想裝 AutoRecon 時，一個簡單的兩段式腳本也能省很多事：

```bash
#!/bin/bash
# quickscan.sh <IP>
IP=$1
mkdir -p $IP && cd $IP
echo "[*] 全埠快掃..."
nmap -p- --min-rate 2000 -T4 -oN allports.txt $IP
PORTS=$(grep '^[0-9]' allports.txt | cut -d/ -f1 | paste -sd, -)
echo "[*] 開放埠：$PORTS，深掃中..."
nmap -sCV -p$PORTS -oN deep.txt $IP
echo "[*] 完成，看 $IP/deep.txt"
```

```bash
chmod +x quickscan.sh
./quickscan.sh 10.10.10.x
```

## 服務枚舉自動化小工具

| 工具 | 用途 |
|------|------|
| `nmap -sCV` | 開放埠的預設腳本 + 版本（最低標配） |
| `enum4linux-ng` | SMB/LDAP 一鍵枚舉（比舊版 enum4linux 完整） |
| `feroxbuster` / `gobuster` | 目錄爆破（feroxbuster 會遞迴） |
| `nikto` | web 伺服器已知問題快掃 |
| `nxc`（NetExec） | SMB/LDAP/WinRM 批量枚舉與憑證噴灑 |

## 自動化的邊界（務必內化）

```
自動化幫你：把所有服務的原始資訊攤在面前、不漏掃
你要自己做：
  - 判讀版本是否真的可利用（版本號會騙人）
  - 把分散的發現串成攻擊鏈
  - 決定先打哪個入口
  - 手動驗證並修改 exploit（Ch 17）
```

**反面教材**：有人開場跑完 AutoRecon 就卡住，因為他在等工具「給答案」。工具只給資料，答案要你從資料裡看出來。這正是 Ch 1 說的「枚舉 > 一切」——自動化讓枚舉更徹底，但思考不能外包。

## 本章對應靶機

對你已經打過的任一台 HTB Easy 機器重跑一次 AutoRecon，比對「它找到的」和「你當初手動找到的」有沒有差異——這是檢驗自己枚舉盲點的好方法。

## 自我檢核

- [ ] 能用 AutoRecon 對多個目標一次開跑並看懂結果目錄結構
- [ ] 有一個自己的兩段式 nmap 快掃腳本
- [ ] 說得出「自動化幫你做什麼、不幫你做什麼」
- [ ] 知道 AutoRecon 不受 Metasploit 考試限制
- [ ] 理解自動化讓枚舉更徹底，但判讀與串鏈仍要人工

→ 回到 [課程首頁](./README.md) ｜ 下一步：[速查卡與考試流程](./cheatsheets/exam-day-flow.md)
