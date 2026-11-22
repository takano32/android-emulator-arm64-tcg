# なぜこの構成なのか（調査記録）

「Android Emulator を起動して adb に認識させる」だけのことが、この環境では素直にできない。
何が障害で、何を調べ、なぜ今の構成に落ち着いたかを残す。

調査日: 2026-11-20

## 対象の環境

| 項目 | 内容 | 確認方法 |
|---|---|---|
| ハードウェア | ARM Chromebook。Cortex-A55 ×4 (CPU 0-3) + Cortex-A78 ×4 (CPU 4-7) | `lscpu` |
| 実行場所 | ChromeOS の Linux 開発環境 (Crostini) の VM 内で動く rootless podman コンテナ（distrobox） | `systemd-detect-virt`, `/run/.containerenv` |
| OS | Arch Linux ARM (aarch64)、glibc 2.43 | `/etc/os-release` |
| カーネル | 6.6.147（ChromeOS の VM 用カーネル。コンテナからは変更できない） | `uname -r` |
| メモリ / ディスク | 6.5 GiB（スワップなし）/ 空き 45 GB | `free -h`, `df -h` |
| adb | 37.0.0（pacman の `android-tools`。aarch64 ネイティブ） | `adb version` |

## 障害は 3 つ

### 1. KVM が無い

`/dev/kvm` が存在せず、そもそもカーネルが仮想化支援なしでビルドされている。

```console
$ ls /dev/kvm
ls: cannot access '/dev/kvm': No such file or directory
$ zcat /proc/config.gz | grep CONFIG_VIRTUALIZATION
# CONFIG_VIRTUALIZATION is not set
```

コンテナや VM の設定で解決できる話ではないので、CPU はソフトウェアでエミュレーションする
（QEMU の TCG）しかない。Android Emulator は通常 KVM を前提にしており、TCG では大幅に遅くなる。

### 2. Google は Linux aarch64 向けのエミュレータを配布していない

- **SDK リポジトリに無い。** `sdkmanager` が参照するインデックスに載っているエミュレータは
  Linux x86_64 / macOS (x86_64, aarch64) / Windows (x86_64, aarch64) だけ。
  Linux で `host-arch` が aarch64 のパッケージは、エミュレータに限らず 1 つも無い。

  ```console
  $ curl -s https://dl.google.com/android/repository/repository2-3.xml \
      | grep -o '<url>emulator[-_][a-z]*_[a-z0-9]*' | sort -u
  <url>emulator-darwin_aarch64
  <url>emulator-darwin_x64
  <url>emulator-linux_x64
  <url>emulator-windows_x64
  <url>emulator_darwin_aarch64
  <url>emulator_linux_x64
  <url>emulator_windows_aarch64
  <url>emulator_windows_x64
  ```

- **公式 CI のビルドも入手できない。** Android の CI (ci.android.com) にはかつて
  `aosp-emu-master-dev` ブランチの `emulator-linux_aarch64` ターゲットがあったが、
  最後のビルドは 12929531（2025-01-16）で、成果物の zip はすでに削除されている。

  ```console
  $ curl -s -o /dev/null -w '%{redirect_url}\n' \
      https://ci.android.com/builds/latest/branches/aosp-emu-master-dev/targets/emulator-linux_aarch64/view/BUILD_INFO
  https://ci.android.com/builds/submitted/12929531/emulator-linux_aarch64/latest/view/BUILD_INFO
  $ curl -sL https://ci.android.com/builds/submitted/12929531/emulator-linux_aarch64/latest/raw/sdk-repo-linux_aarch64-emulator-12929531.zip
  <?xml version='1.0' encoding='UTF-8'?><Error><Code>NoSuchKey</Code>...
  ```

  エミュレータのソース (`platform/external/qemu`) にあるブランチのうち、名前に emu / qemu を含む
  55 個すべてについて同じ問い合わせをしたが、ビルドが存在するのはこのブランチだけだった。

- 仮に公式ビルドが手に入っても、KVM 無しでは起動しない
  （[emulator-binary.md](emulator-binary.md) の「このビルドを選んだ理由」）。

### 3. コンテナ型の Android も使えない

Waydroid や redroid は、ホストのカーネルの binder を使って Android を直接動かすので
エミュレーションが不要だが、この環境では `/dev/binder` がコンテナの外のユーザー所有
（コンテナ内からは `nobody`、モード 0600）で開けず、binderfs もカーネルに入っていない。

