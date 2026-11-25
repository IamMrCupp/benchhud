# Changelog

## [0.36.0](https://github.com/IamMrCupp/benchhud/compare/v0.35.0...v0.36.0) (2026-11-25)


### Features

* **updates:** say when a newer release is out, with Help → Check for updates ([cfc0945](https://github.com/IamMrCupp/benchhud/commit/cfc0945bc36b1b0edd00f8ca5d56dc6c83bbdba9))
* **updates:** say when a newer release is out, with Help → Check for updates ([f2a5eaf](https://github.com/IamMrCupp/benchhud/commit/f2a5eafbcb0f99aedfbe5727d872594ff8cf2b4a))
* **updates:** say when a newer release is out, with Help → Check for updates ([3ce5f90](https://github.com/IamMrCupp/benchhud/commit/3ce5f9053465a1802bd9ddf85a52bb205a9b1b39))

## [0.35.0](https://github.com/IamMrCupp/benchhud/compare/v0.34.3...v0.35.0) (2026-11-25)


### Features

* **chips:** find a part number from its first three characters ([66c3c1e](https://github.com/IamMrCupp/benchhud/commit/66c3c1e8dbedb94ecc39184604728bfcd6ae9974))
* **chips:** find a part number from its first three characters ([9baeec5](https://github.com/IamMrCupp/benchhud/commit/9baeec56e579e8686d468d38092c4bc7e8736371))
* **chips:** find a part number from its first three characters ([584296e](https://github.com/IamMrCupp/benchhud/commit/584296eab36cc0d152118e9afaebbba24cf7e652))
* **chips:** find a part number from its first three characters ([ebcecbc](https://github.com/IamMrCupp/benchhud/commit/ebcecbc9d3980345aba32bde2d55b5708d99a7ef))
* **chips:** find a part number from its first three characters ([88b244d](https://github.com/IamMrCupp/benchhud/commit/88b244d5e0e4fd5ee90f91564762c19d6e1d18c6))


### Documentation

* **readme:** add APPEARANCE.md to the docs table ([75cc729](https://github.com/IamMrCupp/benchhud/commit/75cc729a4e0ff696bf58db71c287bfd3aa444e8e))
* **readme:** keep device names out of the HUD picture, and clean up after the picture script ([1ebe0aa](https://github.com/IamMrCupp/benchhud/commit/1ebe0aac3f1cd3dd88b8ef287775bdc2401cfca4))
* **readme:** keep device names out of the HUD picture, and clean up after the picture script ([a5e213c](https://github.com/IamMrCupp/benchhud/commit/a5e213c63af745e1cd8f0fa4f758917c024aec67))
* **readme:** keep device names out of the HUD picture, and clean up after the picture script ([78f5efd](https://github.com/IamMrCupp/benchhud/commit/78f5efdece3591160eb686aa15d94df3502ae05d))
* **readme:** retake the HUD picture and add the thermal modes, meter and jobs, rendered from demo data ([1ebe0aa](https://github.com/IamMrCupp/benchhud/commit/1ebe0aac3f1cd3dd88b8ef287775bdc2401cfca4))
* **readme:** retake the HUD picture and add the thermal modes, meter and jobs, rendered from demo data ([a5e213c](https://github.com/IamMrCupp/benchhud/commit/a5e213c63af745e1cd8f0fa4f758917c024aec67))
* **readme:** retake the HUD picture and add the thermal modes, meter and jobs, rendered from demo data ([78f5efd](https://github.com/IamMrCupp/benchhud/commit/78f5efdece3591160eb686aa15d94df3502ae05d))

## [0.34.3](https://github.com/IamMrCupp/benchhud/compare/v0.34.2...v0.34.3) (2026-11-25)


### Documentation

* **readme:** add the parts inventory and recon panels to the feature list ([fcecb39](https://github.com/IamMrCupp/benchhud/commit/fcecb39e4209cf57d5522cfe2b6e7410e017e115))

## [0.34.2](https://github.com/IamMrCupp/benchhud/compare/v0.34.1...v0.34.2) (2026-11-25)


### Bug Fixes

* **meter:** a command that takes the session down still lets the Output OFF presses behind it run ([e6bb28c](https://github.com/IamMrCupp/benchhud/commit/e6bb28ca5306dd5f29dbcafd4d85f930f034a253))
* **meter:** a done-callback that raises can't cut the session's last drain short either ([71a4126](https://github.com/IamMrCupp/benchhud/commit/71a412658de3de79ee6dfb4f129d3fefb011ac78))

## [0.34.1](https://github.com/IamMrCupp/benchhud/compare/v0.34.0...v0.34.1) (2026-11-25)


### Bug Fixes

* **meter:** the session thread lets go of the port even when its shutdown is cut short ([1ebf8e5](https://github.com/IamMrCupp/benchhud/commit/1ebf8e539337b9e2add96550804aa12b9531e01c))

## [0.34.0](https://github.com/IamMrCupp/benchhud/compare/v0.33.0...v0.34.0) (2026-11-24)


### Features

* **sources:** a now-playing title too long for the banner scrolls ([5de0521](https://github.com/IamMrCupp/benchhud/commit/5de052121721825b6de38a6dd2cf138e254f6515))


### Bug Fixes

* **instruments:** a session stopped mid-command drops the output before closing the port ([52f533b](https://github.com/IamMrCupp/benchhud/commit/52f533b74fac525a40a0aeafef5ae2feedd0ab13))
* **meter:** quitting and restarting wait for the last session to let go of the port ([eeaff9b](https://github.com/IamMrCupp/benchhud/commit/eeaff9b714d9f8aa99b01c06ce68906042f254e8))

## [0.33.0](https://github.com/IamMrCupp/benchhud/compare/v0.32.5...v0.33.0) (2026-11-24)


### Features

* **shell:** add benchhud kwin-rule to open the window on the big display ([2989a6f](https://github.com/IamMrCupp/benchhud/commit/2989a6f44ab53b525a4d32c26ea4f9102bdcf230))


### Bug Fixes

* **instruments:** a stopping session runs nothing that was still queued ([f773f83](https://github.com/IamMrCupp/benchhud/commit/f773f839aa88dbb5b466e4ab9f6898b4b0f14e86))
* **meter:** a supply result from a stopped session isn't shown ([0d55526](https://github.com/IamMrCupp/benchhud/commit/0d555268d2781f15c57f6ae37b8603cce7149a03))
* **meter:** the supply controls say when the instrument stops answering ([e313288](https://github.com/IamMrCupp/benchhud/commit/e313288c86189969816688a3959eee88890ae7b0))

## [0.32.5](https://github.com/IamMrCupp/benchhud/compare/v0.32.4...v0.32.5) (2026-11-24)


### Bug Fixes

* **meter:** the supply controls say ready once an instrument connects ([17f0949](https://github.com/IamMrCupp/benchhud/commit/17f0949257d588be7fbe22e916aa62973e9a68a9))

## [0.32.4](https://github.com/IamMrCupp/benchhud/compare/v0.32.3...v0.32.4) (2026-11-23)


### Bug Fixes

* **browser-sources:** a page refused while the panels went off asks again once they're back ([df02aeb](https://github.com/IamMrCupp/benchhud/commit/df02aebdccb4a905273a6a2a2bf483b5819023cf))
* **browser-sources:** a page whose feed answered with an error opens it again ([a21387c](https://github.com/IamMrCupp/benchhud/commit/a21387c1c4092fe3bdd8221529ebb9a28e1452c6))

## [0.32.3](https://github.com/IamMrCupp/benchhud/compare/v0.32.2...v0.32.3) (2026-11-22)


### Bug Fixes

* **browser-sources:** a stale page gives up its feed before it checks, so six open pages can all reload ([c9c1232](https://github.com/IamMrCupp/benchhud/commit/c9c123212b06428875411bb1c8a403a586f5ee8e))
* **browser-sources:** pages left open in OBS reload themselves after an upgrade ([c9c1232](https://github.com/IamMrCupp/benchhud/commit/c9c123212b06428875411bb1c8a403a586f5ee8e))


### Documentation

* **readme:** add pictures of the stream view and Find chip ([ddf31e6](https://github.com/IamMrCupp/benchhud/commit/ddf31e61d6f3ea57505cb5690a31641d8a8fdf86))
* **readme:** list Windows for the terminal, and add the OBS scenes and benchmark docs to the table ([ddf31e6](https://github.com/IamMrCupp/benchhud/commit/ddf31e61d6f3ea57505cb5690a31641d8a8fdf86))
* **readme:** say what in the stream picture is OBS's, and show the job card's credit ([ddf31e6](https://github.com/IamMrCupp/benchhud/commit/ddf31e61d6f3ea57505cb5690a31641d8a8fdf86))

## [0.32.2](https://github.com/IamMrCupp/benchhud/compare/v0.32.1...v0.32.2) (2026-11-22)


### Bug Fixes

* **shell:** flat dropdown and spin box arrows in the look's colours ([577dd98](https://github.com/IamMrCupp/benchhud/commit/577dd9841a71f58b1667d2320846205a21f0da7d))

## [0.32.1](https://github.com/IamMrCupp/benchhud/compare/v0.32.0...v0.32.1) (2026-11-22)


### Bug Fixes

* **chips:** the inventory row re-reads its sources after a Preferences OK and keeps no text once hidden ([2fc306b](https://github.com/IamMrCupp/benchhud/commit/2fc306ba21ced7b635eb5c93d87fcb2a348ad027))
* **chips:** the inventory row reads the on-air chip sources only, like the stream card ([2fc306b](https://github.com/IamMrCupp/benchhud/commit/2fc306ba21ced7b635eb5c93d87fcb2a348ad027))

## [0.32.0](https://github.com/IamMrCupp/benchhud/compare/v0.31.0...v0.32.0) (2026-11-22)


### Features

* **shell:** light, dark and custom themes ([090bcd3](https://github.com/IamMrCupp/benchhud/commit/090bcd350830d0cae65a2f85707308aca8bfbbc7))
* **shell:** light, dark and custom themes ([04ba4e1](https://github.com/IamMrCupp/benchhud/commit/04ba4e13f51a6a7b7fd2ebad2720f33884506eb7))

## [0.31.0](https://github.com/IamMrCupp/benchhud/compare/v0.30.0...v0.31.0) (2026-11-22)


### Features

* **chips:** keep a chip source off the stream card with on_air = false ([2f87a8a](https://github.com/IamMrCupp/benchhud/commit/2f87a8ada1853bd3d5a8745f26410b0ff43b0452))


### Bug Fixes

* **chips:** a misspelt or misplaced on_air keeps the source off the stream card ([ee9c9db](https://github.com/IamMrCupp/benchhud/commit/ee9c9db6a2a96a2fad159fbe913e463a273f611d))

## [0.30.0](https://github.com/IamMrCupp/benchhud/compare/v0.29.1...v0.30.0) (2026-11-22)


### Features

* **chips:** put a chip on the stream card from Find chip or an inventory row ([c50ba86](https://github.com/IamMrCupp/benchhud/commit/c50ba86622fbaf3aa18b744f25652f598bce8f37))
* **recon:** end the active engagement from the Engagement panel ([5e8f71a](https://github.com/IamMrCupp/benchhud/commit/5e8f71a77e005249c5b7fad68212186bac90cbac))


### Bug Fixes

* **chips:** the inventory row's stream-card button takes the mouse only, and sending never takes the keyboard ([0658474](https://github.com/IamMrCupp/benchhud/commit/0658474de4b3bf2b814b2019993e5f28eecf35a1))
* **jobs:** restore the selected job after a restart ([52f8ae4](https://github.com/IamMrCupp/benchhud/commit/52f8ae487780e5de2ba9bab7c1c7abad20dd6f6a))

## [0.29.1](https://github.com/IamMrCupp/benchhud/compare/v0.29.0...v0.29.1) (2026-11-22)


### Bug Fixes

* **macos:** staple the notarization ticket to the app inside the .dmg ([1658069](https://github.com/IamMrCupp/benchhud/commit/1658069c6082ec8ac545b9d86ccfe5d8534098a7))

## [0.29.0](https://github.com/IamMrCupp/benchhud/compare/v0.28.0...v0.29.0) (2026-11-22)


### Features

* **chips:** open Find chip from a Chips menu, with which source said what and a guide to all of it ([29aa966](https://github.com/IamMrCupp/benchhud/commit/29aa966e61d9e380fc52101b056120fc5f98f794))
* **job-card:** a small benchhud credit in the card's corner ([#494](https://github.com/IamMrCupp/benchhud/issues/494)) ([3728080](https://github.com/IamMrCupp/benchhud/commit/3728080d63e69d3baac60e8eae15afa4f8c0955a))


### Bug Fixes

* **chips:** Find chip drops an old source's rows and failures at once, and re-reads after a Preferences OK ([53ec05b](https://github.com/IamMrCupp/benchhud/commit/53ec05b41b0693650587a1414f36ca6485bdcf67))

## [0.28.0](https://github.com/IamMrCupp/benchhud/compare/v0.27.0...v0.28.0) (2026-11-22)


### Features

* **chips:** look a mark up on the marking sites, or open a document's maker page, from Find chip ([b2cae43](https://github.com/IamMrCupp/benchhud/commit/b2cae432941ca5f7b624511b8030c2f27a50db41))


### Bug Fixes

* **chips:** Find chip's link-outs refuse look-alike hosts, take the mouse only, and say the lock first ([b2cae43](https://github.com/IamMrCupp/benchhud/commit/b2cae432941ca5f7b624511b8030c2f27a50db41))
* **config:** keep 0/1 switches and fractions working, log a dropped value, and write the file in one step ([0089f0d](https://github.com/IamMrCupp/benchhud/commit/0089f0d9ac80c11fe92e4dc48a4bfcc33992332c))
* **config:** start on defaults when the config can't be read, and never save one that can't ([0089f0d](https://github.com/IamMrCupp/benchhud/commit/0089f0d9ac80c11fe92e4dc48a4bfcc33992332c))

## [0.27.0](https://github.com/IamMrCupp/benchhud/compare/v0.26.0...v0.27.0) (2026-11-22)


### Features

* **chips:** show a chip from the chip reference on a stream-safe panel ([ecece28](https://github.com/IamMrCupp/benchhud/commit/ecece28ca6611490300171822e8b69334eec13c8))
* **inventory:** show a part's chip details and open its datasheet from the row ([5fc2d41](https://github.com/IamMrCupp/benchhud/commit/5fc2d41571caf68a80f2656466c9faf9d25fbe99))


### Bug Fixes

* **chips:** the chip panel re-reads before a refresh, wraps a long part number, and drops invisible characters ([ecece28](https://github.com/IamMrCupp/benchhud/commit/ecece28ca6611490300171822e8b69334eec13c8))
* **inventory:** give the datasheet line its own row so it isn't cut off in a narrow dock ([5fc2d41](https://github.com/IamMrCupp/benchhud/commit/5fc2d41571caf68a80f2656466c9faf9d25fbe99))
* **inventory:** never name a local file on the chip row, and keep long names from widening the dock ([5fc2d41](https://github.com/IamMrCupp/benchhud/commit/5fc2d41571caf68a80f2656466c9faf9d25fbe99))

## [0.26.0](https://github.com/IamMrCupp/benchhud/compare/v0.25.0...v0.26.0) (2026-11-22)


### Features

* **chips:** look a chip up by its mark or part number in a Find chip dialog ([bead5aa](https://github.com/IamMrCupp/benchhud/commit/bead5aaa24127d4ab9a78bb3b90bc7a3e4081680))


### Bug Fixes

* **chips:** Find chip's keys never act on a list that's behind the box, and chip text stays out of tooltips ([bead5aa](https://github.com/IamMrCupp/benchhud/commit/bead5aaa24127d4ab9a78bb3b90bc7a3e4081680))

## [0.25.0](https://github.com/IamMrCupp/benchhud/compare/v0.24.0...v0.25.0) (2026-11-22)


### Features

* **chips:** add the [chips] config and a Preferences tab for chip sources ([b0420e7](https://github.com/IamMrCupp/benchhud/commit/b0420e7490b69d209e35b906f4c353b48d64877e))


### Bug Fixes

* **chips:** keep the Chips tab and the config in step with what the HUD loads ([b0420e7](https://github.com/IamMrCupp/benchhud/commit/b0420e7490b69d209e35b906f4c353b48d64877e))

## [0.24.0](https://github.com/IamMrCupp/benchhud/compare/v0.23.0...v0.24.0) (2026-11-22)


### Features

* **chips:** merge several chip sources into one, with provenance and conflicts ([b6b1774](https://github.com/IamMrCupp/benchhud/commit/b6b17748c05315346fd7259405a6279904bb7dc9))


### Bug Fixes

* **chips:** keep one source's bad answer from sinking a merged lookup ([b6b1774](https://github.com/IamMrCupp/benchhud/commit/b6b17748c05315346fd7259405a6279904bb7dc9))

## [0.23.0](https://github.com/IamMrCupp/benchhud/compare/v0.22.1...v0.23.0) (2026-11-22)


### Features

* **chips:** find a chip's documents in a local folder, by hash then by name ([dc86641](https://github.com/IamMrCupp/benchhud/commit/dc8664188a0119e0eea58e8d05269ac904983777))

## [0.22.1](https://github.com/IamMrCupp/benchhud/compare/v0.22.0...v0.22.1) (2026-11-21)


### Features

* **chips:** read chips from a chip-data checkout or a built index ([5e74241](https://github.com/IamMrCupp/benchhud/commit/5e7424117e07f0f854c35c46c1d4fa866bac6d7f))


### Bug Fixes

* **chips:** keep the directory source from raising on files it can't read ([ec921cb](https://github.com/IamMrCupp/benchhud/commit/ec921cbea8a018aeafb8173c60e40b9da6cfe104))

## [0.22.0](https://github.com/IamMrCupp/benchhud/compare/v0.21.0...v0.22.0) (2026-11-21)


### Features

* **tools:** pick OBS's scene from the Tools dock, and a scene per tool ([#455](https://github.com/IamMrCupp/benchhud/issues/455)) ([3872612](https://github.com/IamMrCupp/benchhud/commit/38726121c982f0f412f06f41fb7c2c1be630e697))

## [0.21.0](https://github.com/IamMrCupp/benchhud/compare/v0.20.1...v0.21.0) (2026-11-21)


### Features

* **chips:** add the chip model, ChipSource seam and marking match ([c68ba54](https://github.com/IamMrCupp/benchhud/commit/c68ba5490483c9fdb3a2f520aaf988e789337256))

## [0.20.1](https://github.com/IamMrCupp/benchhud/compare/v0.20.0...v0.20.1) (2026-11-21)


### Bug Fixes

* **sources:** make hidden win over a panel's own display so the now-playing banner hides ([bcad59a](https://github.com/IamMrCupp/benchhud/commit/bcad59a67b0740f2e71166d9e2c5022346e7da2a))

## [0.20.0](https://github.com/IamMrCupp/benchhud/compare/v0.19.0...v0.20.0) (2026-11-21)


### Features

* **controls:** section the Controls panel into cards ([288137c](https://github.com/IamMrCupp/benchhud/commit/288137ccfd36e507575a3c1616ba2af3fafd3c05))
* **shell:** stacked Bench/Recon groups and a bottom Tools dock ([#429](https://github.com/IamMrCupp/benchhud/issues/429)) ([288137c](https://github.com/IamMrCupp/benchhud/commit/288137ccfd36e507575a3c1616ba2af3fafd3c05))
* **theme:** bundled Inter and status tones ([288137c](https://github.com/IamMrCupp/benchhud/commit/288137ccfd36e507575a3c1616ba2af3fafd3c05))

## [0.19.0](https://github.com/IamMrCupp/benchhud/compare/v0.18.0...v0.19.0) (2026-11-20)


### Features

* **ui:** match burnwright's palette and panel look ([#440](https://github.com/IamMrCupp/benchhud/issues/440)) ([c498fba](https://github.com/IamMrCupp/benchhud/commit/c498fbac9180861ce1b4a70f5afb5264e7a551de))

## [0.18.0](https://github.com/IamMrCupp/benchhud/compare/v0.17.0...v0.18.0) (2026-11-19)


### Features

* **jobs:** set every job field from the command line ([#436](https://github.com/IamMrCupp/benchhud/issues/436)) ([30ed6a8](https://github.com/IamMrCupp/benchhud/commit/30ed6a82f2dd1201ca823ee88025419f00a529d6))

## [0.17.0](https://github.com/IamMrCupp/benchhud/compare/v0.16.1...v0.17.0) (2026-11-19)


### Features

* **jobs:** create and select a job from the command line ([#433](https://github.com/IamMrCupp/benchhud/issues/433)) ([147aeba](https://github.com/IamMrCupp/benchhud/commit/147aebaea44c3a7843f21d10cb61cdaad07bd280))

## [0.16.1](https://github.com/IamMrCupp/benchhud/compare/v0.16.0...v0.16.1) (2026-11-18)


### Bug Fixes

* **ui:** show the ampersand in the Pinout & protocols title ([#430](https://github.com/IamMrCupp/benchhud/issues/430)) ([01d237e](https://github.com/IamMrCupp/benchhud/commit/01d237e65e57085f914357f4049c2a3e8e146e88))

## [0.16.0](https://github.com/IamMrCupp/benchhud/compare/v0.15.0...v0.16.0) (2026-11-17)


### Features

* **output:** build, ship and register softcam.dll so Windows publishes the virtual camera ([2c5ec55](https://github.com/IamMrCupp/benchhud/commit/2c5ec55a80950d80aefad847938fcac0c990330e))

## [0.15.0](https://github.com/IamMrCupp/benchhud/compare/v0.14.8...v0.15.0) (2026-11-17)


### Features

* **preferences:** find intake and bench-parts on this machine ([#424](https://github.com/IamMrCupp/benchhud/issues/424)) ([aa04d1d](https://github.com/IamMrCupp/benchhud/commit/aa04d1d2c54b46b824660f3baa5a545f297b1890))
* **ui:** give the composite the window and tidy the theme ([#422](https://github.com/IamMrCupp/benchhud/issues/422)) ([594d47c](https://github.com/IamMrCupp/benchhud/commit/594d47c5401018508edf24ce109744d7ee497d9a))

## [0.14.8](https://github.com/IamMrCupp/benchhud/compare/v0.14.7...v0.14.8) (2026-11-17)


### Documentation

* **readme:** the unified installer on a Mac ([#418](https://github.com/IamMrCupp/benchhud/issues/418)) ([53dc7c3](https://github.com/IamMrCupp/benchhud/commit/53dc7c32a13bc3a87fe679be836620469affef30))

## [0.14.7](https://github.com/IamMrCupp/benchhud/compare/v0.14.6...v0.14.7) (2026-11-17)


### Performance

* **hud:** limit OpenCV to 2 threads by default, set from performance.opencv_threads ([75f43fb](https://github.com/IamMrCupp/benchhud/commit/75f43fb79e066a30f2140de59a08a69ec9c4a154))

## [0.14.6](https://github.com/IamMrCupp/benchhud/compare/v0.14.5...v0.14.6) (2026-11-17)


### Bug Fixes

* **license:** relicense to AGPL-3.0-or-later ([59b053d](https://github.com/IamMrCupp/benchhud/commit/59b053dd9da264d2c8c3559e47c49a5b25d1fb66))

## [0.14.5](https://github.com/IamMrCupp/benchhud/compare/v0.14.4...v0.14.5) (2026-11-17)


### Documentation

* **readme:** install on a Mac with Homebrew ([d18b84e](https://github.com/IamMrCupp/benchhud/commit/d18b84e947263fcb2b4d127dfb68e88ea37cccd4))

## [0.14.4](https://github.com/IamMrCupp/benchhud/compare/v0.14.3...v0.14.4) (2026-11-17)


### Bug Fixes

* **app:** a command's output goes to stdout in the Mac app too ([89390bf](https://github.com/IamMrCupp/benchhud/commit/89390bfae9ed18de0d898190587265e5d206f810))

## [0.14.3](https://github.com/IamMrCupp/benchhud/compare/v0.14.2...v0.14.3) (2026-11-17)


### Documentation

* point the README and BENCH-SETUP at the bench-suite installer ([2785d0f](https://github.com/IamMrCupp/benchhud/commit/2785d0f1a858106d97a5a8a213a6646829567d69))

## [0.14.2](https://github.com/IamMrCupp/benchhud/compare/v0.14.1...v0.14.2) (2026-11-17)


### Documentation

* **benchmark:** correct the bench invocation and the Linux thread naming ([856b490](https://github.com/IamMrCupp/benchhud/commit/856b4900ce18490c7623750c0d32779c077e21bd))

## [0.14.1](https://github.com/IamMrCupp/benchhud/compare/v0.14.0...v0.14.1) (2026-11-17)


### Documentation

* **packaging:** how to build the notarized macOS .dmg ([3ef15ea](https://github.com/IamMrCupp/benchhud/commit/3ef15ea377905cfb8ae196f2b0f6d9a18275fc68))

## [0.14.0](https://github.com/IamMrCupp/benchhud/compare/v0.13.1...v0.14.0) (2026-11-17)


### Features

* **terminal:** run the terminal panel on Windows through ConPTY ([d75b2a4](https://github.com/IamMrCupp/benchhud/commit/d75b2a4a2197a45a12a30a433713fa3071431d7b))


### Performance

* **bench:** add a headless pipeline benchmark with fps, CPU per thread and stage times ([fb25972](https://github.com/IamMrCupp/benchhud/commit/fb25972193013b3409e31669851a9ef33c44782d))

## [0.13.1](https://github.com/IamMrCupp/benchhud/compare/v0.13.0...v0.13.1) (2026-11-16)


### Bug Fixes

* **capture:** open the scope with V4L2 on Linux so the size request is applied ([79df64a](https://github.com/IamMrCupp/benchhud/commit/79df64a8491f6139b404a1762a7e93ed88b36059))
* **shell:** size the window against the largest screen, not Qt's primary one ([377948c](https://github.com/IamMrCupp/benchhud/commit/377948c8e39d12ebcb34489a1a87fd99fe21a0e0))


### Documentation

* **readme:** add bench screenshots and bring the status section up to date ([0752f30](https://github.com/IamMrCupp/benchhud/commit/0752f30e51785392aaaec4802cc88f01cee416b0))

## [0.13.0](https://github.com/IamMrCupp/benchhud/compare/v0.12.6...v0.13.0) (2026-11-16)


### Features

* **capture:** make the reduced live decode a setting, panels.scope.reduced_live, on by default ([840e6fd](https://github.com/IamMrCupp/benchhud/commit/840e6fd224e49bfbc1b81f456e1d07c94cabdf71))

## [0.12.6](https://github.com/IamMrCupp/benchhud/compare/v0.12.5...v0.12.6) (2026-11-15)


### Performance

* **shell:** size frames to the view before Qt and write BGR to the loopback ([8e8b860](https://github.com/IamMrCupp/benchhud/commit/8e8b86092579d705d0b677160b7957a8116a09b0))

## [0.12.5](https://github.com/IamMrCupp/benchhud/compare/v0.12.4...v0.12.5) (2026-11-15)


### Documentation

* **setup:** rebuild the initramfs after changing the v4l2loopback options ([94d228b](https://github.com/IamMrCupp/benchhud/commit/94d228bc6edee95ef92e0fdb5460792c190d9fdc))

## [0.12.4](https://github.com/IamMrCupp/benchhud/compare/v0.12.3...v0.12.4) (2026-11-13)


### Bug Fixes

* **thermal:** check for Recovery Mode off the GUI thread so a USB dropout can't freeze the HUD ([15f92eb](https://github.com/IamMrCupp/benchhud/commit/15f92ebbe860b80817015d9e5e79ebd639751400))

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
