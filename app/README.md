# Aplikasi Android (APK)

Pembungkus [Capacitor](https://capacitorjs.com) untuk dashboard di `../docs`. Proyek `android/` dibuat ulang otomatis
oleh workflow `.github/workflows/apk.yml` setiap ada perubahan di `app/` atau `docs/`, lalu APK diunggah ke release
`app-latest`:

**Unduh:** https://github.com/Maftuuh1922/sol-ai-bot/releases/latest/download/sol-ai-bot.apk

Aplikasi mengambil data terbaru dari `https://raw.githubusercontent.com/Maftuuh1922/sol-ai-bot/main/docs/data/state.json`
(bukan data yang ikut ter-bundle), jadi tidak perlu install ulang tiap hari.

## Tanda tangan APK

- Kalau secret `ANDROID_KEYSTORE_B64` (+ `ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`) ada,
  APK ditandatangani dengan keystore itu.
- Kalau tidak, dipakai `solbot-release.p12` (self-signed, password `solaibot-public`). Kunci ini **publik** — cukup untuk
  aplikasi pribadi agar update bisa dipasang menimpa versi lama, tapi bukan untuk distribusi ke Play Store.
  Kalau pindah ke keystore pribadi, aplikasi lama harus di-uninstall sekali.

## Build lokal

```bash
cd app && npm ci && npm run web && npx cap add android && npx cap sync android
cd android && ./gradlew assembleRelease
```
