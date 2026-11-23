# android-emulator-arm64-tcg

ARM Chromebook の Linux 環境（aarch64・KVM なし）で Android Emulator を起動し、`adb` から使えるようにする。

- 取得するものの URL・バージョン・チェックサムは [`config.sh`](config.sh) に固定してある
- 取得・生成したものはすべて `var/`（git 管理外）に置く。リポジトリの外には何も書き込まない
- エミュレータ本体は非公式ビルドなので、サンドボックスの中でだけ実行する
- こうなっている理由は [`docs/`](docs/) に残してある

## 使い方

```sh
bin/setup      # 初回のみ。取得・検証・展開・AVD 作成
bin/start      # 起動し、adb に認識されるまで待つ
adb devices    # → emulator-5554   device
```

| コマンド | すること |
|---|---|
| `bin/setup` | エミュレータ本体とシステムイメージを取得して SHA-256 を照合し、`var/` に展開・Google Play ストアを組み込んで AVD を作る。何度実行してもよい |
| `bin/start` | エミュレータをバックグラウンドで起動し、`adb devices` に `device` として現れるまで待つ（既定で GUI ウィンドウを直接表示。`--headless` でウィンドウ無し起動） |
| `bin/start --wait-boot` | さらに Android の起動完了（`sys.boot_completed=1`）まで待ち、アニメーション・背景ブラー・不要ジョブ無効化などの高速化設定を適用する |
| `bin/start --headless` | ウィンドウを表示せずヘッドレスで起動する（画面が必要なときだけ `bin/screen` を使用） |
| `bin/start --cold` | スナップショットを読み込まずにコールドブートする |
| `bin/status` | プロセス・adb での認識・Android の起動状況を表示する |
| `bin/screen` | `scrcpy` でエミュレータの画面をウィンドウに表示し、マウス・キーボードで操作する |
| `bin/stop` | 停止前にゲストの未書き込みデータをディスクへ反映し、エミュレータを停止する |
| `bin/emulator <引数>` | エミュレータ本体をサンドボックス内で直接実行する（`-version`、`-help` などの確認用） |

起動後は普段どおり `adb` を使う。シリアルは既定で `emulator-5554`（`config.local.sh` でポートを変えた場合はそちら）。

```sh
adb -s emulator-5554 shell getprop ro.build.version.release   # → 14
adb -s emulator-5554 shell su 0 id                            # → uid=0(root)
adb -s emulator-5554 shell pm path com.android.vending        # → /product/priv-app/Phonesky/Phonesky.apk
adb -s emulator-5554 install app.apk
adb -s emulator-5554 logcat
```

既定ではエミュレータ自身の GUI ウィンドウ（Qt/X11、`DISPLAY=:0` 上でスケーリング無効の 1:1 ピクセルパーフェクト `360x640` 表示）を直接開く。ヘッドレスで起動した場合は `bin/screen`（要 `scrcpy`）、静止画で確認したいときはスクリーンショットを取る。

```sh
adb -s emulator-5554 exec-out screencap -p > screen.png
```

## 前提

- Linux aarch64。エミュレータ本体の配布元は glibc 2.43 以降を要件としている。確認した環境は Arch Linux ARM
- 空きディスク 12 GB 以上（Play ストア注入時の一時作業領域を含む。完了後の `var/` は約 9 GB）、ホストメモリ＋スワップ合計 8 GB 以上推奨（Chromebook Crostini 等メモリ 6.5 GiB 環境では `AVD_RAM_MB=2048` 運用のためホスト側に 8 GiB 程度のスワップファイルを有効化推奨。スワップが無い環境は `config.local.sh` で `AVD_RAM_MB="1024"` / `TCG_CACHE_MB="128"` へ下げる）
- パッケージ:

  ```sh
  sudo pacman -S --needed android-tools bubblewrap curl e2fsprogs python tar xz unzip util-linux procps-ng libx11
  ```

  `android-tools` が `adb`・`lpdump`・`lpmake`、`bubblewrap` がサンドボックス（`bwrap`）、
  `e2fsprogs` と `python` が Google Play ストアのオフライン組み込みに使われる。
  画面をウィンドウで表示する `bin/screen` を使う場合は `scrcpy` も入れる。
  `util-linux`（`setsid`、`taskset`、libuuid）と `procps-ng`（`pgrep`、`ps`）は通常は導入済み。

