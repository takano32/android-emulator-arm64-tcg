# root と Google Play ストアの両立手順と仕組み

このリポジトリでは、**root 権限（`adb root` / `su 0`）** と **正規 Google Play ストア（`com.android.vending`）＋ GMS（`com.google.android.gms`）** を同時に使える **Android 14 (API 34)** 環境を構成している。
なぜ標準イメージのままでは両立できないのか、どのような調査・検証を経てオフライン統合・軽量化＋`init`/`apexd`/`vendor` パッチ方式（v5.0-api34）に至ったのか、そして [`bin/setup`](../bin/setup)・[`bin/start`](../bin/start)・[`lib/inject-playstore.py`](../lib/inject-playstore.py) が具体的に何をしているのかを記録する。

調査・検証日: 2026-11-21（API 31 初版） / 2026-11-22（Android 14 / API 34 移行・サインイン完了・コア数ベンチマーク）

---

## 1. 背景: なぜ公式イメージのままでは両立できないか

Google が SDK 向けに配布している Android 12〜14 (API 31〜34 / `arm64-v8a`) のシステムイメージは 3 系統あり、**root の可否**と **Play ストアの有無**が排他になっている。

| イメージ (`SYSIMG_TAG`) | ビルド種別 | root (`adb root` / `su`) | `/data/local.prop` (`ro.hw_timeout_multiplier`) | GMS | Google Play ストア |
|---|---|---|---|---|---|
| `default`（素の AOSP） | `userdebug` | ○ | ○ | × | × |
| `google_apis` | `userdebug` | **○** | **○** | **○（dev-keys 署名）** | × |
| `google_apis_playstore` | **`user`（本番相当）** | × | **×** | **○（release-keys 署名）** | **○（release-keys 署名）** |

KVM の無い本環境（QEMU TCG によるソフトウェアエミュレーション）では、`ro.hw_timeout_multiplier=20` を `/data/local.prop` で設定して Watchdog / ANR の強制終了を避けることが不可欠だが、Android の `init` が `/data/local.prop` を読み込むのは **`userdebug` ビルド（`ro.debuggable=1`）だけ** である。
さらに `user` ビルドでは `sepolicy` に `u:r:su:s0` ドメインが含まれず、`adbd` も `adb root` を拒否する。

したがって、**公式 `userdebug` である `google_apis` の `system` / `vendor` / `ramdisk` をベースにし、Google 系アプリの署名鍵を揃えつつ Play ストアを `google_apis_playstore` からオフライン移植する**方針を採用した。

---

## 2. 調査・検証で判明した 3 つの技術的課題と解決策

API 31 (`arm64-v8a`) の `google_apis` (`arm64-v8a-31_r11.zip`) と `google_apis_playstore` (`arm64-v8a-31_r09-linux.zip`) それぞれの `system.img` 内部（`system` / `system_ext` / `product` / `vendor`）を `debugfs`・`lpdump`・`apksigner`・`objdump` で解析し、実機エミュレータでの起動検証を通じて以下の事実と課題を突き止めた。

### 課題 1: `Phonesky64.apk` 単体注入では GMS との署名不一致で白い画面になる（v1 → v2/v3）

当初（v1）は `google_apis_playstore` から `product/priv-app/Phonesky64/Phonesky64.apk` 単体を抽出して `google_apis` の `product` に注入した。これで Play ストアのインストール・特権権限付与・起動自体は成功したが、スプラッシュ画面の後に `UnauthenticatedMainActivity` が真っ白な画面のまま進まず、`logcat` に以下が出力された。

```text
W GooglePlayServicesUtil: com.google.android.googlequicksearchbox requires Google Play Store, but its signature doesn't match that of Google Play services.
```

各パーティションの APK 署名証明書（SHA-256 ダイジェスト）を調査したところ、以下の構造になっていることが判明した。

| パーティション / APK | `google_apis` (`r11`, `userdebug`) | `google_apis_playstore` (`r09`, `user`) |
|---|---|---|
| **プラットフォーム署名アプリ** (`system/framework/framework-res.apk`, `system_ext/priv-app/{SettingsGoogle,SystemUIGoogle,GoogleSdkSetup}`) | `30:1A:A3:CB:...` (`userdebug` プラットフォーム鍵) | `00:B1:22:36:...` (`user` プラットフォーム鍵) |
| **`product` 内の Google 製アプリ全般** (`PrebuiltGmsCore.apk`, `Velvet.apk`, `SetupWizardPrebuilt.apk`, `Maps.apk`, `PrebuiltGmail.apk` 等) | `19:75:B2:F1:...` (Google dev/test 鍵) | **`F0:FD:6C:5B:...` (Google 本番 release 鍵)** |
| **`product/priv-app/Phonesky64/Phonesky64.apk`** | （存在しない） | **`F0:FD:6C:5B:...` (Google 本番 release 鍵)** |
| **`system_ext/priv-app/GoogleServicesFramework/GoogleServicesFramework.apk`** (`com.google.android.gsf`) | `19:75:B2:F1:...` (Google dev/test 鍵) | **`F0:FD:6C:5B:...` (Google 本番 release 鍵)** |
| **`system_ext/priv-app/GoogleFeedback/GoogleFeedback.apk`** | `19:75:B2:F1:...` (Google dev/test 鍵) | **`F0:FD:6C:5B:...` (Google 本番 release 鍵)** |

