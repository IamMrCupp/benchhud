# Changelog

## [0.8.0](https://github.com/IamMrCupp/benchhud/compare/v0.7.0...v0.8.0) (2026-10-15)


### Features

* **sources:** a now-playing banner for the stream ([f458ba5](https://github.com/IamMrCupp/benchhud/commit/f458ba5256f0f97c243b8d9d2d048cce61b23b7d))

## [0.7.0](https://github.com/IamMrCupp/benchhud/compare/v0.6.0...v0.7.0) (2026-10-15)


### Features

* **capture:** pick the scope camera's resolution ([1c8209b](https://github.com/IamMrCupp/benchhud/commit/1c8209b5e38400896b1ec017794598e4a9f83869))
* **overlay:** readable thermal overlays with a heat-only blend, auto range and outlines ([576c4b6](https://github.com/IamMrCupp/benchhud/commit/576c4b66d6777725d90ea3a273da736670215ca6))
* **overlay:** two-point thermal calibration with a hot tip ([debb968](https://github.com/IamMrCupp/benchhud/commit/debb968c8be102ebd8586fb5a3393682bf0bec98))

## [0.6.0](https://github.com/IamMrCupp/benchhud/compare/v0.5.0...v0.6.0) (2026-10-15)


### Features

* **power:** record power-on current signatures and compare with a known-good board ([6e09066](https://github.com/IamMrCupp/benchhud/commit/6e09066de3eb808119485d4d8d19e8f88cbe68fd))


### Bug Fixes

* **output:** say why the loopback device is missing, and offer the ones that exist ([c724ff1](https://github.com/IamMrCupp/benchhud/commit/c724ff1618373d0b9fc598e67b443b3f13617167))

## [0.5.0](https://github.com/IamMrCupp/benchhud/compare/v0.4.1...v0.5.0) (2026-10-14)


### Features

* **flash:** a flashrom panel for backing up and writing SPI chips ([1e3917a](https://github.com/IamMrCupp/benchhud/commit/1e3917a0d3aa0a1ee00e234e8b78f28afd48c449))
* **readings:** test-point readings compared with a known-good board ([2a81b8e](https://github.com/IamMrCupp/benchhud/commit/2a81b8e73d48e9e208987efeb7a57add255f9d49))

## [0.4.1](https://github.com/IamMrCupp/benchhud/compare/v0.4.0...v0.4.1) (2026-10-13)


### Bug Fixes

* **ui:** fit every panel in a narrow dock and stop clipping text ([2687449](https://github.com/IamMrCupp/benchhud/commit/26874493de524f41e02973229e6be5acfba0981a))

## [0.4.0](https://github.com/IamMrCupp/benchhud/compare/v0.3.1...v0.4.0) (2026-10-13)


### Features

* **tools:** open command-line tools in the terminal, GUI tools as windows ([e662a05](https://github.com/IamMrCupp/benchhud/commit/e662a05cfcaf68943ee08bfd3874c63156edd207))

## [0.3.1](https://github.com/IamMrCupp/benchhud/compare/v0.3.0...v0.3.1) (2026-10-12)


### Documentation

* **readme:** an Install section, and a current status note ([e7d3e74](https://github.com/IamMrCupp/benchhud/commit/e7d3e74b3a95d443bd6c70af1ef8499957569d7f))

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
