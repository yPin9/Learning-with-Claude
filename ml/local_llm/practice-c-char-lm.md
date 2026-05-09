# 練習 C — 訓練一個 character-level 語言模型（古典詩詞語料）

> 目標：把 Part 4 學到的所有技術（資料管線、訓練迴圈、lr schedule、評估）整合成一個完整的訓練專案。

## 任務規格

| 項目 | 規格 |
|------|------|
| 模型架構 | Character-level GPT |
| 語料 | 古典詩詞（唐詩、宋詞，或金庸小說片段） |
| 訓練目標 | val perplexity < 30，生成品質可閱讀 |
| 必備功能 | checkpoint、lr schedule、訓練曲線視覺化 |
| 加分 | 實作 top-p 採樣、比較不同超參數效果 |
| 硬體 | CPU，約 30–60 分鐘 |

## 語料來源建議

```
唐詩三百首：搜尋 "唐詩三百首 txt github"，有很多開源版本
《射鵰英雄傳》片段：金庸作品已公版，可自由使用
《紅樓夢》：語言豐富，繁體中文
自訂：任何純文字的繁體中文都行
```

建議語料大小：100KB–5MB（過小容易過擬合，過大 CPU 訓練太慢）。

## 任務一：建立完整訓練腳本

建立 `train_char_lm.py`，包含以下元件：

1. **資料管線**（Ch 18）：讀檔、清洗、建 vocab、切 train/val
2. **模型**（Ch 12）：GPT 類別
3. **訓練迴圈**（Ch 20）：含 checkpoint、gradient clipping
4. **LR Schedule**（Ch 22）：warmup + cosine decay
5. **評估**（Ch 24）：perplexity、生成樣本

## 期望輸出

訓練過程：
```
step    0: train=4.856  val=4.855  ppl=128.0  lr=0.000e+00
step  500: train=2.341  val=2.412  ppl=11.2   lr=3.000e-04
step 1000: train=1.893  val=1.967  ppl=7.2    lr=2.850e-04
step 2000: train=1.543  val=1.621  ppl=5.1    lr=2.400e-04
step 3000: train=1.312  val=1.398  ppl=4.0    lr=1.800e-04
```

生成樣本（step 3000）：
```
[床] 床前明月光，疑是地上霜。舉頭望明月，
[春] 春眠不覺曉，處處聞啼鳥。夜來風雨聲，
[白] 白日依山盡，黃河入海流。欲窮千里目，
```

## 實作步驟建議

### Step 1：資料準備

```python
import re, torch, os

def prepare_data(filepath, val_ratio=0.1):
    with open(filepath, encoding='utf-8') as f:
        text = f.read()

    # 清洗：只保留中文字和基本標點
    text = re.sub(r'[^一-鿿，。？！、；：「」『』（）—…]', '', text)
    text = re.sub(r'\s+', '', text)
    print(f"語料長度：{len(text)} 字")

    # 建 vocab
    chars = sorted(set(text))
    print(f"Vocab size: {len(chars)}")
    stoi = {c: i for i, c in enumerate(chars)}
    itos = {i: c for i, c in enumerate(chars)}

    # 轉 tensor
    data = torch.tensor([stoi[c] for c in text], dtype=torch.long)
    n = int(len(data) * (1 - val_ratio))
    return data[:n], data[n:], stoi, itos, len(chars)
```

### Step 2：超參數配置

```python
config = {
    # 模型
    "d_model":    128,
    "num_heads":  4,
    "num_layers": 4,
    "block_size": 256,
    "dropout":    0.1,

    # 訓練
    "batch_size":    32,
    "max_steps":     5000,
    "eval_every":    500,
    "save_every":    1000,
    "accum_steps":   1,     # 記憶體不夠時調高

    # 優化器
    "max_lr":        3e-4,
    "min_lr":        3e-5,
    "warmup_steps":  200,
    "weight_decay":  0.1,
    "grad_clip":     1.0,
}
```

### Step 3：訓練主迴圈

整合 Ch 20 的完整訓練迴圈，加上：
- 每 `eval_every` 步計算 val perplexity
- 每 `save_every` 步儲存 checkpoint
- 訓練結束後畫損失曲線

### Step 4：生成函數