重要な点として、**`product/app` および `product/priv-app` にはプラットフォーム鍵（`30:1A:A3:CB:...`）で署名されたアプリが 1 つも存在しない**。
一方で、`system_ext/priv-app/GoogleServicesFramework.apk`（`com.google.android.gsf`）は `product/priv-app/PrebuiltGmsCore/PrebuiltGmsCore.apk`（`com.google.android.gms`）と同一の `sharedUserId="com.google.uid.shared"`（UID 10104）を共有している。そのため `product` だけを入れ替えると（v2）、起動時に `PackageManagerService` が以下の致命的例外を投げる：

```text
FATAL EXCEPTION IN SYSTEM PROCESS: main
java.lang.IllegalStateException: Signature mismatch on system package com.google.android.gsf for shared user SharedUserSetting{... com.google.uid.shared/10104}
```

**解決策（v3 のパーティション統合設計）**:
1. **`product` パーティション丸ごと置換**: `google_apis` の `product` を `google_apis_playstore` の `product` に丸ごと置き換える。これにより Play ストア (`Phonesky64.apk`)・GMS (`PrebuiltGmsCore.apk`)・Google アプリ (`Velvet.apk`) 等がすべて本番 release 鍵（`F0:FD:6C:5B:...`）で統一される。
2. **`system_ext` の Google 署名 2 パッケージのみ置換**: `google_apis` の `system_ext` はベース（`userdebug` のプラットフォーム鍵 `30:1A:A3:CB:...` で署名された `SettingsGoogle` / `SystemUIGoogle` / `GoogleSdkSetup`）を維持したまま、`priv-app/GoogleServicesFramework/GoogleServicesFramework.apk` と `priv-app/GoogleFeedback/GoogleFeedback.apk` の 2 つだけを `google_apis_playstore` の本番署名版に差し替え、既存の `oat/` を削除する。

これにより、**プラットフォーム鍵（`system` と `system_ext` の OS コンポーネント）は `userdebug` 鍵で完全一致**し、かつ **`com.google.uid.shared` を含む全 Google 製アプリは本番 release 鍵で完全一致**する。

### 課題 2: `/system/bin/init` にハードコードされた `sys_prop` 2 秒タイムアウトによる `system_server` クラッシュ

KVM なし TCG 環境で GMS + Play ストアを含む重い起動処理が走ると、`ro.hw_timeout_multiplier=20` を設定していても、高負荷時に以下のエラーで `system_server` が突然死してブートループに陥ることがあった。

```text
E System  : ********** Java exception Let system die **********
E System  : java.lang.RuntimeException: failed to set system property
E System  :   at android.os.SystemProperties.native_set(Native Method)
E System  :   at android.app.PropertyInvalidatedCache.disableSystemWide(PropertyInvalidatedCache.java:...)
```

この直前のカーネル／`init` ログには必ず以下が出力されていた。

```text
init: Control message: Could not find 'aidl/...' for ctl.interface_start from pid: 472 (/system/bin/servicemanager)
init: Receiving property_set message failed: Try again
init: Unable to receiveesh socket message: Resource temporarily unavailable
```

`system/bin/init`（バイナリ実体は `system/ext4` 内の `/system/bin/init`）を逆アセンブルして調査したところ、Android 12 の `handle_property_set_fd()`（`system/core/init/property_service.cpp`）にある `PropertySetSocket` の受信タイムアウト `kDefaultSocketTimeout = 2000`（2000 ms = 2 秒）は **`ro.hw_timeout_multiplier` を参照せずバイナリに即値でハードコード**されていた。

```text
0000000000117aa4: d10143ff  sub  sp, sp, #0x50
0000000000117aa8: f90013fe  str  x30, [sp, #32]
0000000000117aac: 5280fa0a  mov  w10, #0x7d0      // ← 2000 ms (0x7d0) ハードコード
```

`init` のメインループが `ctl.interface_start` 等で数秒間ブロックしている間に `system_server` がシステムプロパティを更新しようとすると、2 秒でタイムアウトして `RuntimeException: failed to set system property` が投げられ、`system_server` が自死していた。

