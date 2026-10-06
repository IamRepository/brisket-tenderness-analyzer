from __future__ import annotations
from datetime import datetime
from io import BytesIO
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, CondPageBreak, KeepTogether
from reportlab.graphics.shapes import Drawing, Line, String, Group, PolyLine, Circle
from reportlab.pdfbase.pdfmetrics import stringWidth

DARK=colors.HexColor('#27343B'); ACCENT=colors.HexColor('#A94722'); LIGHT=colors.HexColor('#F3F5F6'); GRID=colors.HexColor('#D6DCE0'); TRANSFER=colors.HexColor('#7A3E9D')
PALETTE=[colors.HexColor(x) for x in ('#C6532B','#2F6B9A','#5B7F4B','#8A5A9B','#C18B2F')]; CONTENT=A4[0]-28*mm
STYLES=getSampleStyleSheet();STYLES.add(ParagraphStyle(name='ReportTitle',parent=STYLES['Title'],fontSize=21,leading=25,textColor=DARK));STYLES.add(ParagraphStyle(name='Section',parent=STYLES['Heading2'],fontSize=14,leading=17,textColor=ACCENT,spaceBefore=8,spaceAfter=6));STYLES.add(ParagraphStyle(name='Small',parent=STYLES['BodyText'],fontSize=7.4,leading=9));STYLES.add(ParagraphStyle(name='Cell',parent=STYLES['BodyText'],fontSize=7.1,leading=8.6));STYLES.add(ParagraphStyle(name='HeadCell',parent=STYLES['Cell'],fontName='Helvetica-Bold',textColor=colors.white));STYLES.add(ParagraphStyle(name='CellC',parent=STYLES['Cell'],alignment=1));STYLES.add(ParagraphStyle(name='HeadCellC',parent=STYLES['HeadCell'],alignment=1));STYLES.add(ParagraphStyle(name='Callout',parent=STYLES['BodyText'],fontSize=8.6,leading=11,textColor=colors.HexColor('#5A3A00')))

def _text(v):
    if v is None or (isinstance(v,float) and pd.isna(v)):return 'N/A'
    if isinstance(v,pd.Timestamp):return v.strftime('%d/%m/%Y %H:%M:%S')
    t=str(v)
    for x in ('🥩','🔥','🌡','♨','🚫','🟦','🟩'):t=t.replace(x,'')
    return t.strip()

def _temp(v):return 'N/A' if v is None or pd.isna(v) else f'{float(v):.1f}'
def _short(v):
    t=_text(v);return t.split(' — ',1)[0].strip() if ' — ' in t else t

def _source(v):
    t=_text(v)
    if '/' not in t:return t
    a,b=[x.strip() for x in t.split('/',1)];low=b.lower()
    if low.startswith('poin') and set(low[4:])<={'t'}:b='Point'
    elif low=='enviroment':b='Environment'
    return f'{a} / {b}'

def _env(v,stage=None):
    t=_short(v).lower()
    if 'composite' in t:return f'Composite Environment ({stage})' if stage else 'Composite Environment'
    if 'hold' in t:return 'Hold Environment'
    if 'grate' in t:return 'Cook Environment - Grate'
    if 'pid' in t or 'controller' in t:return 'Cook Environment - PID'
    return _short(v)

def _stage(label,r):
    role=_short(label);k=r.detection.session_type
    if k=='Cook + Hold':return f'{role} (Cook + Hold)'
    if k in ('Hold Only','Calibration / Hold Test'):return f'{role} (Hold only)'
    if k=='Cook Only':return f'{role} (Cook only)'
    return f'{role} ({k})'

def _cell_style(header_row,column,centre):
    name=('HeadCell' if header_row else 'Cell')+('C' if column in centre else '')
    return STYLES[name]

