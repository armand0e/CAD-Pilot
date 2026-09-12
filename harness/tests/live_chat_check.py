"""Opt-in real provider/WS/persistence smoke on the isolated 7802 service only.

Creates an empty test CAD session, asks a non-modeling question, then reopens its
project. Never trains, edits geometry, or attaches to an existing user session.
"""
import asyncio
import argparse
import json
from pathlib import Path
import tempfile
import time

import httpx
import websockets


async def main(base):
    if base not in ('http://127.0.0.1:7802','http://127.0.0.1:7803'):
        raise ValueError('This test only targets isolated local test ports')
    wsbase=base.replace('http:','ws:')
    output=Path(tempfile.mkdtemp(prefix='chat-live-',dir=Path(__file__).resolve().parents[2]/'runs/harness-checks'))
    sid=None
    async with httpx.AsyncClient(timeout=30) as client:
        async with asyncio.timeout(15):
            while True:
                try:
                    (await client.get(base+'/api/status')).raise_for_status()
                    break
                except httpx.ConnectError:
                    await asyncio.sleep(.25)
        apps=(await client.get(base+'/api/apps')).json()['apps']
        app=next(a for a in apps if 'openscad' in a['name'].lower())
        response=await client.post(base+'/api/sessions',json={'app_id':app['id'],'engine':'visual'})
        response.raise_for_status();session=response.json();sid=session['id'];project_id=session['project_id']
        try:
            async with websockets.connect(f'{wsbase}/ws/agent/{sid}',max_size=8*1024*1024) as socket:
                snapshot=json.loads(await socket.recv());assert snapshot['chat_protocol']==1
                await socket.send(json.dumps({'t':'web_setting','enabled':False}))
                preference=json.loads(await socket.recv());assert preference['t']=='web_setting' and not preference['enabled']
                await socket.send(json.dumps({'t':'start','mode':'auto','task':
                    'Answer this question in one short paragraph: what is the difference between a sketch and a solid in CAD? Do not perform any modeling, use tools, or change the document.'}))
                events=[];start=time.monotonic()
                async with asyncio.timeout(120):
                    while True:
                        event=json.loads(await socket.recv());events.append(event)
                        if event['t'] in ('answer_start','answer_done','done','error','pause'):
                            print(json.dumps(event),flush=True)
                        if event['t']=='control' and not event['locked']:break
                        if event['t']=='pause' and event['paused']:
                            await socket.send(json.dumps({'t':'stop'}))
                deltas=[e for e in events if e['t']=='answer_delta']
                assert len(deltas)>1,events
                assert not any(e['t'] in ('intent','action','artifact','research_start') for e in events),events
                assert any(e['t']=='answer_done' and e['status']=='completed' for e in events),events
                answer=''.join(e['text'] for e in deltas)
            activity=(await client.get(base+f'/api/sessions/{sid}/activity')).json()
            await client.delete(base+f'/api/sessions/{sid}');sid=None
            response=await client.post(base+'/api/sessions',json={'app_id':app['id'],'project_id':project_id,'engine':'visual'})
            response.raise_for_status();sid=response.json()['id']
            async with websockets.connect(f'{wsbase}/ws/agent/{sid}',max_size=8*1024*1024) as socket:
                reopened=json.loads(await socket.recv())
                assert reopened['web_enabled'] is False
                assert ''.join(e['text'] for e in reopened['transcript'] if e['t']=='answer_delta')==answer
                assert [e['event_id'] for e in reopened['transcript']]==[e['event_id'] for e in activity['transcript']]
            report={'status':'passed','model':'configured stock planner','stream_deltas':len(deltas),'answer':answer,
                    'reasoning_fragments':sum(e['t']=='thinking_delta' for e in events),
                    'reopened_with_history':True,'web_disabled_persisted':True,'cad_operations':0,
                    'elapsed_s':round(time.monotonic()-start,3),'project_id':project_id}
            (output/'activity.json').write_text(json.dumps(activity,indent=2))
            (output/'report.json').write_text(json.dumps(report,indent=2))
            print(json.dumps({'output':str(output),**report},indent=2),flush=True)
        finally:
            if sid:await client.delete(base+f'/api/sessions/{sid}')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base',default='http://127.0.0.1:7802')
    asyncio.run(main(parser.parse_args().base))