```console
$ ls -l /dev/binder
crw------- 1 nobody nobody 10, 257 ... /dev/binder
$ zcat /proc/config.gz | grep ANDROID_BINDERFS
# CONFIG_ANDROID_BINDERFS is not set
```

## 検討した手段

| 手段 | 結果 |
|---|---|
| 公式 SDK のエミュレータ | Linux は x86_64 版のみ。不可 |
| 公式 CI の `emulator-linux_aarch64` ビルド | 成果物が削除済み。不可 |
| 公式の x86_64 版を `qemu-user` 経由で実行 | エミュレータ自体をエミュレーションしたうえで、その中で Android の CPU をエミュレーションすることになる。試していない |
| この PC でソースからビルド | ソース約 17 GB + ビルドツリー約 36 GB、RAM 14 GB で約 50 分という報告に対し、空き 45 GB・RAM 6.5 GB。不可 |
| **非公式ビルド（KVM なし対応パッチ入り）** | **採用**。出所の確認内容は [emulator-binary.md](emulator-binary.md) |
| Waydroid / redroid | binder が使えない。不可 |
| pacman の `qemu-system-aarch64` で Android を直接起動 | Android Emulator 用のシステムイメージは専用の仮想デバイス（goldfish / ranchu）を前提にしているので、そのままでは動かないと見込んだ。試していない |
| ChromeOS 自身の Android (ARC) に `adb connect` | エミュレータではなく Chromebook 本体の Android に繋ぐ方法。ChromeOS 側の設定変更が必要。試していない |

## 採用した構成で起動するまでに踏んだ問題

どれも [`bin/setup`](../bin/setup) / [`bin/start`](../bin/start) に対処を入れてある。

1. **SDK ルートの検査で起動を拒否される。**
   エミュレータは `ANDROID_SDK_ROOT` に `platforms/` と `platform-tools/` が無いと
   `PANIC: Broken AVD system path` で終了する。
   → `bin/setup` が空の `platforms/` と、システムの adb へのシンボリックリンクだけを置いた
   `platform-tools/` を作る。

2. **ランチャーがエミュレータを遅いコアに固定する。**
   `emulator` は自分自身の CPU アフィニティを CPU 0-3 に設定してから QEMU 本体を起動する。
   この機種の CPU 0-3 は省電力コア（Cortex-A55、`cpu_capacity` 444）で、
   高性能コア（Cortex-A78、同 1024）の半分以下の速さしかない。
   → `bin/start` が起動直後に `taskset` で最速のコア群へ固定し直す（`config.sh` の `EMU_CPUS`）。

3. **遅すぎると Android が再起動ループに入る。**
   省電力コアのままだと、ゲスト内の `ueventd` の初期化（coldboot）に 24 秒かかった。
   一方 `apexd` は `/dev/loop-control` の出現を 20 秒しか待たないので、間に合わなかった回は
   APEX の有効化に失敗し、init がシステムを再起動する。

   ```
   apexd: Failed to pre-allocate loop devices : wait for '/dev/loop-control' timed out and took 20008ms
   apexd: Failed to activate /system/apex/com.android.runtime.apex : ... Failed to open loop-control
   init: Service with 'reboot_on_failure' option failed, shutting down system.
   reboot: Restarting system with command 'bootloader,bootstrap-apexd-failed'
   ```

   再起動後はたまたま間に合って先へ進んだが、運任せになる。
   → 2 の対処（高性能コアへの固定）で coldboot が速くなり、解消する見込み
   （固定した状態で最初から起動しての確認はまだしていない）。
   起動ログに `bootstrap-apexd-failed` が出ていたら、CPU の固定が効いているかを疑う。

## 起動に非常に時間がかかる

AVD 作成直後の初回起動を 1 回計測した結果（2026-11-20）。

| 起動からの経過 | 出来事 |
|---|---|
| 6 分 23 秒 | adb に `emulator-5554 device` として現れる。以後 `adb shell` は使える |
| （ゲスト起動後 760 秒、1060 秒） | Android の Watchdog が system_server を強制終了。フレームワークが起動し直す |
| （同 1320 秒、1580 秒） | system_server が `Lost network stack` で異常終了。フレームワークが起動し直す |
| 約 32 分 | 5 回目のフレームワーク起動で `sys.boot_completed=1` に到達 |

4 回の起動し直しは、どれも「実機の速さを前提にしたタイムアウト」に引っかかったもの。
ゲストの `/data/system/dropbox/` に残っていた記録で確認した。

