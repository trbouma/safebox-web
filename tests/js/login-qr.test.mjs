import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const source = readFileSync(new URL('../../app/static/login-qr.js', import.meta.url), 'utf8')
  .replace('import QrScanner from "/static/qr-scanner.min.js";', '');
function setup({supported=true, denied=false, pending=false}={}) {
  const elements={}, scanners=[], listeners={}, timers=[];
  let submitted=0, resolveStart;
  for (const key of ['video','start','stop','status','payload','form']) {
    elements[key]={value:'', handlers:{}, addEventListener(event, handler){this.handlers[event]=handler;},
      requestSubmit(){submitted++;}};
  }
  class Scanner {
    constructor(video, callback){this.callback=callback;this.destroyed=0;scanners.push(this);}
    async start(){if(denied) throw Error('SECRET'); if(pending) await new Promise(r=>resolveStart=r);}
    destroy(){this.destroyed++;}
  }
  const document={hidden:false,
    querySelector(){return {querySelector(s){return elements[s.slice('[data-login-qr-'.length,-1)];}};},
    addEventListener(e,h){listeners[e]=h;}};
  vm.runInNewContext(source,{QrScanner:Scanner, document,
    window:{isSecureContext:true,addEventListener(e,h){listeners[e]=h;}},
    navigator:{mediaDevices:supported?{getUserMedia(){}}:undefined},
    setTimeout(fn){timers.push(fn);return timers.length;},clearTimeout(){}});
  return {elements,scanners,listeners,timers,document,submitted:()=>submitted,
    click:key=>elements[key].handlers.click(),complete:()=>resolveStart()};
}
test('camera requires explicit start and accepts only bounded login QR text',async()=>{
  const ui=setup();
  assert.equal(ui.scanners.length,0);
  await ui.click('start');
  for(const text of ['https://example.com/SECRET','cashuBSECRET','safebox-card:v1:'+'A'.repeat(535)]){
    ui.scanners[0].callback({data:text});
    assert.equal(ui.submitted(),0);
    assert.doesNotMatch(ui.elements.status.textContent,/SECRET/);
  }
  ui.scanners[0].callback({data:'safebox-card:v1:AQID'});
  assert.equal(ui.submitted(),1);
  assert.equal(ui.elements.payload.value,'safebox-card:v1:AQID');
  assert.equal(ui.scanners[0].destroyed,1);
  ui.scanners[0].callback({data:'safebox-card:v1:AQID'});
  assert.equal(ui.submitted(),1);
});
test('cancel, timeout, navigation and form submission prevent late results',async()=>{
  for(const stop of [u=>u.click('stop'),u=>u.timers[0](),u=>u.listeners.pagehide(),
    u=>{u.document.hidden=true;u.listeners.visibilitychange();},u=>u.listeners.submit()]){
    const ui=setup(); await ui.click('start'); stop(ui);
    ui.scanners[0].callback({data:'safebox-card:v1:AQID'});
    assert.equal(ui.submitted(),0);
    assert.equal(ui.scanners[0].destroyed,1);
  }
});
test('late permission response after cancellation cannot restart scanning',async()=>{
  const ui=setup({pending:true}); const starting=ui.click('start');
  ui.click('stop'); ui.complete(); await starting;
  ui.scanners[0].callback({data:'safebox-card:v1:AQID'});
  assert.equal(ui.submitted(),0);
  assert.ok(ui.scanners[0].destroyed>=1);
});
test('unsupported camera or permission denial retains paste fallback without secret errors',async()=>{
  for(const options of [{supported:false},{denied:true}]){
    const ui=setup(options); await ui.click('start');
    assert.equal(ui.submitted(),0);
    assert.match(ui.elements.status.textContent,/paste/i);
    assert.doesNotMatch(ui.elements.status.textContent,/SECRET/);
  }
});