def _table(rows,widths,size=7.1,header=True,centre=()):
    t=Table([[Paragraph(_text(x),_cell_style(header and i==0,j,centre)) for j,x in enumerate(row)] for i,row in enumerate(rows)],colWidths=widths,repeatRows=1 if header else 0,splitByRow=1,hAlign='LEFT')
    cmd=[('GRID',(0,0),(-1,-1),.35,GRID),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3)]
    if header:cmd += [('BACKGROUND',(0,0),(-1,0),DARK),('TEXTCOLOR',(0,0),(-1,0),colors.white),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,LIGHT])]
    t.setStyle(TableStyle(cmd));return t

# One colour per entity, matching the app (validated categorical order).
SERIES_COLOURS={'point':'#2a78d6','flat':'#eb6834','pid':'#1baf7a','grate':'#eda100','hold':'#e87ba4'}
FALLBACK_COLOURS=['#4a3aa7','#008300','#e34948']
INK=colors.HexColor('#27343B'); MUTED=colors.HexColor('#52514e'); GRIDLINE=colors.HexColor('#E3E6E8')

def _colour(name,index):
    text=str(name).lower()
    for key,hexcode in SERIES_COLOURS.items():
        if key in text:return colors.HexColor(hexcode)
    return colors.HexColor(FALLBACK_COLOURS[index%len(FALLBACK_COLOURS)])

def _nice_step(span,target):
    """A round step (1, 2, 2.5, 5 x 10^n) giving about `target` intervals."""
    import math
    if span<=0:return 1.0
    raw=span/target;power=10**math.floor(math.log10(raw))
    for m in (1,2,2.5,5,10):
        if raw<=m*power:return m*power
    return 10*power

def _segments(hours,values,max_gap_h):
    """Split a series wherever readings are further apart than max_gap_h, so gaps stay empty."""
    out=[];current=[]
    for i,(x,y) in enumerate(zip(hours,values)):
        if current and x-current[-1][0]>max_gap_h:
            out.append(current);current=[]
        current.append((x,y))
    if current:out.append(current)
    return out

