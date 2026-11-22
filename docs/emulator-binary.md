# エミュレータ本体の出所と信頼の根拠

このリポジトリが実行するエミュレータ本体は **Google の公式バイナリではない**。
何を、どこから取得し、何を確認したうえで使っているかをここに残す。
（そもそも非公式ビルドが必要な理由は [background.md](background.md)）

確認日: 2026-11-20

## 取得しているもの

| 項目 | 値 |
|---|---|
| 配布元 | <https://github.com/Mr0btain/Android-Emulator-Arm64> |
| リリース | `v35.6.3-3`（2026-11-06 公開） |
| ファイル | `android-emulator-35.6.3-linux-aarch64.tar.xz`（111,263,044 バイト） |
| SHA-256 | `7dc6b2fbb9ad30167a4a4a581c26c12fde49a4fe10a57e33fc0adb6975822c32` |
| 中身 | Android Emulator 35.6.3（`emulator` ランチャーと `qemu-system-aarch64` など） |
| 元のソース | AOSP `platform/external/qemu` の `emu-master-dev` ブランチ（公開されている最後の系列） |
| ライセンス | QEMU 部分は GPLv2、Android 固有部分は Apache 2.0（展開後の `var/sdk/emulator/NOTICE.txt`） |

URL と SHA-256 は [`config.sh`](../config.sh) に固定してあり、`bin/setup` は一致しないファイルを展開しない。

## このビルドを選んだ理由

Linux aarch64 向けの非公式ビルドは調べた範囲で 3 つあった。

| リポジトリ | 版 | ビルド手順の公開 | KVM なしでの動作 | 判断 |
|---|---|---|---|---|
| Mr0btain/Android-Emulator-Arm64 | 35.6.3 | スクリプト・パッチ・CI 定義すべて公開 | 対応パッチあり | **採用** |
| akexorcist/android-emulator-cli-linux-aarch64 | 不明 | なし（バイナリのみ） | 不明 | 中身を確かめる手段が無いので不採用 |
| jduartedj/android-emulator-linux-aarch64-dgx-spark | 35.6.3 | あり | KVM 前提（README が `/dev/kvm` を必須としている） | この環境では動かせないので不採用 |

決め手は 2 点。

1. **KVM なしで起動するためのパッチが入っている。** 素のソースは aarch64 Linux 上で
   `gic-version=host` と `-cpu host` を前提にしており、どちらも KVM が無いと QEMU が起動を拒否する。
   採用したビルドは、KVM が使えないときに GICv3 と `-cpu max` へ切り替える。
   実際に起動ログへ次の 2 行が出る。

   ```
   INFO | KVM not available, using cpu 'max' under TCG
   qemu-system-aarch64-headless: warning: gic-version=host requires KVM, using 3 for TCG
   ```

2. **バイナリが作られた経緯を追える。** 次節のとおり。

## 確認したこと

