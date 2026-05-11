# GEX Analysis Service — Built for the Stack, Not Just to Replace GEXBot

**Status:** Greenfield — design phase
**Goal:** A self-contained Python service that computes a *richer* gamma analysis than any public vendor exposes, designed specifically to feed into AJ's existing futures scalping infrastructure (ConfluenceAlertSystem, RegimeClassifierV1, LevelTracker) on MES.

**This is NOT a GEXBot reimplementation.** GEXBot's output format was a UI choice for a general-audience tool. We're building for a specific trading edge with specific integration points, and we have the freedom to design the data contract from first principles.

---

## Why this exists (the actual reasons)

The four reasons to build, ordered by priority for AJ's edge:

1. **Surface gamma information GEXBot can't.** Charm exposure, vanna with sign, gamma profile shape, gradient at spot, wall test degradation — these aren't on GEXBot's product roadmap and they're directly actionable for intraday scalping.
2. **Integrate natively with the existing stack.** ConfluenceAlertSystem wants a continuous score, not a discretized "Moderate" tag. RegimeClassifierV1 already does price-action regime classification — combining gamma regime with that gives a 2x2 alignment signal that no off-the-shelf tool produces.
3. **Tune for futures and the trading windows that matter.** GEXBot computes for SPX cash with one daily snapshot. AJ trades MES at 9:30–11:00 AM and 15:00–16:00 ET. These are very different gamma environments and deserve different methodology.
4. **Own the methodology.** Eventually intraday updates, custom analytics, and (if research warrants) replacing the daily paste pack workflow entirely with a richer NT8 ingestion path.

If a phase doesn't move toward one of these, it's the wrong phase.

---

## What we're keeping vs. replacing from the v1 (GEXBot-compatible) approach

| Aspect | v1 (already shipped) | v2 (this project) |
|---|---|---|
| Level count | Fixed 13 (Call Wall, Put Wall, ZG, VT, L1-L4, C1-C4) | Variable, all peaks above relative threshold |
| Magnitude | Discrete (Strongest / Moderate / Weakest) | Continuous (0.0–1.0 normalized score) |
| Greeks | Gamma only | Gamma + Charm + Vanna |
| Profile data | Peak prices only | Full profile + half-widths + gradient at spot |
| Time-of-day | Single daily snapshot | Time-aware (charm intensity, expiry weighting curve) |
| Wall tracking | None | Intraday test count per major wall |
| Regime | Boolean (positive/negative gamma) | Continuous regime confidence + transition detection |
| Implied moves | In the pack | **Removed as a first-class field** — see "Normalization basis" below; still emitted in paste-pack tokens 14–15 for v1 compat |
| Output format | 16-token paste pack | Structured JSON (richer) + paste pack (v1 compat) |

**The v1 paste pack output is preserved as a compatibility layer** so the existing `GexLevelsPastePack.cs` NT8 indicator keeps working unchanged. v2 consumers (a new richer NT8 indicator, ConfluenceAlertSystem, RegimeClassifierV1) read the structured JSON. v1 deprecates whenever AJ chooses to retire it.

---

## The richer data model

The output object the service emits per run:

```python
@dataclass
class GexAnalysis:
    # Metadata
    timestamp: datetime              # when this analysis was computed (America/New_York)
    underlying: str                  # "SPX" — drives which futures map to it
    spot: float                      # current cash spot price
    expected_move_1d: float          # ATM-straddle-derived, used for normalizing distances

    # Continuous profiles (the source of truth — all other fields derive from these)
    gamma_profile: dict[float, float]    # strike -> dealer GEX (signed, normalized to [-1, 1] by max abs)
    charm_profile: dict[float, float]    # strike -> dealer charm exposure
    vanna_profile: dict[float, float]    # strike -> dealer vanna exposure

    # Discrete derived levels (variable count, ranked by magnitude)
    call_peaks: list[GammaPeak]      # peaks above relative threshold
    put_peaks: list[GammaPeak]

    # Major walls (single highest-magnitude peak each side)
    call_wall: WallInfo              # composition over GammaPeak; see below
    put_wall: WallInfo

    # Regime
    zero_gamma: float                # spot price where total GEX = 0 (see compute/zero_gamma.py)
    distance_to_zg_pts: float        # signed: positive = spot above ZG
    distance_to_zg_normalized: float # signed: distance as fraction of expected_move_1d
    gamma_gradient_at_spot: float    # d_GEX/d_K evaluated at spot (per-point hedging pressure)
    regime: GammaRegime              # Positive / Negative / Transitioning / Battleground
    regime_confidence: float         # 0.0..1.0 — how decisively in regime

    # Time-of-day context
    minutes_to_close: int
    charm_intensity: float           # 0.0..1.0 — see compute/time_of_day.py for formula
    near_close_warning: bool         # true in last 30 min when charm is meaningful

    # Cross-cut analyses
    oi_gex_profile: dict[float, float]      # OI-based gamma (structural)
    volume_gex_profile: dict[float, float]  # Today's volume-based gamma (what's moving now)
    new_positioning: list[float]            # strikes where volume GEX deviates from OI GEX

    # State (carried across runs)
    walls_tested_today: dict[float, int]    # strike -> test count for today's session
```