**解決策（`/system/bin/init` の 4 バイトインプレースパッチ）**:
- `/system/bin/init` の `mov w10, #0x7d0`（2000 ms）命令列をパターン検索し（API 31 と API 34 でコンパイラの命令スケジューリング順が異なるため、`INIT_TIMEOUT_PATTERNS` で両世代に対応）、`mov w10, #<SYSPROP_TIMEOUT_MS>`（既定 60000 ms = `0xea60`、[`config.sh`](../config.sh) の `SYSPROP_TIMEOUT_MS`）へ書き換える。
- `system.img` の `system` パーティションは ext4 の `shared_blocks` が有効な読み取り専用イメージだが、`debugfs` の `bmap` と `icheck` で対象オフセットの物理ブロックが他 inode と共有されていないことを確認したうえで、該当 4 バイトだけを直接書き換える。
- これにより inode メタデータや `security.selinux = "u:object_r:init_exec:s0"` を一切傷つけずに `sys_prop` のタイムアウトを 60 秒へ延長でき、TCG 下での `system_server` 再起動が **0 回** になった。

### 課題 2.5: Android 14 (API 34) 特有の 5 つの壁と解決策（v5.0-api34）

Android 12 (API 31) から Android 14 (API 34 / `34_r14`) へ移行した際、署名構造（`product` と `system_ext` の GSF/Feedback が Google 本番鍵 `F0:FD:6C:5B:...`、OS 側プラットフォームアプリが `userdebug` 鍵 `30:1A:A3:CB:...`）は同じだったが、新たに以下 5 つの壁が判明し、すべて自動対処を組み込んだ。

1. **`/system/bin/apexd` の 1,000 ms `dm-verity` タイムアウトによるブートループ**:
   - Android 14 の `apexd` を逆アセンブルしたところ、圧縮 APEX（CAPEX）22 個のマウント時に `WaitForDevice("/dev/block/mapper/by-uuid/...")` の既定タイムアウトが **1,000 ms** にハードコードされており、並列起動すると TCG 下で 1,000〜1,900 ms かかって `apexd` が失敗 → `libstatssocket.so not found` → `zygote` SIGABRT → 再起動ループに陥った。
   - `apexd` は起動 0.3 秒前に読み込まれる `/data/local.prop` の設定プロパティを参照するため、[`bin/setup`](../bin/setup) で以下 4 つを書き込むことで **34/34 APEX すべて有効化成功（失敗 0）** となった：
     - `apexd.config.dm_create.timeout=30000`
     - `apexd.config.dm_delete.timeout=30000`
     - `apexd.config.loop_wait.attempts=30`
     - `apexd.config.boot_activation.threads=1`
2. **HWC3 (`composer3-service.ranchu`) + `swiftshader_indirect` の黒画面**:
   - Android 14 の HWC3 で `DEVICE` 合成が走ると `Error3::BAD_BUFFER` となり、画面・`screencap` ともに真っ黒になる。
   - [`bin/start`](../bin/start) の `tune_guest()` で起動ごとに `service call SurfaceFlinger 1008 i32 1`（HW オーバーレイ無効化＝`CLIENT` 合成固定）を実行することで正常描画される。
3. **Wi-Fi (`AndroidWifi`) の自動切断**:
   - TCG 下の遅延で `IpReachabilityMonitor` が `AndroidWifi` を数十秒ごとに切断する。
   - [`bin/start`](../bin/start) で毎起動 `cmd wifi set-ipreach-disconnect disabled` を実行し、初回接続を安定化した。
4. **Google サインイン WebView (`MinuteMaidActivity`) の 90 秒タイムアウト**:
   - Android 14 には **WebView `113.0.5672.136`** が入っており `accounts.google.com` に完全対応するが、GSF の `auth_minute_maid_timeout` が既定 `90000` ms（90 秒）のため、TCG 下の初回 JS 描画完了直前に `Timed-out. Loading took too long.` で `ErrorActivity` へ落ちた。
   - [`bin/start`](../bin/start) で `am broadcast -a com.google.gservices.intent.action.GSERVICES_OVERRIDE --es auth_minute_maid_timeout 600000`（600 秒）を投入して解消。
   - なお Android 14 では `AccountIntroActivity` 直接起動が `No account type passed in!` で拒否されるため、手動でログイン画面を開く場合は `am start -a android.settings.ADD_ACCOUNT_SETTINGS` を使う。
5. **`com.google.android.gms.persistent` の `DeviceConfig` / `Phenotype` 再試行嵐（常時 50% CPU 消費）**:
   - ネット接続後、`gms.persistent` が `com.google.android.westworld` の `DeviceConfig` コミット失敗を無限リトライして `lmkd --reinit` と `system_server` を巻き込み、ゲスト空き CPU を 5% まで圧迫した。
   - [`bin/start`](../bin/start) で `device_config set_sync_disabled_for_tests persistent`、`location_mode 0`、`wifi_scan_always_enabled 0`、`ble_scan_always_enabled 0`、さらに Android 14 で改名された `com.google.android.googlesdksetup/.DefaultActivity` と `com.google.android.bluetooth` の無効化を適用することで、`gms.persistent` 50.9% → **0%**、`system_server` 25.0% → **1.9%**、空き CPU 5% → **135%** まで劇的に改善した。

