"""Offline choreography and playback tests; rendered OBS/audio acceptance is separate."""
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "examples" / "cinematic-intro"
NODE = shutil.which("node")


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.tags = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class IntroSourceTests(unittest.TestCase):
    def test_playback_controls_are_hidden_before_javascript_initializes(self):
        page = Page((PACK / "intro.html").read_text(encoding="utf-8"))
        controls = next(a for t, a in page.tags if t == "nav" and a.get("id") == "controls")
        self.assertIn("hidden", controls, "Controls must not flash on the first OBS frame")
        self.assertRegex((PACK / "intro.css").read_text(encoding="utf-8"),
                         r"\[hidden\]\s*\{\s*display:\s*none\s*!important;")

    def test_local_references_master_canvas_and_locked_identity(self):
        page = Page((PACK / "intro.html").read_text(encoding="utf-8"))
        self.assertEqual([a["src"] for t, a in page.tags if t == "script"], ["vendor/three.min.js", "intro.js"])
        self.assertTrue(all("defer" in a for t, a in page.tags if t == "script"))
        self.assertEqual([a["href"] for t, a in page.tags if t == "link"], ["intro.css"])
        self.assertEqual([a["src"] for t, a in page.tags if t == "audio"], ["score.ogg"])
        canvas = next(a for t, a in page.tags if t == "canvas")
        self.assertEqual((canvas["width"], canvas["height"]), ("1920", "1080"))
        self.assertIn("The Trading Table", canvas["aria-label"])
        self.assertIn("Synthetic Tournament", canvas["aria-label"])
        self.assertFalse(any(t in {"iframe", "video", "form"} for t, _ in page.tags))
        policy = next(a["content"] for _, a in page.tags if a.get("http-equiv") == "Content-Security-Policy")
        self.assertIn("connect-src 'none'", policy)
        self.assertIn("media-src 'self'", policy)
        self.assertNotIn("unsafe-inline", policy)

    def test_bounded_sources_without_network_or_dynamic_code(self):
        for name in ("intro.js", "intro.css", "intro.html"):
            self.assertLess((PACK / name).stat().st_size, 128 * 1024)
        script = (PACK / "intro.js").read_text(encoding="utf-8")
        self.assertNotRegex(script, r"\b(?:fetch|eval|XMLHttpRequest|WebSocket|setInterval|setTimeout)\s*\(")
        for forbidden in ("innerHTML", "Math.random", "Date.now", "location.reload", "https://", "http://"):
            self.assertNotIn(forbidden, script)
        self.assertIn('emblem.src = "../tournament/piphound-emblem.png"', script)
        self.assertTrue((PACK.parent / "tournament" / "piphound-emblem.png").is_file())
        self.assertIn('tracking("THE TRADING TABLE"', script)
        css = (PACK / "intro.css").read_text(encoding="utf-8")
        self.assertNotIn("@import", css)
        self.assertNotIn("url(", css)


MOCK = r"""
const assert = require('node:assert/strict'), m = require(process.argv[1]);
function context() {
  const calls=[];
  const gradient={addColorStop:(...args)=>calls.push(['stop',...args])};
  const ctx=new Proxy({}, {set(obj,key,value){obj[key]=value; calls.push(['set',key,value]);return true;},
    get(obj,key){
      if(key==='measureText') return text=>({width:text.length*30});
      if(key==='createLinearGradient' || key==='createRadialGradient') return (...args)=>{calls.push([key,...args]);return gradient;};
      return obj[key] || ((...args)=>calls.push([key,...args]));
    }});
  return {ctx,calls};
}
class Element {
  constructor(){this.dataset={};this.attrs={};this.events=new Map();this.textContent='';}
  setAttribute(k,v){this.attrs[k]=v;}
  addEventListener(k,v){this.events.set(k,v);}
  removeEventListener(k){this.events.delete(k);}
  click(){this.events.get('click')();}
}
function fixture(query='',reduce=false,playMode='reject') {
  const {ctx,calls}=context(), nodes={};
  for(const name of ['picture','score','controls','replay','sound','status']) nodes[name]=new Element();
  nodes.picture.getContext=()=>ctx;
  const audio=nodes.score, raf=new Map(), prefs=new Map(), deferred=[];
  Object.assign(audio,{currentTime:0,paused:true,ended:false,playCount:0,pauseCount:0,
    pause(){this.paused=true;this.pauseCount++;},
    play(){this.playCount++; if(playMode==='throw') throw new Error('blocked');
      if(playMode==='reject') return Promise.reject(new Error('blocked'));
      if(playMode==='pending') return new Promise(resolve=>deferred.push(resolve));
      this.paused=false;return Promise.resolve();}});
  let now=0,id=0;
  const preference={matches:reduce,addEventListener:(k,v)=>prefs.set(k,v),removeEventListener:k=>prefs.delete(k)};
  const doc={getElementById:id=>nodes[id]};
  const win={location:{search:query},performance:{now:()=>now},matchMedia:()=>preference,
    Image:class extends Element {constructor(){super();this.complete=true;this.naturalWidth=1280;}},
    requestAnimationFrame:cb=>{raf.set(++id,cb);return id;},cancelAnimationFrame:id=>raf.delete(id)};
  const controller=m.mount(doc,win);
  return {nodes,audio,raf,prefs,preference,controller,calls,doc,win,deferred,
    advance(ms){now=ms;const jobs=[...raf.values()];raf.clear();jobs.forEach(cb=>cb(ms));},
    reduce(value){preference.matches=value;prefs.get('change')();}};
}
"""