```python
@dataclass
class GammaPeak:
    strike: float
    magnitude_signed: float          # raw GEX value
    magnitude_normalized: float      # 0.0..1.0 relative to max peak today
    half_width_strikes: float        # strikes over which GEX stays above 50% of peak
    distance_to_spot_pts: float      # signed
    distance_to_spot_normalized: float  # signed, as fraction of expected_move_1d
    role: PeakRole                   # Support / Resistance / Broken (function of price location)

@dataclass
class WallInfo:
    peak: GammaPeak                  # the underlying peak — all geometry comes from here
    test_count_today: int            # 0 = untested, 1 = first test, 2+ = degraded
    distance_dollars: float          # dealer hedging $ for 1-pt spot move; see "Wall dollar impact"
    confidence: float                # dominance vs. next-highest peak (separation factor)

class GammaRegime(Enum):
    Positive       # spot well above ZG, mean-reverting environment
    Negative       # spot well below ZG, trend-amplifying environment
    Battleground   # spot within tolerance of ZG, no clear regime
    Transitioning  # spot just crossed ZG with conviction (within last N runs) — TRADEABLE event
```

### Normalization basis

`expected_move_1d` is the ATM-straddle-implied 1-day move for the underlying, computed from the nearest-expiry ATM call+put mid prices summed and divided by spot (the standard straddle approximation). It is computed **inside this service** rather than imported from a separate vol module, because every normalized distance field (`distance_to_zg_normalized`, `distance_to_spot_normalized`, the Battleground/high-confidence regime thresholds) depends on it.

The "implied moves" we explicitly drop are the *display* fields that v1 surfaces (1D + 1W IV-derived moves emitted as paste-pack tokens 14–15). Those continue to be produced in `output/paste_pack.py` from the same ATM-straddle calculation — there is exactly one source of truth for the number.

### Wall dollar impact

`WallInfo.distance_dollars` is the dealer hedging dollars implied by a 1-point move of spot through the wall:

```
distance_dollars = gamma_at_wall × contract_multiplier × 100 × spot
```

Contract multipliers: SPX cash = 100, MES = 5, NQ = 20, MNQ = 2. This field is provided for downstream sizing intuition; nothing in this service consumes it internally.

---

## How this plugs into AJ's existing stack

**Cadence (through Phase 4).** The service runs as a discrete job — once daily by default, optionally on a manual or cron-driven cadence. A "run" is the unit of intraday state: `transition_lookback_runs` counts service invocations, not market bars. Phase 6+ may introduce bar-driven updates; this section describes pre-Phase-6 behavior.

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

LevelTracker handles dynamic intraday levels (VWAP bands, ORB, prior session H/L/C, London session). GEX Analysis Service handles options-derived structural levels. Both feed into ConfluenceAlertSystem; neither replaces the other. The high-confidence trades will be where dynamic LevelTracker levels coincide with GEX peaks — explicit cross-confirmation.

---

## Design principles (the choices we're making different from GEXBot)

1. **Continuous over discrete wherever possible.** Magnitude is a number; expose it as a number. Distance is a number; normalize it but keep it numeric. Let downstream consumers set their own thresholds.

2. **Variable count over fixed count.** Some days have 3 meaningful peaks per side, some have 7. The peak count itself is information about market structure (concentrated vs. distributed positioning).

3. **Profile data over peak prices alone.** Width matters, gradient matters. A "wide diffuse call wall from 7320–7360" trades very differently than a "single-strike spike at 7330."

4. **Time-of-day is first-class.** Compute charm and vanna; weight by remaining trading time; recognize that the same Call Wall is more constraining at 15:50 than at 10:00.

