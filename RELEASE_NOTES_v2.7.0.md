# Brisket Session Analyser v2.7.0

## Rendering interpretation correction

- Consolidates all Point sources into one canonical Point profile.
- Consolidates all Flat sources into one canonical Flat profile.
- Averages simultaneous measurements at the same physical location before analysis.
- Preserves missing periods and does not interpolate across gaps.
- Calculates rendering once per physical location over the complete Cook plus Hold timeline.
- Reports one Point assessment and one Flat assessment, rather than one assessment per uploaded stream.
- Reports one whole-brisket tenderness assessment based on the mean of canonical Point and Flat rendering totals.
- Removes the premature Brisket Balance Score feature. Balance scoring is deferred until reporting is validated.

## Retained

- v2.6.9 portrait PDF layout.
- Shared master timeline and transfer markers.
- One-decimal temperature formatting.
- Environment labels and source-name normalisation.
