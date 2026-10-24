# Clean Data Flow — Mathematical & Statistical Audit (Phase 1)

Scope: `backend/analysis.py`, `backend/main.py`, `backend/market_data.py`,
`backend/market_manager.py`, `backend/market_cache.py`, `backend/tests/`,
frontend `index.html` / `script.js` / `style.css`.
**No formulas, parameters, or backend files were modified for this report.**

---

## 1. Executive summary

- The latest-only detectors (LOF, Autoencoder, Transformer) are the strongest part
  of the codebase: causal per-bar features (bar *i* uses only bars *i* and *i−1*),
  scaler/model/threshold fitted on history only, the latest bar scored separately,
  honest `available: False` outcomes instead of fabrications, trailing-incomplete-bar
  exclusion, and dedicated causality tests. No look-ahead bias or data leakage was
  found in them.
- Three genuine defects live in the *deterministic* filters:
  1. **Volume–Price Divergence excludes the latest close and latest volume from
     its own change window** (`values[-period-1 : -1]`), so the signal describes the
     *previous* bar, not the latest one, and price/volume horizons can silently
     misalign after zero/missing-volume filtering. **(High)**
  2. **Volume Spike ignores `period` when building its baseline** — it averages
     *all* preceding bars in the returned window, not the `period` bars before the
     latest. The documented and real behaviour differ, and the result changes with
     the requested history length. **(High)**
  3. **RSI is Cutler-style (simple window mean), not Wilder RSI**, and SMA/RSI have
     no parameter validation (`sma_period=0` → `ZeroDivisionError`; they are currently
     internal-only, so severity is held down). **(Medium)**
- Thresholds everywhere (2.0× ratio, 2·σ error bands, contamination=0.05, ratios
  0.236…) are conventional defaults / arbitrary, not empirically validated. The UI
  is honest about this ("statistical outlier, not proof of manipulation").
- The Volatility Anomaly filter compares absolute True-Range levels, which is
  distorted by price-level drift over long windows; TR/close (as LOF/AE/TR use)
  would be scale-free. **(Medium)**
- Test coverage is unusually good (279 backend tests including causality
  re-verification per prefix). Missing tests concentrate on the divergence
  window-exclusion bug and the volume-spike baseline length.

---

## 2. Comparison table

