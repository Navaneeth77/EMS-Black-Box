"""OSM extract to SUMO network, via netconvert.

## Why netconvert reads the raw OSM, not the Phase 1 GeoPackage

netconvert's OSM importer is the component that understands OSM semantics:
``layer`` and ``bridge`` tags, ``type=restriction`` relations, ``lanes:forward``
and ``turn:lanes``, junction clustering, and traffic-signal nodes that sit on
approach ways rather than at junction centres. Feeding it the processed
GeoPackage would mean re-encoding all of that into SUMO plain XML by hand —
which is writing a second, worse OSM importer.

So the raw extract is the input, and the Phase 1 tables get a better job: they
become the **independent reference** the conversion is checked against. Two
separate parses of the same bytes — this project's parser and netconvert's —
compared afterwards. That is what catches netconvert silently dropping a
corridor, which a single-parser pipeline cannot notice.

## What is never done here

``--flatten`` removes all z-data. It is not in the configuration, and a guard
rejects it if someone adds it: flattening the Silk Board flyover produces a
network that converts cleanly, simulates cleanly, and lets traffic change level
for free.

## What netconvert necessarily supplies

SUMO cannot simulate an edge without a lane count and a speed. Where OSM has
neither, netconvert applies a default — that is unavoidable, not a choice this
module makes. What *is* a choice is whether the substitution stays visible, so
``--osm.annotate-defaults`` is on: every edge that received a default carries a
parameter saying so. The same applies to traffic-light programs, which
netconvert generates and which are ``ESTIMATED_DATA`` until Phase 4 replaces
them. See ``docs/SUMO_CONVERSION.md``.
"""

from __future__ import annotations

import contextlib
import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ems_sim.network.study_area import StudyArea
from ems_sim.runner.sumo_env import SumoInstallation, require_sumo

# Metres of elevation per OSM layer index for --osm.layer-elevation.
#
# DISABLED (0.0), on measured evidence rather than preference.
#
# Both variants were converted and validated. Topology came out identical:
# 1681 edges, 745 junctions, 12 traffic lights, 39 elevated edges, 47 ramps, and
# - the point of the exercise - 8 plan-view crossings between elevated and ground
# edges with 0 shared junctions in each case.
#
# That is the finding: **grade separation in SUMO is topological, not vertical.**
# The flyover is separate because its ways share no nodes with the ground
# network, which is how OSM encodes it and how netconvert preserves it. The z
# coordinate is presentation.
#
# Enabling it cost something real. netconvert reconstructs elevation by raising
# each layer, and where a layer transition falls on a very short connector edge
# the implied gradient is absurd: 32 edges above 15%, the worst at 1047% (the
# grades scaled linearly with this constant - 2.0 m gave 378%, 5.5 m gave 1047% -
# confirming the cause). Neither --geometry.max-grade.fix nor
# --osm.layer-elevation.max-grade constrained them.
#
# SUMO models grade resistance, so those gradients would not merely look wrong;
# they would change the travel time measured across those edges. Trading a
# corrupted measurement for a cosmetic z is the wrong way round for a project
# whose headline output is travel time.
#
# The 3D view loses nothing: OSM layer data is preserved in the Phase 1 tables,
# every SUMO lane carries its source OSM way ID as an 'origId' parameter, and the
# scene can apply a clean per-layer offset - which is better than netconvert's
# reconstruction artefacts anyway.
#
# Set > 0 to re-enable; the impossible-grade check will fail and say why.
LAYER_ELEVATION_M = 0.0

# Options that would destroy grade separation. Refused rather than trusted.
FORBIDDEN_OPTIONS: frozenset[str] = frozenset({"flatten", "osm.layer-elevation.max-grade.0"})


class ConversionError(RuntimeError):
    """Raised when netconvert fails or is asked to do something unsafe."""


