# shellcheck shell=bash
#
# このリポジトリが取得するものと AVD の設定を、ここ 1 か所で定義する。
# lib/common.sh から source される（単体では実行しない）。
#
# 手元だけ値を変えたいときは、同じ変数を config.local.sh（git 管理外）に書く。
# 取得物を差し替えるときは URL とチェックサムを必ずセットで更新すること
# （手順は README.md「構成を変える」を参照）。

# --- Android Emulator 本体 ---------------------------------------------------
# Google は Linux aarch64 向けのエミュレータを配布していないため、AOSP のソースに
# KVM なし (TCG) 対応パッチを当てた非公式ビルドを使う。出所とレビュー内容は
# docs/emulator-binary.md。
EMULATOR_VERSION="35.6.3"
EMULATOR_ARCHIVE="android-emulator-35.6.3-linux-aarch64.tar.xz"
EMULATOR_URL="https://github.com/Mr0btain/Android-Emulator-Arm64/releases/download/v35.6.3-3/android-emulator-35.6.3-linux-aarch64.tar.xz"
EMULATOR_SHA256="7dc6b2fbb9ad30167a4a4a581c26c12fde49a4fe10a57e33fc0adb6975822c32"

# --- システムイメージ（Google 公式配布） -------------------------------------
# Android 14 (API 34) の Google APIs イメージ（sdk_gphone64_arm64-userdebug）。選んだ理由:
#   - userdebug ビルドなので root が使える（adb root、adb shell su 0 <コマンド>）
#   - userdebug なので /data/local.prop が読まれ、HW_TIMEOUT_MULTIPLIER や apexd.config.* が効く
#   - Google Play 開発者サービス (GMS) や com.android.vending の権限設定が同梱されている
SYSIMG_API="34"
SYSIMG_TAG="google_apis"
SYSIMG_TAG_DISPLAY="Google APIs"
SYSIMG_ABI="arm64-v8a"
SYSIMG_ARCHIVE="google_apis-arm64-v8a-34_r14.zip"
SYSIMG_URL="https://dl.google.com/android/repository/sys-img/google_apis/arm64-v8a-34_r14.zip"
SYSIMG_SHA256="bd1c323cbdd3c9dd55dde54588b298d172fd126fe16e127a7bd7aeab6897890a"

# --- Google Play ストアの組み込み --------------------------------------------
# google_apis には Play ストア本体 (Phonesky64.apk) が含まれない一方、Google 公式の
# google_apis_playstore は user ビルド（本番用）のため root も /data/local.prop も使えない。
# また google_apis 内の GMS はテスト鍵 (dev-keys)、playstore の Phonesky64 は本番鍵
# (release-keys) のため APK 単体移植では署名不一致で動かない。
# そこで system / system_ext / vendor / ramdisk は google_apis (userdebug) のまま残し、
# bin/setup が super 内の product パーティションだけ google_apis_playstore 版へ差し替える。
# 空文字にすると差し替えない（素の google_apis として使う）。
PLAYSTORE_ARCHIVE="google_apis_playstore-arm64-v8a-34_r14.zip"
PLAYSTORE_URL="https://dl.google.com/android/repository/sys-img/google_apis_playstore/arm64-v8a-34_r14.zip"
PLAYSTORE_SHA256="8359fc54b9c91465b8cbc74816e581d143d63deeeded445a2761738b74ea6f67"

# --- init の sys_prop タイムアウト延長 ---------------------------------------
# Android 12 の /system/bin/init (property_service.cpp) は ro.hw_timeout_multiplier を
# 参照せず 2000 ms (2 秒) 固定のため、TCG 高負荷時に system_server がタイムアウトで
# 再起動することがある。bin/setup が /system/bin/init の即値 (4 バイト) をこの値 (ms)
# へ延長する（2000〜65535）。空文字にすると書き換えない。
SYSPROP_TIMEOUT_MS="60000"

# 以前使っていた Android 12 (API 31) の素の AOSP イメージ（GMS・Play ストアなし、軽め）。
#   SYSIMG_API="31"  SYSIMG_TAG="default"  SYSIMG_TAG_DISPLAY="Default Android System Image"
#   SYSIMG_ARCHIVE="android-arm64-v8a-31_r03.zip"
#   SYSIMG_URL="https://dl.google.com/android/repository/sys-img/android/arm64-v8a-31_r03.zip"
#   SYSIMG_SHA256="75e18261311189a641b6880e4427d5f90b7c4e9b0cc7c4ff38c70b6f447b39d8"
#   PLAYSTORE_URL=""
#   AVD_NAME="aosp31"

# さらに以前使っていた Android 11 (API 30) の ATD イメージ（UI なしの自動テスト向け）。
# Android 11 はタイムアウトを延ばせず、起動途中でフレームワークが何度も落ちる（docs/background.md）。
#   SYSIMG_API="30"  SYSIMG_TAG="aosp_atd"  SYSIMG_TAG_DISPLAY="AOSP ATD"
#   SYSIMG_ARCHIVE="aosp_atd-arm64-v8a-30_r02.zip"
#   SYSIMG_URL="https://dl.google.com/android/repository/sys-img/aosp_atd/arm64-v8a-30_r02.zip"
#   SYSIMG_SHA256="1e34054eeb0cccfa9a682eb6f00f3a5c6a312ab7713e9bd3f7a8b673da5b58b6"

