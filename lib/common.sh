# shellcheck shell=bash
#
# bin/ 以下のスクリプトが source する共通定義。単体では実行しない。

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"

# 生成物はすべて var/ の下に置く（git 管理外）。リポジトリの外には何も書かない。
VAR_DIR="$REPO_ROOT/var"
DL_DIR="$VAR_DIR/downloads"   # 取得したアーカイブ
SDK_DIR="$VAR_DIR/sdk"        # ANDROID_SDK_ROOT 相当（emulator/ と system-images/）
AVD_HOME="$VAR_DIR/avd"       # ANDROID_AVD_HOME 相当
SBX_HOME="$VAR_DIR/home"      # サンドボックス内の $HOME（~/.android の代わり）
SBX_TMP="$VAR_DIR/tmp"        # サンドボックス内の /tmp
LOG_DIR="$VAR_DIR/log"
RUN_DIR="$VAR_DIR/run"

# shellcheck source=../config.sh
source "$REPO_ROOT/config.sh"
if [ -f "$REPO_ROOT/config.local.sh" ]; then
  # shellcheck source=/dev/null
  source "$REPO_ROOT/config.local.sh"
fi

EMULATOR_DIR="$SDK_DIR/emulator"
EMULATOR_BIN="$EMULATOR_DIR/emulator"
SYSIMG_REL="system-images/android-${SYSIMG_API}/${SYSIMG_TAG}/${SYSIMG_ABI}"
SYSIMG_DIR="$SDK_DIR/$SYSIMG_REL"
AVD_INI="$AVD_HOME/${AVD_NAME}.ini"
AVD_DIR="$AVD_HOME/${AVD_NAME}.avd"
EMU_ADB_PORT=$((EMU_CONSOLE_PORT + 1))
EMU_SERIAL="emulator-${EMU_CONSOLE_PORT}"
EMU_LOG="$LOG_DIR/emulator.log"
PID_FILE="$RUN_DIR/emulator.pid"
CONSOLE_TOKEN_FILE="$SBX_HOME/.emulator_console_auth_token"

