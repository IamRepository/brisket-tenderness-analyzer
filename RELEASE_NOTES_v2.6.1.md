# Brisket Session Analyser v2.6.1

## Fixed

- Added one master session start across all uploaded streams.
- Hold streams no longer restart at elapsed hour zero.
- Added elapsed session hours measured from cook start.
- Added support for a smoker-to-hold transfer marker.
- Preserved raw timestamps and genuine gaps.
- Prevented interpolation across the transfer boundary.

## Chart wording

Changed:

```text
Elapsed time from each stream start (hours)
```

to:

```text
Elapsed time from cook start (hours)
```
