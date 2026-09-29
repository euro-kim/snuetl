from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2] / 'backend'))
from academic import reconcile
import snuetl_windows_backend as backend

PREFS={'categories':['announcements','assignments','reminders','grades','discussions']}

def test_baseline_revisions_and_restart():
    import json
    state={}
    row={'announcement_id':1,'course_id':7,'title':'Welcome','summary':'One'}
    assert reconcile(state,{'announcements':[row]},PREFS)==[]
    assert reconcile(state,{'announcements':[row]},PREFS)==[]
    row={**row,'summary':'Two'}
    assert len(reconcile(state,{'announcements':[row]},PREFS))==1
    restored=json.loads(json.dumps(state))
    assert reconcile(restored,{'announcements':[row]},PREFS)==[]
    assert len(restored['events'])==1

def test_no_opt_in_and_course_override():
    state={}; row={'announcement_id':1,'course_id':7,'title':'A'}
    reconcile(state,{'announcements':[row]}, {})
    assert not reconcile(state,{'announcements':[{**row,'title':'B'}]}, {})
    assert not reconcile(state,{'announcements':[{**row,'title':'C'}]}, {'categories':['announcements'],'courses':{'7':[]}})

def test_announcement_event_has_course_and_message():
    state={'courses':[{'id':'7','name':'Computer Science'}]}
    reconcile(state,{'announcements':[]},PREFS)
    row={'announcement_id':1,'course_id':7,'title':'Room change','summary':'Meet in room 301.', 'url':'https://myetl.snu.ac.kr/courses/7/discussion_topics/1'}
    event, = reconcile(state,{'announcements':[row]},PREFS)
    assert event['course_name']=='Computer Science'
    assert event['detail']=='Meet in room 301.'
    assert event['url']==row['url']

def test_deadline_edits_submissions_and_dedup():
    now=datetime(2026,9,27,tzinfo=UTC)
    row={'assignment_id':3,'course_id':7,'title':'Paper','due_at':(now+timedelta(minutes=45)).isoformat()}
    state={}
    events=reconcile(state,{'submissions':[row]},PREFS,now)
    assert len(events)==1 and events[0]['category']=='reminders'
    assert not reconcile(state,{'submissions':[row]},PREFS,now)
    submitted={**row,'submitted_at':now.isoformat()}
    events=reconcile(state,{'submissions':[submitted]},PREFS,now)
    assert all(e['category']!='reminders' for e in events)
    assert state['reminders']=={}
    moved={**row,'due_at':(now+timedelta(days=2)).isoformat()}
    events=reconcile(state,{'submissions':[moved]},PREFS,now)
    assert all(e['category']!='reminders' for e in events)

def test_failed_endpoint_does_not_reset_baseline():
    state={}; row={'announcement_id':1,'course_id':7,'title':'A'}
    reconcile(state,{'announcements':[row]},PREFS)
    reconcile(state,{},PREFS)
    assert len(reconcile(state,{'announcements':[{**row,'title':'B'}]},PREFS))==1

def test_missing_addon_is_capability_error(tmp_path,monkeypatch):
    import pytest
    monkeypatch.setenv('SNUETL_WINDOWS_DATA_DIR',str(tmp_path))
    assert backend.capabilities()['automatic_signin'] is False
    with pytest.raises(backend.BackendError) as error: backend.auth_auto()
    assert error.value.code=='OPTIONAL_COMPONENT_MISSING'


def test_hydration_does_not_read_entire_file_into_memory(tmp_path, monkeypatch):
    from snuetl_windows_backend import hydrate
    import json, hashlib
    monkeypatch.setenv('SNUETL_WINDOWS_DATA_DIR', str(tmp_path))
    content = tmp_path / 'large.bin'
    with content.open('wb') as stream:
        for _ in range(16): stream.write(b'x' * 1024 * 1024)
    row = {'kind':'file','course_id':'1','source_id':'2','revision':'3','cache_path':str(content),'size':content.stat().st_size}
    (tmp_path/'manifest.json').write_text(json.dumps({'schema_version':1,'entries':[row]}),encoding='utf8')
    monkeypatch.setattr(Path,'read_bytes',lambda self: (_ for _ in ()).throw(AssertionError('unbounded read_bytes')))
    result = hydrate({'v':1,'kind':'file','course_id':'1','source_id':'2','revision':'3'})
    assert result['size'] == 16*1024*1024
    with content.open('rb') as stream: assert result['sha256']==hashlib.file_digest(stream,'sha256').hexdigest()


def test_new_course_gets_silent_baseline_but_future_announcement_notifies():
    state={'courses':[{'id':'7'}]}
    reconcile(state,{'announcements':[]},PREFS)
    first={'announcement_id':1,'course_id':'7','title':'New announcement'}
    assert len(reconcile(state,{'announcements':[first]},PREFS))==1
    state['courses'].append({'id':'8'})
    old={'announcement_id':2,'course_id':'8','title':'Older course announcement'}
    assert not reconcile(state,{'announcements':[first,old]},PREFS)


def test_interactive_download_does_not_wait_for_refresh(monkeypatch):
    import io, json, threading
    started, downloaded = threading.Event(), threading.Event()
    order=[]
    def dispatch(method, params):
        if method=='manifest.refresh':
            started.set()
            assert downloaded.wait(2), 'download starved behind refresh'
            order.append('refresh')
        if method=='content.hydrate':
            assert started.wait(2)
            order.append('download')
            downloaded.set()
        return {}
    monkeypatch.setattr(backend,'dispatch',dispatch)
    source=io.StringIO('\n'.join(json.dumps({'id':i,'method':m}) for i,m in enumerate(['manifest.refresh','content.hydrate','shutdown'])))
    backend.serve(source,io.StringIO())
    assert order==['download','refresh']
