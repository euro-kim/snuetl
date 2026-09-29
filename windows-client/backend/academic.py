"""Persistent, account-scoped API snapshots and revision-based academic events."""
from __future__ import annotations
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin
from snuetl.canvas_api import CanvasClient, live_data, _courses
from snuetl.errors import AuthenticationRequired, DiscoveryError

COMMANDS = ('announcements', 'submissions', 'upcoming', 'missing', 'feedback', 'grades', 'calendar', 'discussions')
CATEGORIES = {'announcements': 'announcements', 'submissions': 'assignments', 'feedback': 'grades', 'grades': 'grades', 'discussions': 'discussions', 'calendar': 'assignments'}

def stamp():
    return datetime.now(UTC).isoformat()

def revision(row):
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def selected(preferences, category, course):
    categories = preferences.get('categories', [])
    overrides = preferences.get('courses', {})
    return category in overrides.get(str(course), categories)

def reconcile(state, datasets, preferences, now=None):
    """Failed datasets must be omitted: never rebaseline or delete their history."""
    now = now or datetime.now(UTC)
    seen = state.setdefault('seen', {})
    baseline = state.setdefault('baseline', [])
    history = state.setdefault('events', [])
    emitted = []
    for kind, rows in datasets.items():
        category = CATEGORIES.get(kind)
        if category is None:
            continue
        course_ids = {str(c['id']) for c in state.get('courses', [])} | {str(row.get('course_id')) for row in rows}
        for row in rows:
            initial = f"{kind}:{row.get('course_id')}" not in baseline
            item_id = next((str(row[k]) for k in ('announcement_id','assignment_id','topic_id','item_id','event_id') if row.get(k) is not None), str(row.get('url') or row.get('course_id') or ''))
            key = f"{kind}:{row.get('course_id')}:{item_id}"
            rev = revision(row)
            previous = seen.get(key)
            if previous != rev and not initial and selected(preferences, category, row.get('course_id')):
                event = dict(id=revision([key,rev]), category=category, course_id=str(row.get('course_id') or ''),
                             course_name=row.get('course_name') or next((c.get('name') for c in state.get('courses', []) if str(c['id']) == str(row.get('course_id'))), '') or '',
                             title=row.get('title') or row.get('course_name') or 'Course grade updated',
                             detail=(row.get('summary') or '') if kind == 'announcements' else ('New' if previous is None else 'Updated') + ' · ' + kind,
                             url=row.get('url'), time=now.isoformat())
                emitted.append(event)
            seen[key] = rev
        baseline.extend(f'{kind}:{course}' for course in course_ids if f'{kind}:{course}' not in baseline)
    # Derive reminders from current submission state; no stale queued timer survives
    # a deadline edit, submission, removal or endpoint failure.
    if 'submissions' in datasets:
        reminders = state.setdefault('reminders', {})
        active = {}
        for row in datasets['submissions']:
            reminded = False
            if not selected(preferences, 'reminders', row.get('course_id')) or row.get('submitted_at') or row.get('excused') or row.get('workflow_state') in ('submitted','graded','pending_review'):
                continue
            try:
                due = datetime.fromisoformat(row['due_at'].replace('Z','+00:00'))
                if due.tzinfo is None: continue
            except (KeyError, TypeError, ValueError):
                continue
            for hours in sorted(preferences.get('reminder_hours', [24,1])):
                if not isinstance(hours, (int,float)) or not 0 < hours <= 720: continue
                key = f"{row.get('course_id')}:{row.get('assignment_id')}:{due.isoformat()}:{hours}"
                active[key] = reminders.get(key, False)
                if now < due and now >= due - timedelta(hours=hours) and not active[key]:
                    active[key] = True
                    if reminded: continue
                    reminded = True
                    emitted.append(dict(id=revision(key), category='reminders', course_id=str(row.get('course_id') or ''), course_name=row.get('course_name') or next((c.get('name') for c in state.get('courses', []) if str(c['id']) == str(row.get('course_id'))), '') or '', title=row.get('title') or 'Assignment due', detail=f"Due {due.astimezone():%b %d, %H:%M}", url=row.get('url'), time=now.isoformat()))
        state['reminders'] = active
    known = {e['id'] for e in history}
    emitted = [e for e in emitted if e['id'] not in known]
    state['events'] = (list(reversed(emitted)) + history)[:500]
    return emitted

def snapshot(root: Path, config, token, *, refresh=False, preferences=None):
    path = root / 'academic.json'
    account = f'{token.origin}|{token.user_id}'
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
        if state.get('account') != account: state = {}
    except (OSError, ValueError):
        state = {}
    if not refresh:
        return {**state, 'new_events': []}
    state['account'] = account
    datasets = {}
    errors = []
    try:
        with CanvasClient(config, token) as client:
            state['courses'] = [{'id':str(c['id']), 'name':c.get('name') or c.get('course_code'), 'url':c.get('html_url') or f"{token.origin}/courses/{c['id']}"} for c in _courses(client,config,None)]
    except AuthenticationRequired:
        raise
    except DiscoveryError as exc:
        errors.append(str(exc))
    for command in COMMANDS:
        try:
            datasets[command] = live_data(config, command, token=token)
            for row in datasets[command]:
                if row.get("url"): row["url"] = urljoin(token.origin + "/", str(row["url"]))
        except AuthenticationRequired:
            raise
        except DiscoveryError as exc:
            errors.append(f'{command}: {exc}')
    emitted = reconcile(state,datasets, preferences or {})
    state.setdefault('datasets',{}).update(datasets)
    state['errors'] = sorted(set(errors))
    state['generated_at'] = stamp()
    root.mkdir(parents=True,exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8')
    os.replace(temporary,path)
    return {**state, 'new_events': emitted}
