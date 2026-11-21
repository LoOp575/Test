# FP Live (5 · 15 · 30 · 60 menit): formula specification

## Status dan ruang lingkup

Modul terisolasi di `backend/fp_live.py`, dipakai hanya oleh menu `/fp-live`
(`GET` / `POST /api/fp-live/{symbol}`). Modul ini **tidak** diimpor oleh
`backend/screening.py`, tidak mengubah Model v1 (`/fokker-planck`) maupun
`FP v2 · Banding` (`/fokker-planck-v2`), dan tidak mengubah skor produksi
Screener.

Seluruh kode v1/v2 dibaca **read-only**: `fokker_planck.solve_fokker_planck`,
`fokker_planck.export_chart_curve`, `fokker_planck._sanitize_candles`,
`fokker_planck_v2._directional_and_quantile_stats`,
`fokker_planck_v2._solve_intraday_v1_baseline`,
`fokker_planck_v2.fetch_intraday_5m_klines`, serta helper bursa publik di
`market_data` (`_kline_hosts`, `_fetch_json`, `_parse_klines`, `TTLCache`).

## Umpan data

| Komponen | Sumber | Catatan |
| --- | --- | --- |
| Candle acuan | `5m`, 240 bar (±20 jam) | volatilitas struktural, CVD 30m/2j, audit walk-forward |
| Candle cepat | `1m`, 90 bar (±90 menit) | volatilitas cepat, CVD 5m/15m |
| Buku order | `depth?limit=100` + `trades?limit=500` | Top-5 Bid/Ask, filter spoof jarak |
| Akumulator WS | browser, jendela 10 menit | opsional, lewat `POST`; dihaluskan bobot `e^(−λ·umur)` |

Field `source5m` / `source1m` / `sourceBook` plus `sourceNotes`
(`live` \| `seed` \| `sintetis-dari-5m`) menyatakan **secara jujur** mana feed
live dan mana data deterministik. Bila candle 1 menit tidak terjangkau, 1m
diturunkan dari 5m (`derive_1m_from_5m`) dan ditandai `sintetis-dari-5m` —
bukan diklaim sebagai data live.

## Rumus

### 1. Volatilitas multi-skala `D(x, t)`

- `v5_slow` = campuran EWMA(0.92) 72 bar + Parkinson (24 bar ber-bobot `e^(−0.10·umur)`).
- `var1_slow = v5_slow / 5` (varians per menit struktural).
- `var1_fast` = EWMA(0.93)+Parkinson dari 1m, dijepit `[0.30, 3.20] × var1_slow`.
- Rata-rata integral pada horizon `H` dengan `τv = 18` menit:
  `w = (τv / H)·(1 − e^(−H/τv))`, `var1_eff = var1_slow + (var1_fast − var1_slow)·w`.
- Difusi: `D(x,t) = var(t) · (1 + down_asym·max(0, −x/s) + tail_eta·min((x/s)², 16))`
  dengan `s = max(σ_H, band)`, `tail_eta` dari kurtosis dan `down_asym` dari rasio varians negatif/positif.

### 2. Rezim `VR` (Lo–MacKinlay, uncentered)

- `VR = E[(Σ_q r)²] / (q · E[r²])`, `q = 4` (5m) dan `q = 5` (1m); gabungan `0.65·VR5m + 0.35·VR1m`.
- `ER` = Kaufman Efficiency Ratio pada 2 jam.
- `VR ≥ 1.06 atau ER ≥ 0.28` → **Tren Berlanjut**; `VR ≤ 0.86 dan ER ≤ 0.20` → **Sideways**; selain itu **Transisi**.
- Versi *uncentered* dipakai supaya drif konstan terbaca sebagai tren (versi *demeaned* salah menandainya sebagai mean-reversion).

### 3. Arus taker multi-jendela

`cvd_w = Σ e^(−λ·umur) · (2·takerBuy − volume) / Σ e^(−λ·umur) · volume` untuk
5m (`λ=0.18`), 15m (`0.08`), 30m (`0.14`), 2j (`0.045`); akselerasi
`= cvd_15m − cvd_2h`. Partisipasi `part_ratio = trades_30m / trades_2j_sebelumnya`.