Java、Android Studio、`sdkmanager`、`avdmanager` は使わない。

## 所要時間とリソースの目安

KVM が無いため CPU をソフトウェアでエミュレーションしており、起動には分単位の時間がかかる。
下は Cortex-A78 ×4 + Cortex-A55 ×4 / メモリ 6.5 GiB + ホストスワップ 8 GiB の Chromebook での実測値（2026-11-22、Android 14 `google_apis` + Play ストア構成、`gplay34`）。

| 項目 | 実測 |
|---|---|
| `bin/setup` | ダウンロード約 3.0 GB（回線次第）+ 展開・Play ストア統合・軽量化 約 3 分 |
| `bin/start` で adb に `device` として現れるまで | 約 3 分半（ウォームブート **`AVD_CORES=2` で 203 秒**、初回コールドブート 292 秒） |
| `bin/start --wait-boot` で Android の起動完了まで | 初回コールドブート 約 26 分 20 秒（2 回目以降ウォームブートは **`AVD_CORES=2` で 10 分 09 秒**、`3` で 10 分 28 秒、`1` で 12 分 20 秒） |
| ディスク（`var/`） | 約 9 GB |
| メモリ（エミュレータのプロセス） | 約 2.5 GiB（`AVD_RAM_MB=2048` + ゲスト内 `zram0` ~1.48 GiB + `TCG_CACHE_MB=256`、ホストスワップ使用 ~0.4 GiB） |
| CPU の計算速度（同じ静的バイナリの整数演算ループ） | ホストの高性能コアで 0.35 秒 → エミュレータ内で 1.8〜2.0 秒（約 5 倍遅い） |

