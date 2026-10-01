"""Validation-only CDP harness for the isolated headless Edge session."""
import base64
import json
import time
import uuid
from pathlib import Path

import requests
import websocket

OUT = Path(__file__).parent
target = next(t for t in requests.get('http://localhost:9222/json').json() if t['url'].startswith('http://localhost:3005'))
ws = websocket.create_connection(target['webSocketDebuggerUrl'], timeout=130)
sequence = 0
events = []


def command(method, params=None):
    global sequence
    sequence += 1
    request_id = sequence
    ws.send(json.dumps(dict(id=request_id, method=method, params=params or {})))
    while True:
        result = json.loads(ws.recv())
        if result.get('id') == request_id:
            assert 'error' not in result, result
            return result.get('result', {})
        if result.get('method') == 'Runtime.exceptionThrown':
            events.append(result)
        if result.get('method') == 'Page.javascriptDialogOpening':
            sequence += 1
            ws.send(json.dumps(dict(id=sequence, method='Page.handleJavaScriptDialog', params={'accept': True})))


def js(expression):
    result = command('Runtime.evaluate', dict(expression=expression, awaitPromise=True, returnByValue=True))
    assert 'exceptionDetails' not in result, result
    return result['result'].get('value')


def wait(expression):
    deadline = time.monotonic() + 110
    while time.monotonic() < deadline:
        if js(expression):
            return
        time.sleep(.25)
    raise AssertionError(expression)


def screenshot(name):
    data = command('Page.captureScreenshot', {'format': 'png', 'captureBeyondViewport': True})
    (OUT / name).write_bytes(base64.b64decode(data['data']))


report = {}
trip = 'BROWSER-VALIDATION-' + uuid.uuid4().hex[:8]
try:
    command('Runtime.enable')
    command('Page.enable')
    command('Emulation.setDeviceMetricsOverride', dict(width=1440, height=1100, deviceScaleFactor=1, mobile=False))
    command('Page.navigate', {'url': 'http://localhost:3005'})
    wait("document.querySelector('#mcp-form') && document.querySelector('#itinerary-list').textContent.length > 20")
    report['page_title'] = js('document.title')
    report['crud_controls'] = js("['open-create-button','itinerary-form','day-filter','ai-review-form','mcp-form','rag-form'].every(id=>!!document.getElementById(id))")
    js("document.querySelector('#open-create-button').click()")
    fields = {'trip-reference': trip, 'day': '99', 'start-time': '09:00', 'end-time': '10:00',
              'activity-id': '1', 'destination-id': '1', 'estimated-cost': '12.50', 'notes': trip}
    for name, value in fields.items():
        js(f"document.getElementById({json.dumps(name)}).value={json.dumps(value)}")
    js("document.querySelector('#itinerary-form').requestSubmit()")
    wait("document.querySelector('#form-panel').hidden")
    wait("!!document.querySelector('#day-filter option[value=\"99\"]')")
    js("document.querySelector('#day-filter').value='99'; document.querySelector('#day-filter').dispatchEvent(new Event('change'))")
    wait(f"document.querySelector('#itinerary-list').textContent.includes({json.dumps(trip)})")
    card = f"[...document.querySelectorAll('.itinerary-card')].find(c=>c.textContent.includes({json.dumps(trip)}))"
    js(card + ".querySelector('button').click()")
    js("document.querySelector('#estimated-cost').value='15'; document.querySelector('#itinerary-form').requestSubmit()")
    wait("document.querySelector('#form-panel').hidden")
    wait(card + ".textContent.includes('$15.00')")
    js(card + ".querySelector('.button--danger').click()")
    wait(f"!document.querySelector('#itinerary-list').textContent.includes({json.dumps(trip)})")
    report['browser_create_edit_delete'] = 'PASS'
    js("document.querySelector('#day-filter').value='2'; document.querySelector('#day-filter').dispatchEvent(new Event('change'))")
    wait("!document.querySelector('#loading').hidden === false")
    report['day_filter'] = js("document.querySelector('#itinerary-list').textContent")
    js("document.querySelector('#ai-review-day').value='1'; document.querySelector('#ai-review-prompt').value='Review Day 1 briefly in two sentences.'; document.querySelector('#ai-review-form').requestSubmit()")
    wait("!document.querySelector('#ai-review-results').hidden || !document.querySelector('#ai-review-error').hidden")
    assert js("document.querySelector('#ai-review-error').hidden")
    report['ai_review_display'] = js("document.querySelector('#ai-review-results').textContent")
    js("document.querySelector('#mcp-form [name=trip_reference]').value='TRIP-1001'; document.querySelector('#mcp-form [name=day]').value='2'; document.querySelector('#mcp-form').requestSubmit()")
    wait("!document.querySelector('#mcp-form button').disabled")
    report['mcp_display'] = js("document.querySelector('#mcp-result').textContent")
    mcp = json.loads(report['mcp_display'])
    assert mcp['count'] == 3 and mcp['day'] == 2
    js("document.querySelector('#rag-form [name=trip_reference]').value='TRIP-1001'; document.querySelector('#rag-form [name=query]').value='What is the estimated itinerary cost?'; document.querySelector('#rag-form').requestSubmit()")
    wait("!document.querySelector('#rag-form button').disabled")
    report['rag_display'] = js("document.querySelector('#rag-result').textContent")
    assert '199.50' in report['rag_display'] and 'Confidence: High' in report['rag_display']
    assert 'itinerary_6' in report['rag_display'] and 'itinerary-db:/itinerary-items' in report['rag_display']
    js("document.querySelector('#rag-title').scrollIntoView()")
    screenshot('browser-grounded-answer.png')
    js("document.querySelector('#rag-form [name=query]').value=\"What is tomorrow's weather?\"; document.querySelector('#rag-form').requestSubmit()")
    wait("!document.querySelector('#rag-form button').disabled")
    report['insufficient_display'] = js("document.querySelector('#rag-result').textContent")
    assert 'Insufficient' in report['insufficient_display']
    screenshot('browser-insufficient.png')
    report['javascript_exceptions'] = events
    assert not events
    report['overall'] = 'PASS'
finally:
    # Only remove this harness's uniquely identified record if an earlier UI check failed.
    for item in requests.get('http://localhost:6005/itinerary-items', timeout=10).json():
        if item['trip_reference'] == trip:
            requests.delete(f"http://localhost:3005/api/itinerary/{item['itinerary_item_id']}", timeout=10).raise_for_status()
    (OUT / 'browser-results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    ws.close()