### 課題 3: その他の重要な前提仕様

1. **dm-verity は論理パーティションに対して無効（`vbmeta` の改変不要）**
   - `ramdisk.img` 内および `vendor/etc/fstab.ranchu` の両方を確認したところ、`system` / `vendor` / `product` / `system_ext` のマウントオプションは `wait,logical,first_stage_mount` であり、`,avb` フラグが付いていない。
   - そのため `system` / `system_ext` / `product` を編集して `super` を再構築しても、`vbmeta` や `VerifiedBootParams.textproto` を変更せずにそのままマウント・起動できる。
2. **`config.ini` は `PlayStore.enabled=false` のままにする**
   - エミュレータ本体に `PlayStore.enabled=true` を渡すとエミュレータ側が `PlayStoreImage` モード（本番イメージ向けの制限モード）になる。Android ゲスト内の Play ストア自体は `PlayStore.enabled=false` でも問題なく動作するため変更しない。
3. **強制終了後のスナップショットロック (`snapshot.lock.lock`) の掃除**
   - `bwrap` サンドボックス（`--unshare-pid`）内ではエミュレータの PID が毎回 `2` になるため、強制終了等で `var/avd/<AVD_NAME>.avd/snapshot.lock.lock`（中身は PID `2`）が残ると、次回起動時に `ERROR | A snapshot operation for 'gplay31' is pending and timeout has expired. Exiting...` で即終了する。[`bin/start`](../bin/start) で起動前に `$AVD_DIR/*.lock` を一括削除するようにした。
4. **`AVD_RAM_MB` の上限（3056 MB）とホストスワップ＋ゲスト 2048 MB 構成**
   - arm64 `ranchu`（QEMU `virt`）ではゲスト物理 RAM が `0x40000000`（1,024 MiB）から始まり、`0xff018000`（RAM 先頭から **3,056 MiB** 地点）に `ramoops`（`memmap=0x10000$0xff018000`）の予約領域が置かれている。
   - `AVD_RAM_MB` に `3072` など 3,056 MB を超える値を指定すると、`DMA32` ゾーン（`0x40000000..0xffffffff`）の内部に未初期化のページブロックホールが生じ、起動完了直前に `system_server` が `/proc/pagetypeinfo` を読んだ瞬間（`pagetypeinfo_showfree_print+0xa8/0x270`）に NULL ポインタ参照でゲストカーネルが毎回パニックする。そのため [`bin/start`](../bin/start) で `AVD_RAM_MB > 3056` を拒否している。
   - 一方、Google Play ストア + GMS + Chromium WebView 113（サインイン画面）を同時に動かすとゲスト内メモリ消費が 1.2〜1.8 GiB に達し、`AVD_RAM_MB="1024"` ではゲスト内 `zram0` スワップとキャッシュ追い出しが連鎖して WebView 起動が極端に遅くなる。
   - ホスト側に 8 GiB のスワップファイル（`/swap/swapfile`）を有効化したうえで `AVD_RAM_MB="2048"` / `TCG_CACHE_MB="256"` とすることで、ゲスト内 `zram0`（約 1,483 MiB）の使用量は数十 MiB 程度、ホスト側スワップ使用も ~400 MiB に収まり、`QEMU2 main loop` のハングも OOM も発生しない。（ホストスワップが無い環境では `config.local.sh` で `AVD_RAM_MB="1024"` / `TCG_CACHE_MB="128"` に下げる。なお Crostini は再起動時に `/etc/fstab` を適用しないため、再起動後は `sudo swapon /swap/swapfile` が必要。）
