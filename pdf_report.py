from __future__ import annotations
from datetime import datetime
from io import BytesIO
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, CondPageBreak
from reportlab.graphics.shapes import Drawing, Line, String
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.widgets.markers import makeMarker

DARK=colors.HexColor('#27343B'); ACCENT=colors.HexColor('#A94722'); LIGHT=colors.HexColor('#F3F5F6'); GRID=colors.HexColor('#D6DCE0'); TRANSFER=colors.HexColor('#7A3E9D')
PALETTE=[colors.HexColor(x) for x in ('#C6532B','#2F6B9A','#5B7F4B','#8A5A9B','#C18B2F')]; CONTENT=A4[0]-28*mm
STYLES=getSampleStyleSheet();STYLES.add(ParagraphStyle(name='ReportTitle',parent=STYLES['Title'],fontSize=21,leading=25,textColor=DARK));STYLES.add(ParagraphStyle(name='Section',parent=STYLES['Heading2'],fontSize=14,leading=17,textColor=ACCENT,spaceBefore=8,spaceAfter=6));STYLES.add(ParagraphStyle(name='Small',parent=STYLES['BodyText'],fontSize=7.4,leading=9));STYLES.add(ParagraphStyle(name='Cell',parent=STYLES['BodyText'],fontSize=7.1,leading=8.6))

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

def _table(rows,widths,size=7.1,header=True):
    t=Table([[Paragraph(_text(x),STYLES['Cell']) for x in row] for row in rows],colWidths=widths,repeatRows=1 if header else 0,splitByRow=1,hAlign='LEFT')
    cmd=[('GRID',(0,0),(-1,-1),.35,GRID),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),4),('RIGHTPADDING',(0,0),(-1,-1),4),('TOPPADDING',(0,0),(-1,-1),3),('BOTTOMPADDING',(0,0),(-1,-1),3)]
    if header:cmd += [('BACKGROUND',(0,0),(-1,0),DARK),('TEXTCOLOR',(0,0),(-1,0),colors.white),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,LIGHT])]
    t.setStyle(TableStyle(cmd));return t