5. **State persists across runs within a day.** Wall test counts, regime transitions, charm intensity — these are time series, not single snapshots. The service should remember what happened earlier in the day.

6. **Multiple greeks, not just gamma.** Charm and vanna are computationally cheap to add once you have the chain. They're independently actionable.

7. **OI vs. Volume separately tracked.** They answer different questions; conflating them loses information.

8. **Validation is empirical edge, not vendor parity.** We're not trying to match GEXBot. We're trying to produce data that, when fed into the trading system, improves PnL. Validation is backtest + live use, not "does our ZG match theirs."

---

## Output contracts

**Timezones.** All wall-clock fields (`timestamp`, `minutes_to_close`, time-of-day window boundaries, charm-intensity calibration anchor) are `America/New_York`. The `YYYY-MM-DD` portion of filenames is the **US equity trading-session date** for the analysis, not UTC. Sessions that span midnight UTC still belong to a single ET trading-session date.

### Primary output: `analysis_YYYY-MM-DD.json`

Full `GexAnalysis` object serialized as JSON. This is the source of truth for v2 consumers.

```json
{
  "timestamp": "2026-05-04T16:30:00-04:00",
  "underlying": "SPX",
  "spot": 7225.50,
  "expected_move_1d": 46.24,
  "regime": "Positive",
  "regime_confidence": 0.78,
  "zero_gamma": 7108.50,
  "distance_to_zg_pts": 117.0,
  "distance_to_zg_normalized": 2.53,
  "gamma_gradient_at_spot": -2340.5,
  "call_wall": {
    "peak": {
      "strike": 7330.0,
      "magnitude_normalized": 1.0,
      "half_width_strikes": 8.0,
      "role": "Resistance"
    },
    "test_count_today": 0,
    "confidence": 0.92,
    "distance_dollars": 169102875.0
  },
  "call_peaks": [...],
  "put_peaks": [...],
  "charm_intensity": 0.15,
  "near_close_warning": false,
  ...
}
```

### Compatibility output: `pack_YYYY-MM-DD.txt`

The 16-token paste pack format, generated from the same underlying analysis. Lets the existing `GexLevelsPastePack.cs` indicator keep working without changes during the v2 transition.

The mapping from `GexAnalysis` → paste pack tokens is **finalized in Phase 5 against the actual `GexLevelsPastePack.cs:ParsePack` logic.** The provisional mapping below should be **verified against the C# parser before shipping**, because v1's L1–L4 / C1–C4 slot semantics (top-by-magnitude vs. nearest-by-distance vs. ordered-from-spot) are not yet confirmed from this repo:

- Tokens 3, 4: `call_wall.peak.strike`, `put_wall.peak.strike`
- Token 5: Vol Trigger — derived from vanna profile (the spot where vanna sign flips)
- Token 6–9 (L1-L4): top 4 put-side peaks by magnitude (or fewer, padded with zeros if not enough qualifying peaks)
- Token 10–13 (C1-C4): top 4 call-side peaks by magnitude (similarly padded)
- Token 14, 15: ATM straddle-derived 1D and 1W expected moves (same source as `expected_move_1d`)
- Token 16: `zero_gamma`

Note: when v2 emits more than 4 peaks per side, the paste pack only carries the top 4. **The richer JSON has all of them.**

### Future output: NT8 file ingestion

Phase 5+: replace the daily paste-pack workflow with NT8 reading the JSON directly via a new indicator or AddOn. This unlocks intraday updates and the full data model. Don't build this until the JSON-consuming side has proven its value.

---

## Project structure

