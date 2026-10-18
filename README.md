# SOL AI Bot 🤖📈

Bot trading **SOL/IDR** di **Indodax** yang berjalan otomatis setiap hari lewat GitHub Actions, dengan dashboard web (bisa dipasang di layar utama Android).

> ⚠️ **Bukan nasihat keuangan. Tidak ada jaminan profit.**
> Kripto sangat fluktuatif dan bisa turun tajam. Bot ini default berjalan dalam **mode PAPER** (uang simulasi Rp1.000.000).
> Kalau kamu menyalakan mode LIVE, semua risiko kerugian ditanggung sendiri. Jangan pakai uang yang tidak siap hilang.

**Dashboard:** https://maftuuh1922.github.io/sol-ai-bot/

## Cara kerja

Setiap hari pukul **07:15 WIB** (00:15 UTC, setelah candle harian Indodax tutup jam 00:00 UTC):

1. Ambil candle harian SOL/IDR dari Indodax (hanya candle yang sudah tutup).
2. Hitung indikator: Supertrend (10, 3), EMA 50/200, RSI 14, ATR, volatilitas 30 hari, return 7/30/90 hari, drawdown.
3. AI memilih **target eksposur** 0–100% (porsi modal di SOL, sisanya IDR) beserta alasan dalam Bahasa Indonesia.
4. Rem pengaman:
   - batas volatilitas: eksposur maks = 60% ÷ volatilitas tahunan,
   - rebalance diabaikan kalau perubahannya < 10% modal (hemat fee),
   - spot saja, tanpa leverage, tanpa short.
5. Eksekusi di dompet simulasi (atau order sungguhan di mode LIVE).
6. Hasil disimpan ke `docs/data/state.json` dan tampil di dashboard.

Tiga dompet dibandingkan, masing-masing mulai Rp1.000.000:

| Dompet | Strategi |
|---|---|
| 🤖 AI | keputusan AI + rem pengaman |
| 📈 Supertrend | aturan murni: 100% SOL saat tren naik, 0% saat turun |
| 💎 Hold | beli SOL di hari pertama lalu diam |

Fee Indodax yang dihitung: beli 0,2111%, jual 0,4211%.

## Penyedia AI

Urutan yang dicoba:

1. **Arcee** (opsional) — hanya kalau secret `ARCEE_API_KEY` diisi. Model bisa diatur lewat variable `ARCEE_MODEL` (default `deepseek/deepseek-v4-flash-latest`). Kalau error, lanjut ke langkah 2.
2. **GitHub Models** — memakai `GITHUB_TOKEN` bawaan Actions (`permissions: models: read`). Model bisa diatur lewat variable `GH_MODEL` (default `openai/gpt-4.1-mini`).
3. **Cadangan Supertrend** — kalau semua AI gagal, bot tetap jalan pakai aturan Supertrend. Error-nya dicatat di dashboard.

## Menjalankan manual

Tab **Actions → SOL AI Bot → Run workflow**. Bot hanya memproses satu kali per candle harian; kalau dijalankan lagi di hari yang sama, tidak ada perubahan.

Lokal:

```bash
python bot/aibot.py paper    # simulasi (default)
python bot/aibot.py status   # lihat saldo
```

## Mode LIVE (opsional, risiko sendiri)

Default-nya PAPER. Untuk order sungguhan di Indodax:

1. Buat API key Indodax dengan izin **trade** saja (jangan izin withdraw).
2. Settings → Secrets and variables → Actions:
   - Secrets: `INDODAX_KEY`, `INDODAX_SECRET`
   - Variables: `BOT_MODE` = `live`
3. Pastikan saldo IDR di akun Indodax sesuai modal yang mau dipakai.

Repo ini publik: **jangan pernah menaruh API key di file**, selalu lewat Secrets.

## Pasang di Android

Buka dashboard di Chrome → menu ⋮ → **Tambahkan ke layar utama / Instal aplikasi**.

## Struktur

```
bot/aibot.py                 bot (Python, tanpa dependensi luar)
.github/workflows/bot.yml    jadwal harian + jalankan manual
docs/                        dashboard (GitHub Pages)
docs/data/state.json         saldo, riwayat keputusan, kurva ekuitas
```
