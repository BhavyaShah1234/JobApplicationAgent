"""Host launcher preserves display isolation and refuses unrelated containers."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def launcher():
    path=Path(__file__).resolve().parents[2]/'scripts/desktop_background.py'
    spec=importlib.util.spec_from_file_location('desktop_background',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_child_display_environment_does_not_reuse_personal_desktop_session(launcher,tmp_path):
    original={'DISPLAY':':0','WAYLAND_DISPLAY':'wayland-0','DBUS_SESSION_BUS_ADDRESS':'personal',
              'DBUS_SESSION_BUS_PID':'123','PATH':'test-path'}
    env=launcher.display_environment(tmp_path,original)
    assert env['DISPLAY']=='127.0.0.1:102' and env['PATH']=='test-path'
    assert env['XAUTHORITY']==str(tmp_path/'Xauthority')
    assert not any(key in env for key in ('WAYLAND_DISPLAY','DBUS_SESSION_BUS_ADDRESS','DBUS_SESSION_BUS_PID'))
    assert original['DISPLAY']==':0'
    assert (tmp_path/'runtime').stat().st_mode&0o777==0o700


@pytest.mark.parametrize('change',['none','id','uid','root','network','writable'])
def test_only_the_recorded_gui_container_can_be_reused(launcher,tmp_path,change):
    container={'Id':'owned','HostConfig':{'NetworkMode':'host'},'Config':{'User':f'{os.getuid()}:{os.getgid()}'},
               'Mounts':[{'Destination':'/display','Source':str(tmp_path),'RW':False}]}
    record={'id':'owned','project':str(launcher.PROJECT)}
    if change=='id':record['id']='other'
    if change=='uid':container['Config']['User']='0:0'
    if change=='root':container['Mounts'][0]['Source']='/personal'
    if change=='network':container['HostConfig']['NetworkMode']='bridge'
    if change=='writable':container['Mounts'][0]['RW']=True
    assert launcher.owned(container,record,tmp_path) is (change=='none')


def test_a_running_owned_display_is_checked_without_restarting_it(launcher,tmp_path,monkeypatch):
    directory=tmp_path/'display';directory.mkdir();(directory/'Xauthority').write_text('test cookie');(directory/'Xauthority').chmod(0o600)
    (directory/'owner.json').write_text(json.dumps({'id':'owned','project':str(launcher.PROJECT)}))
    container={'Id':'owned','State':{'Running':True},'HostConfig':{'NetworkMode':'host'},
               'Config':{'User':f'{os.getuid()}:{os.getgid()}'},
               'Mounts':[{'Destination':'/display','Source':str(directory),'RW':False}]}
    calls=[]
    def run(args,**kwargs):
        calls.append(args)
        assert args[:2] in (['docker','inspect'],['xdpyinfo'])
        return SimpleNamespace(returncode=0,stdout=json.dumps([container]))
    monkeypatch.setattr(launcher.subprocess,'run',run)
    monkeypatch.setattr(launcher.subprocess,'check_output',lambda *a,**k:pytest.fail('No container launch'))
    class HTTP:
        def __init__(self,host,port,timeout):assert host=='127.0.0.1' and port==6083
        def request(self,*a):pass
        def getresponse(self):return SimpleNamespace(status=200)
        def close(self):pass
    monkeypatch.setattr(launcher.http.client,'HTTPConnection',HTTP)
    assert launcher.ensure_display(directory)['DISPLAY']=='127.0.0.1:102'
    assert calls==[['docker','inspect',launcher.NAME],['xdpyinfo']]
