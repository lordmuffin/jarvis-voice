# Changelog

## [0.15.0](https://github.com/lordmuffin/jarvis-voice/compare/macos-v0.14.0...macos-v0.15.0) (2026-10-31)


### Features

* local-only as server-down fallback, visible mode, resumable uploads ([57bed7b](https://github.com/lordmuffin/jarvis-voice/commit/57bed7b6c8bdb53e5d1218169344c7d4e354bc95))
* local-only gate and restricted sessions for work calls (task 6) ([a89f745](https://github.com/lordmuffin/jarvis-voice/commit/a89f7453fa24b0e2d453b776f08e03fc96c6700f))
* **macos:** record locally when the server is down; fix interrupted uploads ([6f2c7b1](https://github.com/lordmuffin/jarvis-voice/commit/6f2c7b109de598d636e5c5f3afeec256d0c3be73))
* **macos:** refuse all network traffic during a local-only session ([5ef5df8](https://github.com/lordmuffin/jarvis-voice/commit/5ef5df88cc53abe66dc37298f26690a301d15f1d))
* **protocol:** speaker_update message, sent only to clients that ask for it ([7b3eb11](https://github.com/lordmuffin/jarvis-voice/commit/7b3eb11163e2c42678e5dbbfe97e340c0436990f))
* **server:** speaker diarization at finalize, with a speaker_update protocol message ([f4e49b8](https://github.com/lordmuffin/jarvis-voice/commit/f4e49b8527641f8a2c32f8d0bc80607774f58995))


### Bug Fixes

* **macos:** restore restricted uploads, cut open connections at the local-only gate ([de58f26](https://github.com/lordmuffin/jarvis-voice/commit/de58f26bafdaf39aeac9df0b79e7e1a7a18c34fa))
* restore main and the local-only guarantees from the [#214](https://github.com/lordmuffin/jarvis-voice/issues/214) review ([6538f3a](https://github.com/lordmuffin/jarvis-voice/commit/6538f3a1600cbfff4066fcd0b6bf379a0afb7d26))
* **server:** renumber speakers migration after main's 0011/0012 ([7d05ac6](https://github.com/lordmuffin/jarvis-voice/commit/7d05ac607c243afa9fbd2754f2b2d37ba7a85f15))

## [0.14.0](https://github.com/lordmuffin/jarvis-voice/compare/macos-v0.13.1...macos-v0.14.0) (2026-10-15)


### Features

* design-system guardrails (web, Android, Swift) with CI ([8151724](https://github.com/lordmuffin/jarvis-voice/commit/8151724d4f3bb2988bf6ee6b154082c3c61d1d68))
* design-system guardrails (web, Android, Swift) with CI ([7236e56](https://github.com/lordmuffin/jarvis-voice/commit/7236e566d2a33d15554b4d4b4675356b5135ec2e))

## [0.13.1](https://github.com/lordmuffin/jarvis-voice/compare/macos-v0.13.0...macos-v0.13.1) (2026-10-14)


### Bug Fixes

* align copy and vocabulary with the Instrument design system ([492b090](https://github.com/lordmuffin/jarvis-voice/commit/492b0906ddf2fae45a3e25d340fb9e36637a1dcb))
* **macos:** align copy with Instrument vocabulary ([311d47f](https://github.com/lordmuffin/jarvis-voice/commit/311d47fba793665256a99624280e1bcd00d9a81e))

## [0.13.0](https://github.com/lordmuffin/jarvis-voice/compare/macos-v0.12.0...macos-v0.13.0) (2026-10-14)


### Features

* **macos:** adopt the Instrument design system ([cbc3d49](https://github.com/lordmuffin/jarvis-voice/commit/cbc3d4973d6af746f5a74420c9adcb253195a83a))
* **macos:** adopt the Instrument design system ([ca420a0](https://github.com/lordmuffin/jarvis-voice/commit/ca420a04b23059dc82659063ef6a84c16230fe13))

## [0.12.0](https://github.com/lordmuffin/jarvis-voice/compare/macos-v0.11.0...macos-v0.12.0) (2026-10-14)


### Features

* **macos:** show live as a red LIVE tag, like the dashboard ([c74c9ae](https://github.com/lordmuffin/jarvis-voice/commit/c74c9ae679e5d13527dd462ae23209899f6407a5))
* **macos:** show live as a red LIVE tag, like the dashboard ([fc490cc](https://github.com/lordmuffin/jarvis-voice/commit/fc490ccfff27bc79cf737824fad4183975d2a470))

## [0.11.0](https://github.com/lordmuffin/jarvis-voice/compare/macos-v0.10.0...macos-v0.11.0) (2026-10-12)


### Features

* **macos:** show the version and a LAB/LIVE tag in the menu, menu-bar icon and Settings ([6385b74](https://github.com/lordmuffin/jarvis-voice/commit/6385b74e1e4562b4c0a80166a6294cab785a2470))
* **macos:** show the version and a LAB/LIVE tag in the menu, menu-bar icon and Settings ([be434d3](https://github.com/lordmuffin/jarvis-voice/commit/be434d3d2fc3ecd91727e34d59e609c6933a472f))

## [0.10.0](https://github.com/lordmuffin/jarvis-voice/compare/macos-v0.9.1...macos-v0.10.0) (2026-10-12)


### Features

* **macos:** add Jarvis Voice menu-bar app ([b4eae2d](https://github.com/lordmuffin/jarvis-voice/commit/b4eae2d19fa534d41e344cc7946f4b0ff871846b))
* **macos:** add Jarvis Voice menu-bar app ([8364f68](https://github.com/lordmuffin/jarvis-voice/commit/8364f68b3483bd82ac81c3925a52248825f60cf7))
* **macos:** add JarvisLiveKit package (protocol, framing, outbox, transport, session store) ([8ba0d8f](https://github.com/lordmuffin/jarvis-voice/commit/8ba0d8fc2fbbbec547e24d2c5f9d489f3152f1b1))
* **macos:** add JarvisLiveKit package (protocol, framing, outbox, transport, session store) ([953c3af](https://github.com/lordmuffin/jarvis-voice/commit/953c3af85057fbaf8963f55e7aca0373b1eb94c5))
* **macos:** double-tap the dictation key to start, tap to stop ([6666fb6](https://github.com/lordmuffin/jarvis-voice/commit/6666fb66b9e093f3b32418c1b13efaf033ac6aa9))
* **macos:** double-tap the dictation key to start, tap to stop ([298619e](https://github.com/lordmuffin/jarvis-voice/commit/298619e7dcea20bd25eb9255bfda1a62282ea145))
* **macos:** on-device push-to-talk dictation on Right ⌘ with clipboard + paste ([03f7c69](https://github.com/lordmuffin/jarvis-voice/commit/03f7c69d9dd13936c3416f8a8ae63edd1517ff59))
* **macos:** on-device push-to-talk dictation on Right ⌘ with clipboard + paste ([41a6d4b](https://github.com/lordmuffin/jarvis-voice/commit/41a6d4b9bf7afecfac9c97e2977fdb704efa149a))
* **macos:** show the app version in the menu bar dropdown ([c3fd396](https://github.com/lordmuffin/jarvis-voice/commit/c3fd396a9456edb817a69b9004534584bcc993e9))
* **macos:** show the app version in the menu bar dropdown ([c48f3ea](https://github.com/lordmuffin/jarvis-voice/commit/c48f3ea05db428ef7d4f499758c933dc8693d124))
* **macos:** turn on Local only automatically away from home ([e6b27a8](https://github.com/lordmuffin/jarvis-voice/commit/e6b27a839b5d58d93f52a02e26a6f9823ad5c9b9))
* multiple users with Pocket-ID sign-in ([23fbd10](https://github.com/lordmuffin/jarvis-voice/commit/23fbd10e48aa1bd06524d57aae47cc337e103ef7))
* scaffold Jarvis Live (protocol, server, CI, release-please) ([f608c33](https://github.com/lordmuffin/jarvis-voice/commit/f608c335df3a436abf1182339d81e9db5ee1da36))
* scaffold Jarvis Live protocol, server, CI and release automation ([269ce76](https://github.com/lordmuffin/jarvis-voice/commit/269ce76e18ca644646a5afb11fea81ee27de2ee7))
* **server:** promote live channel to live.apj.dev ([7b61b02](https://github.com/lordmuffin/jarvis-voice/commit/7b61b02a7f68c625cfe431ebca9530cb24edb0ef))
* **server:** promote live channel to live.apj.dev ([122483a](https://github.com/lordmuffin/jarvis-voice/commit/122483a8f88f570dcfe99554fb83a5d67eb60de5))
* turn on Local only automatically away from home (Android + macOS) ([a33ef5d](https://github.com/lordmuffin/jarvis-voice/commit/a33ef5dc6a07334cd11658233773b96d7c06077f))


### Bug Fixes

* **macos:** ad-hoc sign the release build so macOS keeps permission grants ([c742394](https://github.com/lordmuffin/jarvis-voice/commit/c742394f7a0bd1489f874f980cd4b67a530445de))
* **macos:** bound LiveConnection.end so Stop never leaves the app stuck in stopping ([c04be6c](https://github.com/lordmuffin/jarvis-voice/commit/c04be6c7b3e7a585ce32bffd453601d99c2bf6ea))
* **macos:** dictation asks for the mic once and never hangs on Transcribing ([778f7ed](https://github.com/lordmuffin/jarvis-voice/commit/778f7edb90aa584dbef70651d66464ff6dc3c382))
* **macos:** dictation pastes at the cursor instead of only copying ([dfb73b3](https://github.com/lordmuffin/jarvis-voice/commit/dfb73b3af1273dca386e9b94a3da4741f1e4d326))
* **macos:** dictation pastes at the cursor instead of only copying ([6199f62](https://github.com/lordmuffin/jarvis-voice/commit/6199f62a74030730706481e8578e6ee50ca6a97d))
* **macos:** don't get stuck in "stopping" after Stop Session ([b479f62](https://github.com/lordmuffin/jarvis-voice/commit/b479f6218041f4f189d93a336850deb8e187d1d4))
* **macos:** open Settings from the menu again ([951c41f](https://github.com/lordmuffin/jarvis-voice/commit/951c41f967b2fd3a1114cb4bf6cb81fcce87f65a))
* **macos:** Settings opens, dictation mic prompt and Transcribing hang ([84ed289](https://github.com/lordmuffin/jarvis-voice/commit/84ed28926f28e9844b83d12c6d4aa8cadc35dd6a))

## [0.9.1](https://github.com/lordmuffin/jarvis-voice/compare/macos-v0.9.0...macos-v0.9.1) (2026-10-12)


### Bug Fixes

* **macos:** dictation pastes at the cursor instead of only copying ([dfb73b3](https://github.com/lordmuffin/jarvis-voice/commit/dfb73b3af1273dca386e9b94a3da4741f1e4d326))
* **macos:** dictation pastes at the cursor instead of only copying ([6199f62](https://github.com/lordmuffin/jarvis-voice/commit/6199f62a74030730706481e8578e6ee50ca6a97d))

## [0.9.0](https://github.com/lordmuffin/jarvis-voice/compare/macos-v0.8.0...macos-v0.9.0) (2026-10-12)


### Features

* **macos:** show the app version in the menu bar dropdown ([c3fd396](https://github.com/lordmuffin/jarvis-voice/commit/c3fd396a9456edb817a69b9004534584bcc993e9))
* **macos:** show the app version in the menu bar dropdown ([c48f3ea](https://github.com/lordmuffin/jarvis-voice/commit/c48f3ea05db428ef7d4f499758c933dc8693d124))

## [0.8.0](https://github.com/lordmuffin/jarvis-voice/compare/macos-v0.7.0...macos-v0.8.0) (2026-10-11)


### Features

* **server:** promote live channel to live.apj.dev ([7b61b02](https://github.com/lordmuffin/jarvis-voice/commit/7b61b02a7f68c625cfe431ebca9530cb24edb0ef))
* **server:** promote live channel to live.apj.dev ([122483a](https://github.com/lordmuffin/jarvis-voice/commit/122483a8f88f570dcfe99554fb83a5d67eb60de5))

## [0.7.0](https://github.com/lordmuffin/jarvis-voice/compare/macos-v0.6.1...macos-v0.7.0) (2026-10-11)


### Features

* **macos:** double-tap the dictation key to start, tap to stop ([6666fb6](https://github.com/lordmuffin/jarvis-voice/commit/6666fb66b9e093f3b32418c1b13efaf033ac6aa9))
* **macos:** double-tap the dictation key to start, tap to stop ([298619e](https://github.com/lordmuffin/jarvis-voice/commit/298619e7dcea20bd25eb9255bfda1a62282ea145))

## [0.6.1](https://github.com/lordmuffin/jarvis-voice/compare/macos-v0.6.0...macos-v0.6.1) (2026-10-11)


### Bug Fixes

* **macos:** ad-hoc sign the release build so macOS keeps permission grants ([c742394](https://github.com/lordmuffin/jarvis-voice/commit/c742394f7a0bd1489f874f980cd4b67a530445de))
* **macos:** dictation asks for the mic once and never hangs on Transcribing ([778f7ed](https://github.com/lordmuffin/jarvis-voice/commit/778f7edb90aa584dbef70651d66464ff6dc3c382))
* **macos:** open Settings from the menu again ([951c41f](https://github.com/lordmuffin/jarvis-voice/commit/951c41f967b2fd3a1114cb4bf6cb81fcce87f65a))
* **macos:** Settings opens, dictation mic prompt and Transcribing hang ([84ed289](https://github.com/lordmuffin/jarvis-voice/commit/84ed28926f28e9844b83d12c6d4aa8cadc35dd6a))

## [0.6.0](https://github.com/lordmuffin/jarvis-voice/compare/macos-v0.5.1...macos-v0.6.0) (2026-10-11)


### Features

* **macos:** on-device push-to-talk dictation on Right ⌘ with clipboard + paste ([03f7c69](https://github.com/lordmuffin/jarvis-voice/commit/03f7c69d9dd13936c3416f8a8ae63edd1517ff59))
* **macos:** on-device push-to-talk dictation on Right ⌘ with clipboard + paste ([41a6d4b](https://github.com/lordmuffin/jarvis-voice/commit/41a6d4b9bf7afecfac9c97e2977fdb704efa149a))

## [0.5.1](https://github.com/lordmuffin/jarvis-voice/compare/macos-v0.5.0...macos-v0.5.1) (2026-10-11)


### Bug Fixes

* **macos:** bound LiveConnection.end so Stop never leaves the app stuck in stopping ([c04be6c](https://github.com/lordmuffin/jarvis-voice/commit/c04be6c7b3e7a585ce32bffd453601d99c2bf6ea))
* **macos:** don't get stuck in "stopping" after Stop Session ([b479f62](https://github.com/lordmuffin/jarvis-voice/commit/b479f6218041f4f189d93a336850deb8e187d1d4))

## [0.5.0](https://github.com/lordmuffin/jarvis-voice/compare/macos-v0.4.0...macos-v0.5.0) (2026-10-11)


### Features

* multiple users with Pocket-ID sign-in ([23fbd10](https://github.com/lordmuffin/jarvis-voice/commit/23fbd10e48aa1bd06524d57aae47cc337e103ef7))

## [0.4.0](https://github.com/lordmuffin/jarvis-voice/compare/macos-v0.3.0...macos-v0.4.0) (2026-10-10)


### Features

* **macos:** add Jarvis Voice menu-bar app ([b4eae2d](https://github.com/lordmuffin/jarvis-voice/commit/b4eae2d19fa534d41e344cc7946f4b0ff871846b))
* **macos:** add Jarvis Voice menu-bar app ([8364f68](https://github.com/lordmuffin/jarvis-voice/commit/8364f68b3483bd82ac81c3925a52248825f60cf7))

## [0.3.0](https://github.com/lordmuffin/jarvis-voice/compare/macos-v0.2.0...macos-v0.3.0) (2026-10-09)


### Features

* **macos:** add JarvisLiveKit package (protocol, framing, outbox, transport, session store) ([8ba0d8f](https://github.com/lordmuffin/jarvis-voice/commit/8ba0d8fc2fbbbec547e24d2c5f9d489f3152f1b1))
* **macos:** add JarvisLiveKit package (protocol, framing, outbox, transport, session store) ([953c3af](https://github.com/lordmuffin/jarvis-voice/commit/953c3af85057fbaf8963f55e7aca0373b1eb94c5))

## [0.2.0](https://github.com/lordmuffin/jarvis-voice/compare/macos-v0.1.0...macos-v0.2.0) (2026-10-09)


### Features

* scaffold Jarvis Live (protocol, server, CI, release-please) ([f608c33](https://github.com/lordmuffin/jarvis-voice/commit/f608c335df3a436abf1182339d81e9db5ee1da36))
* scaffold Jarvis Live protocol, server, CI and release automation ([269ce76](https://github.com/lordmuffin/jarvis-voice/commit/269ce76e18ca644646a5afb11fea81ee27de2ee7))