log() { printf '%s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

need_cmd() {
  local cmd
  for cmd in "$@"; do
    command -v "$cmd" >/dev/null 2>&1 ||
      die "コマンド '$cmd' が見つかりません（必要パッケージは README.md「前提」を参照）"
  done
}

require_setup() {
  [ -x "$EMULATOR_BIN" ] || die "エミュレータ本体がありません。先に bin/setup を実行してください"
  [ -f "$SYSIMG_DIR/system.img" ] || die "システムイメージがありません。先に bin/setup を実行してください"
  [ -f "$AVD_DIR/config.ini" ] || die "AVD '$AVD_NAME' がありません。先に bin/setup を実行してください"
}

# 起動中のエミュレータ（サンドボックス）の PID を出力する。動いていなければ失敗する。
emu_pid() {
  local pid
  [ -r "$PID_FILE" ] || return 1
  pid="$(<"$PID_FILE")"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  # PID が別プロセスに使い回されていないことを、コマンドラインに含まれる
  # このリポジトリのパスで確かめる。
  [ -r "/proc/$pid/cmdline" ] || return 1
  # 読んでいる最中にプロセスが終了することがあるので、読み取りエラーは捨てる。
  tr '\0' '\n' <"/proc/$pid/cmdline" 2>/dev/null | grep -qxF -- "$SBX_HOME" || return 1
  printf '%s\n' "$pid"
}

port_in_use() {
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

# サンドボックス内で動いている QEMU 本体の PID を出力する。
# 引数はサンドボックスの PID（= セッション ID）。まだ居なければ失敗する。
qemu_pid() {
  local pid
  pid="$(pgrep --session "$1" --oldest qemu-system-)" || return 1
  printf '%s\n' "$pid"
}

# 性能の違うコアが混在する CPU (big.LITTLE) で、最も速いコア群を taskset 用の
# カンマ区切りリストで出力する。混在していない・判定できない場合は何も出力しない。
fastest_cpus() {
  local f
  for f in /sys/devices/system/cpu/cpu[0-9]*/cpu_capacity; do
    [ -r "$f" ] || return 0
    f="${f%/cpu_capacity}"
    printf '%s %s\n' "${f##*/cpu}" "$(<"$f/cpu_capacity")"
  done | awk '
    { cap[$1] = $2; if ($2 > max) max = $2; if (min == "" || $2 < min) min = $2 }
    END { if (max > min) for (cpu in cap) if (cap[cpu] == max) print cpu }
  ' | sort -n | paste -sd, -
}

# adb から見たエミュレータの状態（device / offline / unauthorized）。見えなければ空文字。
adb_state() {
  adb devices 2>/dev/null | awk -v s="$EMU_SERIAL" '$1 == s { print $2 }'
}

# sandbox_exec CMD [ARGS...]
#
# CMD を bubblewrap のサンドボックスに閉じ込めて exec する。
# エミュレータ本体は非公式ビルドなので、触れられる範囲を次のとおりに絞る。
#
#   読み書き可: var/home, var/avd, var/tmp（/tmp として見える）
#   読み取り専用: var/sdk, /usr, /sys と /etc の一部（名前解決と証明書）
#   見えない  : 本物のホームディレクトリ、/run/user/<uid>（SSH エージェント等の
#               ソケット）、他プロセス、呼び出し元の環境変数
#   共有      : ネットワーク（adb サーバーが 127.0.0.1 のポートへ接続するため）
#
# 追加の bwrap オプションは配列 SANDBOX_BWRAP_ARGS で渡せる。
sandbox_exec() {
  need_cmd bwrap
  local uid user path
  uid="$(id -u)"
  user="$(id -un)"
  local -a args=(
    --unshare-user --unshare-ipc --unshare-pid --unshare-uts --unshare-cgroup-try
    --clearenv
    --ro-bind /usr /usr
    --proc /proc
    --dev /dev
    --ro-bind /sys /sys
    --tmpfs /run
    --dir "/run/user/$uid"
    --bind "$SBX_TMP" /tmp
    --ro-bind-try /tmp/.X11-unix /tmp/.X11-unix
    --ro-bind "$SDK_DIR" "$SDK_DIR"
    --bind "$SBX_HOME" "$SBX_HOME"
    --bind "$AVD_HOME" "$AVD_HOME"
    --chdir "$SBX_HOME"
    --setenv HOME "$SBX_HOME"
    --setenv USER "$user"
    --setenv LOGNAME "$user"
    --setenv PATH /usr/bin
    --setenv LANG C.UTF-8
    --setenv XDG_RUNTIME_DIR "/run/user/$uid"
    --setenv ANDROID_HOME "$SDK_DIR"
    --setenv ANDROID_SDK_ROOT "$SDK_DIR"
    --setenv ANDROID_AVD_HOME "$AVD_HOME"
    --setenv ANDROID_EMULATOR_HOME "$SBX_HOME/.android"
    --setenv DISPLAY "${DISPLAY:-:0}"
    --setenv QT_QPA_PLATFORM xcb
    --setenv QT_ENABLE_HIGHDPI_SCALING 0
    --setenv QT_AUTO_SCREEN_SCALE_FACTOR 0
    --setenv QT_SCALE_FACTOR 1
    --setenv QT_SCREEN_SCALE_FACTORS 1
    --setenv QT_USE_PHYSICAL_DPI 0
    --setenv QT_FONT_DPI 96
  )
  local xauth="${XAUTHORITY:-$HOME/.Xauthority}"
  if [ -r "$xauth" ]; then
    install -D -m 600 "$xauth" "$SBX_HOME/.Xauthority"
    args+=(--setenv XAUTHORITY "$SBX_HOME/.Xauthority")
  fi
  # /bin や /lib は、ホスト側が /usr へのシンボリックリンクならそのまま再現する。
  for path in /bin /sbin /lib /lib64; do
    if [ -L "$path" ]; then
      args+=(--symlink "$(readlink "$path")" "$path")
    elif [ -d "$path" ]; then
      args+=(--ro-bind "$path" "$path")
    fi
  done
  # /etc は丸ごとではなく、動的リンク・名前解決・TLS 証明書に必要なものだけ。
  # コンテナ環境 (distrobox 等) でパッケージ更新後に bind mount 元 inode が置換されると
  # Links: 0 (//deleted) になり bwrap の再 bind mount が ENOENT で失敗するため、その場合は
  # var/tmp に実体をコピーしてからマウントする。
  local nlinks copy_path
  for path in /etc/ld.so.cache /etc/ld.so.conf /etc/ld.so.conf.d \
              /etc/resolv.conf /etc/hosts /etc/nsswitch.conf \
              /etc/passwd /etc/group /etc/localtime \
              /etc/ssl /etc/ca-certificates; do
    [ -e "$path" ] || continue
    nlinks="$(stat -L -c '%h' "$path" 2>/dev/null || echo 0)"
    if [ "$nlinks" = 0 ] && [ -f "$path" ]; then
      mkdir -p "$SBX_TMP/.etc"
      copy_path="$SBX_TMP/.etc/${path##*/}"
      cat "$path" >"$copy_path"
      args+=(--ro-bind "$copy_path" "$path")
    else
      args+=(--ro-bind-try "$path" "$path")
    fi
  done
  exec bwrap "${args[@]}" "${SANDBOX_BWRAP_ARGS[@]}" -- "$@"
}
SANDBOX_BWRAP_ARGS=()
