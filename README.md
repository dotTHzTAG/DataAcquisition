# Data Acquisition

Data Acquisition is a Menlo Systems THz acquisition and dotTHz HDF5 project management application.

## Features

- Menlo ScanControl connection and acquisition
- Baseline, reference, and sample waveform capture
- dotTHz project storage with per-measurement HDF5 groups
- Editable measurement metadata and profile management
- HDF5 tree browsing, attribute editing, and measurement removal
- Waveform and frequency-domain dataset plotting

## Installation

Python 3.9 or newer is required.

```powershell
python -m pip install -r requirements.txt
```

## Run

```powershell
python main.py
```

## Build a Release

Install the build dependency once, then create a portable Windows release:

```powershell
python -m pip install "pyinstaller>=6"
python build.py
```

The portable application is written to `release/DataAcquisition/`, with the
executable at `release/DataAcquisition/DataAcquisition.exe`.

Menlo Systems ScanControl must be running and its remote interface must be available for acquisition.

## Project Structure

- `catx/`: models, repositories, services, and ScanControl integration
- `ui/`: PyQt6 controllers and Designer `.ui` files
- `resources/`: application icons
- `tests/`: automated tests
- `projects/`: local dotTHz project files

Release metadata is stored in `version.json`.