```
gex_analysis/
├── data_sources/
│   ├── __init__.py
│   ├── base.py                  # DataSource ABC: fetch_chain(), fetch_spot(), fetch_rate()
│   └── <provider>.py            # implementation depending on AJ's available APIs
├── compute/
│   ├── greeks.py                # BS gamma, charm, vanna — vectorized
│   ├── per_strike.py            # aggregate per-contract greeks to per-strike profile
│   ├── peaks.py                 # peak detection above relative threshold
│   ├── walls.py                 # major wall identification + half-width + confidence
│   ├── zero_gamma.py            # numerical root-find with hysteresis-aware regime gating
│   ├── vanna_trigger.py         # vol trigger from vanna profile
│   ├── gradient.py              # d_GEX/d_K at spot (numerical or analytical)
│   ├── regime.py                # GammaRegime classification + transition detection
│   ├── expected_move.py         # ATM straddle 1D/1W moves (one source for JSON + paste pack)
│   └── time_of_day.py           # charm intensity, expiry weighting curve, close warnings
├── state/
│   ├── __init__.py
│   ├── intraday.py              # tracks wall test counts, regime transitions, profile changes
│   └── persistence.py           # JSON-on-disk per session-date
├── output/
│   ├── analysis.py              # GexAnalysis dataclass + JSON serialization
│   ├── paste_pack.py            # v1 compat output
│   └── push_to_nt.py            # file-watcher integration (Phase 5+)
├── validation/
│   ├── unit/                    # closed-form math tests
│   ├── empirical/               # backtest setup quality vs. forward returns
│   ├── reference_packs/         # historical GEXBot packs (for sanity comparison only)
│   └── methodology_compare.py   # A/B sign convention / expiry weight knobs
├── config.py                    # methodology knobs (all empirical decisions are config)
├── main.py                      # daily orchestrator
└── tests/
```

### `compute/zero_gamma.py` — definition

**Definition.** Zero gamma is the spot value `S*` such that **total dealer GEX, recomputed by shifting all greeks to that spot**, equals zero — i.e. we re-evaluate the chain's gamma at hypothetical spots across a search grid spanning `spot × (1 ± zg_search_range_pct)`, build a signed-GEX function of spot, and root-find with `scipy.optimize.brentq`. This is distinct from the cumulative-profile-crossing definition, which uses today's per-strike GEX cumsum at a fixed spot; the two can give materially different answers and we commit to the shift-spot definition.

**Hysteresis.** A regime transition (`Transitioning`) is declared only when spot crosses ZG by more than `zg_hysteresis_pts` (config, default 5.0). The crossing flag and its run-count are persisted in intraday state so a single near-ZG oscillation does not flip regime repeatedly.

### `compute/time_of_day.py` — `charm_intensity` formula

```
charm_intensity = clip(
    abs(total_charm_at_spot)
      × (minutes_open_to_close / max(minutes_left_to_close, 1))
      / charm_intensity_normalizer,
    0.0, 1.0
)
```

Where `minutes_open_to_close` is the full session length (typically 390 for US equity cash) and `minutes_left_to_close` is the remaining minutes in the regular session. `charm_intensity_normalizer` is calibrated once during Phase 3 against an end-of-day reference value at 15:50 ET on a representative day and pinned in `GexConfig`. The intensity is near zero at the open (large denominator) and rises through the day as remaining time shrinks.

### `state/intraday.py` — wall test semantics

A "test" of a wall increments `walls_tested_today[strike]` when spot enters within `wall_test_proximity_strikes` of the wall AND subsequently exits that band by at least `wall_test_retreat_strikes`. Without the retreat requirement, sideways drift inside the band would inflate counts. Tracking is per-strike, per-session-date. The "currently inside the band" flag is part of intraday state and persists across runs.

### `state/persistence.py` — store choice

State is persisted as one JSON file per trading-session date under `state/YYYY-MM-DD.json`. SQLite was considered and **deferred** — a single-writer daily process does not justify the extra moving part. Revisit only if Phase 6+ introduces concurrent intraday writers or cross-session queries.

### Methodology knobs in `config.py`

```python
@dataclass
class GexConfig:
    # Sign convention (verify empirically)
    dealer_short_calls: bool = True
    dealer_long_puts: bool = True

    # Expiry handling
    expiry_window_dte: tuple[int, int] = (0, 30)
    expiry_weighting: Literal['none', 'linear_decay', 'sqrt_decay'] = 'sqrt_decay'

    # Time-of-day adjustment
    charm_weight_starts_at_minutes_to_close: int = 180
    near_close_warning_threshold_minutes: int = 30
    charm_intensity_normalizer: float = 1.0  # calibrated in Phase 3

    # Peak detection
    peak_relative_threshold: float = 0.20  # peaks must be >= 20% of max magnitude
    peak_min_separation_strikes: float = 5.0  # don't double-count adjacent strikes

    # Wall confidence
    wall_dominance_threshold: float = 1.5  # major wall must be >= 1.5x next runner-up

    # Wall test counting
    wall_test_proximity_strikes: float = 3.0
    wall_test_retreat_strikes: float = 2.0

    # Zero gamma search
    zg_search_range_pct: float = 0.10
    zg_hysteresis_pts: float = 5.0
    transition_lookback_runs: int = 5  # how recent a ZG cross is "Transitioning" (in service runs)

    # Regime
    regime_battleground_normalized: float = 0.5  # |dist/exp_move| within this = Battleground
    regime_high_confidence_normalized: float = 2.0  # beyond this = full confidence
```

