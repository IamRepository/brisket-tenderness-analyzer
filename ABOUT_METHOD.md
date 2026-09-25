# About the Methodology

## Background

Brisket Session Analyser estimates brisket tenderness using probe temperature history and time-at-temperature calculations.

The methodology implemented in this project is based on work developed and published by Steve Gow.

## What the Application Does

1. Reads probe temperature data.
2. Calculates elapsed time between readings.
3. Classifies cook and hold phases.
4. Estimates rendering contribution from the observed temperature history.
5. Produces a rendering estimate and tenderness assessment.

## What the Application Does Not Do

The application does not replace:

- Probe tenderness checks
- Visual inspection
- Slice testing
- Pitmaster judgement

## Attribution

The underlying rendering methodology belongs to Steve Gow.

This project is an independent implementation of those published concepts.