5. **TCG + SwiftShader 環境向けのオフライン軽量化・高速化（v5.0-api34）**
   - **`product` パーティションからの不要重量アプリ 34 個のオフライン削除**: `google_apis_playstore` の `product` から、API 31 の 26 個（`Velvet`, `PrebuiltGmail`, `Maps`, `YouTube`, `Chrome`, `Photos`, `Drive`, `PrebuiltBugle`, `WellbeingPrebuilt` 等）に加え、Android 14 固有の 8 個（`GoogleContacts`, `PixelThemesStub`, `PrebuiltGoogleAdservicesTvp`, `DeviceIntelligenceNetworkPrebuilt`, `DevicePersonalizationPrebuiltPixel2021`, `GoogleDialer`, `KidsSupervisionStub`, `OdadPrebuilt`）をオフライン削除。Play ストア (`Phonesky`)・GMS (`PrebuiltGmsCore`)・ログイン用 WebView (`WebViewGoogle` / `TrichromeLibrary`)・キーボード・アカウント初期化系だけを残す。
   - **`vendor` パーティションのオフライン高速化調整**:
     - `/vendor/etc/sensors/hals.conf` を空ファイル化し、センサー HAL のポーリングを完全停止。
     - `/vendor/etc/init/rild_goldfish.rc` の `vendor.ril-daemon` に `disabled` を付与し、`etc/vintf/manifest.xml` から HIDL・AIDL 両方の `android.hardware.radio*` HAL 定義を削除。
     - `fstab.ranchu` の `zram0` スワップと `init.ranchu.rc` の `swappiness 100` / `ro.radio.noril yes` / `persist.sys.zram_enabled 1` / `goldfish-logcat` 無効化を設定。
   - **解像度・アニメーション・タッチ効果音の最適化**: 論理画面サイズ（`360x640 dp`）を維持したまま `360x640` (`160 dpi`) で初期作成して SwiftShader の描画ピクセル数を 40% 削減し、アニメーション（`window_animation_scale=0` 等）およびタッチ効果音のソフトウェア Vorbis デコード（`sound_effects_enabled=0`）を無効化。
   - **QEMU 2.12 MTTCG の `futex` 競合と Android 14 最適コア数（`AVD_CORES="2"`）**: 本エミュレータ（35.6.3）の中身は QEMU 2.12.0 ベースのため、`AVD_CORES >= 2`（MTTCG）では ARMv8.0 排他命令（`LDAXR`/`STLXR`）や `TLBI ...IS` のたびに `start_exclusive()` が他 vCPU を `futex` で停止・再開する。Android 12 では 3 コアがわずかに最速（`7:54` vs `8:16`）だったが、Linux 6.1・重量化した Android 14 (`gplay34`) では 3 コア時のコンテキストスイッチが 13〜16.8 万回/秒へ約 3 倍に悪化し、**`AVD_CORES="2"` が起動・アプリ起動・操作応答のすべてで明確に最良**となった（Cortex-A78 ×4、同一条件のクリーンウォームブート実測）：
     - **1 コア**: 起動 **12:20** (`adb` 208s + `boot` 500s) / Settings 起動 **>247 秒（タイムアウト）** / `pm list` 12.9s / ホスト `cs/s` 3,800〜4,700（MTTCG 同期はゼロだが 1 コア奪い合いで極遅）
     - **2 コア（既定・最速）**: 起動 **10:09** (`adb` **203s** + `boot` **376s**) / Settings 起動 **75.0 秒（2.3 倍速い）** / `pm list` **10.3s** / ホスト `cs/s` **46,800〜58,100**（vCPU 実効 83% × 2、大コア 2 本空きで GUI・ホストとも軽い）
     - **3 コア**: 起動 **10:28** (`adb` 206s + `boot` 401s) / Settings 起動 **172.3 秒** / `pm list` 11.8s / ホスト `cs/s` **129,700〜168,000**（vCPU 実効 69% × 3、約 31% を `futex` 待ちで浪費）
   - **ネイティブ GUI ウィンドウの 1:1 ピクセルパーフェクト表示（スケーリング完全無効）**: `scrcpy` 経由の画面転送はゲスト内で H.264 ソフトウェアエンコーダ（`media.swcodec`）が 35〜55% の CPU を消費するため、同一マシン上ではエミュレータ自身の Qt/X11 GUI（`qemu-system-aarch64`）を直接表示する（`EMU_WINDOW="yes"`）。ChromeOS Crostini（Sommelier）では `DISPLAY_LOW_DENSITY=:1` が `960x540` の仮想解像度を 2 倍拡大する仕組みのため、`360x640` が高さ不足で `275x489` に縮小されたあと 2 倍拡大される二重スケーリングが発生する。[`lib/common.sh`](../lib/common.sh) でネイティブ物理解像度の `DISPLAY=:0`（`1920x1080`）を指定し、`QT_ENABLE_HIGHDPI_SCALING=0` / `QT_AUTO_SCREEN_SCALE_FACTOR=0` / `QT_SCALE_FACTOR=1` / `QT_SCREEN_SCALE_FACTORS=1` / `QT_USE_PHYSICAL_DPI=0` / `QT_FONT_DPI=96` で Qt 6 側の HiDPI スケーリング（`Xft.dpi: 120` = 1.25x）を完全に止め、さらに [`bin/start`](../bin/start) で `-fixed-scale` と `emulator-user.ini` の `window.scale = 1.000000` を強制することで、WM 枠・Qt ウィンドウ・OpenGL サブウィンドウ・ゲスト液晶のすべてが厳密に `360x640` 物理ピクセル 1:1 で一致する。

---

## 3. 使用しているアーカイブ（[`config.sh`](../config.sh) に固定）

