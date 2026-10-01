# v2.6.1 Master Session Timeline Integration

## Purpose

All meat and environment streams must use one zero point:

```text
master_session_start = earliest valid timestamp across every selected stream
```

Do not calculate elapsed time separately for each uploaded profile.

## Application integration

After meat and environment streams have been prepared, build the timeline:

```python
from master_timeline import (
    add_transfer_marker,
    align_streams_to_master,
    build_master_timeline,
)

all_stream_frames = []
for valid in meat_data.values():
    all_stream_frames.append((valid, "timestamp"))
for item in environment_profiles.values():
    all_stream_frames.append((item["data"], "timestamp"))

master_timeline = build_master_timeline(
    all_stream_frames,
    transfer_time=transfer_time,
)
```

Create chart streams:

```python
chart_streams = {}
for label, valid in meat_data.items():
    chart_streams[label] = (valid, "timestamp", "temperature_c")
for label, item in environment_profiles.items():
    chart_streams[label] = (item["data"], "timestamp", "temperature_c")

master_chart_data = align_streams_to_master(
    chart_streams,
    master_timeline,
)
```

Plot against master elapsed hours:

```python
figure = px.line(
    master_chart_data,
    x="Elapsed session hours",
    y="Temperature °C",
    color="Stream",
    labels={
        "Elapsed session hours": "Elapsed time from cook start (hours)",
    },
)
figure = add_transfer_marker(figure, master_timeline)
st.plotly_chart(figure, use_container_width=True, key="master_session_chart")
```

## PDF/report integration

The report generator must use the same `master_chart_data`. Remove code similar to:

```python
elapsed_hours = (timestamp - stream_timestamp.min()).total_seconds() / 3600
```

Replace it with:

```python
elapsed_hours = (
    timestamp - master_timeline.session_start
).total_seconds() / 3600
```

Change the axis title from:

```text
Elapsed time from each stream start (hours)
```

to:

```text
Elapsed time from cook start (hours)
```

Add a dashed vertical transfer marker at:

```python
master_timeline.transfer_elapsed_hours
```

## Integrity rules

- Preserve original timestamps.
- Never move a stream simply to make traces overlap.
- Never interpolate across the smoker-to-hold transfer.
- Later-starting hold profiles must begin at their real session offset.
- Missing periods must remain visually empty.
- Apply the master clock to meat, Cook PID, Cook Grate and Hold Environment.
