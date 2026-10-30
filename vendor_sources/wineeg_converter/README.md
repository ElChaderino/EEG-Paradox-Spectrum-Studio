# Maintained WinEEG conversion source

This source subset supplies native WinEEG intake for Spectrum Studio. It includes
the binary parser/serializer (`core.py`), conversion service and package metadata.
The standalone app uses its audited intake adapter and retains calibration,
event and quantization information inside the generated run.

This repository does not include the converter's separate desktop application,
recordings, private validation outputs or workstation-specific launch examples.
Use Spectrum Studio's documented file loader and build instructions.