Google 公式の SDK インデックス（`sys-img2-3.xml`）記載の SHA-1 で照合したうえで、SHA-256 を [`config.sh`](../config.sh) に固定している。

| 役割 | ファイル | URL | 公式 XML の SHA-1 | SHA-256 |
|---|---|---|---|---|
| ベース (`userdebug`, Android 14 / API 34) | `google_apis-arm64-v8a-34_r14.zip` | `https://dl.google.com/android/repository/sys-img/google_apis/arm64-v8a-34_r14.zip` | `2fe8b46d419a3400e30f31b0152b241b50c8b99f` | `bd1c323cbdd3c9dd55dde54588b298d172fd126fe16e127a7bd7aeab6897890a` |
| Play ストア抽出元 (Android 14 / API 34) | `google_apis_playstore-arm64-v8a-34_r14.zip` | `https://dl.google.com/android/repository/sys-img/google_apis_playstore/arm64-v8a-34_r14.zip` | `c307c3301dc52635ebc78b943c39b3c377856ebc` | `8359fc54b9c91465b8cbc74816e581d143d63deeeded445a2761738b74ea6f67` |

---

## 4. オフライン統合・軽量化の手順（[`lib/inject-playstore.py`](../lib/inject-playstore.py)）

`bin/setup` の `[3/4]` ステップで、以下の処理を自動実行する（すべて一時ディレクトリ `var/tmp/sysimg-patch-*` で行い、完了後に一時ファイルは削除する）。

1. **抽出元 `google_apis_playstore` zip からの 1 パス・ストリーム抽出**
   - `google_apis_playstore` の zip 内にある `arm64-v8a/system.img` を全量展開せずストリームで 1 回だけ走査し、GPT ヘッダ（LBA 2）と `lpdump` メタデータから `system_ext` と `product` の 2 パーティションを取り出す。
   - 抽出した `sysext_playstore.img` から `debugfs` で本番署名の `GoogleServicesFramework.apk` と `GoogleFeedback.apk` をダンプし、`sysext_playstore.img` は即座に削除する。
   - 抽出した `product_playstore.img` を新しい `product` パーティションのベースにする。
2. **`product` パーティションからの不要な重量プリインストールアプリ 34 個の削除（`debloat_product_partition`）**
   - `product_playstore.img` の末尾 verity データを切り落として `e2fsck -fy -E unshare_blocks` で `shared_blocks` を解除し、`debugfs -w` で以下の 34 ディレクトリを再帰削除する：
     - `app/`: `CalendarGooglePrebuilt`, `Camera2`, `Chrome`, `Chrome-Stub`, `Drive`, `GoogleContacts`, `GoogleTTS`, `Maps`, `MarkupGoogle`, `NexusWallpapersStubPrebuilt2018`, `Photos`, `PixelThemesStub`, `PrebuiltDeskClockGoogle`, `PrebuiltGmail`, `PrebuiltGoogleAdservicesTvp`, `PrebuiltGoogleTelemetryTvp`, `SoundPickerPrebuilt`, `WallpapersBReel2018`, `YouTube`, `YouTubeMusicPrebuilt`
     - `priv-app/`: `AndroidAutoStubPrebuilt`, `Contacts`, `DeviceIntelligenceNetworkPrebuilt`, `DevicePersonalizationPrebuiltPixel2021`, `Dialer`, `GoogleDialer`, `GoogleRestorePrebuilt`, `ImsServiceEntitlement`, `KidsSupervisionStub`, `OdadPrebuilt`, `PrebuiltBugle`, `SettingsIntelligenceGooglePrebuilt`, `Velvet`, `WellbeingPrebuilt`
   - Play ストア (`Phonesky`)、GMS (`PrebuiltGmsCore`)、Google ログイン用 WebView (`WebViewGoogle` / `TrichromeLibrary`)、キーボード (`LatinIMEGooglePrebuilt`)、アカウント・初期設定系 (`SetupWizardPrebuilt`, `GoogleOneTimeInitializer`, `PartnerSetupPrebuilt`, `ConfigUpdater`, `GoogleContactsSyncAdapter`, `ModuleMetadataGoogle`) はそのまま保持する。
3. **対象 `google_apis` の `system.img` から論理パーティションを取り出し**
   - `system.img`（GPT: `vbmeta` + `super`）の `super` から、`system`・`system_ext`・`vendor` および Android 14 の `system_dlkm`（`erofs`）を取り出す（`product` は手順 1〜2 で作成したスリム化済み `product_playstore.img` に置き換え、`system_dlkm` は無改変のまま維持する）。
