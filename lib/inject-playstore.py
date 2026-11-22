#!/usr/bin/env python3
"""
Google APIs システムイメージ (userdebug) の system.img に対して、以下をオフラインで適用する。

1. Play ストア版 product + system_ext 内 GSF/Feedback の差し替えと軽量化 (--playstore-zip):
   google_apis 内の Google 系アプリ (PrebuiltGmsCore / GoogleServicesFramework 等) は
   テスト鍵 (dev-keys) で署名されている一方、google_apis_playstore 側の Phonesky64 は
   Google 本番鍵 (release-keys) で署名されている。
   そのため:
     - product パーティションを google_apis_playstore 版へ差し替え（GMS・Play ストア・
       WebView・キーボード等の署名を本番鍵で統一）したうえで、エミュレータ動作に不要で
       バックグラウンド CPU/メモリを圧迫する重量プリインストールアプリ 26 個（Velvet, Gmail,
       Maps, YouTube, Chrome, Photos, Drive, Messages, Wellbeing 等）をオフライン削除
     - system_ext パーティションはプラットフォーム鍵署名アプリ (SettingsGoogle / SystemUIGoogle)
       を維持したまま、GMS と com.google.uid.shared を共有する GoogleServicesFramework.apk と
       GoogleFeedback.apk の 2 つだけを google_apis_playstore の本番署名版へ差し替え
     - vendor パーティション内の設定を TCG 高速化向けに調整:
       * /vendor/etc/sensors/hals.conf を空にしてセンサーポーリング (常時 40〜75% CPU) を根絶
       * /vendor/etc/init/rild_goldfish.rc の ril-daemon を無効化してセルラーブロードキャスト嵐を根絶
       * /vendor/etc/fstab.ranchu に 512 MiB zram0 スワップを追加し init.ranchu.rc で有効化
       * /vendor/etc/init/hw/init.ranchu.rc の背景ブラー無効化・goldfish-logcat 停止・noril 設定

2. /system/bin/init の sys_prop 受信タイムアウト延長 (--sysprop-timeout-ms):
   Android 12 の init (property_service.cpp: handle_property_set_fd) は
   ro.hw_timeout_multiplier を参照せず kDefaultSocketTimeout = 2000 ms を即値で持っている。
   TCG ソフトウェアエミュレーションの高負荷時に system_server がこの 2 秒制限に引っかかり
   再起動するのを防ぐため、該当する ARM64 の `mov w10, #2000` (4 バイト) を指定値へ書き換える。

使い方:
  inject-playstore.py --target <system.img> --tmp-dir <dir> \
      [--playstore-zip <zip>] [--sysprop-timeout-ms <2000..65535>]
"""

import argparse
import os
import re
import struct
import subprocess
import sys
import tempfile
import zipfile

SECTOR_SIZE = 512
EXT4_BLOCK_SIZE = 4096
CHUNK_SIZE = 8 * 1024 * 1024
SELINUX_SYSTEM_FILE = b"u:object_r:system_file:s0\x00"
SELINUX_VENDOR_CONFIG_FILE = b"u:object_r:vendor_configs_file:s0\x00"
SELINUX_VENDOR_ETC_FILE = b"u:object_r:vendor_file:s0\x00"

# system_ext 内で google_apis のテスト鍵から playstore 本番鍵へ差し替える対象
SYSTEM_EXT_GOOGLE_APPS = (
    ("priv-app/GoogleServicesFramework", "GoogleServicesFramework.apk"),
    ("priv-app/GoogleFeedback", "GoogleFeedback.apk"),
)

