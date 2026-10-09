"""Exercise shared demo geometry without a browser or mandatory Node dependency."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parents[1]
PACKS = ("starter", "tournament")


def participant(name="ALPHA", eligibility="ranked", **levels):
    return {"id": name.lower(), "name": name, "eligibility": eligibility,
            "trade": {"instrument": "DEMO / POINTS", "direction": "long",
                      "entry": 5124.25, "stop": 5118, "target": 5142, **levels}}


class CandlestickTests(unittest.TestCase):
    def render_dom(self, mode="demo", view="table", count=2, malformed_rows=False, portrait=False, levels=None):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is optional; DOM behavior was not exercised")
        now = datetime.now(timezone.utc)
        stamp = now.isoformat(timespec="milliseconds").replace("+00:00","Z")
        rows=[]
        for index in range(count):
            row=participant("SEAT " + str(index))
            row.update(realized_minor=5000 if index<2 else 4000-index*100,unrealized_minor=0,
                       fees_minor=100,status="active",observed_at=stamp)
            rows.append(row)
        rows[0].update(id="orbit",name="ORBIT")
        if levels:
            rows[0]["trade"].update(levels)
        if malformed_rows:
            rows[-1].update(observed_at=None,fees_minor=None)
            rows[-2]["observed_at"]=(now-timedelta(seconds=180)).isoformat(timespec="milliseconds").replace("+00:00","Z")
        snapshot={"schema":"obs.tournament.v1","mode":mode,"currency":"USD","session_id":"test",
                  "label":"Test fixture","as_of":stamp,"round":1,"stage":"final","participants":rows}
        code = r'''
const fs=require('fs'),vm=require('vm'),input=JSON.parse(fs.readFileSync(0,'utf8'));
const ids={};
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.attributes={};this.style={};this.dataset={};this.textContent='';this.className=''}
 set id(value){ids[value]=this} get id(){return ''}
 setAttribute(k,v){this.attributes[k]=v} appendChild(child){this.children.push(child);return child}
 replaceChildren(){this.children=[]}
}
const stage=new Element('main'),data=new Element('script'),body=new Element('body');
stage.id='stage';data.id='tournament-data';data.textContent=JSON.stringify({snapshot:input.snapshot,max_age_seconds:120,portrait:input.portrait});body.dataset.view=input.view;
const document={body,getElementById:id=>ids[id],createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag)};
vm.runInNewContext(fs.readFileSync(process.argv[1],'utf8'),{document,window:{innerWidth:1920,innerHeight:1080,addEventListener(){}},setInterval(){},Date,console});
console.log(JSON.stringify(stage));
'''
        results=[]
        for pack in PACKS:
            run=subprocess.run([node,"-e",code,str(ROOT/"examples"/pack/"broadcast.js")],
                input=json.dumps({"snapshot":snapshot,"view":view,"portrait":portrait}),text=True,encoding="utf-8",capture_output=True,timeout=10,check=True)
            results.append(json.loads(run.stdout))
        return results

    def flatten(self, element):
        return [element]+[descendant for child in element["children"] for descendant in self.flatten(child)]

    def execute(self, snapshot, rows=None):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is optional; pure JavaScript geometry was not exercised")
        code = "const fs=require('fs'),api=require(process.argv[1]),v=JSON.parse(fs.readFileSync(0,'utf8')); console.log(JSON.stringify(api.buildCandleChart(v.snapshot,v.rows)));"
        output = []
        for pack in PACKS:
            result = subprocess.run([node, "-e", code, str(ROOT / "examples" / pack / "broadcast.js")],
                input=json.dumps({"snapshot":snapshot,"rows":rows if rows is not None else snapshot["participants"]}),
                text=True,encoding="utf-8",capture_output=True,timeout=10,check=True)
            output.append(json.loads(result.stdout))
        self.assertEqual(*output)
        return output[0]

    def demo(self, *participants):
        return {"mode":"demo","participants":list(participants)}

    def test_geometry_is_deterministic_ohlc_and_preserves_supplied_levels(self):
        snapshot = self.demo(participant())
        chart = self.execute(snapshot)
        self.assertEqual(chart,self.execute(snapshot))
        self.assertEqual(len(chart["bars"]),64)
        for bar in chart["bars"]:
            self.assertLessEqual(bar["low"], min(bar["open"],bar["close"]))
            self.assertGreaterEqual(bar["high"], max(bar["open"],bar["close"]))
            self.assertLess(bar["highY"], bar["lowY"])
            self.assertGreater(bar["highY"],chart["plot"]["top"])
            self.assertLess(bar["lowY"],chart["plot"]["bottom"])
            self.assertLess(bar["x"]-bar["width"]/2,bar["x"]+bar["width"]/2)
        self.assertEqual({row["kind"]:row["value"] for row in chart["levels"]},
                         {"entry":5124.25,"stop":5118,"target":5142})
        self.assertTrue(any(bar["close"]<bar["open"] for bar in chart["bars"]))
        self.assertTrue(any(bar["close"]>bar["open"] for bar in chart["bars"]))

    def test_price_lines_use_the_same_scale_as_candles(self):
        for row in (participant(direction="short",entry=20,stop=30,target=10),
                    participant(entry=-10,stop=-30,target=-5),
                    participant(entry=1e9-1,stop=1e9-2,target=1e9)):
            chart = self.execute(self.demo(row))
            for level in chart["levels"]:
                expected = chart["plot"]["bottom"]-(level["value"]-chart["minimum"])/(chart["maximum"]-chart["minimum"])*(chart["plot"]["bottom"]-chart["plot"]["top"])
                self.assertAlmostEqual(level["y"],expected)
                self.assertLess(chart["minimum"],level["value"])
                self.assertGreater(chart["maximum"],level["value"])

    def test_near_coincident_and_flat_levels_keep_exact_prices_but_separate_labels(self):
        for row in (participant(entry=1,stop=1-1e-12,target=1+1e-12),
                    participant(direction="flat",entry=0,stop=0,target=0)):
            chart = self.execute(self.demo(row))
            levels = chart["levels"]
            self.assertTrue(all(b["labelY"]-a["labelY"]>=26 for a,b in zip(levels,levels[1:])))
            self.assertGreaterEqual(levels[0]["labelY"],chart["plot"]["top"])
            self.assertLessEqual(levels[-1]["labelY"],chart["plot"]["bottom"])
            self.assertEqual({v["kind"]:v["value"] for v in levels}, {k:row["trade"][k] for k in ("entry","stop","target")})

    def test_feature_identity_prefers_first_eligible_seat_not_sorted_standings(self):
        first,second,third=participant("FIRST","stale"),participant("SECOND"),participant("THIRD")
        chart=self.execute(self.demo(first,second,third))
        self.assertEqual(chart["featured"]["id"],"second")
        second["eligibility"]=third["eligibility"]="missing"
        chart=self.execute(self.demo(first,second,third))
        self.assertEqual(chart["featured"]["id"],"first")
        self.assertEqual(chart["featured"]["eligibility"],"stale")

    def test_paper_never_receives_fabricated_ohlc_and_missing_levels_are_explicit(self):
        for snapshot in ({"mode":"paper","participants":[participant()]},
                         self.demo({"id":"x","name":"X","eligibility":"ranked"}),
                         self.demo(participant(entry=True)),self.demo(participant(stop=None))):
            result=self.execute(snapshot)
            self.assertFalse(result["available"])
            self.assertNotIn("bars",result)
            self.assertIn("reason",result)

    def test_shared_data_helpers_remain_identical(self):
        snippets=[]
        for pack in PACKS:
            content=(ROOT/"examples"/pack/"broadcast.js").read_text(encoding="utf-8")
            snippets.append(content[content.index("function parseUTC("):content.index("function buildCandleChart(")])
        self.assertEqual(*snippets)

    def test_actual_chart_dom_has_64_bodies_and_exact_level_text(self):
        for dom in self.render_dom():
            nodes=self.flatten(dom)
            svg=next(node for node in nodes if node["tag"]=="svg")
            rects=[node for node in self.flatten(svg) if node["tag"]=="rect"]
            self.assertEqual(len(rects),67)  # 64 candle bodies and three level labels.
            text=[node["textContent"] for node in nodes]
            for expected in ("ENTRY 5124.25","STOP 5118","TARGET 5142"):
                self.assertIn(expected,text)
            self.assertTrue(any("NOT MARKET DATA" in value for value in text))
            self.assertFalse(any("NaN" in str(node["attributes"]) for node in nodes))
        for dom in self.render_dom(mode="paper"):
            self.assertFalse(any(node["tag"]=="svg" for node in self.flatten(dom)))

    def test_leaderboard_keeps_all_seats_ties_unknowns_components_and_observed_times(self):
        for dom in self.render_dom(view="standings",count=6,malformed_rows=True):
            nodes=self.flatten(dom)
            body=next(node for node in nodes if node["tag"]=="tbody")
            self.assertEqual(len(body["children"]),6)
            texts=[node["textContent"] for node in nodes]
            self.assertIn("Leaderboard.",texts)
            self.assertEqual(texts.count("=1"),2)
            self.assertTrue(any("STALE" in value for value in texts))
            self.assertTrue(any("MISSING" in value for value in texts))
            self.assertIn("OBSERVED UNKNOWN",texts)
            self.assertEqual(sum(value.startswith("OBSERVED ") for value in texts),6)
            for value in ("REALIZED","UNREALIZED","FEES","NET / USD"):
                self.assertIn(value,texts)
            self.assertEqual(texts.count("—"),8)  # 2 rank cells and 3 components per unranked seat.

    def test_two_seat_leaderboard_retains_comparison_cards(self):
        for dom in self.render_dom(view="standings"):
            nodes=self.flatten(dom)
            self.assertEqual(sum("comparison-card" in node["className"] for node in nodes),2)
            self.assertFalse(any(node["tag"]=="table" for node in nodes))

    def test_six_seat_intermission_host_is_separate_from_chart_and_competing_seats(self):
        starter,showcase=self.render_dom(view="starting-soon",count=6,portrait=True)
        nodes=self.flatten(showcase)
        self.assertTrue(any(node["tag"]=="img" and node.get("src")=="presenter.png" for node in nodes))
        self.assertIn("PIPHOUND",[node["textContent"] for node in nodes if node["className"]=="portrait-signature"])
        for dom in (starter,showcase):
            self.assertTrue(any("6 seats" in node["textContent"] for node in self.flatten(dom)))
        generic=self.flatten(self.render_dom(view="starting-soon",count=6,portrait=False)[1])
        self.assertIn("ORBIT",[node["textContent"] for node in generic if node["className"]=="portrait-signature"])
        for dom in self.render_dom(count=6,portrait=True):
            nodes=self.flatten(dom)
            self.assertTrue(any(node["tag"]=="strong" and node["textContent"].startswith("ORBIT ·") for node in nodes))
            self.assertFalse(any(node["tag"]=="img" for node in nodes))

    def test_axis_ticks_remain_distinct_for_tiny_and_large_declared_prices(self):
        for levels in ({"entry":.001,"stop":.001-1e-12,"target":.001+1e-12},
                       {"entry":1e9-1,"stop":1e9-2,"target":1e9}):
            for dom in self.render_dom(levels=levels):
                labels=[node["textContent"] for node in self.flatten(dom)
                        if node["tag"]=="text" and node["attributes"].get("class")=="chart-axis"
                        and node["attributes"].get("text-anchor")=="end"]
                self.assertEqual(len(labels),5)
                self.assertEqual(len(set(labels)),5)


if __name__ == "__main__":
    unittest.main()