def _chart(series,title,origin=None,transfer=None):
    d=Drawing(CONTENT,94*mm);c=LinePlot();c.x=15*mm;c.y=27*mm;c.width=CONTENT-27*mm;c.height=50*mm;data=[];names=[];xs=[];ys=[]
    for name,f,tc,vc in series:
        q=f[[tc,vc]].dropna().sort_values(tc)
        if q.empty:continue
        if len(q)>350:q=q.iloc[::max(len(q)//350,1)]
        base=pd.Timestamp(origin) if origin is not None else q[tc].min();xx=(q[tc]-base).dt.total_seconds()/3600;yy=pd.to_numeric(q[vc],errors='coerce');pts=[(float(x),float(y)) for x,y in zip(xx,yy) if pd.notna(y)]
        if pts:data.append(pts);names.append(_short(name));xs.extend(x for x,_ in pts);ys.extend(y for _,y in pts)
    if not data:return Paragraph('No chart data available.',STYLES['BodyText'])
    xmin,xmax=min(xs),max(xs);c.data=data;c.xValueAxis.valueMin=xmin;c.xValueAxis.valueMax=xmax if xmax>xmin else xmin+1;c.xValueAxis.labelTextFormat='%0.1f';c.xValueAxis.labels.fontSize=7;c.yValueAxis.valueMin=min(ys)-2;c.yValueAxis.valueMax=max(ys)+2;c.yValueAxis.labels.fontSize=7
    for i in range(len(data)):c.lines[i].strokeColor=PALETTE[i%len(PALETTE)];c.lines[i].strokeWidth=1.2;c.lines[i].symbol=makeMarker('FilledCircle');c.lines[i].symbol.size=1.7
    l=Legend();l.x=15*mm;l.y=17*mm;l.fontSize=6.2;l.deltax=76;l.deltay=9;l.columnMaximum=2;l.colorNamePairs=[(PALETTE[i%len(PALETTE)],names[i]) for i in range(len(names))]
    d.add(c);d.add(l);d.add(String(15*mm,83*mm,title,fontName='Helvetica-Bold',fontSize=10.5,fillColor=DARK));d.add(String(15*mm,4*mm,'Elapsed time from master session start (hours)',fontSize=6.5));d.add(String(1*mm,48*mm,'Temperature (C)',fontSize=6.5))
    if transfer is not None and origin is not None:
        th=(pd.Timestamp(transfer)-pd.Timestamp(origin)).total_seconds()/3600
        if xmin<=th<=xmax:
            mx=c.x+(th-xmin)/max(xmax-xmin,1e-9)*c.width;d.add(Line(mx,c.y,mx,c.y+c.height,strokeColor=TRANSFER,strokeWidth=1,strokeDashArray=[3,2]));d.add(String(mx+2,c.y+c.height-7,'Transfer',fontSize=6.5,fillColor=TRANSFER))
    return d

def _footer(canvas,doc):canvas.saveState();canvas.setFont('Helvetica',7);canvas.setFillColor(DARK);canvas.drawString(14*mm,8*mm,'Brisket Tenderness Analyzer - analytical report');canvas.drawRightString(A4[0]-14*mm,8*mm,f'Page {doc.page}');canvas.restoreState()
def _interval(x):return 'Unknown' if x is None else (f'{x:.0f} sec' if x<60 else (f'{x/60:.1f} min' if x<3600 else f'{x/3600:.2f} h'))
def _duration(f):return 0 if f is None or len(f)<2 else (f.timestamp.iloc[-1]-f.timestamp.iloc[0]).total_seconds()/3600

def build_pdf_report(app_version,configuration,meat_results,stats,environment_results,transfer_time,environment_sources=None,master_start=None,cook_aggregate=None,hold_aggregate=None,environment_integrity=None,overall_assessment=None):
    environment_sources=environment_sources or {}
    environment_integrity=environment_integrity or []
    if master_start is None:
        starts=[x['valid'].timestamp.min() for x in meat_results.values() if not x['valid'].empty]+[r.timeline.timestamp.min() for r in environment_results.values() if not r.timeline.empty];master_start=min(starts) if starts else None
    out=BytesIO();doc=SimpleDocTemplate(out,pagesize=A4,leftMargin=14*mm,rightMargin=14*mm,topMargin=13*mm,bottomMargin=15*mm);s=[]
    # Configuration roles are normalised here as well as in analytical sections.
    config=[['Role','Source']]+[[_env(r.get('Role')),_source(r.get('Source'))] for r in configuration]
    s += [Paragraph(f'Brisket Tenderness Analyzer {app_version}',STYLES['ReportTitle']),Paragraph('Full session analysis report',STYLES['Heading2']),Paragraph(f'Generated: {datetime.now():%d/%m/%Y %H:%M:%S}',STYLES['Small']),Paragraph('Methodology',STYLES['Section']),Paragraph('Independent implementation based on brisket time-temperature and hot-hold concepts shared by Steve Gow. Results are analytical estimates.',STYLES['BodyText']),Paragraph('Configuration',STYLES['Section']),_table(config,[78*mm,102*mm]),Spacer(1,3*mm),_table([['Master timeline start',_text(master_start)],['Derived smoker-to-hold boundary',_text(transfer_time)]],[72*mm,108*mm],header=False)]
    timing=[['Profile','Source','Session','Conf.','Pull','Cook h','Hold h']];temps=[['Profile','Peak C','Avg C','Min C','Cook avg C','Hold avg C','Sampling','Gap']];comp=[['Profile','Source','Cook','Hold','Total','Assessment','h']];meat_series=[]
    for label,item in meat_results.items():
        r=item['result'];st=stats[label];sa=item['sample'];name=_stage(label,r);timing.append([name,_source(item['source']),r.detection.session_type,f'{r.detection.confidence}%',_text(r.detection.pull_timestamp),f"{st['cook_h']:.2f}",f"{st['hold_h']:.2f}"]);temps.append([name,_temp(r.detection.peak_temperature),_temp(r.detection.average_temperature),_temp(r.detection.minimum_temperature),_temp(st['cook_avg']),_temp(st['hold_avg']),_interval(sa['normal']),_interval(sa['threshold'])]);comp.append([name,_source(item['source']),f'{r.cook:.1%}',f'{r.hold:.1%}',f'{r.total:.1%}',r.assessment if r.complete else 'Partial session',f'{r.analysed_hours:.2f}']);meat_series.append((name,item['valid'],'timestamp','temperature_c'))
    s += [PageBreak(),Paragraph('Session detection and timing',STYLES['Section']),_table(timing,[42*mm,31*mm,28*mm,14*mm,32*mm,16*mm,16*mm],6.8),Spacer(1,5*mm),Paragraph('Temperature and sampling summary',STYLES['Section']),_table(temps,[48*mm,17*mm,17*mm,17*mm,22*mm,22*mm,21*mm,21*mm],6.8),PageBreak(),Paragraph('Brisket probe comparison',STYLES['Section']),_table(comp,[43*mm,31*mm,17*mm,17*mm,17*mm,42*mm,14*mm],6.8),PageBreak(),Paragraph('Brisket temperature profiles',STYLES['Section']),_chart(meat_series,'Brisket Point and Flat temperature profiles',master_start,transfer_time),PageBreak(),Paragraph('Environment analysis',STYLES['Section'])]
    if environment_integrity:
        integrity_rows = [["Environment role", "Status", "Streams"]]
        integrity_rows += [[x.get("Environment role"), x.get("Status"), x.get("Streams")] for x in environment_integrity]
        s += [Paragraph("Environment integrity", STYLES["Section"]), _table(integrity_rows, [92*mm, 48*mm, 40*mm])]
    if overall_assessment:
        overall_rows = [
            ["Whole brisket metric", "Value"],
            ["Overall rendering", f"{overall_assessment['total']:.1%}"],
            ["Overall tenderness assessment", overall_assessment["assessment"]],
            ["Canonical locations", overall_assessment["locations"]],
            ["Point total", "N/A" if overall_assessment.get("point_total") is None else f"{overall_assessment['point_total']:.1%}"],
            ["Flat total", "N/A" if overall_assessment.get("flat_total") is None else f"{overall_assessment['flat_total']:.1%}"],
        ]
        s += [
            Spacer(1, 4*mm),
            Paragraph("Whole brisket tenderness assessment", STYLES["Section"]),
            _table(overall_rows, [86*mm, 94*mm]),
            Paragraph("Probes at the same physical location are averaged over time before rendering is calculated. Cook and Hold contributions are integrated once across the canonical Point and Flat profiles.", STYLES["Small"]),
        ]
    if environment_results:
        er=[['Stream','Source','Stage','Start','End','h','Avg C','Min C','Max C','Stab.']];es=[]
        for label,r in environment_results.items():
            f=r.timeline;n=_env(r.role);stage='Hold' if n=='Hold Environment' else 'Cook';er.append([n,_source(environment_sources.get(label,'N/A')),stage,_text(f.timestamp.min()),_text(f.timestamp.max()),f'{_duration(f):.2f}',_temp(r.average),_temp(r.minimum),_temp(r.maximum),f'{r.stability_score:.0f}/100']);es.append((n,f,'timestamp','temperature_c'))
        s += [_table(er,[27*mm,28*mm,15*mm,31*mm,31*mm,11*mm,11*mm,11*mm,11*mm,15*mm],6.2),Spacer(1,4*mm),_chart(es,'Cook and Hold Environment profiles',master_start,transfer_time)]
    ar=[['Stage','Start','End','Readings','Average C','Sensors']];cs=[]
    for stage,f in (('Cook',cook_aggregate),('Hold',hold_aggregate)):
        if f is not None and not f.empty:cs.append((_env('Composite',stage),f,'timestamp','Environment aggregate °C'));ar.append([stage,_text(f.timestamp.min()),_text(f.timestamp.max()),len(f),_temp(f['Environment aggregate °C'].mean()),f"{f['Available sensors'].mean():.2f}"])
    if cs:s += [PageBreak(),Paragraph('Composite environment timeline',STYLES['Section']),Paragraph('Cook and Hold segments share the master timeline. The transfer gap remains visible.',STYLES['Small']),_table(ar,[22*mm,38*mm,38*mm,20*mm,30*mm,32*mm]),Spacer(1,4*mm),_chart(cs,'Composite Environment',master_start,transfer_time)]
    for label,item in meat_results.items():
        r=item['result'];st=stats[label];name=_stage(label,r);metrics=[['Metric','Value','Metric','Value'],['Source',_source(item['source']),'Session',r.detection.session_type],['Confidence',f'{r.detection.confidence}%','Detected pull',_text(r.detection.pull_timestamp)],['Cook duration',f"{st['cook_h']:.2f} h",'Hold duration',f"{st['hold_h']:.2f} h"],['Cook contribution',f'{r.cook:.1%}','Hold contribution',f'{r.hold:.1%}'],['Recorded total',f'{r.total:.1%}','Assessment',r.assessment if r.complete else 'Partial session']];bands=r.summary[r.summary['Duration hours']>0];br=[['Phase','Band','Temperature range','Duration h','Rate/h','Contribution']]+[[x['Phase'],x['Band'],x['Temperature range'],f"{x['Duration hours']:.3f}",f"{x['Rate per hour']:.1%}",f"{x['Tenderness contribution']:.1%}"] for _,x in bands.iterrows()]
        s += [PageBreak(),Paragraph(name,STYLES['Section']),Paragraph(f"Source: {_source(item['source'])}",STYLES['Small']),_table(metrics,[29*mm,61*mm,29*mm,61*mm]),Spacer(1,4*mm),_chart([(name,item['valid'],'timestamp','temperature_c')],f'Temperature profile - {name}',master_start,transfer_time)]
        if not bands.empty:s += [CondPageBreak(68*mm),Paragraph('Rendering band calculation',STYLES['Section']),_table(br,[27*mm,12*mm,49*mm,25*mm,24*mm,35*mm],6.8)]
    s += [PageBreak(),Paragraph('Interpretation notes',STYLES['Section']),Paragraph('All report charts use one shared master session timeline. The transfer marker indicates the derived smoker-to-hold boundary. Missing values are not interpolated across the transfer.',STYLES['BodyText'])]
    doc.build(s,onFirstPage=_footer,onLaterPages=_footer);return out.getvalue()