# product パーティションから削除する不要・重量プリインストールアプリ。
# Play ストア (Phonesky64)、GMS (PrebuiltGmsCore)、ログイン用 WebView (WebViewGoogle /
# TrichromeLibrary)、キーボード (LatinIMEGooglePrebuilt)、セットアップ系 (SetupWizardPrebuilt /
# GoogleOneTimeInitializer / PartnerSetupPrebuilt / ConfigUpdater / GoogleContactsSyncAdapter /
# ModuleMetadataGoogle) は残す。
PRODUCT_DEBLOAT_DIRS = (
    "app/CalendarGooglePrebuilt",
    "app/Camera2",
    "app/Chrome",
    "app/Chrome-Stub",
    "app/Drive",
    "app/GoogleTTS",
    "app/Maps",
    "app/MarkupGoogle",
    "app/NexusWallpapersStubPrebuilt2018",
    "app/Photos",
    "app/PrebuiltDeskClockGoogle",
    "app/PrebuiltGmail",
    "app/PrebuiltGoogleTelemetryTvp",
    "app/SoundPickerPrebuilt",
    "app/WallpapersBReel2018",
    "app/YouTube",
    "app/YouTubeMusicPrebuilt",
    "priv-app/AndroidAutoStubPrebuilt",
    "priv-app/Contacts",
    "priv-app/Dialer",
    "priv-app/GoogleRestorePrebuilt",
    "priv-app/ImsServiceEntitlement",
    "priv-app/PrebuiltBugle",
    "priv-app/SettingsIntelligenceGooglePrebuilt",
    "priv-app/Velvet",
    "priv-app/WellbeingPrebuilt",
    # Android 14 (API 34) の google_apis_playstore にのみある重量アプリ
    "app/GoogleContacts",
    "app/PixelThemesStub",
    "app/PrebuiltGoogleAdservicesTvp",
    "priv-app/DeviceIntelligenceNetworkPrebuilt",
    "priv-app/DevicePersonalizationPrebuiltPixel2021",
    "priv-app/GoogleDialer",
    "priv-app/KidsSupervisionStub",
    "priv-app/OdadPrebuilt",
)

# /system/bin/init 内 handle_property_set_fd の sys_prop 受信タイムアウト (2000 ms) の命令列。
# コンパイラの命令スケジューリングがバージョンで異なるため、(パターン, mov w10 のオフセット) を列挙する。
#   Android 12 (API 31): ldr x8,[sp,#128] / ldr w9,[sp,#136] / mov w10,#0x7d0 / add x0,sp,#0x68
#   Android 14 (API 34): ldr x8,[sp,#128] / mov w10,#0x7d0 / ldr w9,[sp,#136] / add x0,sp,#0x68
INIT_TIMEOUT_PATTERNS = (
    (bytes.fromhex("e84340f9e98b40b90afa8052e0a30191"), 8),  # API 31
    (bytes.fromhex("e84340f90afa8052e98b40b9e0a30191"), 4),  # API 34
)


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def run(
    cmd: list[str],
    *,
    input_bytes: bytes | None = None,
    ok_codes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, input=input_bytes, capture_output=True)
    if proc.returncode not in ok_codes:
        stderr = proc.stderr.decode("utf-8", "replace").strip()
        stdout = proc.stdout.decode("utf-8", "replace").strip()
        detail = stderr or stdout
        die(f"コマンド失敗 ({proc.returncode}): {' '.join(cmd)}\n{detail}")
    return proc


def parse_gpt_super(gpt_header_and_entries: bytes) -> tuple[int, int]:
    """GPT エントリから super パーティションの (first_lba, last_lba) を返す。"""
    if len(gpt_header_and_entries) < 512 * 34:
        die("system.img の GPT ヘッダが短すぎます")
    if gpt_header_and_entries[512:520] != b"EFI PART":
        die("system.img に GPT ヘッダ (EFI PART) が見つかりません")
    num_entries = int.from_bytes(gpt_header_and_entries[512 + 80 : 512 + 84], "little")
    entry_size = int.from_bytes(gpt_header_and_entries[512 + 84 : 512 + 88], "little")
    entries_lba = int.from_bytes(gpt_header_and_entries[512 + 72 : 512 + 80], "little")
    base = entries_lba * SECTOR_SIZE
    for i in range(min(num_entries, 128)):
        off = base + i * entry_size
        ent = gpt_header_and_entries[off : off + entry_size]
        if len(ent) < 128:
            break
        first_lba = int.from_bytes(ent[32:40], "little")
        last_lba = int.from_bytes(ent[40:48], "little")
        if first_lba == 0:
            continue
        name = ent[56:128].decode("utf-16le", "ignore").rstrip("\x00")
        if name == "super":
            return first_lba, last_lba
    die("system.img の GPT に super パーティションが見つかりません")