- リリースのファイル 4 点はすべて `github-actions[bot]` がアップロードしており、
  作成時刻（2026-11-06 22:55:58 UTC）がワークフロー実行
  [42301656896](https://github.com/Mr0btain/Android-Emulator-Arm64/actions/runs/42301656896)
  の release ジョブ（22:55:41〜22:56:06 UTC）の中に収まっている。
  人の手でアップロードし直した形跡は無い。
- そのワークフローはタグ `v35.6.3-3`（コミット `41dcaa5884a381cb42202faeb6b2100c27f83422`）の
  push で起動している。このコミット時点のリポジトリ全体（約 76 KB）を読んだ。
  - `.github/workflows/build.yml`: GitHub の arm64 ランナー上の `ubuntu:26.04` コンテナで
    `scripts/ci-build.sh` を実行し、生成物をそのままリリースに添付する。
  - `scripts/`: `sources.list` に SHA 固定された AOSP の 54 プロジェクトを
    `https://android.googlesource.com` から取得 → パッチ適用 → AOSP 標準の `cmake.py` でビルド → tar で固める、だけ。
    それ以外のサイトから実行コードを取ってくる処理は無い。
  - `patches/`: 10 件。内訳は下表。エミュレータの動作を変えるのは 3 件で、いずれも数行〜十数行。
- 取得したファイルの SHA-256 が、GitHub API の示すアセットの digest、および同梱の `.sha256` と一致する。
- tar の中身は `emulator/` ディレクトリ 1 つだけで、絶対パス・`..`・setuid ビット・デバイスファイルを含まない。
- ヘッドレス実行で使う実行ファイルとライブラリが依存する共有ライブラリは、
  同梱のものと glibc / libgcc / libuuid / libX11 だけ（`readelf -d` で確認。Qt を使う GUI 版は対象外）。

### パッチの内訳

| パッチ | 内容 | 種別 |
|---|---|---|
| qemu 0001, 0002 | aarch64 ホストでのネイティブビルド対応（ホスト判定、システムの cmake、x86 専用の clang を要求しない） | ビルド |
| qemu 0003 | クラッシュレポート送信を無効にしたビルドではシンボル抽出を省く | ビルド |
| qemu 0004 | GCC 15 でのコンパイルエラー修正（`#include` 追加など） | ビルド |
| qemu 0005 | `hw/arm/virt.c`: KVM が無いとき GICv3 にフォールバック | **動作** |
| qemu 0006 | `main.cpp`: `/dev/kvm` が使えない／`-accel off` のとき `-cpu max` を使う | **動作** |
| qemu 0007 | libuuid / libunwind を同梱の静的ライブラリではなくシステムのものにする（TLS の不具合によるクラッシュ回避） | **動作** |
| qemu 0008 | 入れ子の CMake ビルドにシステムの ninja を渡す | ビルド |
| gfxstream 0001 / netsim 0001 | GCC 15 向けの `#include <cstdint>` 追加 | ビルド |

## 確認できていないこと（残るリスク）

- **バイナリとソースの対応は検証していない。** ビルドを再現して突き合わせたわけではない。
  ビルド環境は配布者のリポジトリの GitHub Actions であり、ワークフローは ccache を
  実行間でキャッシュしているので、配布者に悪意があれば細工の余地は残る。
- GitHub のビルド来歴証明（artifact attestation）は付いていない。
- 配布者は個人で、リポジトリは 2026-11-06 に作られたばかり（確認時点で star 0）。評判による裏付けは無い。
- リリースは immutable 設定ではないので、後からファイルを差し替えることは技術的に可能。
  → SHA-256 を固定しているので、差し替えられた場合は `bin/setup` が検知して止まる。
- 35.6.3 は Google の現行版（37.x）ではない。配布元の記録によれば `emu-master-dev` の
  公開ソースは 2025 年 3 月で止まっており、それ以後の修正は入っていない。

## リスクへの対処: サンドボックス

上記の理由から、エミュレータ本体は必ず bubblewrap のサンドボックス内で実行する
（[`lib/common.sh`](../lib/common.sh) の `sandbox_exec`）。

| | サンドボックス内からの見え方 |
|---|---|
| ホームディレクトリ | 見えない。`$HOME` は `var/home` に差し替え |
| このリポジトリ | `var/home`・`var/avd`・`var/tmp` が読み書き可、`var/sdk` が読み取り専用。それ以外（`.git` を含む）は見えない |
| `/run/user/<uid>` | 空（SSH エージェント、GnuPG、Wayland、D-Bus などのソケットに届かない） |
| 環境変数 | 呼び出し元のものは引き継がない。必要な 10 個だけを設定 |
| 他のプロセス | 見えない（PID 名前空間を分離） |
| `/usr`、`/sys`、`/etc` の一部 | 読み取り専用。`/etc` は動的リンク・名前解決・証明書に必要なファイルだけ |
| ネットワーク | **共有している**（下記） |

ネットワークを分離していないのは、サンドボックスの外で動く adb サーバーが
`127.0.0.1:5555` に接続する必要があるため。したがってエミュレータのプロセスは
外部への通信と、ローカルで待ち受けている他のサービス（adb サーバーの 5037 番など）への接続ができる。
読み出せる秘密情報が無い状態にすることで、通信できても持ち出すものが無いようにしている。

なお、エミュレータは補助プロセス `crashpad_handler` を
`--url=https://clients2.google.com/cr/report`（Google のクラッシュレポート収集先）付きで起動する。
エミュレータがクラッシュしたときに実際に送信されるかどうかは確認していない。
利用統計の送信は `-no-metrics` で止めている。

サンドボックスの中身は次のように確かめられる（`ls` などを中で実行して何が見えるかを見る）。

```sh
bash -c 'source lib/common.sh; sandbox_exec /usr/bin/ls -la "$HOME/.." /run/user/$(id -u)'
```

## 別のビルドに替える・自分でビルドする

- **新しいリリースに替える**: `config.sh` の `EMULATOR_*` を書き換えて `bin/setup`。
  SHA-256 はリリースページの値をそのまま信じるのではなく、上の「確認したこと」をやり直したうえで固定する。
- **配布者を信頼せずに済ませる**: 配布元リポジトリを自分のアカウントに fork し、
  内容を読んだうえでタグを push すれば、同じ手順のビルドが自分の GitHub Actions 上で走る
  （arm64 ランナーが無料なのは公開リポジトリのみ）。できた tarball の URL と SHA-256 を `config.sh` に書く。
- **この PC でビルドする**: 現実的でない。配布元の README によれば
  ソース約 17 GB + ビルドツリー約 36 GB が必要で、8 コア・RAM 14 GB で約 50 分。
  この環境は空きディスク 45 GB・RAM 6.5 GB で、どちらも足りない。