---

## Phased build plan

The phasing is structured so each phase produces a verifiable artifact and can ship independently. Don't move to N+1 until N is observable in production.

### Phase 0: Scope confirmation
- Document available APIs (Polygon? Broker? CBOE? ThetaData?)
- Pull a recent SPX chain end-to-end with one provider
- Verify: strike, expiry, type, OI, volume, IV per contract
- Eyeball the data — confirm IV is per-strike (not just ATM), confirm OI is non-zero across strikes
- **Stop here, write up what you've got, before any compute logic.**

### Phase 1: Per-strike gamma profile (the foundation)
- Implement BS gamma in `compute/greeks.py` (vectorized via py_vollib_vectorized)
- Compute per-strike GEX summed across the configured expiry window
- Implement `compute/expected_move.py` (needed for normalized distances from Phase 2)
- Output: a JSON file with `gamma_profile` only (just the dict[strike, GEX])
- **Verify:** plot the profile, eyeball it for shape, confirm peaks are at strikes with high OI
- **Sanity (not calibration).** Compare computed Zero Gamma to GEXBot's published ZG for the same date. Order-of-magnitude agreement (within ~1% of spot) suggests the math is wired correctly. **Persistent disagreement is not a failure** — sign convention, expiry window, and weighting differ. Do not tune knobs to match GEXBot; that's a Phase 7 empirical question.

### Phase 2: Variable peaks + walls + regime
- Add peak detection (`compute/peaks.py`) with relative magnitude threshold
- Add wall identification with half-width and confidence (`compute/walls.py`)
- Add zero gamma root-find (`compute/zero_gamma.py`) using the shift-spot definition
- Add basic regime classification (`compute/regime.py` — without transition detection yet)
- Output: complete `GexAnalysis` JSON minus charm/vanna/state
- **Verify:** levels look "right" — eyeball against GEXBot for sanity, but don't constrain to match exactly

### Phase 3: Charm and vanna
- Add charm and vanna in `compute/greeks.py`
- Add charm-intensity time weighting in `compute/time_of_day.py`; calibrate `charm_intensity_normalizer`
- Add vol trigger from vanna profile (`compute/vanna_trigger.py`)
- Add `near_close_warning` logic
- Output: full `GexAnalysis` JSON for a single point in time
- **Verify:** charm intensity should be near-zero at 9:30 AM and approach 1.0 at 15:50 — eyeball the curve

### Phase 4: Intraday state — the differentiator
- Add `state/intraday.py` for wall test counting (with proximity + retreat semantics) and regime transition detection
- Add `state/persistence.py` (JSON-per-session-date) to carry state across multiple daily runs
- Wire into output: `walls_tested_today`, `regime` includes `Transitioning` state, ZG hysteresis active
- **Verify:** simulate a day with multiple wall tests, confirm count increments correctly only on enter-then-retreat cycles

### Phase 5: Multi-symbol output + paste-pack compat
- Extend to NDX and RUT chains
- Implement `output/paste_pack.py` to emit v1-compatible paste pack alongside JSON
- **Confirm token mapping against `GexLevelsPastePack.cs:ParsePack`** before shipping; provisional mapping in "Output contracts" may need to change
- Wire daily orchestrator (`main.py`) to produce both files
- **Verify:** paste pack round-trips through `GexLevelsPastePack.cs:ParsePack` cleanly

### Phase 6: NT8 ingestion
- New NT8 indicator (`GexAnalysisV2`) that consumes the JSON directly
- Exposes the richer data via public API to `ConfluenceAlertSystem`
- Run alongside `GexLevelsPastePack` (v1) until proven, then deprecate v1

### Phase 7+: Empirical methodology refinement
- A/B sign convention, expiry weighting, peak threshold
- Backtest: compute setup quality scores using GEX inputs, compare PnL across config variants
- This is where the long-term edge tuning happens

---

## Validation methodology (different from v1)

We're **not** trying to match GEXBot. So validation has three layers:

### Layer 1: Math correctness (unit tests)
- Black-Scholes gamma at ATM matches closed-form value
- Charm at zero time-to-expiry approaches infinity correctly
- Zero gamma converges for synthetic profiles with known answers
- Per-strike aggregation across expiries sums correctly
- Wall test counter increments exactly once per enter→retreat cycle

