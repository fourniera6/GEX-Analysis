# Drawing level history on the NT8 chart (`[HISTORY]`, `[PREMARKET]`)

**For:** `GexLevelsPastePack.cs` (AJ's indicator). The service already writes both sections. The current indicator ignores them (spec: "Unknown sections are ignored"), so nothing changes on the chart until the indicator learns to read them. This doc is the indicator-side change. The service doesn't have the indicator's source, so the method names below come from `docs/GEX-PACK-SPEC.md` and the C# is a sketch to adapt, not a drop-in.

**Why:** intraday updates move levels. On 7 Oct SPY PW went 767→777 at 15:42 and QQQ PW went 700→755 at 10:42. Today the chart redraws each level at its newest price across the whole chart, so you can't see where a level used to be or when it moved. With history, each level draws as a **stepped line**: flat while it held, stepping at the bar where its update was written.

## The format

```
[HISTORY]
2026-10-07T08:29 ES=7956.46|7552.46|7819.56|...;MES=...;NQ=...;MNQ=...;RTY=...;SPX=...;SPY=...;NDX=...;QQQ=...;RUT=...;IWM=...
2026-10-07T09:28 ES=...
2026-10-07T10:12 ES=...
```

- **One line per pack written this session, oldest first.** Each line is the write time, a space, then that pack's full v2 blocks (same 11 symbols, same 12 slots, same `;` separators as `[PACK]`).
- **The time is local ET** (`yyyy-MM-ddTHH:mm`), the moment the pack was written, i.e. when the levels reached the chart. The underlying data is about 15 minutes older (CBOE's delay). Drawing at the write time means the chart shows what you could actually have seen then, with no look-ahead.
- **Lines are not joined**, unlike `[PACK]`/`[PRIOR]`. Each line stands alone.
- **The last line equals `[PACK]`.** The section restarts when the session date changes, like `[PREMARKET]`. A full `--intraday` day is about 15 lines (~20 KB).
- `[PREMARKET]` is the session's pack as of the open, in the normal `[PACK]` layout (date line + blocks).

## Parsing (sketch)

```csharp
// In ReadPackFile's section loop: keep [HISTORY] lines unjoined.
List<string> historyLines = sections.TryGetValue("HISTORY", out var h) ? h : new List<string>();

var history = new List<(DateTime written, Dictionary<string, double[]> blocks)>();
foreach (string line in historyLines)
{
    int space = line.IndexOf(' ');
    if (space <= 0) continue;
    if (!DateTime.TryParseExact(line.Substring(0, space), "yyyy-MM-ddTHH:mm",
            CultureInfo.InvariantCulture, DateTimeStyles.None, out DateTime writtenEt)) continue;
    var blocks = ParseV2Blocks(line.Substring(space + 1));   // same block walker as [PACK]
    if (blocks != null && blocks.Count > 0)
        history.Add((ConvertEtToChartTime(writtenEt), blocks));
}
```

`ConvertEtToChartTime`: the stamp is Eastern. Convert with `TimeZoneInfo.ConvertTime(writtenEt, easternTz, Core.Globals.GeneralOptions.TimeZoneInfo)` (`easternTz = TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time")`, which handles DST). A Central-time chart would otherwise step every level an hour late.

## Drawing

For the chart's native block (e.g. `MES`) and, through the existing conversion, its ETF sibling (`SPY`):

1. **Segments.** For each level slot, walk `history`. Entry *i* holds from `written[i]` until `written[i+1]`; the last entry runs to the right edge, which is the existing live line. Draw one horizontal segment per run of equal values (merge consecutive identical values) and a thin vertical riser where the value changes. In `OnRender`: `chartControl.GetXByTime(start)` → `GetXByTime(end)`, `chartScale.GetYByValue(RoundToTickSize(value))`.
2. **Styling.**
   - The newest segment is today's line, unchanged: same color, label and role logic.
   - Earlier segments use the same color dimmed (around 40% opacity), with no labels.
   - At each wall step (CW/PW), add a small text tag at the riser: `PW 767→777 15:42`. TL/DP drift every update, so give them no tags; the stepped line already shows the drift.
3. **Scope.**
   - Majors (CW, PW, TL, DP) on by default. GD/C 1–4 history behind the existing minors toggle.
   - Add one property, `ShowLevelHistory` (default true), so the chart can be returned to today's look.
   - **ETF overlay history:** convert each historical ETF value with the *current* ratio and basis, as `BuildEtfOverlay` does for today's levels. The ratio barely moves within a day.
4. **Keep it out of the API.** History segments aren't levels. Exclude them from the proximity API, `CurrentGammaRegime` and the confluence tags, the same way `[PRIOR]` ghosts are excluded.
5. **Before the session's first entry,** draw nothing from `[HISTORY]`. `[PRIOR]` already covers yesterday.

## `[PREMARKET]` (optional, smaller change)

Reuse the `[PRIOR]` path (`BuildPriorDayLevels`) with a `PM` label instead of `Y`:
- **Moved levels:** a pre-market level more than 8 ticks from today's current native level draws as a dotted ghost line labeled `PM <FIELD> <price>`.
- **Held levels:** within 8 ticks, the native label gets `• PM` instead of a ghost line.

With history drawn, `[PREMARKET]` is mostly redundant on the futures chart, since the first history segment *is* the pre-market level. It still helps when `ShowLevelHistory` is off.

## Checking it

- Sessions recorded before 8 Oct have no `[HISTORY]` in their pack copies, so rebuild one:
  `python -m gex_analysis.timeline 2026-10-07 --pack-with-history "$NT8/gex_pack_history_test.txt"`.
  It writes that session's last pack with `[HISTORY]` rebuilt from `data/packs/`. From 8 Oct on, the live `gex_pack_service.txt` carries it.
- Point a test chart's *Pack File Path* at that file and scroll to 7 Oct.
  - On an MES chart, the SPY overlay PW should be flat at 767 until the 15:42 bar, then step to 777.
  - On an MNQ chart, the QQQ overlay PW should step 700→755 at 10:42 and 755→757 at 15:42.
- The timeline page from the same command shows the same steps, so the two can be checked against each other.