def parse_lpdump(super_head: bytes, tmp_dir: str) -> dict:
    """super 先頭 1 MiB を lpdump に掛けてメタデータを解析する。"""
    head_path = os.path.join(tmp_dir, "super_head.img")
    with open(head_path, "wb") as f:
        f.write(super_head)
    out = run(["lpdump", head_path]).stdout.decode("utf-8", "replace")
    os.remove(head_path)

    meta_max = int(re.search(r"Metadata max size:\s*(\d+)\s*bytes", out).group(1))
    meta_slots = int(re.search(r"Metadata slot count:\s*(\d+)", out).group(1))

    partitions = []
    part_section = out.split("Partition table:", 1)[1].split("Super partition layout:", 1)[0]
    for block in part_section.split("------------------------"):
        m_name = re.search(r"Name:\s*(\S+)", block)
        if not m_name:
            continue
        name = m_name.group(1)
        group = re.search(r"Group:\s*(\S+)", block).group(1)
        attrs = re.search(r"Attributes:\s*(\S+)", block).group(1)
        extents = re.findall(r"(\d+)\s*\.\.\s*(\d+)\s+linear\s+super\s+(\d+)", block)
        if len(extents) != 1:
            die(f"パーティション '{name}' のエクステント構成が想定外です: {extents}")
        lo, hi, phys_start = map(int, extents[0])
        if lo != 0:
            die(f"パーティション '{name}' の先頭セクタが 0 ではありません")
        partitions.append({
            "name": name,
            "group": group,
            "attrs": attrs,
            "start_sector": phys_start,
            "num_sectors": hi - lo + 1,
        })

    groups = {}
    group_section = out.split("Group table:", 1)[1]
    for block in group_section.split("------------------------"):
        m_name = re.search(r"Name:\s*(\S+)", block)
        m_size = re.search(r"Maximum size:\s*(\d+)\s*bytes", block)
        if m_name and m_size:
            groups[m_name.group(1)] = int(m_size.group(1))

    if not partitions:
        die("lpdump から論理パーティションを読み取れませんでした")

    return {
        "metadata_max_size": meta_max,
        "metadata_slots": meta_slots,
        "partitions": partitions,
        "groups": groups,
    }


def skip_stream(stream, nbytes: int) -> None:
    """順次ストリームから nbytes バイト読み捨てる。"""
    rem = nbytes
    while rem > 0:
        chunk = stream.read(min(rem, CHUNK_SIZE))
        if not chunk:
            die("アーカイブ内の system.img が途中で終了しました")
        rem -= len(chunk)


def copy_stream(stream, out_path: str, nbytes: int) -> None:
    """順次ストリームから nbytes バイトをファイルへ書き出す。"""
    rem = nbytes
    with open(out_path, "wb") as out:
        while rem > 0:
            chunk = stream.read(min(rem, CHUNK_SIZE))
            if not chunk:
                die("アーカイブ内の system.img が途中で終了しました")
            out.write(chunk)
            rem -= len(chunk)


def ext4_size_bytes(img_path: str) -> int:
    out = run(["tune2fs", "-l", img_path]).stdout.decode("utf-8", "replace")
    blocks = int(re.search(r"Block count:\s*(\d+)", out).group(1))
    bsize = int(re.search(r"Block size:\s*(\d+)", out).group(1))
    return blocks * bsize


