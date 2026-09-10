# Environment

Toolchain observed on the development machine (macOS, Apple Silicon) on
2026-09-09. Recorded because two details here will otherwise cost real time.

## Detected

| Tool | Version | Notes |
|---|---|---|
| Node | 24.10.0 | Fine for Vite 7 |
| npm | 11.6.0 | |
| Python | 3.14.6 (`/opt/homebrew/bin/python3`) | **Not what this project targets** |
| Python | 3.11 (`/opt/homebrew/bin/python3.11`) | **Target interpreter** |
| Python | 3.9.6 (`/usr/bin/python3`) | Apple system Python; do not use |
| SUMO | 1.27.1 | macOS Framework install |
| Docker Engine | 29.5.2 | Daemon running |
| Docker Compose | **missing** | See below |

## Gotcha 1 — `sumo` on your PATH is the GUI

SUMO is installed as a macOS Framework bundle, not via Homebrew:

```
/usr/local/bin/sumo -> /Applications/SUMO sumo-gui.app/Contents/MacOS/SUMO sumo-gui
```

That target is a shell script that launches **sumo-gui** in the background and
returns immediately. Running `sumo -c scenario.sumocfg` from a terminal therefore
appears to succeed, simulates nothing headlessly, and leaves TraCI failing to
connect for reasons that look unrelated to the actual cause.

The installer also does **not** export `SUMO_HOME`.

The real installation is at:

```
/Library/Frameworks/EclipseSUMO.framework/Versions/Current/EclipseSUMO/share/sumo
├── bin/     sumo, sumo-gui, netconvert, netedit, netgenerate,
│            duarouter, jtrrouter, marouter, dfrouter, od2trips,
│            polyconvert, activitygen
└── tools/   traci/, sumolib/, and the Python tool scripts
```

`ems_sim.runner.sumo_env` resolves this and always returns absolute binary paths,
so code that uses it is unaffected. For your own shell:

```bash
export SUMO_HOME="/Library/Frameworks/EclipseSUMO.framework/Versions/Current/EclipseSUMO/share/sumo"
export PATH="$SUMO_HOME/bin:$PATH"
export PYTHONPATH="$SUMO_HOME/tools:$PYTHONPATH"
```

`scripts/check_env.sh` prints the exact lines for this machine.

### traci and sumolib are not pip dependencies

They ship inside `SUMO_HOME/tools` and must match the installed SUMO exactly.
Installing the PyPI copies alongside a different SUMO version produces opaque
TraCI protocol errors, so they are resolved at runtime from `SUMO_HOME` instead
of being pinned in a requirements file.

Verified working: `PYTHONPATH=$SUMO_HOME/tools python3 -c "import traci, sumolib"`.

## Gotcha 2 — target Python 3.11, not 3.14

`python3` on this machine is 3.14.6. The geospatial stack — GeoPandas, Shapely,
pyproj, OSMnx — depends on compiled GEOS/PROJ/GDAL bindings, and wheel
availability lags new interpreter releases. On 3.14 you are likely to hit source
builds that need system GEOS and GDAL, or outright resolution failures.

Python 3.11 is installed and is what `pyproject.toml`, the requirements files and
`scripts/bootstrap.sh` target.

## Gotcha 3 — Docker Compose plugin is not installed

The daemon runs, but the CLI has no `compose` subcommand:

```
$ docker compose version
docker: unknown command: docker compose
```

Nothing depends on Postgres yet, so this blocks nothing today. When you want the
database, install the plugin (`brew install docker-compose`, then ensure
`~/.docker/cli-plugins/docker-compose` exists) or run the image directly:

```bash
docker run -d --name ems-postgis -p 5433:5432 \
  -e POSTGRES_DB=ems_black_box -e POSTGRES_USER=ems -e POSTGRES_PASSWORD=ems_local_dev \
  postgis/postgis:16-3.4
```

## Not yet installed

Nothing in `simulation/requirements.txt` or `analysis/requirements.txt` is
installed. Neither is needed until map ingestion begins.
`scripts/bootstrap.sh` installs the backend and test tooling only, which is what
the current scaffold actually exercises.
