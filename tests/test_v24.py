import unittest
from datetime import datetime,timedelta
import pandas as pd
import brisket_engine as engine
import pit_engine as pit
class TestV24(unittest.TestCase):
 def test_classification(self):
  d=pd.DataFrame({'timestamp':[datetime(2026,1,1)+timedelta(seconds=i) for i in range(100)],'Meat Probe':[20+i*.5 for i in range(100)],'Grate Probe':[120+(-1)**i*2 for i in range(100)],'Smoker Controller':[120]*100,'Target Temp':[120]*100})
  c=pit.classify_columns(d,'timestamp').set_index('Column')['Suggested role'].to_dict()
  self.assertEqual(c['Meat Probe'],'Meat temperature'); self.assertEqual(c['Grate Probe'],'Grate temperature'); self.assertEqual(c['Smoker Controller'],'Controller temperature'); self.assertEqual(c['Target Temp'],'Target temperature')
 def test_meat_demo(self):
  d=engine.demo_data(); t,p=engine.detect_columns(d); v,r=engine.prepare(d,t,p[0]); det=engine.classify_session(v); self.assertEqual(det.session_type,'Cook + Hold')
if __name__=='__main__': unittest.main()