@dataclass(frozen=True)
class NetconvertConfig:
    """A netconvert configuration, serialisable to a committed ``.netccfg``.

    The ``.netccfg`` is the reproducibility artefact: a human can read it, and
    ``netconvert -c <file>`` reruns the exact conversion without going through
    this code at all.
    """

    osm_file: Path
    output_file: Path
    study_area: StudyArea
    seed: int = 42
    layer_elevation_m: float = LAYER_ELEVATION_M

    extra: dict[str, str] = field(default_factory=dict)
    """Overrides merged last. Used by tests and by one-off investigations."""

    def options(self) -> dict[str, str]:
        """The full option set, as netconvert would receive it.

        Grouped by intent, with the reasoning for anything non-obvious. Options
        deliberately *absent* are documented in ``docs/SUMO_CONVERSION.md``;
        the most important is ``--ramps.guess``, left off because this extract
        has 39 real OSM ``*_link`` ways and guessing would add ramps the source
        does not contain.
        """
        box = self.study_area.bbox
        opts: dict[str, str] = {
            # --- input / output ---------------------------------------------
            "osm-files": str(self.osm_file),
            "output-file": str(self.output_file),
            # OSM is in WGS84; UTM keeps the network in metres. netconvert
            # records the projection in the .net.xml <location> element, which
            # is the authority for every later coordinate conversion.
            "proj.utm": "true",
            # --- GRADE SEPARATION (the critical requirement) -----------------
            # Preserved by netconvert keeping the layers' ways as distinct
            # junctions - they share no OSM nodes. Nothing is needed here to
            # obtain it, and --flatten is what would destroy it.
            #
            # --osm.layer-elevation is deliberately NOT set by default; see
            # LAYER_ELEVATION_M for the measurements behind that.
            # --- attribute fidelity ------------------------------------------
            # Import turning arrows so lane-to-lane connections follow OSM
            # rather than netconvert's geometric guesswork.
            "osm.turn-lanes": "true",
            # Mark every edge whose lane count or speed came from a netconvert
            # default. Without this the substitution is invisible and a guessed
            # lane count becomes indistinguishable from a mapped one.
            "osm.annotate-defaults": "true",
            # Keep OSM way IDs and street names in the output. The way IDs are
            # what allow the result to be cross-checked against Phase 1.
            "output.original-names": "true",
            "output.street-names": "true",
            # --- study-area clipping ------------------------------------------
            # The extract overhangs its bbox by ~2.3 km because OSM returns ways
            # whole. Clip to the committed study area so the simulated network
            # is the area the project actually claims to model.
            "keep-edges.in-geo-boundary": (
                f"{box.min_lon},{box.min_lat},{box.max_lon},{box.max_lat}"
            ),
            # Motor-vehicle network only. Footways and cycleways are real but
            # out of scope, and they distort connectivity statistics.
            "keep-edges.by-vclass": "passenger",
            # --- junctions -----------------------------------------------------
            # OSM splits large Indian intersections into several nodes; joining
            # reassembles them into one junction. Clustering requires nodes to be
            # connected by short edges, so ways on different layers - which share
            # no nodes - are not candidates. That is asserted after conversion
            # rather than assumed: see sumo_validate.check_grade_separation.
            "junctions.join": "true",
            "junctions.join-dist": "15",
            "junctions.corner-detail": "5",
            # --- traffic lights ------------------------------------------------
            # Phase 1 established that all 18 traffic_signals nodes in this
            # extract have way-degree 1: OSM puts them on approach ways, set back
            # from the junction. Without guess-signals netconvert would build a
            # traffic light at each of those approach nodes instead of one at the
            # intersection they actually control.
            "tls.guess-signals": "true",
            "tls.guess-signals.dist": "30",
            "tls.join": "true",
            # Raised from netconvert's default of 20 m on measured evidence.
            #
            # At 20 m the Sarjapura Road / Madiwala Sarjapura Road junction came
            # out as FIVE independent traffic lights, with pairs 10.3 m and 10.8 m
            # apart. One physical intersection modelled as five uncoordinated
            # signals would make Phase 5's EMS priority policy actuate five objects
            # the real junction controls as one, and the recovered time it measured
            # would be partly an artefact of that split.
            #
            # 60 m merges them into a single 24-link light. Verified across
            # junctions.join-dist 15-30 and tls.join-dist 20-80: edge and junction
            # counts are unchanged and grade separation still holds (8 plan-view
            # crossings, 0 shared junctions). 80 m was rejected as less clearly
            # correct - it additionally absorbs a single-link Hosur Road signal -
            # and it does not fix the remaining split at Silk Board itself, which
            # needs junction-level joining and is left for human judgement.
            "tls.join-dist": "60",
            # Static programs. netconvert's generated timings are ESTIMATED_DATA
            # and are replaced in Phase 4; static keeps them inspectable in the
            # meantime, where actuated logic would obscure what is assumed.
            "tls.default-type": "static",
            # --- connectivity ---------------------------------------------------
            # Keep only the largest weakly connected component.
            #
            # Justified by measurement, not convention. Converted without this,
            # the network has 7 components: one of 1515 edges and six fragments
            # totalling 166 edges (9.9%, 10.2 km) - 182 edges by netconvert's own
            # count, which includes ones later merged. The fragments are
            # residential and service roads, and all 31 Hosur Road edges, 27 Outer
            # Ring Road edges, 39 elevated edges and 47 ramps sit in the main
            # component.
            #
            # Not everything lost is trivial: one 580 m tertiary way named
            # Sarjapura Road (way/457214835) sits in a pruned fragment. The
            # major_ways_survive_conversion check reports it as a warning rather
            # than letting it disappear quietly - see docs/SUMO_CONVERSION.md.
            #
            # They are residential pockets whose links out were cut by the
            # study-area clip. Left in, they are traps: a vehicle inserted there
            # cannot route anywhere, so it either fails to depart or teleports -
            # and a teleport that touches a measured trip invalidates it.
            #
            # netconvert reports exactly what it removed, and that report is
            # parsed into the conversion record rather than taken on trust.
            "keep-edges.components": "1",
            # --- audit ----------------------------------------------------------
            # Record which junctions were joined into which cluster. Without it,
            # "30 clusters joined" is a number nobody can check.
            "junctions.join-output": f"{self.output_file.stem}.joined-junctions.xml",
            # --- geometry ------------------------------------------------------
            # Merge nodes that only describe edge shape. Topology is unchanged;
            # this removes tens of thousands of geometry-only nodes.
            "geometry.remove": "true",
            # --- determinism ---------------------------------------------------
            "seed": str(self.seed),
            # --- reporting ------------------------------------------------------
            # Not "print-options": it makes netconvert enumerate every option it
            # supports and emit a deprecation warning for each legacy one - 34
            # warnings that have nothing to do with this network, burying the
            # handful that matter. The .netccfg is already the record of options.
            "verbose": "true",
        }
        if self.layer_elevation_m > 0:
            opts["osm.layer-elevation"] = str(self.layer_elevation_m)

        opts.update(self.extra)

        forbidden = FORBIDDEN_OPTIONS & set(opts)
        if forbidden:
            raise ConversionError(
                f"Refusing to run netconvert with {sorted(forbidden)}. "
                "--flatten removes all z-data, which would collapse the Silk Board "
                "flyover into the ground network. The result would convert and "
                "simulate cleanly while being wrong in a way nothing downstream "
                "can detect."
            )
        return opts

    def to_netccfg(self, path: Path, relative_paths: bool = True) -> Path:
        """Write the configuration as a netconvert XML config file.

        Paths are written relative to **the config file's own directory**,
        because that is what netconvert resolves them against — not the working
        directory. Getting this wrong produces a config that works when run from
        one place and reports "No nodes loaded" from another.

        Relative rather than absolute so the committed file is portable and
        reviewable in a diff.
        """
        opts = dict(self.options())
        if relative_paths:
            base = path.parent.resolve()
            for key in ("osm-files", "output-file"):
                if key in opts:
                    # os.path.relpath, not Path.relative_to: the targets sit
                    # outside the config's directory and need "..".
                    with contextlib.suppress(ValueError):
                        opts[key] = os.path.relpath(Path(opts[key]).resolve(), base)

        input_keys = {"osm-files"}
        output_keys = {"output-file"}
        lines = [
            "<?xml version='1.0' encoding='UTF-8'?>",
            "",
            "<!--",
            "  EMS Black Box - netconvert configuration for the Silk Board study area.",
            "",
            "  Generated by ems_sim.network.sumo_convert. COMMITTED as an input: this",
            "  file is the recipe that makes the conversion reproducible. Rerun with",
            "",
            f"      $SUMO_HOME/bin/netconvert -c {path.name}",
            "",
            "  The 'flatten' option must never appear here. It removes all z-data",
            "  and would collapse the flyover into the ground network. (Written",
            "  without its leading dashes because XML comments cannot contain them.)",
            "-->",
            "",
            "<configuration>",
            "    <input>",
        ]
        for key in sorted(k for k in opts if k in input_keys):
            lines.append(f'        <{key} value="{opts[key]}"/>')
        lines.append("    </input>")
        lines.append("")
        lines.append("    <output>")
        for key in sorted(k for k in opts if k in output_keys):
            lines.append(f'        <{key} value="{opts[key]}"/>')
        lines.append("    </output>")
        lines.append("")
        lines.append("    <processing>")
        for key in sorted(k for k in opts if k not in input_keys | output_keys):
            lines.append(f'        <{key} value="{opts[key]}"/>')
        lines.append("    </processing>")
        lines.append("</configuration>")

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


