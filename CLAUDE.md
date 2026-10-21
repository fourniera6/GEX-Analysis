# GEX Analysis Service — Built for the Stack, Not Just to Replace GEXBot

**Status:** Working service. It computes GEX levels for SPX/SPY, NDX/QQQ and RUT/IWM from CBOE's free delayed chains and writes `gex_pack.txt` for the NT8 indicator, plus a richer JSON per symbol. Next: side-by-side validation against the vendor on live charts.
**Goal:** A self-contained Python service that computes a *richer* gamma analysis than any public vendor exposes, designed specifically to feed into AJ's existing futures scalping infrastructure (ConfluenceAlertSystem, RegimeClassifierV1, LevelTracker) on MES.

```
python -m gex_analysis.main --pack-path "<Documents>\NinjaTrader 8\gex_pack_service.txt"             # once
python -m gex_analysis.main --pack-path "<Documents>\NinjaTrader 8\gex_pack_service.txt" --intraday  # now + every 30 min to 15:45
python -m gex_analysis.compare <vendor gex_pack.txt> data/packs/<session>_<pre-market HHMM>.txt
python -m gex_analysis.timeline [YYYY-MM-DD]   # page showing how the levels moved through a session
pytest
```

**What this replaces.** Today AJ's levels come from a vendor scanner, pasted daily into `gex_pack.txt`. That file is read by AJ's own NT8 indicator, `GexLevelsPastePack.cs` (about 1,950 lines, in the NinjaTrader project), which renders the levels, overlays the ETF sibling's levels, shows prior-day ghosts and confluence tags, and exposes a public `CurrentGammaRegime` and proximity API. The indicator originally mirrored a closed TradingView indicator (OutsideRedge "GEX Levels"), which is how the vendor pack format came about. **This service replaces the vendor and the manual paste:** it writes a spec-compliant `gex_pack.txt`, so every NT8 chart works with no indicator changes. The richer JSON is the path for data the pack can't carry.

**The output contract is `docs/GEX-PACK-SPEC.md`**, extracted from the live indicator source. Where it and this doc overlap on the pack format, the spec wins.

**What we match and what we don't.** We match the vendor's *semantics*: what a Call Wall, Put Wall, Transition Level or Dealer Pivot means, because AJ's trading reads the levels that way. The definitions below were chosen by comparing against the vendor's actual levels. We don't tune knobs to chase the vendor's exact numbers. Differences within a few points are expected and fine. Edge is decided by Layer 3 validation, not vendor parity.

---

## Why this exists (the actual reasons)

The four reasons to build, ordered by priority for AJ's edge:

1. **Surface gamma information the vendor can't.** Charm exposure, vanna with sign, gamma profile shape, gradient at spot, wall test degradation — these aren't in the vendor pack and they're directly actionable for intraday scalping.
2. **Integrate natively with the existing stack.** ConfluenceAlertSystem wants a continuous score, not a discretized "Moderate" tag. RegimeClassifierV1 already does price-action regime classification — combining gamma regime with that gives a 2x2 alignment signal that no off-the-shelf tool produces.
3. **Tune for futures and the trading windows that matter.** AJ trades MES at 9:30–11:00 AM and 15:00–16:00 ET. These are very different gamma environments and deserve different methodology.
4. **Own the methodology and remove the manual step.** No vendor dependency, no daily paste, no hand-typed refs (the 25 Sep file had `QQQ 7.45.4`, which silently broke the NQ ratio).

If a phase doesn't move toward one of these, it's the wrong phase.

---

## What we're keeping vs. replacing

| Aspect | Vendor pack (today) | This service |
|---|---|---|
| Level count | Fixed 12 slots per symbol (CW, PW, TL, GD1–4, C1–4, DP) | Same 12 slots in the pack; variable peak lists in JSON |
| Magnitude | Discrete rank (Strongest / Moderate / Weakest) | Rank in the pack; continuous 0.0–1.0 score in JSON |
| Greeks | Gamma only | Gamma now; Charm + Vanna planned (Phase 3) |
| Profile data | Peak prices only | Full profile + half-widths in JSON |
| Time-of-day | Single daily snapshot | Daily now; time-aware planned (Phase 3–4) |
| Wall tracking | None | Intraday test count planned (Phase 4) |
| Regime | Pivot level only | Pivot in the pack; continuous regime confidence in JSON |
| Futures levels | Index levels + one basis | Same, with the basis implied from index options |
| Refs | Typed by hand at paste time | Written from the same snapshot, so each pair is simultaneous |
| Output | Pasted `gex_pack.txt` | Generated `gex_pack.txt` (atomic, auto-rolled `[PRIOR]`) + JSON |

---

## Level definitions

Every structural level uses dealer dollar gamma per 1% move, per strike, with dealers long calls and short puts. Net = call side + put side.

| Pack slot | Meaning | Our definition | Expiries |
|---|---|---|---|
| CW | Call Wall | Strike with the largest **positive net** GEX | 0–365 DTE |
| PW | Put Wall | Strike with the largest **negative net** GEX | 0–365 DTE |
| TL | Transition Level (vol trigger) | Shift-spot zero gamma | **0–7 DTE** |
| GD1–4 | Support-side ranks | Top 4 strikes by **negative net** GEX (put-dominant), strongest first | 0–365 DTE |
| C1–4 | Resistance-side ranks | Top 4 strikes by **positive net** GEX (call-dominant), strongest first | 0–365 DTE |
| DP | Dealer Pivot (zero gamma) | Shift-spot zero gamma | 0–365 DTE |

Ranks keep strikes at least 0.25% of spot apart, include levels on either side of spot (the indicator assigns Support/Resistance roles by price), and are padded with `0` when fewer than 4 qualify. Because walls and ranks use the same net series, **PW = GD1 and CW = C1**.

**Priority (from AJ):** TL and DP are the major levels, then CW/PW. GD/C 1–4 are minor. Validation leads with TL/DP.

**Evidence (one day, 25 Sep 2026; indicative only).** The vendor's 25 Sep pack was built from **24 Sep** close data (the date line is the session traded). Our CBOE snapshot was taken after the **25 Sep** close, so spot and IV are a day apart, and whether the open interest lines up is unverified. The first like-for-like check is the vendor's 28 Sep pack against our pre-market 28 Sep run.

