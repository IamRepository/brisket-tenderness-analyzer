# Brisket Session Analyser v2.8.0

## Comma decimals

- Temperatures such as `23,5`, `88,0 °C` and `1.023,5` are now read correctly.
- Semicolon-separated CSV files with comma decimals (common European logger exports) now load.
- In v2.7.1 every reading in such a file was discarded.

## Same-location probe averaging

- Probes at the same location (Point with Point, Flat with Flat) are now averaged even when they log at different seconds.
- Every reading time of every probe goes on one shared time grid.
- At each point, each probe contributes its latest reading, as long as that reading is within the probe's own gap threshold.
- In v2.7.1, probes logging at different seconds were interleaved instead of averaged, which had three effects:
  - the curve zig-zagged between probes;
  - the peak and the detected pull followed whichever probe read hotter;
  - the gap threshold was halved.
- Gaps shared by all probes are still left empty and never bridged.
- If one probe drops out, the remaining probes carry the profile.
- Sampling interval and gap threshold for a combined profile come from its member probes, not from the merged grid.

## Removed

- The "Try reference brisket" data source, its built-in demo curve and its test. Upload is now the only data source.

## Fixed

- Source names no longer show "Pointt". The typo correction for "Poin" now matches whole words only.

## Tests

- New `tests/test_v28.py` covers comma decimals, offset probes, single-probe passthrough, shared gaps, probe dropout and rendering equivalence.
- `test_v24.test_classification` still expects the pre-2.7 role names and fails, as it did in v2.7.1.