| # | Filter | Equation (as implemented) | Default | Exposes own-bar in its own baseline? | Look-ahead? | Missing/NaN handling | Biggest defect (severity) |
|---|--------|---------------------------|---------|--------------------------------------|-------------|----------------------|---------------------------|
| 1 | SMA | `SMA = mean(last P closes)` | P=20 | Yes (conventional for SMA) | No | None-close bars dropped from series | No `P ≤ 0` validation → ZeroDivisionError (Low, internal-only) |
| 2 | RSI | Cutler RSI: simple mean of gains/losses over last P close-to-close changes; `RSI = 100 − 100/(1+G/L)`; L=0 → 100 (G>0) else 50 | P=14 | Yes (conventional) | No | None closes dropped → changes may span a multi-bar gap | Not Wilder smoothing (Medium); `P≤0` silently returns 50 (Low) |
| 3 | Fibonacci | `level(r) = swingHigh − r·(swingHigh − swingLow)`, r∈{0.236,0.382,0.5,0.618,0.786} over the whole returned window | fixed ratios | Yes | Whole-window (non-causal, disclosed) | None high/low excluded | No trend direction (always measured down from the high) (Low) |
| 4 | EMA Deviation | `EMA_t = (c_t − EMA_{t−1})·α + EMA_{t−1}`, α=2/(P+1), seed=SMA(first P); `dev = (c_last − EMA_last)/EMA_last × 100` | P=20 | Yes | No | None closes dropped; no close>0 guard against 0-division | Zero/negative prices can crash or invert sign (Low) |
| 5 | Volume–Price Divergence | `pct(v) = (v_end − v_start)/|v_start| × 100` over `values[-P−1 : −1]` (latest **excluded**); signal from signs of price & volume pct | P=20 | No — **latest bar excluded from its own signal** | No | Zero/missing volumes filtered (compresses horizon) | Latest-close/volume exclusion + horizon misalignment (High) |
| 6 | Volume Spike | `ratio = v_latest / mean(pos volumes of bars preceding latest)`, labels 2.0×/1.5×/1.0× | P=20 | No (latest excluded ✓) | No | Zero baseline volumes filtered; zero baseline → unavailable | `period` ignored for baseline length (High) |
| 7 | Volatility Anomaly | `TR = max(H−L, |H−C₋₁|, |L−C₋₁|)`; `ratio = mean(TR last P) / mean(TR all earlier)` | P=20 | No (recent window excluded ✓) | No | None TR filtered; zero baseline → unavailable | Absolute TR not scale-free across price drift (Medium) |
| 8 | LOF | sklearn `LocalOutlierFactor(novelty=True)` on 3 causal features, StandardScaler fit on history only; score = −score_samples; anomaly iff decision<0 | n=20, contamination=0.05 | No (latest excluded from fit ✓) | No | Invalid rows skipped, gaps fail honestly, constant features handled | Correlated features + arbitrary contamination (Medium) |
| 9 | Autoencoder | MLPRegressor 3→(8,4,8)→3, X→X on scaled history; error = mean((x−x̂)²); threshold = mean+2σ of train errors | layers=(8,4,8) | No ✓ | No | Same robust pattern as LOF | Threshold calibrated on in-sample (training) errors → optimistic (Medium) |
| 10 | Transformer | Learned W_pos + W_in (seq_len 8 → d16, 2 heads, FF 32) → mean-pool → linear → predicts **next-step** 3 features; SGD batch=1, MSE; threshold = mean+2σ of train-sequence errors | seq=8, epochs=500 (→120 for >80 obs), lr=1e-3 | No ✓ | No | Same robust pattern; history bounded to 180 bars, disclosed | Same in-sample-threshold optimism (Medium) |

---

## 3. Exact equations and parameter values (per filter)

### 3.1 SMA — `analyze_bars()` (analysis.py L59–61; API defaults; **not** user-overridable)

```
SMA_t = (1/P) · Σ_{i=t−P+1..t} close_i
```
- Default P = 20. Needs ≥ P non-None closes. No validation of P.
- Matches the conventional definition. Includes the latest bar (conventional).
- Bars with `close` = None are dropped, so "last 20 closes" may reach further
  back than 20 bars — acceptable, but undisclosed.

### 3.2 RSI — `analyze_bars()` (L63–71)

```
changes = [close_i − close_{i−1}]ᵢ over the last P changes
G = mean(max(Δ,0)), L = mean(max(−Δ,0))
RSI = 100 − 100/(1 + G/L)      (L = 0 → 100 if G>0, else 50)
```
- Default P = 14; needs P+1 closes (code checks `len(closes) > rsi_period` ✓).
- **Smoothing method: simple arithmetic mean over a fixed window = Cutler's RSI,**
  *not* Wilder's recursively smoothed RSI (`avg_t = (avg_{t−1}·(P−1) + x_t)/P`,
  α = 1/P). Values coincide asymptotically in trends but differ near reversals;
  the UI method string honestly says "simple average gains/losses".
- Flat prices: G = L = 0 → returns 50. L = 0 with G > 0 → 100. Both handled and
  unit-tested (`test_flat_prices_rsi_50`, `test_all_gains_rsi_100`).
- `rsi_period = 0`: `range(len, len)` is empty → G=L=0 → silently returns 50.0
  (misleading; unreachable via API today because main.py calls with defaults).

### 3.3 Fibonacci — `analyze_bars()` (L73–83)