### Layer 2: Sanity (eyeball + GEXBot comparison, NOT calibration)
- Computed Zero Gamma in the order-of-magnitude vicinity of GEXBot's published ZG (within ~1% of spot most days)
- Major Call Wall and Put Wall match GEXBot's strikes for ~80% of days
- Profile shape "looks right" — peaks at high-OI strikes
- Charm intensity rises monotonically through the trading day

If Layer 2 is wildly off (e.g., ZG is on the wrong side of spot, walls are at structurally implausible strikes), methodology is wrong; iterate config knobs. **If Layer 2 is in the rough vicinity but not matching exactly, that is expected and not a failure** — methodology differences from GEXBot are intentional.

### Layer 3: Empirical edge (the real validation)
- Backtest historical setups using both v1 (GEXBot levels) and v2 (our richer levels) as ConfluenceAlertSystem inputs
- Compare: does v2 produce better setup quality scores? Better PnL? Better expectancy?
- **This is the only validation that ultimately matters.** Phases 1–6 produce data; Phase 7+ proves the data is worth more than v1.

Layer 3 needs historical chain data which may not be available cheaply. Possible workaround: run v2 forward in production for several months alongside v1, compare live performance.

---

## What to drop, defer, or rule out

**Drop:**
- Implied moves as a **first-class field** of `GexAnalysis`. They're IV-derived display values, not gamma. The v1 paste-pack tokens 14–15 still carry them (because the format requires it), generated by `compute/expected_move.py` — which also produces `expected_move_1d` for normalization. One source of truth, used in two places.

**Defer:**
- NDX/RUT support until SPX/MES is solid (Phase 5)
- Intraday updates (more frequent than once-daily) until daily output is proven valuable
- Custom analytics like "gamma momentum" (rate of change of gamma profile) until base profile is reliable
- SQLite-backed state (current plan: JSON files per session date)

**Rule out:**
- Trying to match GEXBot exactly. We're not. Set the expectation up front so we don't get stuck in calibration loops.
- Building this in NinjaScript. Wrong tool. Python with vectorized greeks is the right home.
- Coupling state persistence to a database. SQLite or even JSON files on disk are fine for the data volumes involved — see decision above.

---

## Open questions for AJ before Phase 0

1. **Data source.** Which of these do you have working access to: Polygon Options Advanced, broker API (TD/Schwab/IBKR), CBOE DataShop, ThetaData, Tradier, others? IV-per-strike is the requirement; everything else is negotiable.
2. **Compute environment.** Local machine running daily? Cloud VM? Schedule via cron, Windows Task Scheduler, or run manually?
3. **Output destination.** File on disk that NT8 reads? Push to a directory NT8 watches? HTTP endpoint? Daily email?
4. **Existing TradingAgents tooling.** Does the TauricResearch repo have any options data adapters or chain handlers worth reusing?
5. **Chain expiry universe.** When you've used GEX in trading, has it felt like 0DTE-driven dynamics matter most, or do longer-dated levels feel structural to you? This shapes the default `expiry_window_dte` config.
6. **Backtest data.** Do you have historical option chain data (or budget for it) anywhere? This unlocks Layer 3 validation. If not, we'll plan around forward-only validation.
7. **Latency budget.** Is this service permanently a once-daily batch job, or do you anticipate moving to intraday (every N minutes) by Phase 6+? This shapes the data-source decision **today**: a daily CBOE EOD pull is cheap and sufficient for batch, but Polygon WebSocket or broker streaming is needed for intraday. Picking the wrong source now means rewriting `data_sources/` later — far more expensive than picking right up front.

---

## Reference companion files

- `GexLevelsPastePack.cs` (NinjaTrader project) — the v1 indicator. Defines the paste-pack format we maintain compatibility with. **Token slot semantics must be confirmed against this file before Phase 5 ships.**
- `ConfluenceAlertSystem.cs` (NinjaTrader project) — the eventual consumer of v2 data. Look at its three-gate structure (Context → Location → Trigger) to understand how the GEX data should flow.
- `RegimeClassifierV1.cs` (NinjaTrader project) — the regime classifier we'll stack with gamma regime to produce the 2x2 alignment signal.
- `LevelTracker.cs` (NinjaTrader project) — the existing dynamic level tracker. v2 GEX levels complement these.

---

## Reference paste packs (for v1 compat validation only)

Save to `validation/reference_packs/`:

