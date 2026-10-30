# Software checks

Spectrum Studio 0.6.8 supports local recorded-file review. The Windows build
includes the runtime, scientific readers, native intake, report generator and
application icon. It does not include real recordings or completed private runs.

Verification has covered:

- Calibrated synthetic signals, periodogram normalization, band integration,
  original-time window boundaries and missing-data masks.
- EDF and native WinEEG intake, calibration audits, retained channel types,
  saved scalp positions and file-clock evidence.
- Numerical equality between source and frozen runs for the compared real
  recording selections.
- Playback, linked spectrum/waterfall panels, band inspection, trends, scalp
  controls, review state, saved layouts and interruption handling.
- Native file selection, completed-run restoration and viewer/report/folder
  opening in a normal Windows desktop session.
- Image exports, automatic PNG figures, CSV summaries and PDF report rendering.
- Embedded Windows icons at multiple sizes and isolated one-file launches.

The repository's Python and viewer regression checks use engineering fixtures.
The real recordings used for local verification, their identifying filenames,
hashes, private run locations and internal acceptance logs are not published.

The README screenshots were captured from a separate copy with identifying
metadata removed. They show retained real signal values. Their image files have
no EXIF, XMP, IPTC or comment metadata.

These checks establish tested software behaviour for the exercised inputs and
environment. They do not establish clinical validation, guaranteed frame rates,
measured electrode anatomy, hardware simultaneity or live-amplifier operation.
