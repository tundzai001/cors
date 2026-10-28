const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {randomUUID} = require('node:crypto');
async function check(source) {
    const signals = [], events = {}, intervals = [];
    const location = {pathname:'/',search:'?token=private'};
    const values = new Map();
    let successful = true;
    const context = {
        crypto:{randomUUID}, AbortSignal, URLSearchParams,
        navigator:{maxTouchPoints:0}, location,
        localStorage:{getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)},
        document:{hidden:false,addEventListener:(name,fn)=>events[name]=fn},
        history:{pushState(_state,_title,url){const next=new URL(url,'https://example.test');location.pathname=next.pathname;location.search=next.search;},replaceState(){}},
        setInterval:(fn,ms)=>{assert.equal(ms,30000);intervals.push(fn);},
        fetch:async (_url,options)=>{signals.push(JSON.parse(options.body));return {ok:successful};},
        window:{ApiClient:{getClientMeta:async()=>({device_name:'macOS · Brave'})},addEventListener:(name,fn)=>events[name]=fn}
    };
    vm.runInNewContext(source,context);
    const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
    await flush();
    assert.equal(signals.length,1);
    assert.equal(signals[0].path,'/');
    assert.equal(signals[0].heartbeat,false);
    intervals[0]();await flush();
    assert.equal(signals[1].view_id,signals[0].view_id);
    assert.equal(signals[1].heartbeat,true);
    context.document.hidden=true;intervals[0]();await flush();assert.equal(signals.length,2);
    context.document.hidden=false;events.visibilitychange();await flush();assert.equal(signals[2].heartbeat,true);
    context.history.pushState({},'','/?secret=ignored');await flush();assert.equal(signals.at(-1).heartbeat,true);
    successful=false;context.history.pushState({},'','/?view=map');await flush();
    const failed=signals.at(-1);assert.equal(failed.path,'/?view=map');assert.equal(failed.heartbeat,false);
    successful=true;intervals[0]();await flush();assert.equal(signals.at(-1).view_id,failed.view_id);assert.equal(signals.at(-1).heartbeat,false);
    context.history.pushState({},'','/security.html?private=yes');await flush();
    assert.equal(signals.at(-1).path,'/security');assert.equal(signals.at(-1).visitor_id,signals[0].visitor_id);
}
(async()=>{for(const file of ['cors_dashboard/src/js/analytics-tracker.js','cors_dashboard/frontend/js/dist/analytics-tracker.min.js'])await check(fs.readFileSync(file,'utf8'));console.log('PASS: source and built page tracking, heartbeat, hidden tabs, retry UUID, SPA navigation and query stripping');})().catch(error=>{console.error(error);process.exitCode=1;});
