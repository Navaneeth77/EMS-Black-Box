"""Launching and supervising the SUMO process.

Two constraints shape this, and both were learned rather than assumed.

**Always launch the resolved binary, never the bare name.** On this machine the
``sumo`` on ``PATH`` is a wrapper that starts the GUI in the background and exits
immediately, so a headless run through it appears to start and then simulates
nothing. ``ems_sim.runner.sumo_env.require_sumo()`` resolves the real path.

**Every launch records its seed.** SUMO's ``--seed`` governs vehicle insertion
jitter and driver-behaviour randomness. A baseline and its counterfactual must
pass the same value or their background traffic differs and the comparison
measures noise rather than policy.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from ems_sim.runner.sumo_env import SumoInstallation, proj_data_dir, require_sumo


class SumoLaunchError(RuntimeError):
    """Raised when SUMO could not be started or did not accept a connection."""


@dataclass
class SumoRunOptions:
    """Command-line options for one SUMO run.

    Defaults are chosen for measurement rather than for a demo. In particular
    ``time_to_teleport`` keeps SUMO's default of 300 s rather than disabling
    teleporting: a teleport is information about gridlock, and switching it off
    replaces a visible symptom with an invisible one — vehicles jammed forever,
    and travel times that never complete.
    """

    net_file: Path
    route_files: tuple[Path, ...]
    begin_s: float = 0.0
    end_s: float = 3900.0
    step_length_s: float = 0.5
    seed: int = 20260910

    time_to_teleport_s: float = 300.0
    collision_action: str = "warn"

    tripinfo_output: Path | None = None
    summary_output: Path | None = None
    vehroute_output: Path | None = None
    queue_output: Path | None = None
    stop_output: Path | None = None
    statistic_output: Path | None = None

    extra: dict[str, str] = field(default_factory=dict)

    def as_command(self, binary: Path, traci_port: int | None = None) -> list[str]:
        command = [
            str(binary),
            "--net-file",
            str(self.net_file),
            "--route-files",
            ",".join(str(r) for r in self.route_files),
            "--begin",
            str(self.begin_s),
            "--end",
            str(self.end_s),
            "--step-length",
            str(self.step_length_s),
            "--seed",
            str(self.seed),
            "--time-to-teleport",
            str(self.time_to_teleport_s),
            "--collision.action",
            self.collision_action,
            # Teleports and departure problems are reported per vehicle rather
            # than only as a total, so each one can be attributed.
            "--no-step-log",
            "true",
            "--duration-log.statistics",
            "true",
            "--xml-validation",
            "never",
            # Without this, vehicles that cannot be inserted are silently
            # discarded and the network looks less loaded than configured.
            "--verbose",
            "true",
        ]
        for option, path in (
            ("--tripinfo-output", self.tripinfo_output),
            ("--summary-output", self.summary_output),
            ("--vehroute-output", self.vehroute_output),
            ("--queue-output", self.queue_output),
            ("--stop-output", self.stop_output),
            ("--statistic-output", self.statistic_output),
        ):
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                command += [option, str(path)]
        for key, value in sorted(self.extra.items()):
            command += [f"--{key}", value]
        if traci_port is not None:
            command += ["--remote-port", str(traci_port)]
        return command


def free_port() -> int:
    """An unused TCP port for TraCI."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def build_sumo_command(
    options: SumoRunOptions,
    traci_port: int | None = None,
    installation: SumoInstallation | None = None,
    gui: bool = False,
) -> list[str]:
    installation = installation or require_sumo()
    binary = installation.binary("sumo-gui" if gui else "sumo")
    return options.as_command(binary, traci_port)


def sumo_environment(installation: SumoInstallation) -> dict[str, str]:
    """Environment for the SUMO subprocess.

    ``SUMO_HOME`` must be exported: without it SUMO falls back to built-in type
    maps and silently disables XML validation.
    """
    environment = {**os.environ, "SUMO_HOME": str(installation.home)}
    proj = proj_data_dir(installation)
    if proj is not None:
        # Silences "pj_obj_create: Cannot find proj.db" and gives SUMO the full
        # transform rather than the reduced fallback.
        environment.setdefault("PROJ_LIB", str(proj))
        environment.setdefault("PROJ_DATA", str(proj))
    return environment


def start_sumo(
    options: SumoRunOptions,
    traci_port: int,
    installation: SumoInstallation | None = None,
    gui: bool = False,
    startup_timeout_s: float = 120.0,
) -> subprocess.Popen[str]:
    """Start SUMO as a bare subprocess, without connecting to it.

    Kept for callers that want to drive SUMO themselves. The TraCI loop does not
    use this: see the note below.

    **Do not probe the TraCI port to test readiness.** SUMO accepts exactly one
    TraCI client; a probe that opens and closes a socket looks like the client
    connecting and disconnecting, and SUMO shuts down. That failure presents as
    "Could not connect in 21 tries" from the real client afterwards, which points
    nowhere near the cause. ``ems_sim.runner.traci_bridge`` uses ``traci.start``,
    which launches and connects atomically and avoids the problem entirely.
    """
    installation = installation or require_sumo()
    command = build_sumo_command(options, traci_port, installation, gui)
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=sumo_environment(installation),
    )

    deadline = time.monotonic() + startup_timeout_s
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _, stderr = process.communicate()
            raise SumoLaunchError(
                f"SUMO exited during startup (code {process.returncode}).\n"
                f"  command: {' '.join(command)}\n  {stderr[-2000:]}"
            )
        time.sleep(0.2)
        return process

    process.kill()
    raise SumoLaunchError(
        f"SUMO did not start within {startup_timeout_s:.0f}s.\n  command: {' '.join(command)}"
    )
