# Changelog

## 0.1.0 (unreleased)

First packaged build.

- HUD shell with dockable panels: scope, thermal, meter and power graph,
  controls, tools, jobs and inventory.
- Scope and thermal cameras. The thermal panel reads raw Y16 as per-pixel °C
  and won't fake a heatmap from an ordinary webcam.
- Thermal fused onto the scope, with registration controls.
- Live OWON SPM8104 power supply and DMM readings, with output control off by
  default and hard limits enforced by the driver.
- Virtual-camera output for OBS: v4l2loopback on Linux, OBS's camera extension
  on macOS.
- On-stream meter readout and power graph, with voltage, current and power in
  scope-channel colours.
- Repair jobs with captures, fault logging and reports; ticket import from an
  intake system; parts use from bench-parts inventory.
- Board-view library that opens boards and matching schematics in OpenBoardView
  or FlexBV.
- Packages: a signed macOS app and an Ubuntu .deb.