```
swingHigh = max(all returned highs); swingLow = min(all returned lows)
level(r) = swingHigh − r · (swingHigh − swingLow),  r ∈ {0.236, 0.382, 0.5, 0.618, 0.786}
```
- Requires span > 0, else `fib = None` (honest).
- Matches retracement-down-from-high; but with **no trend direction test** — in a
  downtrend the same arithmetic is presented without noting the swing order
  (high before vs after low). Levels also depend wholly on whichever window the
  user requested (3mo vs 1y give different levels); the `method` string discloses
  this. No 0.0/1.0 boundary or extension levels (1.272/1.618) — cosmetic gap.

### 3.4 EMA Deviation — `_ema()` + `calculate_ema_deviation()` (L94–174)

```
α = 2/(P+1)
EMA_t (seed, index P−1) = SMA(values[0..P−1])
EMA_t = (close_t − EMA_{t−1})·α + EMA_{t−1}      (recursive, α verified correct)
deviation% = (latest_close − latest_EMA)/latest_EMA × 100
```
- Default P = 20; supported {5,10,15,20,25,30}; unsupported period → honest
  `available: False`. Warm-up: EMA defined from index P−1 ✓.
- **α = 2/(P+1) verified**; initialization is the standard SMA-seed variant of the
  conventional definition.
- No guard that closes > 0: a zero or negative close makes the divisor ≤ 0 →
  sign inversion or ZeroDivisionError (not currently possible via validated
  market data, still worth guarding).

### 3.5 Volume–Price Divergence — `calculate_divergence()` (L177–285)

```
window for both series: values[−P−1 : −1]      ← latest element EXCLUDED
pct(v) = (v_last_of_window − v_first_of_window)/|v_first_of_window| × 100
price_up/down = sign(pct_price); volume_up/down = sign(pct_volume)
price↑ & volume↓ → "bearish"; price↓ & volume↑ → "bullish";
same direction → "aligned"; else "neutral"
```
- Default P = 20; supported {5..30}; needs ≥ P+1 closes and ≥ P+1 **non-zero**
  volumes.
- **Defect (High):** the observation being explained never touches the signal —
  the window ends at the *previous* bar. The intent (docstring, explanation text,
  the change attributable to the latest session) requires `values[−P−1:]`.
- **Defect (Medium):** closes and volumes are filtered independently; with
  missing/zero volume days the two endpoint-pairs can cover different bar
  ranges, so the comparison isn't strictly same-horizon.
- Statistical meaning: a 2-point endpoint slope over P bars is a weak, noisy
  statistic (no magnitude normalization, no robustness, no significance). The
  labels "bullish/bearish divergence" are conventional divergence *names* but
  the statistic is far simpler than textbook divergence (which usually compares
  an oscillator's direction with price's). Honest framing is present in the UI.

### 3.6 Volume Spike — `calculate_volume_spike()` (L335–417)

```
latest_volume = volumes[-1]
baseline = mean(pos volumes of ALL bars before the latest)   ← period IGNORED
ratio = latest_volume / baseline
label: >2.0 "spike"; ≥1.5 "high"; ≥1.0 "elevated"; else "normal"
```
- Default P = 20; needs ≥ P+1 volumes.
- **Latest bar excluded from its own baseline ✓** (user check satisfied).
- **Defect (High):** `baseline_volumes = all_volumes[:-1]` — the entire returned
  history, not the last `period` bars before the latest. The docstring says "a
  configurable number of preceding bars"; with 250 bars and P=20 the baseline is
  249 bars. Consequences: (a) `period` only acts as a minimum-data gate; (b)
  results differ between 3mo/1y windows for the same latest bar.
- Zero volumes are excluded from the baseline (documented, honest); a zero
  latest volume yields ratio 0 → labelled "normal" (arguably worth its own
  "no volume" label).

### 3.7 Volatility Anomaly — `_true_range()` + `calculate_volatility_anomaly()` (L295–524)