@dataclass
class ConversionResult:
    """What one netconvert run produced, including everything it complained about."""

    net_file: Path
    config_file: Path
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    netconvert_version: str = ""
    statistics: dict[str, str] = field(default_factory=dict)
    """netconvert's own summary lines - what it loaded, joined and removed."""

    @property
    def warning_categories(self) -> dict[str, int]:
        """Warnings grouped by kind, so they can be investigated rather than
        scrolled past.

        netconvert emits one line per affected element, so a single systematic
        issue can produce thousands of lines. Grouping is what makes the
        difference between "1,400 warnings" and "one cause, 1,400 times".
        """
        counter: Counter[str] = Counter()
        for warning in self.warnings:
            counter[_categorise_warning(warning)] += 1
        return dict(counter.most_common())


# Patterns map a netconvert warning to a short, stable category name. The
# categories are what the validation report and the docs discuss.
_WARNING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Turn restrictions netconvert could not apply. Each one is a real turning
    # movement that stays legal in the network but is illegal on the ground.
    ("turn_restriction_ignored", re.compile(r"restriction relation", re.I)),
    # Signal nodes whose controlled edges were clipped away or filtered out.
    # Worth checking against the intersections the study actually cares about.
    ("tls_not_built", re.compile(r"traffic light.*does not control any links", re.I)),
    ("tls_program_failed", re.compile(r"[Cc]ould not build program", re.I)),
    # Non-road OSM types (waterways, power lines, proposed rail). Expected.
    ("non_road_type_discarded", re.compile(r"[Dd]iscarding unusable type", re.I)),
    # Public-transport stops, out of scope while the network is passenger-only.
    ("pt_stop_dropped", re.compile(r"pt stop", re.I)),
    # Geometry worth a human look: sharp angles and overlapping turn paths.
    ("sharp_angle", re.compile(r"Found angle of", re.I)),
    ("intersecting_turns", re.compile(r"Intersecting .*turns", re.I)),
    ("environment", re.compile(r"SUMO_HOME", re.I)),
    ("summary_line", re.compile(r"total messages of type", re.I)),
    ("edge_too_short", re.compile(r"is not (?:long|wide) enough|too short", re.I)),
    ("connection_discarded", re.compile(r"connection.*(?:discard|remov|not build)", re.I)),
    ("junction_join", re.compile(r"join(?:ing|ed)? (?:of )?junction|cluster", re.I)),
    ("shape_geometry", re.compile(r"shape|geometry|overlap|self-intersect", re.I)),
    ("deprecated_option", re.compile(r"is deprecated", re.I)),
)


