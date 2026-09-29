# Changelog

## [0.3.0](https://github.com/IamMrCupp/benchhud/compare/v0.2.0...v0.3.0) (2026-09-29)


### Features

* **meter:** supply controls behind the safety rules: setpoints, confirmed output on, DMM mode ([ebfdf11](https://github.com/IamMrCupp/benchhud/commit/ebfdf1108db41fea4cb0519d3e44ca60a25e2133))
* **terminal:** serial consoles: a raw port, and the OWON's query console ([c0e5ea9](https://github.com/IamMrCupp/benchhud/commit/c0e5ea90a1d013d2c9e51e42c11306f7e85de998))


### Documentation

* **config:** add the missing [inventory] block to config.example.toml ([72487fc](https://github.com/IamMrCupp/benchhud/commit/72487fccc0fc4022b457d36182120d06ace58d1f))
* **config:** add the missing inventory block to the example ([c74ad9e](https://github.com/IamMrCupp/benchhud/commit/c74ad9ee891f5d9587a4cba64f7a31436b54b295))
* **packaging:** recreate the Mac bundle for a release build ([9533247](https://github.com/IamMrCupp/benchhud/commit/953324752cbd8f78b14838658666d2f5a2461e5b))
* **packaging:** the Windows MSI, what CI proves and what it can't ([bd3993d](https://github.com/IamMrCupp/benchhud/commit/bd3993d6327b0ea589e0839477b3e509b1af8b57))

## [0.2.0](https://github.com/IamMrCupp/benchhud/compare/v0.1.0...v0.2.0) (2026-09-25)


### Features

* **terminal:** in-app terminal panel, shareable to OBS read-only ([f8276dc](https://github.com/IamMrCupp/benchhud/commit/f8276dc4f6c1e860130a0c7e470249d400a36515))


### Bug Fixes

* **packaging:** add the two libraries QtWebEngine still lacked in the .deb ([e0313d3](https://github.com/IamMrCupp/benchhud/commit/e0313d3c3b40e3b17e37beaac1a3f4d24541ee36))

## 0.1.0 (2026-09-23)

First packaged build. Releases after this one are cut by release-please from
conventional commits.

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
- OBS browser sources: the meter, power graph and job card each served as a
  transparent page on localhost.
- Repair jobs with captures, fault logging and reports; ticket import from an
  intake system; parts use from bench-parts inventory.
- Board-view library that opens boards and matching schematics in OpenBoardView
  or FlexBV.
- Packages: a signed macOS app and an Ubuntu .deb.