```
TR_t = max(H_t − L_t, |H_t − C_{t−1}|, |L_t − C_{t−1}|)     (first bar: H − L)
recent_atr  = mean(last P valid TR)
baseline_atr = mean(all valid TR before the last P)
ratio = recent_atr / baseline_atr
label: >2.0 "anomalous"; ≥1.5 "elevated"; ≥1.0 "slightly elevated"; else "normal"
```
- Default P = 20; needs ≥ P+1 valid TRs; zero baseline → honest unavailable.
- Not Wilder-ATR (simple means, not smoothed). Baseline is whole-history mean as
  *documented*, so no contract break — but statistically the whole-history mean
  is contaminated by regime drift.
- **Defect (Medium):** absolute-TR comparison across different price levels — a
  stock at ₹2,900 has mechanically larger TR than the same stock at ₹1,400.
  Scale-free variant `TR_t / close_{t−1}` (already used by LOF/AE/TR) removes
  the drift bias; the current design can mislabel high-priced growth periods.
- First-bar TR seed (H−L) slightly biases the baseline upward when the window
  is very short (Low).

### 3.8 LOF — `calculate_lof_anomaly()` (L39–43, L552–884)

```
features per bar i (causal):  ret% = (C_i − C_{i−1})/C_{i−1}·100
                              vol% = (V_i − V_{i−1})/V_{i−1}·100
                              TR%  = TR_i / C_{i−1}·100
StandardScaler fit on history rows only → LocalOutlierFactor(novelty=True,
n_neighbors=20, contamination=0.05) fit on scaled history
lof_score = −score_samples(latest); threshold = −offset_;
is_anomaly = decision_function(latest) < 0
```
- Validation: `n_neighbors` int in [2,200]; `contamination` finite in (0, 0.5].
- Min history: 20 observations **and** `n_neighbors + 1`.
- Self-influence: **none — the latest row is excluded from fit** (`rows[:-1]`),
  scaler fit on history only. No future data (row i uses only i, i−1).
- Trailing incomplete bars: excluded from scoring; an internal gap that makes
  "latest" ambiguous fails honestly (`available: False`) instead of scoring an
  older bar as if it were the latest.
- Semantics: `contamination` is a training-set prior that sets the offset, **not
  a probability** that the latest bar is anomalous; LOF score is a density ratio
  (≈1 is "normal"), not a calibrated probability. The UI text does not claim
  probability — correct.
- Defects (Medium): correlated features (TR% strongly co-moves with |ret%|),
  which inflates effective dimensionality; fixed contamination is arbitrary.

### 3.9 Autoencoder — `calculate_autoencoder_anomaly()` (L891–1138)

```
X = scaled history (3 features); model MLPRegressor(8,4,8), ReLU, adam,
    α(L2)=1e-4, max_iter=3000, seed=42, shuffle=False, early_stopping=False
objective: X → X (MSE)
hist_error_j = mean((X_j − X̂_j)²)          (training rows only)
threshold = mean(hist_error) + 2·std(hist_error)     (fallback: max, else 1.0)
latest_error = mean((x_latest − x̂_latest)²);  anomaly iff latest_error > threshold
```
- Min history: 15 observations.
- Scaling fit on history only; latest row excluded from training and threshold ✓;
  no future data ✓; deterministic (seeded adam) ✓ (unit-tested).
- **Defect (Medium):** the threshold is calibrated on **in-sample** training
  errors, which are biased low (the network memorizes noise in training rows).
  A held-out-fit (train/validation split) or a robust statistic
  (median + k·MAD) would be better calibrated.
- Layout note: first hidden layer 8 > input 3 risks near-identity mapping,
  limited only by the 4-unit bottleneck; (3,2,3) or tied weights would enforce
  compression (Low).

### 3.10 Transformer — `_TinyTransformer` + `calculate_transformer_anomaly()` (L1145–1790)