@unittest.skipUnless(NODE, "Node is optional; static contracts still run")
class IntroRuntimeTests(unittest.TestCase):
    def run_node(self, code):
        result = subprocess.run([NODE, "-e", MOCK + code, str(PACK / "intro.js")],
                                text=True, capture_output=True, timeout=20,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_syntax(self):
        result = subprocess.run([NODE, "--check", str(PACK / "intro.js")],
                                text=True, capture_output=True, timeout=20,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_storyboard_boundaries_effect_windows_and_final_hold(self):
        self.run_node(r"""
assert.deepEqual([m.WIDTH,m.HEIGHT,m.DURATION],[1920,1080,24]);
for(const [t,shot] of [[0,'iris'],[4.8,'corridor'],[9.6,'reveal'],[14.4,'gather'],[18,'title'],[24,'title']])
  assert.equal(m.stateAt(t).shot,shot);
for(let t=0;t<=24;t+=.025){const s=m.stateAt(t);
  for(const [key,value] of Object.entries(s)) if(typeof value==='number') assert(Number.isFinite(value));
  for(const key of ['iris','corridor','wire','code','gather','emblem','title']) assert(s[key]>=0 && s[key]<=1);
  if(t<=9.6 || t>=14.4) assert.equal(s.code,0);
  if(t>=18) {assert.equal(s.title,1);assert.equal(s.emblem,1);assert.equal(s.gather,0);assert.equal(s.corridor,0);}
}
assert.equal(m.stateAt(-100).time,0);assert.equal(m.stateAt(100).time,24);
assert.throws(()=>m.stateAt(NaN));assert.throws(()=>m.stateAt(Infinity));
""")

    def test_rendered_commands_differ_by_shot_are_finite_and_hold(self):
        self.run_node(r"""
const crypto=require('node:crypto'), outputs=[];
for(const t of [0,2.4,7.2,12,16.2,18,24]) {
  const {ctx,calls}=context();m.renderer(ctx,{complete:true,naturalWidth:1280})(t);
  for(const call of calls) for(const arg of call) if(typeof arg==='number') assert(Number.isFinite(arg) && Math.abs(arg)<100000);
  assert(calls.length<10000);
  outputs.push(crypto.createHash('sha256').update(JSON.stringify(calls)).digest('hex'));
}
assert.equal(new Set(outputs.slice(0,6)).size,6);assert.equal(outputs[5],outputs[6]);
""")

    def test_freeze_parameters_refuse_unbounded_or_empty_values(self):
        self.run_node(r"""
for(const x of ['', ' ', 'NaN','Infinity','-1','24.001','1e99','https://example.invalid'])
  assert.equal(m.options('?freeze='+x).freeze,null);
for(const x of [0,12.5,24]) assert.equal(m.options('?freeze='+x).freeze,x);
assert.deepEqual(m.options('?obs=1&silent=1&freeze=4.8'),{obs:true,silent:true,freeze:4.8});
assert.equal(m.options('?obs=true&silent=yes').obs,false);
""")

    def test_clock_follows_advancing_audio_then_survives_stall_and_end(self):
        self.run_node(r"""
const clock=m.createClock(0), audio={paused:false,ended:false,currentTime:0};
assert.equal(clock.sample(.5,audio),.5);audio.currentTime=.4;assert.equal(clock.sample(.6,audio),.6);
assert.equal(clock.seekTarget,.6);audio.currentTime=.6;
audio.currentTime=1.3;assert.equal(clock.sample(1.5,audio),1.3);
assert.equal(clock.sample(1.9,audio),1.3);assert(clock.sample(2.8,audio)>1.3);
audio.ended=true;assert(clock.sample(3.8,audio)>2);
clock.reset(10);assert.equal(clock.sample(11,null),1);assert.equal(clock.sample(40,null),24);
""")

    def test_late_audio_and_resumed_decoder_seek_forward_without_picture_rewind(self):
        self.run_node(r"""
const f=fixture('',false,'pending');f.advance(2200);assert.equal(f.nodes.picture.dataset.time,'2.200');
f.audio.paused=false;f.audio.currentTime=.1;f.advance(2300);
assert.equal(f.nodes.picture.dataset.time,'2.300');assert.equal(f.audio.currentTime,2.3);
f.audio.currentTime=2.4;f.advance(2400);assert.equal(f.nodes.picture.dataset.time,'2.400');
f.advance(3400);const stalled=Number(f.nodes.picture.dataset.time);assert(stalled>=3.4);
f.audio.currentTime=2.5;f.advance(3500);assert(Number(f.nodes.picture.dataset.time)>stalled);
assert.equal(f.audio.currentTime,Number(f.nodes.picture.dataset.time));
""")

    def test_failed_late_join_seek_continues_silent_and_pending_mute_stays_muted(self):
        self.run_node(r"""
(async()=>{
  const f=fixture('',false,'pending');f.advance(2000);f.audio.paused=false;
  Object.defineProperty(f.audio,'currentTime',{get:()=>.1,set:()=>{throw new Error('not seekable');}});
  f.advance(2100);assert.equal(f.audio.paused,true);assert.equal(f.nodes.picture.dataset.time,'2.100');
  f.advance(3100);assert.equal(f.nodes.picture.dataset.time,'3.100');
  assert.equal(f.nodes.status.textContent,'Silent playback · score unavailable · basic preview');
  const g=fixture('',false,'pending');g.nodes.sound.click();const pauses=g.audio.pauseCount;
  g.deferred[0]();await Promise.resolve();assert(g.audio.pauseCount>pauses);
  g.advance(2000);assert.equal(g.nodes.picture.dataset.time,'2.000');
})().catch(error=>{console.error(error);process.exitCode=1;});
""")

    def test_frame_scheduler_is_bounded_to_thirty_paints_per_second(self):
        self.run_node(r"""
const f=fixture('?silent=1');f.calls.length=0;
for(let ms=10;ms<=1000;ms+=10) f.advance(ms);
const paints=f.calls.filter(call=>call[0]==='setTransform').length;
assert(paints>=25 && paints<=31);assert.equal(f.raf.size,1);
""")

    def test_rejected_pending_and_throwing_autoplay_do_not_freeze(self):
        self.run_node(r"""
(async()=>{for(const mode of ['reject','pending','throw']) {
  const f=fixture('',false,mode);await Promise.resolve();await Promise.resolve();
  f.advance(2300);assert.equal(f.nodes.picture.dataset.time,'2.300');assert.equal(f.raf.size,1);
  f.advance(24000);assert.equal(f.nodes.picture.dataset.time,'24.000');assert.equal(f.raf.size,0);
  assert.equal(f.nodes.status.textContent,'Intro complete · basic preview');assert(f.audio.pauseCount>=2);
}})().catch(error=>{console.error(error);process.exitCode=1;});
""")

    def test_freeze_and_reduced_motion_are_silent_static_and_react_to_preference(self):
        self.run_node(r"""
for(const query of ['?freeze=12','?freeze=0&obs=1']) {const f=fixture(query,false,'success');
  assert.equal(f.audio.playCount,0);assert.equal(f.raf.size,0);assert.equal(f.nodes.controls.hidden,query.includes('obs=1'));
  assert.throws(()=>f.controller.render(25));assert.throws(()=>f.controller.render(Infinity));}
const f=fixture('',true,'success');assert.equal(f.nodes.picture.dataset.shot,'title');
assert.equal(f.audio.playCount,0);assert.equal(f.raf.size,0);
f.reduce(false);assert.equal(f.audio.playCount,1);assert.equal(f.raf.size,1);
f.reduce(true);assert.equal(f.raf.size,0);assert.equal(f.audio.paused,true);assert.equal(f.nodes.picture.dataset.time,'24.000');
""")

    def test_replay_single_scheduler_silent_switch_and_stale_play_promise(self):
        self.run_node(r"""
(async()=>{const f=fixture('',false,'pending');f.advance(4000);f.nodes.replay.click();
assert.equal(f.nodes.picture.dataset.time,'0.000');assert.equal(f.raf.size,1);assert.equal(f.audio.playCount,2);
const pauses=f.audio.pauseCount;f.deferred[0]();await Promise.resolve();assert.equal(f.audio.pauseCount,pauses);
f.nodes.sound.click();assert.equal(f.nodes.sound.attrs['aria-pressed'],'false');
f.advance(5000);assert.equal(f.nodes.picture.dataset.time,'1.000');
assert.equal(m.mount(f.doc,f.win),null);f.controller.stop();assert.equal(f.raf.size,0);
assert.equal(f.prefs.size,0);assert.equal(f.nodes.replay.events.size,0);
})().catch(error=>{console.error(error);process.exitCode=1;});
""")

    def test_pinned_three_geometry_material_reveal_camera_and_disposal(self):
        self.run_node(r"""
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const sandbox={console};vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(path.dirname(process.argv[1]),'vendor/three.min.js'),'utf8'),sandbox);
const THREE=sandbox.THREE;assert.equal(THREE.REVISION,'186');
let disposed=0,lost=0,rendered=0;
class FakeGPU {
  constructor({canvas}){this.domElement=canvas;}
  setPixelRatio(){} setSize(){} render(scene,camera){assert(scene.isScene && camera.isPerspectiveCamera);rendered++;}
  dispose(){disposed++;} forceContextLoss(){lost++;}
}
const doc={createElement:()=>({getContext:()=>context().ctx})};
const world=m.createWorld({...THREE,WebGLRenderer:FakeGPU},doc);
world.render(6.8);const solid=world.inspect();
assert.equal(solid.materialOpacity,1);assert.equal(solid.edgeOpacity,0);assert.equal(solid.gridOpacity,0);
assert(solid.meshes>100 && solid.meshes<220);assert(solid.travellers>=25 && solid.travellers<40);
world.render(11.8);const mesh=world.inspect();assert.equal(mesh.materialOpacity,0);assert(mesh.edgeOpacity>.5);
assert(mesh.gridOpacity>.3);assert(mesh.camera[2]<solid.camera[2]);
world.render(16.4);const gathered=world.inspect();assert(gathered.collapse>.95);
const spread=positions=>Math.max(...positions.map(p=>Math.abs(p[0])));
assert(spread(gathered.positions)<spread(solid.positions)*.08);
assert(rendered===3);world.dispose();world.dispose();assert.equal(disposed,1);assert.equal(lost,1);
assert.throws(()=>world.render(5));
""")

    def test_3d_timeline_keeps_seed_handoff_and_pure_finite_bounds(self):
        self.run_node(r"""
for(let t=0;t<=24;t+=.025){const w=m.worldState(t);
  for(const key of ['travel','collapse','wire','visibility','iris']) assert(w[key]>=0 && w[key]<=1);
  assert(w.camera.every(Number.isFinite));assert(w.camera[2]>=-4 && w.camera[2]<=20);
  if(t<9.6) assert.equal(w.wire,0);
  if(t>18) assert.equal(w.visibility,0);
}
assert(m.worldState(15).visibility>.9);assert(m.worldState(15).collapse>0);
assert.equal(m.worldState(18).collapse,1);assert.throws(()=>m.worldState(NaN));
""")

    def test_missing_or_failed_3d_is_explicit_and_does_not_break_title(self):
        self.run_node(r"""
assert.equal(m.createWorld(null,{}),null);
const f=fixture('?silent=1');assert.equal(f.nodes.picture.dataset.backend,'canvas-fallback');
assert(f.nodes.status.textContent.includes('basic preview'));
let failed=0,disposed=0;const {ctx,calls}=context();
const draw=m.renderer(ctx,{complete:true,naturalWidth:1280},{render(){throw new Error('context lost');},dispose(){disposed++;}},()=>failed++);
draw(7);draw(12);draw(24);assert.equal(failed,1);assert.equal(disposed,1);
assert(calls.some(c=>c[0]==='fillText' && c[1]==='P'));
""")


if __name__ == "__main__":
    unittest.main()
