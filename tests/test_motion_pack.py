"""Offline motion contracts; optional Node probes use no browser, network or OBS."""
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "examples" / "motion"
NODE = shutil.which("node")


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs): self.tags.append((tag, dict(attrs)))


class MotionSourceTests(unittest.TestCase):
    def test_standalone_pages_reference_only_the_shared_local_assets(self):
        for name in ("fibonacci", "market", "table"):
            page = Page((PACK / (name + ".html")).read_text(encoding="utf-8"))
            scripts = [a for t, a in page.tags if t == "script"]
            self.assertEqual(scripts, [{"src": "motion.js", "defer": None}])
            self.assertEqual([a["href"] for t, a in page.tags if t == "link"], ["motion.css"])
            body = next(a for t, a in page.tags if t == "body")
            self.assertEqual(body["data-motion-loop"], name)
            self.assertIn("data-motion-standalone", body)
            self.assertTrue(any(t == "main" and a.get("id") == "stage" for t, a in page.tags))
            policy = next(a["content"] for t, a in page.tags if a.get("http-equiv") == "Content-Security-Policy")
            self.assertIn("default-src 'none'", policy)
            self.assertNotIn("unsafe-inline", policy)
            self.assertFalse(any(t in {"iframe", "audio", "video", "img"} for t, _ in page.tags))

    def test_files_bounded_and_no_runtime_network_or_html_evaluation(self):
        expected = {"motion.js", "motion.css", "fibonacci.html", "market.html", "table.html", "README.md"}
        self.assertEqual({p.name for p in PACK.iterdir()}, expected)
        for name in expected:
            self.assertGreater((PACK / name).stat().st_size, 0)
            self.assertLess((PACK / name).stat().st_size, 128 * 1024)
        script = (PACK / "motion.js").read_text(encoding="utf-8")
        self.assertNotRegex(script, r"\b(?:eval|fetch|XMLHttpRequest|WebSocket|setInterval|setTimeout)\s*\(")
        self.assertNotIn("innerHTML", script)
        self.assertNotIn("Math.random", script)
        self.assertNotIn("Date.now", script)
        urls = re.findall(r'https?://[^"\s]+', script)
        self.assertEqual(urls, ["http://www.w3.org/2000/svg"])

    def test_transparency_ambient_exclusion_and_palette_contract(self):
        css = (PACK / "motion.css").read_text(encoding="utf-8")
        script = (PACK / "motion.js").read_text(encoding="utf-8")
        self.assertIn("background: transparent", css)
        self.assertIn("pointer-events: none", css)
        self.assertIn('data-treatment="ambient"', css)
        self.assertIn("x: 0, y: 244, width: 1920, height: 771", script)
        self.assertIn('x: 0, y: 244, width: 1500, height: 616, fill: "black"', script)
        self.assertIn('node("feGaussianBlur", { stdDeviation: 24 }', script)
        colors = set(re.findall(r"#[0-9a-fA-F]{6}\b", script + css))
        self.assertEqual(colors, {"#d4ad65", "#d7b777", "#171719", "#09090b", "#242426", "#79613a"})