```
features per bar i: same 3 causal features as LOF
sequences from history only:  features[t : t+seq_len]  →  features[t+seq_len]
model: X(seq,3) → W_in + learned W_pos → 1·self-attention (2 heads, d_model 16)
       → FFN (32) → residual → mean-pool → linear → 3 next-step outputs
training: per-sample SGD (batch 1), MSE loss, grad-clip 1.0, Adam-free plain SGD
hist_errors = per-sequence mean((pred − y)²) over training sequences only
threshold = mean + 2·std(hist_errors)     (fallback: max, else 1.0)
score = mean((pred(x_hist[-8:]) − feature_of_latest)²); anomaly iff score > threshold
```
- Defaults: seq_len 8, epochs 500 (reduced to 120 when history > 80 rows,
  disclosed), lr 1e-3, history bounded to last 180 bars (excluded older bars
  disclosed). Validation: seq_len ≥ 2, epochs ≥ 1, lr > 0 finite.
- Scaling: none beyond percent features (already ~O(1)) — acceptable, disclosed.
- Train/test separation: there is **no hold-out** — training, threshold
  calibration, and history errors all use the same rows (same Medium issue as
  the AE). The latest bar itself is excluded from training ✓.
- Causality: sequences predict the *next* step from the *previous* seq_len
  features; the latest feature row never enters training ✓.
- Determinism + caching: identical calls reuse a cached result (TTL 900 s,
  env-controlled) — documented and tested.

---

## 4. Defects classified

**Critical — none found.** (No crashes reachable via the API; no fabricated data
paths; no leakage/look-ahead in the detectors.)

**High**
1. `DIVERGENCE-measures-previous-bar`: divergence window `[-P-1:-1]` excludes the
   latest close AND latest volume — the reported signal describes the bar before
   the latest, contradicting the evaluated-bar timestamp it is paired with.
2. `VOLUME_SPIKE-period-ignored`: baseline is "all preceding bars", not the
   documented `period` bars; parameter is silently reduced to a data-sufficiency
   gate.

**Medium**
3. RSI: simple-mean (Cutler) smoothing presented without a comparison caveat to
   Wilder's RSI; method string is honest but the number will differ from any
   Wilder-based reference chart.
4. Volatility: absolute (currency) True Range compared across price levels —
   scale distortion over drift; `TR/close₋₁` recommended.
5. AE + Transformer thresholds calibrated on in-sample training errors
   (optimistic; false-positive-prone). Validation-split or MAD-based calibration
   recommended.
6. Divergence: price vs volume horizon misalignment after independent
   zero/missing filtering of the two series.
7. LOF: correlated feature triplet; contamination as a training prior is an
   arbitrary operating point (honestly disclosed, still arbitrary).

**Low**
8. SMA/RSI/EMA-deviation lack period validation (`P ≤ 0` paths: ZeroDivisionError
   for SMA, misleading 50.0 for RSI). Not user-reachable today — internal-only.
9. EMA/deviation: no close>0 guard (divisor can be ≤ 0 on pathological data).
10. Fibonacci: no trend-direction bookkeeping; window-dependent levels.
11. AE: bottleneck layer 4 < input 3 exists, but first layer 8 > 3 weakens the
    compression requirement; tied weights would strengthen it.
12. Fixed label thresholds (2.0×, 1.5×, 0.05, 2·σ) are conventional defaults —
    not empirically optimal, and *not claimed* to be anywhere in the UI.

---

## 5. Recommended mathematical corrections (no code changed yet)

1. **Divergence:** compute `pct` over `values[-P-1:]` (include the latest), and
   build price and volume windows *from the same bar subset* (intersect the two
   validity sets, then take endpoints) so horizons always match. Add a test
   asserting the latest close/volume are inside the window.
