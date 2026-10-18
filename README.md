# SOL AI Bot 🤖📈

Bot trading **SOL/IDR** di **Indodax**, gratis di **GitHub Actions** (tanpa server), dengan aplikasi Android
untuk memantau dan mengatur semuanya: grafik live, trade, backtest, notifikasi, dan **Pengaturan**.

> ⚠️ **Bukan nasihat keuangan. Tidak ada jaminan profit.**
> Kripto sangat fluktuatif dan bisa turun tajam. Bot default berjalan dalam **mode PAPER** (uang simulasi).
> Mode LIVE memakai uang sungguhan; semua risiko kerugian ditanggung sendiri.

**Aplikasi Android (APK):** https://github.com/Maftuuh1922/sol-ai-bot/releases/latest/download/sol-ai-bot.apk

## Cara kerja

GitHub Actions menjalankan bot **tiap ±15 menit** (`*/15`; jadwal GitHub sering telat 5–30 menit). Setiap pengecekan:

1. Baca `config/bot-config.json` (divalidasi; nilai salah → default, config rusak → default + PAPER, tidak pernah crash).
2. **Bot aktif OFF** → keluar tanpa trading (posisi terbuka dipertahankan, kecuali kamu tekan **Tutup posisi**).
3. Jika ada posisi: cek **stop-loss**, **trailing stop**, **minimal ROI**, lalu **batas rugi harian** dan **kill switch rugi total** dengan harga ticker terbaru.
4. Jika candle timeframe pilihan (15m / 1j / 4j / 1H) baru tutup: jalankan **strategi** → **AI** sebagai penentu/filter ukuran → **rem pengaman wajib**
   (eksposur maks, batas volatilitas, ATR guard, rebalance min 10%, spot tanpa leverage). Kalau semua AI gagal, **cadangan Supertrend** selalu dipakai.
5. Eksekusi di dompet paper, atau order Indodax sungguhan hanya bila LIVE lolos semua pemeriksaan.
6. Simpan `docs/data/state.json` (dompet, posisi, trade, order, keputusan, kurva ekuitas, **events** ber-id stabil) dan `docs/data/events.json` (feed kecil untuk notifikasi).
   Commit hanya kalau ada perubahan; push dengan retry + rebase; `concurrency` mencegah run tumpang tindih.

### Strategi (pilih di Pengaturan)

| Nama | Aturan |
|---|---|
| `ai_supertrend` (default) | AI memilih eksposur 0–100%; Supertrend (10, 3) jadi aturan cadangan |
| `ema_rsi` | Masuk saat EMA12 memotong ke atas EMA26 dan RSI < 70; keluar saat potong ke bawah atau RSI > 80 |
| `bollinger` | Masuk saat close < Bollinger bawah (20, 2) dan RSI < 30; keluar saat RSI > 70 |

Untuk `ema_rsi` dan `bollinger`, AI (jika aktif) bertindak sebagai **filter & penentu ukuran**: boleh menolak entry (0%) atau mengecilkan ukuran.
Di bawah "keyakinan minimum", bot memakai aturan strategi. Setelah keluar karena stop/trailing/ROI, entry baru menunggu sinyal baru.

### Manajemen risiko

Stop-loss %, trailing stop (jarak + aktif setelah untung X%), tabel minimal ROI (menit → %), cooldown setelah trade rugi,
maks posisi terbuka (satu pair: 0 atau 1), batas rugi harian (tutup posisi + tanpa entry sampai besok WIB) dan kill switch rugi total
(tutup posisi, `enabled=false`, `mode=paper`). Di mode LIVE, batas harian juga mematikan bot dan kembali ke PAPER. Semua bisa ON/OFF dari aplikasi.

### Backtest

`python3 bot/backtest.py` (atau tombol **Jalankan backtest** di aplikasi → workflow **Backtest**) menguji strategi + risiko dari config pada
30–365 hari candle Indodax (fee dihitung) dan menulis `docs/data/backtest.json`: return, max drawdown, win rate, jumlah trade, profit factor,
vs Hold, plus perbandingan ketiga strategi. **AI tidak disimulasikan** (untuk `ai_supertrend` dipakai aturan Supertrend). Hasil masa lalu bukan jaminan.

## Aplikasi Android

Tab **Beranda** (saklar Bot aktif, grafik candle SOL/IDR live dari Indodax + garis Supertrend + penanda BELI/JUAL bot, dompet, keputusan
terakhir dengan "AI: penyedia · model", kurva ekuitas), **Trade** (posisi terbuka, PnL, trade selesai, order, riwayat keputusan),
**Backtest**, **Notif** (riwayat kejadian) dan **⚙︎ Pengaturan** (ikon gear di header atau tab Atur).

