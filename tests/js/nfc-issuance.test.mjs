import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const source = readFileSync(new URL('../../app/static/nfc-issuance.js', import.meta.url), 'utf8');
function setup({readOnly = false} = {}) {
  const elements = {}, readers = [], writes = [];
  let submissions = 0;
  for (const name of ['write','read','cancel','confirm','status','write-payload','read-payload','read-form']) {
    elements[name] = {checked:false, value:'AQID', handlers:{},
      addEventListener(event, handler) {this.handlers[event] = handler;},
      requestSubmit() {submissions++;}};
  }
  if (readOnly) {
    delete elements.write;
    delete elements.confirm;
    delete elements['write-payload'];
  }
  class Reader {
    constructor() {readers.push(this);}
    async write(message, options) {writes.push({message,options});}
    async scan() {}
  }
  const document = {querySelector() {return {querySelector(selector) {return elements[selector.slice(11,-1)];}};}, addEventListener() {}};
  vm.runInNewContext(source, {document, window:{isSecureContext:true,NDEFReader:Reader,addEventListener(){}},
    AbortController,Uint8Array,atob,btoa,setTimeout:()=>1,clearTimeout(){}});
  return {elements, readers, writes, submissions:()=>submissions, click:(n)=>elements[n].handlers.click()};
}
test('credential write requires replacement consent and writes binary with overwrite enabled', async () => {
  const ui=setup();
  assert.equal(ui.readers.length,0);
  await ui.click('write');
  assert.equal(ui.writes.length,0);
  ui.elements.confirm.checked=true;
  await ui.click('write');
  assert.equal(ui.writes[0].options.overwrite,true);
  assert.deepEqual([...ui.writes[0].message.records[0].data],[1,2,3]);
  assert.equal(ui.submissions(),0);
});
test('read preserves byte offsets and submits only supported MIME', async () => {
  const ui=setup();
  await ui.click('read');
  ui.readers[0].onreading({message:{records:[{recordType:'mime',mediaType:'application/vnd.safebox.card+cbor',data:new DataView(new Uint8Array([9,1,2,9]).buffer,1,2)}]}});
  assert.equal(ui.elements['read-payload'].value,'AQI=');
  assert.equal(ui.submissions(),1);
});
test('cancel ignores late callbacks and unknown records are not submitted', async () => {
  const ui=setup();
  await ui.click('read');
  await ui.click('cancel');
  ui.readers[0].onreading({message:{records:[]}});
  assert.equal(ui.submissions(),0);
  await ui.click('read');
  ui.readers[1].onreading({message:{records:[{recordType:'url'}]}});
  assert.equal(ui.submissions(),0);
});

test('login uses read-only controls and submits only after an explicit read', async () => {
  const ui = setup({readOnly:true});
  assert.equal(ui.readers.length, 0);
  assert.equal(ui.submissions(), 0);
  await ui.click('read');
  ui.readers[0].onreading({message:{records:[{recordType:'mime',mediaType:'application/vnd.safebox.card+cbor',data:new DataView(new Uint8Array([1,2]).buffer)}]}});
  assert.equal(ui.submissions(), 1);
  assert.equal(ui.writes.length, 0);
});