> **遅さについて**: エミュレータのバイナリは aarch64 ネイティブだが、Android 側の CPU 命令は
> すべてソフトウェアで変換して実行している。ホストと同じ arm64 の命令でも、直接実行するには KVM が要る。
> 単純な計算で約 5 倍、メモリアクセスやシステムコールが絡む実際の処理ではそれ以上に遅い。
>
> Android 14 では `ro.hw_timeout_multiplier=20`（[`config.sh`](config.sh) の `HW_TIMEOUT_MULTIPLIER`）に加え、
> `/system/bin/apexd` の dm-verity 作成タイムアウト延長（`apexd.config.dm_create.timeout=30000` /
> `boot_activation.threads=1`）と `/system/bin/init` にハードコードされたプロパティ設定ソケット（`sys_prop`）の
> 2 秒タイムアウトを 60 秒（`SYSPROP_TIMEOUT_MS=60000`）へオフラインパッチしているため、34 個の APEX と
> `system_server` が落ちずに起動を完了できる。さらに `bin/start --wait-boot` で HWC3 黒画面回避・Wi-Fi 切断防止・
> Google サインイン WebView タイムアウト延長・`DeviceConfig` 同期暴走抑止を自動適用する。
> 詳細は [docs/background.md](docs/background.md#起動に非常に時間がかかる) と [docs/playstore-and-root.md](docs/playstore-and-root.md)。

## 仕組み

```
  このコンテナ（普段の環境）                    サンドボックス (bubblewrap)
 ┌──────────────────────────┐        ┌────────────────────────────────────────┐
 │ adb (android-tools)      │        │ emulator → qemu-system-aarch64         │
 │   adb サーバー :5037     │◀──────▶│   コンソール 127.0.0.1:5554            │
 │   鍵 ~/.android/adbkey   │  TCP   │   adb        127.0.0.1:5555            │
 │   X11 (/tmp/.X11-unix)   │◀──────▶│     └ Android 14 (arm64) を TCG で実行 │
 │ bin/start, bin/stop ...  │        │ 見えるのは var/ の一部と /usr だけ     │
 └──────────────────────────┘        └────────────────────────────────────────┘
```

- **エミュレータ本体**は、AOSP のソースに KVM なし対応のパッチを当てた非公式ビルド（35.6.3）。
  Google は Linux aarch64 向けを配布していない。→ [docs/emulator-binary.md](docs/emulator-binary.md)
- **システムイメージ**は Google 公式の Android 14 (API 34 / `34_r14`) `google_apis`（`userdebug`・arm64-v8a）をベースに、
  同じく公式の `google_apis_playstore` から `product` パーティション（不要な重量プリインストールアプリ 34 個を除いた
  Play ストア・GMS・WebView 113・キーボード等）と `system_ext` の Google 署名パッケージ
  （`GoogleServicesFramework.apk` / `GoogleFeedback.apk`）を `bin/setup` 時にオフライン統合（`system_dlkm` は維持）し、
  さらに `/system/bin/init` の `sys_prop` タイムアウト延長、`data/local.prop` の `apexd.config.*` 延長、
  `vendor` パーティションの高速化パッチ（センサーポーリング・HIDL/AIDL RIL 無効化、`zram0` スワップ設定）を当てたもの。
  `adb root` / `su 0` と正規 Google Play ストア（アカウントサインイン済）、`ro.hw_timeout_multiplier` を両立している。
  → [docs/playstore-and-root.md](docs/playstore-and-root.md)
- **CPU** は QEMU の TCG によるソフトウェアエミュレーション、**描画**は SwiftShader によるソフトウェア描画。
- **adb** は OS のパッケージのものをそのまま使う。adb サーバーは `127.0.0.1:5555` で待ち受ける
  エミュレータを自動的に見つけ、`emulator-5554` として一覧に載せる（`adb connect` は不要）。
- **サンドボックス**はネットワークだけを外と共有し、ホームディレクトリや他のプロセスは見えない。
  → [docs/emulator-binary.md](docs/emulator-binary.md#リスクへの対処-サンドボックス)

## ファイル構成

```
config.sh                取得物の URL・SHA-256、AVD の設定、ポートなど（設定はここだけ）
bin/                     上の表のコマンド
lib/common.sh            共通処理（パスの定義、サンドボックスの組み立て）
lib/inject-playstore.py  google_apis の system.img へ Play ストアをオフライン注入する処理
docs/background.md       環境の制約と、検討した手段、起動までに踏んだ問題
docs/emulator-binary.md  エミュレータ本体の出所・確認内容・残るリスク
docs/playstore-and-root.md  root と Google Play ストアを両立させる手順・仕組み・検証記録
var/                     生成物（git 管理外。消しても bin/setup で元に戻る）
  downloads/             取得したアーカイブ
  sdk/                   ANDROID_SDK_ROOT に相当（emulator/、system-images/）
  avd/                   AVD（仮想デバイスの設定とディスクイメージ）
  home/                  サンドボックス内の $HOME（~/.android の代わり）
  tmp/                   サンドボックス内の /tmp
  log/emulator.log       エミュレータの出力とゲストのカーネルログ
  run/emulator.pid       起動中のサンドボックスの PID
```

### リポジトリの外にあって、このリポジトリが依存しているもの

| もの | 場所 | 備考 |
|---|---|---|
| 「前提」のパッケージ | `/usr` | pacman で管理 |
| adb サーバー | 常駐プロセス（TCP 5037） | `bin/start` が `adb start-server` で起動する。`bin/stop` では止めない |
| adb の鍵 | `~/.android/adbkey`, `adbkey.pub` | adb が初回に自動生成する。`bin/start` は公開鍵だけを `var/home/.android/` にコピーする |
| ダウンロード元 | github.com、dl.google.com | `bin/setup` の実行時のみ |

これ以外に、リポジトリの外のファイルを読み書きしない。

## 構成を変える

設定はすべて [`config.sh`](config.sh) にある。手元だけ変えたい値は、同じ変数を `config.local.sh`（git 管理外）に書く。

- **メモリ・コア数・画面サイズ**: `AVD_RAM_MB` と `AVD_CORES` は次の `bin/start` から反映される。画面サイズ・データ容量を変えた場合は `bin/setup --recreate-avd`（エミュレータ内のデータは消える）。
- **ポート**: `EMU_CONSOLE_PORT`。5554〜5584 の偶数にすること（adb サーバーが自動認識する範囲）。
- **Google Play ストアの有効／無効**: 既定では `google_apis` イメージに Play ストアをオフライン注入する。素の `google_apis` や軽量な AOSP (`default`) に切り替えたい場合は [docs/playstore-and-root.md](docs/playstore-and-root.md#5-使い方と切り替え) を参照。
- **別のシステムイメージ**: `SYSIMG_*` を書き換えて `bin/setup --recreate-avd`。
  使えるのは `arm64-v8a` のイメージだけ。一覧と URL は Google のインデックスにある。

  ```sh
  curl -s https://dl.google.com/android/repository/sys-img/google_apis/sys-img2-3.xml | grep -o 'arm64-v8a[^<]*\.zip'
  ```

  `google_apis` の部分は `android`（素の AOSP）、`aosp_atd`、`google_atd`、`google_apis_playstore` などに替えられ、
  URL は `https://dl.google.com/android/repository/sys-img/<種類>/<ファイル名>` になる。
  `SYSIMG_SHA256` には、取得したファイルを自分で `sha256sum` した値を書く
  （インデックスに載っているのは SHA-1 なので、`sha1sum` の結果と突き合わせてから）。
  新しい Android ほど重く、起動に時間がかかる。このリポジトリで動作確認済みなのは API 30 ATD、API 31 `default` / `google_apis` + Play ストア注入、および現在の **API 34 (Android 14) `google_apis` + Play ストア注入**。
- **エミュレータ本体**: [docs/emulator-binary.md](docs/emulator-binary.md#別のビルドに替える自分でビルドする)

## 困ったとき

まず `bin/status` と `var/log/emulator.log` を見る。

| 症状 | 原因と対処 |
|---|---|
| `adb devices` に出てこない | `bin/status` でプロセスが動いているか確認する。動いていれば `adb kill-server && adb start-server` で adb サーバーに探し直させる |
| `offline` のまま進まない | ゲストの adbd がまだ起動していない。ログの末尾が進んでいれば待つ |
| ログに `bootstrap-apexd-failed` や `updatable_crashing` が出て、カーネルの起動が繰り返される | エミュレーションが遅すぎるか、Android 14 の `apexd` 既定 1 秒 dm-verity タイムアウトに引っかかっている。`bin/start` の出力に「CPU … に固定しました」が出ていること、`bin/setup` で `apexd.config.*` が `data/local.prop` に入っていることを確認する。→ [docs/playstore-and-root.md](docs/playstore-and-root.md) |
| 画面が真っ黒のまま映らない（Android 14） | HWC3 の `DEVICE` 合成と `swiftshader_indirect` の相性問題。`bin/start --wait-boot` が `service call SurfaceFlinger 1008 i32 1`（CLIENT 合成固定）を自動適用する |
| `bwrap: ... Operation not permitted` | ユーザー名前空間が使えない環境。`unshare -Ur true` が通るか確認する |
| `ポート 5554 が使用中です` | 別のエミュレータが動いている。止めるか `EMU_CONSOLE_PORT` を変える |
| ログに `A snapshot operation for '...' is pending and timeout has expired` が出て終了する | 前回の強制終了で `var/avd/<AVD_NAME>.avd/*.lock`（`snapshot.lock.lock` 等）が残っている。`bin/start` が起動前に自動削除する |
| ログの `libStubXlib.so ... cannot be preloaded` と `Failed to initialize global Vulkan emulation` | 無害。前者は同梱されていない補助ライブラリ、後者はホストに Vulkan が無いため |
| 途中で消える・`earlyoom` に殺される・Crostini 再起動後に落ちる | メモリ不足（またはホストスワップ未有効化）。Crostini は再起動時に `/etc/fstab` のスワップを有効化しないため、`sudo swapon /swap/swapfile` を再実行するか、`AVD_RAM_MB` を `1024`〜`2048` に調整する（`3056` を超えると `ramoops` 領域と重なりゲストカーネルがパニックする）。ホスト側の `earlyoom` を恒久停止したい場合は `distrobox-host-exec sudo systemctl disable --now earlyoom` |
| 最初からやり直したい | `bin/stop && rm -rf var && bin/setup` |

## 利用条件について

- システムイメージは Google の [Android SDK ライセンス](https://developer.android.com/studio/terms)のもとで配布されている。
  `bin/setup` は `sdkmanager` と違って同意の確認を挟まずにダウンロードするので、利用者が各自で条件を確認すること。
- エミュレータ本体は GPLv2 / Apache 2.0 のソフトウェアを第三者がビルドしたもの。
  Google や AOSP が提供・保証するものではない。