支援 temperature + top-k + top-p 三種採樣：

```python
@torch.no_grad()
def generate(model, stoi, itos, prompt_char, max_new=50, temperature=0.8, top_k=20, top_p=0.9):
    model.eval()
    ids = [stoi[c] for c in prompt_char if c in stoi]
    x   = torch.tensor([ids])

    for _ in range(max_new):
        x_cond  = x[:, -model.max_seq_len:]
        logits  = model(x_cond)
        logits  = logits[0, -1, :] / temperature

        # Top-k
        if top_k:
            v, _ = torch.topk(logits, top_k)
            logits[logits < v[-1]] = float('-inf')

        # Top-p
        if top_p < 1.0:
            sorted_logits, sorted_idx = torch.sort(logits, descending=True)
            cum_probs = torch.cumsum(torch.softmax(sorted_logits, dim=-1), dim=-1)
            remove = cum_probs > top_p
            remove[1:] = remove[:-1].clone(); remove[0] = False
            sorted_logits[remove] = float('-inf')
            logits[sorted_idx] = sorted_logits

        next_id = torch.multinomial(torch.softmax(logits, dim=-1), 1)
        x = torch.cat([x, next_id.unsqueeze(0)], dim=1)

    return ''.join(itos[i.item()] for i in x[0])
```

## 完整參考解答

<details>
<summary>點開完整的 train_char_lm.py</summary>