4. **`system_ext.img` の GoogleServicesFramework / GoogleFeedback 差し替え（`replace_system_ext_google_apks`）**
   - `tune2fs -l` で純粋な ext4 サイズを取得して末尾の verity データを切り落とし、`resize2fs` で拡張後、`e2fsck -fy -E unshare_blocks` で `shared_blocks` を解除する。
   - `debugfs -w` で `priv-app/GoogleServicesFramework` と `priv-app/GoogleFeedback` の旧 `oat/` ディレクトリと旧 APK を削除し、`google_apis_playstore` から抽出した本番署名 APK を書き込んで mode `0644` および `security.selinux = "u:object_r:system_file:s0\0"` を設定する。
5. **`vendor.img` の TCG 高速化パッチ（`tune_vendor_partition`）**
   - `e2fsck -fy -E unshare_blocks` で書き込み可能化し、`debugfs stat` で各ファイルの元の `security.selinux` 拡張属性（`u:object_r:vendor_configs_file:s0` 等）を取得・保持したうえで、`debugfs -w` で以下の 6 ファイルを更新：
     - `etc/sensors/hals.conf`: 空ファイル（0 バイト）に置換し、センサー MultiHal がポーリングスレッドを起動しないようにする。
     - `etc/init/rild_goldfish.rc`: `service vendor.ril-daemon` に `disabled` を追加し、RIL / Telephony ブロードキャスト嵐を停止。
     - `etc/vintf/manifest.xml`: HIDL・AIDL 両形式（`format="(?:hidl|aidl)"`）の `android.hardware.radio*` HAL 定義をすべて削除し、`IRadio` / `IRadioConfig` への無限ポーリングを防止。
     - `etc/permissions/handheld_core_hardware.xml`: `android.hardware.telephony*` および `android.software.voice_recognizers` フィーチャーを削除。
     - `etc/fstab.ranchu`: zram スワップ設定が無い場合のみ 512 MiB の `/dev/block/zram0` を追記（Android 14 では既定の `zramsize=75%` を活用）。
     - `etc/init/hw/init.ranchu.rc`: `ro.radio.noril yes`、`persist.sys.zram_enabled 1`、`write /proc/sys/vm/swappiness 100` を設定し、`service goldfish-logcat` に `disabled` を追加。
6. **`system.img` 内の `/system/bin/init` の `sys_prop` タイムアウト延長パッチ（`patch_init_sysprop_timeout`）**
   - `INIT_TIMEOUT_PATTERNS` で `/system/bin/init` 内の `mov w10, #0x7d0`（2,000 ms）を検出し、`debugfs` の `bmap` / `icheck` で非共有ブロックであることを検証したうえで `mov w10, #<SYSPROP_TIMEOUT_MS>`（既定 60,000 ms = `0xea60`）へインプレース変更する。
7. **`super` の再構築と書き戻し**
   - `lpmake`（1 MiB アライン）で `system`・`system_ext`・`product`・`vendor`・`system_dlkm` を束ねた `super` を再構築し、対象 `system.img` の `super` オフセットへ書き戻す。
8. **冪等性の管理**
   - 統合が完了すると `var/sdk/system-images/android-34/google_apis/arm64-v8a/.playstore-from` に `v5.0-api34:debloat+vendor+vintf-radio-aidl:<SYSIMG_SHA256>:<PLAYSTORE_SHA256>:<SYSPROP_TIMEOUT_MS>` を記録する。
   - 次回以降の `bin/setup` はスタンプが一致していればスキップし、設定やアーカイブが変わった場合は `google_apis` を素の状態から展開し直して再適用する。
   - 起動後の `tune_guest`（[`bin/start`](../bin/start)）では、**GMS 内部サービスへの `pm disable` は厳禁**（GMS 側の自己修復 `setComponentEnabledSetting` と衝突して無限 `PACKAGE_CHANGED` 嵐と `phenotype.db` ロック詰まりを引き起こすため）。毎起動必要な設定（`SurfaceFlinger 1008 i32 1`、`cmd wifi set-ipreach-disconnect disabled`、`swappiness=100` 等）以外は `/data/local/tmp/.guest_tuned_v50` で初回のみ適用する冪等構成にしている。

---

## 5. 使い方と切り替え

### 標準（root + Google Play ストア）でセットアップ・起動

```sh
bin/setup
bin/start --wait-boot
```

起動後の確認コマンド（ポートを `config.local.sh` で変えている場合はシリアルを読み替える）：

```sh
# root の確認
adb -s emulator-5554 shell su 0 id
adb -s emulator-5554 root && adb -s emulator-5554 shell id

# Google Play ストア・GSF の確認
adb -s emulator-5554 shell pm path com.android.vending
# → package:/product/priv-app/Phonesky/Phonesky.apk
adb -s emulator-5554 shell pm path com.google.android.gsf
# → package:/system_ext/priv-app/GoogleServicesFramework/GoogleServicesFramework.apk
```

### Play ストア無し（素の `google_apis`）や軽量な AOSP (`default`) に戻す場合

`config.local.sh` に以下を書いて `bin/setup --recreate-avd` を実行する。