2. **Volume Spike:** baseline = mean of positive volumes in
   `all_volumes[-P-1:-1]` (exactly `period` bars before the latest), keeping
   latest exclusion. Update the docstring and add a "baseline length == period"
   test.
3. **RSI:** either switch to Wilder smoothing (seed = first simple average, then
   `avg_t = (avg_{t−1}·(P−1) + x_t)/P`) or keep Cutler and add "Cutler-style"
   to the method string to prevent cross-tool confusion.
4. **Volatility:** use `TR_t / close_{t−1}` (percent TR) for both windows;
   optionally keep the absolute TR as a second, labelled field.
5. **AE/Transformer calibration:** split history into train/validation (~85/15),
   calibrate the threshold on validation errors; or use `median + 3·MAD` of
   train errors (robust to the low-error bias).
6. Guards: validate `sma_period ≥ 1`, `rsi_period ≥ 1` before use; guard
   non-positive closes in EMA deviation.

---

## 6. Suggested parameter ranges (justification, not "optimal")

| Parameter | Current | Suggested range | Justification |
|---|---|---|---|
| SMA period | 20 | 20–200 | NSE daily liquidity: short windows are noise-dominated; the current default is fine as a display baseline |
| RSI period | 14 | 14 (Wilder) | 14 is the Wilder standard; changing it without a validation study would be arbitrary |
| Fib window | whole returned range | keep, disclose | levels are a *range view* of the requested window by design |
| EMA deviation period | 20 | 20–50 | shorter windows raise false "extended" readings in trending NSE names |
| Divergence lookback | 20 | 20–60 | endpoint-slope noise shrinks with longer windows |
| Volume-spike baseline | 20 (intended) | 20–30 | matches "average daily volume" conventions used on NSE trackers |
| Volatility window | 20 | 20–60 | monthly horizon is a common risk baseline |
| LOF n_neighbors | 20 | 10–40 | 20 ≈ sqrt(≈400 obs); bounded by returned-window size |
| LOF contamination | 0.05 | 0.01–0.10 | labels more than ~10% *all* outliers risk crying wolf |
| AE layers | (8,4,8) | (4,2,4)–(8,4,8) | compression should bind; keep ≤ 2·features in layer 1 |
| AE/Transformer k | 2.0 | 2.0–3.0 | 3σ ≈ 0.3% false-positive rate under normality |
| Transformer seq_len | 8 | 8–16 | too long starves `min_sequences` with 180-bar cap |

These are starting ranges; none has been empirically validated in this repo, and
no claim of optimality is made.

---

## 7. Missing tests (existing coverage is otherwise strong)

1. Divergence: test that the **latest** close and volume participate in the
   change window (would fail against current code — pins the fix).
2. Divergence: same-horizon guarantee when zero-volume days are present.
3. Volume Spike: baseline restricted to `period` bars (would fail today).
4. RSI: behaviour with a None-close gap mid-series (documents the multi-bar
   change distortion).
5. SMA/RSI/EMA: `period ≤ 0` guard after the recommended validation lands.
6. AE/Transformer: threshold on a validation split (after §5.5) + a synthetic
   "clean train / spike validation" set proving calibration, not memorization.
7. Fibonacci: downtrend ordering case (low before high) to document semantics.
8. Volatility: price-drift case where baseline TR is at half the price level
   (documents the scale-free-tradeoff decision).

Existing coverage actually verified (file `backend/tests/test_analysis.py` and
friends): empty/insufficient inputs, exact-boundary warm-ups, None filtering,
all-gain/all-loss/flat RSI, divergence 4-signal matrix, spike/zero-baseline,
volatility elevated/normal/zero-baseline, LOF/AE/TR normal vs injected anomaly,
insufficient history, invalid parameters, constant features, determinism,
latest-bar exclusion, trailing-incomplete handling, long-history bounding and
epoch reduction, transformer cache identity, plus causality re-scans
(`test_historical_results_are_causal_when_future_bars_are_removed`,
per-prefix reference sweeps for volume/volatility/divergence).