| Level | SPX vendor → ours | Across SPX, SPY, NDX, QQQ, IWM |
|---|---|---|
| CW / PW | 7800 / 7500 → **7800 / 7500** | 9 of 10 exact (SPY PW 750 vs 760) |
| TL | 7710 → **7710.1** | SPY +2.0, QQQ +2.1, IWM −4.8; **NDX off by 2.6%** |
| DP | 7659 → 7668.8 | SPY +4.5, QQQ +2.6, IWM +3.4, NDX −347 |
| GD1–4 | 8000, 7700, 7000, 7800 → 7500, 7650, 7600, 7400 | differs **by design** (box-resistant; see below) |
| C1–4 | 7797, 7897, 7997, 7504 → 7800, 7900, 8000, 8100 | underlying levels 3 of 4 on SPX (see "Vendor C levels") |

- **Net walls** fix the "8000 is both walls" artifact. The 8000 strike carries huge, mostly offsetting call and put gamma (almost certainly box or conversion trades).
- **Box-resistant GD (AJ's choice, 27 Sep).** Ranking support by put-side |GEX| (the vendor's way) puts that 8000 box strike at GD1, i.e. "strongest support" 250 points *above* spot, even though offsetting positions carry little real hedging. Ranking by negative net keeps only put-dominant strikes. It's a logic fix, not a statistically proven one; `support_rank_source="put"` restores the vendor-style ranking.
- **Rejected:** a cumulative-sum zero gamma (7850–7980 for SPX), side-of-spot walls (8000/7700), and a vanna sign flip for TL.
**First like-for-like check (28 Sep, pre-market run after the stale-spot fix):**
- Median |TL| / |DP| error for SPX/SPY: 0.18% / 0.21% (all five symbols: 0.20% / 0.27%).
- CW/PW exact everywhere except SPX CW 7900 vs the vendor's 7800. Our wall confidence there was 0.06, a near tie, so treat 7800–7900 as the resistance zone.
- SPX C set matches 4/4 (only the order differs). ES basis: 61.72 vs the vendor's 61.