```sh
# 素の google_apis（GMS あり・Play ストアなし）
PLAYSTORE_URL=""
```

```sh
# 素の AOSP（GMS・Play ストアなし。起動が軽い）
SYSIMG_API="31"
SYSIMG_TAG="default"
SYSIMG_TAG_DISPLAY="Default Android System Image"
SYSIMG_ARCHIVE="android-arm64-v8a-31_r03.zip"
SYSIMG_URL="https://dl.google.com/android/repository/sys-img/android/arm64-v8a-31_r03.zip"
SYSIMG_SHA256="75e18261311189a641b6880e4427d5f90b7c4e9b0cc7c4ff38c70b6f447b39d8"
PLAYSTORE_URL=""
AVD_NAME="aosp31"
```

---

## 6. 検証結果（2026-11-22 Android 14 / API 34 実測）

環境: Cortex-A78 ×4 + Cortex-A55 ×4 / メモリ 6.5 GiB + ホストスワップ 8 GiB（Chromebook Crostini + Arch Linux ARM コンテナ、`AVD_RAM_MB=2048`、`TCG_CACHE_MB=256`、`AVD_CORES=2`、ゲスト内 `zram0=75%` (~1,483 MiB)、`HW_TIMEOUT_MULTIPLIER=20`、`SYSPROP_TIMEOUT_MS=60000`）。

- **`bin/setup --recreate-avd`（アーカイブ取得済み状態）**: 展開・Play ストア `product`（不要重量アプリ 34 個削除）+ `system_ext` 統合・`system_dlkm` 維持・`vendor` 高速化パッチ・`init` パッチ・`apexd.config.*` 書き込み・AVD 作成まで約 3 分。再実行時はすべてスキップされることを確認。
- **APEX 有効化**: `apexd` **34/34 パッケージすべて成功**（失敗・`updatable_crashing`・再起動ループ **0 回**）。
- **初回コールドブート（`bin/start --wait-boot`、AVD 作成直後、`AVD_CORES=3` で実測）**:
  - `adb` 認識: **292 秒** / `boot_completed`: **1,166 秒** / 合計 **26 分 20 秒**（クラッシュ・再起動 **0 回**）
- **2 回目以降ウォームブート＆操作性能（アカウント登録済み `gplay34`、コア数別クリーン比較）**:

| 項目 | `AVD_CORES=1` | **`AVD_CORES=2`（最良・既定）** | `AVD_CORES=3` |
|---|---:|---:|---:|
| `adb` 認識 | 208 秒 | **203 秒** | 206 秒 |
| `boot_completed` | 500 秒 | **376 秒** | 401 秒 |
| ウォームブート合計 | 12 分 20 秒 | **10 分 09 秒** | 10 分 28 秒 |
| Settings 起動 (`Displayed`) | >247 秒 (timeout) | **75.0 秒** | 172.3 秒 |
| `pm list packages` | 12,925 ms | **10,335 ms** | 11,816 ms |
| ホスト `cs/s` | 3,800〜4,700 /秒 | **46,800〜58,100 /秒** | 129,700〜168,000 /秒 |

- **root 権限**:
  - `adb shell su 0 id` → `uid=0(root) gid=0(root) ... context=u:r:su:s0`
  - `ro.build.version.release=14` / `ro.build.version.sdk=34`
- **Google アカウントサインイン & 正規 Google Play ストア (`com.android.vending`)**:
  - `pm path com.android.vending` → `package:/product/priv-app/Phonesky/Phonesky.apk`
  - `pm path com.google.android.gsf` → `package:/system_ext/priv-app/GoogleServicesFramework/GoogleServicesFramework.apk`
  - WebView `113.0.5672.136` + `auth_minute_maid_timeout=600000` により、`am start -a android.settings.ADD_ACCOUNT_SETTINGS` 経由で Google アカウントのメール・パスワード・利用規約同意・`AccountManager` 登録まで完了し、サインイン済み Play ストア（`AssetBrowserActivity`、プロフィールアイコン表示）の起動を確認。

### 補足: 初回起動後に `Pixel is starting...` のまま切り替わらない場合

`sys.boot_completed=1` 到達後に画面が `Pixel is starting...`（`FallbackHome`）のまま残っている場合、エミュレータ用の初回スタブ（Android 14 では `com.google.android.googlesdksetup/.DefaultActivity`、Android 12 では `com.google.android.sdksetup/.DefaultActivity`）が無効化されず残っていることがある。[`bin/start`](../bin/start) で自動無効化しているが、手動で戻す場合は以下を実行する：

```sh
adb -s emulator-5554 shell su 0 pm disable com.google.android.googlesdksetup/.DefaultActivity
adb -s emulator-5554 shell am start -a android.intent.action.MAIN -c android.intent.category.HOME
```
