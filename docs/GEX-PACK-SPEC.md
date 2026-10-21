# GEX Pack File — Integration Spec for the Level-Generation Service

**Purpose:** This document is the output contract for the Python service that will generate GEX levels from our own sources. The consumer is the NT8 indicator `GexLevelsPastePack.cs` (current: 1,950 lines, Sep 2026 build), which reads a single text file — `gex_pack.txt` — and renders everything from it. **If the service writes this file correctly, the entire NT8 side (parsing, ETF overlay, prior-day ghosts, confluence tags, hot reload) works with zero indicator changes.** That is the definition of "matching the output."

Verified against the live indicator source; all constants and worked examples below are extracted from it, not from memory.

---

## 1. The file

- **Path:** `Documents\NinjaTrader 8\gex_pack.txt` (the indicator's default when its `Pack File Path` is empty; a custom path can be configured per instance).
- **Encoding:** plain text, UTF-8 fine. Line endings don't matter (lines are trimmed).
- **Hot reload:** on every realtime bar close the indicator compares `File.GetLastWriteTimeUtc` to its cached value; any increase triggers a full teardown/re-parse/rebuild. So the service just overwrites the file; the chart refreshes within one bar (1-min charts → ≤1 minute).
  - **Write atomically:** write to a temp file in the same directory, then rename/replace over `gex_pack.txt`. The indicator wraps reads in try/catch and retries next bar if the file is locked mid-write, so a non-atomic write won't crash anything — but it can cause one bar of "half file → parse warning → fallback" noise. Atomic replace avoids it entirely.
  - Historical processing never reads the file mid-run; only realtime bars poll.

### File grammar

```
# comment lines start with '#' and are ignored; blank lines ignored
[REFS]
SPY 774.09
MES 7762.0
QQQ 716.50
MNQ 29350.00

[PACK]
4 SEP 2026
ES=7810|7510|7730|8010|7710|7010|7810|7812|7835|7913|7789|7654;MES=...;SPY=...

[PRIOR]
3 SEP 2026
ES=...;MES=...
```

- Section headers are `[NAME]` on their own line, case-insensitive. Recognized: `REFS`, `PACK`, `PRIOR`. Unknown sections are ignored.
- **Lines within `[PACK]`/`[PRIOR]` are trimmed and space-joined** before parsing. This is why the date can sit on its own line: after joining, it becomes an inline prefix of the first block, which the pack parser captures. The service may emit the whole pack on one line or split it across lines at any `;` boundary — both join to the same string.
- `[PRIOR]` is optional. When present, it drives the prior-day ghost/persistence display. The service should emit yesterday's pack there (i.e., keep a one-day history and roll it).

### `[REFS]` — the spot table

One entry per line, `SYMBOL price` (space, comma, tab, `|`, or `=` all work as the separator). Purpose: lets each chart derive the exact ETF→futures conversion ratio daily instead of using a stale hardcoded one.

- The indicator resolves, per chart: **ETF ref** = the chart's ETF sibling (SPY for ES/MES, QQQ for NQ/MNQ, IWM for RTY/M2K) and **futures ref** = the chart's own symbol, with micro↔big interchangeability (`MES`↔`ES`, `MNQ`↔`NQ`, `M2K`↔`RTY`, `MYM`↔`YM`) — quote either one.
- **The two prices in a pair must be simultaneous observations** (same moment), because the ratio is computed as `(futures_ref − basis) / etf_ref`. The service should capture both from the same snapshot (e.g., the tickstream free feed covers SPY/QQQ/ES/NQ in one place).
- Emit refs for every complex the pack covers: `SPY` + `ES` (or `MES`), `QQQ` + `NQ` (or `MNQ`), `IWM` + `RTY` if the RUT complex is included.
- Legacy form (a line of two bare numbers = ETF ref, futures ref) still parses but is pair-ambiguous across complexes — **the service must emit the symbol-keyed form only.**
- Missing refs for a pair → the indicator warns in the Output window and falls back to a static ratio parameter (default 10.02). Functional but degraded; don't rely on it.

---

## 2. The pack string (v2 format)

Concatenated symbol blocks, `;`-separated:

```
[optional free-text date prefix] SYMBOL=v1|v2|...|v12;SYMBOL=...;...
```

### Parsing rules the service must satisfy

1. **Block boundary:** each block is `SYMBOL=` followed by exactly **12** `|`-separated numeric values. Fewer or more than 12 → that block is skipped with a warning.
2. **Symbol extraction** walks backward from each `=` over `[A-Za-z0-9]`; anything before that run (the date, whitespace, newlines) is ignored as prefix. Symbols are matched case-insensitively against NT8's `MasterInstrument.Name` — so emit **bare symbols**: `ES`, `MES`, `SPY`, `SPX`, `NQ`, `MNQ`, `QQQ`, `NDX`, `RTY`, `IWM` (no `$` prefix, no `1!` suffix — those are legacy-format artifacts, though the normalizer would strip them).
3. **Numbers:** invariant-culture floats (`.` decimal separator, no thousands separators). Values are tick-rounded by the indicator at render (`RoundToTickSize`), so emit full precision; don't pre-round.
4. **`0.0` means "level not provided"** — that line/label is suppressed. Use `0` for any level slot the service can't compute; never omit a slot (the 12-count is fixed).
5. **Date prefix:** free text before the first symbol, captured for the on-chart annotation. To get the stale-pack warning working, it must parse via C# `DateTime.TryParse` with InvariantCulture — `4 SEP 2026`, `04 SEP 2026`, `2026-09-04` all work. **Emit the date; the stale warning is a safety feature** (annotation turns red when pack date < current session date).
6. A **legacy format** (16 comma-separated tokens, `$`-prefixed symbols, incl. implied-move fields) still exists in the parser for old saved packs, auto-detected by absence of `=`/`|`. **The service must emit v2 only.**

### Field order (index → meaning)

Confirmed empirically against the vendor's TradingView render (SPY, 24 JULY 2026) and stable since:

| Idx | Const | Field name (display) | Semantics |
|---|---|---|---|
| 0 | `V2_CW` | `CW` — Call Wall | Major resistance / ceiling |
| 1 | `V2_PW` | `PW` — Put Wall | Major support / floor |
| 2 | `V2_VT` | `TL` — Transition Level (a.k.a. Vol Trigger) | Vol-regime marker |
| 3 | `V2_L1` | `GD1` (Strongest) | Support-side rank 1 |
| 4 | `V2_L2` | `GD2` (Moderate) | Support-side rank 2 |
| 5 | `V2_L3` | `GD3` (Moderate) | Support-side rank 3 |
| 6 | `V2_L4` | `GD4` (Weakest) | Support-side rank 4 |
| 7 | `V2_C1` | `C1` (Strongest) | Resistance-side rank 1 |
| 8 | `V2_C2` | `C2` (Moderate) | Resistance-side rank 2 |
| 9 | `V2_C3` | `C3` (Moderate) | Resistance-side rank 3 |
| 10 | `V2_C4` | `C4` (Weakest) | Resistance-side rank 4 |
| 11 | `V2_ZG` | `DP` — Dealer Pivot (a.k.a. Zero Gamma) | Gamma-regime pivot; price above = positive gamma |

Critical ordering facts:
- **GD1–4 / C1–4 are ordered by strength rank, not by price.** The vendor's "deep" level floats between slots on different days. The service should likewise emit its top-4 peaks per side by GEX magnitude, rank-ordered. Do not sort by price.
- No implied-move fields in v2 (those were legacy tokens 14–15). If we want them back someday, that's a format extension, not slots to repurpose.

### Which symbol blocks to emit, and the invariants between them

Emit all of: `ES, MES, NQ, MNQ, RTY, SPX, SPY, NDX, QQQ, IWM` (and `RUT` if we compute it — see §3 gotcha).

**Invariant A — micro ≡ big:** `MES` block values must be byte-identical to `ES`; `MNQ` ≡ `NQ`. (The vendor does this; the indicator's refs lookup and users' cross-checks assume it.)

**Invariant B — uniform futures basis:** for every position i, `futures[i] − cash_index[i]` must be **one constant** across all 12 positions (e.g., Sep 4: ES − SPX = 10 at every slot; Jul 24: 36 at every slot; NQ − NDX = 31/254 on their respective days). The indicator derives the basis as the **median** of the 12 diffs and the median is only meaningful because the shift is uniform. Implementation: compute levels on the cash index (SPX/NDX/RUT), then produce the futures blocks by adding the day's single basis value to every slot. Never compute futures levels independently and let per-slot basis wobble in.

**Invariant C — ETF blocks are that chain's own levels,** not scaled copies of the index block. SPY's walls live at different equivalent prices than SPX's — that divergence is the entire point of the ETF overlay. The indicator does the SPY→futures axis conversion itself using the `[REFS]` ratio and pack-derived basis; the service must NOT pre-convert.

---

## 3. How the indicator consumes the file (what "matching" must survive)

Understanding the downstream math clarifies which emission mistakes are silent vs. loud.

1. **Native levels** (chart symbol's block): 12 levels drawn as horizontal lines with dynamic labels. Roles recomputed per bar close: GD/C → Support/Resistance by price side; CW → Resistance or "Magnet ↓" when breached; PW → Support or "Magnet ↑"; TL/DP → "Pos γ"/"Neg γ" with a "Battleground" state within 4 ticks (default). `DP` drives the public `CurrentGammaRegime` API.
2. **Basis derivation:** `basis = median(native_futures[i] − cash_index[i])` over the 12 slots. Requires the futures AND its cash-index block both present (`ES`/`MES`→`SPX`, `NQ`/`MNQ`→`NDX`, `RTY`→`RUT`).
3. **Ratio derivation:** `ratio = (futures_ref − basis) / etf_ref` from `[REFS]`.
4. **ETF overlay:** for the chart's ETF sibling block, each level converts as `futures_equiv = RoundToTick(etf_value × ratio + basis)`. Always drawn: CW, PW, TL, DP, GD1, C1; GD2–4/C2–4 behind a toggle. A converted level landing within 8 ticks (default `EtfConfluenceTicks`) of a native level draws no line — the native label gets a `+SPY <FIELD>` tag instead (accumulating, e.g. `+SPY DP, SPY C2`).
5. **Prior-day overlay** (`[PRIOR]`): per level, if within the same 8-tick tolerance of any current native level → that native label gets `• Held` (no ghost line); otherwise a dimmed dotted ghost line labeled `Y <FIELD> <price>`. Majors + toggle-gated minors. Prior levels are excluded from the public proximity API.
6. **Tick rounding** happens at render, per instrument.

**Gotcha — RUT:** the vendor's packs stopped including a `RUT` block, which means on RTY charts the basis can't be derived and the IWM overlay is skipped (warning printed; a manual basis override parameter exists). If our service computes RUT levels, **emit the RUT block** — it silently upgrades RTY charts to full overlay support with no indicator change.

---

## 4. Worked example (verified end-to-end, Sep 4 2026 data)

Inputs — `[REFS]`: `SPY 774.09`, `MES 7762.0`. Pack blocks (excerpt):

```
MES=7810|7510|7730|8010|7710|7010|7810|7812|7835|7913|7789|7654
SPX=7800|7500|7720|8000|7700|7000|7800|7802|7825|7903|7779|7644
SPY=780|760|772|770|775|765|760|778|748|788|758|771
```

Derived on an MES chart:
- basis = median(MES−SPX) = **10** (uniform at all 12 slots — Invariant B)
- ratio = (7762.0 − 10) / 774.09 = **10.01434**
- SPY conversions: PW 760 → 7621.00 · GD1 770 → **7721.00** · TL 772 → 7741.00 · C1 778 → 7801.25 · CW 780 → 7821.25 · DP 771 → 7731.00 → **within 1.00 pt (4 ticks) of native TL 7730 → suppressed, native TL tagged `+SPY DP`**

A service-generated file reproducing these inputs must reproduce exactly these outputs. This example doubles as the first regression test.

---

## 5. Matching checklist for the service (run before calling any output "done")

Structural (per emitted file):
- [ ] Sections present: `[REFS]` (symbol-keyed), `[PACK]` (with parseable date line), `[PRIOR]` (yesterday's pack, rolled)
- [ ] Every block: bare symbol, `=`, exactly 12 `|`-separated invariant-culture floats, blocks `;`-separated
- [ ] `MES` ≡ `ES` and `MNQ` ≡ `NQ` byte-identical (Invariant A)
- [ ] `futures[i] − index[i]` constant across all 12 slots for each complex (Invariant B)
- [ ] ETF blocks are native chain levels, not conversions (Invariant C)
- [ ] Unavailable levels emitted as `0`, never omitted
- [ ] Refs are simultaneous same-snapshot observations, both symbols of each pair present

Behavioral (mirror-parse in Python; port the ~40-line section reader + block walker from this spec, or lift `ParseV2Blocks`/`ReadPackFile` logic from `GexLevelsPastePack.cs`):
- [ ] Round-trip: generated file → mirror parser → same values back
- [ ] Basis derivation on generated file yields the intended single basis
- [ ] Ratio from generated refs is sane (SPX/SPY ≈ 10.0–10.05 zone; NDX/QQQ ≈ 40–41 zone as of late 2026 — assert loose bounds to catch swapped refs)
- [ ] Worked example above reproduces exactly when fed the Sep 4 inputs

Operational:
- [ ] Atomic write (temp + rename)
- [ ] `[PRIOR]` rolls automatically (today's `[PACK]` becomes tomorrow's `[PRIOR]`)
- [ ] Date line updates with each generation (drives the stale warning)

---

## 6. Reference files

- `GexLevelsPastePack.cs` — the consumer; source of truth for parsing (`ReadPackFile`, `ParseV2Blocks`, `ParsePackV2`) and consumption (`BuildEtfOverlay`, `BuildPriorDayLevels`). In the NinjaTrader project.
- `gex_pack.txt` — current live sample (Sep 3/4 vendor data + SPY/MES refs), valid target output to diff against.
- `CLAUDE.md` (GEX Analysis Service) — the broader v2 service design (composite cross-complex model, richer JSON output). **This spec is the v1-compat lane of that plan:** the service's paste-pack emitter targets this file format so the existing indicator works unchanged while the richer JSON consumer gets built later. Where that doc and this one overlap on the pack format, this spec (extracted from the live indicator) wins.