`2026-04-30.txt`:
```
30 APRIL 2026 $SPX, SPX, 7200, 6800, 7095, 7000, 7150, 6000, 7100, 7200, 7300, 7400, 7222, 0.0064, 0.0151, 7041, $SPY, SPY, 720, 700, 710, 700, 710, 705, 715, 718.09, 728.05, 723.07, 738.01, 0.0064, 0.0151, 710, $NDX, NDX, 26700, 23500, 26690, 26700, 27000, 27500, 26500, 27731, 27948, 27540, 27323, 0.01, 0, 26313, $QQQ, QQQ, 675, 635, 654, 650, 660, 640, 645, 675.32, 649.68, 660.2, 679.93, 0.01, 0, 655, $RUT, RUT, 2725, 2680, 2700, 2700, 2680, 2725, 2750, 2679, 2718, 2619, 2668, 0.01, 0, 2732, $IWM, IWM, 280, 266, 272, 270, 260, 265, 275, 270.09, 279.95, 276.93, 266.25, 0.01, 0, 280, $ES1!, ES1!, 7230.05, 6830.05, 7125.05, 7030.05, 7180.05, 6030.05, 7130.05, 7230.05, 7330.05, 7430.05, 7252.05, 0.0064, 0.0151, 7071.05, $MES1!, MES1!, 7230.05, 6830.05, 7125.05, 7030.05, 7180.05, 6030.05, 7130.05, 7230.05, 7330.05, 7430.05, 7252.05, 0.0064, 0.0151, 7071.05, $NQ1!, NQ1!, 26857.27, 23657.27, 26847.27, 26857.27, 27157.27, 27657.27, 26657.27, 27888.27, 28105.27, 27697.27, 27480.27, 0.01, 0, 26470.27, $MNQ1!, MNQ1!, 26857.27, 23657.27, 26847.27, 26857.27, 27157.27, 27657.27, 26657.27, 27888.27, 28105.27, 27697.27, 27480.27, 0.01, 0, 26470.27, $RTY1!, RTY1!, 2731.92, 2686.92, 2706.92, 2706.92, 2686.92, 2731.92, 2756.92, 2685.92, 2724.92, 2625.92, 2674.92, 0.01, 0, 2738.92, $M2K1!, M2K1!, 2731.92, 2686.92, 2706.92, 2706.92, 2686.92, 2731.92, 2756.92, 2685.92, 2724.92, 2625.92, 2674.92, 0.01, 0, 2738.92
```

`2026-05-01.txt`:
```
1 MAY 2026 $SPX, SPX, 7200, 6800, 7070, 7000, 7200, 7150, 6000, 7202, 7303, 7252, 7396, 0.0063, 0.0151, 7060, $SPY, SPY, 720, 700, 713, 700, 710, 720, 715, 717.98, 727.95, 737.91, 720.12, 0.0063, 0.0151, 717, $NDX, NDX, 26700, 24500, 26690, 26700, 27000, 27500, 27300, 27754, 27974, 27562, 27507, 0.01, 0, 26570, $QQQ, QQQ, 675, 600, 664, 660, 675, 670, 650, 674.81, 680.1, 670.18, 664.89, 0.01, 0, 661, $RUT, RUT, 2785, 2680, 2700, 2800, 2785, 2750, 2700, 2820, 2680, 2800, 2870, 0.01, 0, 2751, $IWM, IWM, 280, 266, 273, 270, 280, 260, 275, 266.08, 269.89, 260.1, 265, 0.01, 0, 279, $ES1!, ES1!, 7231.05, 6831.05, 7101.05, 7031.05, 7231.05, 7181.05, 6031.05, 7233.05, 7334.05, 7283.05, 7427.05, 0.0063, 0.0151, 7091.05, $MES1!, MES1!, 7231.05, 6831.05, 7101.05, 7031.05, 7231.05, 7181.05, 6031.05, 7233.05, 7334.05, 7283.05, 7427.05, 0.0063, 0.0151, 7091.05, $NQ1!, NQ1!, 26840.13, 24640.13, 26830.13, 26840.13, 27140.13, 27640.13, 27440.13, 27894.13, 28114.13, 27702.13, 27647.13, 0.01, 0, 26710.13, $MNQ1!, MNQ1!, 26840.13, 24640.13, 26830.13, 26840.13, 27140.13, 27640.13, 27440.13, 27894.13, 28114.13, 27702.13, 27647.13, 0.01, 0, 26710.13, $RTY1!, RTY1!, 2793.98, 2688.98, 2708.98, 2808.98, 2793.98, 2758.98, 2708.98, 2828.98, 2688.98, 2808.98, 2878.98, 0.01, 0, 2759.98, $M2K1!, M2K1!, 2793.98, 2688.98, 2708.98, 2808.98, 2793.98, 2758.98, 2708.98, 2828.98, 2688.98, 2808.98, 2878.98, 0.01, 0, 2759.98
```