# --- ゲスト内のタイムアウトの倍率 -------------------------------------------
# Android は「60 秒応答が無ければ system_server を強制終了」「20 秒でサービスが始まらなければ ANR」
# といった時間を実機の速さ前提で決めている。ソフトウェアエミュレーションではこれに次々と引っかかり、
# 起動が終わらない。Android 12 以降は ro.hw_timeout_multiplier でまとめて延ばせるので、その倍率を指定する。
# bin/setup がイメージの初期データ (data/local.prop) に書き込み、AVD の初回起動時に /data へ入る。
# 変更を反映するには bin/setup --recreate-avd が必要。空文字にすると設定しない。
HW_TIMEOUT_MULTIPLIER="20"

# --- AVD（仮想デバイス） -----------------------------------------------------
AVD_NAME="gplay34"
# ゲストが使うメモリ (MB) と仮想 CPU 数。bin/start が起動のたびに渡すので、
# 変更は次の起動から効く（AVD の作り直しは不要）。
# 注意:
#   1. ChromeOS (Crostini) は virtio_balloon でホスト VM の 6.5 GiB のうち 3 GiB 以上を
#      回収するため、ホストに swap が無い環境では 1024 MB / TCG 128 MB に抑える必要がある。
#      一方、Google Play ストア + GMS + Chromium WebView（サインイン画面）を同時に動かすと
#      ゲスト内消費が 1.2〜1.4 GiB に達するため、1024 MB では zram0 スワップとファイルキャッシュ
#      追い出しが連鎖し、WebView 起動だけで 100 秒超かかってサインインがタイムアウトする。
#      ホスト側に十分な swap（推奨 8 GiB）がある環境では 2048 MB / TCG 256 MB に設定する。
#      （ホスト swap が無い場合は config.local.sh で AVD_RAM_MB="1024" / TCG_CACHE_MB="128" に下げる）
#   2. arm64 ranchu では物理アドレス 0xff018000（RAM 先頭 0x40000000 から 3056 MB 地点）に
#      ramoops 予約領域があるため、3056 MB を超える値（3072 など）を指定すると起動完了直後の
#      /proc/pagetypeinfo 読み取り (pagetypeinfo_showfree_print) でゲストカーネルが毎回パニックする。
#   3. このエミュレータは QEMU 2.12.0 ベースのため、MTTCG (AVD_CORES >= 2) では ARMv8.0 排他命令
#      (LDAXR/STLXR) や TLBI ...IS で start_exclusive() の futex 同期コストが発生する。
#      Cortex-A78 ×4 構成での Android 14 (gplay34) クリーンウォームブート実測比較:
#        1 コア: 起動 12:20 / Settings >247s / cs 4,300/s   (MTTCG 同期ゼロだが 1 コア奪い合いで極遅)
#        2 コア: 起動 10:09 / Settings  75.0s / cs 52,000/s (最速・大コア 2 本空きで GUI/ホストも軽い) ← 既定
#        3 コア: 起動 10:28 / Settings 172.3s / cs 151,000/s (Android 14 では MTTCG 同期が 3 倍に悪化し逆転)
AVD_RAM_MB="2048"
AVD_CORES="2"

# 変換済みコード (TCG) のキャッシュの上限 (MB)。空文字ならエミュレータ任せ（割り当てメモリの 1/4）。
# エミュレータのプロセスは「ゲストのメモリ + このキャッシュ + 描画用などのヒープ数百 MB」を使う。
# ホスト swap が無い環境では 128 MB に抑えるが、ホスト swap (8 GiB) ありなら 256 MB にして
# GMS と WebView の JIT ブロック追い出しを減らす。
TCG_CACHE_MB="256"

# 画面解像度と密度。SwiftShader は CPU で全ピクセルをソフトウェア描画するため、
# 同じ論理サイズ (360x640 dp) のまま 480x800 (240 dpi) → 360x640 (160 dpi) に下げて
# 描画ピクセル数を 40% 削減し、画面描画・スクロールを軽くしている。
AVD_LCD_WIDTH="360"
AVD_LCD_HEIGHT="640"
AVD_LCD_DENSITY="160"
AVD_DATA_SIZE="2G"

# スナップショット（Quickboot）を使うかどうか（yes / no）。
# この環境（btrfs コンテナ）では FileBacked Quickboot が無効化されて qcow2 保存になり、
# メモリと I/O 負荷が増えるため既定は no（毎回コールドブート）とする。
AVD_SNAPSHOT="no"

# エミュレータ自身の GUI ウィンドウ (Qt/X11) を直接表示するかどうか（yes / no）。
# yes にすると scrcpy (ゲスト内のソフトウェア H.264 動画エンコード負荷 30〜50% CPU) を
# 使わずにホスト側で直接画面を表示できる（X11 ソケットが無い場合は自動でヘッドレスにフォールバック）。
EMU_WINDOW="yes"

# --- エミュレータを走らせる CPU コア -----------------------------------------
# ランチャーは自分自身を先頭のコア群 (CPU 0-3) に固定するが、big.LITTLE 構成の CPU では
# そこが低速な省電力コアであることが多く、起動が 2 倍以上遅くなる。bin/start が固定し直す。
#   auto        性能の違うコアが混在していれば最速のコア群へ。混在していなければ何もしない
#   空文字      固定し直さない（ランチャー任せ）
#   4-7 など    taskset に渡す CPU リストを直接指定
EMU_CPUS="auto"

# --- ポート -----------------------------------------------------------------
# コンソール用ポート。adb 用は +1。adb 上のシリアルは emulator-<このポート> になる。
# adb サーバーが自動認識するのは 5554〜5584 の偶数だけ。
EMU_CONSOLE_PORT="5554"

# --- 待ち時間の上限（秒） ---------------------------------------------------
ADB_WAIT_TIMEOUT="900"     # bin/start: adb に device として現れるまで
BOOT_WAIT_TIMEOUT="3600"   # bin/start --wait-boot: Android の起動完了まで