```python
#!/usr/bin/env python3
"""train_char_lm.py — character-level 語言模型訓練"""

import argparse, math, os, re, time
import torch, torch.nn as nn, torch.nn.functional as F
import matplotlib.pyplot as plt

# ===== 模型 =====
class CausalSelfAttention(nn.Module):
    def __init__(self, d, h, T, p):
        super().__init__()
        self.h, self.dh = h, d//h
        self.qkv = nn.Linear(d, 3*d, bias=False)
        self.proj = nn.Linear(d, d, bias=False)
        self.drop = nn.Dropout(p)
        self.register_buffer("mask", torch.tril(torch.ones(T,T)))
    def forward(self, x):
        B,T,C = x.shape; H,D = self.h, self.dh
        q,k,v = self.qkv(x).split(C,2)
        q=q.view(B,T,H,D).transpose(1,2); k=k.view(B,T,H,D).transpose(1,2); v=v.view(B,T,H,D).transpose(1,2)
        s = q@k.transpose(-2,-1)/math.sqrt(D)
        s = s.masked_fill(self.mask[:T,:T]==0,float('-inf'))
        return self.proj((self.drop(F.softmax(s,-1))@v).transpose(1,2).contiguous().view(B,T,C))

class Block(nn.Module):
    def __init__(self, d, h, T, p):
        super().__init__()
        self.ln1=nn.LayerNorm(d); self.ln2=nn.LayerNorm(d)
        self.attn=CausalSelfAttention(d,h,T,p)
        self.ff=nn.Sequential(nn.Linear(d,4*d),nn.GELU(),nn.Linear(4*d,d),nn.Dropout(p))
    def forward(self, x): return x+self.ff(self.ln2(x+self.attn(self.ln1(x))))

class GPT(nn.Module):
    def __init__(self, V, d=128, h=4, L=4, T=256, p=0.1):
        super().__init__()
        self.T = T
        self.emb = nn.Embedding(V,d); self.pos = nn.Embedding(T,d)
        self.drop = nn.Dropout(p)
        self.blocks = nn.ModuleList([Block(d,h,T,p) for _ in range(L)])
        self.ln = nn.LayerNorm(d); self.head = nn.Linear(d,V,bias=False)
        self.head.weight = self.emb.weight
        self.apply(lambda m: nn.init.normal_(m.weight,0,0.02) if isinstance(m,(nn.Linear,nn.Embedding)) else None)
    def forward(self, idx, tgt=None):
        B,T = idx.shape
        x = self.drop(self.emb(idx)+self.pos(torch.arange(T,device=idx.device)))
        for b in self.blocks: x=b(x)
        logits = self.head(self.ln(x))
        loss = F.cross_entropy(logits.view(-1,logits.size(-1)),tgt.view(-1)) if tgt is not None else None
        return logits, loss
    @torch.no_grad()
    def generate(self, x, n, temp=0.8, top_k=20):
        for _ in range(n):
            c=x[:,-self.T:]; lg,_=self(c); lg=lg[0,-1,:]/temp
            if top_k: v,_=torch.topk(lg,top_k); lg[lg<v[-1]]=float('-inf')
            x=torch.cat([x,torch.multinomial(F.softmax(lg,-1),1).unsqueeze(0)],1)
        return x

# ===== 資料 =====
def load_data(path, val=0.1):
    text = re.sub(r'[^一-鿿，。？！、；：「」]','',open(path,encoding='utf-8').read())
    chars=sorted(set(text)); stoi={c:i for i,c in enumerate(chars)}; itos={i:c for i,c in enumerate(chars)}
    data=torch.tensor([stoi[c] for c in text],dtype=torch.long)
    n=int(len(data)*(1-val))
    return data[:n],data[n:],stoi,itos,len(chars)

def get_batch(data, B, T):
    ix=torch.randint(len(data)-T,(B,))
    return torch.stack([data[i:i+T] for i in ix]),torch.stack([data[i+1:i+T+1] for i in ix])

# ===== 訓練 =====
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="poems.txt")
    p.add_argument("--steps", type=int, default=5000)
    args = p.parse_args()

    train,val,stoi,itos,V = load_data(args.data)
    cfg = dict(d=128,h=4,L=4,T=256,p=0.1)
    model = GPT(V,**cfg)
    print(f"V={V}, params={sum(p.numel() for p in model.parameters()):,}")

    opt = torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=0.1)
    def lr_fn(s):
        ws,ts,mr = 200,args.steps,0.1
        if s<ws: return s/ws
        prog=(s-ws)/(ts-ws); return mr+(1-mr)*0.5*(1+math.cos(math.pi*prog))
    sch = torch.optim.lr_scheduler.LambdaLR(opt,lr_fn)

    tl,vl,vs=[],[],[]
    for step in range(args.steps):
        model.train()
        x,y=get_batch(train,32,256)
        _,loss=model(x,y); opt.zero_grad(); loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(),1.0)
        opt.step(); sch.step(); tl.append(loss.item())

        if (step+1)%500==0:
            model.eval()
            with torch.no_grad():
                vx,vy=get_batch(val,32,256); _,vloss=model(vx,vy)
            vl.append(vloss.item()); vs.append(step+1)
            ppl=math.exp(vloss.item())
            lr=opt.param_groups[0]['lr']
            print(f"step {step+1:5d}: train={loss.item():.3f} val={vloss.item():.3f} ppl={ppl:.1f} lr={lr:.2e}")
            for c in ["床","春","白"]:
                if c in stoi:
                    out=model.generate(torch.tensor([[stoi[c]]]),30,temp=0.7,top_k=15)
                    print(f"  [{c}] {''.join(itos[i.item()] for i in out[0])}")

    # 畫圖
    plt.figure(figsize=(10,4))
    plt.plot(tl,alpha=0.3,label="train"); plt.plot(vs,vl,"r-o",label="val")
    plt.legend(); plt.savefig("loss.png"); print("loss.png 已儲存")

if __name__=="__main__":
    main()
```

</details>

## 測試用例

| 條件 | 期望結果 |
|------|---------|
| step 0，loss | 接近 `ln(vocab_size)` |
| step 5000，val ppl | < 30 |
| 生成「床」20 字 | 有 50%+ 的字是詩詞裡出現過的組合 |
| 重複率（3-gram） | < 0.2 |

## 加分挑戰

1. **比較超參數**：分別跑 `d_model=64` 和 `d_model=256`，畫在同一張圖上比較
2. **Top-p vs Top-k**：生成各 10 個樣本，比較多樣性
3. **語料大小的影響**：用原語料的 10%、50%、100% 各訓練一次，比較 perplexity

## 自我檢核

- [ ] 完整訓練腳本能跑起來，loss 有在下降
- [ ] checkpoint 功能正常，能中斷後繼續訓練
- [ ] val perplexity < 30
- [ ] 生成的古典詩詞有一定可讀性

→ [Ch 25 為什麼要 fine-tune：base vs instruct vs chat](./25-why-finetune.md)