def _categorise_warning(warning: str) -> str:
    for name, pattern in _WARNING_PATTERNS:
        if pattern.search(warning):
            return name
    return "other"


_STATISTIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("osm_nodes_loaded", re.compile(r"(\d+) nodes loaded")),
    ("osm_edges_loaded", re.compile(r"(\d+) edges loaded")),
    ("geometry_nodes_removed", re.compile(r"(\d+) nodes removed")),
    ("components_found", re.compile(r"Found (\d+) components")),
    ("components_removed", re.compile(r"Found \d+ components and removed (\d+)")),
    ("component_edges_removed", re.compile(r"components and removed \d+ \((\d+) edges\)")),
    ("junctions_joined", re.compile(r"Joined (\d+) junction cluster")),
)


def _parse_statistics(stdout: str) -> dict[str, str]:
    """Pull netconvert's own summary numbers out of its verbose output.

    These are netconvert reporting on itself - how many components it found and
    removed, how many junctions it joined. Recording them means the conversion's
    effects are auditable from the run itself rather than from a number someone
    measured once and wrote into a comment.
    """
    found: dict[str, str] = {}
    for name, pattern in _STATISTIC_PATTERNS:
        match = pattern.search(stdout)
        if match:
            found[name] = match.group(1)
    return found


def run_netconvert(
    config: NetconvertConfig,
    config_file: Path,
    repo_root: Path,
    installation: SumoInstallation | None = None,
) -> ConversionResult:
    """Write the ``.netccfg`` and run netconvert from it.

    The binary is always the resolved absolute path from
    ``ems_sim.runner.sumo_env``. Never the bare name: on macOS the ``sumo`` on
    ``PATH`` is a wrapper that launches the GUI and returns immediately, and
    while ``netconvert`` itself is not symlinked that way, relying on ``PATH``
    for one tool and not the other is how that trap gets re-set later.
    """
    installation = installation or require_sumo()
    binary = installation.binary("netconvert")

    config.to_netccfg(config_file)
    config.output_file.parent.mkdir(parents=True, exist_ok=True)

    # netconvert needs SUMO_HOME to find its type maps and XML schemas. The
    # macOS Framework installer does not export it, so without this netconvert
    # falls back to built-in type maps AND silently disables XML validation -
    # it says so in a warning that is easy to scroll past.
    env = {**os.environ, "SUMO_HOME": str(installation.home)}

    command = [str(binary), "-c", str(config_file)]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        cwd=repo_root,
        env=env,
        check=False,
    )

    combined = completed.stdout + "\n" + completed.stderr
    warnings = [
        line.strip() for line in combined.splitlines() if line.strip().startswith("Warning:")
    ]
    errors = [line.strip() for line in combined.splitlines() if line.strip().startswith("Error:")]

    statistics = _parse_statistics(completed.stdout)

    version = ""
    try:
        version_out = subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True, timeout=15, check=False
        )
        first = version_out.stdout.splitlines()[0] if version_out.stdout else ""
        match = re.search(r"\d+\.\d+(?:\.\d+)?", first)
        version = match.group(0) if match else first.strip()
    except (OSError, subprocess.SubprocessError):
        pass

    result = ConversionResult(
        net_file=config.output_file,
        config_file=config_file,
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        warnings=warnings,
        errors=errors,
        netconvert_version=version,
        statistics=statistics,
    )

    if completed.returncode != 0 or not config.output_file.is_file():
        detail = "\n  ".join(errors[:10]) or completed.stderr[-2000:]
        raise ConversionError(
            f"netconvert failed (exit {completed.returncode}). No network was written.\n"
            f"  command: {' '.join(command)}\n  {detail}"
        )

    return result
