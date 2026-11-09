from copy import deepcopy
from decimal import Decimal as D
import pytest
from app.domain import transition, equipment_transition, invariant, RuleError, QTY

@pytest.fixture
def card():
    return dict(id='card-1',card_no='RC-1',route_free=False,route_version_id=1,current_node_id=1,status='WAITING',original_qty=D(100),current_qty=D(100),scrap_qty=D(0),rework_qty=D(0),split_qty=D(0),defect_qty=D(0),returned_qty=D(0),transferred_qty=D(0),version=1,abnormal_counts={})

@pytest.fixture
def ctx():
    return dict(actor_id='actor',mode='operator',employee_no='OP1',operation={'id':1,'code':'PRINT','requires_equipment':True,'is_active':True},equipment={'id':'eq','state':'ENABLED','is_active':True},equipment_operations=[1],open_record=None,defect_code_ids=[1,2],qc_items=[],next_node_id=2)

def running(card,ctx):
    card['status']='IN_PROGRESS'
    ctx['open_record']={'id':'record','input_qty':D(100),'equipment_id':'eq','operation_id':1,'is_temporary':False}

def act(card,ctx,kind,**kw):
    return transition(card,dict(type=kind,version=card['version'],**kw),ctx)

def fails(code,fn):
    with pytest.raises(RuleError) as e: fn()
    assert e.value.code==code

def test_br01_balance_and_pure_function(card,ctx):
    before=deepcopy(card)
    result=act(card,ctx,'check-in')
    assert card==before and result['card'] is not card
    invariant(result['card'])
    card['current_qty']=D(99)
    fails('BR-01',lambda:act(card,ctx,'check-in'))

@pytest.mark.parametrize('bad',['NaN','Infinity','-1'])
def test_br01_invalid_quantity(card,ctx,bad):
    card['current_qty']=bad
    with pytest.raises(RuleError):act(card,ctx,'check-in')

def test_br02_input_is_current_not_client_qty(card,ctx):
    r=act(card,ctx,'check-in',qty=1)
    assert next(e for e in r['effects'] if e['type']=='open_record')['input_qty']==100

@pytest.mark.parametrize('output,defects',[(101,[]),(95,[]),(95,[{'defect_code_id':1,'qty':4}]),(95,[{'defect_code_id':3,'qty':5}]),(95,[{'defect_code_id':1,'qty':-5}]),(95,[{'defect_code_id':1,'qty':2},{'defect_code_id':1,'qty':3}])])
def test_br03_bad_allocations(card,ctx,output,defects):
    running(card,ctx)
    fails('BR-03',lambda:act(card,ctx,'check-out',output_qty=output,defects=defects))

def test_br03_defect_child_decimal(card,ctx):
    running(card,ctx)
    r=act(card,ctx,'check-out',output_qty='99.7',defects=[{'defect_code_id':1,'qty':'0.1'},{'defect_code_id':2,'qty':'0.2'}])
    assert r['card']['current_qty']==D('99.7')
    assert r['card']['defect_qty']==D('0.3')
    child=next(e['card'] for e in r['effects'] if e['type']=='child')
    assert child['status']=='DEFECT_HOLD' and child['current_qty']==D('0.3')
    invariant(child)

@pytest.mark.parametrize('qty',[0,-1,100,101])
def test_br04_invalid_split(card,ctx,qty):
    ctx['mode']='admin'
    fails('BR-04',lambda:act(card,ctx,'split',qty=qty))

def test_br04_split(card,ctx):
    ctx['mode']='admin'
    r=act(card,ctx,'split',qty=20)
    assert r['card']['current_qty']==80 and r['card']['split_qty']==20
    invariant(r['card'])

def test_br05_one_open_record(card,ctx):
    ctx['open_record']={'id':'existing'}
    fails('BR-05',lambda:act(card,ctx,'check-in'))

