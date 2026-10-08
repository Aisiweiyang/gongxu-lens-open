# SupplyLens (供需透镜) · v0

An open-source project exploring recycled-material matching, procurement plans, and scenario-based net emission accounting.

**This is v0, the initial public version. Updates and maintenance will happen from time to time as features, documentation, and usability improve.**
Feedback, suggestions, and contributions are welcome.

[简体中文](README.md) | English

![SupplyLens demo](docs/e2e-shots/site-1440.png)

## About

SupplyLens explores whether available recycled-material batches can meet procurement requirements, and how to combine them while understanding costs, transport, missing evidence, and environmental trade-offs.

The current demo uses recycled PP pellets to replace virgin PP in non-food-contact industrial packaging and reusable containers. Demand, supply, and factor inputs produce screening results, alternative plans, reports, and decision passports.

The project uses Python 3.12+, PyYAML, and OR-Tools, with local offline computation and a SQLite-backed pilot workbench.

## Features

- Supply screening: comparable / pending evidence / rejected; missing evidence never silently passes.
- Procurement plans: compare cost, net emission avoidance, and supply concentration, with disruption checks.
- Scenario accounting: inspect calculation inputs, factor ranges, and deterministic sensitivity checks.
- Multi-demand matching: generate suggestions and export matching sheets.
- Pilot workbench: manage demand, supply, evidence, reservations, and delivery feedback, with roles, auditing, and backup/restore.
- Outputs: reports, an offline dashboard, and JSON decision passports.

## Quick start

Install Python 3.12+, download or clone the repository, and open a terminal in the project directory.

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --industry 再生PP --data-mode demo
.\.venv\Scripts\python.exe serve.py --port 8765
```

Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py --industry 再生PP --data-mode demo
.venv/bin/python serve.py --port 8765
```

Open `http://127.0.0.1:8765/` in your browser.
For the pilot workbench, replace `serve.py` in the last command with `pilot/start_pilot.py`. The first visit initializes the administrator account; there is no default password. Run the demo and workbench separately.

## Data and verification

Built-in demo data is fictional. CSV templates are in [templates/](templates/); place local data under `data/input/` and use `--data-mode local`.
Missing prices, distances, or factors are flagged rather than replaced with zero.

Using the virtual environment's Python, install `requirements-test.txt` and run `scripts/check_project.py` for automated tests and independent verification.
See [CONTRIBUTING.md](CONTRIBUTING.md) and [the initial acceptance report](docs/开源准备验收-20261008.md) for details and platform limits.

## Documentation and maintenance

See the [documentation index](docs/INDEX.md) for methods and deployment notes, and the [changelog](CHANGELOG.md) for updates.
**v0 is the first public version.** References to `v10`–`v27.1` are internal iteration numbers from before open sourcing; algorithm and schema versions are separate.
Updates and maintenance will be irregular, with no fixed schedule. Repository records describe actual changes. Documentation is primarily in Chinese.

## Scope

This version is for exploration, demos, and pilot validation. Demo data and assumed factors must not be used for real procurement decisions. Emission figures are scenario estimates, not measured reductions or carbon offsets.
Real use requires verified inputs, test reports, factors, and commercial terms. Report security issues privately as described in [SECURITY.md](SECURITY.md).

## Acknowledgements

Thank you to **秦仲远 (Qin Zhongyuan)** and **杨初墨 (Yang Chumo)** for their assistance with the project.

## License

The project uses the [MIT license](LICENSE). See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for third-party licenses.