**Notifikasi** lokal Android (tanpa server/FCM): saat aplikasi dibuka/kembali ke depan, dan tiap ±15 menit di latar belakang
(Capacitor Background Runner / WorkManager) aplikasi membaca `events.json`; kejadian baru (trade, stop/trailing/kill switch, error/AI cadangan,
ringkasan harian) jadi notifikasi sesuai saklar di Pengaturan. Beberapa merek HP mematikan tugas latar belakang — lihat dontkillmyapp.com.

### Menghubungkan aplikasi ke GitHub (sekali saja)

1. Buka https://github.com/settings/personal-access-tokens/new (fine-grained token).
2. **Repository access → Only select repositories → `Maftuuh1922/sol-ai-bot`**.
3. **Repository permissions**: `Contents: Read and write`, `Actions: Read and write`, `Secrets: Read and write` (Metadata: read otomatis).
4. Generate → salin → tempel di **Pengaturan → Koneksi GitHub → Simpan**. Token hanya disimpan di HP (tombol **Hapus token** tersedia).

Dengan token, aplikasi bisa: mengubah `config/bot-config.json` (commit "chore: update bot config from app"), menyimpan API key sebagai
**Actions secrets** (dienkripsi di HP dengan public key repo — libsodium sealed box via tweetnacl + blake2b yang dibundel; key tidak pernah
ditampilkan lagi, hanya status "tersimpan"), menjalankan **Tes AI**, **Jalankan bot sekarang**, dan **Backtest**. Tanpa token, Pengaturan hanya-baca.

### Mode LIVE

Di **Pengaturan → Mode Live**: isi secret `INDODAX_API_KEY` dan `INDODAX_SECRET` (API key Indodax izin **trade** saja, **tanpa withdraw**),
atur modal maksimum dan maks per order, lalu **Aktifkan LIVE** → baca peringatan → ketik **SAYA PAHAM**. Saat bot jalan, LIVE tetap ditolak
(kembali PAPER, tercatat di Notif) kalau secret tidak ada, config rusak, atau kill switch aktif. Saat LIVE, header menampilkan badge merah dan
tombol **STOP / kembali ke Paper** (pilihan: stop saja, atau stop + jual posisi live).
Repo ini publik: **jangan pernah menaruh API key di file**.

## Penyedia AI

NafNaf (`qwen3.8-flash-next`) → Gonka (`zai-org/GLM-5.3-Flash`) → Arcee, urutan/model/ON-OFF diatur di aplikasi (daftar model diambil dari
`/v1/models` penyedia). Prioritas: `config/bot-config.json` > repo variables (`NAFNAF_MODEL`, …) > default.

## Lokal

```bash
python3 bot/aibot.py tick      # pengecekan terjadwal
python3 bot/aibot.py run       # paksa evaluasi
python3 bot/aibot.py probe     # tes penyedia AI
python3 bot/backtest.py        # backtest
python3 bot/test_bot.py        # tes offline (tanpa jaringan, tidak pernah live)
```

## Struktur

```
bot/aibot.py        bot (Python stdlib saja)        bot/engine.py     dompet, posisi, risiko
bot/strategies.py   indikator + strategi             bot/config.py     validasi config
bot/backtest.py     backtester                       config/bot-config.json  pengaturan (tanpa rahasia)
.github/workflows/  bot.yml (tiap 15 mnt) · backtest.yml · ai-check.yml · apk.yml
app/                pembungkus Android (Capacitor)   docs/             aplikasi web (isi APK)
```

## Kredit

- Konsep manajemen risiko (stoploss, trailing stop, `minimal_roi`, cooldown/protections, backtesting) terinspirasi
  [Freqtrade](https://github.com/freqtrade/freqtrade) (GPL-3.0). Strategi `ema_rsi` dan `bollinger` mengikuti ide umum di
  [freqtrade-strategies](https://github.com/freqtrade/freqtrade-strategies) (GPL-3.0). **Tidak ada kode Freqtrade yang disalin**; semuanya ditulis ulang.
- [lightweight-charts](https://github.com/tradingview/lightweight-charts) (Apache-2.0, © TradingView), [Chart.js](https://www.chartjs.org/) (MIT),
  [TweetNaCl.js](https://github.com/dchest/tweetnacl-js) (Unlicense), [blakejs](https://github.com/dcposch/blakejs) (MIT/CC0), Capacitor (MIT).