@pytest.mark.parametrize('next_node,status,node',[(2,'WAITING',2),(None,'COMPLETED',1)])
def test_br06_next_or_completed(card,ctx,next_node,status,node):
    running(card,ctx);ctx['next_node_id']=next_node
    r=act(card,ctx,'check-out',output_qty=100)
    assert r['card']['status']==status and r['card']['current_node_id']==node

def test_br07_qc_hold_retains_failed_node(card,ctx):
    running(card,ctx)
    ctx['qc_items']=[{'id':1,'lower_limit':10,'upper_limit':20}]
    r=act(card,ctx,'check-out',output_qty=100,qc=[{'qc_item_id':1,'value':21}])
    assert r['card']['status']=='QUALITY_HOLD' and r['card']['current_node_id']==1
    fails('FORBIDDEN',lambda:act(r['card'],ctx,'release',reason='pass'))
    fails('QC_REQUIRED',lambda:act(card,ctx,'check-out',output_qty=100))

@pytest.mark.parametrize('status',['QUALITY_HOLD','ABNORMAL'])
def test_br08_release_all_same_node(card,ctx,status):
    card['status']=status;ctx['mode']='admin'
    r=act(card,ctx,'release',reason='QA approved')
    assert r['card']['current_qty']==100 and r['card']['current_node_id']==1 and r['card']['status']=='WAITING'

def test_br09_temporary_never_advances_even_at_last_node(card,ctx):
    result=act(card,ctx,'temporary',reason='Extra measurement')
    assert next(e for e in result['effects'] if e['type']=='open_record')['is_temporary']
    running(card,ctx);ctx['open_record']['is_temporary']=True;ctx['next_node_id']=None
    result=act(card,ctx,'check-out',output_qty=100)
    assert result['card']['status']=='WAITING' and result['card']['current_node_id']==1

def test_br10_authorized_skip(card,ctx):
    fails('BR-10',lambda:act(card,ctx,'skip',target_node_id=2))
    ctx.update(authorized_by='supervisor',skip_authorized=True,target_node_valid=True)
    assert act(card,ctx,'skip',target_node_id=2)['card']['current_node_id']==2

def test_br11_route_immutable(card,ctx):
    ctx['mode']='admin'
    fails('BR-11',lambda:act(card,ctx,'edit',fields={'route_free':True}))
    card.update(route_free=True,route_version_id=None,current_node_id=None)
    running(card,ctx)
    assert act(card,ctx,'check-out',output_qty=100)['card']['status']=='WAITING'
    assert act(card,ctx,'check-out',output_qty=100,finish_route_free=True)['card']['status']=='COMPLETED'

def test_br12_cancel_state_reason_history(card,ctx):
    ctx['mode']='admin'
    fails('BR-24',lambda:act(card,ctx,'cancel',disposition='scrap'))
    running(card,ctx)
    fails('STATE',lambda:act(card,ctx,'cancel',disposition='scrap',reason='cancel'))

@pytest.mark.parametrize('disposition,bucket',[('scrap','scrap_qty'),('return','returned_qty'),('transfer','transferred_qty')])
def test_opt04_cancel_balances_all_dispositions(card,ctx,disposition,bucket):
    ctx['mode']='admin'
    r=act(card,ctx,'cancel',disposition=disposition,reason='Disposition confirmed')
    assert r['card']['status']=='CANCELLED' and r['card']['current_qty']==0 and r['card'][bucket]==100
    invariant(r['card'])
    assert any(e['type']=='movement' for e in r['effects'])


def test_br13_revoke_latest_snapshot_guard(card,ctx):
    before=deepcopy(card);ctx['mode']='admin';running(card,ctx)
    ctx['last_record']={'id':'last','before_card':before,'equipment_id':'eq'}
    r=act(card,ctx,'revoke',reason='wrong scan')
    assert r['card']['status']=='WAITING'
    assert next(e for e in r['effects'] if e['type']=='revoke_record')['record_id']=='last'
    ctx['processed_defects']=True
    fails('BR-13',lambda:act(card,ctx,'revoke',reason='wrong scan'))
    ctx['processed_defects']=False;ctx['subsequent_mutation']=True
    fails('BR-13',lambda:act(card,ctx,'revoke',reason='wrong scan'))