- **Watchdog（1・2 回目）。** system_server の主要スレッドが 60 秒応答しないと、プロセスごと強制終了する仕組み。
  記録の件名は 2 回とも `Blocked in handler on main thread (main)`。
  同じ記録でメモリ逼迫の指標（`/proc/pressure/memory`）は 0、Watchdog がカーネルログに出力させた
  ブロック中タスクの一覧も空で、メモリ不足や I/O 待ちではなく、メインスレッドの処理が 60 秒で終わらなかっただけだった。
- **ネットワークスタックの巻き添え（3・4 回目）。** `com.android.networkstack.process` が
  サービス開始のタイムアウト（ANR）で終了させられ、それを検知した system_server が自分から落ちる。

  ```
  Subject: executing service com.android.networkstack.tethering/.TetheringService   ← ANR
  java.lang.IllegalStateException: Lost network stack                               ← system_server
      at android.net.ConnectivityModuleConnector.maybeCrashWithTerribleFailure(...)
  ```

Android 11 では、これらの時間はソースに固定値で書かれている
（`Watchdog.DEFAULT_TIMEOUT = 60 * 1000`、`ActiveServices.SERVICE_TIMEOUT = 20*1000`）。

この計測は条件が悪かった点に注意。

- 最初の約 4 分は省電力コアで動いており、前節 3 の再起動を 1 回挟んでいる
- 調査のために `adb logcat -d` でログ全体を何度も吸い出しており、それ自体がゲストの負荷になった。
  2 回目の Watchdog の記録では adbd 48% + logd 29% + logcat 20% を占めていた

起動中のゲストに負荷をかける操作（ログの全量取得、`adb shell` の連打）は、それだけで起動を遅らせる。
`bin/start --wait-boot` が起動完了の確認を 20 秒おきにしているのはこのため。

まだ試していない改善の候補:

- **Android 12 (API 31) 以降のイメージに替えて、タイムアウトを延ばす。**
  Android 12 で `ro.hw_timeout_multiplier` というプロパティが入り、Watchdog やサービス開始の
  タイムアウトがその倍率で延びるようになった。ソースのコメントは、まさにこの状況を想定している。

  ```java
  // frameworks/base/core/java/android/os/Build.java (android-12.0.0_r1)
  //   The intent is that products targeting software emulators that are orders of magnitude slower
  //   than real hardware may set this to a large number.
  public static final int HW_TIMEOUT_MULTIPLIER =
      SystemProperties.getInt("ro.hw_timeout_multiplier", 1);
  ```

  Android 11 (`android-11.0.0_r1`) にはこの定数が無いことも確認した。
  起動し直しの無駄が無くなれば、所要時間は大きく縮むはず。
- **仮想 CPU の数を変える。** 現在は 4 個を高性能コア 4 個に固定している。
  省電力コアは同じ計算に 2.7 倍かかる（0.96 秒対 0.35 秒）ので、8 個にしても処理能力の合計は
  理屈の上で最大 4 割増。起動中のゲストは実行待ちが仮想 CPU 数を超えているので効く可能性はあるが、
  Watchdog に引っかかったメインスレッドが省電力コアに乗れば逆効果にもなりうる。
- さらに軽い旧バージョンのイメージ（API 28 以前）に替える

## 速さが必要な場合の別解（どれも未検証）

ソフトウェアエミュレーションである限り、桁を変えるほどの高速化は見込めない。
実機並みの速さが要るなら、エミュレータ以外の Android を adb の相手にする方法がある。

- **Chromebook 自身の Android に繋ぐ。** ChromeOS の設定で
  「Linux 開発環境 → Android アプリの開発 → ADB デバッグを有効にする」をオンにし（再起動が入る）、
  Linux 側から `adb connect arc` する。この環境では `arc` は `100.115.92.2` に解決されるが、
  確認時点では 5555 番ポートは閉じていた（未設定）。管理対象の端末では有効にできない。
  手順の出典: <https://chromeos.dev/en/android-environment/deploying-apps>
- **実機を使う。** USB で繋いだ端末は、このコンテナからはデバイスノードの所有者が
  `nobody` に見えて書き込めず、`adb devices` で `no permissions` になる。
  Android 11 以降の端末なら、ワイヤレスデバッグ（`adb pair` → `adb connect`）で USB を介さずに繋げる。
- **KVM が使える別のマシンでエミュレータを動かし**、`adb connect` や SSH のポート転送で繋ぐ。
