"""Create editable local tournament scenes; never starts OBS or a server."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from obs_director.tournament import TournamentValidationError, demo_snapshot, read_snapshot, render_tournament


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot", type=Path, help="Local obs.tournament.v1 JSON; timestamps are preserved")
    source.add_argument("--demo", action="store_true", help="Create explicit synthetic values with the current creation time")
    parser.add_argument("--template", choices=("starter", "piphound"), default="starter",
                        help="Neutral starter (default) or the illustrated PipHound showcase")
    parser.add_argument("--output", type=Path, required=True, help="New output directory; parent must exist")
    parser.add_argument("--motion", action="store_true", help="Add decorative gold motion loops to intermission pages")
    parser.add_argument("--demo-roster", action="store_true", help="Use six fictional bot seats, including a tie and a missing result; requires --demo")
    args = parser.parse_args(argv)
    if args.demo_roster and not args.demo:
        parser.error("--demo-roster requires --demo")
    try:
        snapshot = demo_snapshot() if args.demo else read_snapshot(args.snapshot)
        if args.demo and args.template == "starter":
            snapshot["session_id"] = "starter-demo-01"
            snapshot["label"] = "The session"
            for participant, identifier in zip(snapshot["participants"], ("host", "guest")):
                participant["id"] = identifier
                participant["name"] = identifier.upper()
        if args.demo_roster:
            snapshot["session_id"] = "bot-roster-demo-01"
            snapshot["label"] = "Bot tournament"
            seats = []
            for index, (name, net) in enumerate(zip(("ORBIT", "RIVET", "LANTERN", "MARBLE", "FLINT", "NOVA"),
                                                   (1268500, 924550, 340000, 340000, -82500, None))):
                row = deepcopy(snapshot["participants"][index % 2])
                row.update(id="bot-" + str(index + 1), name=name, realized_minor=None if net is None else net + 1250,
                           unrealized_minor=0, fees_minor=1250)
                if net is None:
                    row.update(observed_at=None, status="paused")
                    row.pop("trade", None)
                seats.append(row)
            snapshot["participants"] = seats
        portrait = ROOT / "examples" / "funded-desk" / "presenter.png" if args.demo and args.template == "piphound" else None
        template_name = "starter" if args.template == "starter" else "tournament"
        receipt = render_tournament(snapshot, args.output, template_dir=ROOT / "examples" / template_name,
                                    portrait_path=portrait,
                                    motion_dir=ROOT / "examples" / "motion" if args.motion else None)
    except (TournamentValidationError, OSError) as error:
        parser.exit(2, f"Tournament rendering failed: {type(error).__name__}\n")
    print(json.dumps(receipt, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
