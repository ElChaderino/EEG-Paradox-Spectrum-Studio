# Attribution and source

EEG Paradox Spectrum Studio 0.6.8 is licensed under GNU GPL version 3.
The complete license is in LICENSE. This module is separate from the existing
Decoder, Auto Pipeline, Detection Studio, Source Studio and Topo Studio.

The files assets/ridge.vert and assets/ridge.frag are modified EEG/WebGL2
adaptations of AetherSDR's resources/shaders/dss_mesh.vert and dss_mesh.frag.
AetherSDR is by Jeremy (KK7GWY) and the AetherSDR contributors:
https://github.com/aethersdr/AetherSDR

Source commit: 66d81be20cf50910ff6f57febc338da5f0ae9f43 (retrieved 2026-10-26).
Original file copies, GPL license and per-file Git blob identities are retained
under third_party/aethersdr. SOURCE_MANIFEST.json records direct source URLs.
The original authors' ownership and license are retained.

Changes made here: GLSL 440/Qt QRhi interfaces replaced with GLSL ES 300/WebGL2;
radio dBm replaced with EEG PSD dB relative to 1 uV^2/Hz; radio frequency frames,
slice flags, tile-overhang logic and transmit controls removed; exact original-time
EEG windows and missing-window masks substituted. The receding perspective,
height transfer, depth fade and amplitude-colored curtain design are adapted.

Version 0.2.0 retains the original plot-floor curtain anchoring and uses two
instanced draw passes with depth-tested occlusion and antialiased outline ribbons.
Its waterfall shaders and shared texture/render scheduler are new module code.
Data textures are retained across replay frames; neither the scientific samples
nor spectra are interpolated across time. Linear interpolation between neighboring
frequency bins is display geometry only, with no change to retained arrays.

WaterfallHistoryBuffer.*, WaterfallLevelMap.h, DssRenderer.h and texturedquad.frag
are unmodified reference source, not compiled or executed by this Python module.
The Python PSD engine, shared GPU waterfall, Canvas fallback/waveform renderer and
application controls are implemented for this module. The viewer uses exact-row waterfall display;
it does not apply AetherSDR's cubic temporal reconstruction across EEG gaps.

Version 0.3.0 adds original module code for linked GPU viewports, retained channel
textures, draggable EEG band inspection, original-time event overlays, explicit
scalp montage display, technical quality flags, linear-power A/B review and portable
recording-bound workspaces. These use AetherSDR's display concepts; its radio
noise blankers, de-comb, voice denoisers and channel-strip DSP are not executed.
Template scalp coordinates, when explicitly selected, come from MNE's public
standard_1020 montage and are labelled as templates rather than measured anatomy.

Version 0.5.0 adds original module code for recording inspection/channel selection,
saved-run navigation, cached band-power timelines, calibrated interval CSV/PNG
exports and keyboard/direct-time review controls. No additional upstream DSP is
executed. The numerical PSD estimator and acquired samples remain unchanged.

Version 0.6.0 adds original module code for a named portable layout library,
original-time waterfall clock guides and local allowlisted diagnostics. Their
display concepts were reviewed against AetherSDR's WorkspaceDocument.h,
WaterfallTimeMarkers.h and SupportDialog.cpp; no additional upstream source or
radio DSP is compiled/executed. Clock guides use the recording's original time
and validity masks. Layouts carry display settings without recording events or
acquisition context. Diagnostics contain no EEG or recording identifiers.

Version 0.6.1 hardens saved-run inventory validation, bounds file-hashing memory,
prevents restoring unrelated results into an active batch and corrects inspection
FFT-bin counts. These are original module fixes; no new upstream code or DSP is
included, and the numerical PSD estimator is unchanged.

Version 0.6.2 corrects original module code for playback speed-change continuity
and the sample boundary of whole-recording selections. No new upstream code,
filtering or DSP is included. Original input files are not modified.

Version 0.6.3 fixes interruption of the original module's display-performance
check and restores the recording cursor before image/workspace export. No new
upstream code or scientific processing is included.

Version 0.6.4 corrects original module image captions and unavailable scalp-map
messages/exports. No new upstream code or scientific processing is included.

Version 0.6.5 preserves file-backed timing/layout context from the maintained
WinEEG parser and MNE readers, and explicit signal-file metadata. WinEEG montage
coordinates are schematic display geometry, not measured anatomy. File frame
alignment does not establish hardware simultaneity. No new AetherSDR code or
signal processing is included; the PSD estimator and source recordings are unchanged.

Version 0.6.6 adds original topo channel controls, an expanded map view and
report-image layout fixes. Unpositioned channels stay selectable and are listed
outside the scalp. No new upstream code, electrode positions or EEG processing
is introduced.

Version 0.6.7 fixes original review-cursor/export interactions, makes retained
named scalp-band totals available independently of the spectrum crop, adds a
fixed power-scale fit control and preserves the maintained converter's native
auxiliary channel types. Trend images render at export resolution with correct
text proportions. No new upstream code or EEG processing is introduced.

The HTML export embeds the modified shaders, JavaScript, styles and GPL license.
Keep this source module, notices and license with redistributed derivative work.
There is no claim of AetherSDR endorsement, identical native Qt performance,
clinical validation or connection to EEG acquisition hardware.
