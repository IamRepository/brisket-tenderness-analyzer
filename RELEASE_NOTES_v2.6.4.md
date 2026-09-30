# Brisket Session Analyser v2.6.4

## Gap 1 - Explicit environment segmentation
- Transfer boundary is derived only from Cook + Hold brisket probes.
- Hold-only and calibration probe streams do not affect smoker pull detection.
- Cook PID and Cook Grate are retained only before transfer.
- Hold Environment is retained only from transfer onwards.
- A coverage table reports readings retained and excluded by phase.

## Gap 2 - Phase-aware alignment
- Environment streams are aligned to the combined brisket timestamp set.
- Alignment uses sampling-aware nearest-time tolerances.
- No interpolation is performed across the smoker-to-hold transfer.
- Separate Cook and Hold aggregate environment curves are generated.
- Coverage and sensor availability are reported.