@unittest.skipUnless(NODE, "Node is optional; source contracts still run without it")
class MotionGeometryTests(unittest.TestCase):
    def run_node(self, code):
        result = subprocess.run([NODE, "-e", code, str(PACK / "motion.js")],
                                text=True, capture_output=True, timeout=20,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_javascript_syntax(self):
        result = subprocess.run([NODE, "--check", str(PACK / "motion.js")],
                                text=True, capture_output=True, timeout=20,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_geometry_is_finite_bounded_periodic_and_not_static(self):
        self.run_node(r"""
const assert = require('node:assert/strict'), m = require(process.argv[1]);
assert.deepEqual(m.DURATIONS, { fibonacci:18, market:24, table:20 });
function numbers(value) {
  if (typeof value === 'number') return [value];
  if (value && typeof value === 'object') return Object.values(value).flatMap(numbers);
  return [];
}
for (const [name, duration] of Object.entries(m.DURATIONS)) {
  for (let i=0; i<=120; i++) {
    const time = duration*i/120, a=numbers(m.frame(name,time)), b=numbers(m.frame(name,time+duration));
    assert(a.every(Number.isFinite));
    assert(a.every(x => Math.abs(x)<=2280));
    assert.equal(a.length,b.length);
    a.forEach((x,j)=>assert(Math.abs(x-b[j])<1e-8));
  }
  assert.notDeepEqual(m.frame(name,duration*.25),m.frame(name,duration*.55));
}
assert.throws(()=>m.frame('prices',1)); assert.throws(()=>m.frame('market',NaN));
assert.throws(()=>m.frame('market',Infinity));
""")

    def test_fibonacci_squares_are_adjacent_and_arcs_are_contiguous(self):
        self.run_node(r"""
const assert = require('node:assert/strict'), m = require(process.argv[1]);
const squares=m.fibonacciSquares(), sizes=squares.map(x=>x.size/34);
assert.deepEqual(sizes,[1,1,2,3,5,8,13,21]);
for(let i=1;i<squares.length;i++) {
  const a=squares[i];
  assert(squares.slice(0,i).some(b =>
    ((a.x===b.x+b.size || b.x===a.x+a.size) && Math.min(a.y+a.size,b.y+b.size)>Math.max(a.y,b.y)) ||
    ((a.y===b.y+b.size || b.y===a.y+a.size) && Math.min(a.x+a.size,b.x+b.size)>Math.max(a.x,b.x))));
  const prev=squares[i-1].arc.split(' ').map(Number), curr=a.arc.split(' ').map(Number);
  assert.equal(curr[1],prev[prev.length-2]); assert.equal(curr[2],prev[prev.length-1]);
}
for(const a of squares) assert(a.x>=0 && a.y>=0 && a.x+a.size<=1920 && a.y+a.size<=1080);
""")

    def test_reset_boundaries_are_visibly_continuous(self):
        self.run_node(r"""
const assert = require('node:assert/strict'), m = require(process.argv[1]), epsilon=0.0001;
for(const name of ['fibonacci','table']) {
  assert.equal(m.frame(name,0).opacity,0);
  assert(m.frame(name,m.DURATIONS[name]-epsilon).opacity<1e-6);
  assert(m.frame(name,epsilon).opacity<1e-6);
}
for(let i=0;i<15;i++) {
  const time=24*i/15;
  const before=m.frame('market',time-epsilon).candles, after=m.frame('market',time+epsilon).candles;
  for(let j=0;j<15;j++) for(const key of ['x','y','height'])
    assert(Math.abs(before[j][key]*before[j].opacity-after[j][key]*after[j].opacity)<0.2);
}
""")

    def test_freeze_parser_rejects_unbounded_values(self):
        self.run_node(r"""
const assert = require('node:assert/strict'), m = require(process.argv[1]);
for(const value of ['', 'NaN','Infinity','-1','3600.01','1e100','0xNO'])
  assert.equal(m.options('?freeze='+value).freeze,null);
for(const value of [0,9.125,3600]) assert.equal(m.options('?freeze='+value).freeze,value);
assert.equal(m.options('?background=dark').dark,true);
assert.equal(m.options('?background=https://example.invalid').dark,false);
""")

    def test_axis_aligned_ratio_and_wick_strokes_have_valid_paint(self):
        self.run_node(r"""
const assert = require('node:assert/strict'), m = require(process.argv[1]);
class Element {
  constructor(tag) { this.tag=tag; this.attrs={}; this.children=[]; this.dataset={}; }
  setAttribute(k,v) { this.attrs[k]=String(v); }
  appendChild(child) { this.children.push(child); child.parentNode=this; return child; }
  insertBefore(child) { this.children.unshift(child); child.parentNode=this; return child; }
  querySelector() { return null; }
  get firstChild() { return this.children[0] || null; }
}
function mounted(preset) {
  const body=new Element('body'), stage=new Element('main'); body.dataset.motionLoop=preset;
  const doc={body,getElementById:()=>stage,createElementNS:(_,tag)=>new Element(tag),
    addEventListener(){},removeEventListener(){}};
  const win={location:{search:'?freeze=9'},performance:{now:()=>0},
    matchMedia:()=>({matches:false,addEventListener(){},removeEventListener(){}}),
    MutationObserver:class { observe(){} disconnect(){} }};
  m.mount(doc,win);
  const flatten=node=>[node,...node.children.flatMap(flatten)];
  return flatten(stage.firstChild);
}
for(const [preset,pattern,count] of [
  ['fibonacci',/^M310 [-\d.]+H1608$/,5],
  ['market',/^M0 [-\d.]+V[-\d.]+$/,15]
]) {
  const nodes=mounted(preset), paths=nodes.filter(n=>n.tag==='path' && pattern.test(n.attrs.d));
  assert.equal(paths.length,count,`${preset} must contain the actual ratio/wick paths`);
  for(const path of paths) {
    assert(Number(path.attrs['stroke-width'])>0);
    const paint=path.attrs.stroke, reference=/^url\(#([^)]*)\)$/.exec(paint || '');
    if(reference) {
      const gradient=nodes.find(n=>n.attrs.id===reference[1]);
      assert(gradient,`${preset}: missing paint server`);
      // SVG objectBoundingBox paint is undefined for a horizontal/vertical path's zero extent.
      assert.equal(gradient.attrs.gradientUnits,'userSpaceOnUse',
        `${preset}: zero-extent path cannot use objectBoundingBox gradient`);
      assert(gradient.attrs.x1!==gradient.attrs.x2 || gradient.attrs.y1!==gradient.attrs.y2);
    } else assert(/^#[0-9a-f]{6}$/i.test(paint),`${preset}: visible solid paint required`);
  }
}
""")

    def test_dom_mount_remount_freeze_and_reduced_motion_use_single_clock(self):
        self.run_node(r"""
const assert = require('node:assert/strict'), m = require(process.argv[1]);
class Element {
  constructor(tag) { this.tag=tag; this.attrs={}; this.children=[]; this.parentNode=null; this.dataset={}; }
  setAttribute(k,v) { this.attrs[k]=String(v); }
  appendChild(child) { return this.insertBefore(child,null); }
  insertBefore(child,ref) {
    if(child.parentNode) child.parentNode.children.splice(child.parentNode.children.indexOf(child),1);
    const i=ref ? this.children.indexOf(ref) : this.children.length;
    this.children.splice(i,0,child); child.parentNode=this; return child;
  }
  get firstChild() { return this.children[0] || null; }
  querySelector(query) { return this.children.find(c=>c.attrs.class==='motion-layer') || null; }
  replaceChildren(...children) { this.children.forEach(c=>c.parentNode=null); this.children=[]; children.forEach(c=>this.appendChild(c)); }
}
function fixture(preset,query='',reduce=false,ambient=false,existing=true) {
  const body=new Element('body'), stage=existing ? new Element('main') : null, caption=new Element('h1');
  body.dataset.motionLoop=preset;
  if(ambient) body.dataset.motionTreatment='ambient';
  if(stage) { stage.id='stage'; stage.appendChild(caption); body.appendChild(stage); }
  const events=new Map(), preferences=new Map(), raf=new Map();
  let now=0, id=0, observer;
  const media={matches:reduce,addEventListener:(n,cb)=>preferences.set(n,cb),removeEventListener:(n)=>preferences.delete(n)};
  const doc={body,hidden:false,createElement:t=>new Element(t),createElementNS:(_,t)=>new Element(t),
    getElementById:()=>stage,addEventListener:(n,cb)=>events.set(n,cb),removeEventListener:n=>events.delete(n)};
  const win={location:{search:query},performance:{now:()=>now},matchMedia:()=>media,
    requestAnimationFrame:cb=>{raf.set(++id,cb);return id;},cancelAnimationFrame:i=>raf.delete(i),
    MutationObserver:class { constructor(cb){ this.cb=cb; observer=this; } observe(s){this.stage=s;} disconnect(){this.disconnected=true;} }};
  const controller=m.mount(doc,win), mounted=stage || body.children[0], svg=mounted.firstChild;
  return {doc,win,media,events,preferences,raf,controller,stage:mounted,svg,caption,observer,
    setNow:value=>{now=value;}};
}
for(const preset of Object.keys(m.DURATIONS)) {
  const f=fixture(preset);
  assert.equal(f.svg.attrs.class,'motion-layer'); assert.equal(f.svg.attrs['aria-hidden'],'true');
  assert.equal(f.stage.children[1],f.caption); assert.equal(f.raf.size,1);
  assert.equal(m.mount(f.doc,f.win),null); assert.equal(f.raf.size,1);
  const replacement=new Element('h2'); f.stage.replaceChildren(replacement); f.observer.cb(); f.observer.cb();
  assert.equal(f.stage.firstChild,f.svg); assert.equal(f.stage.children.length,2); assert.equal(f.raf.size,1);
  f.media.matches=true; f.preferences.get('change')(); assert.equal(f.raf.size,0);
  assert.equal(Number(f.svg.attrs['data-time']),m.DURATIONS[preset]*.52);
  f.media.matches=false; f.preferences.get('change')(); assert.equal(f.raf.size,1);
  f.doc.hidden=true; f.events.get('visibilitychange')(); assert.equal(f.raf.size,0);
  f.setNow(5000); f.doc.hidden=false; f.events.get('visibilitychange')();
  assert.equal(Number(f.svg.attrs['data-time']),5); assert.equal(f.raf.size,1);
  f.controller.stop(); assert.equal(f.raf.size,0); assert(f.observer.disconnected);
  assert.equal(f.events.size,0); assert.equal(f.preferences.size,0);
}
const frozen=fixture('market','?freeze=9.5',true,true,false);
assert.equal(frozen.raf.size,0); assert.equal(frozen.svg.attrs['data-time'],'9.5');
assert.equal(frozen.svg.attrs['data-treatment'],'ambient');
assert.equal(frozen.doc.body.dataset.motionStandalone,'');
function count(node) { return 1+node.children.reduce((n,c)=>n+count(c),0); }
assert(count(frozen.svg)<160);
""")


if __name__ == "__main__":
    unittest.main()
