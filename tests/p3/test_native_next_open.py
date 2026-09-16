"""Real pinned engine diagnostics, separately selected and never qualification."""
from decimal import Decimal
from datetime import date,timedelta
import json
import os
from pathlib import Path
import subprocess

import pytest

from packages.alpha_lifecycle.executable_reference import synthetic_next_open_accounting


@pytest.mark.host_coupled
@pytest.mark.skipif(not os.environ.get('P3_NATIVE_TEST_PYTHON'),reason='pinned offline native test Python required')
@pytest.mark.parametrize('targets,opens',[
    ((1,0),('100','120')),
    ((1,0,1,0),('100','80','80','90')),
    ((0,1,1,0),('100','105','90','110')),
    ((0,0),('100','90')),
])
def test_real_native_next_open_matches_reference_and_has_no_early_fill(targets,opens):
    start=1756684800000000000
    boundaries=tuple(start+i*86400000000000 for i in range(len(targets)))
    days=tuple((date(2025,8,31)+timedelta(days=i)).isoformat() for i in range(len(targets)))
    inputs=[dict(target=target,open=opening,boundary_ns=boundary,source_day=day)
        for target,opening,boundary,day in zip(targets,opens,boundaries,days,strict=True)]
    script=Path(__file__).resolve().parents[2]/'engines/nautilus/runtime_v1/p3_next_open.py'
    expected=synthetic_next_open_accounting(targets=targets,opens=tuple(map(Decimal,opens)),
        boundary_times_ns=boundaries,source_days=days,price_increment=Decimal('.01'),
        size_increment=Decimal('.00001'),quote_quantum=Decimal('.01'),minimum_notional=Decimal('10'))
    observed=[]
    for _ in range(3):
        result=subprocess.run([os.environ['P3_NATIVE_TEST_PYTHON'],'-I','-B',str(script)],
            input=json.dumps(inputs,sort_keys=True,separators=(',',':')).encode(),
            capture_output=True,check=True,timeout=30,env={})
        rows=json.loads(result.stdout);observed.append(rows)
        assert len(rows)==len(expected)
        for actual,reference in zip(rows,expected,strict=True):
            for key in reference:
                if key in {'cash_after','fee_quote'}:
                    assert abs(Decimal(actual[key] or '0')-Decimal(reference[key] or '0'))<=Decimal('.01')
                else:assert actual[key]==reference[key],(key,actual,reference)
        assert all(row['event_time_ns'] in {boundary+1 for boundary in boundaries}
            for row in rows if row['kind']=='FILL')
        assert rows[-1]['position_after']=='0'
    assert observed[0]==observed[1]==observed[2]


@pytest.mark.host_coupled
@pytest.mark.skipif(not os.environ.get('P3_NATIVE_TEST_PYTHON'),reason='pinned offline native test Python required')
def test_native_request_rejects_invalid_boundaries_and_policy():
    script=Path(__file__).resolve().parents[2]/'engines/nautilus/runtime_v1/p3_next_open.py'
    probe='''
import importlib.util
import sys
spec=importlib.util.spec_from_file_location('p3_next_open',sys.argv[1])
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
start=1756684800000000000
steps=[dict(target=1,open='100',boundary_ns=start,source_day='2025-08-31'),
       dict(target=0,open='120',boundary_ns=start+86400000000000,source_day='2025-09-01')]
for key,value in [('target',True),('target',2),('open','NaN'),('open','0'),
                  ('open','1e999999'),('boundary_ns',start+1),('source_day','2025-09-03')]:
    invalid=[dict(row) for row in steps]
    invalid[1][key]=value
    try:module.run_next_open(invalid)
    except ValueError:pass
    else:raise AssertionError((key,value))
invalid=[dict(row) for row in steps];invalid[-1]['target']=1
try:module.run_next_open(invalid)
except ValueError:pass
else:raise AssertionError('terminal long accepted')
'''
    subprocess.run([os.environ['P3_NATIVE_TEST_PYTHON'],'-I','-B','-c',probe,str(script)],
        capture_output=True,check=True,timeout=30,env={})
    malformed=b'[{"target":0,"target":1}]'
    result=subprocess.run([os.environ['P3_NATIVE_TEST_PYTHON'],'-I','-B',str(script)],
        input=malformed,capture_output=True,timeout=30,env={})
    assert result.returncode!=0 and not result.stdout
    assert b'canonical input required' in result.stderr