### 4. Penyerapan iceberg 15m

Aktif bila `|cvd_15m| ≥ 0.14` dan volume relatif `≥ 0.95`:
`prog = clamp(arah·ret_15m / (|cvd_15m|·1.15·σ_15m), 0, 1)`,
`abs_idx = clamp(strength·(1 − prog), 0, 1)`, sisi = lawan arah CVD bila `abs_idx ≥ 0.38`.

### 5. Harga wajar: VWAP `e^(−λd)` + Stoikov micro-price

`λ = clamp(1/(1.6·σ_H), 35, 380)`; bobot tiap bar
`volume · e^(−λ·|typical − harga|) · e^(−0.032·umur)`. Micro-price memakai
`lo/hi` 3 bar + `taker buy`; bila buku live tersedia, `0.60·micro_buku + 0.40·micro_bar`.
`eq = w_vwap·VWAP + (1 − w_vwap)·micro`, `w_vwap = 0.80` (sideways) / `0.65` (lainnya).

### 6. Drif PDE `μ(x, t)`

`μ(x,t) = μ_ob·e^(−t/τ_ob) + μ_fast·e^(−t/τ_fast) + μ_cvd·e^(−t/τ_cvd)
+ μ_struct·e^(−t/τ_struct) − κ·(x − x_eq)`, dengan `τ = 2.5–12 / 8 / 24 / 48` menit
dan `κ = 0.005–0.034` menurut rezim + penyerapan. Komponen cepat dinonaktifkan
oleh penyerapan iceberg (faktor `1 − 0.65·abs_idx`) agar arus palsu tidak
mendorong kurva.

### 7. Dua solusi Chang–Cooper

- **Ruang bebas** (`fokker_planck.solve_fokker_planck`): `P(Naik)`, `P(Turun)`,
  `P10…P90`, `expectedMovePct`, kurva `p(x,T)` untuk dibandingkan dengan v1 baseline.
- **Batas serap** (`solve_absorbing_first_passage`, `p(±band, t) = 0`): skema
  backward-Euler implisit, non-negatif, dengan `P(Target duluan) + P(Batas bawah duluan) + P(Belum kena) = 1`.

### 8. Kalibrasi & rem

Audit walk-forward kronologis (tanpa mengintip masa depan) tiap `step_bars = 3`.
Bila `win-rate < 0.47` dan `Brier > 0.258` pada ≥ 12 sinyal, semua drif dikali
`brake ∈ [0.58, 0.82]` (ambang sinyal otomatis diperketat).

## Guard penyajian (bukan bagian rumus)

Hal ini **tidak** mengubah prakiraan, hanya memastikan level yang ditampilkan
tidak menyilang harga:

1. Titik tunggu bawah selalu `< harga acuan` dan target selalu `> harga acuan`
   (margin 0.015%) — mencegah range tunggu menyilang harga saat tembok bid/ask
   menempel tepat di spot.
2. Catatan tembok hanya ditampilkan bila temboknya berada di sisi yang benar
   (bid di bawah harga, ask di atas harga).
3. `midPrice` dari akumulator WS browser diabaikan bila menyimpang >25% dari
   harga candle, dengan catatan >3% dimunculkan sebagai peringatan.
4. `qtyUnit` menyatakan satuan kolom jumlah (`koin` untuk bursa spot biasa,
   `kontrak` untuk mirror `binance.us`).

## Batasan yang perlu diketahui

- Saat endpoint bursa tidak terjangkau, seluruh payload memakai data deterministik
  (ditandai `seed` / `sintetis-dari-5m`); angka tetap konsisten, tetapi bukan harga nyata.
- Win-rate walk-forward bersifat sampel kecil (±30–60 titik per koin/jendela) —
  arah dan besaran Brier lebih informatif daripada selisih win-rate tipis.
- Peluang `P(...)` adalah keluaran model, bukan jaminan hasil.