def test_br14_abnormal_requires_progress_and_reason(card,ctx):
    fails('STATE',lambda:act(card,ctx,'abnormal-end',reason='jam',disposition='keep'))
    running(card,ctx)
    fails('REASON_REQUIRED',lambda:act(card,ctx,'abnormal-end',disposition='keep'))


def test_br15_keep_same_node_increment(card,ctx):
    running(card,ctx)
    r=act(card,ctx,'abnormal-end',reason='jam',disposition='keep')['card']
    assert r['status']=='WAITING' and r['current_node_id']==1 and r['abnormal_counts']=={'1':1}


def test_br16_mark_requires_admin_resolution(card,ctx):
    running(card,ctx)
    c=act(card,ctx,'abnormal-end',reason='jam',disposition='mark')['card']
    assert c['status']=='ABNORMAL'
    fails('STATE',lambda:act(c,ctx,'check-in'))
    fails('FORBIDDEN',lambda:act(c,ctx,'release',reason='resolved'))


def test_br17_compatible_equipment(card,ctx):
    ctx['equipment_operations']=[2]
    fails('BR-17',lambda:act(card,ctx,'check-in'))

@pytest.mark.parametrize('state',['OFF','MAINTENANCE'])
def test_br18_enabled_equipment(card,ctx,state):
    ctx['equipment']['state']=state
    fails('BR-18',lambda:act(card,ctx,'check-in'))


def test_br19_auto_machine_reasons(card,ctx):
    assert next(e for e in act(card,ctx,'check-in')['effects'] if e['type']=='equipment')['reason_code']=='UP'
    running(card,ctx)
    for r in [act(card,ctx,'check-out',output_qty=100),act(card,ctx,'abnormal-end',disposition='keep',reason='stop')]:
        assert next(e for e in r['effects'] if e['type']=='equipment')['reason_code']=='US_4'

@pytest.mark.parametrize('code,valid',[('UP',True),('US_2',True),('US_5',True),('US_4',False),('DU_1',False)])
def test_br20_occupied_machine_restrictions(ctx,code,valid):
    ctx['occupied']=True
    reason={'code':code,'state':'MAINTENANCE' if code.startswith('DU') else 'ENABLED'}
    if valid: equipment_transition(ctx['equipment'],reason,ctx)
    else: fails('BR-20',lambda:equipment_transition(ctx['equipment'],reason,ctx))


def test_br21_history_close_and_open(ctx):
    r=equipment_transition(ctx['equipment'],{'code':'US_4','state':'ENABLED'},ctx)
    assert r['effects'][0]=={'type':'equipment_history','close_previous':True}


def test_br22_tablet_identity_required(card,ctx):
    ctx['employee_no']=''
    fails('BR-22',lambda:act(card,ctx,'check-in'))


def test_br23_every_transition_emits_audit_and_outbox(card,ctx):
    effects=act(card,ctx,'check-in')['effects']
    log=next(e for e in effects if e['type']=='audit')
    assert log['from_status']=='WAITING' and log['to_status']=='IN_PROGRESS'
    assert any(e['type']=='outbox' for e in effects)

@pytest.mark.parametrize('action',['backfill','revoke','release','cancel'])
def test_br24_admin_reason_mandatory(card,ctx,action):
    ctx['mode']='admin'
    fails('BR-24',lambda:act(card,ctx,action))


def test_release_optional_policy_is_explicit(card,ctx):
    card['status']='QUALITY_HOLD';ctx.update(mode='admin',release_reason_required=False)
    assert act(card,ctx,'release')['card']['status']=='WAITING'


def test_optimistic_version(card,ctx):
    fails('VERSION_CONFLICT',lambda:transition(card,{'type':'check-in','version':0},ctx))