def _chart(series,title,origin=None,transfer=None,unit='°C',band=None,height=88*mm):
    """Line chart on the master timeline.

    series: (name, frame, time column, value column). Lines only (no point
    markers), broken at recording gaps, one fixed colour per entity, legend
    above the plot, light gridlines, round axis steps. band=(low, high, label)
    shades a horizontal range, e.g. the ideal rendering range.
    """
    d=Drawing(CONTENT,height)
    left,right,bottom=17*mm,4*mm,13*mm
    top=height-21*mm            # room above the plot for title, legend and transfer label
    plot_w=CONTENT-left-right;plot_h=top-bottom
    lines=[];xs=[];ys=[]
    for index,(name,f,tc,vc) in enumerate(series):
        q=f[[tc,vc]].dropna().sort_values(tc)
        if q.empty:continue
        base=pd.Timestamp(origin) if origin is not None else q[tc].min()
        hours=((q[tc]-base).dt.total_seconds()/3600).to_numpy();values=pd.to_numeric(q[vc],errors='coerce').to_numpy()
        steps=pd.Series(hours).diff().dropna();steps=steps[steps>0]
        max_gap=max(float(steps.median())*3,float(steps.median())+1/3600) if not steps.empty else float('inf')
        if len(q)>600:   # thin very dense series for file size; gaps are found first
            keep=max(len(q)//600,1);segments=[seg[::keep]+[seg[-1]] for seg in _segments(hours,values,max_gap)]
        else:
            segments=_segments(hours,values,max_gap)
        lines.append((_short(name),_colour(name,index),segments))
        xs.extend(hours);ys.extend(v for v in values if pd.notna(v))
    if not lines:return Paragraph('No chart data available.',STYLES['BodyText'])

    xmin,xmax=min(xs),max(xs)
    if band:ys+= [band[0],band[1]]
    xstep=_nice_step(xmax-xmin,8);xlo=xstep*(xmin//xstep);xhi=xstep*-(-xmax//xstep)
    ystep=_nice_step(max(ys)-min(ys),5);ylo=ystep*(min(ys)//ystep);yhi=ystep*-(-max(ys)//ystep)
    if yhi==ylo:yhi=ylo+ystep
    if xhi==xlo:xhi=xlo+xstep
    px=lambda x:left+(x-xlo)/(xhi-xlo)*plot_w
    py=lambda y:bottom+(y-ylo)/(yhi-ylo)*plot_h
    label_fmt=(lambda v:f'{v:g}')

    # Title and legend (one row of swatches; wraps if it runs out of width)
    d.add(String(left,height-6*mm,title,fontName='Helvetica-Bold',fontSize=10,fillColor=INK))
    lx,ly=left,height-12*mm
    for name,colour,_ in lines:
        width=14+stringWidth(name,'Helvetica',7)+12
        if lx+width>left+plot_w:lx=left;ly-=9
        d.add(Line(lx,ly+2.5,lx+10,ly+2.5,strokeColor=colour,strokeWidth=2))
        d.add(String(lx+14,ly,name,fontName='Helvetica',fontSize=7,fillColor=MUTED))
        lx+=width

    # Grid and axes
    if band:
        from reportlab.graphics.shapes import Rect
        d.add(Rect(left,py(band[0]),plot_w,py(band[1])-py(band[0]),fillColor=colors.Color(0.05,0.64,0.05,alpha=0.13),strokeColor=None))
        d.add(String(left+3,py(band[1])-8,band[2],fontName='Helvetica',fontSize=6.5,fillColor=colors.HexColor('#0a7a0a')))
    y=ylo
    while y<=yhi+1e-9:
        d.add(Line(left,py(y),left+plot_w,py(y),strokeColor=GRIDLINE,strokeWidth=0.5))
        d.add(String(left-3,py(y)-2.3,label_fmt(y),fontName='Helvetica',fontSize=6.5,fillColor=MUTED,textAnchor='end'))
        y+=ystep
    x=xlo
    while x<=xhi+1e-9:
        d.add(Line(px(x),bottom,px(x),bottom+plot_h,strokeColor=GRIDLINE,strokeWidth=0.5))
        d.add(String(px(x),bottom-8,label_fmt(x),fontName='Helvetica',fontSize=6.5,fillColor=MUTED,textAnchor='middle'))
        x+=xstep
    d.add(Line(left,bottom,left+plot_w,bottom,strokeColor=MUTED,strokeWidth=0.6))
    d.add(String(left+plot_w/2,1.5*mm,'Elapsed time from session start (hours)',fontName='Helvetica',fontSize=7,fillColor=MUTED,textAnchor='middle'))
    ylabel=Group(String(0,0,f'Rendering ({unit})' if unit=='%' else f'Temperature ({unit})',fontName='Helvetica',fontSize=7,fillColor=MUTED,textAnchor='middle'))
    ylabel.transform=(0,1,-1,0,4*mm,bottom+plot_h/2)   # rotated 90 degrees, clear of the tick labels
    d.add(ylabel)

    # Transfer marker: dashed line, label above the plot so it never sits on a curve
    if transfer is not None and origin is not None:
        th=(pd.Timestamp(transfer)-pd.Timestamp(origin)).total_seconds()/3600
        if xlo<=th<=xhi:
            mx=px(th)
            d.add(Line(mx,bottom,mx,bottom+plot_h+3,strokeColor=MUTED,strokeWidth=0.9,strokeDashArray=[3,2]))
            d.add(String(mx+2,bottom+plot_h+4,'Smoker to hold',fontName='Helvetica',fontSize=6.5,fillColor=MUTED))

    # Data lines last, so they sit on top of the grid
    for name,colour,segments in lines:
        for seg in segments:
            pts=[(px(x),py(y)) for x,y in seg if pd.notna(y)]
            if len(pts)>=2:
                d.add(PolyLine([c for p in pts for c in p],strokeColor=colour,strokeWidth=1.5,strokeLineJoin=1,strokeLineCap=1))
            elif pts:
                d.add(Circle(pts[0][0],pts[0][1],1.2,fillColor=colour,strokeColor=None))
    return d

def _footer(canvas,doc):canvas.saveState();canvas.setFont('Helvetica',7);canvas.setFillColor(DARK);canvas.drawString(14*mm,8*mm,'Brisket Tenderness Analyzer - analytical report');canvas.drawRightString(A4[0]-14*mm,8*mm,f'Page {doc.page}');canvas.restoreState()
def _interval(x):return 'Unknown' if x is None else (f'{x:.0f} sec' if x<60 else (f'{x/60:.1f} min' if x<3600 else f'{x/3600:.2f} h'))
def _duration(f):return 0 if f is None or len(f)<2 else (f.timestamp.iloc[-1]-f.timestamp.iloc[0]).total_seconds()/3600

ASSESSMENT_SCALE = [
    ('Below 80 %', 'Underdone and tight'),
    ('80 % to below 95 %', 'Slightly tight but sliceable'),
    ('95 % to 105 %', 'Ideal tenderness'),
    ('Above 105 % to 120 %', 'Very soft and potentially overdone'),
    ('Above 120 %', 'Increased risk of mushy or over-rendered texture'),
]


def _when(v):
    return 'N/A' if v is None else pd.Timestamp(v).strftime('%d/%m/%Y %H:%M')


def _hours(seconds):
    if seconds is None:
        return 'N/A'
    h, m = divmod(int(seconds / 60 + 0.5), 60)  # round half up, matching the 2-decimal hours elsewhere
    return f'{h} h {m:02d} min'


def _pct(v):
    return 'N/A' if v is None else f'{v:.1%}'


def _callout(text):
    box = Table([[Paragraph(text, STYLES['Callout'])]], colWidths=[CONTENT], hAlign='LEFT')
    box.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#FFF4E0')),
        ('BOX', (0, 0), (-1, -1), 0.8, colors.HexColor('#E0A040')),
        ('LEFTPADDING', (0, 0), (-1, -1), 8), ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 6), ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    return box


def _generated_text(generated_at):
    if generated_at is None:
        generated_at = datetime.now()
    zone = generated_at.tzname() if generated_at.tzinfo is not None else None
    return f"Generated {generated_at:%d/%m/%Y %H:%M}" + (f" ({zone})" if zone else "")


def build_pdf_report(app_version, configuration, meat_results, stats, environment_results, transfer_time,
                     environment_sources=None, master_start=None, cook_aggregate=None, hold_aggregate=None,
                     environment_integrity=None, overall_assessment=None, generated_at=None):
    environment_sources = environment_sources or {}
    environment_integrity = environment_integrity or []
    meat_starts = [x['valid'].timestamp.min() for x in meat_results.values() if not x['valid'].empty]
    meat_ends = [x['valid'].timestamp.max() for x in meat_results.values() if not x['valid'].empty]
    if master_start is None:
        starts = meat_starts + [r.timeline.timestamp.min() for r in environment_results.values() if not r.timeline.empty]
        master_start = min(starts) if starts else None
    session_end = max(meat_ends) if meat_ends else None

    out = BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=14*mm, rightMargin=14*mm, topMargin=13*mm, bottomMargin=15*mm,
                            title=f'Brisket Tenderness Analyzer {app_version}', author='Brisket Tenderness Analyzer')
    s = []
    section = lambda title: Paragraph(title, STYLES['Section'])
    small = lambda text: Paragraph(text, STYLES['Small'])

    # ---- Summary (page 1): the answer first --------------------------------
    cook_date = f" of {pd.Timestamp(master_start):%d %B %Y}" if master_start is not None else ''
    s += [Paragraph(f'Brisket Tenderness Analyzer {app_version}', STYLES['ReportTitle']),
          Paragraph(f'Brisket session report{cook_date}', STYLES['Heading2']),
          small(_generated_text(generated_at)),
          section('Summary')]

    results = [['Location', 'Cook', 'Hold', 'Total', 'Assessment']]
    for label, item in meat_results.items():
        r = item['result']
        results.append([_short(label), _pct(r.cook), _pct(r.hold), _pct(r.total), r.assessment if r.complete else 'Partial session'])
    if overall_assessment:
        whole = 'Whole brisket (mean of Point and Flat)' if overall_assessment['locations'] > 1 else 'Whole brisket'
        results.append([f'<b>{whole}</b>', '', '', f"<b>{_pct(overall_assessment['total'])}</b>", f"<b>{overall_assessment['assessment']}</b>"])
    summary_table = _table(results, [52*mm, 20*mm, 20*mm, 20*mm, 70*mm])
    if overall_assessment and len(results) > 2:
        summary_table.setStyle(TableStyle([('LINEABOVE', (0, -1), (-1, -1), 0.9, DARK)]))
    s += [summary_table, Spacer(1, 3*mm)]
    if overall_assessment and overall_assessment.get('spread_note'):
        s += [_callout(overall_assessment['spread_note']), Spacer(1, 3*mm)]

    cook_seconds = (pd.Timestamp(transfer_time) - pd.Timestamp(master_start)).total_seconds() if transfer_time is not None and master_start is not None else None
    hold_seconds = (pd.Timestamp(session_end) - pd.Timestamp(transfer_time)).total_seconds() if transfer_time is not None and session_end is not None else None
    total_seconds = (pd.Timestamp(session_end) - pd.Timestamp(master_start)).total_seconds() if session_end is not None and master_start is not None else None
    s += [_table([['Session start', _when(master_start), 'Cook time', _hours(cook_seconds)],
                  ['Transfer to hold', _when(transfer_time), 'Hold time', _hours(hold_seconds)],
                  ['Last brisket reading', _when(session_end), 'Total', _hours(total_seconds)]],
                 [38*mm, 53*mm, 38*mm, 53*mm], header=False),
          Spacer(1, 2*mm)]

    meat_series = [(_stage(label, item['result']), item['valid'], 'timestamp', 'temperature_c') for label, item in meat_results.items()]
    s += [_chart(meat_series, 'Brisket Point and Flat temperature profiles', master_start, transfer_time)]

    # ---- Session details ----------------------------------------------------
    timing = [['Profile', 'Source', 'Session', 'Conf.', 'Detected pull', 'Cook h', 'Hold h']]
    temps = [['Profile', 'Peak °C', 'Avg °C', 'Min °C', 'Cook avg °C', 'Hold avg °C', 'Sampling', 'Gap limit']]
    comp = [['Profile', 'Source', 'Cook', 'Hold', 'Total', 'Assessment', 'Analysed h (≥ 60 °C)']]
    for label, item in meat_results.items():
        r = item['result']; st = stats[label]; sa = item['sample']; name = _stage(label, r)
        timing.append([name, _source(item['source']), r.detection.session_type, f'{r.detection.confidence}%',
                       _when(r.detection.pull_timestamp), f"{st['cook_h']:.2f}", f"{st['hold_h']:.2f}"])
        temps.append([name, _temp(r.detection.peak_temperature), _temp(r.detection.average_temperature),
                      _temp(r.detection.minimum_temperature), _temp(st['cook_avg']), _temp(st['hold_avg']),
                      _interval(sa['normal']), _interval(sa['threshold'])])
        comp.append([name, _source(item['source']), _pct(r.cook), _pct(r.hold), _pct(r.total),
                     r.assessment if r.complete else 'Partial session', f'{r.analysed_hours:.2f}'])
    split_note = (f"Cook and hold hours are split at the shared transfer time ({_when(transfer_time)}), the same split used for "
                  "rendering. Detected pull is where each profile peaked." if transfer_time is not None else
                  "No shared transfer time was derived; hours are split at each profile's detected pull.")
    s += [CondPageBreak(80*mm), section('Session details'),
          _table(timing, [42*mm, 40*mm, 24*mm, 13*mm, 29*mm, 17*mm, 17*mm], 6.8), small(split_note), Spacer(1, 4*mm),
          KeepTogether([Paragraph('Temperature and sampling', STYLES['Heading4']),
                        _table(temps, [44*mm, 16*mm, 16*mm, 16*mm, 20*mm, 20*mm, 25*mm, 25*mm], 6.8)]), Spacer(1, 4*mm),
          KeepTogether([Paragraph('Rendering by profile', STYLES['Heading4']),
                        _table(comp, [40*mm, 38*mm, 15*mm, 15*mm, 15*mm, 39*mm, 20*mm], 6.8),
                        small('Analysed hours count only time at or above 60 °C without recording gaps; cooler time does not add to rendering.')])]
    accumulated = []
    for label, item in meat_results.items():
        timeline = item['result'].timeline
        if timeline.empty:
            continue
        first = pd.DataFrame({'t': [timeline['Timestamp'].iloc[0]], 'pct': [0.0]})
        rest = pd.DataFrame({'t': timeline['Next timestamp'], 'pct': timeline['Accumulated rendering'] * 100})
        accumulated.append((_stage(label, item['result']), pd.concat([first, rest], ignore_index=True), 't', 'pct'))
    if accumulated:
        s += [CondPageBreak(95*mm), Spacer(1, 3*mm),
              _chart(accumulated, 'Accumulated rendering over time', master_start, transfer_time, unit='%', band=(95, 105, 'Ideal (95-105 %)'))]

    # ---- Environment ----------------------------------------------------------
    s += [CondPageBreak(70*mm), section('Environment')]
    if environment_integrity:
        rows = [['Environment role', 'Status', 'Streams']] + [[x.get('Environment role'), x.get('Status'), x.get('Streams')] for x in environment_integrity]
        integrity_table = _table(rows, [92*mm, 45*mm, 45*mm], centre=(0, 1, 2))
        s += [integrity_table, Spacer(1, 4*mm)]
    if environment_results:
        er = [['Stream', 'Source', 'Stage', 'Start', 'End', 'h', 'Avg °C', 'Min °C', 'Max °C', 'Stability', 'Level changes']]
        es = []
        for label, r in environment_results.items():
            f = r.timeline; n = _env(r.role); stage = 'Hold' if n == 'Hold Environment' else 'Cook'
            er.append([n, _source(environment_sources.get(label, 'N/A')), stage,
                       f.timestamp.min().strftime('%d/%m %H:%M'), f.timestamp.max().strftime('%d/%m %H:%M'),
                       f'{_duration(f):.2f}', _temp(r.average), _temp(r.minimum), _temp(r.maximum),
                       f'{r.stability_score:.0f}/100', str(getattr(r, 'level_changes', 0))])
            es.append((n, f, 'timestamp', 'temperature_c'))
        s += [_table(er, [27*mm, 26*mm, 11*mm, 19*mm, 19*mm, 10*mm, 12*mm, 12*mm, 12*mm, 16*mm, 18*mm], 6.4),
              small('Stability (0-100) is scored while the cooker holds a temperature: deliberate setpoint steps and the '
                    'ramps between them are not counted against it. Level changes is the number of setpoint steps found.'),
              Spacer(1, 3*mm),
              _chart(es, 'Cook and Hold Environment profiles', master_start, transfer_time)]
    else:
        s += [small('No environment streams were assigned.')]

    # The composite only adds information when a stage combines several sensors.
    hold_sensors = hold_aggregate['Available sensors'].max() if hold_aggregate is not None and not hold_aggregate.empty else 0
    if hold_sensors > 1:
        ar = [['Stage', 'Start', 'End', 'Readings', 'Average °C', 'Sensors']]; cs = []
        for stage, f in (('Cook', cook_aggregate), ('Hold', hold_aggregate)):
            if f is not None and not f.empty:
                cs.append((_env('Composite', stage), f, 'timestamp', 'Environment aggregate °C'))
                ar.append([stage, _when(f.timestamp.min()), _when(f.timestamp.max()), len(f),
                           _temp(f['Environment aggregate °C'].mean()), f"{f['Available sensors'].mean():.2f}"])
        s += [CondPageBreak(110*mm), section('Composite environment'),
              small('Several hold sensors are averaged where their readings overlap. Cook and Hold segments share the master timeline; the transfer gap remains visible.'),
              _table(ar, [22*mm, 38*mm, 38*mm, 22*mm, 30*mm, 32*mm]), Spacer(1, 3*mm),
              _chart(cs, 'Composite Environment', master_start, transfer_time)]

    # ---- One section per brisket location -------------------------------------
    for label, item in meat_results.items():
        r = item['result']; st = stats[label]; name = _stage(label, r)
        metrics = [['Metric', 'Value', 'Metric', 'Value'],
                   ['Source', _source(item['source']), 'Session', r.detection.session_type],
                   ['Detected pull', _when(r.detection.pull_timestamp), 'Rendering split at', _when(transfer_time) if transfer_time is not None else _when(r.detection.pull_timestamp)],
                   ['Cook duration', f"{st['cook_h']:.2f} h", 'Hold duration', f"{st['hold_h']:.2f} h"],
                   ['Cook contribution', _pct(r.cook), 'Hold contribution', _pct(r.hold)],
                   ['Recorded total', _pct(r.total), 'Assessment', r.assessment if r.complete else 'Partial session']]
        bands = r.summary[r.summary['Duration hours'] > 0]
        br = [['Phase', 'Band', 'Temperature range', 'Duration h', 'Rate/h', 'Contribution']] + [
            [x['Phase'], x['Band'], x['Temperature range'], f"{x['Duration hours']:.3f}", f"{x['Rate per hour']:.1%}", f"{x['Tenderness contribution']:.1%}"]
            for _, x in bands.iterrows()]
        # The location's temperature curve is already on page 1; this section adds the numbers behind it.
        s += [CondPageBreak(90*mm), section(name),
              _table(metrics, [30*mm, 61*mm, 30*mm, 61*mm]), Spacer(1, 3*mm)]
        if not bands.empty:
            s += [KeepTogether([Paragraph(f'Rendering band calculation - {name}', STYLES['Heading4']),
                                _table(br, [30*mm, 14*mm, 50*mm, 26*mm, 26*mm, 36*mm], 6.8)])]

    # ---- Method and configuration (reference) ---------------------------------
    config_rows = [['Role', 'Source', 'File name']] + [[_env(r.get('Role')), _source(r.get('Source')), r.get('File name') or 'N/A'] for r in configuration]
    s += [CondPageBreak(120*mm), section('Method'),
          Paragraph('Independent implementation based on brisket time-temperature and hot-hold concepts shared by Steve Gow. '
                    'Results are analytical estimates.', STYLES['BodyText']),
          Paragraph('Rendering accumulates for every interval the brisket spends at or above 60 °C, at an hourly rate that rises '
                    'with temperature across ten bands (shown in each band calculation table). Intervals below 60 °C and '
                    'recording gaps longer than the gap limit add nothing. Contributions before the transfer count as Cook, '
                    'after it as Hold. Probes at the same location are averaged over time first, so each location is '
                    'calculated once. The whole-brisket figure is the mean of the Point and Flat totals.', STYLES['BodyText']),
          Spacer(1, 2*mm), Paragraph('Assessment scale', STYLES['Heading4']),
          _table([['Recorded total', 'Assessment']] + [list(x) for x in ASSESSMENT_SCALE], [50*mm, 132*mm]),
          Spacer(1, 2*mm),
          small('All charts use one master session timeline from the earliest reading of any stream. The transfer marker shows the '
                'derived smoker-to-hold boundary. Missing values are not interpolated across the transfer.'),
          KeepTogether([section('Configuration'), _table(config_rows, [52*mm, 62*mm, 68*mm])])]

    doc.build(s, onFirstPage=_footer, onLaterPages=_footer)
    return out.getvalue()