def align_up(value: int, alignment: int) -> int:
    return ((value + alignment - 1) // alignment) * alignment


def extract_from_playstore_zip(playstore_zip: str, tmp_dir: str) -> tuple[str, dict[str, str]]:
    """
    google_apis_playstore の zip 内 system.img を 1 回のストリーム読み込みで走査し、
    - system_ext から GoogleServicesFramework.apk / GoogleFeedback.apk を抽出
    - product パーティション本体をファイルへ書き出す
    返り値: (product_img_path, {apk_name: apk_path})
    """
    product_out = os.path.join(tmp_dir, "product_playstore.img")
    sysext_tmp = os.path.join(tmp_dir, "sysext_playstore.img")
    extracted_apks: dict[str, str] = {}

    with zipfile.ZipFile(playstore_zip, "r") as zf:
        sysimg_members = [m for m in zf.namelist() if m.endswith("/system.img")]
        if len(sysimg_members) != 1:
            die(f"アーカイブ内の system.img を一意に特定できません: {sysimg_members}")
        member = sysimg_members[0]

        with zf.open(member, "r") as stream:
            pos = 0
            gpt_buf = stream.read(512 * 64)
            pos += len(gpt_buf)
            super_first_lba, _ = parse_gpt_super(gpt_buf)
            super_offset = super_first_lba * SECTOR_SIZE
            if super_offset < pos:
                die(f"super オフセットが想定外です: {super_offset}")

            skip_stream(stream, super_offset - pos)
            pos = super_offset

            super_head = stream.read(1024 * 1024)
            pos += len(super_head)
            lp = parse_lpdump(super_head, tmp_dir)

            wanted = {}
            for name in ("system_ext", "product"):
                part = next((p for p in lp["partitions"] if p["name"] == name), None)
                if part is None:
                    die(f"Play ストア抽出元に {name} パーティションがありません")
                wanted[name] = (
                    super_offset + part["start_sector"] * SECTOR_SIZE,
                    part["num_sectors"] * SECTOR_SIZE,
                )

            for name, (off, size) in sorted(wanted.items(), key=lambda kv: kv[1][0]):
                if off < pos:
                    die(f"{name} のオフセットがストリーム位置より手前です: {off} < {pos}")
                skip_stream(stream, off - pos)
                dest = sysext_tmp if name == "system_ext" else product_out
                copy_stream(stream, dest, size)
                pos = off + size

    try:
        for app_dir, apk_name in SYSTEM_EXT_GOOGLE_APPS:
            apk_out = os.path.join(tmp_dir, apk_name)
            run(["debugfs", "-R", f"dump {app_dir}/{apk_name} {apk_out}", sysext_tmp])
            if not os.path.isfile(apk_out) or os.path.getsize(apk_out) == 0:
                die(f"system_ext から {apk_name} の抽出に失敗しました")
            extracted_apks[apk_name] = apk_out
    finally:
        if os.path.exists(sysext_tmp):
            os.remove(sysext_tmp)

    return product_out, extracted_apks


def debugfs_rm_tree(img_path: str, rel_dir: str) -> list[str]:
    """debugfs で rel_dir 配下を再帰的に削除するためのコマンド列を生成する。"""
    proc = subprocess.run(["debugfs", "-R", f"ls -p {rel_dir}", img_path], capture_output=True)
    if proc.returncode != 0:
        return []
    out = proc.stdout.decode("utf-8", "replace")
    if "File not found by ext2_lookup" in out:
        return []
    cmds: list[str] = []
    for line in out.splitlines():
        fields = line.split("/")
        if len(fields) < 7:
            continue
        mode_oct = fields[2]
        name = fields[5]
        if name in (".", "..", ""):
            continue
        child = f"{rel_dir}/{name}"
        if mode_oct.startswith("04"):
            cmds.extend(debugfs_rm_tree(img_path, child))
        else:
            cmds.append(f"rm {child}")
    cmds.append(f"rmdir {rel_dir}")
    return cmds


def debloat_product_partition(product_img: str) -> int:
    """
    product パーティションから不要な重量 Google アプリ群を削除し、
    初回起動時の dex2oat64 時間とバックグラウンド常駐プロセスを削減する。
    """
    orig_ext4 = ext4_size_bytes(product_img)
    os.truncate(product_img, orig_ext4)
    # unshare_blocks の作業余裕として +32 MiB 広げてから削除を行う
    new_size = align_up(orig_ext4 + 32 * 1024 * 1024, 4 * 1024 * 1024)
    run(["resize2fs", product_img, str(new_size // EXT4_BLOCK_SIZE)])
    run(["e2fsck", "-fy", "-E", "unshare_blocks", product_img], ok_codes=(0, 1))

    cmds: list[str] = []
    removed_count = 0
    for rel_dir in PRODUCT_DEBLOAT_DIRS:
        rm_cmds = debugfs_rm_tree(product_img, rel_dir)
        if rm_cmds:
            cmds.extend(rm_cmds)
            removed_count += 1
    if cmds:
        cmds.append("")
        run(["debugfs", "-w", "-f", "-", product_img], input_bytes="\n".join(cmds).encode("utf-8"))
        run(["e2fsck", "-fy", product_img], ok_codes=(0, 1))
    return removed_count


def replace_system_ext_google_apks(
    system_ext_img: str,
    extracted_apks: dict[str, str],
    tmp_dir: str,
) -> None:
    """
    google_apis の system_ext にある GoogleServicesFramework.apk / GoogleFeedback.apk を
    google_apis_playstore の本番署名版へ差し替え、旧署名の oat/ を除去する。
    """
    orig_ext4 = ext4_size_bytes(system_ext_img)
    os.truncate(system_ext_img, orig_ext4)

    extra = sum(os.path.getsize(p) for p in extracted_apks.values()) + 32 * 1024 * 1024
    new_size = align_up(orig_ext4 + extra, 4 * 1024 * 1024)
    run(["resize2fs", system_ext_img, str(new_size // EXT4_BLOCK_SIZE)])
    run(["e2fsck", "-fy", "-E", "unshare_blocks", system_ext_img], ok_codes=(0, 1))

    selinux_file = os.path.join(tmp_dir, "selinux.attr")
    with open(selinux_file, "wb") as f:
        f.write(SELINUX_SYSTEM_FILE)

    cmds: list[str] = []
    for app_dir, apk_name in SYSTEM_EXT_GOOGLE_APPS:
        apk_src = extracted_apks[apk_name]
        apk_dst = f"{app_dir}/{apk_name}"
        cmds.extend(debugfs_rm_tree(system_ext_img, f"{app_dir}/oat"))
        cmds.extend([
            f"rm {apk_dst}",
            f"write {apk_src} {apk_dst}",
            f"sif {apk_dst} mode 0100644",
            f"sif {apk_dst} uid 0",
            f"sif {apk_dst} gid 0",
            f"ea_set -f {selinux_file} {apk_dst} security.selinux",
        ])
    cmds.append("")
    run(["debugfs", "-w", "-f", "-", system_ext_img], input_bytes="\n".join(cmds).encode("utf-8"))
    run(["e2fsck", "-fy", system_ext_img], ok_codes=(0, 1))

    for app_dir, apk_name in SYSTEM_EXT_GOOGLE_APPS:
        want_size = os.path.getsize(extracted_apks[apk_name])
        stat_out = run(["debugfs", "-R", f"stat {app_dir}/{apk_name}", system_ext_img]).stdout.decode("utf-8", "replace")
        if f"Size: {want_size}" not in stat_out or "u:object_r:system_file:s0" not in stat_out:
            die(f"system_ext への {apk_name} 差し替え検証に失敗しました:\n{stat_out}")


def tune_vendor_partition(vendor_img: str, tmp_dir: str) -> None:
    """
    vendor パーティションに対して TCG ソフトウェアエミュレーション向けの高速化を適用する:
      1. etc/sensors/hals.conf を空にしてセンサー HAL のポーリング (40〜75% CPU) を無効化
      2. etc/init/rild_goldfish.rc の vendor.ril-daemon に disabled を追加して RIL/Telephony 嵐を防止
      3. etc/fstab.ranchu に 512 MiB zram0 スワップエントリを追加
      4. etc/init/hw/init.ranchu.rc で背景ブラー無効化・zram 自動有効化・noril 設定・goldfish-logcat 無効化
    """
    orig_ext4 = ext4_size_bytes(vendor_img)
    os.truncate(vendor_img, orig_ext4)
    new_size = align_up(orig_ext4 + 16 * 1024 * 1024, 4 * 1024 * 1024)
    run(["resize2fs", vendor_img, str(new_size // EXT4_BLOCK_SIZE)])
    run(["e2fsck", "-fy", "-E", "unshare_blocks", vendor_img], ok_codes=(0, 1))

    # 1. etc/sensors/hals.conf (空ファイル)
    hals_local = os.path.join(tmp_dir, "hals.conf")
    with open(hals_local, "wb") as f:
        f.write(b"")

    # 2. etc/init/rild_goldfish.rc (disabled 追加)
    rild_local = os.path.join(tmp_dir, "rild_goldfish.rc")
    run(["debugfs", "-R", f"dump etc/init/rild_goldfish.rc {rild_local}", vendor_img])
    rild_text = open(rild_local, "r", encoding="utf-8").read()
    if "disabled" not in rild_text:
        rild_text = rild_text.rstrip() + "\n    disabled\n"
    with open(rild_local, "w", encoding="utf-8") as f:
        f.write(rild_text)

    # 3. etc/fstab.ranchu (zram0 追加)
    fstab_local = os.path.join(tmp_dir, "fstab.ranchu")
    run(["debugfs", "-R", f"dump etc/fstab.ranchu {fstab_local}", vendor_img])
    fstab_text = open(fstab_local, "r", encoding="utf-8").read()
    if "/dev/block/zram0" not in fstab_text:
        fstab_text = fstab_text.rstrip() + "\n/dev/block/zram0 none swap defaults zramsize=536870912\n"
    with open(fstab_local, "w", encoding="utf-8") as f:
        f.write(fstab_text)

    # 4. etc/init/hw/init.ranchu.rc (背景ブラー 0, noril, zram 有効化, goldfish-logcat disabled)
    rc_local = os.path.join(tmp_dir, "init.ranchu.rc")
    run(["debugfs", "-R", f"dump etc/init/hw/init.ranchu.rc {rc_local}", vendor_img])
    rc_text = open(rc_local, "r", encoding="utf-8").read()
    rc_text = rc_text.replace(
        "setprop ro.surface_flinger.supports_background_blur 1",
        "setprop ro.surface_flinger.supports_background_blur 0\n    setprop ro.radio.noril yes\n    setprop persist.sys.zram_enabled 1\n    write /proc/sys/vm/swappiness 100",
    )
    # Android 14 の init.ranchu.rc には background_blur 行が無いため、`on init` に直接追記する。
    if "write /proc/sys/vm/swappiness 100" not in rc_text:
        rc_text = rc_text.replace(
            "    write /proc/sys/vm/page-cluster 0\n",
            "    write /proc/sys/vm/page-cluster 0\n    write /proc/sys/vm/swappiness 100\n",
            1,
        )
    rc_text = rc_text.replace(
        "service goldfish-logcat /system/bin/logcat -f /dev/hvc1 ${ro.boot.logcat}\n    class main",
        "service goldfish-logcat /system/bin/logcat -f /dev/hvc1 ${ro.boot.logcat}\n    class main\n    disabled",
    )
    with open(rc_local, "w", encoding="utf-8") as f:
        f.write(rc_text)

    # 5. etc/permissions/handheld_core_hardware.xml (telephony / voice_recognizers フィーチャー削除)
    #    これを外すことで com.android.phone が IRadio/slot1 をポーリングせず即座に待機に入り、
    #    RoleService も削除済み Dialer / Messages / Velvet (Assistant) のデフォルトロール検索を行わない。
    perm_local = os.path.join(tmp_dir, "handheld_core_hardware.xml")
    run(["debugfs", "-R", f"dump etc/permissions/handheld_core_hardware.xml {perm_local}", vendor_img])
    perm_text = open(perm_local, "r", encoding="utf-8").read()
    for feat in (
        "android.hardware.telephony",
        "android.hardware.telephony.gsm",
        "android.hardware.telephony.ims",
        "android.software.voice_recognizers",
    ):
        perm_text = re.sub(rf'\s*<feature\s+name="{re.escape(feat)}"[^>]*/>\s*\n', "\n", perm_text)
    with open(perm_local, "w", encoding="utf-8") as f:
        f.write(perm_text)

    # 6. etc/vintf/manifest.xml (android.hardware.radio* HAL 定義の削除)
    #    VINTF manifest に android.hardware.radio / android.hardware.radio.config が残っていると
    #    libhidlbase の getService() が IRadio/slot1 や IRadioConfig/default の出現を無限待機して
    #    hwservicemanager / init を毎秒叩き起こすため、radio 系をまとめて削除する。
    vintf_local = os.path.join(tmp_dir, "manifest.xml")
    run(["debugfs", "-R", f"dump etc/vintf/manifest.xml {vintf_local}", vendor_img])
    vintf_text = open(vintf_local, "r", encoding="utf-8").read()
    vintf_text = re.sub(
        r"\s*<hal\s+format=\"(?:hidl|aidl)\">\s*<name>android\.hardware\.radio(?:\.[^<]+)?</name>.*?</hal>",
        "",
        vintf_text,
        flags=re.S,
    )
    with open(vintf_local, "w", encoding="utf-8") as f:
        f.write(vintf_text)

    targets = [
        ("etc/sensors/hals.conf", hals_local),
        ("etc/init/rild_goldfish.rc", rild_local),
        ("etc/fstab.ranchu", fstab_local),
        ("etc/init/hw/init.ranchu.rc", rc_local),
        ("etc/permissions/handheld_core_hardware.xml", perm_local),
        ("etc/vintf/manifest.xml", vintf_local),
    ]
    cmds: list[str] = []
    for i, (dst, src) in enumerate(targets):
        stat_out = run(["debugfs", "-R", f"stat {dst}", vendor_img]).stdout.decode("utf-8", "replace")
        m_ctx = re.search(r'security\.selinux\s*=\s*"([^"]+)"', stat_out)
        ctx_str = m_ctx.group(1).replace("\\000", "") if m_ctx else "u:object_r:vendor_configs_file:s0"
        sel_path = os.path.join(tmp_dir, f"sel_vendor_{i}.attr")
        with open(sel_path, "wb") as f:
            f.write(ctx_str.encode("utf-8") + b"\x00")
        cmds.extend([
            f"rm {dst}",
            f"write {src} {dst}",
            f"sif {dst} mode 0100644",
            f"sif {dst} uid 0",
            f"sif {dst} gid 0",
            f"ea_set -f {sel_path} {dst} security.selinux",
        ])
    cmds.append("")
    run(["debugfs", "-w", "-f", "-", vendor_img], input_bytes="\n".join(cmds).encode("utf-8"))
    run(["e2fsck", "-fy", vendor_img], ok_codes=(0, 1))


def patch_init_sysprop_timeout(system_part_img: str, timeout_ms: int, tmp_dir: str) -> None:
    """
    system パーティション内の /system/bin/init にある mov w10, #2000 (4 バイト) を
    物理ブロック上で直接書き換える（inode・権限・SELinux コンテキストは一切変更しない）。
    """
    if not (2000 <= timeout_ms <= 65535):
        die(f"SYSPROP_TIMEOUT_MS は 2000〜65535 の範囲で指定してください: {timeout_ms}")

    init_dump = os.path.join(tmp_dir, "init.bin")
    run(["debugfs", "-R", f"dump system/bin/init {init_dump}", system_part_img])
    blob = open(init_dump, "rb").read()
    os.remove(init_dump)

    hits = []
    for pat, mov_off in INIT_TIMEOUT_PATTERNS:
        pos = 0
        while True:
            idx = blob.find(pat, pos)
            if idx == -1:
                break
            hits.append((idx, pat, mov_off))
            pos = idx + 1

    if len(hits) != 1:
        die(f"/system/bin/init 内の sys_prop タイムアウト命令を一意に特定できません (一致数: {len(hits)})")

    match_off, INIT_TIMEOUT_PATTERN, mov_off = hits[0]
    patch_off = match_off + mov_off
    if (match_off // EXT4_BLOCK_SIZE) != ((match_off + len(INIT_TIMEOUT_PATTERN) - 1) // EXT4_BLOCK_SIZE):
        die("パッチ対象命令列が 4 KiB ブロック境界をまたいでいます")

    logical_blk = patch_off // EXT4_BLOCK_SIZE
    in_blk_match_off = match_off % EXT4_BLOCK_SIZE
    in_blk_patch_off = patch_off % EXT4_BLOCK_SIZE

    stat_out = run(["debugfs", "-R", "stat system/bin/init", system_part_img]).stdout.decode("utf-8", "replace")
    m_ino = re.search(r"Inode:\s*(\d+)", stat_out)
    if not m_ino:
        die("system/bin/init の inode 番号を取得できません")
    init_ino = m_ino.group(1)

    bmap_out = run(["debugfs", "-R", f"bmap system/bin/init {logical_blk}", system_part_img]).stdout.decode("utf-8", "replace").strip()
    if not bmap_out.isdigit():
        die(f"bmap の結果が不正です: {bmap_out}")
    phys_blk = int(bmap_out)

    icheck_out = run(["debugfs", "-R", f"icheck {phys_blk}", system_part_img]).stdout.decode("utf-8", "replace")
    owners = re.findall(rf"^{phys_blk}\s+(\d+)\s*$", icheck_out, re.M)
    if owners != [init_ino]:
        die(f"ブロック {phys_blk} の所有 inode が system/bin/init ({init_ino}) 単独ではありません: {owners}")

    phys_match_off = phys_blk * EXT4_BLOCK_SIZE + in_blk_match_off
    phys_patch_off = phys_blk * EXT4_BLOCK_SIZE + in_blk_patch_off
    new_insn = struct.pack("<I", 0x52800000 | (timeout_ms << 5) | 10)  # mov w10, #timeout_ms

    with open(system_part_img, "r+b") as f:
        f.seek(phys_match_off)
        actual = f.read(len(INIT_TIMEOUT_PATTERN))
        if actual != INIT_TIMEOUT_PATTERN:
            die(f"物理ブロック上のバイト列が一致しません: {actual.hex()}")
        f.seek(phys_patch_off)
        f.write(new_insn)
        f.flush()

    run(["debugfs", "-R", f"dump system/bin/init {init_dump}", system_part_img])
    patched = open(init_dump, "rb").read()
    os.remove(init_dump)
    if patched[patch_off : patch_off + 4] != new_insn:
        die("/system/bin/init のパッチ検証に失敗しました")


def patch_target(
    target_sysimg: str,
    playstore_zip: str | None,
    sysprop_timeout_ms: int | None,
    tmp_dir: str,
) -> None:
    playstore_product = None
    sysext_apks: dict[str, str] = {}
    if playstore_zip:
        playstore_product, sysext_apks = extract_from_playstore_zip(playstore_zip, tmp_dir)
        prod_mb = os.path.getsize(playstore_product) / (1024 * 1024)
        print(
            f"  Play ストア版 product ({prod_mb:.1f} MiB) + GSF/Feedback 抽出 OK",
            file=sys.stderr,
        )
        removed = debloat_product_partition(playstore_product)
        print(
            f"  product パーティションから不要な重量プリインストールアプリ {removed} 個を削除 OK",
            file=sys.stderr,
        )

    with open(target_sysimg, "rb") as f:
        gpt_buf = f.read(512 * 64)
        super_first_lba, super_last_lba = parse_gpt_super(gpt_buf)
        super_offset = super_first_lba * SECTOR_SIZE
        super_size = (super_last_lba - super_first_lba + 1) * SECTOR_SIZE

        f.seek(super_offset)
        super_head = f.read(1024 * 1024)
        lp = parse_lpdump(super_head, tmp_dir)

        part_files: dict[str, str] = {}
        for part in lp["partitions"]:
            if part["name"] == "product" and playstore_product is not None:
                part_files["product"] = playstore_product
                continue
            out_path = os.path.join(tmp_dir, f"{part['name']}.img")
            f.seek(super_offset + part["start_sector"] * SECTOR_SIZE)
            copy_stream(f, out_path, part["num_sectors"] * SECTOR_SIZE)
            part_files[part["name"]] = out_path

    if sysext_apks:
        if "system_ext" not in part_files:
            die("対象イメージに system_ext パーティションがありません")
        replace_system_ext_google_apks(part_files["system_ext"], sysext_apks, tmp_dir)
        print("  system_ext 内 GSF/Feedback を本番署名版へ差し替え OK", file=sys.stderr)

    if "vendor" in part_files:
        tune_vendor_partition(part_files["vendor"], tmp_dir)
        print("  vendor パーティションの高速化調整 (sensors-hal/ril 無効化, zram0 追加, ブラー無効化) OK", file=sys.stderr)

    if sysprop_timeout_ms is not None:
        if "system" not in part_files:
            die("対象イメージに system パーティションがありません")
        patch_init_sysprop_timeout(part_files["system"], sysprop_timeout_ms, tmp_dir)
        print(f"  /system/bin/init sys_prop タイムアウト延長 OK (2000 ms → {sysprop_timeout_ms} ms)", file=sys.stderr)

    lpmake_cmd = [
        "lpmake",
        "--device-size", str(super_size),
        "--metadata-size", str(lp["metadata_max_size"]),
        "--metadata-slots", str(lp["metadata_slots"]),
        "--alignment", str(1024 * 1024),
    ]
    used_groups = {p["group"] for p in lp["partitions"]}
    for gname in sorted(used_groups):
        if gname == "default":
            continue
        if gname not in lp["groups"]:
            die(f"グループ '{gname}' の上限サイズが不明です")
        lpmake_cmd += ["--group", f"{gname}:{lp['groups'][gname]}"]

    for part in lp["partitions"]:
        pimg = part_files[part["name"]]
        psize = os.path.getsize(pimg)
        lpmake_cmd += [
            "--partition", f"{part['name']}:{part['attrs']}:{psize}:{part['group']}",
            "--image", f"{part['name']}={pimg}",
        ]

    super_new = os.path.join(tmp_dir, "super_new.img")
    lpmake_cmd += ["--output", super_new]
    run(lpmake_cmd)

    new_super_len = os.path.getsize(super_new)
    if new_super_len > super_size:
        die(f"再構築後の super ({new_super_len} B) が領域サイズ ({super_size} B) を超えています")

    with open(super_new, "rb") as src, open(target_sysimg, "r+b") as dst:
        dst.seek(super_offset)
        while True:
            buf = src.read(CHUNK_SIZE)
            if not buf:
                break
            dst.write(buf)
        dst.flush()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, help="書き換え対象の system.img")
    parser.add_argument("--tmp-dir", required=True, help="一時作業ディレクトリの親")
    parser.add_argument("--playstore-zip", default="", help="google_apis_playstore の zip")
    parser.add_argument("--sysprop-timeout-ms", type=int, default=None, help="init sys_prop タイムアウト (ms)")
    args = parser.parse_args()

    playstore_zip = args.playstore_zip or None
    if playstore_zip is None and args.sysprop_timeout_ms is None:
        return

    os.makedirs(args.tmp_dir, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sysimg-patch-", dir=args.tmp_dir) as tmp_dir:
        patch_target(args.target, playstore_zip, args.sysprop_timeout_ms, tmp_dir)


if __name__ == "__main__":
    main()