**29 Sep (07:29 pre-market run):** SPX/SPY median |TL| 0.04%, |DP| 0.25%. SPY TL/DP/CW/PW all within 0.4. SPX DP +34 (the vendor's DP sits persistently below ours: +20 on 28 Sep, +34 on 29 Sep; later +43, −12, +22 and +42.9 on 13 Oct, when NDX was +216 while SPY/QQQ matched within 0.4%). `python -m gex_analysis.research.dp_variants --vendor <vendor pack> [--stamp <raw archive stamp>]` recomputes DP from a saved snapshot under different expiry windows, strike bands, weeklies-only and snapshot-clock variants, and ranks them against the vendor separately for indexes and ETFs. It's read-only, and findings belong here only once a variant wins on several days. The vendor's SPX PW moved to 7650 (ours 7600, with 7650 as GD3). IWM TL −2.65% was the expired-contract bug, since fixed. RUT OI hadn't refreshed by 07:29.

**Reading wall confidence:** near 0 means the runner-up strike carries almost the same net gamma. Treat the two as a zone rather than trusting which one ranked first.

- **NDX (open, watching per AJ):** both packs put NDX CW/PW at 29475/29500, about 1,100 points below spot, with the call wall under the put wall. It's not a box but a single deep-ITM line: 3,493 Oct-16 NDX 29475 calls against 18 puts, in a thin chain (total NDX OI about 185k vs SPX's 22M). Wider expiry windows pull the NDX pivot toward that cluster (0–30 DTE: 30,025 vs our 0–7 DTE TL of 30,422), which likely explains the vendor's NDX TL of 29,450. So the vendor's NQ walls and TL are probably artifacts too. Candidate fixes are deferred until NQ matters more: an OTM-only variant for thin chains, or combined NDX+QQQ levels via the pivot-log test (QQQ holds 50–62% of the complex's gamma).
- Everything above is config (`structural_window_dte`, `transition_window_dte`, `support_rank_source`, `resistance_rank_source`). Revisit after 1–2 weeks of daily `compare` runs; don't retune on a single day.

### Vendor C levels are index levels routed through the ETF

Across the 21–25 Sep vendor packs, for all three complexes and all four consecutive day pairs, the index/futures C1–4 on day D ÷ the ETF's C1–4 on day D+1 is constant and in the same order: SPX/SPY 10.034–10.050, NDX/QQQ 41.08–41.13, RTY/IWM 10.14–10.16. Same-day ratios scatter, and no other slot shows this. The ETF values sit on index round strikes ÷ ratio: SPY 777/787/797/757 are SPX 7800/7900/8000/7600 ÷ 10.04, and IWM 283/279 are RUT 2870/2830 ÷ 10.14. SPY's own open interest wouldn't concentrate at strikes ending in 7.

So the vendor computes C on the index chain and writes it into the ETF block in ETF units (rounded, a day later). The index block carries the round trip back, which is why SPX shows 7797 rather than 7800. Consequences:
- **Our index C (positive net) already reproduces the underlying levels.** Strip the conversion and SPX C on 25 Sep is ≈ 7800/7900/8000/7500; ours is 7800/7900/8000/8100.
- **The vendor's ETF C slots aren't the ETF's own levels,** which breaks the spec's Invariant C for those slots. On AJ's charts a `+SPY C1` confluence tag was the SPX C level confirming itself, not independent ETF information. Our ETF C are native, so our `+SPY C` tags mean something.
- The TradingView "OutsiderEdge GEX Levels Paste Pack" page gives no methodology (it only plots pasted levels), so this came from the data alone.

---

## The richer data model

The JSON emitted per symbol per run. Fields marked *(planned)* aren't implemented yet; `gex_analysis/output/analysis.py` is the implemented subset.

```python
@dataclass
class GexAnalysis:
    # Metadata
    timestamp: datetime              # snapshot time (America/New_York)
    underlying: str                  # "SPX", "SPY", ..., or "SPX_SPY" / "NDX_QQQ" / "RUT_IWM" for combined views
    spot: float
    expected_move_1d: float          # ATM-straddle-derived, used for normalizing distances
    total_gex_per_pct: float         # dealer $ gamma per 1% move at spot, structural expiries

    # Continuous profiles (the source of truth — all other fields derive from these)
    gamma_profile: dict[float, float]    # strike -> net dealer GEX, normalized to [-1, 1] by max abs
    charm_profile: dict[float, float]    # (planned) strike -> dealer charm exposure
    vanna_profile: dict[float, float]    # (planned) strike -> dealer vanna exposure

    # Discrete derived levels (variable count, ranked by magnitude)
    support_peaks: list[GammaPeak]       # GD1-4 in the pack
    resistance_peaks: list[GammaPeak]    # C1-4 in the pack

    # Major walls
    call_wall: WallInfo                  # CW: largest positive net GEX
    put_wall: WallInfo                   # PW: largest negative net GEX

    # Regime
    zero_gamma: float                    # DP: shift-spot zero gamma, structural expiries
    transition_level: float              # TL: shift-spot zero gamma, short-dated expiries
    distance_to_zg_pts: float            # spot - zero_gamma: positive = spot above DP
    distance_to_zg_normalized: float     # as a multiple of expected_move_1d
    gamma_gradient_at_spot: float        # (planned) d_GEX/d_K at spot (per-point hedging pressure)
    regime: GammaRegime                  # Positive / Negative / Battleground (Transitioning planned)
    regime_confidence: float             # 0.0..1.0 — how decisively in regime

    # Time-of-day context (planned)
    minutes_to_close: int
    charm_intensity: float               # 0.0..1.0 — see compute/time_of_day.py formula below
    near_close_warning: bool             # true in last 30 min when charm is meaningful

    # Cross-cut analyses (planned)
    oi_gex_profile: dict[float, float]      # OI-based gamma (structural)
    volume_gex_profile: dict[float, float]  # Today's volume-based gamma (what's moving now)
    new_positioning: list[float]            # strikes where volume GEX deviates from OI GEX

    # State (planned, carried across runs)
    walls_tested_today: dict[float, int]    # strike -> test count for today's session
```

```python
@dataclass
class GammaPeak:
    strike: float
    magnitude_signed: float          # $ gamma per 1% move from the ranking series
    magnitude_normalized: float      # 0.0..1.0 relative to the strongest peak in the same ranking
    half_width_pts: float            # half the price span over which the ranking stays above 50% of peak
    distance_to_spot_pts: float      # strike - spot: positive = level above spot
    distance_to_spot_normalized: float  # as a multiple of expected_move_1d
    role: str                        # Support / Resistance (by price location; Broken planned)

@dataclass
class WallInfo:
    peak: GammaPeak
    confidence: float                # dominance vs. next-highest peak in the same ranking
    test_count_today: int            # (planned) 0 = untested, 1 = first test, 2+ = degraded
    distance_dollars: float          # (planned) see "Wall dollar impact"

class GammaRegime(Enum):
    Positive       # spot well above DP, mean-reverting environment
    Negative       # spot well below DP, trend-amplifying environment
    Battleground   # spot within tolerance of DP, no clear regime
    Transitioning  # (planned) spot just crossed DP with conviction (within last N runs) — TRADEABLE event
```

### Normalization basis

`expected_move_1d` is the ATM straddle mid at the nearest expiry with two-sided quotes, scaled to one trading day (`÷ √trading days to expiry`), in points. Every normalized distance field and the regime thresholds depend on it. It is **not** in the pack; the v2 format has no implied-move slots.

### Wall dollar impact (planned)

`WallInfo.distance_dollars` is the dealer hedging dollars implied by a 1-point move of spot through the wall:

```
distance_dollars = gamma_at_wall × contract_multiplier × 100 × spot
```

Contract multipliers: SPX cash = 100, MES = 5, NQ = 20, MNQ = 2. This field is provided for downstream sizing intuition; nothing in this service consumes it internally.

---

## How this plugs into AJ's existing stack

**Path today:** service → `gex_pack.txt` → `GexLevelsPastePack.cs` (hot-reloads within one bar) → its `CurrentGammaRegime` and proximity API → `ConfluenceAlertSystem`. Anything the pack can't carry (continuous magnitudes, half-widths, regime confidence, and later charm and wall tests) goes through the JSON, which needs a separate NT8 reader (Phase 6).

**Cadence.** A pre-market run (once), or `--intraday` for the pre-market run plus a full recompute every 30 minutes through 15:45 (see "Intraday mode"). Open interest is published overnight, so a **pre-market run** (around 08:45 ET) picks up the prior session's final OI, while a post-close run uses OI from the day before. **Confirmed on 28 Sep:** by 08:43 ET, OI had refreshed for SPX/SPY/NDX/QQQ/IWM (+2–8%). The RUT file lagged (last refreshed Saturday) and updated by 09:04, which the per-symbol stale-OI warning now flags. A "run" is the unit of intraday state: `transition_lookback_runs` counts service invocations, not market bars.

### ConfluenceAlertSystem integration

The current confluence flow is Context → Location → Trigger. GEX data feeds primarily into Context and Location:

**Context inputs from GEX:**
- `regime`: Positive → favor bounce setups; Negative → favor trend setups; Battleground → veto signals; Transitioning → high-attention regime-change event
- `regime_confidence`: weight the GEX regime signal in confluence scoring
- `near_close_warning`: tighten size or reduce signal weight in last 30 min when charm is mounting

**Location inputs from GEX:**
- `GetNearestPeak(max_normalized_distance)`: returns nearest peak with role + magnitude_normalized + half_width
- A bounce setup's quality multiplier scales with the magnitude_normalized of the level it's bouncing from
- Wall-test alerts: "first test of major call wall in last 30 min" is a top-tier confluence event
- `is_in_battleground`: a hard veto that ConfluenceAlertSystem should respect

**Trigger inputs from GEX:**
- `gamma_gradient_at_spot`: useful for stop sizing — high gradient means small price moves force big hedging, so stops can be tighter
- `walls_tested_today` count: third test of same wall → weight breakout setups higher than reversal setups

### Regime stacking with RegimeClassifierV1

RegimeClassifierV1 already classifies price-action regime (trending/ranging) from VWAP/Keltner/EMA inputs. Combining its output with `GammaRegime` gives a 2x2 that no individual signal produces:

| | Pos Gamma | Neg Gamma |
|---|---|---|
| **Ranging (RC)** | Bounce setups strongly favored — both signals agree on mean reversion | Caution — ranging price action in volatile regime is often pre-breakout |
| **Trending (RC)** | Mean-revert against trend has higher odds than usual — gamma will fight the trend | Trend continuation strongly favored — both signals agree on directional |

**Disagreement is informative.** When RC says trending and GEX says positive gamma, one of them is about to flip. That's a high-value attention signal.

### Time-of-day overlay with AJ's preferred trading windows

| Window | GEX state | What matters most |
|---|---|---|
| 9:30–11:00 AM | Charm not yet meaningful; gamma profile is "structural" | Use yesterday's OI levels; volume GEX of today not yet meaningful; major walls are the dominant signal |
| 11:00 AM–3:00 PM | Mid-day low-volume drift; range compression in pos gamma is real | Pos gamma + ranging RC = high-quality fade setups at peaks |
| 3:00–4:00 PM | Charm acceleration; pinning behavior | `charm_intensity` rises; nearest major peak becomes a strong magnet; reduce position size on counter-magnet trades |
| Last 30 min | `near_close_warning` active | New positioning aggressive; volume GEX deviating from OI GEX is meaningful information about overnight risk |

### LevelTracker complement

LevelTracker handles dynamic intraday levels (VWAP bands, ORB, prior session H/L/C, London session). GEX levels are options-derived structural levels. Both feed into ConfluenceAlertSystem; neither replaces the other. The high-confidence trades will be where dynamic LevelTracker levels coincide with GEX peaks — explicit cross-confirmation.

---

## Design principles

1. **Continuous over discrete wherever possible.** Magnitude is a number; expose it as a number (in JSON). The pack's fixed ranks are a compatibility constraint, not a design choice.
2. **Variable count over fixed count.** Some days have 3 meaningful peaks per side, some have 7. The peak count itself is information about market structure.
3. **Profile data over peak prices alone.** Width matters, gradient matters. A "wide diffuse call wall from 7320–7360" trades very differently than a "single-strike spike at 7330."
4. **Time-of-day is first-class.** Compute charm and vanna; weight by remaining trading time; the same Call Wall is more constraining at 15:50 than at 10:00.
5. **State persists across runs within a day.** Wall test counts, regime transitions, charm intensity — these are time series, not single snapshots.
6. **Multiple greeks, not just gamma.** Charm and vanna are cheap once you have the chain, and independently actionable.
7. **OI vs. Volume separately tracked.** They answer different questions; conflating them loses information.
8. **Vendor semantics, our numbers; edge decides.** Match what each level *means*; judge methodology by trading results (Layer 3), not by matching the vendor's exact values.

---

## Output contracts

**Timezones.** All wall-clock fields are `America/New_York`. The date in output filenames and the pack's date line is the **trading session the levels are for**. A snapshot taken at or after the 16:00 close feeds the next weekday; exchange holidays aren't modeled yet. Raw archives are named by fetch time instead.

### `gex_pack.txt` — the NT8 contract (see `docs/GEX-PACK-SPEC.md`)

Implemented in `output/pack.py`:

- **Sections:** `[REFS]` (one `SYMBOL price` line per ETF and futures symbol), `[PACK]` (date line + blocks), `[PRIOR]` (the previous session's `[PACK]`, rolled automatically when the session date changes; kept on a same-session rerun).
- **Blocks, in order:** `ES, MES, NQ, MNQ, RTY, SPX, SPY, NDX, QQQ, RUT, IWM`. Each is `SYMBOL=` followed by 12 `|`-separated values in slot order CW, PW, TL, GD1–4, C1–4, DP, written at full precision. `0` means not available.
- **Invariant A:** MES ≡ ES and MNQ ≡ NQ, byte-identical.
- **Invariant B:** each futures block is its index block plus one basis at every slot. The basis is the index options' put-call-parity implied forward at the front quarterly futures expiry (rolled 8 days early), minus the spot implied by the same quotes (see "Pricing spot" under "Data source"). ES–SPX: 62.65 on 25 Sep vs the vendor's 63, and 61.82 on 28 Sep pre-market vs the vendor's 61.
- **Invariant C:** ETF blocks are the ETF chain's own levels. The indicator converts them.
- **`[PREMARKET]` (service extension):** the session's levels as of the open, in the same block format as `[PACK]` (futures included). Any run before 09:30 ET sets it, intraday updates keep it, and the next session replaces it. The current indicator ignores unknown sections, so it's safe today. To draw it, `GexLevelsPastePack.cs` can reuse the `[PRIOR]` path (`BuildPriorDayLevels`) with a "PM" label: dotted ghost line when a current level has drifted more than 8 ticks away, a "• PM" tag when it hasn't.
- **`[HISTORY]` (service extension):** every pack written this session, one line each: the write time (ET, `2026-10-07T15:42`), a space, then that pack's v2 blocks. It restarts with each new session, and its last line equals `[PACK]`. The current indicator ignores it. `docs/NT8-HISTORY-RENDERING.md` is the indicator-side spec for drawing each level as a stepped line.
- **The `RUT` block is included,** which gives RTY charts full IWM overlay support (spec §3 gotcha).
- **Refs:** ETF ref = the ETF's official close; futures ref = the index's official close + basis. So `ratio = (futures_ref − basis) / etf_ref = index close / ETF close`, a simultaneous pair even pre-market, when ETFs trade and indexes don't. The ratio drifts only slowly, so a prior-day close pair is accurate to well under a point.
- **Written atomically** (temp file + `os.replace`, retried briefly if NT8 is mid-read on Windows).
- A mirror of the indicator's reader (`read_pack_file`, `derive_basis`, `derive_ratio`, `etf_to_futures`) backs the tests, including the spec's §4 worked example.

**Rollout:** write to a separate file (e.g. `gex_pack_service.txt`) and point a second MES chart instance's *Pack File Path* at it. Compare it against the vendor-fed chart and with `python -m gex_analysis.compare` daily, then switch the main charts over.

### JSON: `output/analysis_<SYMBOL>_<session>.json`

One file per symbol, plus a combined `SPX_SPY`, `NDX_QQQ` and `RUT_IWM` view (see "Research: combined pivots"), containing the implemented subset of `GexAnalysis`.

### Run log: open interest freshness

Each run compares every chain's total open interest with the latest earlier raw archive and prints the change. It warns about any symbol whose OI is unchanged since an **earlier day's** archive, meaning CBOE hasn't refreshed that chain yet (RUT on the morning of 28 Sep), so its levels would rest on stale positioning. It's a warning only; the pack is still written. Re-running later picks up the refresh.

### Intraday mode (`--intraday`)

Runs once immediately (the pre-market run when launched around 08:45), then every `intraday_every_minutes` (30) from `intraday_start` (09:45) to `intraday_end` (15:45) ET. Each run is a **full recompute of all 12 slots** (AJ's choice). The 15-minute feed delay means 09:45 sees the open, and stopping at 15:45 keeps every snapshot before 16:00, so the session date never rolls mid-loop.

- **Housekeeping:**
  - Raw snapshots are archived at most once per clock hour (about 8 a day instead of 14).
  - Every pack written is also saved to `data/packs/<session>_<HHMM>.txt`, and each run prints the slots that moved since the previous pack.
  - A failed fetch is logged and the previous pack stays until the next slot.
  - Slots more than 5 minutes past (e.g. after the laptop slept) are skipped, not replayed.
  - Ctrl-C stops cleanly.
- **Known behaviors (measured 28 Sep, 09:04 vs 11:42 data, SPX −38 pts):**
  - Open interest is frozen intraday (it only refreshes overnight), so updates reflect price, IV and time decay acting on existing positioning, not new positioning, and they lag 15 minutes.
  - Walls and ranks re-weight toward spot (PW 7500→7600, CW 7900→7800), and near-ties flip back and forth.
  - DP moves (+23) can flip `CurrentGammaRegime` (Positive → Battleground) while price sits still.
- **Daily vendor comparison:** use the pre-market copy in `data/packs/`, since the live pack keeps changing. The pivot log gets a row per run, which is the raw material for studying intraday DP/TL drift.
- **Hysteresis (AJ's choice, 28 Sep):**
  - On the 30-minute updates, each wall and rank slot keeps its current strike unless a new one is more than `intraday_level_hysteresis` (20%) stronger. It's implemented in `compute/peaks.py: sticky_peaks`, with the incumbents being this session's current `[PACK]`.
  - The first run of the loop and plain runs are always fresh, so new OI is never held back. TL/DP are never held.
  - A held wall's confidence reads 0, since it's a tie by construction.
  - Replaying 28 Sep: SPX PW held 7500 all day (fresh flipped to 7600), SPY PW held 750 (fresh 761), and QQQ's 730/738 flicker disappeared. SPX CW still moved 7900→7800 because 7800 overtook it decisively (confidence 0.49).
- **Pre-market reference:** every update prints the major levels that differ from `[PREMARKET]`, e.g. `SPX  TL 7710 (pre 7702.70, +7.3)  DP 7683.70 (pre 7661.10, +22.6)`. On 28 Sep the pre-market DP (7661) sat 5.6 points under the day's low while the intraday DP (7684–7687) was where price chopped. Both mattered, which is why the pre-market levels are kept.

### Seeing the moves: timeline page and chart history

Intraday updates move levels: walls migrate toward spot late in the day (SPY PW 767→777 at 15:42 on 7 Oct), and DP drifts. Two views show the session as a whole:

- **`output/timeline_<session>.html`** (`gex_analysis/timeline.py`).
  - It's rewritten after every run, so reloading the browser during `--intraday` shows the latest state. `python -m gex_analysis.timeline [session] [--all]` builds it for any recorded session from `data/packs/` and the pivot log's spots.
  - It has one panel per cash symbol: step lines for TL/DP/CW/PW, spot, pre-market references (dashed), faint GD/C ticks, labels where walls jumped, and a table of each update's levels and changes.
  - Walls too far from spot are listed under the chart instead of being squeezed in.
  - It's self-contained HTML (no network). From WSL: `explorer.exe "$(wslpath -w output/timeline_<session>.html)"`.
- **On the chart:** the pack's `[HISTORY]` section, once `GexLevelsPastePack.cs` implements `docs/NT8-HISTORY-RENDERING.md`.
  - For sessions recorded before `[HISTORY]` existed, `--pack-with-history PATH` rebuilds one to test against.

Both place a level at the time its pack was **written** (when it reached the chart), not its data time (about 15 minutes earlier), so there's no look-ahead when judging reactions.

### Future output: NT8 JSON ingestion (Phase 6)

An NT8 reader for the JSON (a new indicator, or an extension of `GexLevelsPastePack.cs`) for data the pack can't carry. Don't build it until something in the JSON has proven worth trading on.

---

## Data source

**CBOE delayed quotes JSON**: `https://cdn.cboe.com/api/global/delayed_quotes/options/{_SPX|SPY|_NDX|QQQ|_RUT|IWM}.json` (indexes take a leading underscore, ETFs don't). It's free with no key or account, and implemented in `data_sources/cboe.py`.

- **Fields per contract:** OCC symbol (root, expiry, C/P, strike), IV, open interest, volume, bid/ask, delta/gamma/vega/theta. SPX has about 29k contracts, SPY about 13k, NDX 15k, RUT 11k.
- **Settlement times:** AM-settled monthly roots (SPX, NDX, RUT) expire at 09:30; weeklies and ETFs at 16:00. Time to expiry is DST-correct.
- **Expired contracts:** time to expiry is measured from the underlying's last trade, but ETF last trades stop at 15:59:59, one second before the 16:00 expiry. Runs after the close or pre-market used to treat that day's expired 0DTE options as live. They picked up the 15-minute time floor and injected phantom gamma: on 29 Sep IWM's expected move came out at ±0.15, and SPY's TL/DP moved about 2 SPY points (~20 on the ES chart). Contracts are now dropped once expired at the snapshot's own clock (the file's `timestamp`, `Chain.alive`). Measuring *all* time to expiry from that clock was tested and deferred: on 28 Sep it improved SPX TL (7716.1 vs the vendor's 7715) but worsened DP (+0.62% vs +0.28%).
- **Freshness:** 15-minute delayed. After the close, and on weekends, it serves the last session's values.
- **Pricing spot:** outside regular hours the quote's index level (SPX, NDX, RUT) stays at the prior close, while SPX options keep trading (global trading hours) and ETF quotes move pre-market while their options don't. So each chain is priced at the **spot implied by its own option quotes**: the nearest expiry's put-call-parity forward, discounted by the carry implied between that forward and the futures-expiry forward (`compute/basis.py: underlying_level`). On 28 Sep at 08:43 the SPX quote still showed 7743.41 while its options implied 7717. Using the stale quote had pushed the ES basis to 33 and every ES/MES level about 28 points too low.
- **Futures basis comes from the same data** (implied forward; see above). No futures feed is needed, so Tickstream isn't required.
- **Caveats:** It's an unofficial, undocumented endpoint with no SLA; fine for personal use, but it could change or be blocked. It has no history (hence the raw-snapshot archive). CBOE's greeks are rounded to 4 decimals, so we compute gamma ourselves from IV. It has index options only (no ES/MES options), which is why futures levels = index levels + basis.
- **Upgrade paths**, if it breaks or we outgrow it: Schwab Developer API (free with an account), Tradier production ($10/mo), Marketdata.app Starter ($12/mo, 1-credit cached mode). Each is one new `DataSource` subclass.
- **Ruled out:** Polygon free (no options), Tradier sandbox (no greeks), Marketdata.app free (per-contract credits can't cover a full chain), E*TRADE (daily interactive re-auth), Tickstream options (a $29/mo plan; stream sampling rather than snapshots).

### Index + ETF siblings

The pack carries each index and its ETF **separately** (Invariant C), and the indicator's ETF overlay draws the ETF's levels on the futures chart, tagging confluence (`+SPY DP`) where they land near a native level. That overlay is where the ETF's signal shows up in trading.

### Research: combined pivots

Dealers hedge an index complex's exposure from both chains, so the aggregate hedging flip is where **index + ETF** dollar gamma sums to zero. This combined pivot always lies between the index-only and ETF-only pivots, since the sum of two gamma curves can only cross zero between their individual crossings. So the two lines AJ already sees on a chart bracket it, but they don't show where in the bracket it sits, and the indicator's `CurrentGammaRegime` API only knows the native DP.

On 25 Sep (index-scale points):

| Pivot | Index-only | ETF-only | Combined | Position in the bracket |
|---|---|---|---|---|
| SPX DP / TL | 7669 / 7710 | 7725 / 7720 | 7676 / 7711 | 12–13% toward SPY (SPX holds ~97% of the $gamma) |
| NDX DP / TL | 29600 / 30248 | 30245 / 30389 | 30069 / 30354 | 73–75% toward QQQ (QQQ holds 50–62%) |
| RUT DP / TL | 2953 / 2930 | 2953 / 2870 | 2953 / 2892 | 53–63% toward IWM (IWM holds 34–55%) |

For MES the difference is negligible. For NQ it's large (the combined DP is 469 points from the NDX-only DP). **There's no evidence yet that the combined pivot predicts better,** so the pack stays native and every run logs all three:

- `data/pivot_log.csv`: one row per complex per run. It holds spots, ratio, basis, and TL/DP as `index_only`, `etf_only` (on the index scale) and `combined`.
- `output/analysis_<IDX>_<ETF>_<session>.json`: the full combined analysis (the ETF profile mapped onto the index scale, binned to 5 points).

**Planned test (after 4–8 weeks of logs):** for each pivot version, does being above versus below it separate the session's realized range or volatility (positive gamma should mean calmer, more mean-reverting sessions), and how often does price react at it? Use NT8 bar data. If the combined version wins on NQ/RTY, the pack's index DP/TL can switch to it for those complexes.

---

## Project structure

```
gex_analysis/
├── config.py                    # GexConfig methodology knobs + COMPLEXES (index, ETF, futures)
├── main.py                      # daily orchestrator: fetch, archive, compute, JSON + pack
├── compare.py                   # vendor pack vs ours: major levels first, then the full grid
├── timeline.py                  # per-session HTML page: how the levels moved, from data/packs/
├── research/
│   └── dp_variants.py           # read-only: which contract set reproduces the vendor's DP
├── data_sources/
│   ├── base.py                  # Chain dataclass, DataSource ABC
│   └── cboe.py                  # CBOE delayed quotes: OCC parsing, settlement times
├── compute/
│   ├── greeks.py                # BS gamma, vectorized (charm, vanna planned)
│   ├── per_strike.py            # ContractSet: DTE-window filter, dealer $GEX per contract and per strike
│   ├── peaks.py                 # greedy peak ranking + half-widths
│   ├── walls.py                 # wall confidence
│   ├── zero_gamma.py            # shift-spot zero gamma (DP, TL)
│   ├── basis.py                 # front quarterly expiry, implied forward, futures basis
│   ├── expected_move.py         # ATM straddle 1-day move
│   ├── regime.py                # GammaRegime classification
│   └── combine.py               # ETF GEX mapped onto the index scale (combined research views)
└── output/
    ├── analysis.py              # GexAnalysis dataclasses + JSON
    └── pack.py                  # gex_pack.txt writer + mirror reader
docs/GEX-PACK-SPEC.md            # NT8 pack contract (wins on format)
docs/NT8-HISTORY-RENDERING.md    # indicator-side spec for drawing [HISTORY] / [PREMARKET]
validation/reference_packs/      # vendor packs for comparison
tests/                           # pytest: math, parsing, basis, pack invariants, spec §4 example
data/raw/, output/               # generated; not in git (back up data/raw — it's our history)
```

Planned additions: `compute/time_of_day.py` (charm intensity), `compute/gradient.py`, `state/intraday.py` and `state/persistence.py` (wall tests, regime transitions).

### `compute/zero_gamma.py` — definition

**Definition.** Zero gamma is the spot value `S*` where **total dealer GEX, recomputed with every contract's gamma re-evaluated at that spot** (IV held per contract), equals zero. We evaluate a grid across `spot × (1 ± zg_search_range_pct)` and root-find each sign change with `scipy.optimize.brentq`; the crossing nearest spot wins. The same function gives both pivots from different expiry sets: DP from `structural_window_dte`, TL from `transition_window_dte`. The cumulative-profile-crossing definition was tested and rejected.

**Hysteresis (planned).** A regime transition (`Transitioning`) is declared only when spot crosses DP by more than `zg_hysteresis_pts` (default 5.0). The crossing flag and its run-count are persisted in intraday state so a single near-DP oscillation does not flip regime repeatedly.

### `compute/time_of_day.py` — `charm_intensity` formula (planned)

```
charm_intensity = clip(
    abs(total_charm_at_spot)
      × (minutes_open_to_close / max(minutes_left_to_close, 1))
      / charm_intensity_normalizer,
    0.0, 1.0
)
```

Where `minutes_open_to_close` is the full session length (typically 390 for US equity cash) and `minutes_left_to_close` is the remaining minutes in the regular session. `charm_intensity_normalizer` is calibrated once during Phase 3 against an end-of-day reference value at 15:50 ET on a representative day and pinned in `GexConfig`. The intensity is near zero at the open and rises through the day as remaining time shrinks.

### `state/intraday.py` — wall test semantics (planned)

A "test" of a wall increments `walls_tested_today[strike]` when spot enters within `wall_test_proximity_strikes` of the wall AND subsequently exits that band by at least `wall_test_retreat_strikes`. Without the retreat requirement, sideways drift inside the band would inflate counts. Tracking is per-strike, per-session-date. The "currently inside the band" flag is part of intraday state and persists across runs.

### `state/persistence.py` — store choice (planned)

State is persisted as one JSON file per trading-session date under `state/YYYY-MM-DD.json`. SQLite was considered and **deferred** — a single-writer daily process does not justify the extra moving part.

### Methodology knobs

Implemented in `gex_analysis/config.py`:

```python
@dataclass
class GexConfig:
    dealer_long_calls: bool = True            # standard convention: dealers long calls (+GEX)...
    dealer_short_puts: bool = True            # ...and short puts (-GEX)

    structural_window_dte = (0, 365)          # walls, GD, C, DP
    transition_window_dte = (0, 7)            # TL
    min_time_to_expiry_minutes = 15.0         # floor so 0DTE gamma doesn't explode near the close

    risk_free_rate = 0.04
    dividend_yields = {"SPX": 0, "NDX": 0, "RUT": 0, "SPY": 0.012, "QQQ": 0.006, "IWM": 0.012}

    support_rank_source = "net"               # GD1-4: "net" (box-resistant) | "put" (vendor-style)
    resistance_rank_source = "net"            # C1-4: "net" | "call"
    peak_relative_threshold = 0.20            # peaks must be >= 20% of the strongest in the ranking
    peak_min_separation_pct = 0.0025          # of spot
    wall_dominance_threshold = 1.5            # wall confidence reaches 1.0 at 1.5x the runner-up

    zg_search_range_pct = 0.10
    zg_grid_points = 161

    regime_battleground_normalized = 0.5      # |dist to DP| / expected move within this = Battleground
    regime_high_confidence_normalized = 2.0   # beyond this = full confidence

    combined_strike_step = 5.0                # binning for the combined index+ETF research views

    intraday_start, intraday_end = 09:45, 15:45   # --intraday schedule (ET)
    intraday_every_minutes = 30
    intraday_level_hysteresis = 0.20          # walls/ranks hold unless beaten by more than this on intraday updates

    futures_roll_days = 8                     # roll to the next quarterly expiry this many days early
    basis_strike_range_pct = 0.03             # strikes used for the implied forward
```

Planned knobs: `expiry_weighting`, `charm_weight_starts_at_minutes_to_close = 180`, `near_close_warning_threshold_minutes = 30`, `charm_intensity_normalizer`, `wall_test_proximity_strikes = 3.0`, `wall_test_retreat_strikes = 2.0`, `zg_hysteresis_pts = 5.0`, `transition_lookback_runs = 5`.

---

## Phased build plan

Each phase produces a verifiable artifact and can ship independently. Don't move to N+1 until N is observable in production.

### ✅ Phase 0: Data source
CBOE delayed quotes verified for all six chains (see "Data source").

### ✅ Phase 1–2: Profile, walls, ranks, pivots, regime
Per-strike dealer GEX, net walls, GD/C ranks, DP and TL, basic regime, JSON output. Definitions and evidence are under "Level definitions".

### ✅ Phase 5 (pulled forward): `gex_pack.txt` for all complexes
The spec showed the NT8 indicator already provides the integration, so the pack moved ahead of Phases 3–4. It includes all three complexes, the implied-forward basis, auto-rolled `[PRIOR]`, atomic writes and the vendor comparison tool.

### ▶ Next: side-by-side validation (1–2 weeks)
- **Mon 28 Sep (done):** first like-for-like comparison. Median TL/DP error for SPX/SPY was 0.18% / 0.20%, and walls were exact except SPX CW 7900 vs the vendor's 7800. That's a near tie: our wall confidence was 0.06. It also confirmed the overnight OI refresh and exposed the stale-spot bug, now fixed.
- Launch `--intraday` around 08:45 daily (WSL on the NT8 machine; keep the terminal open), writing to `gex_pack_service.txt` on a second MES chart.
- Keep pasting the vendor pack as usual. Run `compare` daily and save the output; the major-levels section (TL/DP first) is what matters.
- Then decide on per-symbol windows (NDX, IWM) and when to switch the main charts over.
- Keep `data/pivot_log.csv` accumulating for the combined-pivot test (4–8 weeks).

### Phase 3: Charm and vanna
- Add charm and vanna in `compute/greeks.py`; charm-intensity time weighting in `compute/time_of_day.py`; calibrate `charm_intensity_normalizer`
- Add `near_close_warning` logic
- **Verify:** charm intensity near zero at 9:30 AM and approaching 1.0 at 15:50

### Phase 4: Intraday state — the differentiator
- `state/intraday.py` for wall test counting (proximity + retreat) and regime transition detection with DP hysteresis
- `state/persistence.py` (JSON-per-session-date) to carry state across runs
- Needs intraday runs (the CBOE feed refreshes every 15 minutes)
- **Verify:** simulate a day with multiple wall tests, confirm counts increment only on enter-then-retreat cycles

### Phase 6: NT8 JSON ingestion
An NT8 reader for the JSON fields the pack can't carry, feeding `ConfluenceAlertSystem` and `RegimeClassifierV1`. Only once Phase 3–4 data has shown value.

### Phase 7+: Empirical methodology refinement
- A/B sign convention, expiry windows and weighting, rank definitions
- Backtest: setup quality scores using GEX inputs, PnL across config variants

---

## Validation methodology

### Layer 1: Math and contract correctness (unit tests, `pytest`)
- Black-Scholes gamma matches the closed form; zero gamma matches an analytic call/put pair; peaks, half-widths, wall confidence, regime
- OCC parsing, AM/PM settlement, DST-correct time to expiry
- Implied forward recovers a parity-consistent forward; quarterly expiry roll
- Pack: 12 slots, Invariants A and B, round trip, `[PRIOR]` roll, atomic write, date parsing, **spec §4 worked example**, and a parse of every vendor reference pack (including the bad QQQ ref)
- Net walls and box-resistant GD on a synthetic two-sided strike; compare report; open-interest freshness; pivot log
- Intraday: slot schedule, change report, a no-archive run end to end on a synthetic data source, and the loop's hourly archiving, error survival, missed-slot skipping and fresh-then-held runs under a fake clock
- Hysteresis (`sticky_peaks`, held walls in `build_analysis`), `[PREMARKET]` round trip and its set/keep/reset rules, drift report
- DP research variants: the 0–365 variant reproduces the production DP from the same archive; masks; ranking report
- `[HISTORY]` round trip and its append/reset rules; timeline loading (spot join, run order), page content (panels, wall-jump labels, off-chart walls, change log) and the `[HISTORY]` backfill

### Layer 2: Vendor comparison (daily, `compare`)
- **Major levels first:** TL, DP, CW, PW per cash symbol, with point and % differences, plus the median |TL| and |DP| error overall and for SPX/SPY.
- Healthy: TL/DP for SPX/SPY within ~0.3% of the level, other symbols within ~0.5%, walls mostly exact.
- Minor grid after that: the full 12 slots, GD/C overlap and each complex's basis. GD won't track the vendor (box-resistant by design), and ETF C overlap isn't meaningful (vendor ETF C are converted index levels).
- Persistent large misses on one symbol mean its definition or window needs work. Small differences are expected and not a failure.

### Layer 3: Empirical edge (the real validation)
- Backtest historical setups using vendor levels vs ours as ConfluenceAlertSystem inputs
- Compare setup quality scores, PnL, expectancy
- **This is the only validation that ultimately matters.**

CBOE has no history, so **every run archives the raw snapshots** to `data/raw/<SYM>_<fetch time>.json.gz` (about 4.5 MB per run for all six). Running daily builds our own history for free. Keep that folder backed up; it isn't in git.

---

## What to drop, defer, or rule out

**Drop:**
- Implied moves in the pack (v2 has no slots for them). `expected_move_1d` stays in JSON for normalization.
- The legacy 16-token pack format. The indicator still parses it for old packs, but we emit v2 only.

**Defer:**
- Intraday runs until Phase 4 needs them
- Custom analytics like "gamma momentum" until the base profile is proven
- SQLite-backed state
- Exchange-holiday calendar for session dates, and a warning when TL/DP jump implausibly vs `[PRIOR]` (bad-snapshot guard)
- Freezing walls/ranks at pre-market values all day (stronger than the 20% hysteresis), if flicker still bothers AJ in practice
- NT8 rendering of `[HISTORY]` and `[PREMARKET]` in `GexLevelsPastePack.cs` (AJ's side; spec in `docs/NT8-HISTORY-RENDERING.md`)
- Switching pack pivots to combined index+ETF: only if the pivot-log test supports it

**Rule out:**
- Tuning knobs to chase the vendor's exact numbers
- Building the computation in NinjaScript. Python is the right home; NT8 consumes files.

---

## Open questions for AJ

1. **Where does the service run?** It has to write where NT8 reads (`Documents\NinjaTrader 8\`), so most likely the NT8 Windows machine via Task Scheduler, pre-market.
2. **Chain expiry universe.** Does 0DTE-driven behaviour feel dominant, or do longer-dated levels feel structural? This bears on TL/DP windows, alongside the daily comparisons.
3. **Backtest data.** Any historical option chain data (or budget for it)? Otherwise Layer 3 runs forward on our own archive.
4. **Existing TradingAgents tooling.** Any options adapters in the TauricResearch repo worth reusing?

Answered:
- Data source: CBOE delayed JSON.
- Latency: daily, pre-market.
- Output destination: `gex_pack.txt` for `GexLevelsPastePack.cs`.
- Pack dating: the date line is the session traded; the 25 Sep and 24 Sep packs were both used on the 25th.
- Level priority: TL and DP are major; GD/C 1–4 are minor.
- C levels: resolved from the data (see "Vendor C levels").

---

## Reference companion files

- `docs/GEX-PACK-SPEC.md` — the pack contract, extracted from the live indicator.
- `GexLevelsPastePack.cs` (NinjaTrader project) — the consumer: `ReadPackFile`, `ParseV2Blocks`, `ParsePackV2`, `BuildEtfOverlay`, `BuildPriorDayLevels`.
- `ConfluenceAlertSystem.cs` (NinjaTrader project) — the three-gate structure (Context → Location → Trigger) GEX data flows into.
- `RegimeClassifierV1.cs` (NinjaTrader project) — the price-action regime classifier we stack with gamma regime for the 2x2.
- `LevelTracker.cs` (NinjaTrader project) — dynamic intraday levels; GEX levels complement these.
- `validation/reference_packs/2026-09-25_vendor.txt` — AJ's live pack with the vendor's 25 Sep (`[PACK]`) and 24 Sep (`[PRIOR]`) levels, kept verbatim, including the `QQQ 7.45.4` ref typo.
- `validation/reference_packs/2026-09-2{1,2,3}_vendor.txt` — the vendor's 21–23 Sep packs (`[PACK]` only, blocks verbatim).
- OutsideRedge "GEX Levels" docs (https://outsideredge.com/docs/gamma-exposure-levels) and the TradingView "OutsiderEdge GEX Levels Paste Pack" script page — the display product the pack format came from. Neither gives any methodology; historical context only.

---

## Dependencies

See `pyproject.toml`: `numpy`, `scipy` (brentq), `requests` (CBOE feed); `pytest` for dev. Install with `pip install -e ".[dev]"`. Closed-form gamma in numpy replaces `py-vollib-vectorized`; revisit when charm and vanna land.

---

## What success looks like

**Side-by-side validation:** our pack renders cleanly on NT8 every day, with walls and pivots close to the vendor's, and AJ is comfortable switching the main charts over.

**End of Phase 4:** the daily JSON includes charm intensity and intraday wall test counts.

**End of Phase 6:** ConfluenceAlertSystem consumes the richer data with a noticeable improvement in setup quality on at least one of: false-positive rate, win rate at quality-tier filters, or expectancy on bounce setups.

**End of Phase 7:** GEX inputs are demonstrably contributing to the trading edge — measured through PnL attribution, not vendor-parity metrics.

If the richer data doesn't show edge improvement, the project's value question gets revisited. We don't keep building this if it's not making money.

---

## Things this project is explicitly NOT

- Not a vendor replacement focused on cost savings
- Not a research project for its own sake
- Not a generalist GEX visualization tool
- Not a replacement for the existing trading system — it's a richer input to it
- Not something to ship without a clear edge-improvement hypothesis at each phase
