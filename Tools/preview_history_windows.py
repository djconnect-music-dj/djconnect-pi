"""Actual current/archive QML reading windows; only synthetic owner transports."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import Q_RETURN_ARG, QMetaObject, QObject, QTimer, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine, QQmlExpression
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from shiboken6 import delete

from djconnect_pi.app import DJConnectBackend
from djconnect_pi.brand_theme import apply_palette
from djconnect_pi.config import Config, save_config
from djconnect_pi.session_transport import SessionError

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(__doc__);parser.add_argument('--output',type=Path,required=True)
    output=parser.parse_args().output;output.mkdir(parents=True,exist_ok=True)
    warnings=[];qInstallMessageHandler(lambda kind,context,message:warnings.append(message))
    QQuickStyle.setStyle('Basic');app=QGuiApplication([]);apply_palette(app);captures=[]
    for width,height,profile in [(720,720,'pi-zero-2w-arm64'),(1200,1920,'pi5-arm64')]:
        config=output/f'fixture-{width}.json'
        save_config(config,Config(language='nl',release_profile=profile,log_file=str(output/'fixture.log'),
            paired=True,device_token='synthetic-not-a-credential',device_id='djconnect-raspberry-pi-fixture',
            ha_url='http://fixture.invalid',active_profile_id='profile-fixture',explicit_profile_id='profile-fixture',
            active_profile_type='personal',active_profile_privacy_mode='private',screen_timeout_seconds=0))
        with patch.object(QTimer,'singleShot'): backend=DJConnectBackend(config)
        for timer in backend.findChildren(QTimer): timer.stop()
        backend._executor.submit=Mock();backend._session_executor.submit=backend._executor.submit
        backend._question_executor.submit=backend._executor.submit;owner=backend.djSession
        caps={'capabilities':{'session_conversation_history':True},'contract_versions':{'session_history_entries':2}}
        active={'session_id':'synthetic-current-prefix','runtime_state':'active','broadcast':{'session':{'session_id':'synthetic-current-prefix'}}}
        owner.transport.active=Mock(return_value={'success':True,'session':active});owner.transport.capabilities=Mock(return_value=caps)
        owner._session=deepcopy(active);owner._caps=deepcopy(caps);owner._loaded=True
        engine=QQmlApplicationEngine();engine.rootContext().setContextProperty('djconnect',backend)
        engine.rootContext().setContextProperty('startWindowed',True);engine.load(str(ROOT/'src/djconnect_pi/qml/Main.qml'))
        assert engine.rootObjects(),warnings
        window=engine.rootObjects()[0];window.setWidth(width);window.setHeight(height);window.setProperty('splashVisible',False)
        for obj in window.findChildren(QObject):
            if 'Timer' in obj.metaObject().className() and obj.property('interval') in (3000,15000):
                expression=QQmlExpression(engine.rootContext(),obj,'running=false');expression.evaluate();assert not expression.hasError()
        screen=window.findChild(QObject,'djSessionScreen')
        scroll=window.findChild(QObject,'sessionHistoryScroll');flick=scroll.property('contentItem')
        def settle():
            executed=set()
            for _ in range(12):
                QTest.qWait(40)
                if owner.busy:
                    job=backend._executor.submit.call_args.args[0];assert job not in executed and len(executed)<3
                    executed.add(job);job()
                QTest.qWait(40)
                if not owner.busy and not screen.property('historyReadPending'): QTest.qWait(100);return
            raise AssertionError('bounded synthetic history read failed to settle')
        def capture(name):
            QTest.qWait(120);target=output/f'{name}-{width}x{height}.png'
            assert window.grabWindow().save(str(target));captures.append(target.name)
        archive_header = json.loads((ROOT/'tests/fixtures/session_core/retention-v2-touch.json').read_text())['tail_window']['session']
        archive_header.update(lifecycle_status='INTERRUPTED', read_only=True, _history_deadline=4_000_000_000)
        summary = archive_header['summary']
        summary['state'] = 'final'
        for name in ('tracks_observed', 'moments_published', 'mood'):
            summary[name]['state'] = 'final'
        owner._caps = {
            'capabilities': {**caps['capabilities'], 'session_history_summary': True},
            'contract_versions': {**caps['contract_versions'], 'session_history_summary': 1},
        }
        owner._archive_summary_binding = owner.transport.binding()
        owner._sessions = [archive_header]
        owner._history_loaded = True
        window.setProperty('activeScreen', 'history')
        for coverage, value, translation in (
            ('unknown', None, 'session_summary_unknown'), ('partial', None, 'session_summary_unknown'),
            ('withdrawn', None, 'session_summary_withdrawn'), ('complete', 'chill', 'mood_chill'),
        ):
            summary['mood'].update(coverage=coverage, value=value)
            owner.changed.emit()
            QTest.qWait(80)
            rows = window.findChild(QObject, 'sessionArchiveRows')
            expression = QQmlExpression(engine.rootContext(), rows,
                                        'itemAt(0).contentItem.children[1].children.find(child => '
                                        'child.objectName === "sessionArchiveSummary").itemAt(2).valueText')
            actual, _ = expression.evaluate()
            assert not expression.hasError() and actual == backend.t(translation), (width, coverage, actual, expression.error())
        window.setProperty('activeScreen', 'session')
        owner.invalidate()
        owner._session = deepcopy(active)
        owner._caps = deepcopy(caps)
        owner._loaded = True
        owner.changed.emit()
        for language in ['en','nl','de','fr','es']:
            settle();backend.setLanguage(language);backend._toast_visible=False;backend.toastChanged.emit();settle()
            for origin in ['current','archive']:
                owner.invalidate();owner._session=deepcopy(active);owner._caps=deepcopy(caps);owner._loaded=True
                sid=active['session_id'] if origin=='current' else 'synthetic-archive-prefix'
                seed=deepcopy(json.loads((ROOT/'tests/fixtures/session_core/retention-v2-touch.json').read_text())['tail_window'])
                seed['session'].update(session_id=sid,revision='synthetic-prefix-revision');seed['revision']='synthetic-prefix-revision'
                pages=[]
                for n in range(7):
                    page=deepcopy(seed);page.pop('window',None);page.pop('anchor_entry_id',None);page['entries']=[]
                    for i in range(20):
                        row=deepcopy(seed['entries'][0]);row.update(session_id=sid,entry_id=f'prefix-{n*20+i+1}',order=n*20+i+1,
                            retained_until='9999-01-01T00:00:00Z',text=f'Synthetic chronological contribution {n*20+i+1} '+('long retained text '*5))
                        page['entries'].append(row)
                    page['next_cursor']=f'prefix-page-{n+1}' if n<6 else None;pages.append(page)
                def response(binding,target,**kwargs):
                    assert target==sid
                    cursor=kwargs.get('cursor','');return deepcopy(pages[int(cursor.rsplit('-',1)[-1])] if cursor else pages[0])
                owner.transport.timeline=Mock(side_effect=response);owner.changed.emit()
                if origin=='current':
                    window.setProperty('activeScreen','session');settle()
                    QMetaObject.invokeMethod(window.findChild(QObject,'sessionReadCurrentHistory'),'clicked');settle()
                else:
                    window.setProperty('activeScreen','history');owner.closeSaved();settle();owner.openSaved(sid);settle()
                    owner.firstEntries();settle()
                assert owner._timeline['session']['session_id']==sid
                assert window.property('activeScreen')==('session-history' if origin=='current' else 'history')
                flick.setProperty('contentY',350);QTest.qWait(60)
                anchor=QMetaObject.invokeMethod(screen,'readingAnchor',Q_RETURN_ARG('QVariant'));offset=screen.property('readingOffset')
                extend=window.findChild(QObject,'sessionHistoryExtend');assert extend.property('visible') and extend.property('enabled')
                before=owner.transport.timeline.call_count;QMetaObject.invokeMethod(extend,'clicked');settle()
                assert len(owner._timeline['entries'])==40 and owner.state['session_id']==active['session_id']
                assert [c.kwargs.get('cursor','') for c in owner.transport.timeline.call_args_list[before:]]==['','prefix-page-1']
                restored=QMetaObject.invokeMethod(screen,'readingAnchor',Q_RETURN_ARG('QVariant'))
                assert restored==anchor and abs(screen.property('readingOffset')-offset)<2,(width,origin,anchor,restored,offset,screen.property('readingOffset'))
                capture(f'history-prefix-{origin}-two-pages-{language}')
                pages[0]['entries'][0]['text']='Synthetic freshly replaced first-page text'
                before=owner.transport.timeline.call_count
                QMetaObject.invokeMethod(window.findChild(QObject,'sessionHistoryRevalidationTimer'),'triggered');settle()
                assert owner._timeline['entries'][0]['text']=='Synthetic freshly replaced first-page text'
                assert [c.kwargs.get('cursor','') for c in owner.transport.timeline.call_args_list[before:]]==['','prefix-page-1']
                capture(f'history-prefix-{origin}-fresh-revalidation-{language}')
                for _ in range(3): QMetaObject.invokeMethod(extend,'clicked');settle()
                assert len(owner._timeline['entries'])==100 and not extend.property('visible')
                assert window.findChild(QObject,'sessionHistoryWindowLimit').property('visible')
                flick.setProperty('contentY',max(0,flick.property('contentHeight')-flick.property('height')));QTest.qWait(60)
                capture(f'history-prefix-{origin}-bounded-window-{language}')
                QMetaObject.invokeMethod(window.findChild(QObject,'sessionHistoryNext'),'clicked');settle()
                assert owner._timeline['entries'][0]['entry_id']=='prefix-101'
                before=owner.transport.timeline.call_count
                QMetaObject.invokeMethod(window.findChild(QObject,'sessionHistoryPrevious'),'clicked');settle()
                assert owner.transport.timeline.call_count-before==5 and len(owner._timeline['entries'])==100
                capture(f'history-prefix-{origin}-fresh-previous-{language}')
                owner.transport.timeline=Mock(side_effect=SessionError('denied'))
                QMetaObject.invokeMethod(window.findChild(QObject,'sessionHistoryRevalidationTimer'),'triggered');settle()
                assert not owner.state['timeline'] and not owner.state['retained_flow']
                capture(f'history-prefix-{origin}-denied-{language}')
        backend.shutdown();delete(window);delete(engine)
    errors=[m for m in warnings if any(w in m for w in ['ReferenceError','TypeError','Error:','Cannot assign','is not a type','failed to load','Binding loop detected'])]
    (output/'readback.json').write_text(json.dumps({'captures':captures,'qml_errors':errors,'proof':'Synthetic owner transports with actual controller/QML. No ARM/install/live/physical acceptance.'},indent=2))
    assert not errors,errors
    print(f'{len(captures)} actual history-prefix QML views; no errors/binding loops')


if __name__=='__main__': main()
