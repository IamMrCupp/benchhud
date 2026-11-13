# Changelog

## [0.12.3](https://github.com/IamMrCupp/benchhud/compare/v0.12.2...v0.12.3) (2026-11-13)


### Performance

* **power:** set the chart's series in one call so the HUD holds its frame rate ([972e28f](https://github.com/IamMrCupp/benchhud/commit/972e28f4dd24f016f2856026391b799138fe26ad))

## [0.12.2](https://github.com/IamMrCupp/benchhud/compare/v0.12.1...v0.12.2) (2026-11-13)


### Bug Fixes

* **thermal:** power-cycle a thermal cam that boots in Recovery Mode ([2eec97e](https://github.com/IamMrCupp/benchhud/commit/2eec97ee89de656b99215d4acf830911960f12d8))

## [0.12.1](https://github.com/IamMrCupp/benchhud/compare/v0.12.0...v0.12.1) (2026-11-13)


### Bug Fixes

* **shell:** guard the wheel per dock, not app-wide, so 0.12.0 stops segfaulting on start ([b09c76e](https://github.com/IamMrCupp/benchhud/commit/b09c76e1a0740541071be6206e0da55bbd2ce0a4))


### Performance

* **shell:** pace the frame loop to output.fps so the HUD leaves CPU for OBS ([d506619](https://github.com/IamMrCupp/benchhud/commit/d506619e868d5e404a879d6f90ef2136b8266030))

## [0.12.0](https://github.com/IamMrCupp/benchhud/compare/v0.11.0...v0.12.0) (2026-11-12)


### Features

* **cameras:** aim USB cameras' pan/tilt/zoom and save shots ([a7dbb94](https://github.com/IamMrCupp/benchhud/commit/a7dbb949063891d7322a380947c54dabffa2f7bf))
* **config:** scriptable 'benchhud config set' and an owner-only config file ([f42d048](https://github.com/IamMrCupp/benchhud/commit/f42d0483eb105b4d5aa5497b93f8896c8efa2466))
* **config:** scriptable 'benchhud config set' and an owner-only config file ([ca83e88](https://github.com/IamMrCupp/benchhud/commit/ca83e8817188efe265620bb331eb36f8b9ad139e))
* **output:** scope-only and thermal-only feeds beside the composite for OBS ([e725ac7](https://github.com/IamMrCupp/benchhud/commit/e725ac7d4e1c3740576e69ad8c834ccbcc7dbf8a))
* **recon:** add the Track E live acceptance harness ([9d1e2df](https://github.com/IamMrCupp/benchhud/commit/9d1e2dff2286480bc84a8d788eee3adc2e340291))
* **recon:** add the Track E live acceptance harness ([eaf6a85](https://github.com/IamMrCupp/benchhud/commit/eaf6a85c489554d639e71e5fae7faaf8760f69e2))


### Bug Fixes

* **bench:** fixed /dev/cam-* names for OBS and name a camera's holder ([155c145](https://github.com/IamMrCupp/benchhud/commit/155c145e879ddb091b0424ce6fa12b64b8e5290b))
* **bench:** fixed /dev/cam-* names for OBS and name a camera's holder ([59d3b81](https://github.com/IamMrCupp/benchhud/commit/59d3b81f782f59837c3071b255b84d8255d279bd))
* **bench:** name cameras by product, not the repeated UVC interface ([2d0c5b3](https://github.com/IamMrCupp/benchhud/commit/2d0c5b39381c2cfde7c37886f2f45ee34653d7b9))
* **capture:** ask an unconfigured scope for 1080p instead of OpenCV's 640x480 ([9c152c2](https://github.com/IamMrCupp/benchhud/commit/9c152c2b51f40cf98d916a62da5ded980686aea4))
* **overlay:** fine thermal zoom that holds, independent of rotation ([1ab3818](https://github.com/IamMrCupp/benchhud/commit/1ab381835e079b452d476ff6ee2b11ad432e8840))
* **packaging:** ship LICENSE and NOTICES inside the macOS app ([1f49039](https://github.com/IamMrCupp/benchhud/commit/1f490393a712da0f17e408f7d0dd5f2c6fa70767))
* **shell:** the mouse wheel scrolls a dock, not the controls it passes over ([87a4f59](https://github.com/IamMrCupp/benchhud/commit/87a4f59da79a8dcf296afe2965a1f401ef0dc8db))
* **terminal:** close inherited fds before exec so a shell job can't hold cameras ([4b0d306](https://github.com/IamMrCupp/benchhud/commit/4b0d306d0d3bb260523f8bc31280ff2d15083a52))


### Performance

* **capture:** decode only the scope frames the HUD takes ([5709c83](https://github.com/IamMrCupp/benchhud/commit/5709c83660af6b2db0939335fb4426086d8defca))
* **overlay:** compose at the size that's shown and blend the thermal in OpenCV ([dae57b8](https://github.com/IamMrCupp/benchhud/commit/dae57b82ec30926d277e1c2322c7e986bf89a14d))
* **overlay:** compose at the size that's shown and blend the thermal in OpenCV ([1802bc8](https://github.com/IamMrCupp/benchhud/commit/1802bc83c76c4e4256181aded291bcc1def97a97))
* **shell:** stop copying every frame to swap its colour channels ([0e844f3](https://github.com/IamMrCupp/benchhud/commit/0e844f377ccf599d7c2caa800ecf6c631934740e))


### Documentation

* **capture:** give the measured 4K cost instead of claiming it can't decode ([a11028a](https://github.com/IamMrCupp/benchhud/commit/a11028ae7780ef339c5d07ad764055d643b5c56b))
* **setup:** document the arp_ignore sysctl for wired + Wi-Fi on one subnet ([ff85f3d](https://github.com/IamMrCupp/benchhud/commit/ff85f3d27e6956dd0ea277b151d7c23391b40a2b))

## [0.11.0](https://github.com/IamMrCupp/benchhud/compare/v0.10.7...v0.11.0) (2026-11-06)


### Features

* **assets:** add the AssetSource seam reading bench-parts' asset registry ([9ac3e19](https://github.com/IamMrCupp/benchhud/commit/9ac3e197dee723c3e3943fe72dbc2d06531a43e9))
* **assets:** AssetSource seam for bench-parts' asset registry ([cfe4f80](https://github.com/IamMrCupp/benchhud/commit/cfe4f804d8c1facc08657841f7b18b7a7f89cc7e))
* **output:** add a tagged-sensitive surface model the compositor enforces ([536b563](https://github.com/IamMrCupp/benchhud/commit/536b56396d96fb555166139cd6573fbab850104c))
* **output:** tagged-sensitive surface model the compositor enforces ([8660136](https://github.com/IamMrCupp/benchhud/commit/86601369e4da7e11a291237bebc2509fd11f12e0))
* **recon:** add the capture/firmware-dump status panel (sensitive) ([1c6590c](https://github.com/IamMrCupp/benchhud/commit/1c6590c2d074645ba384c121b127856c30e9825b))
* **recon:** add the connected-interfaces monitor panel ([69b92d1](https://github.com/IamMrCupp/benchhud/commit/69b92d12dfea7293ad42b133d0cc39110e8243e4))
* **recon:** add the engagement panel (ref-safe projection, off air) ([33da363](https://github.com/IamMrCupp/benchhud/commit/33da363fc28de2b6aa14dd3c3634fca434682dcc))
* **recon:** add the pinout/protocol reference panel (stream-safe) ([2a8ebf3](https://github.com/IamMrCupp/benchhud/commit/2a8ebf39bc4c38ba6db965fcce227f3c71c6bdf2))
* **recon:** add the ReconSource seam mirroring TicketSource ([d942bb9](https://github.com/IamMrCupp/benchhud/commit/d942bb9a3610291c91f95f70712f52891241ee39))
* **recon:** capture/firmware-dump status panel (sensitive) ([537b89c](https://github.com/IamMrCupp/benchhud/commit/537b89c1fb364717af6509540438ca10eb956307))
* **recon:** connected-interfaces monitor panel ([1a10c2c](https://github.com/IamMrCupp/benchhud/commit/1a10c2c2a80e7f09390130bda8737d45b6598e57))
* **recon:** engagement panel (ref-safe projection, off air) ([ab27494](https://github.com/IamMrCupp/benchhud/commit/ab274944bf4dfc11f4d808bcdcd482778545e405))
* **recon:** keep a raw serial console off air (sensitive by default) ([8f7ee89](https://github.com/IamMrCupp/benchhud/commit/8f7ee89c7b03929fd69e6eced9e43ddffbbb0ee8))
* **recon:** keep a raw serial console off air (sensitive by default) ([cd6eaea](https://github.com/IamMrCupp/benchhud/commit/cd6eaea26a9ab0b803cc58557cf8fb5962ea58d2))
* **recon:** pinout/protocol reference panel (stream-safe) ([244f604](https://github.com/IamMrCupp/benchhud/commit/244f604184958b4b5bdca824320d1c93aa1de7f1))
* **recon:** ReconSource seam mirroring TicketSource ([d89e87b](https://github.com/IamMrCupp/benchhud/commit/d89e87bbc14c69e7fe09d2b651098d5ab6ff20ca))

## [0.10.7](https://github.com/IamMrCupp/benchhud/compare/v0.10.6...v0.10.7) (2026-10-17)


### Bug Fixes

* **thermal:** warn when the camera driver passes torn frames on, and document the fix ([c193eab](https://github.com/IamMrCupp/benchhud/commit/c193eaba167864a9136838e53c1a1c29f5aa46f0))

## [0.10.6](https://github.com/IamMrCupp/benchhud/compare/v0.10.5...v0.10.6) (2026-10-16)


### Bug Fixes

* **thermal:** stop the readout and image from resizing the panel every frame ([fed7b8e](https://github.com/IamMrCupp/benchhud/commit/fed7b8e2e3878d42f2b691e702e495e99e1b354e))

## [0.10.5](https://github.com/IamMrCupp/benchhud/compare/v0.10.4...v0.10.5) (2026-10-16)


### Bug Fixes

* **instruments:** read supply setpoints back and report any the supply didn't take ([530771c](https://github.com/IamMrCupp/benchhud/commit/530771c607835687277ca69e5e93dbeab9f96437))
* **shell:** close cleanly on SIGTERM and SIGINT, so the output goes off and settings save ([b19a375](https://github.com/IamMrCupp/benchhud/commit/b19a3750f145f7f0a2cc468c3df2b801dd78e34b))

## [0.10.4](https://github.com/IamMrCupp/benchhud/compare/v0.10.3...v0.10.4) (2026-10-16)


### Bug Fixes

* **theme:** visible check boxes and radio buttons ([d6f77f1](https://github.com/IamMrCupp/benchhud/commit/d6f77f109f2eb8d2c4eb49c59feb9466e8c1761b))

## [0.10.3](https://github.com/IamMrCupp/benchhud/compare/v0.10.2...v0.10.3) (2026-10-16)


### Bug Fixes

* **instruments:** set the DMM mode with FUNCtion:, and match the meter's own mode names in readings ([8751e68](https://github.com/IamMrCupp/benchhud/commit/8751e68f3089edd634253d14accf510ca48523d1))

## [0.10.2](https://github.com/IamMrCupp/benchhud/compare/v0.10.1...v0.10.2) (2026-10-16)


### Bug Fixes

* **instruments:** space OWON commands so none are dropped, and drop the DMM mode picker it ignores ([f3e1bee](https://github.com/IamMrCupp/benchhud/commit/f3e1bee1d904de2c34273e7455616aa3915677e4))
* **thermal:** ride out shutter pauses, back off reconnects, and reset a hung camera's port ([c0a1d8e](https://github.com/IamMrCupp/benchhud/commit/c0a1d8ee9262fde3aab53f813166973817deb207))
* **thermal:** steady panel colours and no marker on a board at room temperature ([1380a5b](https://github.com/IamMrCupp/benchhud/commit/1380a5b13eb76218d357fd14f8d32a956d0aecc4))

## [0.10.1](https://github.com/IamMrCupp/benchhud/compare/v0.10.0...v0.10.1) (2026-10-16)


### Bug Fixes

* **overlay:** a readable, steady hot-spot marker ([7a8f6a1](https://github.com/IamMrCupp/benchhud/commit/7a8f6a1ce29b1fdc47a4061417967ac7d38131f4))
* **thermal:** auto range in the thermal panel, and hide camera nodes that can't stream ([ed69fb6](https://github.com/IamMrCupp/benchhud/commit/ed69fb684fbae3df9c85301ff79bac86124badfe))

## [0.10.0](https://github.com/IamMrCupp/benchhud/compare/v0.9.0...v0.10.0) (2026-10-15)


### Features

* **overlay:** draw the known-good power-on curve on the stream's graph ([e045243](https://github.com/IamMrCupp/benchhud/commit/e04524367ada102ae275ee2697dabe10449f3b7c))


### Bug Fixes

* **prefs:** a switch for instrument telemetry, and the port by its stable name ([efab31c](https://github.com/IamMrCupp/benchhud/commit/efab31c5497f39e721b937bd4c3aa4caa6d62517))

## [0.9.0](https://github.com/IamMrCupp/benchhud/compare/v0.8.0...v0.9.0) (2026-10-15)


### Features

* **flash:** a minipro panel for the T48 ([#254](https://github.com/IamMrCupp/benchhud/pull/254))
* **overlay:** compare the scope with a known-good board's reference ([6031749](https://github.com/IamMrCupp/benchhud/commit/6031749bfb65e6af395cf6c189d7df7afea30a89))
* **overlay:** quarter turns and a rotation-aware fit for a portrait thermal ([79581d8](https://github.com/IamMrCupp/benchhud/commit/79581d8bcf329148b8923820dd28b1c0d2f1e121))


### Bug Fixes

* **capture:** keep cameras by their stable names so they reconnect after a drop ([a3380b8](https://github.com/IamMrCupp/benchhud/commit/a3380b8489a6838d39dda7de7fe99b80282bc34f))

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