`2026-05-04.txt`:
```
4 MAY 2026 $SPX, SPX, 7300, 6800, 7195, 7000, 7200, 7150, 6000, 7302, 7396, 7274, 7353, 0.0064, 0.0149, 7080, $SPY, SPY, 725, 700, 720, 700, 710, 720, 725, 717.67, 727.73, 722.7, 737.07, 0.0064, 0.0151, 719, $NDX, NDX, 26700, 24500, 25550, 27000, 26700, 27500, 28000, 27960, 27738, 27987, 28154, 0.01, 0, 25829, $QQQ, QQQ, 680, 645, 669, 675, 680, 670, 660, 674.91, 680.25, 670.24, 668.91, 0.01, 0, 667, $RUT, RUT, 2850, 2680, 2780, 2800, 2700, 2750, 2785, 2869, 2818, 2849, 2678, 0.01, 0, 2785, $IWM, IWM, 280, 265, 272, 270, 260, 280, 275, 279.88, 265.98, 277.93, 284.88, 0.01, 0, 283, $ES1!, ES1!, 7328.52, 6828.52, 7223.52, 7028.52, 7228.52, 7178.52, 6028.52, 7330.52, 7424.52, 7302.52, 7381.52, 0.0064, 0.0149, 7108.52, $MES1!, MES1!, 7328.52, 6828.52, 7223.52, 7028.52, 7228.52, 7178.52, 6028.52, 7330.52, 7424.52, 7302.52, 7381.52, 0.0064, 0.0149, 7108.52, $NQ1!, NQ1!, 26814.64, 24614.64, 25664.64, 27114.64, 26814.64, 27614.64, 28114.64, 28074.64, 27852.64, 28101.64, 28268.64, 0.01, 0, 25943.64, $MNQ1!, MNQ1!, 26814.64, 24614.64, 25664.64, 27114.64, 26814.64, 27614.64, 28114.64, 28074.64, 27852.64, 28101.64, 28268.64, 0.01, 0, 25943.64, $RTY1!, RTY1!, 2855.31, 2685.31, 2785.31, 2805.31, 2705.31, 2755.31, 2790.31, 2874.31, 2823.31, 2854.31, 2683.31, 0.01, 0, 2790.31, $M2K1!, M2K1!, 2855.31, 2685.31, 2785.31, 2805.31, 2705.31, 2755.31, 2790.31, 2874.31, 2823.31, 2854.31, 2683.31, 0.01, 0, 2790.31
```

---

## Recommended dependencies

```toml
# Core math
numpy = "*"
scipy = "*"                    # brentq for ZG root-find, savgol for profile smoothing
pandas = "*"
py-vollib-vectorized = "*"     # vectorized BS greeks + IV back-solving

# Data sources (depends on chosen provider)
# polygon-api-client = "*"
# requests = "*"

# State persistence
# Just stdlib (json) — no extras needed (SQLite deferred)

# Dev
pytest = "*"
pytest-cov = "*"

# Optional orchestration
schedule = "*"
python-dotenv = "*"
```

---

## What success looks like

**End of Phase 4:** A daily JSON file is being produced with the full `GexAnalysis` object, including charm intensity and intraday wall test counts.

**End of Phase 6:** ConfluenceAlertSystem is consuming the JSON in NT8 with a noticeable improvement in setup quality on at least one of: false-positive rate, win rate at quality-tier filters, or expectancy on bounce setups.

**End of Phase 7:** The methodology has been calibrated against forward-traded data and the GEX inputs are demonstrably contributing to the trading edge — measured through actual PnL attribution, not vendor-parity metrics.

If Phase 6 doesn't show edge improvement, the project's value question gets revisited. We don't keep building this if it's not making money.

---

## Things this project is explicitly NOT

- Not a GEXBot replacement focused on cost savings
- Not a research project for its own sake
- Not a generalist GEX visualization tool
- Not a replacement for the existing trading system — it's a richer input to it
- Not something to ship without a clear edge-improvement hypothesis at each phase