---

## 8. Data leakage & look-ahead assessment

- **Look-ahead was checked three ways in the code itself:**
  `_lof_feature_rows` only ever combines bar *i* with bar *i−1*; all three
  neural detectors split `rows[:-1]` (history) from `rows[-1]` (latest) *before*
  fitting scaler/model/threshold; historical playouts are re-scored per-prefix
  and re-verified against a reference sweep (`calculate_historical_anomalies` +
  per-prefix tests). **No future bar is used anywhere in the detectors.**
- **Incomplete last candle:** handled by `_trailing_incomplete_bars` — trailing
  partial bars (Yahoo's in-progress session) are excluded from scoring; if a
  *complete* bar follows the latest scorable row (internal gap), the filter
  returns honest `available: False` instead of silently scoring an older bar.
  The UI shows "usable OHLCV bar(s)" counts and excludes unusable bars from
  charts/indicators, never pretending a forming bar is complete.
- **Arbitrary thresholds:** present (§6) but labelled as statistical heuristics;
  no filter output is presented as a probability anywhere (checked the frontend
  copy for all three detectors).
- **Score-as-probability:** the UI explicitly says "statistical outlier, not
  proof of manipulation or a guaranteed trading signal" — no probability framing.
- **Overlapping filters:** Volume Spike and Volatility Anomaly both fire on
  "latest bar moved far from its history" (volume level vs TR level) and often
  co-fire on genuine shocks; LOF/AE/TR share the exact same 3-feature basis
  (ret%, Δvol%, TR%), so LOF, AE and Transformer are three detectors over one
  feature view — co-flagging is expected and correlated, not independent
  evidence. The UI groups them and never adds the flags.

---

## 9. Overlapping filters (detail)

| Cluster | Members | Shared signal | Consequence |
|---|---|---|---|
| Density/reconstruction on same features | LOF, Autoencoder, Transformer | ret%, Δvol%, TR% rows | Triple agreement is one factor, not three |
| Bar vs history shift | Volume Spike, Volatility Anomaly | latest level vs baseline level | Co-fires on shock bars; different fields (V vs TR) so not strictly redundant |
| Trend/distance | EMA deviation, Fibonacci | price displacement | EMA deviation is local; Fibonacci is window range — complementary |

---

## 10. Recommended historical validation methodology

1. **Per-prefix playout (already implemented — keep it):** for every cut-off t,
   compute each detector using only bars ≤ t and verify against the reference
   implementation; guards against future regressions introducing leakage.
2. **Threshold calibration study (proposed):** on a 1-year daily panel of
   ~20 liquid NSE names, measure (a) base rate of flags via the current k=2σ and
   contamination presets, (b) whether flags concentrate on days with known
   external causes (results days, index rebalances, bulk/block deals — data
   obtainable from NSE bhavcopy metadata), (c) precision@k of "flagged bar is a
   >3σ TR or >2× volume day". Tune k / contamination only against this evidence.
3. **Label churn metric:** for each parameter set, count how many bars flip
   flag-state when the window length changes from 3mo→1y; Volume Spike (bug §3.6)
   will currently fail this badly, giving a concrete acceptance test.
4. **Cross-scanner agreement matrix:** LOF vs AE vs TR co-flag rate; if ≈100%,
   collapse the UI into one "neural cluster" verdict with per-detector evidence
   cards rather than implying independent confirmation.
5. **Cost of a false positive:** nothing here is a trading signal; any future
   parameter tuning must optimize explanation quality (does the flagged bar's
   explanation match the published event?) rather than PnL.

---

## Bottom line

`calculate_historical_anomalies`, the neural detectors, and the honest-availability
plumbing are the parts worth defending as-is. The two High defects (divergence
window, volume-spike baseline length) are one-line slice fixes plus new tests.
Nothing was changed in this phase, exactly as instructed.
